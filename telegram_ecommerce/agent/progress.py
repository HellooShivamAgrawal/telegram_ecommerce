"""Best-effort Telegram activity messages for real tool dispatches."""

import asyncio
import time
import unicodedata
from datetime import timedelta

from telegram.error import RetryAfter, TelegramError

from telegram_ecommerce.language import get_text
from telegram_ecommerce.utils.log import logger


MIN_EDIT_INTERVAL = 0.8


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
    """Edit one status message; progress delivery never blocks a tool operation."""

    def __init__(self, source, context, *, clock=time.monotonic):
        self.source = source
        self.context = context
        self.clock = clock
        self.message = None
        self.last_text = None
        self.last_progress_at = None
        self.blocked_until = 0.0

    async def start(self):
        text = get_text("activity_thinking", self.context)
        try:
            self.message = await self.source.reply_text(text)
            self.last_text = text
        except RetryAfter as error:
            self.blocked_until = self.clock() + _retry_seconds(error)
        except TelegramError as error:
            logger.warning("Could not send activity message (%s)", type(error).__name__)
        return self

    async def update(self, event):
        text = progress_text(event, self.context)
        now = self.clock()
        if not text or self.message is None or text == self.last_text or now < self.blocked_until:
            return
        if self.last_progress_at is not None and now - self.last_progress_at < MIN_EDIT_INTERVAL:
            return
        self.last_progress_at = now
        try:
            await self.message.edit_text(text)
            self.last_text = text
        except RetryAfter as error:
            self.blocked_until = now + _retry_seconds(error)
        except TelegramError as error:
            logger.warning("Could not update activity message (%s)", type(error).__name__)

    async def _deliver(self, callback, text):
        try:
            return await callback(text)
        except RetryAfter as error:
            await asyncio.sleep(_retry_seconds(error) + 0.1)
            return await callback(text)

    async def finish(self, text):
        delay = self.blocked_until - self.clock()
        if delay > 0:
            await asyncio.sleep(delay + 0.1)
        if self.message is not None:
            if text == self.last_text:
                return
            try:
                await self._deliver(self.message.edit_text, text)
                self.last_text = text
                return
            except TelegramError as error:
                logger.warning("Could not replace activity message (%s)", type(error).__name__)
        # An unavailable/deleted status message must not swallow the actual answer.
        await self._deliver(self.source.reply_text, text)
