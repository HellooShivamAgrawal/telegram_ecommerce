# Project working preferences

- Design for a small shop: reliable shopping, relevant search, clear checkout,
  low running costs, and straightforward maintenance. Catalog sizes used in tests
  are workload examples, not capacity limits.
- Keep improvements focused and reviewable in incremental PRs. Preserve working
  commands, product browsing, admin workflows, and payment behavior; avoid broad
  rewrites while adding a feature.
- Reuse the existing application and PostgreSQL before adding services or caches.
  Add infrastructure when measurements or a concrete product need justify it.
- Product search quality belongs in the shared backend search function. Do not
  depend on an assistant prompt to correct plurals or spelling. Verify relevance
  with direct search tests and preserve the callers' existing contract.
