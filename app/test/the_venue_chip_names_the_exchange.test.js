'use strict';
/**
 * The exchange-keys card names the EXCHANGE while a key waits on the bot.
 *
 * Reported 8 October: a Bybit key waiting on the bot read "applying
 * connect…". The pending branch filled the `{venue}` slot with the pending
 * ACTION ('connect' or 'disconnect') instead of the exchange's name.
 *
 * Driven: `venueChip` is cut out of dashboard.js by its markers and run. The
 * card's call is checked to hand it the venue's label, since a renderer
 * nobody calls with the right argument would pass the first half alone.
 */
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { codeOnly } = require('./helpers/code_only');

const DASH = fs.readFileSync(path.join(__dirname, '..', 'public', 'js', 'dashboard.js'), 'utf8');

function loadChip() {
  const a = DASH.indexOf('  // ── venue chip: start');
  const b = DASH.indexOf('  // ── venue chip: end');
  assert.ok(a > 0 && b > a, 'venueChip moved; it sits between its markers in dashboard.js');
  const T = (k, en) => en;
  const TF = (k, en, map) => String(en).replace(/\{(\w+)\}/g,
    (w, key) => (map && map[key] != null ? String(map[key]) : w));
  const esc = (s) => String(s).replace(/[&<>"]/g,
    (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  const ctx = { T, TF, esc, String };
  vm.runInNewContext(DASH.slice(a, b) + '\nglobalThis.venueChip = venueChip;', ctx);
  return ctx.venueChip;
}

const venueChip = loadChip();

test('a key waiting on the bot names the exchange, not the action', () => {
  const html = venueChip({ connected: false, pending: 'connect', rejected: null, label: 'Bybit' });
  assert.match(html, /chip--warn/);
  assert.match(html, />applying Bybit…</);
  assert.doesNotMatch(html, /applying connect/);
  const off = venueChip({ connected: false, pending: 'disconnect', rejected: null, label: 'Bitget' });
  assert.match(off, />applying Bitget…</);
  assert.doesNotMatch(off, /disconnect/);
});

test('the other three states read as before', () => {
  assert.match(venueChip({ connected: true, pending: null, rejected: null, label: 'Bitget' }),
    /chip--up">✓ connected</);
  assert.match(venueChip({ connected: false, pending: null, rejected: 'code 10003', label: 'Bybit' }),
    /chip--down">✕ rejected</);
  assert.match(venueChip({ connected: false, pending: null, rejected: null, label: 'Bybit' }),
    /<span class="chip">not connected</);
});

test('the keys card hands the chip the venue label', () => {
  const src = codeOnly(DASH);
  const start = src.indexOf('async function renderAccount()');
  assert.ok(start > 0, 'renderAccount moved');
  assert.ok(src.indexOf('venueChip({ connected, pending, rejected, label: v.label })', start) > start,
    'the keys card no longer asks venueChip with the venue label');
});
