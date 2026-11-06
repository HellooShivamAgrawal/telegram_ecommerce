"""Native typing and committed-cart feedback stay inside the handler lifetime."""

import asyncio
import importlib
import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from telegram.error import NetworkError, RetryAfter

os.environ.setdefault("BOT_TOKEN", "test-token")
for name in ("DB_HOST", "DB_USER", "DB_PASSWORD", "DB_NAME"):
    os.environ.setdefault(name, "test")

from telegram_ecommerce.agent.progress import ActivityMessage, TYPING_INTERVAL, progress_text

handler = importlib.import_module("telegram_ecommerce.handlers.catalog_assistant")


def context():
    return SimpleNamespace(user_data={}, chat_data={})


class TypingTests(unittest.IsolatedAsyncioTestCase):
    async def test_typing_refreshes_every_four_seconds_and_cleans_up(self):
        refreshed = asyncio.Event()
        cancelled = asyncio.Event()
        delays = []

        async def sleep(delay):
            delays.append(delay)
            if len(delays) == 1:
                return
            refreshed.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

        source = SimpleNamespace(reply_chat_action=AsyncMock())
        activity = ActivityMessage(source, context())
        with patch("telegram_ecommerce.agent.progress.asyncio.sleep", side_effect=sleep):
            async with activity.typing():
                await asyncio.wait_for(refreshed.wait(), 1)
            self.assertTrue(cancelled.is_set())
        self.assertEqual(delays, [TYPING_INTERVAL, TYPING_INTERVAL])
        self.assertEqual(source.reply_chat_action.await_count, 2)
        source.reply_chat_action.assert_awaited_with("typing")

    async def test_typing_rate_limit_is_respected_and_task_remains_cancellable(self):
        waiting = asyncio.Event()
        cancelled = asyncio.Event()
        delays = []

        async def sleep(delay):
            delays.append(delay)
            waiting.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

        source = SimpleNamespace(reply_chat_action=AsyncMock(side_effect=RetryAfter(60)))
        activity = ActivityMessage(source, context(), clock=lambda: 100.0)
        with patch("telegram_ecommerce.agent.progress.asyncio.sleep", side_effect=sleep):
            async with activity.typing():
                await asyncio.wait_for(waiting.wait(), 1)
        self.assertTrue(cancelled.is_set())
        self.assertEqual(delays, [60])
        self.assertEqual(activity.blocked_until, 160.0)
        source.reply_chat_action.assert_awaited_once()

    async def test_typing_failure_is_best_effort_and_logs_only_error_type(self):
        attempted = asyncio.Event()

        async def fail(*args):
            attempted.set()
            raise NetworkError("PRIVATE TOKEN OR TEXT")

        source = SimpleNamespace(reply_chat_action=AsyncMock(side_effect=fail))
        with patch("telegram_ecommerce.agent.progress.logger.warning") as warning:
            async with ActivityMessage(source, context()).typing():
                await asyncio.wait_for(attempted.wait(), 1)
        self.assertEqual(warning.call_args.args[1], "NetworkError")
        self.assertNotIn("PRIVATE", str(warning.call_args))

    async def test_status_rate_limit_extension_delays_pending_typing(self):
        now = [100.0]
        delays = []
        sent_at = []
        sent = asyncio.Event()

        async def typing(*args):
            sent_at.append(now[0])
            sent.set()
            await asyncio.Event().wait()

        source = SimpleNamespace(reply_chat_action=AsyncMock(side_effect=typing))
        activity = ActivityMessage(source, context(), clock=lambda: now[0])
        activity.blocked_until = 105.0

        async def sleep(delay):
            delays.append(delay)
            now[0] += delay
            if len(delays) == 1:
                activity._record_retry_after(RetryAfter(7))

        with patch("telegram_ecommerce.agent.progress.asyncio.sleep", side_effect=sleep):
            async with activity.typing():
                await asyncio.wait_for(sent.wait(), 1)
        self.assertEqual(sent_at, [112.0])
        self.assertEqual(delays, [5.0, 7.0])


