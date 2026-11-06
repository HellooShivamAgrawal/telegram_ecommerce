"""Persist chat carts and idempotent mutation receipts.

Revision ID: 14b8c5f7a2d1
Revises: 8e7b9b86f86c
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '14b8c5f7a2d1'
down_revision = '8e7b9b86f86c'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'carts',
        sa.Column('chat_id', sa.BigInteger(), autoincrement=False, nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint('chat_id <> 0', name='ck_carts_chat_id_nonzero'),
        sa.PrimaryKeyConstraint('chat_id'),
    )
    op.create_table(
        'cart_items',
        sa.Column('chat_id', sa.BigInteger(), nullable=False),
        sa.Column('product_id', sa.Integer(), nullable=False),
        sa.Column('quantity', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint('quantity BETWEEN 1 AND 99', name='ck_cart_items_quantity'),
        sa.ForeignKeyConstraint(['chat_id'], ['carts.chat_id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['product_id'], ['products.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('chat_id', 'product_id'),
    )
    op.create_index('ix_cart_items_product_id', 'cart_items', ['product_id'])
    op.create_table(
        'cart_operations',
        sa.Column('chat_id', sa.BigInteger(), nullable=False),
        sa.Column('request_id', sa.String(160), nullable=False),
        sa.Column('payload_fingerprint', sa.String(64), nullable=False),
        sa.Column('result', postgresql.JSONB(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(['chat_id'], ['carts.chat_id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('chat_id', 'request_id'),
    )


def downgrade():
    op.drop_table('cart_operations')
    op.drop_index('ix_cart_items_product_id', table_name='cart_items')
    op.drop_table('cart_items')
    op.drop_table('carts')
