'use strict';
/**
 * Claw Guard v0: what a request DOES (a simulation on Base) beside what it
 * SAYS (the X-rays), joined into one plain-words verdict.
 *
 * The simulation answers are real: `eth_simulateV1` replies recorded from
 * https://mainnet.base.org on 22 Oct 2026 (test/fixtures/claw_guard/), for the
 * request `txsim-model.js` builds. A hand-written answer would test the
 * reader against the shape its author expected, not the one Base sends.
 */
process.env.JWT_SECRET = process.env.JWT_SECRET || 'j'.repeat(64);
delete process.env.DATABASE_URL;

const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

const SIM = require('../public/js/txsim-model.js');
const G = require('../public/js/guard-model.js');
const X = require('../public/js/txray-model.js');
const SR = require('../public/js/sigray-model.js');
const { simulate } = require('../lib/tx_simulate');
const { CHAINS } = require('../lib/nft');

const fixture = (name) => JSON.parse(fs.readFileSync(
  path.join(__dirname, 'fixtures', 'claw_guard', `sim_${name}.json`), 'utf8'));
const ids = (list) => list.map((f) => f.id).sort();

test('the request is the standard method, on the latest block, for one call from the account', () => {
  const tx = { from: '0x' + 'AB'.repeat(20), to: '0x' + 'cd'.repeat(20), data: '0x095ea7b3', value: '10' };
  const body = SIM.request(tx);
  assert.strictEqual(body.method, 'eth_simulateV1');
  assert.strictEqual(body.params[1], 'latest');
  assert.strictEqual(body.params[0].traceTransfers, true);
  assert.strictEqual(body.params[0].validation, false);
  assert.deepStrictEqual(body.params[0].blockStateCalls[0].calls,
    [{ from: '0x' + 'ab'.repeat(20), to: '0x' + 'cd'.repeat(20), data: '0x095ea7b3', value: '0xa' }]);
  // Without the account there is no question to ask.
  for (const bad of [{ ...tx, from: '' }, { ...tx, to: 'x' }, { ...tx, data: '0x1' }, { ...tx, value: '-1' }, { ...tx, value: 'abc' }]) {
    assert.strictEqual(SIM.request(bad), null, JSON.stringify(bad));
  }
});

test('Base\'s own answers read as what left, what arrived and what was granted', () => {
  const at = (name) => { const f = fixture(name); return SIM.read(f.answer, f.tx.from); };
  const send = at('native_send');
  assert.strictEqual(send.ok, true);
  assert.strictEqual(send.status, 'success');
  assert.match(send.block, /^[0-9]+$/);
  assert.deepStrictEqual(send.changes, [{ kind: 'native_out', amount: '10000000000000000',
    counterparty: '0x' + '11'.repeat(20) }]);

  const token = at('token_send');
  assert.deepStrictEqual(token.changes.map((c) => [c.kind, c.amount]), [['token_out', '1000000']]);
  assert.deepStrictEqual(ids(token.flags), ['sim_assets_out']);

  const approve = at('unlimited_approve');
  assert.strictEqual(approve.changes[0].kind, 'approval');
  assert.strictEqual(approve.changes[0].amount, 'UNLIMITED');
  assert.deepStrictEqual(ids(approve.flags), ['unlimited_approval']);

  const all = at('approval_for_all');
  assert.deepStrictEqual(all.changes, [{ kind: 'approval_for_all', token: fixture('approval_for_all').tx.to,
    operator: '0x' + '11'.repeat(20), on: true }]);
  assert.deepStrictEqual(ids(all.flags), ['approval_for_all']);

  const bounded = at('bounded_approve');
  assert.deepStrictEqual(bounded.changes.map((c) => [c.kind, c.amount, c.unlimited]), [['approval', '1000000', false]]);
  assert.deepStrictEqual(bounded.flags, [], 'a bounded approval is listed, not flagged as unlimited');

  const nft = at('nft_send');
  assert.deepStrictEqual(nft.changes.map((c) => c.kind), ['nft_out'], 'an ERC-721 transfer is an item, not a token amount');
  assert.match(nft.changes[0].id, /^[0-9]+$/);
  assert.strictEqual(nft.changes[0].token, fixture('nft_send').tx.to);
  assert.deepStrictEqual(ids(nft.flags), ['sim_assets_out']);

  const failed = at('reverted');
  assert.strictEqual(failed.status, 'reverted');
  assert.deepStrictEqual(failed.changes, []);
  assert.deepStrictEqual(ids(failed.flags), ['sim_reverted']);
});

