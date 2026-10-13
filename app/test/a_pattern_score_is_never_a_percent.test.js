'use strict';
/**
 * A chart pattern's figure is the detector's score on every page, never a
 * percent.
 *
 * Each detector in bot/core/chart_patterns.py writes a hand-set constant or a
 * clamp, and nothing records how a pattern's calls turned out. The pages
 * printed it as `72%`: the dashboard's deep-scan cards, the Arena's read chips
 * and the front door's daily digest. They word it with
 * `RCConfidenceScore.patternText` now (`detector score 72 (unmeasured)`, the
 * bot's own words, pinned in a_confidence_is_a_score_on_every_page.test.js),
 * and an unread figure is left out, never printed as 0.
 *
 * Each surface is lifted out of its page and run: the card is a function
 * inside the dashboard's IIFE, the chips a function in arena.html's inline
 * script, the digest an IIFE in index.html that fetches /api/today.
 */
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const CS = require('../public/js/confidence-score');
const { blockBetween } = require('./helpers/block');

const PUB = path.join(__dirname, '..', 'public');
const read = (f) => fs.readFileSync(path.join(PUB, f), 'utf8');
const esc = (s) => String(s == null ? '' : s)
  .replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]);

const PATS = [
  { name: 'Double Top', signal: 'bearish', confidence: 0.71, description: 'two peaks' },
  { name: 'No Score', signal: 'bullish', description: 'nothing read' },
];
const SAID = 'detector score 71 (unmeasured)';
const PERCENT = /71\s*%/;
/** What a reader sees: the markup's text, not its attributes (a bar's CSS
 *  `width:71%` is a length, not a claim). */
const visible = (html) => String(html).replace(/<[^>]*>/g, ' ');

test('the module words a pattern\'s figure as the detector\'s score', () => {
  assert.equal(CS.patternText(0.71), SAID);
  assert.equal(CS.patternText(0), 'detector score 0 (unmeasured)', 'a read zero is a reading');
  for (const v of [null, undefined, '', 'x', true, NaN]) assert.equal(CS.patternText(v), null, String(v));
});

// ── the dashboard's deep-scan card ──────────────────────────────────────────

test('the deep-scan card prints the score, a legend, and a dash for an unread one', () => {
  const END = '${mini}\n    </div>`;\n  }';
  const block = blockBetween(read('js/dashboard.js'), 'function deepScanCard(h) {',
    END, { pad: END.length, label: 'deepScanCard' });
  const ctx = vm.createContext({
    self: { RCConfidenceScore: CS }, esc, fmt: (v) => String(v), fmtPrice: (v) => String(v),
    _dsSigCol: () => 'var(--x)', _dsArrow: () => '', dsBase: (s) => String(s).replace(/\/?USDT.*/, ''),
  });
  vm.runInContext(`${block}\nthis.deepScanCard = deepScanCard;`, ctx);
  const html = ctx.deepScanCard({ symbol: 'ETH/USDT', price: 1582.5, chg: -0.04, rsi: 51,
    chart_patterns: PATS, candle_patterns: {} });

  assert.ok(!PERCENT.test(visible(html)), visible(html));
  assert.match(html, /title="detector score 71 \(unmeasured\)">71</);
  assert.match(html, /style="width:71%;/, 'the bar is drawn at the score');
  assert.match(html, />—</, 'an unread score is a dash');
  assert.match(html, /style="width:0%;/, 'and no bar');
  assert.ok(html.includes('detector score 0-100, unmeasured'), 'the legend names the number once');
});

// ── the Arena's read chips ──────────────────────────────────────────────────

test('the Arena\'s read chips print the score and leave an unread one out', () => {
  const END = "{ tf: gran || '1h' })) + '</div>';\n  }";
  const block = blockBetween(read('arena.html'), 'function readChips(p, parsed, pat, gran, readAt) {',
    END, { pad: END.length, label: 'readChips' });
  const ctx = vm.createContext({
    self: { RCConfidenceScore: CS }, window: {}, esc,
    T: (k, d) => d, fill: (s, o) => String(s).replace('{tf}', o.tf),
  });
  vm.runInContext(`${block}\nthis.readChips = readChips;`, ctx);
  const html = ctx.readChips({}, [], { chart_patterns: PATS }, '1h', Date.now());

  assert.ok(!PERCENT.test(visible(html)), visible(html));
  assert.ok(html.includes(`Double Top · ${SAID}</span>`), html);
  assert.ok(html.includes('No Score</span>'), 'an unread score is left out, not printed as 0');
});

// ── the front door's daily digest ───────────────────────────────────────────

async function digest(today) {
  // Up to (not including) the chain's own catch, which this re-attaches.
  const block = blockBetween(read('index.html'), "fetch('/api/today'",
    '.catch(function () { /* digest is decoration on the band */ });', { pad: 0, label: 'today strip' });
  const el = { hidden: true, textContent: '', innerHTML: '' };
  let chain;
  const ctx = vm.createContext({
    self: { RCConfidenceScore: CS },
    document: { getElementById: (id) => (id === 'todayStrip' ? el : null) },
    fetch: () => {
      chain = Promise.resolve({ json: () => today });
      return chain;
    },
  });
  // The lifted block is the fetch chain itself; run it and let it settle.
  vm.runInContext(`this.p = ${block}\n  .catch(function () {});`, ctx);
  await ctx.p;
  return el;
}

test('the daily digest names the pattern\'s score and draws its icons as icons', async () => {
  const el = await digest({ top_pattern: { symbol: 'SOL/USDT', name: 'Double Top', confidence: 0.71 },
    signals: { created_today: 3, resolved_today: 2, wins_today: 1 } });
  assert.equal(el.hidden, false);
  assert.ok(el.innerHTML.includes(`SOL · Double Top (${SAID})`), el.innerHTML);
  assert.ok(!PERCENT.test(visible(el.innerHTML)), el.innerHTML);
  // The strip carries svg icons; written as text they printed as markup.
  assert.ok(el.innerHTML.includes('<svg class="icon"'), el.innerHTML);
  assert.equal(el.textContent, '', 'the markup is not written as text');
});

test('the digest leaves an unread score out and escapes what it was sent', async () => {
  // Every value is the payload's, counts included: written as markup now,
  // an unescaped one would be markup too.
  const el = await digest({ top_pattern: { symbol: 'X<b>', name: 'No <i>Score</i>' },
    signals: { created_today: '3<i>', resolved_today: '2<b>', wins_today: '1<i>' },
    arena: { traders: '9<b>', closes_24h: '4<i>' } });
  assert.ok(el.innerHTML.includes('X&lt;b&gt; · No &lt;i&gt;Score&lt;/i&gt;'), el.innerHTML);
  assert.ok(el.innerHTML.includes('3&lt;i&gt; signals today, 1&lt;i&gt;/2&lt;b&gt; wins'), el.innerHTML);
  assert.ok(el.innerHTML.includes('9&lt;b&gt; traders · 4&lt;i&gt; closes'), el.innerHTML);
  assert.ok(!el.innerHTML.includes('(detector score'), 'no score to name');
  assert.ok(!/<b>|<i>/.test(el.innerHTML), el.innerHTML);
});
