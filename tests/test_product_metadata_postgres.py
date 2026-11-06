"""Merchant search metadata and generated indexes on disposable PostgreSQL."""

import os
from pathlib import Path
import subprocess
import sys

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import inspect, select, text

from tests.test_postgres import database, empty_tables  # Guarded database fixtures.

pytestmark = pytest.mark.skipif(
    not os.environ.get('TEST_DATABASE_URL'),
    reason='Set TEST_DATABASE_URL to an expendable PostgreSQL database',
)


@pytest.fixture
def category_id(database):
    from telegram_ecommerce.database.models import Category, Session

    with Session.begin() as session:
        category = Category(name='Stationery', description='Test products')
        session.add(category)
        session.flush()
        return category.id


@pytest.fixture
def product_id(category_id):
    from telegram_ecommerce.database.manipulation import add_product
    from telegram_ecommerce.database.models import Product, Session

    add_product('Notebook', 'Ruled pages', '5.00', 10, 0, category_id, None)
    with Session() as session:
        return session.scalar(select(Product.id))


def product_metadata(product_id):
    from telegram_ecommerce.database.models import Product, Session

    with Session() as session:
        product = session.get(Product, product_id)
        return product.brand, product.short_description, product.tags, product.search_text


def test_schema_contains_optional_fields_and_generated_search_indexes(database):
    inspector = inspect(database)
    columns = {column['name']: column for column in inspector.get_columns('products')}
    for field, maximum in [('brand', 80), ('short_description', 200), ('tags', 500)]:
        assert columns[field]['nullable'] is True
        assert columns[field]['type'].length == maximum
    assert columns['search_text']['nullable'] is False
    assert columns['search_text']['computed']['persisted'] is True
    with database.connect() as connection:
        indexes = dict(connection.execute(text(
            "SELECT indexname, indexdef FROM pg_indexes WHERE tablename = 'products'"
        )).all())
    assert 'USING gin (search_text gin_trgm_ops)' in indexes['ix_products_search_text_trgm']
    assert "USING gin (to_tsvector('simple'::regconfig, search_text))" in indexes['ix_products_search_text_simple']
    assert "USING gin (to_tsvector('english'::regconfig, search_text))" in indexes['ix_products_search_text_english']
    assert 'ix_products_search' in indexes
    assert 'ix_products_search_english' in indexes


def test_existing_positional_add_product_api_is_unchanged(category_id):
    from telegram_ecommerce.database.manipulation import add_product
    from telegram_ecommerce.database.models import Product, Session

    assert add_product('Notebook', 'Ruled pages', '5.00', 10, 2, category_id, None) is None
    with Session() as session:
        product = session.scalar(select(Product))
        assert str(product.price) == '5.00'
        assert (product.quantity_in_stock, product.quantity_purchased) == (10, 2)
        assert (product.brand, product.short_description, product.tags) == (None, None, None)
        assert product.search_text.split() == ['notebook', 'ruled', 'pages']


def test_add_product_accepts_optional_keyword_metadata(category_id):
    from telegram_ecommerce.database.manipulation import add_product
    from telegram_ecommerce.database.models import Product, Session

    add_product('Notebook', 'Ruled pages', category_id=category_id,
                brand=' Acme ', short_description=' A5 Journal ', tags=' diary, writing ')
    with Session() as session:
        product = session.scalar(select(Product))
        assert (product.brand, product.short_description, product.tags) == ('Acme', 'A5 Journal', 'diary, writing')
        assert product.search_text == 'notebook acme diary, writing a5 journal ruled pages'


def test_metadata_arguments_are_keyword_only(category_id):
    from telegram_ecommerce.database.manipulation import add_product

    with pytest.raises(TypeError):
        add_product('Notebook', 'Ruled pages', 5, 10, 0, category_id, None, 'Acme')


def test_update_populates_null_metadata_and_regenerates_search_text(product_id, database):
    from telegram_ecommerce.database.manipulation import update_product_search_metadata

    assert update_product_search_metadata(
        product_id, brand='Acme', short_description='Portable A5', tags='journals, diary',
    ) is True
    brand, summary, tags, searchable = product_metadata(product_id)
    assert (brand, summary, tags) == ('Acme', 'Portable A5', 'journals, diary')
    assert searchable == 'notebook acme journals, diary portable a5 ruled pages'
    with database.connect() as connection:
        assert connection.scalar(text(
            "SELECT id FROM products WHERE to_tsvector('english', search_text) "
            "@@ plainto_tsquery('english', 'journal acme')"
        )) == product_id
        assert connection.scalar(text(
            "SELECT id FROM products WHERE search_text LIKE '%portable%'"
        )) == product_id


def test_omitted_fields_are_preserved_and_empty_fields_are_null(product_id):
    from telegram_ecommerce.database.manipulation import update_product_search_metadata

    update_product_search_metadata(product_id, brand='Acme', short_description='Portable', tags='diary')
    assert update_product_search_metadata(product_id, tags='  planner  ') is True
    assert product_metadata(product_id)[:3] == ('Acme', 'Portable', 'planner')
    update_product_search_metadata(product_id, brand='', short_description='  \t ', tags=None)
    brand, summary, tags, searchable = product_metadata(product_id)
    assert (brand, summary, tags) == (None, None, None)
    assert searchable.split() == ['notebook', 'ruled', 'pages']


def test_direct_name_and_description_updates_refresh_generated_text(product_id, database):
    with database.begin() as connection:
        connection.execute(text(
            "UPDATE products SET name = 'Planner', description = 'Weekly calendar' WHERE id = :id"
        ), {'id': product_id})
    assert product_metadata(product_id)[3].split() == ['planner', 'weekly', 'calendar']


