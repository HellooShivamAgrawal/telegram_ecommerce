"""Transactional carts scoped to a Telegram chat, without reserving inventory.

Callers supply chat IDs from trusted Telegram update metadata. A mutation's
request_id must be stable across retries of the same user action. Receipts are
kept with the cart so a delayed retry cannot apply an increment a second time.
"""

from decimal import Decimal
from hashlib import sha256
import json

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert

from telegram_ecommerce.database.models import Cart, CartItem, CartOperation, Product, Session
from telegram_ecommerce.utils.consts import currency


MAX_QUANTITY = 99
MAX_CART_LINES = 50


class CartError(ValueError):
    """An expected cart validation failure, safe to present to the caller."""

    def __init__(self, code, message):
        self.code = code
        super().__init__(message)


def _validate_chat_id(chat_id):
    if type(chat_id) is not int or chat_id == 0 or not -(2**63) <= chat_id < 2**63:
        raise CartError('invalid_chat', 'A valid Telegram chat is required.')


def _validate_mutation(operation, product_id, quantity, request_id):
    if not isinstance(request_id, str) or not request_id.strip() or len(request_id) > 160:
        raise CartError('invalid_request', 'A valid request identifier is required.')
    if operation not in ('add', 'set', 'remove', 'clear'):
        raise CartError('invalid_operation', 'That cart action is not supported.')
    if operation == 'clear':
        if product_id is not None or quantity is not None:
            raise CartError('invalid_arguments', 'Clearing a cart does not take an item or quantity.')
        return
    if type(product_id) is not int or not 1 <= product_id < 2**31:
        raise CartError('invalid_product', 'A valid product ID is required.')
    if operation == 'remove':
        if quantity is not None:
            raise CartError('invalid_arguments', 'Removing an item does not take a quantity.')
        return
    minimum = 0 if operation == 'set' else 1
    if type(quantity) is not int or not minimum <= quantity <= MAX_QUANTITY:
        raise CartError('invalid_quantity', f'Quantity must be an integer between {minimum} and {MAX_QUANTITY}.')


def _snapshot(session, chat_id):
    rows = session.execute(
        select(CartItem.quantity, Product.id, Product.name, Product.price, Product.quantity_in_stock)
        .join(Product, Product.id == CartItem.product_id)
        .where(CartItem.chat_id == chat_id)
        .order_by(Product.id)
    ).all()
    items = []
    total = Decimal('0.00')
    for quantity, product_id, name, price, stock in rows:
        line_total = price * quantity
        total += line_total
        items.append({
            'product_id': product_id,
            'name': name,
            'quantity': quantity,
            'unit_price': format(price, '.2f'),
            'line_total': format(line_total, '.2f'),
            'available_quantity': stock,
            'stock_sufficient': quantity <= stock,
        })
    return {
        'items': items,
        'total': format(total, '.2f'),
        'currency': currency,
        'item_count': sum(item['quantity'] for item in items),
    }


def get_cart(chat_id):
    """Read a cart using current prices and stock; item_count is total units."""
    _validate_chat_id(chat_id)
    with Session() as session:
        return _snapshot(session, chat_id)


def mutate_cart(chat_id, operation, *, product_id=None, quantity=None, request_id):
    """Atomically apply one action or replay its previously committed receipt.

    ``add`` increments quantity; ``set`` assigns an absolute quantity (zero
    removes it); ``remove`` removes one line; ``clear`` removes all lines.
    Successful writes preserve a receipt even when the cart becomes empty.
    """
    _validate_chat_id(chat_id)
    _validate_mutation(operation, product_id, quantity, request_id)
    payload = json.dumps(
        {'operation': operation, 'product_id': product_id, 'quantity': quantity},
        sort_keys=True, separators=(',', ':'),
    )
    fingerprint = sha256(payload.encode('utf-8')).hexdigest()
    with Session.begin() as session:
        session.execute(insert(Cart).values(chat_id=chat_id).on_conflict_do_nothing())
        cart = session.scalar(select(Cart).where(Cart.chat_id == chat_id).with_for_update())
        receipt = session.get(CartOperation, (chat_id, request_id))
        if receipt is not None:
            if receipt.payload_fingerprint != fingerprint:
                raise CartError('request_conflict', 'This request identifier was already used for a different action.')
            return {**receipt.result, 'replayed': True}

        if operation == 'clear':
            session.execute(delete(CartItem).where(CartItem.chat_id == chat_id))
        elif operation == 'remove' or (operation == 'set' and quantity == 0):
            session.execute(delete(CartItem).where(
                CartItem.chat_id == chat_id, CartItem.product_id == product_id,
            ))
        else:
            _put_item(session, chat_id, operation, product_id, quantity)
        cart.updated_at = func.now()
        session.flush()
        result = {'cart': _snapshot(session, chat_id), 'operation': operation, 'replayed': False}
        session.add(CartOperation(
            chat_id=chat_id, request_id=request_id, payload_fingerprint=fingerprint, result=result,
        ))
        return result


def _put_item(session, chat_id, operation, product_id, quantity):
    # This short shared lock keeps stock/price stable during validation only.
    # It does not reserve inventory after the transaction commits.
    product = session.scalar(select(Product).where(Product.id == product_id).with_for_update(read=True))
    if product is None:
        raise CartError('product_not_found', 'That product no longer exists.')
    item = session.get(CartItem, (chat_id, product_id))
    new_quantity = quantity + (item.quantity if item and operation == 'add' else 0)
    if new_quantity > MAX_QUANTITY:
        raise CartError('quantity_limit', f'A cart line can contain at most {MAX_QUANTITY} units.')
    if new_quantity > product.quantity_in_stock:
        raise CartError('insufficient_stock', f'Only {product.quantity_in_stock} units are currently available.')
    if item is None:
        line_count = session.scalar(select(func.count()).select_from(CartItem).where(CartItem.chat_id == chat_id))
        if line_count >= MAX_CART_LINES:
            raise CartError('cart_full', f'A cart can contain at most {MAX_CART_LINES} different products.')
        session.add(CartItem(chat_id=chat_id, product_id=product_id, quantity=new_quantity))
    else:
        item.quantity = new_quantity
        item.updated_at = func.now()
