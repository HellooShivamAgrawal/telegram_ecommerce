# Launch readiness — November 7, 2026

**Decision: not ready for a public transactional launch.** The native preview
browser is being prepared for review and a controlled demo. Passing automated
checks does not establish live payment, fulfillment, or service reliability.

## Workflow audit

| Journey | Evidence | Remaining boundary |
| --- | --- | --- |
| Start and categories | Real Telegram handler dispatch, category-scoped results | Catalog and nine-item picker observed in Telegram; automated clicks unavailable |
| Explicit and keyword search | PostgreSQL relevance, typo/modifier/isolation tests; no API required | Snapshot freshness depends on import |
| Category → product → Next/Previous | Multipart serialization and constant message ID | Catalog and nine-item picker observed in Telegram; automated clicks unavailable |
| Details and nine-item picker | Caption edits, 9/9/2 pagination, whole-result selection | Native text picker; no image grid |
| Missing photos / failed edits | Text fallback, neutral artwork, retained prior index | Transport failure may prevent even an alert |
| Live-store cart | PostgreSQL CRUD, isolation, stock checks, concurrent writes and replay tests | No combined cart checkout exists |
| Checkout | Existing registration, price, currency and stock validation tests | No payment provider configured on running bot |
| Payment callback | Duplicate charge and oversold-payment reconciliation tests | No real/sandbox provider end-to-end payment performed |
| Admin/catalog changes | Existing authorization and metadata freshness tests | Zero admin accounts configured on running bot |
| AI routing and tool boundaries | Typed parser, uncertain routes, denied writes, preview isolation, API cooldown tests | Live model routing benchmark was interrupted by daily request limit |

## Observed deployment gates

Read-only inspection of the running bot found `DEMO_BRAND=dailyobjects`, no
`PROVIDER_TOKEN`, zero configured admin IDs, and two live products. Preview
prices are INR source snapshots; the ordinary shop's currency is currently
hard-coded USD. None of these settings were silently changed during the audit.

Before a transactional launch:

1. Choose the actual merchant/catalog, currency and enabled purchase path. Import
   owned, current stock and prices; demo snapshots must not become live inventory
   merely by turning off preview mode.
2. Configure an admin and the chosen payment provider in its sandbox, then verify
   registration → product → invoice → pre-checkout → success → order/stock update
   and duplicate webhook/callback delivery. Only then configure production mode.
3. Resolve the Luna request limit and evaluate real shopper queries, including
   multilingual requests, budgets, comparisons, corrections and ambiguous cart
   writes. Record route accuracy, end-to-end latency and token usage; confidence
   thresholds alone are not an accuracy guarantee.
4. Decide whether launch supports only single-product checkout or requires cart
   checkout. The current persistent cart is not a checkout implementation.
5. Verify order handling, shipping, cancellation/refund procedures, database
   backup/restore and operational error monitoring. These have not been proven
   by this code-level audit.
6. Review and integrate the PR stack. The separate PayPal connector work is
   outside this branch and is not included in its deployment or readiness claim.

## Mini App follow-up

A Mini App is the appropriate separate feature for a true visual grid and richer
filters. Keep Telegram as the conversational entry point; a bottom “Browse shop”
menu button can open the Mini App with an explicit search or product context.
Reuse the current catalog/search backend rather than duplicate ranking in JS.

That feature requires an HTTPS-hosted frontend, server-side validation of Telegram
init data before user/cart operations, and a deliberate handoff between chat and
app. Do not trust client-supplied user IDs or expose bot/API secrets in the browser.
Its acceptance journey is chat query → open matching grid → details → select/cart
→ return to chat with the same selection. It has not been implemented in this PR.

References: [Telegram Mini Apps](https://core.telegram.org/bots/webapps) and
[Bot API message editing](https://core.telegram.org/bots/api#updating-messages).

## Verification record

The prerequisite branch passed 270 tests and 13 subtests in a dedicated
PostgreSQL database. The final browsing branch passed 272 tests and 13 subtests after the live-audit
fallback correction and stale-search-card checks. GitHub CI results for each
pushed revision are tracked on the prerequisite and browsing PRs.

Live Telegram observations confirmed the category overview and nine-item picker.
The desktop automation could use the composer but inline-button pointer actions
failed with `AXError.notImplemented`, so automated live click completion is not
claimed. Actual runtime logs revealed a failed photo upload followed by rejected
attempts to add media to a text fallback card. The browser now keeps such cards
as text during navigation; photos remain editable on actual photo messages.
Transport tests cover both paths and rejected edits. Active DailyObjects image
files were present for all 706 products, so missing local files did not explain
the observed upload failure. External Telegram transport reliability remains an
operational gate, not a solved issue inferred from unit tests.

A fresh live Decisions probe still returned `rate_limit_exceeded`. No full
natural-language model benchmark or sandbox payment completion was possible. No real money was moved during this
audit and no claim of final public-launch readiness is made.
