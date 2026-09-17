/**
 * THE REVIEW IS BESIDE EVERY CONFIRM BUTTON, and each surface is RUN.
 *
 * The co-pilot's only door was `POST /api/trade/copilot`, whose only caller is
 * the dashboard ticket form's Review button — so the second opinion was a
 * property of ONE CLIENT's preview rather than of the proposal, and the two
 * other places a person taps Confirm for the same ticket carried no review of
 * any kind:
 *
 *   * the dashboard's confirm MODAL — the last screen before a real order;
 *   * the chat drawer's trade card, in a DIFFERENT bundle.
 *
 * Both are DRIVEN here, not grepped. `copilotReviewHtml(pt.copilot)` sitting
 * inside a template literal reads as obviously reached, and "obviously
 * reached" is #999's card exactly: present, correct-looking, rendered zero
 * times. Each block is sliced by its markers, executed against a DOM stub, and
 * the assertion is on the HTML a person would see.
 *
 * The second claim is that there is ONE renderer. A byte-identical copy of the
 * block per surface agrees with every fixture and diverges on the first edit to
 * either, so the guard drives the MODEL's own render and requires each surface
 * to answer what it said.
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
const M = require(path.join(PUB, 'js', 'copilot-review-model.js'));
const DASH = fs.readFileSync(path.join(PUB, 'js', 'dashboard.js'), 'utf8');
const CHAT = fs.readFileSync(path.join(PUB, 'js', 'chat.js'), 'utf8');

/** The page's own escaper, byte-for-byte (app.js). */
const esc = (s) => String(s ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;')
  .replace(/>/g, '&gt;').replace(/"/g, '&quot;');

const REVIEW = {
  verdict: 'caution', score: 70, rr: 1.2, stop_pct: 0.2, target_pct: 0.24,
  score_line: 'score 70/100 over 3 of the 4 checks',
  flags: [{ level: 'warn', msg: 'Stop is only 0.2% away — likely to be wicked out.' }],
  notes: [],
  unchecked: [{ name: 'engine_bias', label: "the engine's bias",
    reason: 'the engine has no open idea on SOL right now' }],
};

const TICKET = {
  trade_id: 'T-1', symbol: 'SOL', direction: 'LONG', mode: 'LIVE',
  entry: 71.42, sl: 70.05, tp: 76.42, rr: 3.65, sl_pct: 1.9, tp_pct: 7,
  margin_usd: 250, order_type: 'limit', live_allowed: true, live_reason: '',
  copilot: REVIEW,
};

/** A DOM element that records what was written into it. */
function el() {
  const node = {
    innerHTML: '', textContent: '', hidden: false, className: '', value: '',
    scrollTop: 0, scrollHeight: 0, onclick: null,
    classList: { add() {}, remove() {}, contains: () => false },
    appendChild(child) { node.children.push(child); },
    children: [],
    querySelectorAll: () => [{ disabled: false }, { disabled: false }],
    addEventListener() {}, removeEventListener() {},
  };
  return node;
}

/** Run the dashboard's confirm modal and hand back what it painted. */
function runModal(ticket) {
  const nodes = {};
  const body = el();
  const ctx = {
    esc,
    fmt: (v, d) => (v == null ? '—' : Number(v).toFixed(d || 2)),
    fmtMoney: (v) => '$' + Number(v).toFixed(2),
    // The real adapter, so this drives the one renderer rather than a stand-in.
    copilotReviewHtml: (d) => M.render(d, esc),
    T: (_k, fallback) => fallback,
    toast() {}, cache: {}, fetchJSON: async () => ({ ok: true, data: {} }),
    document: {
      getElementById: (id) => {
        if (id === 'tradeModalBody') return body;
        nodes[id] = nodes[id] || el();
        return nodes[id];
      },
      addEventListener() {}, removeEventListener() {},
    },
    window: { RC: { modalA11y: () => ({ open() {}, close() {} }),
                    postWithStepUp: async () => ({ ok: true, data: {} }) } },
  };
  ctx.RC = ctx.window.RC;
  vm.createContext(ctx);
  const block = blockBetween(DASH, '// ── trade confirm modal ─', '// ── trade confirm modal end ─',
    { label: 'the dashboard confirm modal' });
  vm.runInContext(block + '\n;globalThis.__open = openTradeModal;', ctx);
  ctx.__open(ticket, null);
  return body.innerHTML;
}

/** Run the chat drawer's trade card and hand back what it painted. */
function runChatCard(ticket, model) {
  const made = [];
  const ctx = {
    esc,
    fmt: (v, d) => (v == null ? '—' : Number(v).toFixed(d || 2)),
    body: el(),
    appendMsg() {}, sanitizeBotHtml: (s) => s,
    postWithStepUp: async () => ({ ok: true, data: {} }),
    fetchJSON: async () => ({ ok: true, data: {} }),
    document: {
      createElement: () => { const n = el(); made.push(n); return n; },
      dispatchEvent() {},
    },
    CustomEvent: function () {},
    window: { CopilotReviewModel: model === undefined ? M : model },
  };
  vm.createContext(ctx);
  const block = blockBetween(CHAT, '// ── chat trade card ─', '// ── chat trade card end ─',
    { label: 'the chat drawer trade card' });
  vm.runInContext(block + '\n;globalThis.__card = appendTradeCard;', ctx);
  ctx.__card(ticket);
  assert.equal(made.length, 1, 'the card built exactly one element');
  return made[0].innerHTML;
}

test('the dashboard CONFIRM MODAL carries the review, not just the ticket form', () => {
  const html = runModal(TICKET);
  assert.ok(html.includes('cop-badge'), html);
  assert.ok(html.includes('score 70/100 over 3 of the 4 checks'), html);
  assert.ok(html.includes('likely to be wicked out'), html);
  assert.ok(html.includes("the engine&#39;s bias") || html.includes("the engine's bias"), html);
});

test('the CHAT drawer trade card carries the review', () => {
  const html = runChatCard(TICKET);
  assert.ok(html.includes('cop-badge'), html);
  assert.ok(html.includes('score 70/100 over 3 of the 4 checks'), html);
  assert.ok(html.includes('likely to be wicked out'), html);
});

test('a ticket the bot could not review says so on BOTH confirm surfaces', () => {
  // Never silence: the Confirm button is live either way, so a block that
  // disappeared would leave the card in the state the co-pilot exists to
  // remove — an order one tap away with nothing said about what reviewed it.
  for (const [name, html] of [
    ['modal', runModal({ ...TICKET, copilot: null })],
    ['chat', runChatCard({ ...TICKET, copilot: null })],
  ]) {
    assert.ok(/did not review this ticket/.test(html), name + ': ' + html);
    assert.ok(!html.includes('cop-badge'), name);
  }
});

test('an older payload with no copilot field is the same absence, not a crash', () => {
  const bare = { ...TICKET };
  delete bare.copilot;
  assert.ok(/did not review this ticket/.test(runModal(bare)));
  assert.ok(/did not review this ticket/.test(runChatCard(bare)));
});

test('the chat card without the model says the PAGE could not render it', () => {
  // `index.html` loads `copilot-review-model.js` before `chat.js`; a blocked
  // script or a stale cache can still leave it absent, and the card must not
  // fall through to a badge of its own.
  const html = runChatCard(TICKET, null);
  assert.ok(/could not be rendered on this page/.test(html), html);
  assert.ok(!html.includes('cop-badge'), html);
});

test('ONE renderer: no surface spells a badge class or the footer itself', () => {
  // A byte-identical copy per surface agrees with every fixture and diverges
  // on the first edit to either, which is what a second answer looks like from
  // outside. The model is the only file allowed to name these.
  for (const [name, src] of [['dashboard.js', DASH], ['chat.js', CHAT]]) {
    const code = codeOnly(src);
    assert.ok(!/cop-badge--/.test(code), name + ' spells a co-pilot badge class');
    assert.ok(!code.includes('remain the authority'),
      name + ' spells the advisory footer instead of rendering the model\'s');
    assert.ok(!code.includes('Not checked —'),
      name + ' spells the coverage row instead of rendering the model\'s');
  }
});

test('both pages that can show a trade card load the model', () => {
  // `chat.js` ships on the landing page too, and its trade card renders
  // through the model — a page that loads one without the other shows the
  // could-not-render sentence to every proposer on it.
  for (const page of ['dashboard.html', 'index.html']) {
    const html = fs.readFileSync(path.join(PUB, page), 'utf8');
    assert.ok(/<script src="\/js\/copilot-review-model\.js\?v=\d+"/.test(html),
      page + ' does not load copilot-review-model.js');
  }
  // PRESENCE, not ORDER, and the first draft of this assertion got that wrong
  // and accused `dashboard.html`, which is correct. Both renderers read
  // `window.CopilotReviewModel` at CALL time — a click, long after every
  // deferred script has run — so document order decides nothing here. An
  // assertion that claims a check the product does not need is a check that
  // fails on correct code, which is the loud direction but still wrong.
});
