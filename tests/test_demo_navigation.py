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
    def __init__(self, fail_photo=False, fail_edit=False):
        self.records = []
        self.uploads = []
        self.fail_photo = fail_photo
        self.events = []
        self.fail_edit = fail_edit
        self.messages = {}
        self.next_id = 1

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
        elif endpoint in {'sendPhoto', 'sendMessage', 'editMessageMedia', 'editMessageCaption',
                          'editMessageText', 'editMessageReplyMarkup'}:
            params = request_data.parameters
            editing = endpoint.startswith('edit')
            if editing and self.fail_edit and endpoint != 'editMessageReplyMarkup':
                return 400, json.dumps({'ok': False, 'error_code': 400, 'description': 'Bad Request: edit rejected'}).encode()
            if endpoint in {'sendPhoto', 'editMessageMedia'}:
                wire = httpx.Request('POST', 'https://example.invalid',
                                     data=request_data.json_parameters,
                                     files=request_data.multipart_data).read()
                assert b'fixture image bytes' in wire or b'PNG' in wire
                self.uploads.append(wire)
                if self.fail_photo and endpoint == 'sendPhoto':
                    return 400, json.dumps({'ok': False, 'error_code': 400, 'description': 'Bad Request: photo rejected'}).encode()
            message_id = int(params['message_id']) if editing else self.next_id
            if editing:
                result = dict(self.messages[message_id])
            else:
                self.next_id += 1
                result = {'message_id': message_id, 'date': 1, 'from': bot,
                          'chat': {'id': int(params['chat_id']), 'type': 'private'}}
            if endpoint in {'sendPhoto', 'editMessageMedia'}:
                result.pop('text', None)
                result['photo'] = [{'file_id': 'image', 'file_unique_id': 'image', 'width': 600, 'height': 360}]
            if endpoint == 'editMessageMedia':
                result['caption'] = params['media']['caption']
            result.update({key: params[key] for key in ('text', 'caption', 'reply_markup') if key in params})
            if endpoint == 'editMessageReplyMarkup' and 'reply_markup' not in params:
                result.pop('reply_markup', None)
            self.messages[message_id] = dict(result)
            if endpoint != 'editMessageReplyMarkup':
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


async def navigation(tmp_path, fail_photo=False, missing_image=False):
    (tmp_path / 'photo.jpg').write_bytes(b'fixture image bytes')
    products = {i: SimpleNamespace(id=i, name=f'Bag {i}', details={}, price=100, mrp=None,
                                   available=True, description=f'Description {i}', category='Bags',
                                   demo_image='photo.jpg') for i in (1, 2)}
    if missing_image:
        products[2].demo_image = None
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
            assert second['message_id'] == first['message_id']
            if missing_image:
                assert 'Photo unavailable' in second['caption']
                assert b'PNG' in transport.uploads[-1]
            await app.process_update(click(app.bot, second, 'demo:1:prev', 4))
            first_again = transport.records[-1]
            assert 'Bag 1' in first_again.get('caption', first_again.get('text', ''))
            await app.process_update(click(app.bot, first_again, 'demo:1:detail', 5))
            detailed = transport.records[-1]
            assert detailed['reply_markup']['inline_keyboard'][1][0]['text'] == 'Summary'
            assert detailed['message_id'] == first['message_id']
            assert transport.events.count('sendPhoto') == 1
            assert transport.events.count('editMessageMedia') == (0 if fail_photo else 2)
            assert transport.events.count('editMessageCaption') == (0 if fail_photo else 1)
            if fail_photo:
                assert transport.events.count('editMessageText') == 3
            await app.process_update(click(app.bot, detailed, 'demo:1:back', 6))
            assert 'Bags' in transport.records[-1]['text']
            assert transport.records[-1]['reply_markup']['inline_keyboard'][0][0]['callback_data'] == 'demo:1:cat:0'
    assert not failures
    assert len(transport.uploads) == (1 if fail_photo else 3)
    # Retirement follows delivery rather than deleting the controls before upload.
    assert transport.events.index('sendPhoto') < transport.events.index('editMessageReplyMarkup')
    if fail_photo:
        assert transport.events.count('sendMessage') == 3


