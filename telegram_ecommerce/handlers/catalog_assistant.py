"""Shopping assistant with chat-scoped context and real activity updates."""

import time

from telegram.ext import MessageHandler, filters

from telegram_ecommerce.agent.catalog import CartUpdatedResponseError, answer_catalog_question
from telegram_ecommerce.agent.progress import ActivityMessage
from telegram_ecommerce.language import get_text
from telegram_ecommerce.utils.log import logger


HISTORY_KEY = "shopping_history"
HISTORY_TIME_KEY = "shopping_history_updated_at"
HISTORY_TTL = 30 * 60


def _history(chat_data, now):
    if now - chat_data.get(HISTORY_TIME_KEY, 0) > HISTORY_TTL:
        chat_data.pop(HISTORY_KEY, None)
    history = chat_data.get(HISTORY_KEY, [])
    return [dict(item) for item in history[-6:]]


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
    except CartUpdatedResponseError:
        answer = get_text("assistant_cart_saved", context)
    except Exception as error:
        logger.warning("Shopping assistant failed (%s)", type(error).__name__)
        answer = get_text("catalog_assistant_unavailable", context)
    else:
        context.chat_data[HISTORY_KEY] = (history + [
            {"role": "user", "content": message.text},
            {"role": "assistant", "content": answer},
        ])[-6:]
        context.chat_data[HISTORY_TIME_KEY] = now
    await activity.finish(answer)


catalog_assistant = MessageHandler(filters.TEXT & ~filters.COMMAND, answer_message)
