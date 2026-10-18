'use strict';
/**
 * A live call the server withheld renders as withheld, on every page that
 * lists one.
 *
 * The server sends a visitor a live call's market, time and seal, marked
 * `locked` (lib/signal_access.js). Every renderer was written for a row that
 * carries its side and levels, and each one's reading of an absent field is
 * a claim: `dirChip(undefined)` is "— no direction", `fmtPrice(undefined)`
 * a dash where a price is, `sha256(null)` a hash MISMATCH on a receipt that
 * is intact, an empty strategy board "no side anywhere". A locked row is none
 * of those, so each renderer is driven with one and has to say what it is:
 * a live call, and where to see the rest.
 */
process.env.JWT_SECRET = process.env.JWT_SECRET || 'j'.repeat(64);

const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { blockBetween } = require('./helpers/block.js');

const PUB = path.join(__dirname, '..', 'public');
const read = (p) => fs.readFileSync(path.join(PUB, p), 'utf8');
const { lockedSignal, strategyScanFor } = require('../lib/signal_access');

const LIVE = {
  signal_key: 'live:sol:1', symbol: 'SOL/USDT', direction: 'SHORT', confidence: 0.83,
  entry_price: 171.2345, stop_loss: 175.6789, take_profit: 160.1357, status: 'NEW',
  created_at: new Date(Date.now() - 120e3).toISOString(),
  expires_at: new Date(Date.now() + 3600e3).toISOString(), seal: 'a'.repeat(64),
};
const LOCKED = lockedSignal(LIVE);

test('the status model reads the marker, and a locked row offers nothing to act on', () => {
  const SS = require('../public/js/signal-status-model.js');
  assert.equal(SS.locked(LOCKED), true);
  assert.equal(SS.actionable(LOCKED), false, 'a row with no levels was offered a Trade button');
  assert.equal(SS.locked({ status: 'NEW' }), false, 'an absent field is not a lock');
  assert.equal(SS.actionable({ ...LIVE }), true, 'the member\'s row is still actionable');
});

