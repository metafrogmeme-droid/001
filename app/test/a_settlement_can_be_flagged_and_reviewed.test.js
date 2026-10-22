'use strict';
/**
 * Flags and reviews on the Daily Duel (lib/duel_review.js, the design's §3.5).
 *
 * A player may flag their own settlement once, within the check window; the
 * flag holds the call's chips until the window closes and queues the
 * settlement for the operator. The operator's decision (stands, push or
 * corrected) is a receipt of its own (v9 duel_review), folded into its day's
 * root, and the settlement it names is never rewritten. lib/duel.js verdictOf
 * applies the decision wherever a stored call is scored, so the card, the
 * record and the stack agree.
 *
 * Every rule is driven both ways: the window's edge, a second flag, an
 * unsettled or unpriced call, a decision with no evidence, a correction to
 * the outcome already given, a second review, and the operator gate, which is
 * read from the database (signed in is not the operator).
 */
process.env.JWT_SECRET = process.env.JWT_SECRET || 'j'.repeat(64);
process.env.BOT_SYNC_SECRET = process.env.BOT_SYNC_SECRET || 'b'.repeat(64);
delete process.env.DATABASE_URL;
delete process.env.FLOVIA_KEY;
delete process.env.FOOTBALL_DATA_TOKEN;

const test = require('node:test');
const assert = require('node:assert');
const crypto = require('node:crypto');
const http = require('node:http');
const express = require('express');

const review = require('../lib/duel_review');
const chips = require('../lib/duel_chips');
const duel = require('../lib/duel');
const svc = require('../lib/duel_service');
const receipts = require('../lib/duel_receipts');
const sealRoots = require('../lib/seal_roots');
const { setTickerFetcher } = require('../lib/tickers');
const { setCandleFetcher } = require('../lib/candles');
const { pool } = require('../db');

const sha256 = (s) => crypto.createHash('sha256').update(s, 'utf8').digest('hex');
const H = 3600 * 1000;
const PRICES = {
  BTCUSDT: { price: 60000, volume: 900 }, ETHUSDT: { price: 3000, volume: 800 }, SOLUSDT: { price: 150, volume: 700 },
  BNBUSDT: { price: 600, volume: 600 }, XRPUSDT: { price: 0.5, volume: 500 }, ARBUSDT: { price: 1.2, volume: 50 },
};
setTickerFetcher(async () => PRICES);
setCandleFetcher(async () => []);

// ── the rules, alone ────────────────────────────────────────────────────────

const SETTLED = '2026-10-10T12:00:00.000Z';
const settledPick = (over = {}) => ({ id: 1, round_id: 1, pick: 'long', entry_price: 100, settle_price: 90, settle_state: 'settled',
  settled_at: SETTLED, settle_disputed: null, flagged_at: null, review_state: null, ...over });
const round = { id: 1, day: '2026-10-09', kind: 'direction', agent_direction: 'short', odds: '{"long":1.9,"short":1.9}' };

test('a settlement may be flagged once, by its player, inside the window, and only once it is priced', () => {
  const at = (ms) => new Date(Date.parse(SETTLED) + ms);
  assert.deepStrictEqual(review.canFlag(settledPick(), at(H)), { ok: true });
  assert.deepStrictEqual(review.canFlag(settledPick(), at(review.WINDOW_MS)), { ok: true }, 'the window\'s last instant is inside it');
  assert.strictEqual(review.canFlag(settledPick(), at(review.WINDOW_MS + 1)).reason, 'window_closed');
  assert.strictEqual(review.canFlag(settledPick({ flagged_at: SETTLED }), at(H)).reason, 'flagged');
  assert.strictEqual(review.canFlag(settledPick({ review_state: 'stands' }), at(H)).reason, 'reviewed');
  assert.strictEqual(review.canFlag(settledPick({ settle_state: null }), at(H)).reason, 'not_settled');
  assert.match(review.canFlag(settledPick({ settle_state: 'unresolved' }), at(H)).error, /never priced/);
  assert.strictEqual(review.canFlag(undefined, at(H)).status, 404, 'no call, nothing to flag');
  assert.strictEqual(review.WINDOW_MS, chips.HOLD_MS, 'one check window for the flag and the hold');
});

