"""Shopper-facing relevance, uncertainty and freshness checks on PostgreSQL."""

import os

import pytest

from tests.test_postgres import database, empty_tables
from tests.test_search_postgres import add_products, search_ids

pytestmark = pytest.mark.skipif(not os.environ.get('TEST_DATABASE_URL'),
                              reason='Needs expendable PostgreSQL')


def search(query, **kwargs):
    from telegram_ecommerce.database.query import search_catalog
    return search_catalog(query, **kwargs)


def test_actual_book_precedes_notebook_and_bookcase(add_products):
    notebook, bookcase, book, _ = add_products(
        {'name': 'Notebook'}, {'name': 'Bookcase'}, {'name': 'Book'}, {'name': 'Pen'},
    )
    result = search('book')
    assert [hit.product.id for hit in result.hits] == [book, notebook, bookcase]
    assert [hit.match_kind for hit in result.hits] == ['exact', 'related', 'related']
    assert not result.suggestions_only


@pytest.mark.parametrize('query', ['book', 'books', 'notebok', 'note', 'not'])
def test_partial_or_typo_only_results_are_explicit_suggestions(add_products, query):
    notebook, = add_products({'name': 'Notebook'})
    result = search(query)
    assert [hit.product.id for hit in result.hits] == [notebook]
    assert result.suggestions_only
    assert result.hits[0].match_kind == 'related'
    assert result.hits[0].matched_fields == ('name',)


def test_field_importance_and_repeated_description_cannot_swamp_name(add_products):
    description, short, tags, brand, name = add_products(
        {'name': 'Item One', 'description': 'notebook ' * 40},
        {'name': 'Item Two', 'short_description': 'Notebook for school'},
        {'name': 'Item Three', 'tags': 'notebook, journal'},
        {'name': 'Item Four', 'brand': 'Notebook'},
        {'name': 'Red Notebook'},
    )
    result = search('notebooks')
    assert [hit.product.id for hit in result.hits] == [name, brand, tags, short, description]
    assert [hit.matched_fields for hit in result.hits] == [
        ('name',), ('brand',), ('tags',), ('short_description',), ('description',),
    ]
    assert all(hit.match_kind == 'exact' for hit in result.hits)


def test_brand_modifier_and_description_dimensions_are_required(add_products):
    wanted, _, _, _, mentioned = add_products(
        {'name': 'Red Notebook', 'brand': 'Acme', 'short_description': 'A5 dotted paper'},
        {'name': 'Blue Notebook', 'brand': 'Acme', 'short_description': 'A5 dotted paper'},
        {'name': 'Red Notebook', 'brand': 'Acme', 'short_description': 'A4 dotted paper'},
        {'name': 'Red Notebook', 'brand': 'Other', 'short_description': 'A5 dotted paper'},
        {'name': 'Red Notebook', 'description': 'Acme A5 dotted paper'},
    )
    assert search_ids('acme red A5 notebok') == [wanted, mentioned]
    assert search_ids('acme red A6 notebok') == []


def test_tags_are_searchable_aliases_without_prompt_rewriting(add_products):
    wanted, = add_products({'name': 'Notebook', 'tags': 'exercise book, journal, stationery'})
    result = search('exercise books')
    assert [hit.product.id for hit in result.hits] == [wanted]
    assert result.hits[0].match_kind == 'exact'
    assert 'tags' in result.hits[0].matched_fields


def test_metadata_typos_match_the_correct_field(add_products):
    wanted, = add_products({'name': 'Notebook', 'brand': 'Paperworks', 'tags': 'stationery'})
    result = search('paperwoks stationry')
    assert [hit.product.id for hit in result.hits] == [wanted]
    assert result.suggestions_only
    assert result.hits[0].matched_fields == ('brand', 'tags')


def test_short_codes_never_fuzzily_drift(add_products):
    a5, = add_products({'name': 'Notebook', 'tags': 'A5 XL 4K'})
    assert search_ids('A5') == [a5]
    for query in ('A6', 'XS', '8K', 'A5 X'):
        assert search_ids(query) == []


def test_sold_out_exact_does_not_hide_available_related_products(add_products):
    _, notebook = add_products({'name': 'Book', 'stock': 0}, {'name': 'Notebook'})
    result = search('book')
    assert [hit.product.id for hit in result.hits] == [notebook]
    assert result.suggestions_only


def test_nullable_metadata_old_products_still_search_and_scores_are_stable(add_products):
    ids = add_products(*({'name': 'Notebook'} for _ in range(7)))
    first = search('book', limit=5)
    second = search('book', limit=5)
    assert [hit.product.id for hit in first.hits] == ids[:5]
    assert [hit.score for hit in first.hits] == [hit.score for hit in second.hits]


def test_no_result_is_not_a_confident_suggestion(add_products):
    add_products({'name': 'Notebook'})
    result = search('spaceship')
    assert not result.hits
    assert not result.suggestions_only


def test_connection_threshold_is_unchanged_after_search(add_products, database):
    from sqlalchemy import text
    add_products({'name': 'Notebook'})
    with database.connect() as connection:
        before = connection.scalar(text('SHOW pg_trgm.word_similarity_threshold'))
    search('notebok')
    with database.connect() as connection:
        assert connection.scalar(text('SHOW pg_trgm.word_similarity_threshold')) == before
