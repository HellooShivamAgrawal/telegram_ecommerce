"""Store isolated, source-attributed owner demo catalogs.

Revision ID: 3bcb4f2a77e1
Revises: 6a90c8d2e5f1
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '3bcb4f2a77e1'
down_revision = '6a90c8d2e5f1'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'demo_products',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('brand', sa.String(40), nullable=False),
        sa.Column('source_product_id', sa.String(160), nullable=False),
        sa.Column('source_url', sa.Text(), nullable=False),
        sa.Column('name', sa.Text(), nullable=False),
        sa.Column('description', sa.Text()),
        sa.Column('category', sa.String(160)),
        sa.Column('price', sa.Numeric(12, 2)),
        sa.Column('mrp', sa.Numeric(12, 2)),
        sa.Column('available', sa.Boolean()),
        sa.Column('demo_image', sa.Text()),
        sa.Column('original_image', sa.Text()),
        sa.Column('details', postgresql.JSONB(), nullable=False),
        sa.CheckConstraint('price IS NULL OR price >= 0', name='ck_demo_products_price_nonnegative'),
    )
    op.create_index('ix_demo_products_brand', 'demo_products', ['brand'])
    op.create_index('ix_demo_products_category', 'demo_products', ['category'])
    op.create_index('ix_demo_products_brand_category', 'demo_products', ['brand', 'category'])
    op.create_index('uq_demo_products_brand_source', 'demo_products', ['brand', 'source_product_id'], unique=True)


def downgrade():
    op.drop_table('demo_products')
