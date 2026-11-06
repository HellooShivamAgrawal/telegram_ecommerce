"""Upsert local demo snapshots into the isolated PostgreSQL catalog."""

import argparse
import json
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy.dialects.postgresql import insert


ROOT = Path(__file__).resolve().parents[2] / 'data' / 'demo_catalogs'
BRANDS = ('dailyobjects', 'blue_tokai', 'nicobar', 'ellementry', 'jaipur_rugs')


def display_category(record):
    category = (record.get('category') or '').strip()
    if category and category.lower() not in {'other', 'shop-bundle', 'drop'}:
        return category[:160]
    tags = record.get('tags') or []
    if record['brand'] == 'ellementry':
        groups = ('Furniture & Lighting', 'Kitchenware', 'Dinnerware', 'Serveware',
                  'Coffee & Tea', 'Table Accessories', 'Home Decor', 'Cushions',
                  'Candles', 'Bath', 'Table Linen', 'Gifting')
        for group in groups:
            if any(tag.casefold() == group.casefold() for tag in tags):
                return group
    if record['brand'] == 'nicobar':
        for tag in tags:
            if tag.startswith('Filterclass_'):
                return tag.split('_', 1)[1][:160]
    if record['brand'] == 'blue_tokai':
        return 'Coffee' if 'coffee' in record['name'].lower() or 'estate' in record['name'].lower() else 'Other'
    return category or 'Other'


def import_brand(brand, session):
    from telegram_ecommerce.database.models import DemoProduct

    path = ROOT / f'{brand}.jsonl'
    count = 0
    for line in path.open(encoding='utf-8'):
        record = json.loads(line)
        values = {
            'brand': brand,
            'source_product_id': str(record['source_product_id'])[:160],
            'source_url': record['source_url'],
            'name': record['name'],
            'description': record.get('description'),
            'category': display_category(record),
            'price': record.get('price'),
            'mrp': record.get('mrp'),
            'available': record.get('available'),
            'demo_image': record.get('demo_image'),
            'original_image': record.get('original_image'),
            'details': record,
        }
        statement = insert(DemoProduct).values(**values)
        statement = statement.on_conflict_do_update(
            index_elements=['brand', 'source_product_id'],
            set_={key: statement.excluded[key] for key in values if key not in {'brand', 'source_product_id'}},
        )
        session.execute(statement)
        count += 1
        if count % 100 == 0:
            session.commit()
    session.commit()
    return count


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--brands', nargs='+', choices=BRANDS, default=list(BRANDS))
    args = parser.parse_args()
    load_dotenv()
    from telegram_ecommerce.database.models import Session
    with Session() as session:
        for brand in args.brands:
            print(f'{brand}: {import_brand(brand, session)} products', flush=True)


if __name__ == '__main__':
    main()
