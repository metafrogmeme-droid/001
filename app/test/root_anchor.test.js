'use strict';
// On-chain anchoring for the daily seal roots — Provable Calls' missing leg.
// A mirrored root proves nothing can be back-inserted; the Base block
// timestamp proves WHEN. Non-custodial to the letter: the server never signs,
// it only verifies — and when the chain cannot be read it answers UNKNOWN,
// never a guess.

const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

const { buildAnchorPlan, verifyAnchor, payloadFor } = require('../lib/root_anchor.js');
const read = (...p) => fs.readFileSync(path.join(__dirname, '..', ...p), 'utf8');

const DAY = '2026-07-25';
const ROOT = 'a'.repeat(64);
const TX = '0x' + 'b'.repeat(64);

// A fetch stub that answers the two RPC methods the verifier uses.
const rpc = (tx, block) => async (url, opts) => ({
  ok: true,
  json: async () => {
    const { method } = JSON.parse(opts.body);
    if (method === 'eth_getTransactionByHash') return { jsonrpc: '2.0', result: tx };
    if (method === 'eth_getBlockByHash') return { jsonrpc: '2.0', result: block };
    return { jsonrpc: '2.0', error: { code: -32601, message: 'nope' } };
  },
});

test('the payload is self-describing and byte-stable', () => {
  const p = payloadFor(DAY, ROOT);
  assert.equal(p, '0x' + Buffer.from(`RCROOT1:${DAY}:${ROOT}`, 'utf8').toString('hex'));
  assert.equal(Buffer.from(p.slice(2), 'hex').toString('utf8'), `RCROOT1:${DAY}:${ROOT}`);
});

test('the plan is a dry run that never asks for a key', () => {
  const plan = buildAnchorPlan(DAY, ROOT);
  assert.equal(plan.dry_run, true);
  assert.equal(plan.chain_id, 8453);
  assert.equal(plan.value, '0');
  assert.equal(plan.data, payloadFor(DAY, ROOT));
  assert.match(plan.non_custodial_note, /never holds a key and never sends/);
  assert.equal(buildAnchorPlan('bad', ROOT), null);
  assert.equal(buildAnchorPlan(DAY, 'xyz'), null);
});

test('a matching transaction verifies, with the block time attached', async () => {
  const v = await verifyAnchor(TX, DAY, ROOT, rpc(
    { hash: TX, blockHash: '0x1', input: payloadFor(DAY, ROOT), from: '0xabc' },
    { timestamp: '0x688a0d80' }));
  assert.equal(v.status, 'verified');
  assert.equal(v.from, '0xabc');
  assert.equal(v.block_time, new Date(0x688a0d80 * 1000).toISOString());
});

test('the calldata must EQUAL the payload — a buried payload is not an anchor', async () => {
  const buried = payloadFor(DAY, ROOT) + 'deadbeef';
  const v = await verifyAnchor(TX, DAY, ROOT, rpc(
    { hash: TX, blockHash: '0x1', input: buried, from: '0xabc' }, { timestamp: '0x1' }));
  assert.equal(v.status, 'mismatch');
});

test('a wrong root, a missing tx and an unmined tx each answer honestly', async () => {
  const wrong = await verifyAnchor(TX, DAY, 'c'.repeat(64), rpc(
    { hash: TX, blockHash: '0x1', input: payloadFor(DAY, ROOT) }, { timestamp: '0x1' }));
  assert.equal(wrong.status, 'mismatch');
  const missing = await verifyAnchor(TX, DAY, ROOT, rpc(null, null));
  assert.equal(missing.status, 'mismatch');
  assert.match(missing.reason, /no such transaction/);
  const unmined = await verifyAnchor(TX, DAY, ROOT, rpc({ hash: TX, blockHash: null }, null));
  assert.equal(unmined.status, 'unknown', 'an unmined tx is not a verdict either way');
});

test('an unreadable chain answers UNKNOWN — never a guess', async () => {
  const down = async () => { throw new Error('ECONNREFUSED'); };
  const v = await verifyAnchor(TX, DAY, ROOT, down);
  assert.equal(v.status, 'unknown');
  assert.match(v.reason, /ECONNREFUSED/);
});

test('the routes gate on chain verification, and the first anchor wins', () => {
  const src = read('routes', 'roots.js');
  assert.match(src, /verifyAnchor\(txHash, row\.day, row\.root\)/,
    'the submit route must verify against Base before recording anything');
  assert.match(src, /v\.status === 'unknown'/);
  assert.match(src, /WHERE day = \? AND anchor_tx IS NULL/,
    'an anchored root must be immutable, like the root itself');
  assert.match(src, /authMiddleware/, 'the submit route is rate-limitable per user');
});

