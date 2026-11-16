# Going live — CLBR (Docker + Caddy auto-HTTPS)

Stack on one small VPS: Caddy (HTTPS) → gunicorn/Django → PostgreSQL. Cost ≈ US$6–12/month + domain.

## 0. What you need
* A **domain** (e.g. `clbrwear.shop`) — HTTPS is mandatory, GPS and camera won't work without it.
* A **VPS**: Ubuntu 24.04, 1 vCPU / 1–2 GB RAM. Pick the **Singapore** region (closest to the Philippines).
  DigitalOcean, Vultr, Hetzner, Linode all work.

## 1. DNS
Add an **A record**: `shop.yourdomain.com` → your server's public IP. Wait a few minutes.

## 2. Prepare the server
```bash
ssh root@YOUR_SERVER_IP
curl -fsSL https://get.docker.com | sh
ufw allow OpenSSH && ufw allow 80 && ufw allow 443 && ufw --force enable
```
(Optional but wise: create a non-root user and disable password SSH login.)

## 3. Check it locally first (once), on PostgreSQL
The live site runs on PostgreSQL, so test on it. In the project folder (with `.venv` active):
1. `copy .env.local.example .env`, then edit `.env` with your local Postgres password (the app reads this file automatically).
2. `psql -U postgres -c "CREATE DATABASE clbr;"`
3. `python manage.py migrate` then `python manage.py test store`. All tests must pass (53 at the time of writing). One of them fires 20 parallel buyers at the last 3 pieces to prove nothing oversells, and it only runs on PostgreSQL.
Never upload this local `.env` to the server. The server gets its own, made from `.env.example`.

## 4. Upload the project
Upload `clbr_shop_deploy.zip` to the server and unzip it into `/srv/clbr`
(`scp clbr_shop_deploy.zip root@YOUR_SERVER_IP:/srv/ && ssh root@YOUR_SERVER_IP "cd /srv && unzip clbr_shop_deploy.zip -d clbr"`).
**Never upload** your `.venv`, `db.sqlite3` or `media` folders. They are your laptop's, and the database holds your admin login.

## 5. Configure
```bash
ssh root@YOUR_SERVER_IP && cd /srv/clbr
cp .env.example .env && nano .env
python3 -c "import secrets;print(secrets.token_urlsafe(50))"    # use this for DJANGO_SECRET_KEY
chmod 600 .env
```
Fill in DOMAIN, ALLOWED_HOSTS, CSRF_TRUSTED_ORIGINS, a random DJANGO_ADMIN_URL, and a strong POSTGRES_PASSWORD.
The app refuses to start if DJANGO_SECRET_KEY is still the placeholder. After the live site has run fine for a few days, set `HSTS_SECONDS=31536000`.

## 6. Launch
```bash
docker compose up -d --build
docker compose logs -f web caddy        # shows "order placed / verified / cancelled" events too        # wait for "Listening" and a certificate being obtained; Ctrl+C to leave
docker compose exec web python manage.py createsuperuser    # this is YOUR scanner/admin login (12+ char password)
docker compose exec web python manage.py seed_products        # loads the starter products + photos (placeholder prices/stock)
```
Open `https://shop.yourdomain.com` → storefront. Add products at `https://shop.yourdomain.com/<DJANGO_ADMIN_URL>`.
Scanner: `https://shop.yourdomain.com/ops/`.

## 7. Acceptance test (do this before announcing)
1. On your phone, place a real test order (Auto-Locate GPS inside the Philippines; also try typing an address).
2. Screenshot the QR, open `/ops/` on a second device, scan it → green “Authentic” + manifest.
3. Tap **Confirm & Verify Order**, then scan the same QR again → red “⚠️ Already redeemed/verified”.
4. Try a stock-limited product twice to see the sold-out behaviour; check stock in the admin.

## 8. Backups
```bash
crontab -e     →     0 3 * * * cd /srv/clbr && ./backup.sh
```
Copy `/srv/clbr/backups` off the server regularly (e.g. to Google Drive with rclone).

**Restore (practice this once on a spare droplet, a backup you have never restored is only a hope):**
```bash
cd /srv/clbr
docker compose up -d db
gunzip -c backups/db-YYYY-MM-DD.sql.gz | docker compose exec -T db sh -c 'psql -U "$POSTGRES_USER" "$POSTGRES_DB"'
docker compose exec -T web tar -C /app -xzf - < backups/media-YYYY-MM-DD.tar.gz
docker compose up -d
```
(For a clean restore, start from an empty database: `docker compose down -v` first, then the steps above.)

## 8b. Old customer data (privacy promise)
The Privacy page says old orders are deleted or anonymised. Once a year (or whenever you like) run:
```bash
docker compose exec web python manage.py anonymize_old_orders --days 730          # preview only
docker compose exec web python manage.py anonymize_old_orders --days 730 --apply  # really wipe
```
It wipes name, phone, address and GPS of FINISHED orders older than 2 years, keeps totals for your accounting, and never touches open orders.

## 9. Updating later
```bash
cd /srv/clbr && git pull        # or re-upload files
docker compose up -d --build    # migrations + collectstatic run automatically
```

## Troubleshooting
* **Certificate not issued** → DNS A record not pointing at the server yet, or ports 80/443 blocked.
* **400 Bad Request** → domain missing from `DJANGO_ALLOWED_HOSTS`.
* **403 CSRF failed** → `DJANGO_CSRF_TRUSTED_ORIGINS` must be `https://your.domain` exactly.
* **web container exits immediately** → `docker compose logs web` (usually missing migrations or a bad .env).
* **Product images 404** → check `media_data` volume; upload via the admin, not by hand.

## Things that now happen automatically
* **Self-hosted scripts and fonts.** The Docker build runs `manage.py vendor_assets`, which downloads Tailwind, the QR libraries
  and the fonts into your own static files, so visitors' browsers never contact Google or a CDN. If the download fails
  (build server offline) the site silently falls back to the CDNs. Check the build log for "All third-party assets are now self-hosted."
* **Release of unverified orders** every 10 minutes (the `cron` container).
* **Uptime monitoring:** point a free monitor (UptimeRobot, Better Stack) at `https://your.domain/healthz`. It answers `ok` only when the database is reachable.

## Cancel an order and return its stock
Admin → Orders → tick the order(s) → action **"CANCEL selected orders and RETURN their stock"** → Go.
Use it for buyer cancellations and refused COD parcels. Completed orders can't be cancelled. A cancelled QR shows
"This order was CANCELLED" on the scanner, and stock is returned exactly once even if you click twice.

See `LAUNCH_CHECKLIST.md` before announcing the shop.
