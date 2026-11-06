"""Routing policy tests replay answer fixtures; they do not claim model calibration."""

from copy import deepcopy
from dataclasses import FrozenInstanceError, fields
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock

import httpx2
from openai import APIConnectionError, APIResponseValidationError, APIStatusError, APITimeoutError
from openai.types.decision import Decision

from telegram_ecommerce.agent.routing import (
    DIFFICULTY_LEVELS,
    ROUTING_QUESTIONS,
    ROUTES,
    RoutingDecision,
    RoutingUnavailable,
    classify_request,
    parse_routing_decision,
)


def response(*, history=0.01, route="catalog", difficulty=0):
    return {"answers": [
        {"type": "predicate", "name": "needs_history", "probability": history},
        {"type": "choice", "name": "route", "choice": route, "confidence": 0.99,
         "probabilities": [{"value": value, "probability": 1.0 if value == route else 0.0}
                           for value in ROUTES]},
        {"type": "score", "name": "difficulty", "score": difficulty, "confidence": 0.99,
         "probabilities": [{"value": index, "label": level["label"],
                            "probability": 1.0 if index == difficulty else 0.0}
                           for index, level in enumerate(DIFFICULTY_LEVELS)]},
    ]}


def answer(payload, name):
    return next(item for item in payload["answers"] if item["name"] == name)


def fake_client(result=None, error=None):
    create = AsyncMock(return_value=result, side_effect=error)
    configured = SimpleNamespace(decisions=SimpleNamespace(create=create))
    return SimpleNamespace(with_options=Mock(return_value=configured)), create


class DecisionPolicyTests(unittest.TestCase):
    def test_response_accepts_sdk_objects_and_answer_order_is_irrelevant(self):
        payload = response(route="cart_change")
        payload["answers"].reverse()
        parsed = parse_routing_decision(Decision.model_construct(**payload))
        self.assertFalse(parsed.needs_history)
        self.assertEqual(parsed.route, "cart_change")
        with self.assertRaises(FrozenInstanceError):
            parsed.route = "catalog"

    def test_interface_contains_no_action_authorization(self):
        self.assertEqual([field.name for field in fields(RoutingDecision)],
                         ["needs_history", "route", "difficulty", "confidence"])
        self.assertEqual([question["name"] for question in ROUTING_QUESTIONS],
                         ["needs_history", "route", "difficulty"])

    def test_uncertain_context_does_not_disable_task_or_difficulty(self):
        parsed = parse_routing_decision(response(history=0.5))
        self.assertTrue(parsed.needs_history)
        self.assertEqual((parsed.route, parsed.difficulty, parsed.confidence), ("catalog", 0.0, 0.99))

    def test_uncertain_task_does_not_disable_self_contained_context_or_difficulty(self):
        payload = response()
        answer(payload, "route")["confidence"] = 0.7
        parsed = parse_routing_decision(payload)
        self.assertFalse(parsed.needs_history)
        self.assertEqual((parsed.route, parsed.difficulty, parsed.confidence), ("other", 0.0, 0.7))

    def test_uncertain_difficulty_does_not_disable_context_or_task(self):
        payload = response()
        answer(payload, "difficulty")["confidence"] = 0.5
        parsed = parse_routing_decision(payload)
        self.assertFalse(parsed.needs_history)
        self.assertEqual((parsed.route, parsed.difficulty, parsed.confidence), ("catalog", 3.0, 0.99))

    def test_history_is_retained_except_for_confident_self_contained_messages(self):
        for probability, needs_history in ((0.0, False), (0.15, False), (0.151, True), (1.0, True)):
            with self.subTest(probability=probability):
                self.assertEqual(parse_routing_decision(response(history=probability)).needs_history, needs_history)

    def test_task_and_difficulty_thresholds_are_independent(self):
        for confidence in (0.799, 0.8):
            payload = response()
            answer(payload, "route")["confidence"] = confidence
            self.assertEqual(parse_routing_decision(payload).route, "catalog" if confidence >= 0.8 else "other")
            payload = response()
            answer(payload, "difficulty")["confidence"] = confidence
            self.assertEqual(parse_routing_decision(payload).difficulty, 0.0 if confidence >= 0.8 else 3.0)

    def test_fractional_difficulty_uses_weighted_level_index(self):
        payload = response()
        difficulty = answer(payload, "difficulty")
        difficulty["probabilities"][0]["probability"] = 0.8
        difficulty["probabilities"][1]["probability"] = 0.2
        difficulty["score"] = 0.2
        self.assertEqual(parse_routing_decision(payload).difficulty, 0.2)

    def test_missing_duplicate_unknown_and_refused_answers_request_existing_fallback(self):
        payloads = [None, {}, {"answers": "bad"}, {"answers": response()["answers"][:-1]}]
        for change in (lambda p: p["answers"].__setitem__(-1, deepcopy(p["answers"][0])),
                       lambda p: answer(p, "route").update(name="unknown"),
                       lambda p: answer(p, "needs_history").update(type="refusal"),
                       lambda p: answer(p, "difficulty").update(type="refusal"),
                       lambda p: answer(p, "route").update(type="predicate"),
                       lambda p: answer(p, "route").update(name=None)):
            payload = response()
            change(payload)
            payloads.append(payload)
        for payload in payloads:
            with self.subTest(payload=payload), self.assertRaises(RoutingUnavailable):
                parse_routing_decision(payload)

    def test_numbers_require_finite_numbers_in_range_and_never_booleans(self):
        for name, field in (("needs_history", "probability"), ("route", "confidence"),
                            ("difficulty", "confidence"), ("difficulty", "score")):
            for bad in (True, False, "0.99", None, -0.1, 4, float("nan"), float("inf")):
                with self.subTest(name=name, field=field, bad=bad):
                    payload = response()
                    answer(payload, name)[field] = bad
                    with self.assertRaises(RoutingUnavailable):
                        parse_routing_decision(payload)

    def test_invalid_distributions_choices_and_score_consistency_request_fallback(self):
        changes = (
            lambda p: answer(p, "route").update(choice=True),
            lambda p: answer(p, "route").update(choice="cart"),
            lambda p: answer(p, "route").update(probabilities=[]),
            lambda p: answer(p, "route")["probabilities"][1].update(value="categories"),
            lambda p: answer(p, "route")["probabilities"][0].update(probability=0.5),
            lambda p: answer(p, "difficulty")["probabilities"][0].update(value=True),
            lambda p: answer(p, "difficulty")["probabilities"][0].update(label="average"),
            lambda p: answer(p, "difficulty").update(score=2),
        )
        for change in changes:
            payload = response()
            change(payload)
            with self.assertRaises(RoutingUnavailable):
                parse_routing_decision(payload)

    def test_task_confidence_cannot_exceed_selected_probability(self):
        payload = response()
        answer(payload, "route")["probabilities"][1]["probability"] = 0.6
        answer(payload, "route")["probabilities"][-1]["probability"] = 0.4
        parsed = parse_routing_decision(payload)
        self.assertEqual(parsed.route, "other")
        self.assertEqual(parsed.confidence, 0.6)

    def test_rubric_has_four_distinct_levels_and_current_cart_state_is_not_history(self):
        self.assertEqual([level["label"] for level in DIFFICULTY_LEVELS],
                         ["easy", "medium", "difficult", "very_difficult"])
        self.assertIn("Average/medium are the same level", DIFFICULTY_LEVELS[1]["description"])
        instructions = ROUTING_QUESTIONS[0]["instructions"]
        for example in ("make it three", "Show my cart", "clear my cart", "database state is not chat history"):
            self.assertIn(example, instructions)


