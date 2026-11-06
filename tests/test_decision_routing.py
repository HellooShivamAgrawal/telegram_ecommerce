"""Routing policy tests use answer fixtures; they do not claim model calibration."""

from copy import deepcopy
from dataclasses import FrozenInstanceError
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock

import httpx2
from openai import APIConnectionError, APIResponseValidationError, APIStatusError
from openai.types.decision import Decision

from telegram_ecommerce.agent.routing import (
    DIFFICULTY_LEVELS,
    ROUTING_QUESTIONS,
    RoutingUnavailable,
    classify_request,
    parse_routing_decision,
)


def response(*, history=0.01, route="catalog", difficulty=0, actions=()):
    return {"answers": [
        {"type": "predicate", "name": "needs_history", "probability": history},
        {"type": "choice", "name": "route", "choice": route, "confidence": 0.99,
         "probabilities": [{"value": value, "probability": 1.0 if value == route else 0.0}
                           for value in ("categories", "catalog", "cart", "comparison", "other")]},
        {"type": "score", "name": "difficulty", "score": difficulty, "confidence": 0.99,
         "probabilities": [{"value": index, "label": level["label"],
                            "probability": 1.0 if index == difficulty else 0.0}
                           for index, level in enumerate(DIFFICULTY_LEVELS)]},
        *[{"type": "predicate", "name": action, "probability": 0.99 if action in actions else 0.01}
          for action in ("add", "set", "remove", "clear")],
    ]}


def answer(payload, name):
    return next(item for item in payload["answers"] if item["name"] == name)


def fake_client(result=None, error=None):
    create = AsyncMock(return_value=result, side_effect=error)
    configured = SimpleNamespace(decisions=SimpleNamespace(create=create))
    return SimpleNamespace(with_options=Mock(return_value=configured)), create