test('the queue holds what is flagged or disputed, settled and not reviewed', () => {
  assert.strictEqual(review.needsReview(settledPick({ flagged_at: SETTLED })), true);
  assert.strictEqual(review.needsReview(settledPick({ settle_disputed: 1 })), true);
  assert.strictEqual(review.needsReview(settledPick({ settle_disputed: 0 })), false, 'agreement is not a dispute');
  assert.strictEqual(review.needsReview(settledPick()), false);
  assert.strictEqual(review.needsReview(settledPick({ flagged_at: SETTLED, review_state: 'push' })), false);
  assert.strictEqual(review.needsReview(settledPick({ flagged_at: SETTLED, settle_state: 'unresolved' })), false);
});

test('a decision needs a queued settlement, a word the rules know, evidence, and a correction to another outcome', () => {
  const p = settledPick({ flagged_at: SETTLED });
  const now = new Date(Date.parse(SETTLED) + H);
  assert.deepStrictEqual(review.checkDecision(round, p, 'stands', null, 'checked the venue candle', now), { ok: true, outcome: null });
  assert.deepStrictEqual(review.checkDecision(round, p, 'corrected', 'up', 'venue candle was a wick', now), { ok: true, outcome: 'up' });
  assert.strictEqual(review.checkDecision(round, p, 'corrected', 'down', 'x', now).reason, 'outcome', 'down is what it settled at');
  assert.strictEqual(review.checkDecision(round, p, 'corrected', 'sideways', 'x', now).reason, 'outcome');
  assert.strictEqual(review.checkDecision(round, p, 'push', 'up', 'x', now).reason, 'outcome', 'only a correction names an outcome');
  assert.strictEqual(review.checkDecision(round, p, 'maybe', null, 'x', now).reason, 'decision');
  assert.strictEqual(review.checkDecision(round, p, 'stands', null, '', now).reason, 'evidence');
  assert.strictEqual(review.checkDecision(round, settledPick(), 'stands', null, 'x', now).reason, 'not_queued');
  assert.strictEqual(review.checkDecision(round, p, 'stands', null, 'x', new Date(Date.parse(SETTLED) + review.REVIEW_MAX_MS + 1)).reason, 'too_late');
  assert.strictEqual(review.evidenceOf('  a\nb\u0000c  '), 'a b c');
  assert.strictEqual(review.evidenceOf('x'.repeat(900)).length, review.EVIDENCE_MAX);
});

