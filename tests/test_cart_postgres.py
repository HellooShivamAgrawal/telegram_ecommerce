"""Cart behavior against an expendable PostgreSQL database."""

from concurrent.futures import ThreadPoolExecutor
import os
from threading import Barrier

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

from tests.test_postgres import database, empty_tables  # Shared guarded database fixtures.

pytestmark = pytest.mark.skipif(
    not os.environ.get('TEST_DATABASE_URL'),
    reason='Set TEST_DATABASE_URL to an expendable PostgreSQL database',
)


@pytest.fixture
def products(database):
    from telegram_ecommerce.database.models import Category, Product, Session

    with Session.begin() as session:
        category = Category(name='Stationery', description='Test products')
        session.add(category)
        session.flush()
        notebook = Product(name='Notebook', description='A5 notebook', price='9.99',
                           quantity_in_stock=100, quantity_purchased=0, category_id=category.id)
        pen = Product(name='Pen', description='Blue ink', price='2.50',
                      quantity_in_stock=10, quantity_purchased=0, category_id=category.id)
        session.add_all([notebook, pen])
        session.flush()
        return notebook.id, pen.id


def test_cart_crud_and_decimal_totals(products):
    from telegram_ecommerce.database.cart import get_cart, mutate_cart
    from telegram_ecommerce.database.models import Cart, Product, Session

    notebook, pen = products
    assert get_cart(123)['items'] == []
    with Session() as session:
        assert session.get(Cart, 123) is None

    result = mutate_cart(123, 'add', product_id=notebook, quantity=2, request_id='add-notebook')
    assert result['cart']['total'] == '19.98'
    assert result['cart']['item_count'] == 2
    assert 'chat_id' not in result['cart']
    assert result['replayed'] is False
    mutate_cart(123, 'add', product_id=notebook, quantity=1, request_id='one-more')
    result = mutate_cart(123, 'add', product_id=pen, quantity=2, request_id='add-pens')
    assert result['cart']['total'] == '34.97'
    result = mutate_cart(123, 'set', product_id=notebook, quantity=1, request_id='set-notebook')
    assert result['cart']['total'] == '14.99'
    mutate_cart(123, 'remove', product_id=pen, request_id='remove-pens')
    assert get_cart(123)['total'] == '9.99'
    mutate_cart(123, 'set', product_id=notebook, quantity=0, request_id='set-zero')
    assert get_cart(123)['items'] == []
    mutate_cart(123, 'add', product_id=pen, quantity=1, request_id='one-pen')
    result = mutate_cart(123, 'clear', request_id='clear-cart')
    assert result['cart'] == {'items': [], 'total': '0.00', 'currency': 'USD', 'item_count': 0}
    with Session() as session:
        assert session.get(Cart, 123) is not None
        assert session.get(Product, notebook).quantity_in_stock == 100
        assert session.get(Product, pen).quantity_in_stock == 10


def test_private_and_group_chats_are_isolated(products):
    from telegram_ecommerce.database.cart import get_cart, mutate_cart

    notebook, pen = products
    group_id = -1001234567890
    mutate_cart(group_id, 'add', product_id=notebook, quantity=3, request_id='same-message')
    mutate_cart(123, 'add', product_id=pen, quantity=1, request_id='same-message')
    assert get_cart(group_id)['items'][0]['product_id'] == notebook
    assert get_cart(123)['items'][0]['product_id'] == pen
    mutate_cart(group_id, 'clear', request_id='clear-group')
    assert get_cart(group_id)['items'] == []
    assert get_cart(123)['item_count'] == 1


def test_replayed_add_never_reapplies_even_after_clear(products):
    from telegram_ecommerce.database.cart import get_cart, mutate_cart

    notebook, _ = products
    first = mutate_cart(123, 'add', product_id=notebook, quantity=2, request_id='retry-me')
    repeated = mutate_cart(123, 'add', product_id=notebook, quantity=2, request_id='retry-me')
    assert repeated['cart'] == first['cart']
    assert repeated['replayed'] is True
    assert get_cart(123)['item_count'] == 2
    mutate_cart(123, 'clear', request_id='clear')
    mutate_cart(123, 'add', product_id=notebook, quantity=2, request_id='retry-me')
    assert get_cart(123)['items'] == []


def test_request_key_cannot_be_reused_for_different_payload(products):
    from telegram_ecommerce.database.cart import CartError, get_cart, mutate_cart

    notebook, _ = products
    mutate_cart(123, 'add', product_id=notebook, quantity=1, request_id='same')
    with pytest.raises(CartError) as error:
        mutate_cart(123, 'add', product_id=notebook, quantity=2, request_id='same')
    assert error.value.code == 'request_conflict'
    assert get_cart(123)['item_count'] == 1


@pytest.mark.parametrize('same_request', [False, True])
def test_concurrent_adds_are_serialized_and_deduplicated(products, same_request):
    from telegram_ecommerce.database.cart import get_cart, mutate_cart

    notebook, _ = products
    barrier = Barrier(2)

    def add(index):
        barrier.wait(timeout=10)
        request_id = 'concurrent' if same_request else f'concurrent-{index}'
        return mutate_cart(123, 'add', product_id=notebook, quantity=1, request_id=request_id)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(add, range(2)))
    assert get_cart(123)['item_count'] == (1 if same_request else 2)
    assert sum(result['replayed'] for result in results) == (1 if same_request else 0)


