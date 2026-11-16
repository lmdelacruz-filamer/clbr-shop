# CLBR launch checklist

Tick everything before you announce the shop. The first block is code and settings; the second is business paperwork.

## A. Site settings (10 minutes)
- [ ] `config/settings.py` → **POLICIES**: replace the sample delivery time, shipping fee, return window and courier name with your real ones, then set `POLICIES_CONFIRMED = True`. (Until then the Terms and Shipping pages show a red DRAFT banner.)
- [ ] `config/settings.py` → **BUSINESS**: check phone, email, and add your **DTI registration number** to `dti_registration` once you have it. It then shows in the footer.
- [ ] Admin → Products: set real **prices** and **stock per size** (the starter products use placeholders: ₱750 shirts, 5 per size).
- [ ] Read `/privacy/`, `/terms/`, `/shipping-returns/` on the live site once. They are written as a sensible starting point; if you can, have a lawyer or a DTI e-commerce adviser glance at them.
- [ ] `.env`: a random `DJANGO_ADMIN_URL`, a real `DJANGO_SECRET_KEY` (the app refuses to start otherwise), strong `POSTGRES_PASSWORD`, `SITE_URL`.
- [ ] After the site has run fine for a few days: set `HSTS_SECONDS=31536000` in `.env` and redeploy.

## B. Acceptance test on the live site (see DEPLOY.md step 7)
- [ ] Place an order on a real phone using GPS, then another by typing the address.
- [ ] Screenshot the QR, scan it from the owner phone, confirm. Scan again and see the red "already redeemed" banner.
- [ ] Cancel that test order in the admin and check the stock came back.
- [ ] Let a test order sit unverified past the hold time (or set `ORDER_HOLD_HOURS=0` briefly) and confirm stock is released.
- [ ] Set up a free uptime monitor on `/healthz`, and copy `backups/` off the server (rclone to Google Drive).
- [ ] Look at the footer links, the 404 page, and the share preview (paste the site link in a private Instagram/Messenger chat).

## C. Business paperwork (not code, but what makes a shop legitimate)
Requirements change, so confirm each with the office named. These are the usual ones for a small online seller in the Philippines:
- [ ] **DTI business name registration** (sole proprietor). Add the number to the site footer.
- [ ] **BIR registration** and receipts/invoices for what you sell.
- [ ] **Barangay clearance and Mayor's / business permit** from Roxas City.
- [ ] Check the **DTI's guidance for online sellers** (Internet Transactions Act of 2023) for anything you must display on the site.
- [ ] Keep order records and receipts for your accounting. Customers' data is covered by the Privacy Notice; if you ever grow large or collect sensitive data, check whether National Privacy Commission registration applies.
- [ ] Use a real brand email on your own domain (you@yourdomain) and put the domain on your Instagram/Facebook bios so buyers can verify the shop is yours.
- [ ] Register the domain in your own name, with auto-renew on.

## D. CLBR Radio (SoundCloud), if you launch with it
- [ ] In **Ops → Radio** add each mixtape: title, DJ, SoundCloud link, tracklist (`time | artist | title`, one per line). Every active mixtape is listed on the **/radio/** page, newest first. No active mixtape = the Radio menu link disappears.
- [ ] Upload mixes to SoundCloud as **private first** and watch them for a few days; only go public if nothing is flagged. A clean scan is not permission: ask the artists (FS Green, Penomeco, Kaytranada's team) for approval, and credit every artist in the tracklist.
- [ ] Test on a **real iPhone and a real Android phone**: pick a mix, hear sound, drag the time bar, pause, +15s, fullscreen, close, then pick a different mix. If a phone needs one more tap, the page says "Tap play to start the sound".
- [ ] The SoundCloud player is **hidden by default** (moved off-screen; only the CLBR player shows). SoundCloud's docs do not say whether hiding is allowed, so read their current terms and the Buttons & Logos page. To show SoundCloud's own player instead, set `RADIO_HIDE_PLAYER=0` in `.env`.
- [ ] Nothing loads from SoundCloud until a mix is picked, and the Privacy page says so. Re-read that line if you change how the player loads.
- [ ] If a mix is ever removed from SoundCloud, hide it in Ops → Radio or swap the link. Each mix keeps a "Listen on SoundCloud" link.
