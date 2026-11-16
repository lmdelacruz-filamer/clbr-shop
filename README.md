# CLBR Wear Co. — Checkout + QR Order Verification

Django 5 · PostgreSQL · Tailwind (CDN build) · vanilla JS. Two security layers:
**public checkout** (untrusted input) and **staff-only scanner** (trusted, audited).

## Architecture

```
Customer browser                     Django (store app)                    PostgreSQL
-----------------                    ------------------                    ----------
shop.html + checkout.js  --JSON-->   views_public.create_order             Product (stock)
  cart, GPS, Nominatim               forms.OrderForm (validation)          Order (UUID pk, token HASH)
  Enter-to-submit                    services.place_order (atomic,         OrderItem (price snapshot)
  QR render (qrcode.js)  <--payload-- SELECT…FOR UPDATE, reserve stock)    ScanLog (audit)

Staff phone  /ops/  (login + is_staff)
  html5-qrcode / upload / paste --JSON--> views_ops.api_lookup / api_confirm
                                          services.lookup / confirm (row lock,
                                          PENDING→VERIFIED exactly once)
```

### Files
| Path | Role |
|---|---|
| `store/models.py` | Product, Order, OrderItem, ScanLog |
| `store/services.py` | **All business rules**: stock reservation, expiry, replay protection |
| `store/forms.py` | Server-side validation (PH mobile → `+63…`, lat/lng ranges) |
| `store/security.py` | Rate limiter, staff decorator, Permissions-Policy header |
| `store/views_public.py`, `views_ops.py` | Thin HTTP layer |
| `store/static/store/js/checkout.js` | Cart, GPS, reverse-geocode, Enter key, QR |
| `store/static/store/js/ops_scanner.js` | Camera / screenshot / manual scan, manifest, confirm |
| `store/management/commands/release_expired_orders.py` | Cron: return stock from stale orders |

## Key design decisions
1. **Token is hashed.** The QR carries `CLBR1:<order-uuid>:<256-bit token>`. The DB stores only
   `sha256(token)`, so a database leak can't be used to forge QR codes.
2. **No personal data in the QR.** Name/phone/address stay in the DB and are shown only to
   logged-in staff after a scan. (A QR with PII in it would leak it from every screenshot sent over DM.)
3. **Inventory = reserve on order, release on expiry.** Stock is deducted inside a transaction
   with row locks (no overselling). Each order has `expires_at` (default 24 h, `ORDER_HOLD_HOURS`);
   cron returns the stock of unverified orders. Restock is guarded by a status check under lock → never twice.
4. **Replay protection is atomic.** `confirm()` locks the order row and flips `PENDING → VERIFIED`
   only if still `PENDING`. A second scan/click gets the red
   “⚠️ Warning: This QR code has already been redeemed/verified” banner. Every scan is logged in `ScanLog`.
5. **Prices and totals are computed server-side.** The client sends only product ids + quantities.
6. **XSS:** customer text is rendered with `textContent`/Django autoescape only — it is attacker-controlled
   and ends up on your admin screen.
7. Extra status `EXPIRED` beyond your four (needed to represent released stock).

## Implementation plan
1. `python -m venv .venv && pip install -r requirements.txt`
2. Create Postgres DB/user; copy `.env.example` → `.env` and fill it in (export the vars).
3. `python manage.py makemigrations store && python manage.py migrate`
4. `python manage.py createsuperuser` (strong password, 12+ chars) — this is your scanner login (`is_staff`).
5. Open `/<DJANGO_ADMIN_URL>/`, add Products (name, price, stock, image).
6. Run locally with `DJANGO_DEBUG=1 python manage.py runserver`; GPS/camera also work on `localhost`.
7. `python manage.py test store`
8. Deploy: gunicorn behind nginx + Let's Encrypt (**HTTPS is mandatory** — browsers block GPS and camera otherwise).
   nginx must set `X-Real-IP $remote_addr` and `X-Forwarded-Proto $scheme`; serve `/static/` and `/media/` itself;
   `python manage.py collectstatic`.
9. Cron: `*/10 * * * * cd /srv/clbr && .venv/bin/python manage.py release_expired_orders`
10. Production hardening: Redis (`REDIS_URL`) so rate limits are shared across workers; `django-axes` for
    login lockout; compile Tailwind with the CLI instead of the CDN script; self-host `qrcode.min.js` and
    `html5-qrcode.min.js` (or add SRI hashes) and add a CSP (`django-csp`); nightly `pg_dump` backups.

## Daily workflow
Customer checks out → QR → screenshots it → sends via Instagram DM → you open `/ops/` on your phone →
scan (camera, screenshot upload, or paste) → green “Authentic” + manifest → **Confirm & Verify Order** →
ship COD → mark Shipped/Completed from the Django admin (bulk actions).

## Storefront content
Edit `ARTIST`, `SIZE_CHART`, `SIZE_CHART_CONFIRMED` in `config/settings.py`. Load starter products with `python manage.py seed_products` (placeholder prices/stock; edit in admin). Stock is tracked per size (ProductSize): set each size's quantity in the admin; orders deduct from that size and a size at 0 is sold out.

## Notes / limits
* Nominatim's public server is for light use (1 request per click here) — fine for a small shop; use a
  paid geocoder if traffic grows. Coordinates are sent to OpenStreetMap when the customer taps Auto-Locate.
* GPS from a phone can be ±20–100 m off; the manual address always stays editable and wins.
* The `/ops/` login page is public by nature; protect it with a strong password (+ axes, optional IP allow-list in nginx).

## Philippines-only delivery
* Phone numbers must be PH mobiles (normalised to `+639XXXXXXXXX`).
* GPS pins outside the PH bounding box are rejected by the server; the browser also rejects pins whose
  reverse-geocoded `country_code` isn't `ph`. The bounding box is coarse (parts of Sabah/Kalimantan fall inside it),
  which is why the country-code check exists. A hand-typed address can't be machine-verified — you still
  confirm every order by hand on Instagram before shipping.

## Deploying
See `DEPLOY.md` (Docker Compose + Caddy auto-HTTPS + backups).

## Added for launch
* Pages: Shipping & Returns, Terms of Sale, Privacy Notice (Data Privacy Act), Contact. Footer on every page.
* Checkout consent checkbox (validated on the server; the time is saved on the order as `terms_accepted_at`).
* SEO: meta description, canonical, Open Graph image, JSON-LD, `robots.txt`, `sitemap.xml`, favicon/touch icons. The staff pages are `noindex`.
* Branded 404/500 pages, `/healthz`, logging of order events, `.env` auto-loading for local runs.
* Edit your real details in `config/settings.py`: `BUSINESS` (phone, email, DTI number) and `POLICIES` (+ `POLICIES_CONFIRMED = True`).
