"""Final Telegram delivery is bounded and independent of cart mutations."""

import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from telegram.error import BadRequest, Forbidden, RetryAfter

os.environ.setdefault("BOT_TOKEN", "test-token")

from telegram_ecommerce.agent.progress import ActivityMessage, MAX_FINAL_ATTEMPTS


class FinalDeliveryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.now = 100.0
        self.sleeps = []
        self.context = SimpleNamespace(user_data={})

    async def sleep(self, delay):
        self.sleeps.append(delay)
        self.now += delay

    async def activity(self, status, source=None):
        source = source or SimpleNamespace(reply_text=AsyncMock(return_value=status))
        activity = ActivityMessage(source, self.context, clock=lambda: self.now)
        await activity.start()
        return activity, source

    async def test_twice_rate_limited_final_edit_eventually_succeeds(self):
        status = SimpleNamespace(edit_text=AsyncMock(side_effect=[RetryAfter(1), RetryAfter(2), None]))
        activity, source = await self.activity(status)
        with patch("telegram_ecommerce.agent.progress.asyncio.sleep", side_effect=self.sleep):
            delivered = await activity.finish("Cart saved.")
        self.assertTrue(delivered)
        self.assertEqual(status.edit_text.await_count, 3)
        self.assertEqual(source.reply_text.await_count, 1)
        self.assertAlmostEqual(self.sleeps[0], 1.1)
        self.assertAlmostEqual(self.sleeps[1], 2.1)

    async def test_twice_rate_limited_fallback_reply_succeeds_with_shared_budget(self):
        status = SimpleNamespace(edit_text=AsyncMock(side_effect=BadRequest("message deleted")))
        source = SimpleNamespace(reply_text=AsyncMock(side_effect=[status, RetryAfter(1), RetryAfter(1), None]))
        activity, _ = await self.activity(status, source)
        with patch("telegram_ecommerce.agent.progress.asyncio.sleep", side_effect=self.sleep):
            delivered = await activity.finish("Cart saved.")
        self.assertTrue(delivered)
        self.assertEqual(status.edit_text.await_count, 1)
        self.assertEqual(source.reply_text.await_count, 4)

    async def test_permanent_edit_and_reply_failure_returns_false(self):
        status = SimpleNamespace(edit_text=AsyncMock(side_effect=BadRequest("message deleted")))
        source = SimpleNamespace(reply_text=AsyncMock(side_effect=[status, Forbidden("bot blocked")]))
        activity, _ = await self.activity(status, source)
        self.assertFalse(await activity.finish("Cart saved."))
        self.assertEqual(status.edit_text.await_count, 1)
        self.assertEqual(source.reply_text.await_count, 2)

    async def test_long_rate_limit_preserves_deadline_without_flooding_fallback(self):
        status = SimpleNamespace(edit_text=AsyncMock(side_effect=RetryAfter(3600)))
        activity, source = await self.activity(status)
        with patch("telegram_ecommerce.agent.progress.asyncio.sleep", new_callable=AsyncMock) as sleep:
            self.assertFalse(await activity.finish("Cart saved."))
            self.assertFalse(await activity.finish("Cart saved."))
        sleep.assert_not_awaited()
        self.assertEqual(status.edit_text.await_count, 1)
        self.assertEqual(source.reply_text.await_count, 1)
        self.assertEqual(activity.blocked_until, 3700.0)

    async def test_repeated_rate_limits_stop_at_attempt_limit(self):
        status = SimpleNamespace(edit_text=AsyncMock(side_effect=RetryAfter(1)))
        activity, source = await self.activity(status)
        with patch("telegram_ecommerce.agent.progress.asyncio.sleep", side_effect=self.sleep):
            self.assertFalse(await activity.finish("Cart saved."))
        self.assertEqual(status.edit_text.await_count, MAX_FINAL_ATTEMPTS)
        self.assertEqual(source.reply_text.await_count, 1)
        self.assertGreater(activity.blocked_until, self.now)

    async def test_cumulative_wait_budget_stops_before_long_stall(self):
        status = SimpleNamespace(edit_text=AsyncMock(side_effect=RetryAfter(6)))
        activity, source = await self.activity(status)
        with patch("telegram_ecommerce.agent.progress.asyncio.sleep", side_effect=self.sleep):
            self.assertFalse(await activity.finish("Cart saved."))
        self.assertEqual(status.edit_text.await_count, 2)
        self.assertEqual(len(self.sleeps), 1)
        self.assertLess(sum(self.sleeps), 10.0)
        self.assertEqual(source.reply_text.await_count, 1)

    async def test_initial_status_rate_limit_also_applies_to_final_reply(self):
        source = SimpleNamespace(reply_text=AsyncMock(side_effect=[RetryAfter(2), None]))
        activity, _ = await self.activity(None, source)
        with patch("telegram_ecommerce.agent.progress.asyncio.sleep", side_effect=self.sleep):
            self.assertTrue(await activity.finish("Cart saved."))
        self.assertAlmostEqual(self.sleeps[0], 2.1)
        self.assertEqual(source.reply_text.await_count, 2)


if __name__ == "__main__":
    unittest.main()
