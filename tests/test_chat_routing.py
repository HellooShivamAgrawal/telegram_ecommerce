"""Exercise Telegram's actual handler selection without Telegram network calls."""

import importlib
import json
import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

os.environ.setdefault("BOT_TOKEN", "123:test")
for key in ("DB_HOST", "DB_USER", "DB_PASSWORD", "DB_NAME"):
    os.environ.setdefault(key, "test")

from telegram import Update
from telegram.ext import ApplicationBuilder
from telegram.request import BaseRequest

from telegram_ecommerce.handlers import all_handlers
from telegram_ecommerce.agent.catalog import ProductSelectionRequired

search_handler = importlib.import_module("telegram_ecommerce.handlers.search")
cart_handler = importlib.import_module("telegram_ecommerce.handlers.cart")
assistant_handler = importlib.import_module("telegram_ecommerce.handlers.catalog_assistant")


class TelegramStub(BaseRequest):
    def __init__(self):
        self.messages = []
        self.records = []

    async def initialize(self):
        pass

    async def shutdown(self):
        pass

    async def do_request(self, url, method, request_data=None, **kwargs):
        endpoint = url.rsplit("/", 1)[-1]
        bot = {"id": 123, "is_bot": True, "first_name": "Test", "username": "TestBot"}
        if endpoint == "getMe":
            result = bot
        elif endpoint in {"sendMessage", "editMessageText"}:
            params = request_data.parameters
            self.messages.append(params["text"])
            result = {
                "message_id": params.get('message_id', len(self.messages)), "date": 1, "from": bot,
                "chat": {"id": int(params["chat_id"]), "type": "private"}, "text": params["text"],
            }
            if params.get('reply_markup'):
                result['reply_markup'] = params['reply_markup']
            self.records.append(result)
        elif endpoint == 'answerCallbackQuery':
            result = True
        else:
            raise AssertionError(f"Unexpected Telegram method: {endpoint}")
        return 200, json.dumps({"ok": True, "result": result}).encode()


def inbound(bot, text, update_id, chat_id):
    message = {
        "message_id": update_id, "date": 1,
        "from": {"id": chat_id, "is_bot": False, "first_name": "Shopper"},
        "chat": {"id": chat_id, "type": "private"}, "text": text,
    }
    if text.startswith("/"):
        message["entities"] = [{"type": "bot_command", "offset": 0, "length": len(text.split()[0])}]
    return Update.de_json({"update_id": update_id, "message": message}, bot)


class RoutingTests(unittest.IsolatedAsyncioTestCase):
    async def test_weak_assistant_result_becomes_clickable_product_details(self):
        transport = TelegramStub()
        app = ApplicationBuilder().token('123:test').request(transport).build()
        for handler in all_handlers:
            app.add_handler(handler)
        product = SimpleNamespace(id=7, name='Notebook', price='9.99', image_id=None,
                                  description='A5 dotted paper', quantity_purchased=0)
        with patch.object(assistant_handler, 'answer_catalog_question', new_callable=AsyncMock,
                          side_effect=ProductSelectionRequired([{'id': 7, 'name': 'Notebook', 'price': '9.99'}])), patch.object(
            search_handler, 'get_available_product', return_value=product,
        ) as refresh, patch('telegram_ecommerce.tamplates.products.count_occurrence_of_specified_rating', return_value=0):
            async with app:
                await app.process_update(inbound(app.bot, 'book', 201, 7003))
                choice = transport.records[-1]
                self.assertEqual(choice['message_id'], transport.records[0]['message_id'])
                self.assertEqual(choice['reply_markup']['inline_keyboard'][0][0]['callback_data'], 'search_choice:7')
                callback = Update.de_json({
                    'update_id': 202, 'callback_query': {
                        'id': 'choice', 'from': {'id': 7003, 'is_bot': False, 'first_name': 'Shopper'},
                        'chat_instance': 'shop', 'message': choice, 'data': 'search_choice:7',
                    },
                }, app.bot)
                await app.process_update(callback)
                selection = app.chat_data[7003][search_handler.PRODUCT_SELECTION_KEY]
                self.assertEqual(selection['id'], 7)
                self.assertIn('A5 dotted paper', transport.messages[-1])
        refresh.assert_called_once_with(7)

    async def test_natural_cart_request_after_browsing_reaches_assistant(self):
        transport = TelegramStub()
        app = ApplicationBuilder().token("123:test").request(transport).build()
        for handler in all_handlers:
            app.add_handler(handler)
        product = SimpleNamespace(id=7, name="Notebook", price="9.99", image_id=None)
        with patch.object(search_handler, "search_catalog", return_value=SimpleNamespace(
            hits=[SimpleNamespace(product=product)], suggestions_only=False,
        )), patch.object(
            assistant_handler, "answer_catalog_question",
            new_callable=AsyncMock, return_value="Added two notebooks.",
        ) as assistant:
            async with app:
                await app.process_update(inbound(app.bot, "/search", 101, 7001))
                await app.process_update(inbound(app.bot, "notebook", 102, 7001))
                await app.process_update(inbound(app.bot, "Add two notebooks to my cart", 103, 7001))
        assistant.assert_awaited_once()
        self.assertEqual(assistant.await_args.kwargs["chat_id"], 7001)
        self.assertIn("Added two notebooks.", transport.messages)

    async def test_cart_command_is_available_while_search_waits_for_input(self):
        transport = TelegramStub()
        app = ApplicationBuilder().token("123:test").request(transport).build()
        for handler in all_handlers:
            app.add_handler(handler)
        empty = {"items": [], "total": "0.00", "currency": "USD", "item_count": 0}
        with patch.object(cart_handler.cart_service, "get_cart", return_value=empty) as read:
            async with app:
                await app.process_update(inbound(app.bot, "/search", 104, 7002))
                await app.process_update(inbound(app.bot, "/cart", 105, 7002))
        read.assert_called_once_with(7002)
        self.assertTrue(any("cart is empty" in text for text in transport.messages))
