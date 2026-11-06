"""Shopping assistant with chat-scoped context and real activity updates."""

import time
from types import SimpleNamespace

from telegram.ext import MessageHandler, filters

from telegram_ecommerce.agent.catalog import (
    CartActionRejectedError, CartUpdatedResponseError, ProductSelectionRequired,
    answer_catalog_question,
)
from telegram_ecommerce.agent.progress import ActivityMessage
from telegram_ecommerce.language import get_text
from telegram_ecommerce.utils.log import logger
from telegram_ecommerce.handlers.search import PRODUCT_SELECTION_KEY, show_search_choices


HISTORY_KEY = "shopping_history"
HISTORY_TIME_KEY = "shopping_history_updated_at"
HISTORY_TTL = 30 * 60


def _history(chat_data, now):
    if now - chat_data.get(HISTORY_TIME_KEY, 0) > HISTORY_TTL:
        chat_data.pop(HISTORY_KEY, None)
    history = chat_data.get(HISTORY_KEY, [])
    history = [dict(item) for item in history[-6:]]
    selection = chat_data.get(PRODUCT_SELECTION_KEY)
    if selection and now - selection['selected_at'] > HISTORY_TTL:
        chat_data.pop(PRODUCT_SELECTION_KEY, None)
    elif selection and selection['selected_at'] > chat_data.get(HISTORY_TIME_KEY, 0):
        history.append({'role': 'assistant', 'content':
                        f"Product opened by the shopper: {selection['name']} (ID {selection['id']})."})
    return history[-6:]


async def answer_message(update, context):
    message = update.effective_message
    if len(message.text) > 1000:
        await message.reply_text(get_text("assistant_message_too_long", context))
        return
    now = time.time()
    history = _history(context.chat_data, now)
    activity = await ActivityMessage(message, context).start()
    try:
        answer = await answer_catalog_question(
            message.text,
            chat_id=update.effective_chat.id,
            request_id=f"telegram:{update.update_id}",
            on_progress=activity.update,
            history=history,
        )
    except ProductSelectionRequired as selection:
        await show_search_choices(
            update, context, [SimpleNamespace(**product) for product in selection.products],
            activity=activity,
            notice=get_text('assistant_cart_partial', context) if selection.saved else None,
        )
        return
    except CartUpdatedResponseError:
        answer = get_text("assistant_cart_saved", context)
    except CartActionRejectedError as error:
        if error.saved:
            key = "assistant_cart_partial"
        else:
            key = {
                "insufficient_stock": "cart_insufficient_stock",
                "product_not_found": "cart_product_not_found",
                "quantity_limit": "cart_invalid_quantity",
                "cart_full": "cart_full",
            }.get(error.code, "assistant_cart_rejected")
        answer = get_text(key, context)
    except Exception as error:
        logger.warning("Shopping assistant failed (%s)", type(error).__name__)
        answer = get_text("catalog_assistant_unavailable", context)
    else:
        context.chat_data[HISTORY_KEY] = (history + [
            {"role": "user", "content": message.text},
            {"role": "assistant", "content": answer},
        ])[-6:]
        context.chat_data[HISTORY_TIME_KEY] = now
    if await activity.finish(answer) is False:
        logger.warning("Shopping reply delivery failed for update %s; no operations retried", update.update_id)


catalog_assistant = MessageHandler(filters.TEXT & ~filters.COMMAND, answer_message)