test('a review decides the verdict everywhere it is read: stands, push, corrected', () => {
  const wrong = settledPick();
  assert.strictEqual(duel.verdictOf(round, wrong).state, 'wrong');
  assert.strictEqual(duel.verdictOf(round, { ...wrong, review_state: 'stands' }).state, 'wrong');
  assert.deepStrictEqual(duel.verdictOf(round, { ...wrong, review_state: 'push' }),
    { state: 'push', marks: 0, beat_agent: false, reviewed: 'push' });
  const corrected = duel.verdictOf(round, { ...wrong, review_state: 'corrected', review_outcome: 'up' });
  assert.deepStrictEqual([corrected.state, corrected.marks, corrected.beat_agent], ['correct', 2, true],
    'corrected to UP, a LONG against the Claw\'s SHORT beats it');
  assert.strictEqual(duel.verdictOf(round, { ...wrong, review_state: 'corrected', review_outcome: 'nonsense' }).state, 'wrong',
    'a stored outcome the category does not know corrects nothing');
  const entries = duel.scoreEntries([round], [{ ...wrong, review_state: 'corrected', review_outcome: 'up' }]);
  assert.deepStrictEqual([entries[0].state, entries[0].outcome], ['correct', 'up'], 'the record reads the review');
  const odd = duel.scoreEntries([round], [{ ...wrong, review_state: 'corrected', review_outcome: 'nonsense' }]);
  assert.deepStrictEqual([odd[0].state, odd[0].outcome], ['wrong', 'down'],
    'a correction nothing applied is not shown as the outcome either');
  // Chips follow: corrected to a win pays; a push returns.
  const c = { ...wrong, chips: 50 };
  assert.deepStrictEqual(chips.pickChips({ ...c, review_state: 'corrected', review_outcome: 'up' }, round, Date.parse(SETTLED) + H).payout, 45);
  assert.strictEqual(chips.pickChips({ ...c, review_state: 'push' }, round, Date.parse(SETTLED) + H).state, 'returned');
  // A flag holds the chips until the window closes; a review releases them at once.
  assert.strictEqual(chips.pickChips({ ...c, flagged_at: SETTLED }, round, Date.parse(SETTLED) + H).state, 'held');
  assert.strictEqual(chips.pickChips({ ...c, flagged_at: SETTLED }, round, Date.parse(SETTLED) + review.WINDOW_MS + 1).state, 'lost');
  assert.strictEqual(chips.pickChips({ ...c, flagged_at: SETTLED, review_state: 'stands' }, round, Date.parse(SETTLED) + H).state, 'lost');
});

test('the flag button is drawn on the website only, and a review line in every surface', () => {
  const R = require('../public/js/duel.js');
  const r = (my_call) => ({ id: 5, my_call });
  assert.match(R.reviewHtml(r({ flaggable: true }), null, true), /class="d-flag" data-flag="5"/);
  for (const links of ['verify', false, undefined]) {
    assert.strictEqual(R.reviewHtml(r({ flaggable: true }), null, links), '', `no flag where nothing answers it: ${links}`);
  }
  assert.strictEqual(R.reviewHtml(r({ flaggable: false }), null, true), '');
  const seal = 'f'.repeat(64);
  const pi = R.reviewHtml(r({ review: { state: 'push', outcome: null, seal } }), null, 'verify', 'Check');
  assert.match(pi, /^<span class="d-note d-review">Reviewed: no result <button/, 'no result, and no claim about chips a call may not carry');
  assert.match(pi, new RegExp(`data-receipt="review:${seal}"`), 'the Pi app checks it in place');
  assert.match(R.reviewHtml(r({ review: { state: 'stands', seal } }), null, true), new RegExp(`href="/call/review:${seal}"`));
  assert.match(R.reviewHtml(r({ flagged: true, flaggable: false }), null, true), /Flagged: waiting for a review/);
});

// ── through the routes ──────────────────────────────────────────────────────

let server, base;
function req(method, path, { token, body } = {}) {
  return new Promise((resolve, reject) => {
    const data = body === undefined ? null : JSON.stringify(body);
    const r = http.request(`${base}${path}`, { method, headers: {
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...(data ? { 'Content-Type': 'application/json', 'Content-Length': Buffer.byteLength(data) } : {}) } }, (res) => {
      let raw = ''; res.on('data', (c) => { raw += c; });
      res.on('end', () => { let json = null; try { json = JSON.parse(raw); } catch (e) { /* none */ } resolve({ status: res.statusCode, body: json }); });
    });
    r.on('error', reject); if (data) r.write(data); r.end();
  });
}
const T = {};
test.before(async () => {
  const app = express();
  app.use(express.json());
  app.use('/api/auth', require('../auth').router);
  app.use('/api/duel', require('../routes/duel'));
  await new Promise((resolve) => { server = app.listen(0, '127.0.0.1', () => { base = `http://127.0.0.1:${server.address().port}`; resolve(); }); });
  for (const who of ['player', 'operator']) {
    const r = await req('POST', '/api/auth/register', { body: { email: `review-${who}@example.com`, password: 'longenough1' } });
    assert.ok(r.body && r.body.token, JSON.stringify(r.body));
    T[who] = r.body.token;
    T[who + '_id'] = pool.users.find((u) => u.email === `review-${who}@example.com`).id;
  }
  pool.users.find((u) => u.id === T.operator_id).plan = 'admin';
});
test.after(() => { setTickerFetcher(null); setCandleFetcher(null); delete process.env.DUEL_CHIPS_ENABLED; if (server) server.close(); });

