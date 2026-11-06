"""Behavior checks for Telegram cart scope, activity delivery, and chat context."""

import importlib
import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from telegram.error import BadRequest, NetworkError, RetryAfter

os.environ.setdefault("BOT_TOKEN", "test-token")
for name in ("DB_HOST", "DB_USER", "DB_PASSWORD", "DB_NAME"):
    os.environ.setdefault(name, "test")

from telegram_ecommerce.agent.catalog import CartActionRejectedError, CartUpdatedResponseError
from telegram_ecommerce.agent.progress import ActivityMessage, progress_text

cart_handler = importlib.import_module("telegram_ecommerce.handlers.cart")
assistant_handler = importlib.import_module("telegram_ecommerce.handlers.catalog_assistant")


EMPTY_CART = {"items": [], "total": "0.00", "currency": "USD", "item_count": 0}


def context(**kwargs):
    return SimpleNamespace(user_data={}, chat_data={}, args=[], **kwargs)


def update(chat_id=-100123, update_id=55):
    message = SimpleNamespace(text="Add a notebook", reply_text=AsyncMock())
    return SimpleNamespace(
        effective_chat=SimpleNamespace(id=chat_id),
        effective_user=SimpleNamespace(id=999),
        effective_message=message,
        update_id=update_id,
    )


class CartCommandTests(unittest.IsolatedAsyncioTestCase):
    async def test_read_uses_current_chat_not_current_user(self):
        event = update()
        with patch.object(cart_handler.cart_service, "get_cart", return_value=EMPTY_CART) as read:
            await cart_handler.cart_command(event, context())
        read.assert_called_once_with(-100123)

    async def test_add_uses_trusted_chat_and_update_id(self):
        event = update()
        ctx = context()
        ctx.args = ["add", "7", "2"]
        with patch.object(cart_handler.cart_service, "mutate_cart", return_value={"cart": EMPTY_CART}) as mutate:
            await cart_handler.cart_command(event, ctx)
        mutate.assert_called_once_with(
            -100123, "add", request_id="command:55", product_id=7, quantity=2,
        )

    async def test_extra_arguments_never_reach_service(self):
        ctx = context()
        ctx.args = ["clear", "12345"]
        with patch.object(cart_handler.cart_service, "mutate_cart") as mutate:
            await cart_handler.cart_command(update(), ctx)
        mutate.assert_not_called()

    async def test_replayed_command_displays_fresh_cart(self):
        event = update()
        ctx = context()
        ctx.args = ["clear"]
        with patch.object(cart_handler.cart_service, "mutate_cart",
                          return_value={"cart": {"items": []}, "replayed": True}), patch.object(
            cart_handler.cart_service, "get_cart", return_value=EMPTY_CART,
        ) as read:
            await cart_handler.cart_command(event, ctx)
        read.assert_called_once_with(-100123)

    async def test_service_failure_does_not_expose_exception(self):
        event = update()
        with patch.object(cart_handler.cart_service, "get_cart", side_effect=RuntimeError("PRIVATE DATABASE ERROR")):
            await cart_handler.cart_command(event, context())
        reply = event.effective_message.reply_text.await_args.args[0]
        self.assertNotIn("PRIVATE DATABASE ERROR", reply)
        self.assertIn("/cart", reply)

    async def test_large_cart_replies_stay_inside_telegram_limit(self):
        event = update()
        await cart_handler._reply_cart(event.effective_message, "A" * 100 + "\n" + ("B" * 100 + "\n") * 90)
        self.assertGreater(event.effective_message.reply_text.await_count, 1)
        self.assertTrue(all(len(call.args[0]) <= 3900 for call in event.effective_message.reply_text.await_args_list))


