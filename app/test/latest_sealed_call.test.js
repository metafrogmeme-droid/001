'use strict';
/**
 * One real sealed call on the front door — and every way it could become a lie.
 *
 * The day-root strip proves a SET: every call sealed on a finished day folds
 * into one hash. This proves a single DECISION — symbol, direction, three
 * prices — with a hash fixed at the moment the call was made. That is the
 * sentence the hero actually makes, and it is the site's whole moat: nobody
 * can copy it without building the same machinery.
 *
 * Which is exactly why a fabricated-looking hash here is worse than no widget
 * at all. A visitor who copies it, checks it, and finds it does not verify
 * does not conclude "one card is broken" — they conclude the whole page is
 * theatre, including the parts that are true.
 *
 * So the contract is REAL OR NOTHING, the same one #proofStrip holds, and
 * almost every test below is a failure path that must render nothing. The
 * planted red herring is the important one: a body that looks like a receipt
 * in every respect except that its seal is not a sha256.
 */
const test = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const html = fs.readFileSync(
  path.join(__dirname, '..', 'public', 'index.html'), 'utf8');

const START = "(function () {\n  var box = document.getElementById('latestCall');";

function source() {
  const i = html.indexOf(START);
  assert.ok(i > 0, 'latest-call IIFE not found — did the block move?');
  return html.slice(i, html.indexOf('</script>', i));
}

async function run(payload, { ok = true, reject = false } = {}) {
  const els = {
    latestCall: { hidden: true },
    lcSym: { textContent: '' },
    lcDir: { textContent: '', className: '' },
    lcLevels: { textContent: '' },
    lcSeal: { textContent: '' },
    lcAgo: { textContent: '' },
    lcVerify: { href: '/provable' },
  };
  const ctx = {
    document: { getElementById: (id) => els[id] || null },
    window: {},
    Date,
    Math,
    Number,
    isFinite,
    encodeURIComponent,
    AbortSignal: { timeout: () => null },
    fetch: () => (reject
      ? Promise.reject(new Error('network down'))
      : Promise.resolve({ ok, json: () => Promise.resolve(payload) })),
  };
  vm.createContext(ctx);
  vm.runInContext(source(), ctx);
  for (let i = 0; i < 8; i++) await Promise.resolve();
  return els;
}

const SEAL = 'a3f'.repeat(21) + 'b';           // 64 hex
const GOOD = {
  kind: 'signal',
  seal: SEAL,
  seal_payload: '{"v":1}',
  current: {
    signal_key: 'sig:btc:1234', symbol: 'BTC/USDT', direction: 'LONG',
    entry_price: 64210, stop_loss: 63400, take_profit: 65900,
    confidence: 72, created_at: new Date(Date.now() - 3 * 60000).toISOString(),
  },
};

// ── the happy path ────────────────────────────────────────────────────────

test('a real receipt renders and reveals the card', async () => {
  const e = await run(GOOD);
  assert.strictEqual(e.latestCall.hidden, false);
  assert.strictEqual(e.lcSym.textContent, 'BTC/USDT');
  assert.strictEqual(e.lcDir.textContent, 'LONG');
  assert.strictEqual(e.lcSeal.textContent, SEAL);
  assert.match(e.lcLevels.textContent, /64210/);
  assert.match(e.lcLevels.textContent, /72%/);
});

test('the verify link points at the call, key-escaped', async () => {
  const e = await run(GOOD);
  assert.strictEqual(e.lcVerify.href, '/call/sig%3Abtc%3A1234');
});

test('direction is never coloured as profit', async () => {
  // LONG is not "winning". The outcome does not exist yet — that is the
  // entire claim — so a green LONG would assert the one thing this card
  // exists to prove nobody knows.
  const e = await run(GOOD);
  assert.strictEqual(e.lcDir.className, 'muted');
  assert.ok(!/up|pos|green|win/i.test(e.lcDir.className));
});

// ── real or nothing ───────────────────────────────────────────────────────

test('a dead feed leaves the card hidden', async () => {
  assert.strictEqual((await run(null, { reject: true })).latestCall.hidden, true);
});

test('a non-200 leaves it hidden', async () => {
  assert.strictEqual((await run(GOOD, { ok: false })).latestCall.hidden, true);
});