test('flag, queue, review, receipt and root: end to end, with the operator read from the database', async () => {
  process.env.DUEL_CHIPS_ENABLED = '1';
  const now = new Date();
  const rounds = await svc.ensureRounds(duel.dayKey(now), now);
  const dir = rounds.find((r) => duel.kindOf(r) === 'direction');
  const put = await req('POST', '/api/duel/pick', { token: T.player, body: { round_id: dir.id, pick: 'long', chips: 50 } });
  assert.strictEqual(put.status, 200, JSON.stringify(put.body));
  // Not settled yet: nothing to flag.
  const early = await req('POST', '/api/duel/flag', { token: T.player, body: { round_id: dir.id } });
  assert.deepStrictEqual([early.status, early.body.reason], [409, 'not_settled']);

  // Settle it as a loss (the market fell 5%), written an hour ago.
  const p = pool.duelPicks.find((x) => x.seal === put.body.seal);
  await svc.writeSettlement(dir, p, 'settled', Number(p.entry_price) * 0.95, null, new Date(now.getTime() - H));
  assert.strictEqual(JSON.parse(p.settle_payload).result, 'wrong');
  const settlePayload = p.settle_payload;

  // The operator queue is the operator's: a player is refused, by the database's word.
  assert.strictEqual((await req('GET', '/api/duel/review', { token: T.player })).status, 403);
  let q = await req('GET', '/api/duel/review', { token: T.operator });
  assert.strictEqual(q.status, 200);
  assert.ok(!q.body.items.some((i) => i.pick_id === p.id), 'an unflagged, undisputed settlement is not queued');

  // The player flags it: queued, and the chips held.
  const flag = await req('POST', '/api/duel/flag', { token: T.player, body: { round_id: dir.id } });
  assert.strictEqual(flag.status, 200, JSON.stringify(flag.body));
  const again = await req('POST', '/api/duel/flag', { token: T.player, body: { round_id: dir.id } });
  assert.deepStrictEqual([again.status, again.body.reason], [409, 'flagged']);
  const other = await req('POST', '/api/duel/flag', { token: T.operator, body: { round_id: dir.id } });
  assert.strictEqual(other.status, 404, 'nobody flags a call they did not make');
  let stack = await svc.stackFor(T.player_id, now);
  assert.deepStrictEqual([stack.held, stack.net], [50, 0], 'flagged: held, neither won nor lost');
  q = await req('GET', '/api/duel/review', { token: T.operator });
  const item = q.body.items.find((i) => i.pick_id === p.id);
  assert.ok(item, JSON.stringify(q.body));
  assert.deepStrictEqual([item.flagged, item.outcome, item.result, item.chips], [true, 'down', 'wrong', 50]);
  assert.deepStrictEqual(item.outcomes, ['up', 'down', 'flat']);
  for (const k of ['user_id', 'email', 'handle']) assert.ok(!(k in item), `the queue names no player: ${k}`);

  // The operator decides: refused without evidence, refused for a player, then corrected to UP.
  assert.deepStrictEqual((await req('POST', '/api/duel/review', { token: T.operator,
    body: { pick_id: p.id, decision: 'corrected', outcome: 'up', evidence: '' } })).body.reason, 'evidence');
  assert.strictEqual((await req('POST', '/api/duel/review', { token: T.player,
    body: { pick_id: p.id, decision: 'corrected', outcome: 'up', evidence: 'mine' } })).status, 403);
  const done = await req('POST', '/api/duel/review', { token: T.operator,
    body: { pick_id: p.id, decision: 'corrected', outcome: 'up', evidence: 'The venue candle printed a wick the index never traded.' } });
  assert.strictEqual(done.status, 200, JSON.stringify(done.body));
  const second = await req('POST', '/api/duel/review', { token: T.operator,
    body: { pick_id: p.id, decision: 'stands', evidence: 'again' } });
  assert.strictEqual(second.status, 409, 'a settlement is reviewed once');

  // The settlement's receipt is untouched; the review is its own, and verifies.
  assert.strictEqual(p.settle_payload, settlePayload);
  const rp = JSON.parse(p.review_payload);
  assert.deepStrictEqual([rp.v, rp.kind, rp.decision, rp.outcome, rp.settle_seal, rp.call_seal],
    [9, 'duel_review', 'corrected', 'up', p.settle_seal, p.seal]);
  assert.strictEqual(sha256(p.review_payload), p.review_seal);
  const rr = await receipts.lookupDuel('review:' + p.review_seal);
  assert.strictEqual(rr.code, 200);
  assert.deepStrictEqual(rr.body.settlement, { key: 'settle:' + p.settle_seal, seal: p.settle_seal });
  const sr = await receipts.lookupDuel('settle:' + p.settle_seal);
  assert.deepStrictEqual(sr.body.review, { key: 'review:' + p.review_seal, seal: p.review_seal, decision: 'corrected' });
  assert.strictEqual(sr.body.flagged, true);
  const leaves = await sealRoots.sealsForDay(new Date(p.reviewed_at).toISOString().slice(0, 10));
  assert.ok(leaves.includes(p.review_seal), 'the review is a leaf in its day\'s root');

  // Every reading agrees: the record, the stack, the card.
  const rec = await svc.recordFor(T.player_id, now);
  const e = rec.entries.find((x) => Number(x.round_id) === Number(dir.id));
  assert.deepStrictEqual([e.state, e.outcome], ['correct', 'up']);
  assert.deepStrictEqual(e.chips, { chips: 50, odds: 1.9, state: 'won', payout: 45 });
  stack = await svc.stackFor(T.player_id, now);
  assert.deepStrictEqual([stack.held, stack.net], [0, 45]);
  const card = await svc.cardFor(T.player_id, now);
  const mine = card.rounds.find((r) => Number(r.id) === Number(dir.id)).my_call;
  assert.deepStrictEqual(mine.review, { state: 'corrected', outcome: 'up', seal: p.review_seal });
  assert.strictEqual(mine.flaggable, false);
  assert.strictEqual(card.rounds.find((r) => Number(r.id) === Number(dir.id)).my_result.state, 'correct');
});

