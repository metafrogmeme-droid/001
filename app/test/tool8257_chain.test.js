'use strict';
/**
 * The ERC-8257 registration is claimed only after the CHAIN says so.
 *
 * WHAT WAS MISSING
 *
 * `tool8257.registrationCheck()` is careful and three-valued, and it compares
 * our computed manifest hash against `REGISTERED_MANIFEST_HASH` — an
 * environment variable WE set. Both sides of that comparison are ours. It
 * proves the operator typed the hash they meant to type; it proves nothing
 * about whether a transaction was ever sent, reached the registry, or carried
 * this manifest.
 *
 * That is the shape the daily seal roots had until 2026-08-25, and the shape
 * /roots had until 2026-08-25: a claim about a chain, substantiated from our
 * own record. This is the third instance in one week, which is why it is
 * pinned rather than merely fixed.
 *
 * WHY IT VERIFIES A TRANSACTION, NOT REGISTRY STATE
 *
 * Reading the ToolRegistry's state would need its READ ABI, and ERC-8257 is a
 * draft — this repo knows only the write signature it encodes itself. Inventing
 * a getter and reporting whatever came back would be the SIWF verifier mistake
 * again: a plausible call to an endpoint nobody read the spec for, wrong in a
 * way the surface cannot distinguish from right.
 *
 * So verification uses `eth_getTransactionByHash` alone — the same method
 * root_anchor.js relies on, zero new ABI knowledge. That is strictly weaker
 * than reading state: it proves correct submission, not that the record was
 * never superseded. The `reason` on a verified result says exactly that, so
 * the limitation travels with the claim instead of living only in a docstring.
 */

const test = require('node:test');
const assert = require('node:assert');

const { verifyRegistration, registeredTx, registeredChainId } =
  require('../lib/tool8257_chain.js');

const REGISTRY = '0x265BB2DBFC0A8165C9A1941Eb1372F349baD2cf1';
const TX = '0x' + 'a'.repeat(64);
const CALLDATA = '0xdeadbeef';
const PLAN = { registry: REGISTRY, calldata: CALLDATA };

/** An RPC stub answering the two methods the verifier uses. */
const rpc = (tx, block) => async (url, opts) => ({
  ok: true,
  json: async () => {
    const { method } = JSON.parse(opts.body);
    if (method === 'eth_getTransactionByHash') return { jsonrpc: '2.0', result: tx };
    if (method === 'eth_getBlockByHash') return { jsonrpc: '2.0', result: block };
    return { jsonrpc: '2.0', error: { code: -32601, message: 'nope' } };
  },
});

const mined = (over = {}) => ({
  hash: TX, blockHash: '0x1', to: REGISTRY, input: CALLDATA, from: '0xabc', ...over,
});

// ── the only route to "verified" ──────────────────────────────────────────

test('a matching transaction at the registry verifies, with the block time', async () => {
  const v = await verifyRegistration(TX, PLAN, 8453,
    rpc(mined(), { timestamp: '0x688a0d80' }));
  assert.equal(v.status, 'verified');
  assert.equal(v.from, '0xabc');
  assert.equal(v.block_time, new Date(0x688a0d80 * 1000).toISOString());
});

test('a verified result carries its own limitation', () => {
  // The claim is "submitted correctly", not "currently the live record". A
  // later re-registration could supersede it and this method would not know.
  // Saying so in the payload keeps the caveat attached to the claim rather
  // than stranded in a docstring nobody reads at the moment of trusting it.
  return verifyRegistration(TX, PLAN, 8453, rpc(mined(), { timestamp: '0x1' }))
    .then((v) => assert.match(v.reason, /not prove.*superseded/));
});

// ── mismatch: it exists and it is not this registration ───────────────────

test('a transaction to some OTHER contract is a mismatch', async () => {
  const v = await verifyRegistration(TX, PLAN, 8453,
    rpc(mined({ to: '0x1111111111111111111111111111111111111111' }), { timestamp: '0x1' }));
  assert.equal(v.status, 'mismatch');
  assert.match(v.reason, /not the ToolRegistry/);
});

test('the calldata must EQUAL the plan — a buried payload is not this registration', async () => {
  // A payload inside a larger call is a different transaction doing something
  // else that happens to mention ours. Same distinction root_anchor.js draws.
  const v = await verifyRegistration(TX, PLAN, 8453,
    rpc(mined({ input: CALLDATA + 'ff' }), { timestamp: '0x1' }));
  assert.equal(v.status, 'mismatch');
  assert.match(v.reason, /does not equal/);
});