def test_nine_item_picker_preserves_card_and_full_result_set(tmp_path):
    asyncio.run(picker_flow(tmp_path))


async def picker_flow(tmp_path):
    (tmp_path / 'photo.jpg').write_bytes(b'fixture image bytes')
    products = {i: SimpleNamespace(id=i, name=f'Bag {i}', details={}, price=100, mrp=None,
                                   available=True, description='Description', category='Bags',
                                   demo_image='photo.jpg') for i in range(1, 21)}
    transport = NavigationTransport()
    app = ApplicationBuilder().token('123:test').request(transport).build()
    for handler in demo.demo_handlers: app.add_handler(handler)
    failures = []
    async def error(update, context): failures.append(context.error)
    app.add_error_handler(error)
    with patch.object(demo, 'ACTIVE_BRAND', 'dailyobjects'), patch.object(demo, 'ROOT', tmp_path), \
         patch.object(demo, '_categories', return_value=[('Bags', 20)]), \
         patch.object(demo, '_products', return_value=list(products)), \
         patch.object(demo, '_product', side_effect=lambda product_id, brand: products[product_id]), \
         patch.object(demo, '_picker_products', side_effect=lambda brand, ids: [products[i] for i in ids]):
        async with app:
            await app.process_update(inbound(app.bot, '/start', 1, 8001))
            await app.process_update(click(app.bot, transport.records[-1], 'demo:1:cat:0', 2))
            card_id = transport.records[-1]['message_id']
            for page, count in [(0, 9), (1, 9), (2, 2)]:
                await app.process_update(click(app.bot, transport.messages[card_id], f'demo:1:picker:{page}', 3 + page))
                buttons = transport.messages[card_id]['reply_markup']['inline_keyboard']
                assert sum(row[0]['callback_data'].startswith('demo:1:pick:') for row in buttons) == count
            await app.process_update(click(app.bot, transport.messages[card_id], 'demo:1:pick:20', 7))
            card = transport.messages[card_id]
            assert 'Bag 20' in card['caption']
            assert card['reply_markup']['inline_keyboard'][0][1]['text'] == '20/20'
            assert len(transport.messages) == 2  # Categories + one reusable product card.
            # A rejected edit must leave both display and browsing index on item 20.
            transport.fail_edit = True
            await app.process_update(click(app.bot, card, 'demo:1:prev', 8))
            assert 'Bag 20' in transport.messages[card_id]['caption']
            state = app.user_data[8001][demo.STATE_KEY][8001]
            assert state['index'] == 19
            # Old cards cannot operate on the new result set.
            old = dict(card, message_id=999)
            edits = transport.events.count('editMessageMedia')
            await app.process_update(click(app.bot, old, 'demo:1:next', 9))
            assert transport.events.count('editMessageMedia') == edits
            # New weak search results must invalidate the previous active card.
            transport.fail_edit = False
            result = SimpleNamespace(hits=[SimpleNamespace(product=products[i]) for i in (1, 2)], suggestions_only=True)
            with patch.object(demo, 'search_demo_catalog', return_value=result):
                await app.process_update(inbound(app.bot, '/search bags', 10, 8001))
            choices = transport.records[-1]
            await app.process_update(click(app.bot, card, 'demo:1:next', 11))
            assert transport.events.count('editMessageMedia') == edits
            await app.process_update(click(app.bot, choices, 'demo:1:choose:2', 12))
            chosen = transport.records[-1]
            assert chosen['reply_markup']['inline_keyboard'][0][1]['text'] == '2/2'
            await app.process_update(click(app.bot, chosen, 'demo:1:prev', 13))
            assert 'Bag 1' in transport.records[-1]['caption']
            assert transport.records[-1]['message_id'] == chosen['message_id']
    assert not failures


def test_missing_next_photo_does_not_reuse_previous_product_image(tmp_path):
    asyncio.run(navigation(tmp_path, missing_image=True))
