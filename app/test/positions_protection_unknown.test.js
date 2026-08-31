'use strict';
/**
 * A position whose stop nobody could read must not render as a safe one.
 *
 * RC-2026-016. `/api/positions` built its protection fields from
 *
 *     sl = float(getattr(pos, "stop_loss", 0) or 0)
 *     unprotected = (not sl_protected and sl > 0) or ...
 *
 * so an unread stop -- absent field, silent venue, an adoption whose
 * order-book read raised -- became `unprotected: false`, `sl_dist_pct: 0.0`,
 * `sl_order: "manual"`. This renderer then chipped it **🤖 bot-managed** and,
 * worse, the banner above it said:
 *
 *     🛡️ All N live positions have their stop-loss on the exchange.
 *
 * a categorical all-clear whose condition is only `unprotected_count === 0`.
 *
 * The gateway is three-valued now (`unprotected: null`, `sl_unknown: true`,
 * `unknown_count`). This file exists because a payload fix the UI ignores is
 * the #999 defect -- code present, never reached, indistinguishable from
 * working code to any source scan.
 *
 * It slices the real `slPositionsHtml` out of dashboard.js and runs it, rather
 * than matching its source: the sibling test asserts
 * `/p\.sl_order === 'exchange'/` is present, which passes for a renderer that
 * is right and for one that is wrong in the way that matters here.
 */
const test = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const SRC = fs.readFileSync(
  path.join(__dirname, '..', 'public', 'js', 'dashboard.js'), 'utf8');

function slice(name) {
  const start = SRC.indexOf(`function ${name}(`);
  assert.notStrictEqual(start, -1, `${name} not found in dashboard.js`);
  let depth = 0, i = SRC.indexOf('{', start);
  for (let j = i; j < SRC.length; j++) {
    if (SRC[j] === '{') depth++;
    else if (SRC[j] === '}' && --depth === 0) return SRC.slice(start, j + 1);
  }
  throw new Error(`unbalanced braces slicing ${name}`);
}

// dashboard.js destructures its helpers from `window.RC` (dashboard.js:12-14).
// Stub every one of them rather than discovering them by successive
// ReferenceError: a harness that only just runs is one edit away from failing
// for a reason unrelated to the rule under test.
const HELPERS = ['LOGGED_IN', 'fetchJSON', 'fmtK', 'signed', 'pnlClass',
  'fmtAgo', 'sanitizeBotHtml', 'toast', 'renderPanel', 'stateBlock',
  'mustRead', 'connectStream'];
const ctx = {
  esc: (v) => String(v == null ? '' : v),
  T: (_k, fallback) => fallback,
  fmt: (v) => String(v),
  fmtMoney: (v) => (v == null ? '--' : `$${v}`),
  fmtPrice: (v) => (v == null ? '--' : String(v)),
  // Direction is knowable even when the stop is not, so it is deliberately
  // outside every assertion below -- this repo learned from RC-2026-018 that a
  // colour test forbidding all green also removes a true statement.
  dirChip: (d) => `<span class="chip">${String(d || '')}</span>`,
  module: {}, exports: {},
};
for (const h of HELPERS) ctx[h] = () => '';
vm.createContext(ctx);
vm.runInContext(slice('slPositionsHtml') + '\nthis.render = slPositionsHtml;', ctx);
const render = ctx.render;

const POS = (over) => Object.assign({
  symbol: 'BTC/USDT:USDT', pair: 'BTC', direction: 'long',
  entry_price: 100, stop_loss: 95, take_profit: 110,
  sl_dist_pct: 5, leverage: 3,
  sl_order: 'exchange', sl_protected: true, unprotected: false, sl_unknown: false,
}, over || {});

// The row the whole finding is about: nobody read this position's stop.
const UNREAD = POS({
  stop_loss: null, sl_dist_pct: null,
  sl_order: 'unknown', sl_protected: false, unprotected: null, sl_unknown: true,
});

test('an unread stop is not chipped as bot-managed', () => {
  const html = render({
    live: true, positions: [UNREAD],
    protected_count: 0, unprotected_count: 0, unknown_count: 1,
  });
  assert.ok(!/bot-managed/.test(html),
    'a position whose stop could not be read renders as "bot-managed", which ' +
    'tells the operator the bot is holding a stop it never saw');
  assert.match(html, /unknown/i,
    'the row says nothing about the stop being unreadable');
});

test('the all-clear banner does not fire while a position is unread', () => {
  const html = render({
    live: true, positions: [POS(), UNREAD],
    protected_count: 1, unprotected_count: 0, unknown_count: 1,
  });
  assert.ok(!/All \d+ live position/.test(html),
    'the banner claims every position has its stop on the exchange while one ' +
    'of them was never read — the condition is unprotected_count === 0, and ' +
    'an unreadable position is not a protected one');
});

test('the all-clear still fires when everything really was read', () => {
  // The honest path must not eat the true one: this banner is useful.
  const html = render({
    live: true, positions: [POS(), POS()],
    protected_count: 2, unprotected_count: 0, unknown_count: 0,
  });
  assert.match(html, /All 2 live position/);
});

test('a genuinely unprotected position still raises the red alarm', () => {
  const naked = POS({
    sl_order: 'manual', sl_protected: false, unprotected: true, sl_unknown: false,
  });
  const html = render({
    live: true, positions: [naked],
    protected_count: 0, unprotected_count: 1, unknown_count: 0,
  });
  assert.match(html, /unprotected/);
  assert.match(html, /without an exchange stop/);
});

test('an unread stop claims no distance', () => {
  const html = render({
    live: true, positions: [UNREAD],
    protected_count: 0, unprotected_count: 0, unknown_count: 1,
  });
  // `0% away` is what a null sl_dist_pct used to produce upstream. Anchor to
  // the rendered fragment, not the bare string, so this cannot match a
  // leverage or price digit elsewhere on the row.
  assert.ok(!/% away/.test(html), 'the row states a distance nobody measured');
});