class DecisionRequestTests(unittest.IsolatedAsyncioTestCase):
    async def test_one_current_message_only_request_with_bounded_timeout_no_retries(self):
        client, create = fake_client(response())
        await classify_request(client, "Find notebooks")
        client.with_options.assert_called_once_with(timeout=6.0, max_retries=0)
        create.assert_awaited_once()
        request = create.await_args.kwargs
        self.assertEqual(set(request), {"model", "input", "questions"})
        self.assertEqual(request["input"], "Find notebooks")
        self.assertEqual(request["model"], "gpt-6-luna")
        self.assertEqual(request["questions"], ROUTING_QUESTIONS)
        self.assertEqual(len(request["questions"]), 3)
        self.assertIsNot(request["questions"][0], ROUTING_QUESTIONS[0])

    async def test_labeled_message_answer_fixtures_map_to_expected_advisory_policy(self):
        # These are policy replay fixtures, not live linguistic evaluations.
        samples = (
            ("Find notebooks", False, "catalog", 0),
            ("Show my cart", False, "cart_read", 0),
            ("Clear my cart", False, "cart_change", 0),
            ("Add two notebooks", False, "cart_change", 0),
            ("Make it three.", True, "cart_change", 0),
            ("Remove that one", True, "cart_change", 0),
            ("Yes", True, "other", 0),
            ("Esvazie meu carrinho", False, "cart_change", 0),
            ("Compare two notebooks under $10", False, "comparison", 1),
        )
        for text, history, route, difficulty in samples:
            with self.subTest(text=text):
                client, create = fake_client(response(history=0.99 if history else 0.01,
                                                     route=route, difficulty=difficulty))
                parsed = await classify_request(client, text)
                self.assertEqual((parsed.needs_history, parsed.route, parsed.difficulty),
                                 (history, route, difficulty))
                self.assertEqual(create.await_args.kwargs["input"], text)

    async def test_api_errors_are_sanitized_and_request_existing_fallback_without_retry(self):
        request = httpx2.Request("POST", "https://api.openai.com/v1/decisions")
        errors = [APIConnectionError(message="private upstream detail", request=request),
                  APITimeoutError(request=request),
                  APIResponseValidationError(httpx2.Response(200, request=request), body={})]
        errors.extend(APIStatusError("private upstream detail", response=httpx2.Response(code, request=request),
                                     body={"private": "detail"}) for code in (400, 401, 403, 404, 422, 429, 500, 503))
        for error in errors:
            client, create = fake_client(error=error)
            with self.assertRaisesRegex(RoutingUnavailable, "^Decision routing is unavailable$"):
                await classify_request(client, "Find notebooks")
            create.assert_awaited_once()
        with self.assertRaises(RoutingUnavailable):
            await classify_request(object(), "Find notebooks")

    async def test_programming_errors_are_not_hidden_as_availability_failures(self):
        client, _ = fake_client(error=TypeError("implementation problem"))
        with self.assertRaises(TypeError):
            await classify_request(client, "Find notebooks")

    async def test_empty_input_requests_fallback_without_api_call(self):
        client, create = fake_client(response())
        with self.assertRaises(RoutingUnavailable):
            await classify_request(client, "  ")
        create.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