test('the shim honours the anchor UPDATE with first-write-wins', () => {
  const db = read('db.js');
  assert.match(db, /UPDATE SEAL_ROOTS/);
  assert.match(db, /r\.day === params\[2\] && r\.anchor_tx == null/);
});

test('the verify endpoint is the public self-audit — cached only when verified', () => {
  const src = read('routes', 'roots.js');
  const at = src.indexOf("router.get('/verify/:day'");
  assert.ok(at > -1);
  const slice = src.slice(at, at + 1600);
  assert.match(slice, /status: 'unanchored'/, 'an unanchored day answers honestly, not 404');
  assert.match(slice, /verifyAnchor\(row\.anchor_tx, row\.day, row\.root\)/);
  assert.match(slice, /if \(v\.status === 'verified'\) _verifyCache\.set/,
    'caching an UNKNOWN answer would freeze a transient RPC failure into fact');
});

test('the call page states the on-chain leg — present or honestly absent', () => {
  const page = read('public', 'call.html');
  assert.match(page, /d\.anchor\.anchor_tx/);
  assert.match(page, /basescan\.org\/tx\//);
  // The leg moved into `anchorLeg(a, verdict)` so the receipt can re-check the
  // chain instead of asserting "This root is anchored on Base" from our own
  // column — hence `a.day` rather than `d.anchor.day`. The requirement is
  // unchanged and is now covered BEHAVIOURALLY in test/anchor_cell.test.js,
  // which drives anchorLeg in all four states and requires the payload in
  // every one; this line only keeps the string from vanishing entirely.
  assert.match(page, /RCROOT1:' \+ esc\(a\.day\) \+ ':' \+ esc\(a\.root\)/,
    'the exact payload must be shown so a human can compare calldata themselves');
  assert.match(page, /not yet anchored on-chain/,
    'an unanchored day must say so plainly on the receipt');
  assert.match(page, /RCAnchorCell\.anchorState\(/,
    'the receipt decides the anchor state some other way than the tested decider, '
    + 'so it can disagree with /roots about the same day');
  // The old "planned and will be stated here when live" promise is retired.
  assert.ok(!page.includes('is planned and will be stated here when live'),
    'anchoring is live — the page must state what IS, not what was planned');
  assert.match(page, /On-chain anchoring \(live\)/);
});

test('anchorFor carries the anchor leg to the receipt payload', () => {
  const src = read('lib', 'seal_roots.js');
  assert.match(src, /anchor_tx: rootRow\.anchor_tx \|\| null/);
  assert.match(src, /anchored_at: rootRow\.anchored_at \|\| null/);
});

test('the roots feed and page carry the anchor honestly', () => {
  assert.match(read('lib', 'seal_roots.js'), /anchor_tx, anchored_at FROM seal_roots/);

  // The cell moved OUT of roots.html into a pure, unit-tested renderer — see
  // public/js/anchor_cell.js and test/anchor_cell.test.js. It moved because
  // the page was painting the profit green straight from our own anchor_tx
  // column, asserting a fact about Base that nothing had re-checked. So this
  // now follows the seam rather than asserting the strings are still inline:
  // pointing it back at the page would either fail on the fix or, worse, pass
  // on a page that had reverted to the database claim.
  const page = read('public', 'roots.html');
  assert.match(page, /RCAnchorCell\.anchorCell\(/, 'the page no longer uses the tested renderer');
  assert.match(page, /api\/roots\/verify\//, 'the page never asks the chain');

  const cell = read('public', 'js', 'anchor_cell.js');
  assert.match(cell, /rt\.anchored/);
  assert.match(cell, /rt\.unanchored/, 'an unanchored root must say so plainly, never imply');
  assert.match(cell, /basescan\.org\/tx\//);

  const i18n = require('../public/js/i18n.js');
  // The two new states are translated too: an operator reading a non-English
  // page must be told "could not confirm" and "does NOT match" in their own
  // language, on the page whose entire job is not being taken on trust.
  for (const k of ['rt.anchored', 'rt.unanchored', 'rt.anchor_mismatch', 'rt.anchor_unverified']) {
    for (const l of i18n.LANGS) {
      assert.ok(String(i18n.STRINGS[k][l.code] || '').trim().length, `${k} missing ${l.code}`);
    }
  }
});
