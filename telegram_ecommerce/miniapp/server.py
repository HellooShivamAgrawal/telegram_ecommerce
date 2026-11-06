"""Serve the existing PostgreSQL catalog and Mini App from one origin."""
import json
import logging
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from sqlalchemy import select, func, text
from telegram_ecommerce.database.models import DemoProduct, Session
from telegram_ecommerce.database.search import search_demo_catalog, catalog_price
from telegram_ecommerce.miniapp.images import thumbnail
from telegram_ecommerce.database.product_facts import product_facts

STATIC = Path(__file__).with_name('static')
BRAND = os.environ.get('DEMO_BRAND', '')
NAMES = {'dailyobjects': 'DailyObjects', 'nicobar': 'Nicobar', 'blue_tokai': 'Blue Tokai',
         'ellementry': 'ellementry', 'jaipur_rugs': 'Jaipur Rugs'}
PAGE_SIZE = 6


def product_json(product):
    return dict(id=product.id, name=product.name, category=product.category or 'Other',
                price=float(product.price) if product.price is not None else None,
                mrp=float(product.mrp) if product.mrp is not None else None,
                available=product.available, description=product.description or '', facts=product_facts(product),
                image=f'/api/image/{product.id}?v=2' if product.demo_image else None)


def catalog(query='', category='', page=0, max_price=None):
    if len(query) > 100 or len(category) > 160 or not 0 <= page <= 10000:
        raise ValueError('Search is too long or page is invalid.')
    max_price = catalog_price(max_price)
    with Session() as session:
        session.execute(text("SET LOCAL statement_timeout = '5000'"))
        where = [DemoProduct.brand == BRAND]
        if max_price is not None:
            where.append(DemoProduct.price <= max_price)
        if category:
            where.append(DemoProduct.category == category)
        if query:
            result = search_demo_catalog(query, BRAND, category=category, max_price=max_price)
            hits = result.hits
            total = len(hits)
            products = [dict(product_json(hit.product), related=hit.match_kind == 'related')
                        for hit in hits[page * PAGE_SIZE:(page + 1) * PAGE_SIZE]]
        else:
            total = session.scalar(select(func.count()).select_from(DemoProduct).where(*where))
            rows = session.scalars(select(DemoProduct).where(*where).order_by(DemoProduct.name, DemoProduct.id)
                                   .offset(page * PAGE_SIZE).limit(PAGE_SIZE))
            products = [product_json(row) for row in rows]
        categories = session.execute(select(DemoProduct.category, func.count()).where(DemoProduct.brand == BRAND)
                                     .group_by(DemoProduct.category).order_by(DemoProduct.category)).all()
    return dict(brand=NAMES[BRAND], products=products, total=total, page=page,
                search_limited=bool(query and total == 50), has_more=(page + 1) * PAGE_SIZE < total,
                bot_username=os.environ.get('BOT_USERNAME', ''),
                categories=[dict(name=name or 'Other', count=count) for name, count in categories])


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def send(self, status, body, kind='application/json; charset=utf-8', cache='no-store'):
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(status)
        self.send_header('Content-Type', kind)
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', cache)
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('Content-Security-Policy', "default-src 'none'; script-src 'self' https://telegram.org; style-src 'self'; img-src 'self'; connect-src 'self'; base-uri 'none'; form-action 'none'")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        try:
            url = urlsplit(self.path)
            if url.path == '/api/catalog':
                values = parse_qs(url.query, max_num_fields=8)
                self.send(200, catalog(values.get('q', [''])[0].strip(), values.get('category', [''])[0],
                                       int(values.get('page', ['0'])[0]), values.get('max_price', [''])[0]))
            elif url.path.startswith('/api/image/'):
                product_id = int(url.path.rsplit('/', 1)[-1])
                with Session() as session:
                    paths = session.execute(select(DemoProduct.original_image, DemoProduct.demo_image).where(DemoProduct.id == product_id,
                                                                                DemoProduct.brand == BRAND)).first()
                if not paths or not any(paths):
                    raise FileNotFoundError()
                self.send(200, thumbnail(paths[0] or paths[1], mark=bool(paths[0])), 'image/jpeg', 'public, max-age=3600')
            elif url.path in {'/', '/app.js', '/style.css'}:
                name = 'index.html' if url.path == '/' else url.path[1:]
                kind = {'index.html': 'text/html; charset=utf-8', 'app.js': 'text/javascript', 'style.css': 'text/css'}[name]
                self.send(200, (STATIC / name).read_bytes(), kind)
            else:
                self.send(404, {'error': 'Not found'})
        except (ValueError, TypeError):
            self.send(400, {'error': 'Invalid search or product.'})
        except FileNotFoundError:
            self.send(404, {'error': 'Photo unavailable'})
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception:
            logging.exception('Catalog request failed')
            self.send(503, {'error': 'The catalog is temporarily unavailable. Please retry.'})


def main():
    if BRAND not in NAMES:
        raise SystemExit('Configure DEMO_BRAND for the catalog Mini App.')
    ThreadingHTTPServer(('0.0.0.0', 8080), Handler).serve_forever()


if __name__ == '__main__':
    main()
