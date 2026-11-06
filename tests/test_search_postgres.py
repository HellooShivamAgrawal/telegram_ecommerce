"""Catalog search behavior against an expendable PostgreSQL database."""

import os

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import inspect, text

from tests.test_postgres import database, empty_tables  # Guarded database fixtures.

pytestmark = pytest.mark.skipif(
    not os.environ.get('TEST_DATABASE_URL'),
    reason='Set TEST_DATABASE_URL to an expendable PostgreSQL database',
)


@pytest.fixture
def add_products(database):
    from telegram_ecommerce.database.models import Category, Product, Session

    def add(*rows):
        with Session.begin() as session:
            category = Category(name='Catalog', description='Search test products')
            session.add(category)
            session.flush()
            products = [
                Product(
                    name=row['name'], description=row.get('description', ''),
                    price='9.99', quantity_in_stock=row.get('stock', 10),
                    quantity_purchased=0, category_id=category.id,
                    brand=row.get('brand'), tags=row.get('tags'),
                    short_description=row.get('short_description'),
                )
                for row in rows
            ]
            session.add_all(products)
            session.flush()
            return [product.id for product in products]

    return add


def search_ids(phrase, **kwargs):
    from telegram_ecommerce.database.query import search_products

    return [product.id for product in search_products(phrase, **kwargs)]


@pytest.mark.parametrize('name,phrase', [
    ('Notebook', 'notebooks'),
    ('Notebooks', 'notebook'),
    ('Chair', 'chairs'),
    ('Chairs', 'chair'),
    ('Notebook', 'NOTEBOOKS'),
    ('Notebook', '  notebook!  '),
    ('Red Notebook', 'red, notebooks'),
    ('Red Notebook', 'red-notebooks'),
])
def test_english_inflections_case_and_punctuation(add_products, name, phrase):
    ids = add_products({'name': name})
    assert search_ids(phrase) == ids


@pytest.mark.parametrize('name,phrase', [
    ('Caderno azul', 'caderno'),
    ('Красная тетрадь', 'тетрадь'),
    ('ノート', 'ノート'),
    ('Café', 'café'),
    ('पढ़ाई के लिए नीली नोटबुक', 'पढ़ाई के लिए नीली नोटबुक'),
    ('สมุดบันทึกสีฟ้า', 'สมุดบันทึกสีฟ้า'),
])
def test_simple_search_preserves_multilingual_words(add_products, name, phrase):
    ids = add_products({'name': name})
    assert search_ids(phrase) == ids


def test_exact_name_then_name_match_then_description_match(add_products):
    description, name, exact = add_products(
        {'name': 'Planner', 'description': 'Notebook notebook notebook notebook'},
        {'name': 'Red Notebook'},
        {'name': 'Notebook'},
    )
    assert search_ids('notebook') == [exact, name, description]


def test_english_stemming_also_matches_descriptions(add_products):
    ids = add_products({'name': 'Planner', 'description': 'A notebook for daily lists'})
    assert search_ids('notebooks') == ids


def test_lexical_matches_rank_before_typo_matches(add_products):
    exact, related = add_products({'name': 'Notebok'}, {'name': 'Notebook'})
    assert search_ids('notebok') == [exact, related]


@pytest.mark.parametrize('phrase', ['notebok', 'noteboks', 'noteboook', 'notbook', 'notebokk'])
def test_name_typo_fallback(add_products, phrase):
    notebook, _ = add_products({'name': 'Notebook'}, {'name': 'Notepad'})
    assert search_ids(phrase) == [notebook]


def test_description_typos_are_retrievable(add_products):
    ids = add_products({'name': 'Planner', 'description': 'Notebook for daily lists'})
    assert search_ids('notebok') == ids


@pytest.mark.parametrize('phrase', ['red notebooks', 'red notebok', 'red-notebok'])
def test_all_search_words_are_required(add_products, phrase):
    blue, red = add_products({'name': 'Blue Notebook'}, {'name': 'Red Notebook'})
    assert search_ids(phrase) == [red]
    assert blue not in search_ids(phrase)


def test_exact_description_modifier_can_combine_with_name_typo(add_products):
    red, _ = add_products(
        {'name': 'Notebook', 'description': 'Red cover'},
        {'name': 'Notebook', 'description': 'Blue cover'},
    )
    assert search_ids('red notebok') == [red]