class ActivityTests(unittest.IsolatedAsyncioTestCase):
    def test_search_label_is_short_plain_text(self):
        text = progress_text({"name": "search_products", "arguments": {"query": "  Demo\nNotebook\u202e "}}, context())
        self.assertEqual(text, "Finding Demo Notebook…")
        self.assertIsNone(progress_text({"name": "invented_tool"}, context()))

    async def test_progress_deduplicates_and_throttles_but_final_is_delivered(self):
        status = SimpleNamespace(edit_text=AsyncMock())
        source = SimpleNamespace(reply_text=AsyncMock(return_value=status))
        clock = [100.0]
        activity = await ActivityMessage(source, context(), clock=lambda: clock[0]).start()
        event = {"name": "search_products", "arguments": {"query": "notebook"}}
        await activity.update(event)
        await activity.update(event)
        await activity.update({"name": "get_cart"})
        self.assertEqual(status.edit_text.await_count, 1)
        clock[0] += 1
        await activity.update({"name": "get_cart"})
        await activity.finish("Your notebook costs $9.99.")
        self.assertEqual(status.edit_text.await_count, 3)
        self.assertEqual(status.edit_text.await_args.args[0], "Your notebook costs $9.99.")

    async def test_progress_failure_does_not_prevent_final_response(self):
        status = SimpleNamespace(edit_text=AsyncMock(side_effect=[NetworkError("private"), None]))
        source = SimpleNamespace(reply_text=AsyncMock(return_value=status))
        activity = await ActivityMessage(source, context()).start()
        await activity.update({"name": "get_cart"})
        await activity.finish("Your cart is empty.")
        self.assertEqual(status.edit_text.await_count, 2)

    async def test_deleted_status_falls_back_to_new_reply(self):
        status = SimpleNamespace(edit_text=AsyncMock(side_effect=BadRequest("message to edit not found")))
        source = SimpleNamespace(reply_text=AsyncMock(return_value=status))
        activity = await ActivityMessage(source, context()).start()
        await activity.finish("Saved.")
        source.reply_text.assert_awaited_with("Saved.")

    async def test_failed_initial_status_still_delivers_answer(self):
        source = SimpleNamespace(reply_text=AsyncMock(side_effect=[NetworkError("private"), None]))
        activity = await ActivityMessage(source, context()).start()
        await activity.finish("Saved.")
        source.reply_text.assert_awaited_with("Saved.")

    async def test_flood_wait_is_respected_for_final_delivery(self):
        status = SimpleNamespace(edit_text=AsyncMock(side_effect=[RetryAfter(2), None]))
        source = SimpleNamespace(reply_text=AsyncMock(return_value=status))
        activity = await ActivityMessage(source, context(), clock=lambda: 100.0).start()
        await activity.update({"name": "get_cart"})
        await activity.update({"name": "clear_cart"})
        self.assertEqual(status.edit_text.await_count, 1)
        with patch("telegram_ecommerce.agent.progress.asyncio.sleep", new_callable=AsyncMock) as sleep:
            await activity.finish("Saved.")
        sleep.assert_awaited_once_with(2.1)
        self.assertEqual(status.edit_text.await_count, 2)


class AssistantHandlerTests(unittest.IsolatedAsyncioTestCase):
    async def test_assistant_receives_chat_scope_and_chat_history_only(self):
        event = update()
        ctx = context()
        ctx.user_data[assistant_handler.HISTORY_KEY] = [{"role": "user", "content": "Other chat secret"}]
        ctx.chat_data[assistant_handler.HISTORY_KEY] = [{"role": "user", "content": "A notebook"}]
        ctx.chat_data[assistant_handler.HISTORY_TIME_KEY] = 100.0
        with patch.object(assistant_handler.time, "time", return_value=101.0), patch.object(
            assistant_handler, "answer_catalog_question", new_callable=AsyncMock, return_value="Added."
        ) as answer:
            await assistant_handler.answer_message(event, ctx)
        kwargs = answer.await_args.kwargs
        self.assertEqual(kwargs["chat_id"], -100123)
        self.assertEqual(kwargs["request_id"], "telegram:55")
        self.assertEqual(kwargs["history"], [{"role": "user", "content": "A notebook"}])
        self.assertEqual(ctx.chat_data[assistant_handler.HISTORY_KEY][-1]["content"], "Added.")

    async def test_history_expires_and_is_bounded(self):
        event = update()
        ctx = context()
        ctx.chat_data[assistant_handler.HISTORY_KEY] = [{"role": "user", "content": str(i)} for i in range(10)]
        ctx.chat_data[assistant_handler.HISTORY_TIME_KEY] = 100.0
        with patch.object(assistant_handler.time, "time", return_value=2001.0), patch.object(
            assistant_handler, "answer_catalog_question", new_callable=AsyncMock, return_value="Answer."
        ) as answer:
            await assistant_handler.answer_message(event, ctx)
        self.assertEqual(answer.await_args.kwargs["history"], [])
        self.assertEqual(len(ctx.chat_data[assistant_handler.HISTORY_KEY]), 2)

    async def test_saved_cart_failure_is_reported_without_repeating_write(self):
        event = update()
        status = SimpleNamespace(edit_text=AsyncMock())
        event.effective_message.reply_text.return_value = status
        with patch.object(assistant_handler, "answer_catalog_question", new_callable=AsyncMock,
                          side_effect=CartUpdatedResponseError("saved")) as answer:
            await assistant_handler.answer_message(event, context())
        answer.assert_awaited_once()
        final = status.edit_text.await_args.args[0]
        self.assertIn("saved", final)
        self.assertIn("/cart", final)

    async def test_partial_cart_failure_never_claims_nothing_changed(self):
        event = update()
        status = SimpleNamespace(edit_text=AsyncMock())
        event.effective_message.reply_text.return_value = status
        with patch.object(assistant_handler, "answer_catalog_question", new_callable=AsyncMock,
                          side_effect=CartActionRejectedError("insufficient_stock", saved=True)):
            await assistant_handler.answer_message(event, context())
        final = status.edit_text.await_args.args[0]
        self.assertIn("Some cart changes were saved", final)
        self.assertNotIn("not changed", final)

    async def test_undeliverable_final_reply_does_not_rerun_assistant(self):
        event = update()
        with patch.object(assistant_handler, "answer_catalog_question", new_callable=AsyncMock,
                          return_value="Saved.") as answer, patch.object(
            ActivityMessage, "finish", new_callable=AsyncMock, return_value=False,
        ), patch.object(assistant_handler.logger, "warning") as warning:
            await assistant_handler.answer_message(event, context())
        answer.assert_awaited_once()
        self.assertEqual(warning.call_args.args[1], event.update_id)


if __name__ == "__main__":
    unittest.main()
