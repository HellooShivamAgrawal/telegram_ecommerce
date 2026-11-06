# Catalog Mini App

The bot's **Shop** menu and welcome button open a six-product photo grid. Categories and search use the existing PostgreSQL catalog; product details open in a sheet without losing the results. **Open product in chat** deep-links to the selected catalog item for follow-up questions. The preview cannot place orders or access customer records.

## Run

Set `DEMO_BRAND`, `BOT_USERNAME` (without @), and `MINI_APP_URL` (HTTPS origin without query parameters). Build the application and run `docker compose --profile miniapp up -d --build`. Serve the loopback-only catalog port (8088 by default) through your HTTPS reverse proxy. The bot registers its Shop menu on startup. No URL means the existing native browsing flow remains available.

The public web routes expose only the active merchant's snapshot products and derivative images. All search calls use the shared backend. Images are constrained to the catalog directory, limited to 720px, JPEG-compressed, and cached in a bounded 128-entry memory cache. Transparent originals are composited onto a light background rather than becoming black. Source files remain untouched. Telegram can fetch these derivatives by HTTPS URL instead of uploading multipart images over the failing TLS connection.

Search currently shows the best 50 matches, labeled `50+`; browse/category pagination covers the full catalog. Approximate matches are labeled. Failed requests preserve search input and provide Retry. No credentials or signed Telegram user data are needed or sent for public catalog browsing. Any future account/cart API must validate Telegram initData server-side and enforce ownership.

## Verification and launch boundary

277 tests passed, including existing cart/payment safety tests, scoped search, six-item pagination, compression, and same-message navigation. Browser inspection covered desktop and 390px phone layout with real images, category filtering, product details, and search. A temporary HTTPS tunnel is for owner review only; it has no uptime guarantee and depends on the local machine. Configure stable HTTPS hosting before launch. Checkout, API capacity and operational readiness remain subject to the launch-readiness audit.
