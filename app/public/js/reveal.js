/**
 * RUNECLAW shared scroll-reveal — the same .reveal-on-scroll / .rc-stagger
 * treatment the landing page uses, as a standalone drop-in for every other
 * page (styles.css already carries the CSS site-wide; only the observer was
 * missing). Reduced-motion or no-IntersectionObserver reveals everything
 * immediately, so content is never stranded invisible. A MutationObserver
 * re-runs the sweep whenever the page inserts new content, so panels built
 * after a fetch (verdicts, live boards) play the same reveal on arrival.
 */
(function () {
  'use strict';
  var reduce = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  var io = null;

  function run() {
    var els = document.querySelectorAll('.reveal-on-scroll:not(.rc-inview)');
    if (!els.length) return;
    if (reduce || !('IntersectionObserver' in window)) {
      els.forEach(function (el) { el.classList.add('rc-inview'); });
      return;
    }
    if (!io) {
      io = new IntersectionObserver(function (entries, o) {
        entries.forEach(function (e) {
          if (e.isIntersecting) { e.target.classList.add('rc-inview'); o.unobserve(e.target); }
        });
      }, { rootMargin: '0px 0px -8% 0px', threshold: 0.08 });
    }
    els.forEach(function (el) {
      if (el.dataset.rcReveal) return;
      el.dataset.rcReveal = '1';
      io.observe(el);
    });
  }

  function boot() {
    run();
    if ('MutationObserver' in window) {
      new MutationObserver(function () { run(); })
        .observe(document.body, { childList: true, subtree: true });
    }
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot);
  else boot();
})();
