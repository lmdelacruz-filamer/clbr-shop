/* CLBR checkout: cart, GPS auto-locate (+ Nominatim reverse geocode), Enter-to-submit, QR render.
   Every piece of server/user text is inserted with textContent / createElement — never innerHTML. */
(() => {
  'use strict';

  const $ = (sel, root = document) => root.querySelector(sel);
  const CSRF = $('meta[name="csrf-token"]').content;
  const MAX_QTY = (window.CLBR && window.CLBR.maxQty) || 5;
  const STORE_KEY = 'clbr_last_order';
  const products = JSON.parse($('#product-data').textContent);
  const peso = new Intl.NumberFormat('en-PH', { style: 'currency', currency: 'PHP' });

  const form = $('#order-form');
  const f = { name: $('#full_name'), phone: $('#phone'), addr: $('#address'), detail: $('#address_detail'), hp: $('#website') };
  const submitBtn = $('#submit-btn');
  const geoBtn = $('#geo-btn');
  const geoStatus = $('#geo-status');
  const errBox = $('#form-error');

  const cart = new Map();                 // "<product_id>|<SIZE>" -> qty
  let geo = { lat: null, lng: null };
  let addrTouched = false;                // true once the customer types their own address
  let busy = false;

  /* ---------- tiny DOM helper ---------- */
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

  /* ---------- catalog + cart ---------- */
  // cart key = "<productId>|<SIZE>". Stock is tracked per size (set by the admin).
  const picked = new Map();               // product_id -> size currently highlighted on its card
  const qtyEls = new Map(), msgEls = new Map();
  const keyOf = (id, size) => `${id}|${size}`;
  const byId = (id) => products.find((p) => p.id === id);
  const stockOf = (p, size) => { const r = p.sizes.find((x) => x.size === size); return r ? r.stock : 0; };
  const PILL_ON = 'bg-bone text-ink border-bone', PILL_OFF = 'border-bone/40 hover:border-bone';

  function renderProducts() {
    const grid = $('#product-grid');
    grid.replaceChildren();
    if (!products.length) { $('#empty-note').classList.remove('hidden'); return; }

    for (const p of products) {
      const soldOut = p.sizes.length === 0 || p.sizes.every((r) => r.stock <= 0);
      const only = p.sizes.length === 1 ? p.sizes[0] : null;          // e.g. caps: ONE SIZE
      picked.set(p.id, only && only.stock > 0 ? only.size : '');
      const qtyEl = h('span', { class: 'w-10 text-center text-lg font-bold' }, '0');
      qtyEls.set(p.id, qtyEl);
      const msg = h('p', { class: 'text-xs text-blood min-h-[1rem]', role: 'status' });
      msgEls.set(p.id, msg);

      const pills = h('div', { class: 'flex flex-wrap gap-2', role: 'radiogroup', 'aria-label': `Size for ${p.name}` });
      p.sizes.forEach((r) => {
        const out = r.stock <= 0;
        const chosen = !out && only && only.size === r.size;
        const b = h('button', { type: 'button', role: 'radio', 'data-size': r.size, 'aria-checked': String(chosen),
          'aria-label': out ? `${r.size} sold out` : r.size,
          class: 'min-w-[3rem] h-11 px-3 border-2 text-sm font-bold uppercase focus-visible:outline focus-visible:outline-2 focus-visible:outline-blood ' +
                 (out ? 'border-ash text-bone/30 line-through cursor-not-allowed' : chosen ? PILL_ON : PILL_OFF) }, r.size);
        if (out) b.disabled = true;
        else b.addEventListener('click', () => {
          picked.set(p.id, r.size);
          pills.querySelectorAll('button:not([disabled])').forEach((x) => {
            const on = x === b;
            x.setAttribute('aria-checked', String(on));
            x.classList.remove(...PILL_ON.split(' '), ...PILL_OFF.split(' '));
            x.classList.add(...(on ? PILL_ON : PILL_OFF).split(' '));
          });
          msg.textContent = r.stock <= 5 ? `Only ${r.stock} left in ${r.size}.` : '';
          refreshQty(p.id);
        });
        pills.append(b);
      });

      const step = (delta) => {
        msg.classList.add('text-blood'); msg.classList.remove('text-bone');
        const size = picked.get(p.id);
        if (!size) { msg.textContent = 'Pick a size first.'; return; }
        const key = keyOf(p.id, size), cur = cart.get(key) || 0, limit = Math.min(stockOf(p, size), MAX_QTY);
        if (delta > 0 && cur >= limit) { msg.textContent = stockOf(p, size) <= MAX_QTY ? `Only ${stockOf(p, size)} left in ${size}.` : `Max ${MAX_QTY} per item.`; return; }
        msg.textContent = '';
        const next = cur + delta;
        next > 0 ? cart.set(key, next) : cart.delete(key);
        refreshQty(p.id); renderCart();
        if (delta > 0) added(p, size, next);
      };
      const btn = (label, delta, aria) => h('button', { type: 'button', 'aria-label': aria, ...(soldOut ? { disabled: '' } : {}),
        class: 'w-11 h-11 text-lg border-2 border-bone font-bold hover:bg-bone hover:text-ink focus-visible:outline focus-visible:outline-2 focus-visible:outline-blood disabled:opacity-30 disabled:cursor-not-allowed',
        onclick: () => step(delta) }, label);

      grid.append(h('article', { class: 'border-2 border-bone bg-coal' + (soldOut ? ' opacity-70' : '') },
        h('div', { class: 'relative aspect-square bg-ash overflow-hidden border-b-2 border-bone' },
          p.image ? h('img', { src: p.image, alt: p.name, loading: 'lazy', class: 'w-full h-full object-cover' + (soldOut ? ' grayscale' : '') }) : null,
          soldOut ? h('span', { class: 'absolute top-2 left-2 bg-blood text-ink px-2 py-1 text-xs font-bold uppercase tracking-widest' }, 'Sold out') : null),
        h('div', { class: 'p-4 space-y-3' },
          h('h3', { class: 'text-base font-bold uppercase leading-tight' }, p.name),
          h('div', { class: 'flex items-center justify-between text-base' }, h('span', {}, peso.format(Number(p.price))),
            p.sizes.length > 1 ? h('a', { href: '#sizes', class: 'text-xs uppercase tracking-widest text-bone/70 underline decoration-blood underline-offset-4 py-2' }, 'Size guide') : null),
          pills,
          h('div', { class: 'flex items-center justify-between' },
            btn('−', -1, `Remove one ${p.name}`), qtyEl, btn('+', 1, `Add one ${p.name}`)),
          msg)));
    }
  }

  // Feedback when something is added: badges pop, the quantity flashes, and a short "Added" note appears.
  const pulse = (el, cls) => { if (!el) return; el.classList.remove(cls); void el.offsetWidth; el.classList.add(cls); };
  const noteTimers = new Map();
  function added(p, size, qty) {
    pulse($('#nav-cart-count'), 'bump'); pulse($('#cart-bar-count'), 'bump'); pulse(qtyEls.get(p.id), 'flash');
    const m = msgEls.get(p.id); if (!m) return;
    m.classList.remove('text-blood'); m.classList.add('text-bone');
    m.textContent = `Added \u2713 ${size} \u00d7 ${qty} in your cart`;
    clearTimeout(noteTimers.get(p.id));
    noteTimers.set(p.id, setTimeout(() => { if (m.textContent.startsWith('Added')) { m.textContent = ''; m.classList.add('text-blood'); m.classList.remove('text-bone'); } }, 2200));
  }

  function refreshQty(id) { qtyEls.get(id).textContent = String(cart.get(keyOf(id, picked.get(id))) || 0); }

  function cartTotal() {
    let t = 0;
    for (const [k, q] of cart) t += Number(byId(Number(k.split('|')[0])).price) * q;
    return t;
  }

  function renderCart() {
    const box = $('#cart-lines');
    box.replaceChildren();
    if (!cart.size) box.append(h('p', { class: 'text-bone/50' }, 'Cart is empty — pick a piece.'));
    for (const [k, q] of cart) {
      const [idStr, size] = k.split('|'); const p = byId(Number(idStr));
      box.append(h('div', { class: 'flex justify-between gap-2' },
        h('span', { class: 'truncate' }, `${q} × ${p.name}${size ? ' (' + size + ')' : ''}`), h('span', {}, peso.format(Number(p.price) * q))));
    }
    $('#cart-total').textContent = peso.format(cartTotal());
    updateCartBar();
  }

  /* ---------- phone cart bar ---------- */
  const bar = $('#cart-bar');
  let checkoutOnScreen = false, orderDone = false;
  function updateCartBar() {
    if (!bar) return;
    const n = [...cart.values()].reduce((a, b) => a + b, 0);
    $('#cart-bar-count').textContent = String(n);
    const nb = $('#nav-cart-count'); if (nb) nb.textContent = String(n);
    $('#cart-bar-total').textContent = peso.format(cartTotal());
    const show = n > 0 && !checkoutOnScreen && !orderDone;
    bar.classList.toggle('hidden', !show);
    document.body.style.paddingBottom = show ? '5rem' : '';
  }
  if (bar && 'IntersectionObserver' in window) {
    new IntersectionObserver((es) => { checkoutOnScreen = es[0].isIntersecting; updateCartBar(); }, { threshold: 0.15 })
      .observe($('#checkout'));
  }

  /* ---------- phone / address helpers ---------- */
  const normalizedPhone = () => '+63' + f.phone.value.replace(/\D/g, '').replace(/^(63|0)/, '');
  const updateCount = () => { $('#addr-count').textContent = `${f.addr.value.length}/155`; };
  f.addr.addEventListener('input', () => { addrTouched = true; updateCount(); });

  /* ---------- GPS + reverse geocode ---------- */
  const setGeo = (msg, tone) => {
    geoStatus.textContent = msg;
    geoStatus.className = 'mt-2 text-xs min-h-[1.25rem] ' + (tone === 'err' ? 'text-blood' : tone === 'ok' ? 'text-bone' : 'text-bone/60');
  };

  function locate() {
    if (!('geolocation' in navigator)) return setGeo('GPS is not supported on this device. Please type your barangay, city and province.', 'err');
    if (!window.isSecureContext) return setGeo('GPS needs a secure (HTTPS) page. Please type your barangay, city and province.', 'err');
    geoBtn.disabled = true;
    setGeo('Locating… allow location access if your browser asks.');
    navigator.geolocation.getCurrentPosition(onPosition, onGeoError,
      { enableHighAccuracy: true, timeout: 15000, maximumAge: 0 });
  }

  function onGeoError(err) {
    geoBtn.disabled = false;
    const msg = {
      1: 'Location permission denied. You can type your address instead.',
      2: 'Could not determine your position. Please type your address.',
      3: 'Locating timed out. Try again or type your address.',
    }[err.code] || 'Could not get your location. Please type your address.';
    setGeo(msg, 'err');
  }

  // "Puente Acuña, Cagay, Punta Tabuc, Roxas City, Capiz, Western Visayas, 5800, Philippines" -> drop the country,
  // and if it is still too long drop trailing parts (the most specific part is at the front).
  function areaFrom(displayName) {
    let parts = String(displayName).split(',').map((x) => x.trim()).filter(Boolean);
    if (parts.length && /^philippines$/i.test(parts[parts.length - 1])) parts.pop();
    while (parts.length > 1 && parts.join(', ').length > 155) parts.pop();
    return parts.join(', ').slice(0, 155);
  }

  async function onPosition(pos) {
    const { latitude, longitude, accuracy } = pos.coords;
    geo = { lat: Number(latitude.toFixed(6)), lng: Number(longitude.toFixed(6)) };
    setGeo(`PIN LOCKED ${geo.lat}, ${geo.lng} (±${Math.round(accuracy)} m). Resolving address…`);

    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), 8000);
    try {
      const url = new URL('https://nominatim.openstreetmap.org/reverse');
      url.search = new URLSearchParams({
        format: 'jsonv2', lat: String(geo.lat), lon: String(geo.lng), zoom: '18', addressdetails: '1', 'accept-language': 'en',
      });
      const res = await fetch(url, { signal: ctrl.signal, referrerPolicy: 'strict-origin-when-cross-origin' });
      if (!res.ok) throw new Error('HTTP ' + res.status);
      const data = await res.json();
      if (!data || !data.display_name) throw new Error('no address');

      // Philippines only: reject pins that resolve to another country.
      const cc = data.address && data.address.country_code;
      if (cc && cc.toLowerCase() !== 'ph') {
        geo = { lat: null, lng: null };
        setGeo('That location is outside the Philippines. We deliver within the Philippines only.', 'err');
        return;
      }

      if (!f.addr.value.trim() || !addrTouched) {
        f.addr.value = areaFrom(data.display_name);
        addrTouched = false;
        updateCount();
        setGeo('Area filled from GPS. Now add your house no. / street / landmark below. GPS cannot read that part.', 'ok');
      } else {
        setGeo('GPS pin saved. We kept the area you typed. Add your house no. / landmark below.', 'ok');
      }
      if (!f.detail.value.trim()) f.detail.focus();
    } catch {
      setGeo('GPS pin saved, but we could not look up the area. Type your barangay, city and province, then add your house no. / landmark.', 'err');
    } finally {
      clearTimeout(timer);
      geoBtn.disabled = false;
    }
  }
  geoBtn.addEventListener('click', locate);

  /* ---------- submit (button + Enter key) ---------- */
  const showError = (msg) => { errBox.textContent = msg; errBox.classList.remove('hidden'); };
  const clearError = () => { errBox.textContent = ''; errBox.classList.add('hidden'); };

  async function submitOrder() {
    if (busy) return;
    clearError();
    if (!cart.size) return showError('Add at least one item to your cart first.');
    if (!f.name.value.trim() || !f.phone.value.trim())
      return showError('Name and phone number are required.');
    if (f.addr.value.trim().length < 10) { f.addr.focus(); return showError('Add your barangay, city and province (tap Auto-Locate GPS or type it).'); }
    if (f.detail.value.trim().length < 5) { f.detail.focus(); return showError('Add your house no., street or a landmark so the rider can find you.'); }
    if (!$('#consent').checked) return showError('Please tick the box to accept the Terms and Privacy Notice.');

    busy = true;
    submitBtn.disabled = true;
    submitBtn.textContent = '[ Transmitting… ]';
    try {
      const res = await fetch(form.dataset.url, {
        method: 'POST',
        credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json', 'X-CSRFToken': CSRF },
        body: JSON.stringify({
          full_name: f.name.value.trim(),
          phone: normalizedPhone(),
          address_detail: f.detail.value.trim(),
          address: f.addr.value.trim(),
          latitude: geo.lat,
          longitude: geo.lng,
          website: f.hp.value,
          consent: $('#consent').checked,
          items: [...cart].map(([k, quantity]) => { const [id, size] = k.split('|'); return { product_id: Number(id), size, quantity }; }),
        }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        const detail = data.errors ? Object.values(data.errors).flat().join('\n') : '';
        return showError([data.error || 'Something went wrong. Please try again.', detail].filter(Boolean).join('\n'));
      }
      try { localStorage.setItem(STORE_KEY, JSON.stringify(data)); } catch { /* storage unavailable */ }
      showSuccess(data);
    } catch {
      showError('Network error. Check your connection and try again.');
    } finally {
      busy = false;
      submitBtn.disabled = false;
      submitBtn.textContent = '[ Generate Order QR ]';
    }
  }

  form.addEventListener('submit', (e) => { e.preventDefault(); submitOrder(); });

  // Fixing a field clears the old error message instead of leaving it on screen.
  form.addEventListener('input', clearError);
  form.addEventListener('change', clearError);

  // Enter submits from any field. In the address box, Shift+Enter keeps a newline.
  form.querySelectorAll('input, textarea').forEach((el) => {
    el.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) {
        e.preventDefault();
        submitOrder();
      }
    });
  });

  /* ---------- QR ---------- */
  function showSuccess(d) {
    orderDone = true; updateCartBar();
    form.classList.add('hidden');
    $('#cart-lines').classList.add('hidden');
    $('#success').classList.remove('hidden');
    $('#ref').textContent = d.short_ref;
    $('#done-total').textContent = peso.format(Number(d.total));
    const until = new Date(d.expires_at).toLocaleString('en-PH', { dateStyle: 'medium', timeStyle: 'short' });
    $('#hold-note').textContent = `Your items are held until ${until}. Unverified orders are released after that.`;

    const box = $('#qr');
    box.replaceChildren();
    // Payload = CLBR1:<order-id>:<secret token>. Personal details stay on the server.
    new QRCode(box, { text: d.payload, width: 256, height: 256, colorDark: '#000000', colorLight: '#ffffff', correctLevel: QRCode.CorrectLevel.M });
  }

  function downloadQR() {
    const src = $('#qr canvas');
    if (!src) return;
    const pad = 32, out = document.createElement('canvas');   // add a quiet zone so it scans reliably
    out.width = src.width + pad * 2; out.height = src.height + pad * 2;
    const ctx = out.getContext('2d');
    ctx.fillStyle = '#fff'; ctx.fillRect(0, 0, out.width, out.height);
    ctx.drawImage(src, pad, pad);
    const a = document.createElement('a');
    a.download = `clbr-order-${$('#ref').textContent}.png`;
    a.href = out.toDataURL('image/png');
    a.click();
  }
  $('#dl-btn').addEventListener('click', downloadQR);

  $('#new-btn').addEventListener('click', () => {
    try { localStorage.removeItem(STORE_KEY); } catch { /* ignore */ }
    location.reload();
  });

  /* ---------- boot ---------- */
  renderProducts();
  renderCart();
  updateCount();
  try {                                    // survive an accidental refresh while the hold is still valid
    const saved = JSON.parse(localStorage.getItem(STORE_KEY) || 'null');
    if (saved && saved.payload && Date.parse(saved.expires_at) > Date.now()) showSuccess(saved);
  } catch { /* ignore */ }
})();
