'use strict';
/**
 * The reason for a call is now inside the hash.
 *
 * "Every call is hashed before the market moves" was true of the NUMBERS and
 * not of the reason for them. `thesis` has been transmitted at decision time
 * and stored since the signal stream existed — `bot/utils/website_sync.py:328`
 * sends it in the same POST that gets sealed — and it is published by
 * `lib/public_signal.js`, `routes/signals.js` and `routes/copy.js`. It was
 * never in the sealed payload.
 *
 * In practice it could not change: the sync's `ON DUPLICATE KEY UPDATE` touches
 * only `status`, `pnl` and `resolved_at`. But nothing PROVED that to a reader.
 * An edit to the row would have been undetectable, and an unverifiable
 * narrative published beside an unforgeable receipt is precisely what this
 * product exists to make impossible — the reasoning was the one part of a
 * Provable Call you still had to take on trust.
 *
 * TWO THINGS THIS FILE EXISTS TO STOP.
 *
 *   1. v1 changing. `canonicalPayload` is the exact string every historical
 *      seal was computed over. One byte and every receipt ever issued stops
 *      verifying — not "needs re-sealing", STOPS VERIFYING, publicly, on the
 *      page whose whole job is proving nothing was rewritten. So its output is
 *      pinned literally here, not described.
 *   2. The outcome getting in. Both kinds must stay decision-time only.
 */

const test = require('node:test');
const assert = require('node:assert');
const crypto = require('node:crypto');

const {
  canonicalPayload, canonicalSignalPayload, numOrNull, sealOf, sealCall,
} = require('../lib/callseal');

const CALL = {
  signal_key: 'sig-1', symbol: 'BTC/USDT', direction: 'LONG',
  entry_price: 61000, stop_loss: 60000, take_profit: 63000,
  confidence: 0.72, pattern: 'liquidity_sweep', regime: 'trend',
  thesis: 'LONG bias | RSI 58 | Score 72% | Vol 1.8x avg',
  created_at: '2026-08-18T00:00:00.000Z',
};

// ── v1 is frozen ─────────────────────────────────────────────────────────────

test('v1 still produces the exact bytes every historical seal was computed over', () => {
  // Literal, not derived. A derived expectation would move WITH the code and
  // pin nothing — which is the entire failure mode for a wire contract.
  assert.strictEqual(canonicalPayload(CALL),
    '{"v":1,"signal_key":"sig-1","symbol":"BTC/USDT","direction":"LONG",'
    + '"entry_price":61000,"stop_loss":60000,"take_profit":63000,'
    + '"confidence":0.72,"pattern":"liquidity_sweep","regime":"trend",'
    + '"created_at":"2026-08-18T00:00:00.000Z"}');
});

test('v1 keeps its || 0 fallback byte-for-byte on an unreadable call', () => {
  // THE POPULATED FIXTURE ABOVE CANNOT CATCH A CHANGE TO THE FALLBACK.
  // `Number(61000) || 0` and `Number(61000) || null` emit identical bytes, so
  // rewriting v1's `|| 0` to `|| null` passed the literal pin untouched —
  // found by mutating it and watching nothing fail. A frozen contract has to
  // be frozen on the branch that is easy to "tidy up", which is the one nobody
  // has a fixture for.
  assert.strictEqual(canonicalPayload({ signal_key: 's', symbol: 'B', direction: 'L',
    created_at: '2026-08-18T00:00:00.000Z' }),
    '{"v":1,"signal_key":"s","symbol":"B","direction":"L","entry_price":0,'
    + '"stop_loss":0,"take_profit":0,"confidence":0,"pattern":null,'
    + '"regime":null,"created_at":"2026-08-18T00:00:00.000Z"}');
});

test('v1 carries no reasoning — that is why v4 exists', () => {
  assert.ok(!canonicalPayload(CALL).includes('thesis'));
});

// ── v4 seals the reasoning ───────────────────────────────────────────────────

test('v4 is a distinct KIND, not a reused number', () => {
  // `v` discriminates payload kinds: 1 signal, 2 arena_trade, 3 duel_pick.
  // Reusing 2 for this would have collided with arena receipts.
  const p = JSON.parse(canonicalSignalPayload(CALL));
  assert.strictEqual(p.v, 4);
  assert.strictEqual(p.kind, 'signal_call');
});