@pytest.mark.parametrize('field,maximum', [('brand', 80), ('short_description', 200), ('tags', 500)])
def test_exact_maximum_length_is_accepted(product_id, field, maximum):
    from telegram_ecommerce.database.manipulation import update_product_search_metadata

    assert update_product_search_metadata(product_id, **{field: 'x' * maximum}) is True


@pytest.mark.parametrize('field,value', [
    ('brand', 'x' * 81), ('short_description', 'x' * 201), ('tags', 'x' * 501),
    ('brand', True), ('short_description', 123), ('tags', ['diary']),
    ('brand', 'a\x00b'), ('short_description', 'a\x00b'), ('tags', 'a\x00b'),
])
def test_invalid_metadata_does_not_write_any_fields(product_id, field, value):
    from telegram_ecommerce.database.manipulation import update_product_search_metadata

    update_product_search_metadata(product_id, brand='Original', short_description='Original', tags='original')
    before = product_metadata(product_id)
    values = {'brand': 'Changed', 'short_description': 'Changed', 'tags': 'changed', field: value}
    with pytest.raises(ValueError, match=field):
        update_product_search_metadata(product_id, **values)
    assert product_metadata(product_id) == before


def test_add_product_reuses_metadata_validation(category_id, database):
    from telegram_ecommerce.database.manipulation import add_product

    with pytest.raises(ValueError, match='brand'):
        add_product('Notebook', 'Ruled pages', category_id=category_id, brand='x' * 81)
    with database.connect() as connection:
        assert connection.scalar(text('SELECT count(*) FROM products')) == 0


@pytest.mark.parametrize('product_id', [None, True, '1', 0, -1, 2**31])
def test_invalid_product_id_is_rejected(database, product_id):
    from telegram_ecommerce.database.manipulation import update_product_search_metadata

    with pytest.raises(ValueError, match='product_id'):
        update_product_search_metadata(product_id, brand='Acme')


def test_missing_product_and_empty_patch_are_explicit(product_id):
    from telegram_ecommerce.database.manipulation import update_product_search_metadata

    assert update_product_search_metadata(product_id + 1, brand='Acme') is False
    with pytest.raises(ValueError, match='at least one'):
        update_product_search_metadata(product_id)


def test_metadata_is_stored_as_data(product_id, database):
    from telegram_ecommerce.database.manipulation import update_product_search_metadata

    value = "diary'); DROP TABLE products; --"
    update_product_search_metadata(product_id, tags=value)
    assert product_metadata(product_id)[2] == value
    with database.connect() as connection:
        assert connection.scalar(text('SELECT count(*) FROM products')) == 1


def test_migration_preserves_existing_products_and_backfills_generated_text(product_id, database):
    config = Config('alembic.ini')
    original_query = text(
        'SELECT id, name, description, price, quantity_in_stock, quantity_purchased, category_id, image_id '
        'FROM products ORDER BY id'
    )
    with database.connect() as connection:
        before = connection.execute(original_query).all()
    try:
        command.downgrade(config, '29f6c2a7d8b4')
        with database.connect() as connection:
            assert connection.execute(original_query).all() == before
            assert connection.scalar(text("SELECT EXISTS (SELECT FROM pg_extension WHERE extname = 'pg_trgm')"))
        assert {'brand', 'short_description', 'tags', 'search_text'}.isdisjoint(
            column['name'] for column in inspect(database).get_columns('products')
        )
        with database.begin() as connection:
            connection.execute(text("UPDATE products SET name = 'Legacy Planner' WHERE id = :id"), {'id': product_id})
    finally:
        command.upgrade(config, 'head')
    brand, summary, tags, searchable = product_metadata(product_id)
    assert (brand, summary, tags) == (None, None, None)
    assert searchable.split() == ['legacy', 'planner', 'ruled', 'pages']
    with database.connect() as connection:
        after = connection.execute(original_query).one()
    assert after[0] == before[0][0]
    assert after[2:] == before[0][2:]


def run_metadata_cli(*arguments):
    environment = os.environ.copy()
    environment.pop('BOT_TOKEN', None)
    environment.pop('OPENAI_API_KEY', None)
    environment['PYTHONPATH'] = str(Path(__file__).resolve().parents[1])
    return subprocess.run(
        [sys.executable, '-m', 'telegram_ecommerce.database.catalog_metadata', *map(str, arguments)],
        env=environment, text=True, capture_output=True, check=False,
    )


def test_cli_patches_and_clears_without_bot_or_openai_credentials(product_id):
    from telegram_ecommerce.database.manipulation import update_product_search_metadata

    update_product_search_metadata(product_id, brand='Acme', short_description='Portable', tags='diary')
    result = run_metadata_cli(product_id, '--tags', 'planner, journal')
    assert result.returncode == 0, result.stderr
    assert product_metadata(product_id)[:3] == ('Acme', 'Portable', 'planner, journal')
    result = run_metadata_cli(product_id, '--brand', '')
    assert result.returncode == 0, result.stderr
    assert product_metadata(product_id)[:3] == (None, 'Portable', 'planner, journal')


def test_cli_reports_missing_products_and_invalid_input(product_id):
    missing = run_metadata_cli(product_id + 1, '--brand', 'Acme')
    assert missing.returncode == 1
    assert 'not found' in missing.stderr
    invalid = run_metadata_cli(product_id, '--brand', 'x' * 81)
    assert invalid.returncode == 2
    assert 'at most 80' in invalid.stderr
    assert product_metadata(product_id)[:3] == (None, None, None)
