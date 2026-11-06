# Shopping assistant and chat carts

Ordinary text outside a command's input step reaches an OpenAI Responses API
assistant. For example: "Find a notebook", "Add two Demo Notebooks to my cart",
"Make it three", "Remove the notebook", and "Clear my cart". After browsing
product cards with `/search` or `/show_categories`, ordinary text also reaches
the assistant. Commands, admin changes, checkout, and payment use deterministic
Telegram handlers.

Set `OPENAI_API_KEY` in the ignored `.env` file. `OPENAI_MODEL` defaults to
`gpt-6.1-terra` for shopping and tool use; `OPENAI_INTENT_MODEL` defaults to
`gpt-6-luna` for the isolated intent check. Both use Responses and support strict
function calling and structured output. `OPENAI_REASONING_EFFORT=none` preserves
the previous effective setting for this bounded task. If changing models,
choose an effort they support. Setting both model variables to `gpt-6-luna`
is also supported, but validate your shopping flows before deploying a change.
Apply `alembic upgrade head` before starting the updated bot;
the cart migration is `14b8c5f7a2d1`. With Docker, run
`docker compose run --rm app alembic upgrade head`. Restart the app after upgrading.

## Cart behavior

Every Telegram chat has its own persistent cart. In a group, all members share
that cart. The server takes the chat ID from Telegram update metadata; the
model cannot choose a chat ID. Registration is not required to manage a cart.

| Command | Behavior |
| --- | --- |
| `/cart` | Show current items, stock availability, prices, and total |
| `/cart add PRODUCT_ID [QUANTITY]` | Add units; default quantity is one |
| `/cart set PRODUCT_ID QUANTITY` | Set the final quantity; zero removes the item |
| `/cart remove PRODUCT_ID` | Remove an item |
| `/cart clear` | Remove all items |

These commands work without OpenAI. A cart supports at most 50 different
products and 99 units per product. Add/set validates stock; carts do not reserve
inventory or lock prices. Checkout remains the existing single-product Telegram
checkout through `/search` or `/show_categories`; cart checkout is not implemented.

PostgreSQL stores `carts` (chat ID), `cart_items` (chat ID + product ID), and
`cart_operations` (chat ID + request ID). Foreign keys, quantity checks, and
row locks protect consistency. A mutation and its receipt commit in one
transaction. Repeating the same request replays the receipt; changing its
payload with the same request ID is rejected. Receipts survive cart clearing.
The UI and assistant refresh current contents on replay instead of showing
the historical receipt. Do not purge receipts while requests can still retry.

## Tools and activity messages

Tools list categories, search products, inspect products, read the cart, add
items, set quantities, remove items, and clear the cart. Product IDs used for
changes must have appeared in a catalog/cart tool result during that turn.
The [shared ranked search](catalog-search.md) includes names, brand, tags and
descriptions, with partial-name and typo matching. When only related matches
exist, the assistant shows up to five product buttons immediately. Selecting
one opens details and provides short-lived chat context for follow-ups; it does
not authorize a cart change. A related search result cannot be written to the
cart merely because the model inspected it.
The decision request batches routing with four cart-intent predicates, using only
the current user message. Confident operation answers are reused for that turn;
uncertain answers fall back to the existing isolated structured intent check.
Neither catalog text nor conversation history enters these permission checks. It is an additional
model-based check, not a proof that arbitrary model output is correct.
If a requested change has no successful write, generated success claims are
replaced with a localized failure message. Invalid calls and rejected mutations
also stop with a localized failure message.
If earlier writes succeeded, the reply explicitly says the request was partial.

One Telegram message starts as "Thinking…", then shows activity from real tool
dispatches: "Finding notebook…", "Opening your cart…", "Adding Demo Notebook
to your cart…", and so on. The final answer replaces that message. These are
activity summaries, not private model reasoning. Rapid updates are coalesced;
Telegram rate limits are respected. Final delivery uses at most four attempts
and ten seconds of rate-limit waiting. Delivery failures never replay a mutation.
If Telegram stays unavailable, the answer may not arrive; `/cart` shows saved state.

The assistant keeps three recent conversation turns per chat in memory for
30 minutes. This context expires on restart; carts persist in PostgreSQL.
The model receives that history, the current request, and chosen tool results.
Avoid sending passwords or payment details as ordinary chat messages.

Limits: 1,000 input characters, 10 tool calls, six tool rounds, 3,000 response
characters, and a 15-second timeout per API request (one SDK retry). Chat-scoped
conversations reuse batched decision predicates when conclusive; otherwise they
add one isolated intent request, cached for the turn. The bot
processes Telegram updates sequentially. No tool can pay, refund, edit products,
or place an order. New fixed messages are supplied in English and Portuguese,
with the existing locale fallback for other languages; model replies follow
the user's language.