class CommittedFeedbackTests(unittest.IsolatedAsyncioTestCase):
    async def test_save_waits_only_remaining_interval_and_precedes_final_reply(self):
        now = [100.0]
        delays = []
        status = SimpleNamespace(edit_text=AsyncMock())
        source = SimpleNamespace(reply_text=AsyncMock(return_value=status))
        activity = await ActivityMessage(source, context(), clock=lambda: now[0]).start()
        await activity.update({"name": "add_to_cart", "product_name": "Notebook"})
        now[0] += 0.3

        async def sleep(delay):
            delays.append(delay)
            now[0] += delay

        with patch("telegram_ecommerce.agent.progress.asyncio.sleep", side_effect=sleep):
            await activity.update({"name": "cart_saved"})
            await activity.update({"name": "cart_saved"})
            await activity.finish("Added two notebooks.")
        self.assertEqual(source.reply_text.await_count, 1)
        self.assertEqual(len(delays), 1)
        self.assertAlmostEqual(delays[0], 0.5)
        self.assertEqual([call.args[0] for call in status.edit_text.await_args_list], [
            "Adding Notebook to your cart…", "Cart change saved. Preparing your reply…", "Added two notebooks.",
        ])

    async def test_save_does_not_bypass_telegram_rate_limit(self):
        status = SimpleNamespace(edit_text=AsyncMock(side_effect=RetryAfter(30)))
        source = SimpleNamespace(reply_text=AsyncMock(return_value=status))
        activity = await ActivityMessage(source, context(), clock=lambda: 100.0).start()
        await activity.update({"name": "add_to_cart"})
        with patch("telegram_ecommerce.agent.progress.asyncio.sleep", new_callable=AsyncMock) as sleep:
            await activity.update({"name": "cart_saved"})
        sleep.assert_not_awaited()
        status.edit_text.assert_awaited_once()

    def test_commit_status_is_localized_and_requires_its_specific_event(self):
        ctx = context()
        self.assertNotIn("saved", progress_text({"name": "add_to_cart"}, ctx))
        ctx.user_data["language"] = "pt"
        self.assertEqual(progress_text({"name": "cart_saved"}, ctx),
                         "Alteração no carrinho salva. Preparando sua resposta…")


class HandlerTypingTests(unittest.IsolatedAsyncioTestCase):
    async def test_typing_failure_does_not_prevent_assistant_or_final_reply(self):
        attempted = asyncio.Event()

        async def failed_typing(*args):
            attempted.set()
            raise NetworkError("PRIVATE FAILURE")

        async def work(*args, **kwargs):
            await attempted.wait()
            return "Added a notebook."

        status = SimpleNamespace(edit_text=AsyncMock())
        message = SimpleNamespace(text="Add a notebook", reply_text=AsyncMock(return_value=status),
                                  reply_chat_action=AsyncMock(side_effect=failed_typing))
        update = SimpleNamespace(effective_message=message, effective_chat=SimpleNamespace(id=7), update_id=42)
        with patch.object(handler, "answer_catalog_question", side_effect=work) as answer:
            await asyncio.wait_for(handler.answer_message(update, context()), 1)
        answer.assert_awaited_once()
        status.edit_text.assert_awaited_once_with("Added a notebook.")

    async def run_handler(self, *, fail=False, cancel=False):
        typing_started = asyncio.Event()
        typing_stopped = asyncio.Event()
        events = []

        async def typing(*args):
            events.append("typing")
            typing_started.set()
            try:
                await asyncio.Event().wait()
            finally:
                events.append("typing_stopped")
                typing_stopped.set()

        async def edit(text):
            events.append(text)
            if text != "Cart change saved. Preparing your reply…":
                self.assertTrue(typing_stopped.is_set())

        status = SimpleNamespace(edit_text=AsyncMock(side_effect=edit))
        message = SimpleNamespace(text="Add a notebook", reply_text=AsyncMock(return_value=status),
                                  reply_chat_action=AsyncMock(side_effect=typing))
        update = SimpleNamespace(effective_message=message, effective_chat=SimpleNamespace(id=7), update_id=42)

        async def work(*args, **kwargs):
            await typing_started.wait()
            if cancel:
                await asyncio.Event().wait()
            if fail:
                raise RuntimeError("PRIVATE FAILURE")
            await kwargs["on_progress"]({"name": "cart_saved"})
            return "Added a notebook."

        with patch.object(handler, "answer_catalog_question", side_effect=work) as answer:
            task = asyncio.create_task(handler.answer_message(update, context()))
            await asyncio.wait_for(typing_started.wait(), 1)
            if cancel:
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await task
            else:
                await asyncio.wait_for(task, 1)
        answer.assert_awaited_once()
        self.assertTrue(typing_stopped.is_set())
        return events, status

    async def test_typing_stops_before_final_answer_and_commit_status_stays_ordered(self):
        events, status = await self.run_handler()
        self.assertEqual(events, ["typing", "Cart change saved. Preparing your reply…",
                                  "typing_stopped", "Added a notebook."])
        self.assertEqual(status.edit_text.await_count, 2)

    async def test_typing_stops_before_error_reply(self):
        events, status = await self.run_handler(fail=True)
        self.assertEqual(events[:2], ["typing", "typing_stopped"])
        self.assertNotIn("PRIVATE FAILURE", events[-1])
        status.edit_text.assert_awaited_once()

    async def test_handler_cancellation_stops_typing_without_a_final_reply(self):
        events, status = await self.run_handler(cancel=True)
        self.assertEqual(events, ["typing", "typing_stopped"])
        status.edit_text.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
