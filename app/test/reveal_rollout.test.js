'use strict';
/**
 * Site-wide scroll-reveal rollout. styles.css has carried the
 * .reveal-on-scroll / .rc-stagger treatment site-wide from day one, but only
 * pages loading app.js could activate it — every other page stayed flat.
 * A standalone observer (public/js/reveal.js) now brings the Guardian suite
 * and the proof pages into the same motion language.
 * Pins:
 * - the helper reveals everything immediately under reduced motion or a
 *   missing IntersectionObserver (content is never stranded invisible);
 * - each rolled-out page loads the helper AND marks sections, together —
 *   classes without the script would strand content at opacity 0;
 * - the stylesheet's own reduced-motion escape hatch stays in place.
 */
const test = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const path = require('path');

const pub = (...p) => fs.readFileSync(path.join(__dirname, '..', 'public', ...p), 'utf8');
const reveal = pub('js', 'reveal.js');

const PAGES = ['escape.html', 'firewall.html', 'intent.html', 'stress.html',
  'sentinel.html', 'proof.html', 'developers.html', 'provable.html'];

test('reveal.js never strands content invisible', () => {
  // reduced motion or no observer → reveal everything at once
  assert.match(reveal, /prefers-reduced-motion: reduce/);
  assert.match(reveal, /'IntersectionObserver' in window/);
  const fallback = reveal.indexOf("el.classList.add('rc-inview')");
  const observe = reveal.indexOf('io.observe(el)');
  assert.ok(fallback > 0 && observe > 0, 'both paths exist');
  assert.ok(fallback < observe, 'the immediate-reveal fallback is checked first');
  // each element reveals once and is released
  assert.match(reveal, /o\.unobserve\(e\.target\)/);
  // works whether the script runs before or after DOM ready
  assert.match(reveal, /document\.readyState === 'loading'/);
});

test('reveal.js catches content inserted after load', () => {
  // sentinel and the firewall verdict build their panels from fetch results;
  // without a re-run on insertion those panels would strand at opacity 0.
  assert.match(reveal, /new MutationObserver/);
  assert.match(reveal, /childList: true, subtree: true/);
  // and a re-run never double-observes an element already in the queue
  assert.match(reveal, /el\.dataset\.rcReveal/);
});

test('every rolled-out page ships classes and script together', () => {
  for (const page of PAGES) {
    const html = pub(page);
    const marks = (html.match(/reveal-on-scroll/g) || []).length;
    assert.ok(marks >= 2, `${page} marks at least two sections (has ${marks})`);
    assert.ok(html.includes('/js/reveal.js?v='),
      `${page} loads reveal.js — classes without the observer strand content`);
  }
});

test('any page marking reveal-on-scroll loads an observer for it', () => {
  // the inverse guard: nobody can add the class to a page without wiring
  // either reveal.js or app.js (which carries its own revealOnScroll).
  for (const f of fs.readdirSync(path.join(__dirname, '..', 'public'))) {
    if (!f.endsWith('.html')) continue;
    const html = pub(f);
    if (!html.includes('reveal-on-scroll')) continue;
    assert.ok(html.includes('/js/reveal.js') || html.includes('/js/app.js'),
      `${f} uses reveal-on-scroll but loads neither reveal.js nor app.js`);
  }
});

test('the stylesheet keeps its reduced-motion escape hatch', () => {
  const css = pub('styles.css');
  const block = css.slice(css.indexOf('.reveal-on-scroll'));
  assert.match(block,
    /@media \(prefers-reduced-motion: reduce\) \{ \.reveal-on-scroll \{ opacity: 1; transform: none; \} \}/);
});
