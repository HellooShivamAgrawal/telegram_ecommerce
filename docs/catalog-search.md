# Ranked catalog search

The shared PostgreSQL search now ranks product names, brands, merchant tags,
short descriptions and full descriptions. Both the assistant and `/search`
use it. The existing `search_products(query, limit)` API still returns Product
objects; `search_catalog` additionally returns match metadata for the UI.

## Matching and ranking

All query words must match somewhere on a product. Whole words and English
inflections are strong matches; three-letter prefixes, longer partial names
and plausible spelling errors are related matches. Short size/model codes
such as A5, XL and 4K stay exact. For example:

- `book` ranks an available Book first and can also find Notebook or Bookcase.
- `notebook`, `notebooks`, `notebok` and `note` can all find Notebook.
- `Acme red A5 notebook` requires the brand, color, size and product term.
- Merchant tags such as `journal, exercise book` provide explicit aliases.

Full exact names rank first, then products matching every word lexically, then
related matches. Within those groups, each word contributes its strongest
matching field, weighted as follows:

| Field | Weight |
| --- | ---: |
| Name | 10 |
| Brand | 6 |
| Tags / merchant aliases | 5 |
| Short description | 3 |
| Description | 1 |

Exact lexical matches have strength 1, partial matches 0.65, and qualifying
whole-word trigram matches 0.5 + 0.3 × similarity (minimum similarity 0.5).
An exact full name receives an additional 40 points. Repeating a word in a
long description cannot inflate its contribution. Ties use stable product IDs.
Sales volume does not override relevance. These are transparent ranking rules,
not calibrated confidence probabilities or a copy of a retailer's proprietary
ranking model.

Names and descriptions in other scripts remain searchable with PostgreSQL's
simple dictionary. English stemming is additional support, not translation or
full linguistic support for 100+ languages. Language-specific segmentation,
accent folding and semantic translation remain separate capabilities.

## Related options and selection

When every returned product is approximate, Telegram shows up to five named
buttons with prices. Fewer real matches produce fewer buttons. Selecting one
refreshes availability and opens the existing details/checkout view. It does
not add a product to the cart or buy it. Selection is remembered briefly in
that chat so a follow-up such as “add two of it” has context.

The assistant returns these options immediately after the search tool, without
an additional model-generated explanation. Related products cannot be written
to the cart just because the model inspected them. Choice callbacks are bound
to the shopper, chat and message, and stale/unavailable choices are rejected.
Product browsing and checkout keep their existing flows.

Queries accept up to 100 characters and eight words. Results exclude sold-out
products. `/search` has a default limit of 10; the assistant requests five;
the compatibility API caps requested limits at 50. Pagination is separate.

## Merchant metadata

Existing products and the existing add-product wizard continue to work with
empty metadata. Optional fields are `brand` (80 characters), `short_description`
(200), and `tags` (500, comma-separated keywords/aliases). Add or edit them with:

```sh
docker compose exec app python -m telegram_ecommerce.database.catalog_metadata 1 \
  --brand Acme --short-description 'A5 dotted journal' \
  --tags 'stationery, journal, exercise book'
```

Only supplied fields change; an empty string clears a field. No brand or tags
are invented for the demo products. The helper validates types/lengths, and
ordinary product edits refresh the generated search text automatically.

## Database and deployment

Apply `alembic upgrade head` through revision `6a90c8d2e5f1`, then restart the
app. This additive migration adds the optional metadata, generated lower-case
search text, and GIN full-text/trigram indexes. The prior migration supplies
`pg_trgm`. No separate search service, cache, copied catalog or synchronization
job is required. Downgrade preserves the original product data but removes the
new metadata columns, so export those values first if they must be retained.

Indexed full-text, substring and trigram predicates select candidates. Every
candidate is then scored against individual fields; a materialized query stage
avoids repeating the expensive field calculations for filtering and sorting.
The trigram candidate threshold (0.49) is just below the scoring cutoff (0.5).
Threshold and custom-plan settings are transaction-local and do not leak into
pooled connections. Query values are bound parameters.

## Verification and timings

Tests cover field ordering, aliases, repeated descriptions, inflections, typo
and partial matches, multiword constraints, exact model codes, availability,
malformed input, migrations, metadata freshness, chat isolation and the real
Telegram handler path using a fake transport.

A local Docker/PostgreSQL 16 benchmark on November 7, 2026 used 100, 1,000 and
10,000 synthetic products across five product types, colors and brands. Each
query had one warm-up and 25 timed calls, including ORM/database round trips.
The previous implementation was run on the identical data. At 1,000 products:

| Query | New median | New p95 | Previous median | Previous result |
| --- | ---: | ---: | ---: | --- |
| notebooks | 29.49 ms | 30.39 ms | 5.74 ms | Matches |
| book | 28.27 ms | 29.35 ms | 12.16 ms | No matches |
| notebok | 32.94 ms | 33.83 ms | 14.76 ms | Matches |
| acme red A5 notebook | 17.50 ms | 19.29 ms | 2.55 ms | No matches |
| paperwoks | 33.65 ms | 34.46 ms | 12.67 ms | No matches |
| journal | 27.58 ms | 28.45 ms | 12.49 ms | No matches |
| zzzxqv | 26.28 ms | 30.16 ms | 12.50 ms | No matches |

At 100 products, medians were 6.56–12.39 ms. At 10,000, successful queries had
medians of 73.47–258.60 ms; the slowest observed p95 was 313.50 ms. Broader
retrieval and weighted ranking cost more than the old restrictive search.
These development timings exclude OpenAI and Telegram; they are not production
latency guarantees or catalog capacity limits. Measure real catalogs before
adding infrastructure or further tuning.

To reproduce against a **dedicated disposable database** ending in `_test`:

```sh
# TEST_DATABASE_URL must already point to that expendable database.
python -m scripts.benchmark_search --reset --sizes 100 1000 10000 --iterations 25
```

The command deletes existing data in the explicitly supplied test database and
never loads the application's `.env`. Optional `--baseline PATH_TO_OLD_QUERY_PY`
compares an earlier implementation on the same data.

References: [PostgreSQL text search](https://www.postgresql.org/docs/16/textsearch-controls.html)
and [pg_trgm matching and indexes](https://www.postgresql.org/docs/16/pgtrgm.html).
