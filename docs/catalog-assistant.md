# Shopping assistant and chat carts

Ordinary text outside a command's input step reaches an OpenAI Responses API
assistant. For example: "Find a notebook", "Add two Demo Notebooks to my cart",
"Make it three", "Remove the notebook", and "Clear my cart". After browsing
product cards with `/search` or `/show_categories`, ordinary text also reaches
the assistant. Commands, admin changes, checkout, and payment use deterministic
Telegram handlers.

Set `OPENAI_API_KEY` in the ignored `.env` file. `OPENAI_MODEL` defaults to
`gpt-5.4-mini`. Apply `alembic upgrade head` before starting the updated bot;
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
A separate, cached-per-turn intent check reads only the current user message,
without catalog text or conversation history, before any write. Its strict
structured output limits which cart operations can run. It is an additional
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
conversations add one isolated intent request, cached for the turn. The bot
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
and [Telegram message editing](https://core.telegram.org/bots/api#editmessagetext).