test('a flag after the window is refused, and a disputed settlement is queued without one', async () => {
  const now = new Date();
  const rounds = await svc.ensureRounds(duel.dayKey(now), now);
  const r2 = rounds.filter((r) => duel.kindOf(r) === 'direction')[1];
  const put = await req('POST', '/api/duel/pick', { token: T.player, body: { round_id: r2.id, pick: 'short' } });
  assert.strictEqual(put.status, 200);
  const p = pool.duelPicks.find((x) => x.seal === put.body.seal);
  await svc.writeSettlement(r2, p, 'settled', Number(p.entry_price) * 0.9, null, new Date(now.getTime() - review.WINDOW_MS - 60000),
    { second: { read: true, provider: 'CoinGecko', price: Number(p.entry_price) * 1.1, source_at: now.toISOString() }, second_b: null, disputed: true });
  const late = await req('POST', '/api/duel/flag', { token: T.player, body: { round_id: r2.id } });
  assert.deepStrictEqual([late.status, late.body.reason], [409, 'window_closed']);
  const q = await req('GET', '/api/duel/review', { token: T.operator });
  const item = q.body.items.find((i) => i.pick_id === p.id);
  assert.ok(item && item.disputed === true && item.flagged === false, 'a dispute queues itself');
  // Evidence is sealed as one bounded line: blank is none, and a pasted block is folded.
  const blank = await req('POST', '/api/duel/review', { token: T.operator, body: { pick_id: p.id, decision: 'push', evidence: ' \n\t ' } });
  assert.deepStrictEqual([blank.status, blank.body.reason], [400, 'evidence'], 'whitespace is no evidence');
  const push = await req('POST', '/api/duel/review', { token: T.operator,
    body: { pick_id: p.id, decision: 'push', evidence: 'sources split by 20%\n' + 'x'.repeat(900) } });
  assert.strictEqual(push.status, 200);
  const ev = JSON.parse(p.review_payload).evidence;
  assert.ok(ev.startsWith('sources split by 20% xxx') && ev.length === review.EVIDENCE_MAX, ev.slice(0, 40));
  const rec = await svc.recordFor(T.player_id, now);
  assert.strictEqual(rec.entries.find((x) => Number(x.round_id) === Number(r2.id)).state, 'push', 'a push clears the marks');
});

