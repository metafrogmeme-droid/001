/**
 * A REFUSED CONFIRM IS NOT A CONFIRMED TRADE, on either surface that has a
 * Confirm button.
 *
 * The bot gateway answered every sentence its confirm can write with a 200 —
 * the risk gate's refusal, the duplicate skip, "Paper trading is disabled",
 * the chosen-strategy refusal, the practice cooldown — and both browser
 * surfaces read a 200 as a trade. Driven against the unfixed bundles: the
 * dashboard's confirm modal closed on a GREEN "Trade confirmed." toast, nulled
 * the portfolio cache and announced `rc:portfolio-changed` over a refusal; the
 * chat drawer's card printed the refusal as the execution and announced the
 * same event. The bot now sends `placed` beside the answer (from
 * `placed_nothing`, the reading its Telegram Confirm button asks), and
 * `TradeConfirmModel.outcome` is the ONE reading of it in the browser.
 *
 * Each surface is DRIVEN: its block is sliced by its markers, run against a DOM
 * stub, the Confirm button's handler is invoked with a planted response, and
 * the assertions are on what a person would see and what the page announced.
 * The model is also PLANTED with an answer the response does not support, and
 * each surface must obey it — a surface that re-reads `placed` itself agrees
 * with every honest fixture and diverges on the first edit to either.
 */
'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const { blockBetween } = require('./helpers/block.js');
const { codeOnly } = require('./helpers/code_only.js');

const PUB = path.join(__dirname, '..', 'public');
const M = require(path.join(PUB, 'js', 'trade-confirm-model.js'));
const I18N = require(path.join(PUB, 'js', 'i18n.js'));
const DASH = fs.readFileSync(path.join(PUB, 'js', 'dashboard.js'), 'utf8');
const CHAT = fs.readFileSync(path.join(PUB, 'js', 'chat.js'), 'utf8');