## Verification

Run `python -m pytest -q` with `TEST_DATABASE_URL` pointing at a **dedicated test
database**; PostgreSQL tests reset its schema/data. Never point it at a live DB.
Tests cover isolation, concurrent increments, duplicate delivery, transaction
rollback, fresh replay views, failed writes/replies, intent isolation, Telegram
rate limits, and real handler routing using a fake Telegram transport.

For a live check, seed the local demo catalog with
`python -m telegram_ecommerce.database.seed_demo`. Ask to add two Demo Notebooks,
set the quantity to three, view `/cart`, remove the notebook, then clear the cart.
Check another chat remains empty. In groups, ordinary messages must be visible
to the bot under its Telegram privacy settings.

API references: [function calling](https://developers.openai.com/api/docs/guides/function-calling),
[structured outputs](https://developers.openai.com/api/docs/guides/structured-outputs),
[GPT-6.1 Terra](https://developers.openai.com/api/docs/models/gpt-6.1-terra),
[GPT-6 Luna](https://developers.openai.com/api/docs/models/gpt-6-luna),
and [Telegram message editing](https://core.telegram.org/bots/api#editmessagetext).


## Request routing and preview conversations

`agent/routing.py` chooses work before generation. `OPENAI_DECISION_MODEL` and
`OPENAI_READ_MODEL` default to `gpt-6-luna`; `OPENAI_MODEL` remains the stronger
shopping/comparison model (currently configured as Terra). No Jev service or
additional credentials are introduced. The implementation uses the
[OpenAI Decisions API](https://developers.openai.com/api/docs/guides/decisions),
which supports typed routing choices and predicates.

| Request | Work |
| --- | --- |
| Greeting or explicit category phrase | No model call |
| Short, context-free product keywords with backend matches | Database search, no model call |
| Other confidently classified literal keywords | One decision request, then database search |
| Simple natural-language discovery | One decision request, one Luna tool-selection response, then product buttons |
| Comparison or contextual recommendation | One decision request, then Terra with bounded read-only tools |
| Live cart change | Batched decision/intent, guarded existing tool loop, fallback intent check only if needed |

The direct keyword shortcut is intentionally narrow and never strips words.
Sentences, negation, budgets, comparisons and follow-ups go through routing;
search relevance stays entirely in the shared database implementation. All
product choices retain IDs from real results. Direct searches returning no hits
continue through routing. `/search` and category buttons remain deterministic.

Routing requires confidence >= 0.90; inconclusive, malformed or refused decisions
use the guarded assistant. Cart predicates are accepted only when **all four**
probabilities are <= 0.01 or >= 0.99. Conflicting routing and cart intent use the
guarded path. These thresholds are conservative starting policies, not a
measured accuracy guarantee. Product eligibility, stock checks, chat scoping,
idempotency and mutation validation still execute in application code.

Read-only turns expose no mutation tools and reject unexpected mutation calls
at dispatch. Independent read calls run concurrently; cart writes remain
sequential. Search cards skip model-generated restatements. Short read-only
instructions avoid sending irrelevant cart instructions on every read.

`DEMO_BRAND` now supports conversational questions against **only that snapshot**,
with per-user/per-chat history (three turns, 30 minutes) and opened-product context.
Preview tools cannot access live products or carts. Preview prices/availability
are source snapshots, and purchases remain unavailable. `/search` works without AI.

Logs include route, logical API call count, endpoint, model, elapsed milliseconds
and returned token usage, without request text or credentials. SDK retries are
not separate logical calls. A 429 starts an in-process cooldown using Retry-After
(bounded to 5 seconds–1 hour); neither that turn nor following turns cascade into
more models during the cooldown. Backend search and browsing remain available.
Restart clears this in-memory cooldown. A failed final response after a cart write
still reports the saved/partial outcome rather than retrying the mutation.

Verification includes zero-call searches, one-response product cards, typed SDK
response parsing, uncertain/refused decisions, batched intent reuse, denied writes,
merchant isolation, independent-read concurrency, preview context and rate-limit
cooldown. Live verification on November 7 reached the account's 50-request/day Luna
limit; a minimal Decisions request succeeded beforehand. End-to-end latency and
monetary savings are **not yet established**. Compare route/token logs on representative
shopper requests once access resets before claiming a production speedup.


In previews, `/search` without arguments asks for keywords using Telegram's reply
prompt. The next text reply (within five minutes, scoped to that user and chat)
searches the database directly. `/search keywords` also bypasses AI; `/cancel`,
`/start`, or a catalog button exits the pending search. Common catalog requests,
including “what do we have”, browse without a model. Category messages include a
text overview as well as inline buttons.