class DecisionPolicyTests(unittest.TestCase):
    def assert_conservative(self, value):
        self.assertTrue(value.needs_history)
        self.assertEqual((value.route, value.difficulty, value.allowed_actions, value.confidence),
                         ("other", 3.0, frozenset(), 0.0))

    def test_response_accepts_sdk_objects_and_answer_order_is_irrelevant(self):
        payload = response(actions=("add",), route="cart")
        payload["answers"].reverse()
        parsed = parse_routing_decision(Decision.model_construct(**payload))
        self.assertFalse(parsed.needs_history)
        self.assertEqual(parsed.allowed_actions, frozenset({"add"}))
        with self.assertRaises(FrozenInstanceError):
            parsed.route = "catalog"
        self.assertIsInstance(parsed.allowed_actions, frozenset)

    def test_uncertain_routing_keeps_context_and_independent_explicit_write(self):
        payload = response(history=0.79, route="cart", actions=("set",))
        answer(payload, "route")["confidence"] = 0.67
        parsed = parse_routing_decision(payload)
        self.assertTrue(parsed.needs_history)
        self.assertEqual((parsed.route, parsed.difficulty), ("other", 3.0))
        self.assertEqual(parsed.allowed_actions, frozenset({"set"}))
        self.assertLess(parsed.confidence, 0.85)

    def test_ambiguous_action_never_grants_write_or_fast_read_routing(self):
        payload = response()
        answer(payload, "add")["probability"] = 0.8
        parsed = parse_routing_decision(payload)
        self.assertEqual(parsed.allowed_actions, frozenset())
        self.assertTrue(parsed.needs_history)
        self.assertEqual(parsed.route, "other")

    def test_write_threshold_is_conservative_and_independent(self):
        payload = response(route="cart", actions=("set",))
        answer(payload, "add")["probability"] = 0.979
        self.assertEqual(parse_routing_decision(payload).allowed_actions, frozenset({"set"}))
        answer(payload, "add")["probability"] = 0.98
        self.assertEqual(parse_routing_decision(payload).allowed_actions, frozenset({"set", "add"}))

    def test_removal_never_grants_positive_quantity_set_permission(self):
        parsed = parse_routing_decision(response(route="cart", actions=("remove",)))
        self.assertEqual(parsed.allowed_actions, frozenset({"remove"}))

    def test_likely_write_below_write_threshold_never_becomes_easy_read(self):
        payload = response(route="cart")
        answer(payload, "add")["probability"] = 0.97
        parsed = parse_routing_decision(payload)
        self.assertEqual(parsed.allowed_actions, frozenset())
        self.assertTrue(parsed.needs_history)
        self.assertEqual((parsed.route, parsed.difficulty), ("other", 3.0))

    def test_fractional_difficulty_uses_weighted_level_index(self):
        payload = response()
        difficulty = answer(payload, "difficulty")
        difficulty["probabilities"][0]["probability"] = 0.8
        difficulty["probabilities"][1]["probability"] = 0.2
        difficulty["score"] = 0.2
        self.assertEqual(parse_routing_decision(payload).difficulty, 0.2)

    def test_missing_duplicate_unknown_and_refused_answers_fail_closed(self):
        payloads = [None, {}, {"answers": "bad"}, {"answers": response()["answers"][:-1]}]
        for change in (lambda p: p["answers"].__setitem__(-1, deepcopy(p["answers"][0])),
                       lambda p: answer(p, "add").update(name="unknown"),
                       lambda p: answer(p, "remove").update(type="refusal"),
                       lambda p: answer(p, "route").update(type="predicate"),
                       lambda p: answer(p, "set").update(name=None)):
            payload = response(actions=("add",))
            change(payload)
            payloads.append(payload)
        for payload in payloads:
            with self.subTest(payload=payload):
                self.assert_conservative(parse_routing_decision(payload))

    def test_numbers_require_finite_numbers_in_range_and_never_booleans(self):
        for name, field in (("add", "probability"), ("needs_history", "probability"),
                            ("route", "confidence"), ("difficulty", "confidence"),
                            ("difficulty", "score")):
            for bad in (True, False, "0.99", None, -0.1, 4, float("nan"), float("inf")):
                with self.subTest(name=name, field=field, bad=bad):
                    payload = response(actions=("add",))
                    answer(payload, name)[field] = bad
                    self.assert_conservative(parse_routing_decision(payload))

    def test_invalid_distributions_choices_and_score_consistency_fail_closed(self):
        changes = (
            lambda p: answer(p, "route").update(choice=True),
            lambda p: answer(p, "route").update(choice="made_up"),
            lambda p: answer(p, "route").update(probabilities=[]),
            lambda p: answer(p, "route")["probabilities"][1].update(value="categories"),
            lambda p: answer(p, "route")["probabilities"][0].update(probability=0.5),
            lambda p: answer(p, "difficulty")["probabilities"][0].update(value=True),
            lambda p: answer(p, "difficulty")["probabilities"][0].update(label="average"),
            lambda p: answer(p, "difficulty").update(score=2),
        )
        for change in changes:
            payload = response(actions=("add",))
            change(payload)
            self.assert_conservative(parse_routing_decision(payload))

    def test_route_probability_can_lower_overconfident_choice(self):
        payload = response()
        answer(payload, "route")["probabilities"][1]["probability"] = 0.6
        answer(payload, "route")["probabilities"][4]["probability"] = 0.4
        parsed = parse_routing_decision(payload)
        self.assertEqual(parsed.route, "other")
        self.assertEqual(parsed.confidence, 0.6)

    def test_rubric_has_four_distinct_levels_and_context_dependent_set_example(self):
        self.assertEqual([level["label"] for level in DIFFICULTY_LEVELS],
                         ["easy", "medium", "difficult", "very_difficult"])
        self.assertIn("Average/medium are the same level", DIFFICULTY_LEVELS[1]["description"])
        questions = {question["name"]: question for question in ROUTING_QUESTIONS}
        self.assertIn("make it three", questions["set"]["instructions"])
        self.assertIn("make it three", questions["needs_history"]["instructions"])
        for action in ("add", "set", "remove", "clear"):
            self.assertIn("CURRENT message only", questions[action]["instructions"])
            self.assertIn("hypothetical/conditional", questions[action]["instructions"])


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
        self.assertIsNot(request["questions"][0], ROUTING_QUESTIONS[0])

    async def test_labeled_message_answer_fixtures_map_to_expected_policy(self):
        # These are replay fixtures for policy behavior, not live linguistic evals.
        samples = (
            ("Find notebooks", False, "catalog", 0, ()),
            ("Show my cart", False, "cart", 0, ()),
            ("Add two notebooks", False, "cart", 0, ("add",)),
            ("Make it three.", True, "cart", 0, ("set",)),
            ("Remove that one", True, "cart", 0, ("remove",)),
            ("Esvazie meu carrinho", False, "cart", 0, ("clear",)),
            ("Yes", True, "other", 0, ()),
            ('Explain the phrase "add two notebooks"', False, "other", 0, ()),
            ("If I added two notebooks, what would it cost?", False, "catalog", 1, ()),
            ("Compare two notebooks under $10", False, "comparison", 1, ()),
        )
        for text, history, route, difficulty, actions in samples:
            with self.subTest(text=text):
                client, create = fake_client(response(history=0.99 if history else 0.01,
                                                     route=route, difficulty=difficulty, actions=actions))
                parsed = await classify_request(client, text)
                self.assertEqual(parsed.allowed_actions, frozenset(actions))
                self.assertEqual((parsed.needs_history, parsed.route, parsed.difficulty),
                                 (history, route, difficulty))
                self.assertEqual(create.await_args.kwargs["input"], text)

    async def test_availability_errors_are_sanitized_without_retry(self):
        request = httpx2.Request("POST", "https://api.openai.com/v1/decisions")
        errors = [APIConnectionError(message="private upstream detail", request=request)]
        errors.extend(APIStatusError("private upstream detail", response=httpx2.Response(code, request=request),
                                     body={"private": "detail"}) for code in (401, 403, 404, 429, 500, 503))
        for error in errors:
            client, create = fake_client(error=error)
            with self.assertRaisesRegex(RoutingUnavailable, "^Decision routing is unavailable$"):
                await classify_request(client, "Find notebooks")
            create.assert_awaited_once()
        with self.assertRaises(RoutingUnavailable):
            await classify_request(object(), "Find notebooks")

    async def test_malformed_or_refused_answers_do_not_trigger_availability_fallback(self):
        request = httpx2.Request("POST", "https://api.openai.com/v1/decisions")
        errors = [APIResponseValidationError(httpx2.Response(200, request=request), body={})]
        errors.extend(APIStatusError("invalid payload", response=httpx2.Response(code, request=request), body={})
                      for code in (400, 422))
        for error in errors:
            client, _ = fake_client(error=error)
            parsed = await classify_request(client, "Add two notebooks")
            self.assertEqual(parsed.allowed_actions, frozenset())
            self.assertEqual(parsed.confidence, 0.0)
        payload = response(actions=("add",))
        answer(payload, "set")["type"] = "refusal"
        client, _ = fake_client(payload)
        self.assertEqual((await classify_request(client, "Add two notebooks")).allowed_actions, frozenset())

    async def test_programming_errors_are_not_hidden_as_availability_failures(self):
        client, _ = fake_client(error=TypeError("implementation problem"))
        with self.assertRaises(TypeError):
            await classify_request(client, "Find notebooks")

    async def test_empty_input_cannot_authorize_or_send_a_request(self):
        client, create = fake_client(response(actions=("add",)))
        parsed = await classify_request(client, "  ")
        self.assertEqual(parsed.allowed_actions, frozenset())
        create.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
