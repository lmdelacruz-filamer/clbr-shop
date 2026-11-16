/* CLBR Radio page. Nothing from SoundCloud is requested until the visitor picks a mix. */
(() => {
  const root = document.querySelector('[data-radio]');
  const dataEl = document.getElementById('rd-data');
  if (!root || !dataEl) return;
  const mixes = JSON.parse(dataEl.textContent);
  const byId = new Map(mixes.map((m) => [String(m.id), m]));
  const reduce = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  const $ = (k) => root.querySelector('[data-rd="' + k + '"]');
  const all = (k) => root.querySelectorAll('[data-rd="' + k + '"]');
  const fs = document.getElementById('rd-fs');
  const card = $('card'), empty = $('empty'), slot = $('slot'), listBox = $('tracklist'), list = $('list');

  // The fullscreen view reuses the card's four scenes.
  const screen0 = card.querySelector('[data-rd="screen"]');
  const screenFs = fs.querySelector('[data-rd="screen"]');
  [...screen0.children].forEach((n) => { if (!n.classList.contains('rd-fsbtn') && !n.classList.contains('rd-tag')) screenFs.appendChild(n.cloneNode(true)); });
  root.querySelectorAll('.rd-s4 i').forEach((b, i) => { b.style.animationDuration = (0.6 + ((i * 7) % 5) * 0.11).toFixed(2) + 's'; });

  let mix = null, widget = null, loading = false, playing = false, pos = 0, dur = 0, cur = -1, scene = 0;
  let sceneTimer = 0, readyTimer = 0, hintTimer = 0, dragging = false, gen = 0;
  const mmss = (s) => { s = Math.max(0, Math.floor(s)); const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), x = s % 60; return (h ? h + ':' + String(m).padStart(2, '0') : m) + ':' + String(x).padStart(2, '0'); };
  const setText = (k, v) => all(k).forEach((e) => { e.textContent = v; });
  const msg = (t) => setText('msg', t || '');
  const seekEls = () => all('seek');

  function burst() {
    if (reduce) return;
    root.querySelectorAll('.rd-snow').forEach((s) => { s.classList.remove('burst'); void s.offsetWidth; s.classList.add('burst'); });
  }
  function setScene(n, withBurst) {
    scene = n;
    all('screen').forEach((sc) => [...sc.querySelectorAll('.rd-scene')].forEach((e, i) => e.classList.toggle('act', i === n)));
    if (withBurst) burst();
  }
  function startScenes() {
    if (reduce || sceneTimer) return;
    sceneTimer = setInterval(() => { let n; do { n = Math.floor(Math.random() * 4); } while (n === scene); setScene(n, true); }, 5200);
  }
  function stopScenes() { clearInterval(sceneTimer); sceneTimer = 0; }

  function pickState(label) {
    root.querySelectorAll('.rd-mix').forEach((li) => {
      const on = mix && li.dataset.id === String(mix.id);
      li.classList.toggle('cur', !!on);
      li.querySelector('[data-rd="pickstate"]').textContent = on ? label : 'PLAY';
    });
  }
  function setPlaying(on) {
    playing = on;
    root.classList.toggle('on', on);
    all('play').forEach((b) => b.setAttribute('aria-label', on ? 'Pause CLBR Radio' : 'Play CLBR Radio'));
    setText('state', on ? 'ON AIR' : (widget && !loading ? 'PAUSED' : 'OFF AIR'));
    pickState(on ? 'PLAYING' : (loading ? 'LOADING' : 'PAUSED'));
    if (on) startScenes(); else stopScenes();
  }
  function enableControls(on) {
    all('back').forEach((b) => { b.disabled = !on; });
    all('fwd').forEach((b) => { b.disabled = !on; });
    seekEls().forEach((s) => { s.disabled = !on; });
  }
  function drawSeek() {
    const pct = dur ? Math.min(100, (pos / dur) * 100) : 0;
    seekEls().forEach((s) => {
      if (!dragging) s.value = String(Math.floor(pos));
      s.style.setProperty('--p', pct + '%');
      s.setAttribute('aria-valuetext', mmss(pos) + ' of ' + (dur ? mmss(dur) : 'unknown'));
    });
  }
  function showTrack() {
    const tr = mix.tracks;
    let k = -1;
    tr.forEach((t, i) => { if (pos >= t.start) k = i; });
    if (k === cur) return;
    cur = k;
    list.querySelectorAll('li').forEach((li, i) => li.classList.toggle('cur', i === k));
    if (k >= 0) { setText('title', tr[k].title); setText('artist', tr[k].artist); }
    else { setText('title', mix.title); setText('artist', ''); }
    if (playing) { burst(); if (!reduce) setScene(Math.floor(Math.random() * 4), false); }
  }
  function progress(sec) {
    pos = sec;
    setText('pos', mmss(pos)); setText('pos2', mmss(pos));
    drawSeek();
    showTrack();
  }

  function teardown() {
    gen++;
    clearTimeout(readyTimer); clearTimeout(hintTimer); stopScenes();
    if (widget) { try { widget.pause(); } catch (e) { /* the frame is going away anyway */ } }
    slot.textContent = '';
    widget = null; loading = false; playing = false; pos = 0; dur = 0; cur = -1; dragging = false;
    root.classList.remove('on');
    enableControls(false);
    setText('pos', '0:00'); setText('pos2', '0:00'); setText('dur', '--:--');
    seekEls().forEach((s) => { s.max = '100'; s.value = '0'; s.style.setProperty('--p', '0%'); });
    msg('');
  }
  function fail() {
    loading = false; clearTimeout(readyTimer);
    setText('state', 'OFF AIR'); pickState('PLAY');
    msg('Could not load SoundCloud. Use the Listen on SoundCloud link.');
  }
  function loadApi(done) {
    if (window.SC && window.SC.Widget) return done();
    const s = document.createElement('script');
    s.src = 'https://w.soundcloud.com/player/api.js';
    s.async = true; s.onload = done; s.onerror = fail;
    document.head.appendChild(s);
  }
  function boot() {
    const my = ++gen;
    loading = true; msg('Tuning in…'); setText('state', 'TUNING IN'); pickState('LOADING');
    loadApi(() => {
      if (my !== gen) return;
      const f = document.createElement('iframe');
      f.title = 'SoundCloud player (audio only, controlled by the CLBR player)'; f.allow = 'autoplay; encrypted-media';   // SoundCloud's player probes encrypted-media; without it Chrome logs a policy warning
      f.src = 'https://w.soundcloud.com/player/?url=' + encodeURIComponent(mix.url) +
        '&auto_play=true&visual=false&hide_related=true&show_comments=false&show_reposts=false&show_teaser=false&color=%23e5322d';
      slot.appendChild(f);
      const w = window.SC.Widget(f);
      widget = w;
      const E = window.SC.Widget.Events;
      readyTimer = setTimeout(() => { if (my === gen) fail(); }, 12000);
      w.bind(E.READY, () => {
        if (my !== gen) return;
        clearTimeout(readyTimer); loading = false; msg('');
        enableControls(true);
        w.getDuration((d) => { if (my !== gen) return; dur = d / 1000; setText('dur', mmss(dur)); seekEls().forEach((s) => { s.max = String(Math.floor(dur)); }); drawSeek(); });
        w.getCurrentSound((s) => { if (my === gen && s && !mix.tracks.length) setText('artist', (s.user && s.user.username) || ''); });
        w.play();
        // Some phones block play() that was not started by a tap. Tell the visitor instead of staying silent.
        hintTimer = setTimeout(() => { if (my === gen && !playing) { msg('Tap play to start the sound.'); setText('state', 'PAUSED'); pickState('PAUSED'); } }, 2500);
      });
      w.bind(E.PLAY, () => { if (my !== gen) return; msg(''); setPlaying(true); burst(); });
      w.bind(E.PAUSE, () => { if (my === gen) setPlaying(false); });
      w.bind(E.FINISH, () => { if (my === gen) { setPlaying(false); progress(0); } });
      w.bind(E.PLAY_PROGRESS, (d) => { if (my === gen && !dragging) progress(d.currentPosition / 1000); });
      w.bind(E.ERROR, () => { if (my === gen) fail(); });
    });
  }

  function renderTracklist() {
    list.textContent = '';
    mix.tracks.forEach((t) => {
      const li = document.createElement('li');
      const a = document.createElement('span'); a.className = 'rd-ts'; a.textContent = t.label;
      const b = document.createElement('span'); b.textContent = t.artist + ' — ' + t.title;
      li.append(a, b); list.appendChild(li);
    });
    listBox.hidden = !mix.tracks.length;
  }
  function pick(id) {
    const m = byId.get(String(id));
    if (!m) return;
    if (mix && mix.id === m.id && widget) { toggle(); return; }
    teardown();
    mix = m;
    setText('title', m.title); setText('artist', ''); setText('dj', 'Mix by ' + m.dj + ' · ' + m.title);
    $('sclink').href = m.url;
    renderTracklist();
    empty.hidden = true; card.hidden = false;
    setScene(0, false);
    boot();
    // Bring the whole player (its time bar and buttons are at the bottom of the card) into view on phones.
    const top = card.getBoundingClientRect().top;
    if (top < 0 || top > 120) card.scrollIntoView({ behavior: reduce ? 'auto' : 'smooth', block: 'start' });
  }
  function toggle() { if (widget && !loading) widget.toggle(); }
  function seekBy(delta) {
    if (!widget) return;
    widget.getPosition((p) => widget.seekTo(Math.max(0, Math.min((dur || 1e9) * 1000, p + delta * 1000))));
  }

  root.querySelectorAll('[data-rd="pick"]').forEach((b) => b.addEventListener('click', () => pick(b.dataset.id)));
  all('play').forEach((b) => b.addEventListener('click', toggle));
  all('back').forEach((b) => b.addEventListener('click', () => seekBy(-15)));
  all('fwd').forEach((b) => b.addEventListener('click', () => seekBy(15)));
  seekEls().forEach((s) => {
    s.addEventListener('input', () => {
      dragging = true; pos = +s.value;
      setText('pos', mmss(pos)); setText('pos2', mmss(pos));
      seekEls().forEach((o) => { if (o !== s) o.value = s.value; });
      drawSeek();
    });
    s.addEventListener('change', () => {
      if (widget) widget.seekTo(+s.value * 1000);
      dragging = false; showTrack();
    });
  });

  // Fullscreen: the browser's own fullscreen where it exists (desktop, Android), otherwise an overlay that fills the page.
  let opener = null;
  function openFs() {
    opener = document.activeElement;
    fs.hidden = false; document.body.classList.add('rd-lock');
    const req = fs.requestFullscreen || fs.webkitRequestFullscreen;
    if (req) { try { const p = req.call(fs); if (p && p.catch) p.catch(() => {}); } catch (e) { /* overlay still works */ } }
    fs.querySelector('[data-rd="fs-close"]').focus();
  }
  function closeFs() {
    if (fs.hidden) return;
    fs.hidden = true; document.body.classList.remove('rd-lock');
    if (document.fullscreenElement && document.exitFullscreen) { try { document.exitFullscreen(); } catch (e) { /* ignore */ } }
    if (opener && opener.focus) opener.focus();
  }
  card.querySelector('[data-rd="fs-open"]').addEventListener('click', openFs);
  fs.querySelector('[data-rd="fs-close"]').addEventListener('click', closeFs);
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape') closeFs(); });
  document.addEventListener('fullscreenchange', () => { if (!document.fullscreenElement) closeFs(); });
})();
