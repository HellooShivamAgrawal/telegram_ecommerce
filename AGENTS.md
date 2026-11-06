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
- Search should rank useful partial and fuzzy candidates across merchant fields.
  Keep exact products ahead of related alternatives, preserve query modifiers,
  and offer choices for weak matches instead of silently choosing a substitute.
- Keep Telegram browsing in one editable product card: Next, Previous, Details,
  and product-picker actions must not append messages. Test message identity and
  actual Telegram media serialization, not just handler return values.