test('the block named is the state the call ran on, one before the simulated block', () => {
  const f = fixture('token_send');
  const simulated = BigInt(f.answer.result[0].number);
  assert.strictEqual(SIM.read(f.answer, f.tx.from).block, (simulated - 1n).toString());
});

test('an answer read for another account lists nothing as its own, and counts the events', () => {
  const f = fixture('token_send');
  const r = SIM.read(f.answer, '0x' + '99'.repeat(20));
  assert.deepStrictEqual(r.changes, []);
  assert.strictEqual(r.other_events, 1);
});

test('a refusal and a shape this does not read are said, never read as "no changes"', () => {
  assert.deepStrictEqual(SIM.read({ jsonrpc: '2.0', id: 1, error: { code: -32601, message: 'no' } }, '0x' + '11'.repeat(20)),
    { ok: false, reason: 'rpc_error', code: -32601 });
  for (const bad of [null, {}, { result: [] }, { result: [{ calls: [] }] }, { result: [{ number: 'x', calls: [{ status: '0x1' }] }] },
    { result: [{ number: '0x10', calls: [{ status: '0x2' }] }] }, { result: [{ number: '0x10', calls: [{ status: 1 }] }] }]) {
    assert.strictEqual(SIM.read(bad, '0x' + '11'.repeat(20)).ok, false, JSON.stringify(bad));
  }
});

const okFetch = (answer, seen) => async (url, init) => {
  seen.push({ url, body: JSON.parse(init.body) });
  return { ok: true, status: 200, json: async () => answer };
};

test('the transport asks Base\'s keyless endpoints in order and returns the first reading', async () => {
  const f = fixture('unlimited_approve');
  const seen = [];
  const r = await simulate(f.tx, { fetchImpl: okFetch(f.answer, seen) });
  assert.strictEqual(r.ok, true);
  assert.strictEqual(r.chain_id, 8453);
  assert.deepStrictEqual(seen.map((s) => s.url), [CHAINS[8453].rpcs[0]]);
  assert.strictEqual(seen[0].body.method, 'eth_simulateV1');
});

test('a dead endpoint falls to the next; none answering is unreachable; all refusing is rejected', async () => {
  const f = fixture('token_send');
  const tried = [];
  const flaky = async (url, init) => {
    tried.push(url);
    if (url === CHAINS[8453].rpcs[0]) throw new Error('down');
    return { ok: true, status: 200, json: async () => f.answer };
  };
  const r = await simulate(f.tx, { fetchImpl: flaky });
  assert.strictEqual(r.ok, true);
  assert.deepStrictEqual(tried, CHAINS[8453].rpcs);

  const dead = await simulate(f.tx, { fetchImpl: async () => { throw new Error('down'); } });
  assert.deepStrictEqual(dead, { ok: false, reason: 'unreachable' });

  const refuse = await simulate(f.tx, { fetchImpl: async () => ({ ok: true, status: 200,
    json: async () => ({ error: { code: -32601, message: 'method not found' } }) }) });
  assert.deepStrictEqual(refuse, { ok: false, reason: 'rejected', code: -32601 });

  assert.deepStrictEqual(await simulate(f.tx, { chainId: 1, fetchImpl: flaky }), { ok: false, reason: 'unsupported_chain' });
  assert.deepStrictEqual(await simulate({ ...f.tx, from: 'nope' }, { fetchImpl: flaky }), { ok: false, reason: 'invalid' });
});

// ── The verdict ────────────────────────────────────────────────────────────

const decodeOf = (name) => { const f = fixture(name); return X.decodeTx({ data: f.tx.data || '0x', value: f.tx.value || '0' }); };
const simOf = (name) => { const f = fixture(name); return SIM.read(f.answer, f.tx.from); };

test('an unlimited approval is a stop, from the decode alone and from the simulation alone', () => {
  const both = G.verdict(decodeOf('unlimited_approve'), simOf('unlimited_approve'));
  assert.strictEqual(both.level, 'stop');
  assert.deepStrictEqual(both.reasons.map((r) => r.id), ['unlimited_approval']);
  assert.ok(both.reasons[0].en);
  assert.strictEqual(both.block, simOf('unlimited_approve').block);
  // The function hidden behind a selector the X-ray does not know.
  const hidden = G.verdict(X.decodeTx({ data: '0xdeadbeef' }), simOf('unlimited_approve'));
  assert.strictEqual(hidden.level, 'stop');
  assert.ok(hidden.reasons.some((r) => r.id === 'unlimited_approval'));
  assert.ok(hidden.reasons.some((r) => r.id === 'unknown_function'));
});

