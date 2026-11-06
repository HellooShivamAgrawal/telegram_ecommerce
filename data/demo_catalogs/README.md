# Owner demo catalogs

Five source-attributed catalog snapshots can feed a read-only Telegram bot.
Set `DEMO_BRAND` for **one brand per bot instance**. Product counts are not
fixed limits. The bot's normal merchant products, cart, checkout, and payments
use separate tables and are unavailable while demo mode is active.

| `DEMO_BRAND` | Brand | Detailed products | Coverage |
| --- | --- | ---: | --- |
| `dailyobjects` | DailyObjects | 706 | Non-phone-case product sitemap plus category listings; bags, chargers, straps, accessories, desks, and more |
| `blue_tokai` | Blue Tokai | 191 | Published public storefront catalog |
| `nicobar` | Nicobar | 2,412 | Women's, men's, home, jewellery, and accessories collections |
| `ellementry` | ellementry | 1,212 | Published public storefront catalog, including currently unavailable items |
| `jaipur_rugs` | Jaipur Rugs | 524 | Rugs, wall art, home textiles, and furniture |

Jaipur Rugs' product sitemap contains 11,603 URLs, retained in
`jaipur_rugs_sitemap_urls.json`. Its detailed snapshot is a cross-category
selection, as requested for this stage; it is not a full scrape of every rug.
The sitemap may contain outdated or unavailable pages. `manifest.json` has
current counts and field coverage.

Each JSONL line keeps the source URL and ID, observation time, source category,
description, INR price, explicit MRP when shown, tags, all source image URLs,
variants/options, category-specific `custom` fields, and raw source data.
`custom` may include material, color, size choices, weave, shape, care, product
story, or brand-specific filters. Null MRP is intentional when the source did
not publish a distinct MRP. Availability is a snapshot, not live inventory.

## Images

`images/original/<brand>/` holds the exact downloaded bytes of each **primary**
source image. `images/<brand>/` holds a separate 1200-pixel JPEG derivative
with an opaque black `DEMO` label at bottom right. The original stays intact.
Records contain both `original_image` and `demo_image` paths. The bot sends
only `demo_image`. Other gallery image URLs remain in JSONL for provenance;
they must be cached and marked before they are shown in a demo.

To change the label, rebuild from the local originals without downloading them
again:

```sh
python3 scripts/prepare_demo_images.py --rebuild-demo
```

Use `--label 'PREVIEW'` for different text or `--label ''` for a clean
derivative. The bot reads the same mounted paths, so a label-only change needs
no database import or catalog download. Restore `--label DEMO` before showing
the owner demos.

Pillow is needed for the image script. Image files are ignored by Git and
mounted into the Docker app from this directory. Keep `images/original/` when
cleaning generated files; it is the local source cache. A fresh checkout must
download images once before it can display marked photos.

To recollect the four sources supported by the command-line collector:

```sh
python3 scripts/collect_demo_catalogs.py dailyobjects
python3 scripts/collect_demo_catalogs.py blue_tokai
python3 scripts/collect_demo_catalogs.py nicobar
python3 scripts/collect_demo_catalogs.py ellementry
```

Jaipur Rugs blocked direct command-line requests during collection, so its
snapshot was read from public product pages through a browser. It does not yet
have an automated command-line refresh. After collecting again, run the image
script and importer. The importer upserts by brand and source ID, so rerunning
it does not create duplicates.

## Bot setup

```sh
docker compose build app
docker compose up -d db
docker compose run --rm app alembic upgrade head
docker compose run --rm app python -m telegram_ecommerce.database.import_demo_catalogs
docker compose up -d app
```

Set `DEMO_BRAND` in `.env` before starting the app. `/start` and `/demo` browse
the selected brand's categories. Send plain text such as `charger` to search
that same brand. Change `DEMO_BRAND` and restart to serve another brand. For
simultaneous owner demos, run separate bot instances with different Telegram
tokens and a brand setting for each. All instances may use the same PostgreSQL
catalog because every demo query is restricted to the configured brand.
