"""Edit merchant search metadata without changing the product chat wizard."""

import argparse

from dotenv import load_dotenv
from sqlalchemy.exc import SQLAlchemyError


def main(argv=None):
    parser = argparse.ArgumentParser(
        description='Update product search metadata. Omitted fields stay unchanged; empty text clears a field.',
    )
    parser.add_argument('product_id', type=int, help='Existing product ID')
    parser.add_argument('--brand', default=argparse.SUPPRESS, help='Brand, at most 80 characters')
    parser.add_argument('--short-description', default=argparse.SUPPRESS, help='Summary, at most 200 characters')
    parser.add_argument('--tags', default=argparse.SUPPRESS, help='Comma-separated keywords or aliases, at most 500 characters')
    arguments = vars(parser.parse_args(argv))
    product_id = arguments.pop('product_id')
    if not arguments:
        parser.error('Supply at least one of --brand, --short-description, or --tags')

    load_dotenv()
    try:
        from telegram_ecommerce.database.manipulation import update_product_search_metadata
    except (KeyError, ValueError, SQLAlchemyError):
        parser.exit(1, 'Database configuration is invalid. Check DATABASE_URL or DB_* settings.\n')

    try:
        updated = update_product_search_metadata(product_id, **arguments)
    except ValueError as error:
        parser.error(str(error))
    except SQLAlchemyError:
        parser.exit(1, 'Database update failed. Check connection settings and apply migrations.\n')
    if not updated:
        parser.exit(1, f'Product {product_id} was not found.\n')
    print(f'Updated search metadata for product {product_id}.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