test('a transaction that does not exist is a mismatch, not an outage', async () => {
  const v = await verifyRegistration(TX, PLAN, 8453, rpc(null, null));
  assert.equal(v.status, 'mismatch');
  assert.match(v.reason, /no such transaction/);
});

test('a malformed transaction hash is refused before any RPC call', async () => {
  let called = false;
  const spy = async () => { called = true; throw new Error('should not be reached'); };
  const v = await verifyRegistration('not-a-hash', PLAN, 8453, spy);
  assert.equal(v.status, 'mismatch');
  assert.equal(called, false, 'a malformed hash still hit the network');
});

// ── unknown: NOT a verdict ────────────────────────────────────────────────

test('an unmined transaction is unknown, not a rejection', async () => {
  const v = await verifyRegistration(TX, PLAN, 8453, rpc({ hash: TX, blockHash: null }, null));
  assert.equal(v.status, 'unknown');
  assert.match(v.reason, /not yet mined/);
});

test('an unreadable chain is unknown — never a guess either way', async () => {
  const v = await verifyRegistration(TX, PLAN, 8453, async () => { throw new Error('ECONNREFUSED'); });
  assert.equal(v.status, 'unknown');
  assert.match(v.reason, /ECONNREFUSED/);
});

test('an unreadable block is unknown, even though the tx matched', async () => {
  const v = await verifyRegistration(TX, PLAN, 8453, rpc(mined(), null));
  assert.equal(v.status, 'unknown');
  assert.match(v.reason, /block unreadable/);
});

test('OUR broken plan is unknown — never blamed on the transaction', async () => {
  // A missing or malformed plan is a fault on this side. Reporting it as a
  // mismatch would accuse a perfectly good registration of being wrong, and
  // send an operator to re-register over a bug in our own encoder.
  for (const bad of [null, {}, { registry: REGISTRY }, { registry: REGISTRY, calldata: 'zzz' },
                     { calldata: CALLDATA }]) {
    const v = await verifyRegistration(TX, bad, 8453, rpc(mined(), { timestamp: '0x1' }));
    assert.equal(v.status, 'unknown', `plan ${JSON.stringify(bad)} produced ${v.status}`);
  }
});

test('an unconfigured chain is unknown, not a mismatch', async () => {
  const v = await verifyRegistration(TX, PLAN, 999999, rpc(mined(), { timestamp: '0x1' }));
  assert.equal(v.status, 'unknown');
  assert.match(v.reason, /no RPC configured/);
});

test('a second RPC is tried before giving up', async () => {
  // One flaky endpoint must not read as an outage — the registration would
  // show unconfirmed on a page for as long as that provider was down.
  let n = 0;
  const flaky = async (url, opts) => {
    n += 1;
    if (n === 1) throw new Error('first endpoint down');
    return rpc(mined(), { timestamp: '0x1' })(url, opts);
  };
  const v = await verifyRegistration(TX, PLAN, 8453, flaky);
  assert.equal(v.status, 'verified');
  assert.ok(n >= 2, 'the fallback endpoint was never tried');
});

// ── the operator-set inputs ───────────────────────────────────────────────

test('only a well-formed transaction hash is accepted from the environment', () => {
  const before = process.env.REGISTERED_TOOL_TX;
  try {
    for (const bad of ['', 'nope', '0x123', 'a'.repeat(66)]) {
      process.env.REGISTERED_TOOL_TX = bad;
      assert.equal(registeredTx(), null, `accepted ${bad}`);
    }
    process.env.REGISTERED_TOOL_TX = TX.toUpperCase();
    assert.equal(registeredTx(), TX, 'a valid hash is not normalised to lower case');
  } finally {
    if (before === undefined) delete process.env.REGISTERED_TOOL_TX;
    else process.env.REGISTERED_TOOL_TX = before;
  }
});

test('an unknown chain id falls back to Base rather than to nothing', () => {
  const before = process.env.REGISTERED_TOOL_CHAIN_ID;
  try {
    process.env.REGISTERED_TOOL_CHAIN_ID = '999999';
    assert.equal(registeredChainId(), 8453);
    process.env.REGISTERED_TOOL_CHAIN_ID = '1';
    assert.equal(registeredChainId(), 1);
    delete process.env.REGISTERED_TOOL_CHAIN_ID;
    assert.equal(registeredChainId(), 8453, 'the default is not the recommended chain');
  } finally {
    if (before === undefined) delete process.env.REGISTERED_TOOL_CHAIN_ID;
    else process.env.REGISTERED_TOOL_CHAIN_ID = before;
  }
});