test('a 404 "no sealed call yet" renders nothing, not an empty receipt', async () => {
  const e = await run({ error: 'No sealed call yet' }, { ok: false });
  assert.strictEqual(e.latestCall.hidden, true);
  assert.strictEqual(e.lcSeal.textContent, '');
});

test('THE RED HERRING: a receipt-shaped body whose seal is not a sha256', async () => {
  // Everything else is present and plausible — symbol, direction, prices, a
  // key. Only the hash is wrong. This is the shape that would ship a
  // confident, checkable, FALSE claim, and the only defence is refusing to
  // believe a seal that is not 64 hex characters.
  for (const seal of ['', null, undefined, 'pending', 'A'.repeat(64),
    'a'.repeat(63), 'a'.repeat(65), 'z'.repeat(64), 0, SEAL + ' ']) {
    const e = await run({ ...GOOD, seal });
    assert.strictEqual(e.latestCall.hidden, true, `seal=${JSON.stringify(seal)}`);
    assert.strictEqual(e.lcSeal.textContent, '', 'and nothing was written');
  }
});

test('a receipt with no key cannot be offered for verification', async () => {
  // The card's whole promise is "check this yourself". Without a key the
  // verify link goes nowhere, so the card must not appear at all.
  for (const k of [undefined, null, '']) {
    const e = await run({ ...GOOD, current: { ...GOOD.current, signal_key: k } });
    assert.strictEqual(e.latestCall.hidden, true, String(k));
  }
});

test('a receipt missing its symbol or direction is refused', async () => {
  for (const patch of [{ symbol: '' }, { symbol: null }, { direction: '' },
    { direction: null }]) {
    const e = await run({ ...GOOD, current: { ...GOOD.current, ...patch } });
    assert.strictEqual(e.latestCall.hidden, true, JSON.stringify(patch));
  }
});

test('a body with no current block is refused', async () => {
  for (const bad of [{ seal: SEAL }, { seal: SEAL, current: null }, {}, null]) {
    assert.strictEqual((await run(bad)).latestCall.hidden, true, JSON.stringify(bad));
  }
});

// ── absent levels are omitted, never printed as zero ──────────────────────

test('an unreadable level is left out, and does not blank the others', async () => {
  // Omit, not guard: one missing price must not cost the card, and must not
  // print as `stop 0` — which reads as a stop at zero, the most dangerous
  // number on the row.
  const e = await run({
    ...GOOD, current: { ...GOOD.current, stop_loss: null, take_profit: 0 },
  });
  assert.strictEqual(e.latestCall.hidden, false, 'the card survives');
  assert.match(e.lcLevels.textContent, /64210/, 'entry still shown');
  assert.ok(!/stop/.test(e.lcLevels.textContent), 'absent stop is omitted');
  assert.ok(!/\b0\b/.test(e.lcLevels.textContent), 'and never rendered as 0');
});

test('an absent confidence is omitted rather than shown as 0%', async () => {
  for (const c of [null, undefined, 0, 'n/a', NaN]) {
    const e = await run({ ...GOOD, current: { ...GOOD.current, confidence: c } });
    assert.ok(!/%/.test(e.lcLevels.textContent), `confidence=${String(c)}`);
  }
});

test('an undateable call shows no age rather than "0m"', async () => {
  for (const t of [null, undefined, 'soon', '']) {
    const e = await run({ ...GOOD, current: { ...GOOD.current, created_at: t } });
    assert.strictEqual(e.lcAgo.textContent, '', String(t));
    assert.strictEqual(e.latestCall.hidden, false, 'but the receipt still shows');
  }
});

// ── the markup ships hidden ───────────────────────────────────────────────

test('the card ships hidden so nothing flashes before the fetch resolves', () => {
  const markup = html.slice(html.indexOf('id="latestCall"'), html.indexOf('id="latestCall"') + 400);
  assert.match(markup, /hidden/);
  assert.ok(!/[0-9a-f]{64}/.test(markup), 'no placeholder hash in the markup');
  assert.ok(!/—/.test(markup.slice(0, 200)), 'no em-dash placeholder row');
});

test('no dollar figure reaches this public surface', () => {
  const i = html.indexOf('id="latestCall"');
  const block = html.slice(i, html.indexOf('</div>\n  <div class="feature-grid', i));
  assert.ok(!/\$\s?\d/.test(block));
});