const esc = (s) => String(s ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;')
  .replace(/>/g, '&gt;').replace(/"/g, '&quot;');

const REFUSAL = 'Trade REJECTED: price drifted 2.4% since analysis. Re-analyze.';
const FILL = '✅ LIVE LONG SOL/USDT opened';

const ok = (data) => ({ ok: true, status: 200, data });
const REFUSED = ok({ result_html: REFUSAL, placed: false });
const PLACED = ok({ result_html: FILL, placed: true });
const LEGACY = ok({ result_html: FILL });                    // an older bot: no `placed`
const UNREAD = ok({ result_html: 'something', placed: 'maybe' });
const FAILED = { ok: false, status: 403, data: { error: 'not_proposer' } };

// ── the model ─────────────────────────────────────────────────────────────

test('the model reads the bot\'s own `placed`, never the status code', () => {
  assert.equal(M.outcome(REFUSED).kind, 'refused');
  assert.equal(M.outcome(REFUSED).text, REFUSAL);
  assert.equal(M.outcome(PLACED).kind, 'placed');
  assert.equal(M.outcome(PLACED).legacy, false);
  assert.equal(M.outcome(FAILED).kind, 'failed');
  assert.equal(M.outcome(null).kind, 'failed');
});

test('an ABSENT `placed` is an older bot and keeps the old behaviour, flagged', () => {
  const out = M.outcome(LEGACY);
  assert.equal(out.kind, 'placed');
  assert.equal(out.legacy, true);
});

for (const junk of ['maybe', 'false', 0, 1, null, 'true']) {
  test(`a \`placed\` of ${JSON.stringify(junk)} is unread — neither placed nor refused`, () => {
    assert.equal(M.outcome(ok({ result_html: 'x', placed: junk })).kind, 'unread');
  });
}

test('the answer text is carried, and a non-string answer is not invented', () => {
  assert.equal(M.outcome(ok({ result_html: 42, placed: false })).text, '');
});

// ── the dashboard's confirm modal, driven ─────────────────────────────────

function el() {
  const node = {
    innerHTML: '', textContent: '', hidden: false, className: '', onclick: null,
    classes: new Set(),
    classList: {
      add: (c) => node.classes.add(c), remove: (c) => node.classes.delete(c),
      contains: (c) => node.classes.has(c),
    },
    appendChild() {}, addEventListener() {}, removeEventListener() {},
  };
  return node;
}

const TICKET = {
  trade_id: 'TI-1', symbol: 'SOL', direction: 'LONG', mode: 'PAPER',
  entry: 71.42, sl: 70.05, tp: 76.42, rr: 3.65, sl_pct: 1.9, tp_pct: 7,
  margin_usd: 0, order_type: 'limit', live_allowed: false, live_reason: '', copilot: null,
};

async function runModal(response, { model = M } = {}) {
  const nodes = { tradeModal: el(), tradeModalBody: el(), tradeModalMsg: el(),
                  tradeModalConfirm: el(), tradeModalCancel: el() };
  const toasts = [];
  const events = [];
  const done = [];
  const cache = { portfolio: 'CACHED' };
  const ctx = {
    esc,
    fmt: (v, d) => (v == null ? '—' : Number(v).toFixed(d || 2)),
    fmtMoney: (v) => '$' + Number(v).toFixed(2),
    copilotReviewHtml: () => '',
    sanitizeBotHtml: (s) => String(s),
    T: (_k, fallback) => fallback,
    toast: (text, kind) => toasts.push([text, kind]),
    cache,
    fetchJSON: async () => ({ ok: true, data: {} }),
    CustomEvent: function (name) { this.type = name; },
    document: {
      getElementById: (id) => nodes[id] || (nodes[id] = el()),
      addEventListener() {}, removeEventListener() {},
      dispatchEvent: (e) => events.push(e.type),
    },
    window: { RC: { modalA11y: () => ({ open() {}, close() {} }),
                    postWithStepUp: async () => response } },
  };
  if (model) ctx.window.TradeConfirmModel = model;
  ctx.RC = ctx.window.RC;
  vm.createContext(ctx);
  const block = blockBetween(DASH, '// ── trade confirm modal ─', '// ── trade confirm modal end ─',
    { label: 'the dashboard confirm modal' });
  vm.runInContext(block + '\n;globalThis.__open = openTradeModal;', ctx);
  ctx.__open(TICKET, (t) => done.push(t));
  await nodes.tradeModalConfirm.onclick();
  return {
    toasts, events, done, cache,
    msg: nodes.tradeModalMsg.innerHTML || nodes.tradeModalMsg.textContent,
    closed: nodes.tradeModal.classes.has('hidden'),
    confirmStillWired: typeof nodes.tradeModalConfirm.onclick === 'function',
  };
}

test('MODAL: a refusal stays open, says nothing was placed, and paints no green', async () => {
  const r = await runModal(REFUSED);
  assert.deepEqual(r.toasts, [], 'a refusal was toasted');
  assert.deepEqual(r.events, [], 'a refusal announced a portfolio change');
  assert.equal(r.closed, false, 'the modal closed over a refusal');
  assert.equal(r.cache.portfolio, 'CACHED', 'nothing on the book moved');
  assert.ok(r.msg.includes('Nothing was placed.'), r.msg);
  assert.ok(r.msg.includes(REFUSAL), r.msg);
  assert.ok(r.msg.includes('class="neg"'), r.msg);
  assert.ok(r.confirmStillWired, 'the Confirm button must stay usable after a refusal');
  assert.deepEqual(r.done, []);
});

test('MODAL: a placement closes on the green toast and refreshes the book', async () => {
  const r = await runModal(PLACED);
  assert.deepEqual(r.toasts, [['Trade confirmed.', 'up']]);
  assert.deepEqual(r.events, ['rc:portfolio-changed']);
  assert.equal(r.closed, true);
  assert.equal(r.cache.portfolio, null);
  assert.deepEqual(r.done, [FILL]);
});

test('MODAL: an older bot (no `placed`) keeps the behaviour it has always had', async () => {
  const r = await runModal(LEGACY);
  assert.deepEqual(r.toasts, [['Trade confirmed.', 'up']]);
  assert.equal(r.closed, true);
});

test('MODAL: an unreadable `placed` claims neither — no toast, a refresh, the answer shown', async () => {
  const r = await runModal(UNREAD);
  assert.deepEqual(r.toasts, []);
  assert.deepEqual(r.events, ['rc:portfolio-changed'], 'the book may have moved; look');
  assert.equal(r.closed, false);
  assert.ok(r.msg.includes('check your positions'), r.msg);
});

test('MODAL: a failed request keeps its own sentence', async () => {
  const r = await runModal(FAILED);
  assert.deepEqual(r.toasts, []);
  assert.ok(r.msg.includes('not_proposer'), r.msg);
});

test('MODAL: with the model absent a 200 is not announced as a trade', async () => {
  const r = await runModal(PLACED, { model: null });
  assert.deepEqual(r.toasts, [], 'the page claimed a trade it could not read');
  assert.equal(r.closed, false);
});

test('MODAL: it ASKS the model — a planted refusal over `placed: true` is obeyed', async () => {
  const planted = { outcome: () => ({ kind: 'refused', text: 'PLANTED' }) };
  const r = await runModal(PLACED, { model: planted });
  assert.deepEqual(r.toasts, []);
  assert.ok(r.msg.includes('PLANTED'), r.msg);
});

// ── the chat drawer's trade card, driven ──────────────────────────────────

async function runChatCard(response, { model = M } = {}) {
  const made = [];
  const said = [];
  const events = [];
  const buttons = [{ disabled: false, onclick: null }, { disabled: false, onclick: null }];
  const ctx = {
    esc,
    fmt: (v, d) => (v == null ? '—' : Number(v).toFixed(d || 2)),
    body: el(),
    appendMsg: (role, html) => said.push(html),
    sanitizeBotHtml: (s) => String(s),
    T: (_k, fallback) => fallback,
    postWithStepUp: async () => response,
    fetchJSON: async () => ({ ok: true, data: {} }),
    document: {
      createElement: () => {
        const n = el();
        n.querySelectorAll = () => buttons;
        made.push(n);
        return n;
      },
      dispatchEvent: (e) => events.push(e.type),
    },
    CustomEvent: function (name) { this.type = name; },
    window: { CopilotReviewModel: { render: () => '' } },
  };
  if (model) ctx.window.TradeConfirmModel = model;
  vm.createContext(ctx);
  const block = blockBetween(CHAT, '// ── chat trade card ─', '// ── chat trade card end ─',
    { label: 'the chat drawer trade card' });
  vm.runInContext(block + '\n;globalThis.__card = appendTradeCard;', ctx);
  ctx.__card(TICKET);
  await buttons[0].onclick();
  return { said, events, buttons };
}

test('CHAT: a refusal says nothing was placed, and gives both buttons back', async () => {
  const r = await runChatCard(REFUSED);
  assert.equal(r.said.length, 1);
  assert.ok(r.said[0].includes('Nothing was placed.'), r.said[0]);
  assert.ok(r.said[0].includes(REFUSAL), r.said[0]);
  assert.deepEqual(r.events, [], 'a refusal announced a portfolio change');
  assert.equal(r.buttons[0].disabled, false, 'Confirm must come back');
  assert.equal(r.buttons[1].disabled, false, 'Cancel must come back');
});

test('CHAT: a placement prints the answer and refreshes the book', async () => {
  const r = await runChatCard(PLACED);
  assert.deepEqual(r.said, [FILL]);
  assert.deepEqual(r.events, ['rc:portfolio-changed']);
});

test('CHAT: an unreadable `placed` is never printed as "Executed."', async () => {
  const r = await runChatCard(ok({ result_html: '', placed: 'maybe' }));
  assert.equal(r.said.length, 1);
  assert.ok(!r.said[0].startsWith('Executed'), r.said[0]);
  assert.ok(r.said[0].includes('check your positions'), r.said[0]);
});

test('CHAT: with the model absent a 200 is not announced as a trade', async () => {
  const r = await runChatCard(ok({ result_html: '', placed: true }), { model: null });
  assert.ok(!r.said[0].startsWith('Executed'), r.said[0]);
});

test('CHAT: it ASKS the model — a planted refusal over `placed: true` is obeyed', async () => {
  const planted = { outcome: () => ({ kind: 'refused', text: 'PLANTED' }) };
  const r = await runChatCard(PLACED, { model: planted });
  assert.ok(r.said[0].includes('PLANTED'), r.said[0]);
  assert.deepEqual(r.events, []);
});

// ── one reading, reachable on both pages, in fourteen languages ───────────

test('neither surface reads `placed` itself — the model is the one reading', () => {
  const blocks = [
    blockBetween(DASH, '// ── trade confirm modal ─', '// ── trade confirm modal end ─', { label: 'modal' }),
    blockBetween(CHAT, '// ── chat trade card ─', '// ── chat trade card end ─', { label: 'chat card' }),
  ];
  for (const b of blocks) {
    assert.ok(!/\.placed\b/.test(codeOnly(b)), 'a surface re-reads `placed` beside the model');
  }
});

test('both pages that host a Confirm button load the model', () => {
  for (const page of ['dashboard.html', 'index.html']) {
    const html = fs.readFileSync(path.join(PUB, page), 'utf8');
    if (!/\/js\/(chat|dashboard)\.js\?v=/.test(html)) continue;
    assert.match(html, /<script src="\/js\/trade-confirm-model\.js\?v=\d+"/, page);
  }
});

test('the two sentences the surfaces add exist in all fourteen languages', () => {
  const codes = I18N.LANGS.map((l) => l.code);
  assert.equal(codes.length, 14);
  for (const key of ['dd.t_trade_refused', 'dd.t_trade_unread']) {
    const entry = I18N.STRINGS[key];
    assert.ok(entry, key);
    for (const c of codes) assert.ok(typeof entry[c] === 'string' && entry[c].length, `${key}:${c}`);
  }
});