test('two decisions sent at once: one is written, the other is refused by the guard, never both', async () => {
  const now = new Date();
  const rounds = await svc.ensureRounds(duel.dayKey(now), now);
  const r3 = rounds.filter((r) => duel.kindOf(r) === 'direction')[2];
  const put = await req('POST', '/api/duel/pick', { token: T.player, body: { round_id: r3.id, pick: 'long' } });
  assert.strictEqual(put.status, 200);
  const p = pool.duelPicks.find((x) => x.seal === put.body.seal);
  await svc.writeSettlement(r3, p, 'settled', Number(p.entry_price) * 0.9, null, new Date(now.getTime() - H));
  // Two flags at once: both pass the read, one write lands.
  const flags = await Promise.all([svc.flagSettlement(T.player_id, r3.id, now), svc.flagSettlement(T.player_id, r3.id, now)]);
  assert.deepStrictEqual(flags.map((f) => f.ok).sort(), [false, true], JSON.stringify(flags));
  assert.strictEqual(flags.find((f) => !f.ok).reason, 'flagged');
  const firstFlag = p.flagged_at;
  // Two decisions at once: the first written stands, the second never overwrites it.
  const out = await Promise.all([
    svc.writeReview(p.id, 'stands', null, 'first', now),
    svc.writeReview(p.id, 'push', null, 'second', now),
  ]);
  assert.deepStrictEqual(out.map((o) => o.ok), [true, false], JSON.stringify(out));
  assert.strictEqual(out[1].reason, 'reviewed');
  assert.deepStrictEqual([p.review_state, JSON.parse(p.review_payload).evidence, p.flagged_at], ['stands', 'first', firstFlag]);
});

test('the Pi app checks a review receipt in place and words its decision', async () => {
  const pr = require('../public/js/pi-receipt.js');
  const p = pool.duelPicks.find((x) => x.review_state === 'corrected');
  assert.ok(p, 'the end-to-end test left a corrected review');
  const key = 'review:' + p.review_seal;
  assert.ok(pr.KEY_RE.test(key), 'the app accepts the key the renderer links');
  // The app reads /api/call/<key>; answer it with the real receipt.
  const api = async () => {
    const found = await receipts.lookupDuel(key);
    return { ok: found.code === 200, status: found.code, body: found.body };
  };
  const res = await pr.check(api, key);
  assert.strictEqual(res.state, 'verified');
  const rows = Object.fromEntries(res.facts);
  assert.strictEqual(rows['What it is'], 'A review of your settlement');
  assert.strictEqual(rows.Decision, 'Corrected to UP');
  assert.match(rows.Evidence, /wick/);
  for (const [decision, text] of [['stands', 'The result stands'], ['push', 'No result: the call scores nothing']]) {
    assert.strictEqual(Object.fromEntries(pr.facts({ kind: 'duel_review', decision, outcome: null })).Decision, text);
  }
});