def test_stock_rejection_rolls_back_and_get_cart_reports_changes(products):
    from telegram_ecommerce.database.cart import CartError, get_cart, mutate_cart
    from telegram_ecommerce.database.models import Product, Session

    _, pen = products
    mutate_cart(123, 'add', product_id=pen, quantity=8, request_id='add')
    with pytest.raises(CartError) as error:
        mutate_cart(123, 'add', product_id=pen, quantity=3, request_id='too-many')
    assert error.value.code == 'insufficient_stock'
    assert get_cart(123)['item_count'] == 8
    with Session.begin() as session:
        product = session.get(Product, pen)
        product.quantity_in_stock = 2
        product.price = '3.00'
    cart = get_cart(123)
    assert cart['total'] == '24.00'
    assert cart['items'][0]['stock_sufficient'] is False
    assert cart['items'][0]['available_quantity'] == 2
    mutate_cart(123, 'remove', product_id=pen, request_id='remove-unavailable')
    assert get_cart(123)['items'] == []


def test_line_quantity_limit_is_enforced_on_increment(products):
    from telegram_ecommerce.database.cart import CartError, get_cart, mutate_cart

    notebook, _ = products
    mutate_cart(123, 'add', product_id=notebook, quantity=99, request_id='max')
    with pytest.raises(CartError) as error:
        mutate_cart(123, 'add', product_id=notebook, quantity=1, request_id='over-max')
    assert error.value.code == 'quantity_limit'
    assert get_cart(123)['item_count'] == 99


def test_cart_line_limit(products):
    from telegram_ecommerce.database.cart import CartError, get_cart, mutate_cart
    from telegram_ecommerce.database.models import CartItem, Product, Session

    notebook, pen = products
    mutate_cart(123, 'add', product_id=notebook, quantity=1, request_id='first')
    with Session.begin() as session:
        category_id = session.get(Product, notebook).category_id
        for index in range(49):
            product = Product(name=f'Item {index}', description='Test item', price='1.00',
                              quantity_in_stock=10, quantity_purchased=0, category_id=category_id)
            session.add(product)
            session.flush()
            session.add(CartItem(chat_id=123, product_id=product.id, quantity=1))
    with pytest.raises(CartError) as error:
        mutate_cart(123, 'add', product_id=pen, quantity=1, request_id='fifty-first')
    assert error.value.code == 'cart_full'
    assert len(get_cart(123)['items']) == 50


@pytest.mark.parametrize('chat_id', [0, True, '123', 2**63, -(2**63)-1])
def test_invalid_chat_ids_are_rejected(chat_id):
    from telegram_ecommerce.database.cart import CartError, get_cart

    with pytest.raises(CartError) as error:
        get_cart(chat_id)
    assert error.value.code == 'invalid_chat'


@pytest.mark.parametrize('quantity', [True, 1.5, '2', -1, 0, 100])
def test_invalid_add_quantities_are_rejected(products, quantity):
    from telegram_ecommerce.database.cart import CartError, mutate_cart

    with pytest.raises(CartError) as error:
        mutate_cart(123, 'add', product_id=products[0], quantity=quantity, request_id='invalid')
    assert error.value.code == 'invalid_quantity'


@pytest.mark.parametrize('product_id', [0, True, '123', -1, 2**31])
def test_invalid_product_ids_are_rejected(product_id):
    from telegram_ecommerce.database.cart import CartError, mutate_cart

    with pytest.raises(CartError) as error:
        mutate_cart(123, 'remove', product_id=product_id, request_id='invalid')
    assert error.value.code == 'invalid_product'


def test_missing_product_and_invalid_requests(products):
    from telegram_ecommerce.database.cart import CartError, mutate_cart

    for request_id in ('', ' ', 'a' * 161, None):
        with pytest.raises(CartError) as error:
            mutate_cart(123, 'clear', request_id=request_id)
        assert error.value.code == 'invalid_request'
    with pytest.raises(CartError) as error:
        mutate_cart(123, 'add', product_id=9999, quantity=1, request_id='missing')
    assert error.value.code == 'product_not_found'
    with pytest.raises(CartError) as error:
        mutate_cart(123, 'clear', product_id=products[0], request_id='invalid-clear')
    assert error.value.code == 'invalid_arguments'


@pytest.mark.parametrize('quantity', [0, -1, 100])
def test_database_rejects_invalid_cart_quantity(database, products, quantity):
    with database.begin() as connection:
        connection.execute(text('INSERT INTO carts (chat_id) VALUES (123)'))
    with pytest.raises(IntegrityError):
        with database.begin() as connection:
            connection.execute(text('INSERT INTO cart_items (chat_id, product_id, quantity) VALUES (123, :product, :quantity)'),
                               {'product': products[0], 'quantity': quantity})
