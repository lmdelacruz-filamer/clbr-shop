/* CLBR staff scanner: camera + screenshot upload + manual paste -> /ops/api/lookup/ -> manifest -> confirm.
   All order data (customer-controlled!) is rendered with textContent only. */
(() => {
  'use strict';

  const $ = (sel, root = document) => root.querySelector(sel);
  const root = $('#ops');
  const CSRF = $('meta[name="csrf-token"]').content;
  const URLS = { lookup: root.dataset.lookupUrl, confirm: root.dataset.confirmUrl };
  const peso = new Intl.NumberFormat('en-PH', { style: 'currency', currency: 'PHP' });
  const when = (iso) => (iso ? new Date(iso).toLocaleString('en-PH', { dateStyle: 'medium', timeStyle: 'short' }) : '—');

  const out = $('#result');
  const camBtn = $('#cam-btn');
  let scanner = null, scanning = false, handling = false, currentPayload = null;

  const h = (tag, props = {}, ...kids) => {
    const el = document.createElement(tag);
    for (const [k, v] of Object.entries(props)) {
      if (k === 'class') el.className = v;
      else if (k.startsWith('on')) el.addEventListener(k.slice(2), v);
      else el.setAttribute(k, v);
    }
    el.append(...kids.flat().filter((x) => x != null));
    return el;
  };

  /* ---------- network ---------- */
  async function post(url, payload) {
    const res = await fetch(url, {
      method: 'POST', credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json', 'X-CSRFToken': CSRF },
      body: JSON.stringify({ payload }),
    });
    if (res.status === 403) throw new Error('Session expired — reload the page and sign in again.');
    if (res.status === 429) throw new Error('Too many scans. Wait a moment.');
    const data = await res.json().catch(() => null);
    if (!res.ok || !data) throw new Error((data && data.error) || 'Server error.');
    return data;
  }

  async function lookup(text) {
    currentPayload = text.trim();
    out.replaceChildren(h('p', { class: 'text-bone/60 text-sm' }, 'Checking…'));
    try { render(await post(URLS.lookup, currentPayload)); }
    catch (e) { renderBanner('bad', '✖ ' + e.message); }
  }

  async function confirmOrder(btn) {
    btn.disabled = true; btn.textContent = '[ Locking in… ]';
    try { render(await post(URLS.confirm, currentPayload)); }
    catch (e) { renderBanner('bad', '✖ ' + e.message); }
  }

  /* ---------- rendering ---------- */
  const TONES = {
    ok:   'border-bone bg-bone text-ink',
    warn: 'border-blood bg-blood text-ink',
    bad:  'border-blood bg-ink text-blood',
  };
  const STATE_TONE = { pending: 'ok', confirmed: 'ok', redeemed: 'warn', expired: 'warn', cancelled: 'warn', invalid: 'bad' };

  function renderBanner(tone, text, sub) {
    out.replaceChildren(banner(tone, text, sub), nextButton());
  }

  const banner = (tone, text, sub) =>
    h('div', { class: `border-2 px-4 py-3 font-bold ${TONES[tone]}`, role: 'alert' },
      h('p', { class: 'text-base' }, text), sub ? h('p', { class: 'text-xs font-normal mt-1' }, sub) : null);

  const nextButton = () =>
    h('button', { type: 'button', class: 'w-full border-2 border-ash px-3 py-2 text-xs uppercase tracking-[0.2em] hover:border-bone', onclick: reset }, '[ Scan next ]');

  function render(res) {
    out.replaceChildren();
    const o = res.order;
    let sub = null;
    if (res.state === 'redeemed' && o) sub = `Order ${o.ref} · ${o.status_label} · verified ${when(o.verified_at)}${o.verified_by ? ' by ' + o.verified_by : ''}`;
    out.append(banner(STATE_TONE[res.state] || 'bad', res.message, sub));

    if (o) out.append(manifest(o, res.state));

    if (res.state === 'pending') {
      const btn = h('button', {
        type: 'button',
        class: 'w-full bg-blood text-ink border-2 border-blood px-3 py-4 uppercase tracking-[0.25em] font-bold hover:bg-ink hover:text-blood disabled:opacity-50',
      }, '[ Confirm & Verify Order ]');
      btn.addEventListener('click', () => confirmOrder(btn));
      out.append(btn);
    }
    out.append(nextButton());
    out.scrollIntoView({ behavior: 'smooth', block: 'start' });
  }

  const row = (label, ...value) =>
    h('div', { class: 'grid grid-cols-[110px_1fr] gap-3 py-2 border-b border-ash' },
      h('dt', { class: 'text-[11px] uppercase tracking-[0.2em] text-bone/50 self-center' }, label),
      h('dd', { class: 'break-words' }, ...value));

  const link = (href, text) =>
    h('a', { href, target: '_blank', rel: 'noopener noreferrer', class: 'underline decoration-blood underline-offset-4 hover:text-blood' }, text);

  function manifest(o, state) {
    const coords = o.latitude != null && o.longitude != null
      ? [`${o.latitude}, ${o.longitude}  `, link(o.google_maps_url, '[Google Maps]'), ' ', link(o.osm_url, '[OSM]')]
      : ['No GPS pin — manual address only'];

    const items = h('ul', { class: 'space-y-1' },
      o.items.map((i) => h('li', { class: 'flex justify-between gap-3' },
        h('span', {}, `${i.quantity} × ${i.name}${i.size ? ' — SIZE ' + i.size : ''}`), h('span', { class: 'text-bone/60' }, peso.format(Number(i.unit_price) * i.quantity)))));

    const showBuyer = state !== 'invalid';
    return h('div', { class: 'border-2 border-bone bg-coal' },
      h('div', { class: 'border-b-2 border-bone px-4 py-2 text-xs uppercase tracking-[0.3em] text-blood flex justify-between' },
        h('span', {}, 'Order manifest'), h('span', {}, `REF ${o.ref}`)),
      h('dl', { class: 'px-4 py-2 text-sm' },
        row('Status', o.status_label),
        showBuyer ? row('Buyer', o.full_name) : null,
        showBuyer ? row('Phone', link(`tel:${o.phone}`, o.phone)) : null,
        showBuyer ? row('Address', o.address) : null,
        showBuyer ? row('Coordinates', ...coords) : null,
        row('Items', items),
        row('Total (COD)', h('strong', {}, peso.format(Number(o.total)))),
        row('Placed', when(o.created_at))));
  }

  /* ---------- camera / file / manual ---------- */
  function getScanner() {
    if (!scanner) {
      scanner = new Html5Qrcode('reader', { formatsToSupport: [Html5QrcodeSupportedFormats.QR_CODE], verbose: false });
    }
    return scanner;
  }

  async function startCamera() {
    if (scanning) return;
    handling = false;
    try {
      await getScanner().start({ facingMode: 'environment' }, { fps: 10, qrbox: { width: 240, height: 240 } },
        async (text) => {
          if (handling) return;
          handling = true;
          await stopCamera();
          await lookup(text);
        }, () => { /* per-frame decode misses are normal */ });
      scanning = true;
      camBtn.textContent = '[ Stop camera ]';
    } catch (e) {
      renderBanner('bad', '✖ Camera unavailable', 'Allow camera access (page must be on HTTPS) or use screenshot upload / manual paste.');
    }
  }

  async function stopCamera() {
    if (!scanning) return;
    try { await scanner.stop(); } catch { /* already stopped */ }
    scanning = false;
    camBtn.textContent = '[ Start camera ]';
  }

  camBtn.addEventListener('click', () => (scanning ? stopCamera() : startCamera()));

  // One entry point for every way an image can arrive: file picker, drag-and-drop, or paste.
  const MAX_IMAGE_BYTES = 15 * 1024 * 1024;
  async function handleImage(file) {
    if (!file) return;
    if (!/^image\//.test(file.type || '')) return renderBanner('bad', '✖ That is not an image', 'Drop a screenshot or photo of the QR code (JPG, PNG or WEBP).');
    if (file.size > MAX_IMAGE_BYTES) return renderBanner('bad', '✖ Image is too large', 'Use a screenshot under 15 MB.');
    await stopCamera();
    out.replaceChildren(h('p', { class: 'text-bone/60 text-sm' }, 'Reading QR…'));
    try { await lookup(await getScanner().scanFile(file, false)); }
    catch { renderBanner('bad', '✖ No QR code found in that image', 'Try a sharper, uncropped screenshot — or paste the code manually.'); }
  }

  $('#file-input').addEventListener('change', (e) => {
    const file = e.target.files && e.target.files[0];
    e.target.value = '';
    handleImage(file);
  });

  const zone = $('#drop-zone');
  const hasFiles = (e) => e.dataTransfer && Array.from(e.dataTransfer.types || []).includes('Files');
  const DRAG_ON = ['border-blood', 'bg-blood/10'];
  ['dragenter', 'dragover'].forEach((t) => zone.addEventListener(t, (e) => {
    if (!hasFiles(e)) return;
    e.preventDefault();
    zone.classList.add(...DRAG_ON);
  }));
  ['dragleave', 'dragend'].forEach((t) => zone.addEventListener(t, () => zone.classList.remove(...DRAG_ON)));
  zone.addEventListener('drop', (e) => {
    e.preventDefault();
    zone.classList.remove(...DRAG_ON);
    const files = e.dataTransfer && e.dataTransfer.files;
    handleImage(files && files[0]);            // first file only
  });
  // A drop that misses the box must not make the browser navigate away to the image.
  ['dragover', 'drop'].forEach((t) => window.addEventListener(t, (e) => { if (hasFiles(e)) e.preventDefault(); }));
  // Ctrl+V with an image on the clipboard (e.g. a screenshot) works anywhere except while typing in a field.
  document.addEventListener('paste', (e) => {
    const t = e.target;
    if (t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA')) return;
    const item = Array.from((e.clipboardData && e.clipboardData.items) || []).find((i) => i.kind === 'file' && i.type.startsWith('image/'));
    if (!item) return;
    e.preventDefault();
    handleImage(item.getAsFile());
  });

  $('#manual-form').addEventListener('submit', async (e) => {
    e.preventDefault();
    const v = $('#manual').value.trim();
    if (!v) return;
    await stopCamera();
    lookup(v);
  });

  function reset() {
    out.replaceChildren();
    currentPayload = null;
    $('#manual').value = '';
    startCamera();
  }
})();
