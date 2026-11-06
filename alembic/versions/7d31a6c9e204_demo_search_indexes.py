"""Index owner catalogs for the shared ranked search engine."""
from alembic import op
import sqlalchemy as sa

revision = '7d31a6c9e204'
down_revision = '3bcb4f2a77e1'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('demo_products', sa.Column('search_text', sa.Text(), sa.Computed(
        "lower(name || ' ' || brand || ' ' || coalesce(category, '') "
        "|| ' ' || coalesce(description, ''))", persisted=True,
    ), nullable=False))
    op.create_index('ix_demo_products_search_trgm', 'demo_products', ['search_text'],
                    postgresql_using='gin', postgresql_ops={'search_text': 'gin_trgm_ops'})
    for dictionary in ('simple', 'english'):
        op.execute(f"CREATE INDEX ix_demo_products_search_{dictionary} ON demo_products "
                   f"USING gin (to_tsvector('{dictionary}', search_text))")


def downgrade():
    for suffix in ('english', 'simple', 'trgm'):
        op.drop_index(f'ix_demo_products_search_{suffix}', table_name='demo_products')
    op.drop_column('demo_products', 'search_text')
