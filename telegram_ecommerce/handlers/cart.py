"""Deterministic cart commands scoped to the current Telegram chat."""

import asyncio

from telegram.ext import CommandHandler

from telegram_ecommerce.agent.progress import short_label
from telegram_ecommerce.database import cart as cart_service
from telegram_ecommerce.language import get_text
from telegram_ecommerce.utils.log import logger


ERROR_KEYS = {
    "invalid_quantity": "cart_invalid_quantity",
    "quantity_limit": "cart_invalid_quantity",
    "insufficient_stock": "cart_insufficient_stock",
    "product_not_found": "cart_product_not_found",
    "invalid_product": "cart_product_not_found",
    "cart_full": "cart_full",
    "request_conflict": "cart_request_conflict",
}


def render_cart(cart, context):
    if not cart["items"]:
        return get_text("cart_empty", context)
    currency = cart["currency"]
    lines = [get_text("cart_heading", context)]
    for item in cart["items"]:
        lines.append(f"\n#{item['product_id']} {short_label(item['name'])}")
        lines.append(f"{item['quantity']} × {currency} {item['unit_price']} = {currency} {item['line_total']}")
        if not item["stock_sufficient"]:
            lines.append(get_text("cart_stock_changed", context).format(quantity=item["available_quantity"]))
    lines.extend([
        "\n" + get_text("cart_total", context).format(currency=currency, total=cart["total"]),
        get_text("cart_no_reservation", context),
    ])
    return "\n".join(lines)


def _parse_command(args):
    if not args or args == ["show"]:
        return "show", {}
    operation = args[0].lower()
    if operation == "clear" and len(args) == 1:
        return operation, {}
    if operation == "remove" and len(args) == 2:
        return operation, {"product_id": int(args[1])}
    if operation == "add" and len(args) in (2, 3):
        return operation, {"product_id": int(args[1]), "quantity": int(args[2]) if len(args) == 3 else 1}
    if operation == "set" and len(args) == 3:
        return operation, {"product_id": int(args[1]), "quantity": int(args[2])}
    raise ValueError("Invalid cart command")


async def _reply_cart(message, text):
    # A cart with 50 entries can exceed Telegram's single-message limit.
    while len(text) > 3900:
        boundary = text.rfind("\n", 0, 3900)
        if boundary <= 0:
            boundary = 3900
        await message.reply_text(text[:boundary])
        text = text[boundary:].lstrip("\n")
    if text:
        await message.reply_text(text)


async def cart_command(update, context):
    message = update.effective_message
    try:
        operation, arguments = _parse_command(context.args or [])
    except (ValueError, TypeError):
        await message.reply_text(get_text("cart_usage", context))
        return
    try:
        chat_id = update.effective_chat.id
        if operation == "show":
            snapshot = await asyncio.to_thread(cart_service.get_cart, chat_id)
        else:
            receipt = await asyncio.to_thread(
                cart_service.mutate_cart, chat_id, operation,
                request_id=f"command:{update.update_id}", **arguments,
            )
            snapshot = receipt["cart"]
    except cart_service.CartError as error:
        await message.reply_text(get_text(ERROR_KEYS.get(error.code, "cart_error"), context))
        return
    except Exception as error:
        logger.warning("Cart command failed (%s)", type(error).__name__)
        await message.reply_text(get_text("cart_unavailable", context))
        return
    await _reply_cart(message, render_cart(snapshot, context))


cart = CommandHandler("cart", cart_command)
