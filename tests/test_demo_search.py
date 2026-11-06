"""Shared ranking and merchant isolation for snapshot catalogs."""
import os
import pytest
from sqlalchemy import text
from tests.test_postgres import database

pytestmark = pytest.mark.skipif(not os.environ.get('TEST_DATABASE_URL'), reason='Needs expendable PostgreSQL')

@pytest.fixture
def catalog(database):
    from telegram_ecommerce.database.models import DemoProduct, Session
    with database.begin() as connection:
        connection.execute(text('TRUNCATE demo_products RESTART IDENTITY'))
    with Session.begin() as session:
        rows = [
            DemoProduct(brand=brand, source_product_id=str(i), source_url='https://example.com',
                        name=name, category=category, description=description, details={})
            for i, (brand, name, category, description) in enumerate([
                ('dailyobjects', 'Notebook', 'Stationery', None),
                ('dailyobjects', 'Book', 'Reading', 'Paper'),
                ('dailyobjects', 'Red A5 Notebook', 'Stationery', 'Travel journal'),
                ('dailyobjects', 'Blue A5 Notebook', 'Stationery', ''),
                ('nicobar', 'Book', 'Reading', 'Paper'),
            ])]
        session.add_all(rows)
        session.flush()
        return [row.id for row in rows]


def test_demo_ranking_and_isolation(catalog):
    from telegram_ecommerce.database.search import search_demo_catalog
    result = search_demo_catalog('book', 'dailyobjects')
    assert result.hits[0].product.id == catalog[1]
    assert catalog[0] in [hit.product.id for hit in result.hits]
    assert catalog[4] not in [hit.product.id for hit in result.hits]
    assert not result.suggestions_only


def test_demo_typos_modifiers_and_category(catalog):
    from telegram_ecommerce.database.search import search_demo_catalog
    result = search_demo_catalog('red A5 notebok', 'dailyobjects')
    assert [hit.product.id for hit in result.hits] == [catalog[2]]
    assert result.suggestions_only
    assert not search_demo_catalog('red A6 notebok', 'dailyobjects').hits
    assert len(search_demo_catalog('stationery', 'dailyobjects').hits) == 3
    assert len(search_demo_catalog('notebooks', 'dailyobjects').hits) == 3


def test_demo_index_refreshes_after_edit(catalog):
    from telegram_ecommerce.database.models import DemoProduct, Session
    from telegram_ecommerce.database.search import search_demo_catalog
    with Session.begin() as session:
        session.get(DemoProduct, catalog[0]).name = 'Umbrella'
    assert [hit.product.id for hit in search_demo_catalog('umbrellas', 'dailyobjects').hits] == [catalog[0]]


def test_preview_welcome_and_weak_match_choices(catalog, monkeypatch):
    import asyncio
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from telegram_ecommerce.handlers import demo
    monkeypatch.setattr(demo, 'ACTIVE_BRAND', 'dailyobjects')
    message = SimpleNamespace(text='notebok', message_id=7,
                              reply_text=AsyncMock(return_value=SimpleNamespace(message_id=8)))
    update = SimpleNamespace(effective_message=message, effective_chat=SimpleNamespace(id=1))
    context = SimpleNamespace(user_data={}, args=[])
    asyncio.run(demo.demo_command(update, context))
    welcome = message.reply_text.call_args.args[0]
    assert '/demo' not in welcome and 'demo products' not in welcome
    asyncio.run(demo._show_search(update, context, 1, 'notebok'))
    assert 'closest matches' in message.reply_text.call_args.args[0]
    buttons = message.reply_text.call_args.kwargs['reply_markup'].inline_keyboard
    assert 1 <= len(buttons) <= 5
    update.callback_query = SimpleNamespace(data=buttons[0][0].callback_data, answer=AsyncMock())
    # A choice copied from another message must not select a product.
    asyncio.run(demo.demo_callback(update, context))
    assert update.callback_query.answer.call_args.kwargs['show_alert']


def test_preview_tools_cannot_read_another_store_or_mutate(catalog):
    from telegram_ecommerce.agent.catalog import execute_preview_tool
    assert execute_preview_tool('dailyobjects', 'get_product', {'product_id': catalog[4]}) == {'product': None}
    with pytest.raises(ValueError):
        execute_preview_tool('dailyobjects', 'clear_cart', {})
