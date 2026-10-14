'use strict';
/**
 * Every idea card on the website says what the fees leave of its R:R, in the
 * bot's words, and a record that states nothing about fees claims nothing.
 *
 * 12 October: the Telegram cards learned to say "6.47 (5.74 after fees)"
 * (`bot/core/trade_costs.py::rr_words`). Every page here printed the price
 * ratio alone: the chat and dashboard confirm cards, the chat's setup card,
 * the signal stream, the scan's entry cards, the embed, the flight card and
 * the decision court. A page cannot work the net figure out for itself (the
 * fee table and, on some records, the order type are the bot's), so the bot
 * sends `net_rr` and `rr_fee_losing` (`trade_costs.rr_fields`) and
 * `public/js/rr-words.js` words them once.
 *
 * Two doors also dropped the order type the fees depend on: the chat setup
 * card's "Trade this" re-proposed every setup as a limit, and the dashboard's
 * Review button never sent it (the proxy's half is in gateway_routes.test.js).
 *
 * Driven: the module, the ingest and the public read through the real routes,
 * and each card through the real code lifted between its markers.
 */

process.env.BOT_SYNC_SECRET = process.env.BOT_SYNC_SECRET || 's'.repeat(48);
process.env.JWT_SECRET = process.env.JWT_SECRET || 'j'.repeat(64);

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const http = require('node:http');

const { blockBetween } = require('./helpers/block.js');
const { codeOnly } = require('./helpers/code_only.js');

const PUB = path.join(__dirname, '..', 'public');
const RW = require(path.join(PUB, 'js', 'rr-words.js'));
const DASH = fs.readFileSync(path.join(PUB, 'js', 'dashboard.js'), 'utf8');
const CHAT = fs.readFileSync(path.join(PUB, 'js', 'chat.js'), 'utf8');