def test_multiple_typos_still_require_every_word(add_products):
    headphones, _, _ = add_products(
        {'name': 'Wireless Headphones'},
        {'name': 'Wireless Mouse'},
        {'name': 'Wired Headphones'},
    )
    assert search_ids('wireles headphons') == [headphones]


def test_missing_modifier_does_not_return_partial_match(add_products):
    add_products({'name': 'Blue Notebook'})
    assert search_ids('red notebooks') == []
    assert search_ids('red notebok') == []
    assert search_ids('wireless notebok') == []


def test_three_letter_prefixes_work_but_short_typos_stay_exact(add_products):
    pen, notebook = add_products({'name': 'Pen'}, {'name': 'Blue Notebook'})
    assert search_ids('pen') == [pen]
    assert search_ids('pan') == []
    assert search_ids('blu notebok') == [notebook]
    assert search_ids('blue notebok') == [notebook]


def test_short_model_numbers_still_match_lexically(add_products):
    a5, _ = add_products(
        {'name': 'Notebook', 'description': 'A5 paper'},
        {'name': 'Notebook', 'description': 'A4 paper'},
    )
    assert search_ids('A5') == [a5]
    assert search_ids('A5 notebok') == [a5]


def test_substrings_match_but_unrelated_words_do_not(add_products):
    notebook, _, _ = add_products({'name': 'Notebook'}, {'name': 'Pen'}, {'name': 'Camera'})
    assert search_ids('spaceship') == []
    assert search_ids('book') == [notebook]
    assert search_ids('note') == [notebook]


@pytest.mark.parametrize('phrase', ['notebooks', 'notebok'])
def test_search_excludes_sold_out_products(add_products, phrase):
    _, available = add_products({'name': 'Notebook', 'stock': 0}, {'name': 'Notebook'})
    assert search_ids(phrase) == [available]


@pytest.mark.parametrize('phrase', ['notebooks', 'notebok'])
def test_tied_results_have_stable_order_and_limit(add_products, phrase):
    ids = add_products(*({'name': 'Notebook'} for _ in range(4)))
    for _ in range(3):
        assert search_ids(phrase) == ids
        assert search_ids(phrase, limit=2) == ids[:2]


def test_limit_defaults_and_bounds(add_products):
    ids = add_products(*({'name': 'Notebook'} for _ in range(55)))
    assert search_ids('notebooks') == ids[:10]
    assert search_ids('notebooks', limit=0) == ids[:1]
    assert search_ids('notebooks', limit=-1) == ids[:1]
    assert search_ids('notebooks', limit=1000) == ids[:50]
    assert search_ids('notebok', limit=1000) == ids[:50]


@pytest.mark.parametrize('phrase', [
    '', '  ', '!!!', None, 123, 'notebook\x00', 'x' * 101,
    'one two three four five six seven eight nine',
])
def test_invalid_or_oversized_query_fails_closed(add_products, phrase):
    add_products({'name': 'Notebook', 'description': 'one two three four five six seven eight nine'})
    assert search_ids(phrase) == []


def test_eight_word_query_is_supported(add_products):
    ids = add_products({'name': 'Notebook', 'description': 'one two three four five six seven eight'})
    assert search_ids('one two three four five six seven eight') == ids


@pytest.mark.parametrize('phrase', ["' OR 1=1 --", "notebook'; DROP TABLE products; --", 'notebook | camera'])
def test_query_text_cannot_change_sql_or_full_text_operators(add_products, database, phrase):
    ids = add_products({'name': 'Notebook'}, {'name': 'Camera'})
    assert search_ids(phrase) == []
    assert search_ids('notebook') == ids[:1]
    with database.connect() as connection:
        assert connection.scalar(text('SELECT count(*) FROM products')) == 2


def test_search_migration_can_be_reversed_without_dropping_extension(add_products, database):
    ids = add_products({'name': 'Notebook'})
    config = Config('alembic.ini')
    try:
        command.downgrade(config, '14b8c5f7a2d1')
        assert 'ix_products_search_english' not in {
            index['name'] for index in inspect(database).get_indexes('products')
        }
        with database.connect() as connection:
            assert connection.scalar(text("SELECT EXISTS (SELECT FROM pg_extension WHERE extname = 'pg_trgm')"))
            assert connection.scalar(text('SELECT count(*) FROM products')) == 1
    finally:
        command.upgrade(config, 'head')
    assert search_ids('notebooks') == ids