test('the reasoning is inside the hash', () => {
  const p = JSON.parse(canonicalSignalPayload(CALL));
  assert.strictEqual(p.thesis, CALL.thesis);
});

test('changing one character of the reasoning breaks the seal', () => {
  // THE WHOLE POINT. If this passes with a mutated thesis, the reasoning is
  // decorative and the claim is false.
  const a = sealCall(CALL);
  const b = sealCall({ ...CALL, thesis: CALL.thesis.replace('58', '59') });
  assert.notStrictEqual(a.seal, b.seal,
    'the seal is unchanged after editing the reasoning — it is not sealed');
});

test('a client re-derives the seal from the served payload alone', () => {
  // Exactly what call.html does with WebCrypto: hash the stored string
  // verbatim. No re-canonicalisation, so nothing to drift.
  const { seal_payload, seal } = sealCall(CALL);
  const derived = crypto.createHash('sha256').update(seal_payload, 'utf8').digest('hex');
  assert.strictEqual(derived, seal);
});

test('sealCall emits v4 now, and both kinds verify the same way', () => {
  const fresh = sealCall(CALL);
  assert.match(fresh.seal_payload, /^\{"v":4,"kind":"signal_call"/);
  // An old row keeps its v1 string; verification hashes the STORED payload, so
  // there is no migration and no reseal.
  const legacy = canonicalPayload(CALL);
  assert.strictEqual(sealOf(legacy),
    crypto.createHash('sha256').update(legacy, 'utf8').digest('hex'));
});

// ── absent is not zero, and not "" ───────────────────────────────────────────

test('an unreadable number seals as null in v4, where v1 sealed a measured zero', () => {
  // `Number(x) || 0` in v1 is the shape CLAUDE.md's table names, sitting in the
  // canonical contract. It cannot be fixed there without invalidating every
  // receipt; v4 is where the fix could go, so it went there.
  const blind = { ...CALL, confidence: null, entry_price: undefined };
  const v4 = JSON.parse(canonicalSignalPayload(blind));
  assert.strictEqual(v4.confidence, null);
  assert.strictEqual(v4.entry_price, null);
  const v1 = JSON.parse(canonicalPayload(blind));
  assert.strictEqual(v1.confidence, 0, 'v1 must keep its old behaviour byte-for-byte');
});

test('no recorded reasoning seals as null, not as an empty string', () => {
  // The sync coalesces a missing reasoning to "" upstream. "" asserts "the
  // reasoning was empty"; null says "none was recorded". Sealed permanently,
  // so the difference has to be made before the hash, not after.
  for (const empty of ['', null, undefined]) {
    const p = JSON.parse(canonicalSignalPayload({ ...CALL, thesis: empty }));
    assert.strictEqual(p.thesis, null, `thesis ${JSON.stringify(empty)} sealed as ${JSON.stringify(p.thesis)}`);
  }
});

test('numOrNull refuses every unreadable value it can be handed', () => {
  for (const bad of [null, undefined, '', NaN, Infinity, -Infinity, 'n/a', {}]) {
    assert.strictEqual(numOrNull(bad), null, `numOrNull(${String(bad)})`);
  }
  // And 0 is a real, measured zero — muting it would be the opposite error.
  assert.strictEqual(numOrNull(0), 0);
  assert.strictEqual(numOrNull('0'), 0);
  assert.strictEqual(numOrNull(0.72), 0.72);
});

// ── the outcome still never gets in ──────────────────────────────────────────

test('neither kind admits an outcome field', () => {
  const withOutcome = { ...CALL, pnl: 123, exit_price: 62000, status: 'WIN' };
  for (const [name, fn] of [['v1', canonicalPayload], ['v4', canonicalSignalPayload]]) {
    const keys = Object.keys(JSON.parse(fn(withOutcome)));
    for (const banned of ['pnl', 'exit_price', 'status', 'resolved_at', 'outcome']) {
      assert.ok(!keys.includes(banned),
        `${name} would seal ${banned} — "sealed before the outcome" collapses`);
    }
  }
});

test('the sync seals and stores the same thesis value', () => {
  // Two writes of one fact is two chances to disagree: the sealed string said
  // one thing and the column another, and only the column is displayed.
  const fs = require('node:fs');
  const path = require('node:path');
  const src = fs.readFileSync(path.join(__dirname, '..', 'routes', 'sync.js'), 'utf8');
  // Bound the slice FORWARD from `const fixed = {`. An unanchored search for
  // the closing marker found an earlier `ON DUPLICATE KEY` belonging to a
  // different query, giving a negative-length slice — so both assertions below
  // ran against "" and the test failed while the code was correct.
  const from = src.indexOf('const fixed = {');
  assert.ok(from > 0, 'the signal ingest no longer builds a `fixed` object');
  // ...and end it at `upserted++`, not at ON DUPLICATE KEY: the parameter
  // array — where fixed.thesis is actually passed — comes AFTER the SQL text,
  // so the shorter slice checked half the thing it claimed to.
  const to = src.indexOf('upserted++', from);
  assert.ok(to > from, 'the ingest loop no longer ends with upserted++');
  const block = src.slice(from, to);
  assert.match(block, /thesis: s\.thesis != null \? String\(s\.thesis\) : null/,
    'sync no longer normalises thesis into the sealed object');
  assert.match(block, /fixed\.thesis/,
    'the INSERT no longer stores fixed.thesis — the row and the seal can drift');
});

// ── the verify page ──────────────────────────────────────────────────────────

test('the drift check only compares fields the payload actually sealed', () => {
  // Without this guard, adding `thesis` to the comparison list would report
  // `undefined` against every pre-v4 row's stored reasoning and accuse EVERY
  // historical call of having been altered — on the page whose entire job is
  // proving nothing was. A checker that manufactures the accusation it exists
  // to prevent is worse than no checker.
  const fs = require('node:fs');
  const path = require('node:path');
  const html = fs.readFileSync(path.join(__dirname, '..', 'public', 'call.html'), 'utf8');
  const at = html.indexOf("['thesis', 'reasoning']");
  assert.ok(at > 0, 'the reasoning is not in the drift comparison at all');
  const block = html.slice(at, at + 700);
  assert.match(block, /if \(!\(f\[0\] in p\)\) return;/,
    'the drift loop compares fields the sealed payload may not contain');
});

test('the receipt shows the reasoning only when it is inside the hash', () => {
  // This pinned the SPELLING `'thesis' in p && p.thesis ?` and then blocked a
  // fix to the row it guards: `p.thesis` is truthy on calls where the model
  // gave no reason at all, because the bot prefixes a provenance tag to every
  // reasoning it stores (see test/thesis_prose.test.js). The claim worth
  // keeping is the `in` check — a v1 receipt sealed no thesis, so it gets no
  // Reasoning row and no unverifiable narrative — and that is what is asserted
  // now. What follows the `in` check is that other test's subject.
  const fs = require('node:fs');
  const path = require('node:path');
  const html = fs.readFileSync(path.join(__dirname, '..', 'public', 'call.html'), 'utf8');
  const at = html.indexOf("<span class=\"k\">Reasoning</span>");
  assert.ok(at > 0, 'the Reasoning row is gone from the receipt');
  const row = html.slice(Math.max(0, at - 400), at);
  assert.match(row, /'thesis' in p/,
    'the page would print a stored thesis for a v1 receipt that never sealed '
    + 'one — an unverifiable narrative on a verification page');
});

test('a sealed null renders as a dash, never as a measurement', () => {
  // v1 could not seal null: it coerced everything to 0 first, so no renderer
  // ever had to tell "zero" from "unknown". v4 can, which turns two latent
  // lines into live ones — `(null || 0) * 100` is a confident 0% confidence.
  const fs = require('node:fs');
  const path = require('node:path');
  const html = fs.readFileSync(path.join(__dirname, '..', 'public', 'call.html'), 'utf8');
  assert.ok(!/Math\.round\(\(p\.confidence \|\| 0\) \* 100\)/.test(html),
    'confidence is still rendered through `|| 0`, which prints an unmeasured '
    + 'call as 0% confident');
  assert.match(html, /var pct = function \(x\) \{ return \(x === null \|\| x === undefined\) \? '—'/);
  assert.match(html, /var val = function \(x\) \{ return \(x === null \|\| x === undefined\) \? '—'/);
});

test('the call route serves the reasoning it now seals', () => {
  const fs = require('node:fs');
  const path = require('node:path');
  const src = fs.readFileSync(path.join(__dirname, '..', 'routes', 'call.js'), 'utf8');
  assert.match(src, /take_profit, thesis, status/,
    'the signal query does not select thesis, so the drift check has nothing '
    + 'to compare the sealed value against');
  assert.match(src, /thesis: s\.thesis \|\| null,/);
});
