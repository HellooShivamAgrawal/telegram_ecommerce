"""Add English catalog stemming and optional name typo search.

Revision ID: 29f6c2a7d8b4
Revises: 14b8c5f7a2d1
"""

from alembic import op

revision = '29f6c2a7d8b4'
down_revision = '14b8c5f7a2d1'
branch_labels = None
depends_on = None


def upgrade():
    # pg_trgm must be available on the server; install it during deployment,
    # never while serving a customer query.
    op.execute('CREATE EXTENSION IF NOT EXISTS pg_trgm')
    op.execute("""CREATE INDEX ix_products_search_english ON products
        USING gin (to_tsvector('english', name || ' ' || description))""")


def downgrade():
    op.drop_index('ix_products_search_english', table_name='products')
    # Extensions are database-wide and may be used by other applications.
