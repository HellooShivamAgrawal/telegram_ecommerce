import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

os.environ.setdefault("BOT_TOKEN", "test-token")

from telegram_ecommerce.agent.catalog import (
    answer_catalog_question,
    CartUpdatedResponseError,
    CART_TOOLS,
    execute_catalog_tool,
    validate_tool_arguments,
)


class FakeResponses:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.requests = []

    async def create(self, **kwargs):
        self.requests.append(kwargs)
        response = next(self.responses)
        if isinstance(response, Exception):
            raise response
        return response


def fake_response(identifier, output, text=""):
    return SimpleNamespace(id=identifier, output=output, output_text=text)


def call(name, arguments, call_id="call1"):
    import json
    return SimpleNamespace(type="function_call", name=name,
                           arguments=json.dumps(arguments), call_id=call_id)


class CatalogAgentTests(unittest.IsolatedAsyncioTestCase):
    async def test_returns_answer_after_tool_result(self):
        responses = FakeResponses([
            fake_response("r1", [SimpleNamespace(
                type="function_call", name="list_categories", arguments="{}",
                call_id="c1")]),
            fake_response("r2", [], "We have books. Browse with /show_categories."),
        ])
        with patch("telegram_ecommerce.agent.catalog.execute_catalog_tool",
                   return_value={"categories": ["books"]}) as tool:
            answer = await answer_catalog_question(
                "What do you sell?", client=SimpleNamespace(responses=responses))
        self.assertIn("books", answer)
        tool.assert_called_once_with("list_categories", {})
        self.assertEqual(responses.requests[1]["previous_response_id"], "r1")
        self.assertEqual(responses.requests[1]["input"][0]["call_id"], "c1")

    async def test_rejects_oversized_message_before_api_call(self):
        with self.assertRaises(ValueError):
            await answer_catalog_question("x" * 1001, client=object())

    async def test_bounds_tool_rounds(self):
        calls = [SimpleNamespace(type="function_call", name="list_categories",
                                 arguments="{}", call_id=f"c{i}") for i in range(3)]
        responses = FakeResponses([
            fake_response("r1", calls), fake_response("r2", calls),
        ])
        with patch("telegram_ecommerce.agent.catalog.execute_catalog_tool",
                   return_value={"categories": []}), patch("telegram_ecommerce.agent.catalog.MAX_TOOL_CALLS", 4):
            with self.assertRaisesRegex(RuntimeError, "tool budget"):
                await answer_catalog_question(
                    "Categories?", client=SimpleNamespace(responses=responses))

    async def test_duplicate_model_writes_are_deduplicated_and_scoped(self):
        responses = FakeResponses([
            fake_response("r1", [call("search_products", {"query": "notebook"})]),
            fake_response("r2", [call("add_to_cart", {"product_id": 7, "quantity": 2}, "a"),
                                  call("add_to_cart", {"product_id": 7, "quantity": 2}, "b")]),
            fake_response("r3", [], "Added two notebooks."),
        ])
        events = []

        async def progress(event):
            events.append(event)

        with patch("telegram_ecommerce.agent.catalog.execute_tool", side_effect=[
            {"products": [{"id": 7, "name": "Notebook"}]},
            {"cart": {"items": [{"product_id": 7, "name": "Notebook", "quantity": 2}]}, "replayed": False},
        ]) as tool:
            await answer_catalog_question("Add two notebooks", chat_id=-1234, request_id="telegram:10",
                                          on_progress=progress, client=SimpleNamespace(responses=responses))
        self.assertEqual(tool.call_count, 2)
        self.assertEqual(tool.call_args.kwargs["chat_id"], -1234)
        self.assertTrue(tool.call_args.kwargs["operation_key"].endswith(":0"))
        self.assertEqual(events[0]["arguments"], {"query": "notebook"})
        self.assertEqual(events[1]["product_name"], "Notebook")

    async def test_progress_failure_does_not_abort_the_tool(self):
        responses = FakeResponses([
            fake_response("r1", [call("list_categories", {})]), fake_response("r2", [], "Books."),
        ])
        with patch("telegram_ecommerce.agent.catalog.execute_catalog_tool", return_value={"categories": ["books"]}) as tool:
            result = await answer_catalog_question("Categories?", on_progress=AsyncMock(side_effect=RuntimeError()),
                                                   client=SimpleNamespace(responses=responses))
        self.assertEqual(result, "Books.")
        tool.assert_called_once()

    async def test_provider_failure_after_commit_reports_saved_cart(self):
        responses = FakeResponses([
            fake_response("r1", [call("clear_cart", {})]), RuntimeError("API unavailable"),
        ])
        with patch("telegram_ecommerce.agent.catalog.execute_tool", return_value={"cart": {"items": []}}):
            with self.assertRaises(CartUpdatedResponseError):
                await answer_catalog_question("Clear my cart", chat_id=42, request_id="telegram:11",
                                              client=SimpleNamespace(responses=responses))

    async def test_guessed_product_id_cannot_be_written(self):
        responses = FakeResponses([
            fake_response("r1", [call("add_to_cart", {"product_id": 700, "quantity": 2})]),
            fake_response("r2", [], "I need to look up that product."),
        ])
        with patch("telegram_ecommerce.agent.catalog.execute_tool") as tool:
            await answer_catalog_question("Add notebook", chat_id=42, request_id="telegram:12",
                                          client=SimpleNamespace(responses=responses))
        tool.assert_not_called()

    async def test_retried_turn_uses_the_same_persistent_operation_key(self):
        keys = []
        for _ in range(2):
            responses = FakeResponses([
                fake_response("r1", [call("clear_cart", {})]), fake_response("r2", [], "Cart cleared."),
            ])
            with patch("telegram_ecommerce.agent.catalog.execute_tool", return_value={"cart": {"items": []}}) as tool:
                await answer_catalog_question("Clear cart", chat_id=42, request_id="telegram:13",
                                              client=SimpleNamespace(responses=responses))
                keys.append(tool.call_args.kwargs["operation_key"])
        self.assertEqual(keys[0], keys[1])

    async def test_replayed_request_with_different_payload_stops_further_writes(self):
        responses = FakeResponses([fake_response("r1", [call("clear_cart", {})])])
        with patch("telegram_ecommerce.agent.catalog.execute_tool",
                   return_value={"error": "Request conflict", "code": "request_conflict"}) as tool:
            with self.assertRaises(CartUpdatedResponseError):
                await answer_catalog_question("Clear cart", chat_id=42, request_id="telegram:14",
                                              client=SimpleNamespace(responses=responses))
        tool.assert_called_once()


class CatalogToolTests(unittest.TestCase):
    def test_refuses_unlisted_actions(self):
        with self.assertRaises(ValueError):
            execute_catalog_tool("purchase_product", {"product_id": 1})

    def test_model_cannot_supply_another_chat_id(self):
        for tool in CART_TOOLS:
            self.assertNotIn("chat_id", tool["parameters"]["properties"])
        with self.assertRaises(ValueError):
            validate_tool_arguments("get_cart", {"chat_id": 900})

    def test_quantity_validation_rejects_boolean_and_overflow(self):
        for quantity in (True, -1, 100, "2"):
            with self.subTest(quantity=quantity), self.assertRaises(ValueError):
                validate_tool_arguments("add_to_cart", {"product_id": 1, "quantity": quantity})


if __name__ == "__main__":
    unittest.main()
