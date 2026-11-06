# Catalog search for a small shop

Search runs in PostgreSQL through the existing `search_products(query, limit)`
function. The Telegram `/search` command and the assistant's search tool use
that same function and receive the same Product objects. Product browsing,
checkout, payments, and admin flows retain their existing interfaces.

The priorities are useful results, current availability, low operating cost,
and simple maintenance. Test catalogs of 100 and 1,000 products are workload
examples, not capacity limits. No remote search service, catalog copy, cache,
or synchronization job is needed for this increment.

## Matching and ranking

Lexical search combines PostgreSQL's `simple` dictionary with English stemming.
The simple path preserves exact words in other languages; the English path
matches forms such as `notebook` and `notebooks`. Product names and descriptions
are searchable. Exact names rank first, followed by matches in names, then
matches only in descriptions. Relevance and product ID make ties deterministic.

Only when lexical search finds no products does a conservative typo fallback
compare query words against product names with `pg_trgm`. All query words must
match; short terms must match exactly. This avoids turning `red notebok` into
an unrelated blue notebook just because “notebook” is close. Spelling correction
happens in the search function; the assistant does not have to rewrite plurals.

Both paths exclude sold-out products and apply the result limit after ranking.
Queries use bound parameters. The database remains the source of truth, so
product and stock edits take effect without cache invalidation or reindex jobs.
Existing limits remain: `/search` returns the top 10 and the assistant requests
the top 5; the shared function caps any requested limit at 50. Pagination is a
separate increment. Queries accept up to 100 characters and eight words.

## Deployment and limits

Apply `alembic upgrade head` (revision `29f6c2a7d8b4`) before restarting the updated application. The
migration adds an English full-text index and enables PostgreSQL's bundled
`pg_trgm` extension. The database role needs permission to create the extension;
on custom PostgreSQL installations its contrib files must be installed first.
The standard `postgres:16-alpine` image includes them. Downgrade removes this
increment's index and leaves the potentially shared extension installed.

Typo fallback is intentionally a database scan of product names after zero
lexical matches. It avoids another index until measurements justify one.
If real catalog size, query volume, or observed latency warrants it, use a
matching trigram index/query operator or evaluate a dedicated search service
against the same relevance tests before changing the architecture.

English stemming does not mean translation or full linguistic support for
100+ languages. Exact words in other scripts remain searchable; synonyms,
cross-language product retrieval, accent normalization, and language-specific
stemming beyond English need separate relevance examples and increments.

## Verification

Direct PostgreSQL tests check word forms, spelling mistakes, multiword
constraints, name-versus-description ranking, stable limits, language coverage,
unrelated queries, and availability. Run the full suite with
`TEST_DATABASE_URL` set to a dedicated disposable PostgreSQL database.

Local measurement on PostgreSQL 16.11 in Docker used synthetic catalogs of 100
and 1,000 available products across five product types, with names and short
descriptions. After one warm-up, 25 calls per query included SQLAlchemy session
and database round-trip time. At 1,000 products:

| Query | Median | 95th percentile |
| --- | ---: | ---: |
| `notebook` | 5.56 ms | 8.30 ms |
| `notebooks` | 5.22 ms | 8.11 ms |
| `notebok` | 11.21 ms | 12.20 ms |
| `red notebok` | 5.31 ms | 6.35 ms |
| `zzzxqv` (no match) | 9.36 ms | 10.82 ms |

These are development measurements of the search function, excluding OpenAI
and Telegram calls, not a production latency guarantee. They support keeping
the first implementation in the existing database and measuring real workloads
before adding another search service or index.

References: [PostgreSQL dictionaries](https://www.postgresql.org/docs/16/textsearch-dictionaries.html)
and [pg_trgm](https://www.postgresql.org/docs/16/pgtrgm.html).
