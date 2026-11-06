"""Exercise real Telegram dispatch and multipart serialization without sending messages."""
import asyncio
import json
from types import SimpleNamespace
from unittest.mock import patch

import httpx
from telegram import Update
from telegram.ext import ApplicationBuilder
from telegram.request import BaseRequest

from telegram_ecommerce.handlers import demo
from tests.test_chat_routing import inbound


class NavigationTransport(BaseRequest):
    def __init__(self, fail_photo=False):
        self.records = []
        self.uploads = []
        self.fail_photo = fail_photo
        self.events = []

    async def initialize(self): pass
    async def shutdown(self): pass

    async def do_request(self, url, method, request_data=None, **kwargs):
        endpoint = url.rsplit('/', 1)[-1]
        self.events.append(endpoint)
        bot = {'id': 123, 'is_bot': True, 'first_name': 'Test', 'username': 'TestBot'}
        if endpoint == 'getMe':
            result = bot
        elif endpoint == 'answerCallbackQuery':
            result = True
        elif endpoint == 'editMessageReplyMarkup':
            result = True
        elif endpoint in {'sendPhoto', 'sendMessage'}:
            params = request_data.parameters
            if endpoint == 'sendPhoto':
                # Read the real multipart body: the original PosixPath bug fails here.
                wire = httpx.Request('POST', 'https://example.invalid',
                                     data=request_data.json_parameters,
                                     files=request_data.multipart_data).read()
                assert b'fixture image bytes' in wire
                self.uploads.append(wire)
                if self.fail_photo:
                    return 400, json.dumps({'ok': False, 'error_code': 400, 'description': 'Bad Request: photo rejected'}).encode()
            result = {'message_id': len(self.records) + 1, 'date': 1, 'from': bot,
                      'chat': {'id': int(params['chat_id']), 'type': 'private'}}
            result.update({key: params[key] for key in ('text', 'caption', 'reply_markup') if key in params})
            self.records.append(result)
        else:
            raise AssertionError(endpoint)
        return 200, json.dumps({'ok': True, 'result': result}).encode()


def click(bot, record, data, update_id):
    return Update.de_json({'update_id': update_id, 'callback_query': {
        'id': str(update_id), 'from': {'id': 8001, 'is_bot': False, 'first_name': 'Shopper'},
        'chat_instance': 'test', 'message': record, 'data': data,
    }}, bot)


def test_category_product_next_previous_details_and_back(tmp_path):
    asyncio.run(navigation(tmp_path))


def test_photo_failure_keeps_product_and_navigation_accessible(tmp_path):
    asyncio.run(navigation(tmp_path, fail_photo=True))


async def navigation(tmp_path, fail_photo=False):
    (tmp_path / 'photo.jpg').write_bytes(b'fixture image bytes')
    products = {i: SimpleNamespace(id=i, name=f'Bag {i}', details={}, price=100, mrp=None,
                                   available=True, description=f'Description {i}', category='Bags',
                                   demo_image='photo.jpg') for i in (1, 2)}
    transport = NavigationTransport(fail_photo)
    app = ApplicationBuilder().token('123:test').request(transport).build()
    for handler in demo.demo_handlers:
        app.add_handler(handler)
    failures = []
    async def error(update, context): failures.append(context.error)
    app.add_error_handler(error)
    with patch.object(demo, 'ACTIVE_BRAND', 'dailyobjects'), patch.object(demo, 'ROOT', tmp_path), \
         patch.object(demo, '_categories', return_value=[('Bags', 2)]), \
         patch.object(demo, '_products', return_value=[1, 2]) as get_products, \
         patch.object(demo, '_product', side_effect=lambda product_id, brand: products[product_id]):
        async with app:
            await app.process_update(inbound(app.bot, '/start', 1, 8001))
            categories = transport.records[-1]
            await app.process_update(click(app.bot, categories, 'demo:1:cat:0', 2))
            assert not failures
            first = transport.records[-1]
            assert 'Bag 1' in first.get('caption', first.get('text', ''))
            assert first['reply_markup']['inline_keyboard'][0][1]['text'] == '1/2'
            get_products.assert_called_with('dailyobjects', category='Bags')
            await app.process_update(click(app.bot, first, 'demo:1:next', 3))
            second = transport.records[-1]
            assert 'Bag 2' in second.get('caption', second.get('text', ''))
            assert second['reply_markup']['inline_keyboard'][0][1]['text'] == '2/2'
            await app.process_update(click(app.bot, second, 'demo:1:prev', 4))
            first_again = transport.records[-1]
            assert 'Bag 1' in first_again.get('caption', first_again.get('text', ''))
            await app.process_update(click(app.bot, first_again, 'demo:1:detail', 5))
            detailed = transport.records[-1]
            assert detailed['reply_markup']['inline_keyboard'][1][0]['text'] == 'Summary'
            await app.process_update(click(app.bot, detailed, 'demo:1:back', 6))
            assert 'Bags' in transport.records[-1]['text']
            assert transport.records[-1]['reply_markup']['inline_keyboard'][0][0]['callback_data'] == 'demo:1:cat:0'
    assert not failures
    assert len(transport.uploads) == 4
    # Retirement follows delivery rather than deleting the controls before upload.
    assert transport.events.index('sendPhoto') < transport.events.index('editMessageReplyMarkup')
    if fail_photo:
        assert transport.events.count('sendMessage') == 6
