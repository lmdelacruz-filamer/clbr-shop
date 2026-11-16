/* Gentle fade-up for the sections below the fold. Skipped entirely when the visitor prefers reduced motion,
   and everything is shown after a few seconds even if something goes wrong, so content can never stay hidden. */
(function () {
  if (!('IntersectionObserver' in window) || window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;
  var els = [].slice.call(document.querySelectorAll('[data-reveal]')).filter(function (el) {
    return el.getBoundingClientRect().top > window.innerHeight * 0.9;      // never hide what is already on screen
  });
  if (!els.length) return;
  var show = function (el) {
    el.classList.add('in');
    setTimeout(function () { el.classList.remove('rv', 'in'); }, 700);       // drop the transform afterwards
  };
  var io = new IntersectionObserver(function (entries) {
    entries.forEach(function (e) { if (e.isIntersecting) { io.unobserve(e.target); show(e.target); } });
  }, { threshold: 0.08, rootMargin: '0px 0px -6% 0px' });
  els.forEach(function (el) { el.classList.add('rv'); io.observe(el); });
  setTimeout(function () { els.forEach(function (el) { if (el.classList.contains('rv') && !el.classList.contains('in')) { io.unobserve(el); show(el); } }); }, 6000);
  window.addEventListener('beforeprint', function () { els.forEach(function (el) { el.classList.remove('rv'); }); });
})();
