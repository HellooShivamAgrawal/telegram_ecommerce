"""Compare catalog search on synthetic products in an expendable test database.

Requires TEST_DATABASE_URL and --reset; never loads the application's .env.
The named database must end in _test. This command deletes its existing data.
Optional --baseline points to the previous query.py for an identical workload.
"""

import argparse
import importlib.util
import json
import os
from pathlib import Path
import statistics
import time

from sqlalchemy import text
from sqlalchemy.engine import make_url


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--reset', action='store_true', required=True)
    parser.add_argument('--sizes', type=int, nargs='+', default=[100, 1000, 10000])
    parser.add_argument('--iterations', type=int, default=25)
    parser.add_argument('--baseline', type=Path)
    args = parser.parse_args()
    url = os.environ.get('TEST_DATABASE_URL')
    if not url or not (make_url(url).database or '').endswith('_test'):
        parser.error('TEST_DATABASE_URL must explicitly name a database ending in _test')
    if not 5 <= args.iterations <= 100 or any(not 1 <= size <= 100000 for size in args.sizes):
        parser.error('Use 5–100 iterations and catalog sizes of 1–100000')
    os.environ['DATABASE_URL'] = url
    os.environ.setdefault('BOT_TOKEN', 'benchmark-token')
    from alembic import command
    from alembic.config import Config
    from telegram_ecommerce.database.models import engine
    from telegram_ecommerce.database.search import search_catalog
    command.upgrade(Config('alembic.ini'), 'head')
    searches = {'weighted': search_catalog}
    if args.baseline:
        spec = importlib.util.spec_from_file_location('baseline_query', args.baseline)
        baseline = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(baseline)
        searches['previous'] = baseline.search_products

    for size in args.sizes:
        with engine.begin() as connection:
            connection.execute(text('TRUNCATE cart_operations, cart_items, carts, orders, products, '
                                    'category, photo, customers RESTART IDENTITY CASCADE'))
            category_id = connection.scalar(text(
                "INSERT INTO category (name, description) VALUES ('Benchmark', 'Synthetic') RETURNING id"))
            rows = []
            for index in range(size):
                product = ('Notebook', 'Pen', 'Backpack', 'Lamp', 'Headphones')[index % 5]
                color = ('Red', 'Blue', 'Green')[(index // 5) % 3]
                rows.append({
                    'name': f'{color} {product} {index}',
                    'description': f'{color} {product.lower()} for daily use. Practical, durable and easy to carry.',
                    'brand': ('Acme', 'Paperworks', 'Everday', 'Northstar')[(index // 15) % 4],
                    'short_description': f'A5 dotted paper, {color.lower()} cover' if product == 'Notebook' else 'Everyday essentials',
                    'tags': 'stationery, journal, writing' if product == 'Notebook' else product.lower(),
                    'category_id': category_id,
                })
            connection.execute(text('''INSERT INTO products
                (name, description, brand, short_description, tags, price,
                 quantity_in_stock, quantity_purchased, category_id)
                VALUES (:name, :description, :brand, :short_description, :tags, 9.99, 10, 0, :category_id)'''), rows)
            connection.execute(text('ANALYZE products'))
        for query in ('notebooks', 'book', 'notebok', 'acme red A5 notebook', 'paperwoks', 'journal', 'zzzxqv'):
            for version, search in searches.items():
                search(query)
                durations = []
                for _ in range(args.iterations):
                    start = time.perf_counter()
                    result = search(query)
                    durations.append((time.perf_counter() - start) * 1000)
                ordered = sorted(durations)
                print(json.dumps({
                    'size': size, 'query': query, 'version': version,
                    'p50_ms': round(statistics.median(durations), 2),
                    'p95_ms': round(ordered[min(len(ordered) - 1, int(0.95 * len(ordered)))], 2),
                    'matches': len(result.hits if hasattr(result, 'hits') else result),
                }), flush=True)


if __name__ == '__main__':
    main()