test('the dashboard\'s stream and home rows say "live call", never "no direction"', () => {
  const block = blockBetween(read('js/dashboard.js'),
    '// ── a locked call: renderers start', '// ── a locked call: renderers end');
  const sandbox = {
    self: { SignalStatusModel: require('../public/js/signal-status-model.js') },
    T: (k, en) => en,
    esc: (s) => String(s == null ? '' : s).replace(/[&<>"']/g, (c) => `&#${c.charCodeAt(0)};`),
    fmtAgo: () => '2m ago',
  };
  vm.runInNewContext(block + '\nthis.out = { lockedStreamRow, lockedSignalRow, teaserCta };', sandbox);
  for (const html of [sandbox.out.lockedStreamRow(LOCKED), sandbox.out.lockedSignalRow(LOCKED)]) {
    assert.match(html, /SOL\/USDT/, 'the market is named');
    assert.match(html, /Live call/);
    assert.match(html, /href="\/#join"/, 'it says where to see the rest: the free sign-up');
    assert.ok(!/no direction/.test(html), 'a known side read as "no direction"');
    assert.ok(!/data-ptrade|data-parena/.test(html), 'a Trade button on a row with no levels');
  }
  assert.match(sandbox.out.lockedStreamRow(LOCKED), /\/call\/live%3Asol%3A1/, 'the seal is still checkable');
  // The sample's one line under a list: how many are locked, and the door.
  const cta = sandbox.out.teaserCta(4);
  assert.match(cta, /\+4/);
  assert.match(cta, /href="\/#join"/);
  assert.match(cta, /Register free/);
  assert.equal(sandbox.out.teaserCta(0), '', 'nothing locked says nothing');
  assert.equal(sandbox.out.teaserCta(undefined), '', 'an absent count is not a locked row');
});

test('a visitor\'s setup cards: the sample drawn as cards, the rest as one line', () => {
  const src = read('js/dashboard.js');
  const locked = blockBetween(src, '// ── a locked call: renderers start', '// ── a locked call: renderers end');
  const ec = src.slice(src.indexOf('// ── the entry cards: renderer start'), src.indexOf('// ── the entry cards: renderer end'));
  const sandbox = {
    self: { SignalStatusModel: require('../public/js/signal-status-model.js') },
    window: { EntryCardsModel: require('../public/js/entry-cards-model.js') },
    T: (k, en) => en,
    esc: (s) => String(s == null ? '' : s).replace(/[&<>"']/g, (c) => `&#${c.charCodeAt(0)};`),
    fmtAgo: () => '2m ago', dirChip: (d) => `<i>${d}</i>`, fmtPrice: (n) => String(n),
  };
  vm.runInNewContext(locked + ec + '\nthis.out = { ecPanelHtml };', sandbox);
  const card = (sym) => ({ symbol: sym, direction: 'LONG', entry: '1', stop_loss: '0.9', tp1: '1.2', rr: '2', teaser: true });
  const scan = { timestamp: new Date().toISOString(), calls_locked: true,
    entry_cards: [card('AAA'), card('BBB'), card('CCC'), { symbol: 'DDD', locked: true }, { symbol: 'EEE', locked: true }],
    entry_cards_read: { results: 5, above_floor: 5, considered: 5, cards: 5 } };
  const html = sandbox.out.ecPanelHtml(scan, Date.now());
  assert.equal((html.match(/data-label="Setup"/g) || []).length, 3, 'the three sampled cards are drawn whole');
  assert.match(html, /\+2/, 'the two locked cards are counted');
  assert.ok(!/DDD|EEE/.test(html), 'a locked card is not drawn as a row of blanks');
  // Every card locked (an older server): the market names and the door, as before.
  const shut = sandbox.out.ecPanelHtml({ ...scan, entry_cards: scan.entry_cards.map((c) => ({ symbol: c.symbol, locked: true })) }, Date.now());
  assert.match(shut, /AAA/);
  assert.match(shut, /href="\/#join"/);
});

test('the stream and home loaders route a locked row to its renderer first', () => {
  const src = read('js/dashboard.js');
  const home = blockBetween(src, 'async function homeSignalsLoader() {', 'function refreshHomeSignals() {');
  assert.ok(home.indexOf('locked(s)) return lockedSignalRow(s)') < home.indexOf('dirChip('),
    'the home row draws a side before asking whether it is locked');
  const stream = blockBetween(src, 'async function drawStream() {', '.then(mountSignalCharts);');
  assert.ok(stream.indexOf('locked(s)) return lockedStreamRow(s)') < stream.indexOf('dirChip('),
    'the stream row draws a side before asking whether it is locked');
  const ec = blockBetween(src, 'function ecPanelHtml(scan, now) {', '// ── the entry cards: renderer end');
  assert.ok(ec.indexOf('calls_locked === true') < ec.indexOf('dirChip('),
    'an entry card draws a side before asking whether the cards are locked');
});

test('the embedded board shows a live call as live, with no chart and no share', () => {
  global.window = undefined;
  const ROW = require('../public/js/embed-row.js');
  const html = ROW.rowHtml(LOCKED, { canShare: true, shareSuffix: 'https://x/embed/signals' });
  assert.match(html, /LIVE/);
  assert.match(html, /SOL/);
  assert.match(html, /signed-in members/);
  assert.ok(!/e-chart|data-sc-geo/.test(html), 'a chart drawn without the levels');
  assert.ok(!/171\.2345|SHORT/.test(html));
  const open = ROW.rowHtml(LIVE, {});
  assert.match(open, /171\.23/, 'a member\'s row still prints its levels');
});

test('a strategy board withheld from a visitor names what it read, not a quiet market', () => {
  const Scan = require('../public/js/strategy-scan.js');
  const data = strategyScanFor(false, { public: true, board: {
    rule: 'ma_trend', bar_close: '10:00 UTC', read: ['SOL', 'ADA'], unread: [],
    rows: [{ symbol: 'SOL', side: 'SHORT' }], verdict: 'The averages name: short SOL.' } });
  const html = Scan.boardHtml(data.board);
  assert.match(html, /Read: SOL, ADA/);
  assert.match(html, /Sign in to see the trade/);
  assert.ok(!/SHORT|short SOL/.test(html), 'a side reached the visitor\'s board');
  assert.ok(!/no side/.test(html), 'a withheld side read as "no side"');
});

test('the thesis page says the call is live, not that no thesis was recorded', () => {
  const SP = require('../public/js/signal-page.js');
  const html = SP.render({ signal_key: 'live:sol:1', symbol: 'SOL/USDT', locked: true,
    note: 'Sign in to see this call.' });
  assert.match(html, /SOL\/USDT/);
  assert.match(html, /Sign in/);
  assert.ok(!html.includes(SP.NO_THESIS), 'a withheld thesis read as an absent one');
  assert.ok(!html.includes(SP.UNAVAILABLE), 'a withheld thesis read as a failed read');
});

test('the receipt page draws no verdict over a payload it was not sent', () => {
  const page = read('call.html');
  const locked = blockBetween(page, 'if (d.locked === true) {', 'return sha256hex(d.seal_payload)');
  assert.ok(!/verified|MISMATCH/i.test(locked.replace(/\/\/.*$/gm, '')),
    'a locked receipt claimed a verdict about bytes the page never saw');
  assert.match(locked, /d\.seal/, 'the seal is shown, so it can be checked once revealed');
  assert.ok(page.indexOf('if (d.locked === true) {') < page.indexOf('return sha256hex(d.seal_payload)'),
    'the page hashes before asking whether there is anything to hash');
});

test('the landing receipt shows a live call\'s market and seal rather than hiding', () => {
  const page = read('index.html');
  const block = blockBetween(page, "fetch('/api/call/latest'", '.catch(function () { /* stays hidden');
  assert.ok(block.indexOf('d.locked === true') < block.indexOf('!cur.direction'),
    'a locked receipt fell through to the side check and hid the front door\'s receipt');
});
