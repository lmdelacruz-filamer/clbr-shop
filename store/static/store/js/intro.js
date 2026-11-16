/* Cleanup and "skip" for the TV-on intro. The intro ends by itself through CSS even if this file never loads. */
(function () {
  var d = document.documentElement, el = document.getElementById('intro');
  if (!el || !d.classList.contains('intro')) return;
  var done = function () { d.classList.remove('intro'); if (el.parentNode) el.parentNode.removeChild(el); };
  var skipped = false;
  var skip = function () { if (skipped) return; skipped = true; el.classList.add('skip'); setTimeout(done, 250); };
  el.addEventListener('click', skip);
  addEventListener('keydown', skip, { once: true });
  addEventListener('touchstart', skip, { once: true, passive: true });
  setTimeout(done, 2000);
})();