// ── the route ─────────────────────────────────────────────────────────────

test('the status route exists and never claims registration from our own env', () => {
  const fs = require('node:fs');
  const path = require('node:path');
  const src = fs.readFileSync(path.join(__dirname, '..', 'routes', 'tool8257.js'), 'utf8');

  assert.match(src, /router\.get\('\/api\/tool\/registration'/,
    'there is no public self-audit for the registration');
  assert.match(src, /chain\.verifyRegistration\(/,
    'the route does not ask the chain');
  assert.match(src, /status: 'not_submitted'/,
    'a registration that was never sent has no honest state to report');
  // The verdict must come from the chain call, not from the env-var check.
  assert.match(src, /status: v\.status/,
    'the reported status is computed some way other than from the chain verdict');
});

test('an unreadable status is 503, never a confident "not registered"', () => {
  const fs = require('node:fs');
  const path = require('node:path');
  const src = fs.readFileSync(path.join(__dirname, '..', 'routes', 'tool8257.js'), 'utf8');
  const at = src.indexOf("router.get('/api/tool/registration'");
  const body = src.slice(at, src.indexOf("router.post('/api/tool/invoke'", at));
  assert.match(body, /503/);
  assert.match(body, /registration_status_unavailable/);
  assert.ok(!/status: 'not_submitted'[\s\S]{0,200}catch/.test(body),
    'a failure path answers with a verdict shaped like a measurement');
});

// ── the sender is the creator, or the record misattributes itself ─────────

const CREATOR = '0x6649e7eadd90113c26a97f2cbadb2c6c1a7e0924';

test('a registration from the WRONG wallet is a mismatch, not verified', async () => {
  // The manifest declares creatorAddress; the registry records msg.sender as
  // the creator. Send from another wallet and the on-chain record and the
  // document it points at disagree about who made it.
  //
  // The first version of verifyRegistration checked the destination and the
  // calldata and never looked at the sender — it would have answered
  // `verified` for exactly this. Same asymmetry this file's commit pointed out
  // in bot/proofofpnl/anchor.py, reproduced here in the opposite direction
  // within the hour.
  const v = await verifyRegistration(TX,
    { ...PLAN, creator: CREATOR },
    8453,
    rpc(mined({ from: '0xdeadbeefdeadbeefdeadbeefdeadbeefdeadbeef' }), { timestamp: '0x1' }));
  assert.equal(v.status, 'mismatch');
  assert.match(v.reason, /not the manifest's creatorAddress/);
});

test('the creator check is case-insensitive', async () => {
  // Checksummed vs lower-case is the same address, and refusing one would
  // reject a perfectly good registration over presentation.
  const v = await verifyRegistration(TX,
    { ...PLAN, creator: CREATOR.toUpperCase().replace('0X', '0x') },
    8453, rpc(mined({ from: CREATOR }), { timestamp: '0x1' }));
  assert.equal(v.status, 'verified');
});

test('an unresolvable creator does not block a good registration', async () => {
  // `creator` is unset in some configurations. Refusing to verify because we
  // could not resolve our OWN expectation would blame the chain for a gap on
  // this side — the same reason a malformed plan answers `unknown` rather than
  // `mismatch`. Absent expectation = that check is simply not made.
  for (const c of [null, undefined, '']) {
    const v = await verifyRegistration(TX, { ...PLAN, creator: c }, 8453,
      rpc(mined({ from: '0xdeadbeefdeadbeefdeadbeefdeadbeefdeadbeef' }), { timestamp: '0x1' }));
    assert.equal(v.status, 'verified', `creator ${JSON.stringify(c)} blocked verification`);
  }
});

test('the route passes the manifest creator to the verifier', () => {
  const fs = require('node:fs');
  const path = require('node:path');
  const src = fs.readFileSync(path.join(__dirname, '..', 'routes', 'tool8257.js'), 'utf8');
  assert.match(src, /creatorAddress/,
    'the route never resolves the creator, so the sender is never checked');
  assert.match(src, /creator\s*\}?,?\s*chain_id\)|creator \}/,
    'the creator is resolved and then not passed to verifyRegistration');
});
