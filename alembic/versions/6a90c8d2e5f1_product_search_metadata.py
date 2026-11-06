"""Add optional merchant search fields and indexed generated search text.

Revision ID: 6a90c8d2e5f1
Revises: 29f6c2a7d8b4
"""

from alembic import op
import sqlalchemy as sa

revision = '6a90c8d2e5f1'
down_revision = '29f6c2a7d8b4'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('products', sa.Column('brand', sa.String(80), nullable=True))
    op.add_column('products', sa.Column('short_description', sa.String(200), nullable=True))
    op.add_column('products', sa.Column('tags', sa.String(500), nullable=True))
    op.add_column('products', sa.Column(
        'search_text', sa.Text(), sa.Computed(
            "lower(name || ' ' || coalesce(brand, '') || ' ' || coalesce(tags, '') "
            "|| ' ' || coalesce(short_description, '') || ' ' || description)",
            persisted=True,
        ), nullable=False,
    ))
    # pg_trgm is installed by the preceding catalog-search migration.
    op.create_index(
        'ix_products_search_text_trgm', 'products', ['search_text'],
        postgresql_using='gin', postgresql_ops={'search_text': 'gin_trgm_ops'},
    )
    op.execute("""CREATE INDEX ix_products_search_text_simple ON products
        USING gin (to_tsvector('simple', search_text))""")
    op.execute("""CREATE INDEX ix_products_search_text_english ON products
        USING gin (to_tsvector('english', search_text))""")


def downgrade():
    op.drop_index('ix_products_search_text_english', table_name='products')
    op.drop_index('ix_products_search_text_simple', table_name='products')
    op.drop_index('ix_products_search_text_trgm', table_name='products')
    op.drop_column('products', 'search_text')
    op.drop_column('products', 'tags')
    op.drop_column('products', 'short_description')
    op.drop_column('products', 'brand')