const esc = (s) => String(s == null ? '' : s).replace(/[&<>"']/g, (c) => (
  { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const fmt = (v, d) => (v == null ? '—' : Number(v).toFixed(d == null ? 2 : d));

// ── the one wording ───────────────────────────────────────────────────────

test('the wording names each of the three states', () => {
  assert.equal(RW.text(6.47, { net_rr: 5.74, rr_fee_losing: false }), '6.47 (5.74 after fees)');
  assert.equal(RW.text(6.47, { net_rr: null, rr_fee_losing: true }),
    '6.47 (the target does not cover the fees)');
  // A level the bot could not read, and a record written before the fields:
  // no claim about fees either way.
  assert.equal(RW.text(6.47, { net_rr: null, rr_fee_losing: null }), '6.47');
  assert.equal(RW.text(6.47, {}), '6.47');
  assert.equal(RW.text(null, { net_rr: 5.74 }), null, 'an unread ratio is the caller\'s to paint');
});

test('a database row is read as it arrives, and false or 0 is not a fee claim', () => {
  assert.equal(RW.afterFees({ net_rr: '5.7400', rr_fee_losing: 0 }), '5.74 after fees');
  assert.equal(RW.afterFees({ net_rr: null, rr_fee_losing: 1 }), 'the target does not cover the fees');
  assert.equal(RW.afterFees({ net_rr: null, rr_fee_losing: 0 }), null);
  assert.equal(RW.afterFees({ net_rr: '', rr_fee_losing: false }), null);
  assert.equal(RW.afterFees({ net_rr: 0 }), '0.00 after fees', 'a measured break-even is a figure');
  assert.equal(RW.afterFees({ net_rr: 5.74 }, { places: 1, suffix: 'R' }), '5.7R after fees');
});

// ── the public stream: stored null-preserving, served through the allowlist ─

let server;
let base;

test.before(async () => {
  const express = require('express');
  const app = express();
  app.use(express.json());
  app.use('/api/bot/sync', require('../routes/sync'));
  app.use('/api/signals', require('../routes/signals'));
  await new Promise((resolve) => { server = app.listen(0, '127.0.0.1', resolve); });
  base = `http://127.0.0.1:${server.address().port}`;
});

test.after(() => { if (server) server.close(); });

function request(method, p, body) {
  return new Promise((resolve, reject) => {
    const payload = body ? JSON.stringify(body) : null;
    const headers = { 'X-Bot-Secret': process.env.BOT_SYNC_SECRET };
    if (payload) headers['Content-Type'] = 'application/json';
    // The stream is read as a signed-in member: these rows are live calls,
    // whose levels a visitor is not sent (lib/signal_access.js).
    if (method === 'GET') {
      headers.Authorization = 'Bearer ' + require('jsonwebtoken').sign(
        { user_id: 4242, email: 'member@test.dev' }, process.env.JWT_SECRET);
    }
    const req = http.request(base + p, { method, headers }, (res) => {
      let data = '';
      res.on('data', (c) => { data += c; });
      res.on('end', () => resolve({ status: res.statusCode, body: JSON.parse(data) }));
    });
    req.on('error', reject);
    if (payload) req.write(payload);
    req.end();
  });
}

test('the stream stores what the bot stated and serves it, and an absence stays null', async () => {
  const row = (key, extra) => ({
    signal_key: key, symbol: 'TIA/USDT', direction: 'LONG', confidence: 0.71,
    entry_price: 0.5882, stop_loss: 0.58164, take_profit: 0.63065, rr: 6.47,
    status: 'NEW', created_at: new Date().toISOString(), ...extra,
  });
  const r = await request('POST', '/api/bot/sync/signals', { signals: [
    row('fees-read', { net_rr: 5.74, rr_fee_losing: false }),
    row('fees-losing', { net_rr: null, rr_fee_losing: true }),
    row('fees-old', {}),
  ] });
  assert.equal(r.status, 200);
  assert.equal(r.body.upserted, 3);
  const got = await request('GET', '/api/signals?limit=50');
  const by = Object.fromEntries(got.body.signals.map((s) => [s.signal_key, s]));
  assert.equal(Number(by['fees-read'].net_rr), 5.74);
  assert.equal(by['fees-read'].rr_fee_losing, 0);
  assert.equal(by['fees-losing'].net_rr, null);
  assert.equal(by['fees-losing'].rr_fee_losing, 1);
  // `Number(undefined) || 0` would have stored "the fees take it all".
  assert.equal(by['fees-old'].net_rr, null);
  assert.equal(by['fees-old'].rr_fee_losing, null);
  assert.equal(RW.text(by['fees-read'].rr, by['fees-read']), '6.47 (5.74 after fees)');
  assert.equal(RW.text(by['fees-old'].rr, by['fees-old']), '6.47');
});

test('the stream query names both fields, so MySQL returns them', () => {
  // The in-memory pool returns whole rows whatever a SELECT names; a column
  // the query leaves out is one MySQL never returns. Read it off the query.
  const src = codeOnly(fs.readFileSync(path.join(__dirname, '..', 'routes', 'signals.js'), 'utf8'));
  const sel = src.slice(src.indexOf('SELECT signal_key'), src.indexOf('FROM signals ${clause}'));
  assert.ok(sel.length > 0, 'the stream query moved');
  assert.match(sel, /\bnet_rr\b/);
  assert.match(sel, /\brr_fee_losing\b/);
});

// ── the confirm cards ────────────────────────────────────────────────────

const TICKET = {
  trade_id: 'TI-1', symbol: 'TIA', direction: 'LONG', mode: 'PAPER',
  entry: 0.5882, sl: 0.58164, tp: 0.63065, rr: 6.47, sl_pct: 1.1, tp_pct: 7.2,
  margin_usd: 0, order_type: 'market', live_allowed: false, live_reason: '',
  copilot: null, net_rr: 5.74, rr_fee_losing: false,
};

function el() {
  // One child per element, so a handler set through `querySelector` is the
  // one a test then calls.
  let child = null;
  const node = {
    innerHTML: '', textContent: '', className: '', style: {}, disabled: false,
    classList: { add() {}, remove() {}, contains: () => false },
    appendChild() {}, addEventListener() {}, removeEventListener() {},
    querySelector: () => (child = child || el()), querySelectorAll: () => [el(), el()],
    remove() {},
  };
  return node;
}

function modalHtml(ticket, words) {
  const nodes = {};
  const ctx = {
    esc, fmt, fmtMoney: (v) => '$' + Number(v).toFixed(2),
    copilotReviewHtml: () => '', sanitizeBotHtml: (s) => String(s),
    T: (_k, en) => en, toast() {}, cache: {},
    fetchJSON: async () => ({ ok: true, data: {} }),
    CustomEvent: function (name) { this.type = name; },
    document: { getElementById: (id) => nodes[id] || (nodes[id] = el()),
                addEventListener() {}, removeEventListener() {}, dispatchEvent() {} },
    window: { RC: { modalA11y: () => ({ open() {}, close() {} }), postWithStepUp: async () => ({}) },
              TradeConfirmModel: { outcome: () => ({ kind: 'placed', text: '' }) } },
    self: words ? { RCRRWords: words } : {},
  };
  ctx.RC = ctx.window.RC;
  vm.createContext(ctx);
  const block = blockBetween(DASH, '// ── trade confirm modal ─', '// ── trade confirm modal end ─',
    { label: 'the dashboard confirm modal' });
  vm.runInContext(block + '\n;globalThis.__open = openTradeModal;', ctx);
  ctx.__open(ticket);
  return nodes.tradeModalBody.innerHTML;
}

test('the dashboard confirm modal prints the ratio and what the fees leave of it', () => {
  const html = modalHtml(TICKET, RW);
  assert.match(html, /<span>Risk : reward<\/span><b>6\.47 \(5\.74 after fees\)<\/b>/);
  // The other arm: a payload from a bot that sends no fields claims nothing.
  const old = { ...TICKET };
  delete old.net_rr; delete old.rr_fee_losing;
  assert.match(modalHtml(old, RW), /<span>Risk : reward<\/span><b>6\.47<\/b>/);
});

function chatCardHtml(ticket) {
  const made = [];
  const ctx = {
    esc, fmt, body: el(), appendMsg() {}, sanitizeBotHtml: (s) => String(s),
    T: (_k, en) => en, postWithStepUp: async () => ({}),
    fetchJSON: async () => ({ ok: true, data: {} }),
    document: { createElement: () => { const n = el(); made.push(n); return n; },
                dispatchEvent() {} },
    CustomEvent: function (name) { this.type = name; },
    window: { CopilotReviewModel: { render: () => '' },
              TradeConfirmModel: { outcome: () => ({ kind: 'placed', text: '' }) } },
    self: { RCRRWords: RW },
  };
  vm.createContext(ctx);
  const block = blockBetween(CHAT, '// ── chat trade card ─', '// ── chat trade card end ─',
    { label: 'the chat drawer trade card' });
  vm.runInContext(block + '\n;globalThis.__card = appendTradeCard;', ctx);
  ctx.__card(ticket);
  return made[0].innerHTML;
}

test('the chat trade card says both, and labels a market entry as a reference', () => {
  const html = chatCardHtml(TICKET);
  assert.match(html, /<b>R:R 6\.47 \(5\.74 after fees\)<\/b>/);
  assert.match(html, /<span>Entry \(reference\)<\/span>/);
  const limit = chatCardHtml({ ...TICKET, order_type: 'limit', net_rr: 6.04 });
  assert.match(limit, /<b>R:R 6\.47 \(6\.04 after fees\)<\/b>/);
  assert.match(limit, /<span>Entry \(limit\)<\/span>/);
});

// ── the chat setup card: its ratio, and the order type it re-proposes with ─

async function setupCard(setup) {
  const made = [];
  const posted = [];
  const ctx = {
    esc, fmt, body: el(), appendMsg() {}, sanitizeBotHtml: (s) => String(s),
    T: (_k, en) => en, PUBLIC: false, postWithStepUp: async () => ({}),
    fetchJSON: async (url, opts) => { posted.push({ url, body: opts && opts.body });
      return { ok: false, data: { error: 'stop here' } }; },
    document: { createElement: () => { const n = el(); made.push(n); return n; },
                dispatchEvent() {} },
    CustomEvent: function (name) { this.type = name; },
    window: { CopilotReviewModel: { render: () => '' } },
    self: { RCRRWords: RW },
  };
  vm.createContext(ctx);
  const card = blockBetween(CHAT, '// ── chat trade card ─', '// ── chat trade card end ─',
    { label: 'the chat drawer trade card' });
  const action = blockBetween(CHAT, '// ── chat setup action ─', '// ── chat setup action end ─',
    { label: 'the chat setup action' });
  vm.runInContext(card + '\n' + action + '\n;globalThis.__setup = appendSetupAction;', ctx);
  ctx.__setup(setup);
  const div = made[0];
  const btn = div.querySelector('button');
  await btn.onclick();
  return { html: div.innerHTML, posted };
}

test('the chat setup card says both and re-proposes at the idea\'s order type', async () => {
  const setup = { symbol: 'TIA', direction: 'LONG', entry: 0.5882, sl: 0.58164, tp: 0.63065,
                  rr: 6.47, net_rr: 5.74, rr_fee_losing: false, order_type: 'market',
                  confidence: 0.71 };
  const { html, posted } = await setupCard(setup);
  assert.match(html, /<b>R:R 6\.47 \(5\.74 after fees\)<\/b>/);
  assert.equal(posted[0].url, '/api/trade/propose');
  assert.equal(posted[0].body.order_type, 'market');
  // A hint from a bot that sends no order type posts none; the route's
  // default (limit) is the bot's to apply, not this card's to invent.
  const old = { ...setup };
  delete old.order_type;
  const again = await setupCard(old);
  assert.equal('order_type' in again.posted[0].body, false);
});

// ── the dashboard's Review button sends the ticket's order type ──────────

test('the Review button posts the ticket\'s order type', () => {
  // The handler sits inside a 6k-line function no test can call; this reads
  // the body it builds, comment-free, bounded by its own listener.
  const src = codeOnly(DASH);
  const a = src.indexOf("getElementById('tCopilotBtn').addEventListener");
  const b = src.indexOf("fetchJSON('/api/trade/copilot'", a);
  assert.ok(a > 0 && b > a, 'the Review handler moved');
  assert.match(src.slice(a, b), /order_type:\s*\(\$\('tOrderType'\)/);
});

// ── the scan's entry cards ───────────────────────────────────────────────

function entryCards(card) {
  const a = DASH.indexOf('// ── the entry cards: renderer start ─');
  const b = DASH.indexOf('// ── the entry cards: renderer end ─');
  assert.ok(a > 0 && b > a);
  const ctx = {
    window: { EntryCardsModel: require(path.join(PUB, 'js', 'entry-cards-model.js')) },
    self: { RCRRWords: RW }, esc, T: (_k, en) => en,
    dirChip: (d) => `<span class="chip">${d}</span>`,
    fmtPrice: (n) => (isFinite(n) ? String(n) : '—'), out: null,
  };
  vm.runInNewContext(DASH.slice(a, b) + '\nout = { ecPanelHtml: ecPanelHtml };', ctx);
  const at = '2026-10-12T12:00:00.000Z';
  return ctx.out.ecPanelHtml({ timestamp: at, entry_cards: [card],
    entry_cards_read: { results: 1, above_floor: 1, considered: 1, cards: 1, no_atr: 0,
                        no_direction: 0, floor: 0.4, shown_max: 8 } },
  Date.parse('2026-10-12T12:10:00.000Z'));
}

test('an entry card prints its ratio with the fee clause under it', () => {
  const card = { symbol: 'TIA', direction: 'LONG', entry: '0.5882', stop_loss: '0.58164',
                 tp1: '0.63065', rr: '6.47', trigger: 'RSI 55' };
  const html = entryCards({ ...card, net_rr: 6.04, rr_fee_losing: false });
  assert.match(html, /6\.47<div class="small muted">6\.04 after fees<\/div>/);
  assert.doesNotMatch(entryCards(card), /after fees/);
});

// ── the embed and the decision court ─────────────────────────────────────

test('the embed card and its share line say what the fees leave', () => {
  const ER = require(path.join(PUB, 'js', 'embed-row.js'));
  const sig = { symbol: 'TIA/USDT:USDT', direction: 'LONG', confidence: 0.71, rr: '6.4700',
                entry_price: 0.5882, stop_loss: 0.58164, take_profit: 0.63065,
                net_rr: '5.7400', rr_fee_losing: 0 };
  assert.match(ER.rowHtml(sig, {}), /<dt>r:r<\/dt><dd>6\.5R <small>\(5\.7R after fees\)<\/small><\/dd>/);
  assert.match(ER.shareText(sig, {}), /6\.5R target \(5\.7R after fees\)/);
  const old = { ...sig, net_rr: null, rr_fee_losing: null };
  assert.match(ER.rowHtml(old, {}), /<dt>r:r<\/dt><dd>6\.5R<\/dd>/);
  assert.match(ER.shareText(old, {}), /6\.5R target(?! \()/);
});

test('the decision court prints the sealed ratio with what the fees leave of it', () => {
  const M = require(path.join(PUB, 'js', 'decision-court-model.js'));
  const record = (idea) => ({ decision_id: 'D1', timestamp: '2026-10-12T10:35:00Z',
    idea: { direction: 'LONG', entry: 0.5882, sl: 0.58164, tp: 0.63065, ...idea } });
  const flat = (v) => JSON.stringify(v);
  assert.match(flat(M.decisionCourt({ record: record({ rr: 6.47, net_rr: 5.74, rr_fee_losing: false }) }, 200)),
    /"value":"6\.47 \(5\.74 after fees\)"/);
  const old = flat(M.decisionCourt({ record: record({ rr: 6.47 }) }, 200));
  assert.match(old, /"value":"6\.47"/);
  assert.doesNotMatch(old, /after fees/);
});
