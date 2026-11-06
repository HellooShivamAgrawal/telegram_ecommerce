"""Best-effort Telegram activity messages for real tool dispatches."""

import asyncio
import time
import unicodedata
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass
from datetime import timedelta

from telegram.error import RetryAfter, TelegramError
from telegram.constants import ChatAction

from telegram_ecommerce.language import get_text
from telegram_ecommerce.utils.log import logger


MIN_EDIT_INTERVAL = 0.8
TYPING_INTERVAL = 4.0
MAX_FINAL_ATTEMPTS = 4
MAX_FINAL_WAIT_SECONDS = 10.0


@dataclass
class _DeliveryBudget:
    attempts_left: int = MAX_FINAL_ATTEMPTS
    wait_left: float = MAX_FINAL_WAIT_SECONDS


def short_label(value):
    """Keep catalog and search labels readable without Telegram formatting."""
    if not isinstance(value, str):
        return ""
    clean = " ".join(value.split())
    return "".join(char for char in clean if not unicodedata.category(char).startswith("C"))[:100]


def progress_text(event, context):
    name = event.get("name")
    arguments = event.get("arguments") or {}
    product = short_label(event.get("product_name"))
    if name == "search_products":
        query = short_label(arguments.get("query"))
        return (get_text("activity_finding", context).format(product=query)
                if query else get_text("activity_searching", context))
    if name == "list_categories":
        return get_text("activity_categories", context)
    if name == "get_product":
        return (get_text("activity_checking_product", context).format(product=product)
                if product else get_text("activity_checking", context))
    if name == "get_cart":
        return get_text("activity_cart", context)
    if name == "cart_saved":
        return get_text("activity_cart_saved", context)
    if name == "clear_cart":
        return get_text("activity_clearing_cart", context)
    keys = {
        "add_to_cart": ("activity_adding_product", "activity_adding"),
        "update_cart_item": ("activity_updating_product", "activity_updating"),
        "remove_from_cart": ("activity_removing_product", "activity_removing"),
    }
    if name in keys:
        specific, generic = keys[name]
        return get_text(specific, context).format(product=product) if product else get_text(generic, context)
    return None


def _retry_seconds(error):
    delay = error.retry_after
    return delay.total_seconds() if isinstance(delay, timedelta) else float(delay)


class ActivityMessage:
    """Edit one status message; delivery failures do not abort tool operations."""

    def __init__(self, source, context, *, clock=time.monotonic):
        self.source = source
        self.context = context
        self.clock = clock
        self.message = None
        self.last_text = None
        self.last_progress_at = None
        self.blocked_until = 0.0

    def _record_retry_after(self, error):
        self.blocked_until = max(
            self.blocked_until, self.clock() + max(0.0, _retry_seconds(error)),
        )

    async def _typing(self):
        while True:
            blocked_for = self.blocked_until - self.clock()
            if blocked_for > 0:
                await asyncio.sleep(blocked_for)
                continue  # A status edit may have extended the shared deadline.
            delay = TYPING_INTERVAL
            try:
                await self.source.reply_chat_action(ChatAction.TYPING)
            except RetryAfter as error:
                self._record_retry_after(error)
                delay = max(delay, _retry_seconds(error))
            except Exception as error:
                # Optional activity must never interfere with shopping or delivery.
                logger.warning("Could not send typing indicator (%s)", type(error).__name__)
                return
            await asyncio.sleep(delay)

    @asynccontextmanager
    async def typing(self):
        """Keep Telegram's native activity alive only while work is pending."""
        task = asyncio.create_task(self._typing())
        try:
            yield
        finally:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task

    async def start(self):
        text = get_text("activity_thinking", self.context)
        try:
            self.message = await self.source.reply_text(text)
            self.last_text = text
        except RetryAfter as error:
            self._record_retry_after(error)
        except TelegramError as error:
            logger.warning("Could not send activity message (%s)", type(error).__name__)
        return self

    async def update(self, event):
        text = progress_text(event, self.context)
        now = self.clock()
        if not text or self.message is None or text == self.last_text or now < self.blocked_until:
            return
        if self.last_progress_at is not None:
            remaining = MIN_EDIT_INTERVAL - (now - self.last_progress_at)
            if remaining > 0:
                if event.get("name") != "cart_saved":
                    return
                # A confirmed save deserves a visible transition, without a queue.
                await asyncio.sleep(min(remaining, MIN_EDIT_INTERVAL))
                now = self.clock()
                if now < self.blocked_until:
                    return
        self.last_progress_at = now
        try:
            await self.message.edit_text(text)
            self.last_text = text
        except RetryAfter as error:
            self._record_retry_after(error)
        except TelegramError as error:
            logger.warning("Could not update activity message (%s)", type(error).__name__)

    async def _deliver(self, callback, text, budget):
        """Return (delivered, may_try_fallback), sharing one bounded retry budget."""
        while budget.attempts_left > 0:
            delay = self.blocked_until - self.clock()
            if delay > 0:
                wait = delay + 0.1
                if wait > budget.wait_left:
                    logger.warning("Final answer delivery deferred by Telegram rate limit")
                    return False, False
                await asyncio.sleep(wait)
                budget.wait_left -= wait
            budget.attempts_left -= 1
            try:
                await callback(text)
                return True, False
            except RetryAfter as error:
                self._record_retry_after(error)
            except TelegramError as error:
                logger.warning("Final answer delivery failed (%s)", type(error).__name__)
                return False, True
        logger.warning("Final answer delivery exhausted its retry budget")
        return False, False

    async def finish(self, text):
        """Return whether the final answer was delivered, without repeating any tool."""
        if text == self.last_text:
            return True
        budget = _DeliveryBudget()
        if self.message is not None:
            delivered, may_fallback = await self._deliver(self.message.edit_text, text, budget)
            if delivered:
                self.last_text = text
                return True
            if not may_fallback:
                # A new reply would hit the same rate limit; preserve its deadline.
                return False
        delivered, _ = await self._deliver(self.source.reply_text, text, budget)
        if delivered:
            self.last_text = text
        return delivered