test('a plain transfer, decoded and simulated, is clear and lists what leaves', () => {
  const v = G.verdict(decodeOf('token_send'), simOf('token_send'));
  assert.strictEqual(v.level, 'clear');
  assert.deepStrictEqual(v.changes.map((c) => c.kind), ['token_out']);
  assert.strictEqual(v.headline, G.HEADLINES.clear);
});

test('assets leaving through an undecoded call are checked, not cleared', () => {
  const v = G.verdict(X.decodeTx({ data: '0xdeadbeef' }), simOf('token_send'));
  assert.strictEqual(v.level, 'check');
  assert.strictEqual(v.reasons.find((r) => r.id === 'sim_assets_out').sev, 'medium');
});

test('undecoded with no simulation is unknown; with one that could not run, the gap is a reason', () => {
  const none = G.verdict(X.decodeTx({ data: '0xdeadbeef' }), null);
  assert.strictEqual(none.level, 'unknown');
  const failed = G.verdict(X.decodeTx({ data: '0xdeadbeef' }), { ok: false, reason: 'unreachable' });
  assert.strictEqual(failed.level, 'unknown');
  assert.ok(failed.reasons.some((r) => r.id === 'sim_unavailable'));
  // A decoded request whose simulation could not run still says so.
  const known = G.verdict(decodeOf('token_send'), { ok: false, reason: 'rejected' });
  assert.strictEqual(known.level, 'clear');
  assert.ok(known.reasons.some((r) => r.id === 'sim_unavailable'));
});

test('an empty call is unknown until something ran it: to a contract, it runs the fallback', () => {
  const empty = X.decodeTx({ data: '0x', value: '0' });
  assert.strictEqual(G.verdict(empty, null).level, 'unknown');
  assert.strictEqual(G.verdict(empty, simOf('native_send')).level, 'clear');
});

test('a failing call is checked, not cleared', () => {
  assert.strictEqual(G.verdict(decodeOf('reverted'), simOf('reverted')).level, 'check');
});

test('a signature and a delegation get their verdicts from the signature X-ray', () => {
  const now = 1792800000;
  const permit = {
    domain: { name: 'T', version: '1', chainId: 8453, verifyingContract: '0x' + '1a'.repeat(20) },
    types: { Permit: SR.STANDARD.Permit2612 }, primaryType: 'Permit',
    message: { owner: '0x' + 'dd'.repeat(20), spender: '0x' + 'ee'.repeat(20), value: '5', nonce: '0', deadline: String(now + 600) },
  };
  const p = G.verdict(SR.decodeTypedData(permit, { now }), null);
  assert.strictEqual(p.level, 'check');
  assert.deepStrictEqual(p.reasons.map((r) => r.id), ['gasless_permit']);
  const d = G.verdict(SR.decodeAuthorization({ chainId: 0, address: '0x' + 'ee'.repeat(20), nonce: 0 }), null);
  assert.strictEqual(d.level, 'stop');
  assert.deepStrictEqual(ids(d.reasons), ['any_chain', 'delegation_7702']);
});

test('input nothing could read is invalid, never clear', () => {
  assert.strictEqual(G.verdict(X.decodeTx({ data: 'zz' }), null).level, 'invalid');
  assert.strictEqual(G.verdict(SR.decodeTypedData('nope'), null).level, 'invalid');
  assert.strictEqual(G.verdict(null, null).level, 'invalid');
});

test('the same reason from the decode and the simulation is one reason, at the graver severity', () => {
  const v = G.verdict({ actions: [{ tid: 'x' }], flags: [{ id: 'unlimited_approval', sev: 'medium' }] },
    { ok: true, changes: [], block: '1', flags: [{ id: 'unlimited_approval', sev: 'high' }] });
  assert.deepStrictEqual(v.reasons.map((r) => [r.id, r.sev]), [['unlimited_approval', 'high']]);
  assert.strictEqual(v.level, 'stop');
});

test('every reason any model can raise has a sentence', () => {
  const src = ['txray-model.js', 'sigray-model.js', 'txsim-model.js', 'guard-model.js']
    .map((f) => fs.readFileSync(path.join(__dirname, '..', 'public', 'js', f), 'utf8')).join('\n');
  const raised = new Set([...src.matchAll(/flag\('([a-z0-9_]+)'/g), ...src.matchAll(/\{ id: '([a-z0-9_]+)', sev:/g),
    ...src.matchAll(/id = '([a-z0-9_]+)'; sev =/g)].map((m) => m[1]));
  assert.ok(raised.size >= 15, `only ${raised.size} reasons found: the scan lost its anchor`);
  for (const id of raised) assert.ok(G.REASONS[id], `no sentence for ${id}`);
  for (const id of Object.keys(G.REASONS)) assert.ok(raised.has(id), `a sentence for ${id}, which nothing raises`);
});
