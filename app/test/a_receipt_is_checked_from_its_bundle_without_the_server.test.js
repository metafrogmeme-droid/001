'use strict';
/**
 * A receipt's proof bundle and the independent verifier
 * (app/lib/proof_bundle.js, GET /api/call/:key/bundle, tools/rcverify.js),
 * and this server's own reading beside them (GET /api/verify/:key).
 *
 *   round trip   a real receipt's bundle, from the route, passes the
 *                verifier: the hash, the path, the day, each chain read from
 *                that chain, and the quorum the verifier counts itself
 *   tampering    one character of the payload, one step of the path, a
 *                wrong root on a chain: each FAILS, and is named
 *   unknown      a chain that cannot be read is unknown (exit 3), never a
 *                pass; a bundle that is not rcproof/1 is not checked
 *   independent  the verifier requires nothing but Node's own modules, and
 *                trusts none of the bundle's labels: a chain the bundle
 *                calls independent but the verifier does not is not counted
 *   the server   its reading says hash, path and quorum apart, says whose
 *                word it is, and points at the bundle
 */
process.env.JWT_SECRET = process.env.JWT_SECRET || 'j'.repeat(64);
delete process.env.DATABASE_URL;
delete process.env.SOLANA_RPC_URLS;

const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const crypto = require('node:crypto');
const express = require('express');

const { pool } = require('../db');
const { sealArenaTrade } = require('../lib/callseal');
const { merkleRoot } = require('../lib/sealroot');
const { payloadFor } = require('../lib/root_anchor');
const solana = require('../lib/solana_anchor');
const rcverify = require('../../tools/rcverify');

const sha = (s) => crypto.createHash('sha256').update(s, 'utf8').digest('hex');
const DAY_MS = 24 * 3600 * 1000;
const DAY = new Date(Date.now() - 5 * DAY_MS).toISOString().slice(0, 10);
const SEALED_AT = new Date(`${DAY}T10:00:00.000Z`);
const KEY = 'arena:bundle0000aaaabbbb';
const T = { trade_key: KEY, handle: null, symbol: 'BTCUSDT', direction: 'LONG', entry: 60000, leverage: 2, tp: 63000, sl: 58000,
  opened_at: SEALED_AT.toISOString() };
const sealed = sealArenaTrade(T);
const LEAVES = [sealed.seal, 'a'.repeat(64), 'b'.repeat(64), 'c'.repeat(64), 'd'.repeat(64)];
const ROOT = merkleRoot(LEAVES);
const TX_BASE = '0x' + '5'.repeat(64);
const SIG = '5VERv8NMvzbJMEkV8xnrLkEaWRtSz9CosKDYjCJjBRnbJLgp8uirBgmQpjKhoR4tjF3ZpRzrFmBV6UjKdiSZkQUW';
const TX_TESTNET = '0x' + '6'.repeat(64);

/** The chains as they would answer, by the endpoint the VERIFIER chose. */
function chainsFetch({ baseRoot = ROOT, solanaDown = false, seen = [] } = {}) {
  return async (url, opts) => {
    const body = opts && opts.body ? JSON.parse(opts.body) : null;
    seen.push(url);
    const reply = (result) => ({ ok: true, status: 200, json: async () => ({ jsonrpc: '2.0', id: 1, result }) });
    if (url === rcverify.CHAINS.base.rpc && body.method === 'eth_getTransactionByHash') {
      return reply({ hash: TX_BASE, blockHash: '0x1', blockNumber: '0x10', input: payloadFor(DAY, baseRoot) });
    }
    if (url === rcverify.CHAINS.base.rpc && body.method === 'eth_getTransactionReceipt') return reply({ status: '0x1' });
    if (url === rcverify.CHAINS['dogeos-testnet'].rpc && body.method === 'eth_getTransactionByHash') {
      return reply({ hash: TX_TESTNET, blockHash: '0x2', blockNumber: '0x20', input: payloadFor(DAY, ROOT) });
    }
    if (url === rcverify.CHAINS['dogeos-testnet'].rpc && body.method === 'eth_getTransactionReceipt') return reply({ status: '0x1' });
    if (url === rcverify.CHAINS.solana.rpc || solana.DEFAULT_RPCS.includes(url)) {
      if (solanaDown) throw new Error('down');
      return reply({ slot: 301, blockTime: 1761200000, meta: { err: null }, transaction: { signatures: [SIG],
        message: { accountKeys: [{ pubkey: 'P' }], instructions: [{ programId: solana.MEMO_PROGRAMS[0], parsed: `RCROOT1:${DAY}:${ROOT}` }] } } });
    }
    return { ok: false, status: 502, json: async () => ({}) };
  };
}

let server, base;
const get = (p) => new Promise((resolve, reject) => {
  http.get(`${base}${p}`, (res) => {
    let d = ''; res.on('data', (c) => { d += c; });
    res.on('end', () => resolve({ status: res.statusCode, headers: res.headers, body: d ? JSON.parse(d) : null }));
  }).on('error', reject);
});
const realFetch = global.fetch;

test.before(async () => {
  pool.arenaTrades.push({ id: 900001, user_id: 1, trade_key: KEY, symbol: T.symbol, direction: T.direction, entry: T.entry,
    exit_price: 61000, margin: 100, leverage: T.leverage, pnl: 3.3, reason: 'tp', seal: sealed.seal, seal_payload: sealed.seal_payload,
    sealed_at: SEALED_AT, opened_at: SEALED_AT, closed_at: new Date(SEALED_AT.getTime() + 3600000) });
  await pool.execute('INSERT INTO seal_roots (day, root, seal_count, leaves, computed_at) VALUES (?, ?, ?, ?, ?)',
    [DAY, ROOT, LEAVES.length, JSON.stringify(LEAVES), new Date()]);
  for (const [chain, tx] of [['base', TX_BASE], ['solana', SIG], ['dogeos-testnet', TX_TESTNET]]) {
    await pool.execute('INSERT IGNORE INTO seal_root_anchors (day, chain, tx, from_addr, block_time, verified_at) VALUES (?, ?, ?, ?, ?, ?)',
      [DAY, chain, tx, null, new Date(SEALED_AT.getTime() + DAY_MS), new Date()]);
  }
  const app = express();
  app.use('/api/call', require('../routes/call'));
  app.use('/api/verify', require('../routes/verify'));
  await new Promise((r) => { server = app.listen(0, '127.0.0.1', r); });
  base = `http://127.0.0.1:${server.address().port}`;
});
test.after(() => { global.fetch = realFetch; if (server) server.close(); });

test('a real receipt\'s bundle passes the independent verifier, chains and quorum included', async () => {
  const r = await get(`/api/call/${KEY}/bundle`);
  assert.strictEqual(r.status, 200, JSON.stringify(r.body));
  const b = r.body;
  assert.deepStrictEqual([b.format, b.key, b.seal, b.sealed, b.merkle.day, b.merkle.root], ['rcproof/1', KEY, sealed.seal, false, DAY, ROOT]);
  assert.strictEqual(b.seal_payload, sealed.seal_payload);
  assert.deepStrictEqual(b.anchors.map((a) => [a.chain, a.method, a.tx]),
    [['base', 'calldata', TX_BASE], ['dogeos-testnet', 'calldata', TX_TESTNET], ['solana', 'solana_memo', SIG]]);
  assert.match(r.headers['content-disposition'], /attachment; filename="rcproof-arena_bundle0000aaaabbbb\.json"/);
  assert.ok(!JSON.stringify(b).match(/"verified"|"status"/), 'facts, never a verdict');
  for (const k of ['margin', 'pnl']) assert.ok(!(k in b), `nothing beyond the receipt: ${k}`);
  const byChain = Object.fromEntries(b.anchors.map((a) => [a.chain, a]));
  assert.deepStrictEqual(['base', 'dogeos-testnet', 'solana'].map((c) => byChain[c].counts_toward_quorum), [true, false, true]);
  assert.strictEqual(byChain.base.explorer, `https://basescan.org/tx/${TX_BASE}`);

  const seen = [];
  const out = await rcverify.verifyBundle(b, { fetchImpl: chainsFetch({ seen }) });
  const by = Object.fromEntries(out.checks.map((c) => [c.name, c]));
  assert.deepStrictEqual(['hash', 'path', 'day', 'anchor base', 'anchor dogeos-testnet', 'anchor solana'].map((n) => by[n].status),
    ['pass', 'pass', 'pass', 'pass', 'pass', 'pass']);
  assert.match(by.quorum.detail, /^multi-anchored: 2 independent chains verified here \(ethereum-l2, solana\)/,
    'the testnet passes and is not counted');
  assert.strictEqual(out.exit, 0);
  assert.ok(seen.every((u) => /^https:\/\/(mainnet\.base\.org|rpc\.testnet\.dogeos\.com|api\.mainnet-beta\.solana\.com)/.test(u)),
    `the verifier asked only endpoints it chose: ${seen}`);
});

test('tampering fails and is named: the payload, the path, a chain\'s root', async () => {
  const b = (await get(`/api/call/${KEY}/bundle`)).body;
  const payload = await rcverify.verifyBundle({ ...b, seal_payload: b.seal_payload.replace('60000', '60001') }, { offline: true });
  assert.deepStrictEqual([payload.checks.find((c) => c.name === 'hash').status, payload.exit], ['fail', 1]);
  const steps = b.merkle.proof.map((s, i) => (i === 0 ? { ...s, hash: 'e'.repeat(64) } : s));
  const pathOut = await rcverify.verifyBundle({ ...b, merkle: { ...b.merkle, proof: steps } }, { offline: true });
  assert.deepStrictEqual([pathOut.checks.find((c) => c.name === 'path').status, pathOut.exit], ['fail', 1]);
  const chain = await rcverify.verifyBundle(b, { fetchImpl: chainsFetch({ baseRoot: '1'.repeat(64) }) });
  const baseCheck = chain.checks.find((c) => c.name === 'anchor base');
  assert.deepStrictEqual([baseCheck.status, chain.exit], ['fail', 1]);
  assert.match(baseCheck.detail, /not this day's root/);
  // The bundle's own labels are not trusted: a chain it calls independent counts only if the verifier agrees.
  const relabelled = { ...b, anchors: b.anchors.map((a) => (a.chain === 'dogeos-testnet' ? { ...a, independence_group: 'mine', counts_toward_quorum: true } : a)) };
  const q = await rcverify.verifyBundle(relabelled, { fetchImpl: chainsFetch() });
  assert.match(q.checks.find((c) => c.name === 'quorum').detail, /^multi-anchored: 2 independent chains/);
});

test('a chain that cannot be read is unknown, and a bundle that is not rcproof/1 is not checked', async () => {
  const b = (await get(`/api/call/${KEY}/bundle`)).body;
  const down = await rcverify.verifyBundle(b, { fetchImpl: chainsFetch({ solanaDown: true }) });
  assert.strictEqual(down.checks.find((c) => c.name === 'anchor solana').status, 'unknown');
  assert.strictEqual(down.exit, 3, 'unknown is not a pass and not a fail');
  assert.match(down.checks.find((c) => c.name === 'quorum').detail, /^1 independent chain verified here/);
  assert.strictEqual((await rcverify.verifyBundle({ ...b, format: 'rcproof/2' }, { offline: true })).exit, 3);
  assert.strictEqual((await rcverify.verifyBundle(null, { offline: true })).exit, 3);
  // Offline: the hash and the path, the chains not asked.
  const off = await rcverify.verifyBundle(b, { offline: true, fetchImpl: () => { throw new Error('must not be asked'); } });
  assert.strictEqual(off.exit, 0);
  assert.ok(off.checks.filter((c) => c.name.startsWith('anchor ')).every((c) => c.status === 'skip'));
});

test('the verifier stands alone: Node\'s own modules only', () => {
  const src = fs.readFileSync(path.join(__dirname, '..', '..', 'tools', 'rcverify.js'), 'utf8');
  const required = [...src.matchAll(/require\(\s*'([^']+)'\s*\)/g)].map((m) => m[1]);
  assert.deepStrictEqual([...new Set(required)].sort(), ['crypto', 'fs', 'path']);
});

test('the server\'s own reading: hash, path and quorum apart, whose word it is, and the bundle beside it', async () => {
  global.fetch = chainsFetch();
  const r = await get(`/api/verify/${KEY}`);
  assert.strictEqual(r.status, 200, JSON.stringify(r.body));
  assert.deepStrictEqual([r.body.hash, r.body.path, r.body.day, r.body.root], ['match', 'reaches_root', DAY, ROOT]);
  assert.ok(r.body.quorum && typeof r.body.quorum.status === 'string');
  assert.match(r.body.by, /^this server/);
  assert.strictEqual(r.body.bundle, `/api/call/${encodeURIComponent(KEY)}/bundle`);
  assert.ok(!('verified' in r.body), 'three readings, never one "verified"');
  const missing = await get('/api/verify/arena:nosuchtrade00000000');
  assert.strictEqual(missing.status, 404);
  // The readings alone: a sealed payload, a path that misses, a quorum that could not be read.
  const { readingsOf } = require('../routes/verify');
  const sealedR = await readingsOf({ seal: sealed.seal, seal_payload: null, anchor: null });
  assert.deepStrictEqual([sealedR.hash, sealedR.path, sealedR.quorum], ['sealed', 'none', null]);
  const altered = await readingsOf({ seal: sealed.seal, seal_payload: sealed.seal_payload + ' ', anchor: null });
  assert.deepStrictEqual([altered.hash, altered.path], ['mismatch', 'none']);
  const misses = await readingsOf({ seal: sealed.seal, seal_payload: sealed.seal_payload, anchor: { day: DAY, root: '0'.repeat(64), proof: [] } });
  assert.deepStrictEqual([misses.hash, misses.path, misses.quorum], ['match', 'misses_root', null]);
  const unread = await readingsOf({ seal: sealed.seal, seal_payload: sealed.seal_payload, anchor: { day: DAY, root: ROOT, proof: (await get(`/api/call/${KEY}/bundle`)).body.merkle.proof } },
    async () => { throw new Error('anchors unreadable'); });
  assert.deepStrictEqual(unread.quorum, { status: 'unread' });
  assert.strictEqual(sha(sealed.seal_payload), sealed.seal);
});

test('EAS and Dogecoin are each read as their method says: pass, fail, and unknown apart', async () => {
  const day = DAY; const root = ROOT;
  const word = (n) => '0x' + n.toString(16).padStart(64, '0');
  const eas = (out, seen) => async (url, opts) => {
    const body = JSON.parse(opts.body);
    if (seen) seen.push([url, body.params[0].to, body.params[0].data]);
    if (out instanceof Error) throw out;
    return { ok: true, status: 200, json: async () => ({ jsonrpc: '2.0', id: 1, result: out }) };
  };
  const seen = [];
  // The bundle names an EAS contract of its own: the verifier asks its own.
  const stamped = await rcverify.checkAnchor({ chain: 'base-eas', tx: TX_BASE, eas: '0x' + '9'.repeat(40) }, day, root, eas(word(1761200000), seen));
  assert.strictEqual(stamped.status, 'pass');
  assert.deepStrictEqual(seen[0], [rcverify.CHAINS['base-eas'].rpc, '0x4200000000000000000000000000000000000021', '0xd45c4435' + root],
    'the verifier\'s own EAS contract, asked by the root');
  assert.strictEqual((await rcverify.checkAnchor({ chain: 'base-eas', tx: TX_BASE, eas: '0xevil' }, day, root, eas(word(0)))).status, 'fail');
  assert.strictEqual((await rcverify.checkAnchor({ chain: 'base-eas', tx: TX_BASE }, day, root, eas('nonsense'))).status, 'unknown');

  const want = Buffer.from(`RCD1:${day}:`, 'utf8').toString('hex') + root;
  const doge = (status, j) => async (url) => {
    assert.ok(url.startsWith(rcverify.CHAINS.dogecoin.api));
    return { ok: status === 200, status, json: async () => j };
  };
  const out = (hex) => [{ script_type: 'pay-to-pubkey-hash', data_hex: null }, { script_type: 'null-data', data_hex: hex }];
  const TXID = 'f'.repeat(64);
  assert.strictEqual((await rcverify.checkAnchor({ chain: 'dogecoin', tx: TXID }, day, root, doge(200, { outputs: out(want), confirmations: 6 }))).status, 'pass');
  assert.strictEqual((await rcverify.checkAnchor({ chain: 'dogecoin', tx: TXID }, day, root, doge(200, { outputs: out(want), confirmations: 5 }))).status, 'unknown',
    'too shallow is not yet, never a pass');
  assert.strictEqual((await rcverify.checkAnchor({ chain: 'dogecoin', tx: TXID }, day, root, doge(200, { outputs: out(want.replace(/.$/, '0')), confirmations: 9 }))).status, 'fail');
  assert.strictEqual((await rcverify.checkAnchor({ chain: 'dogecoin', tx: TXID }, day, root, doge(404, {}))).status, 'fail');
  assert.strictEqual((await rcverify.checkAnchor({ chain: 'dogecoin', tx: TXID }, day, root, doge(503, {}))).status, 'unknown');
  assert.strictEqual((await rcverify.checkAnchor({ chain: 'polygon', tx: TX_BASE }, day, root, doge(200, {}))).status, 'unknown',
    'a chain the verifier does not know is not checked and not counted');
  // A calldata chain: a reverted transaction fails, an unmined one is unknown.
  const evm = (t, rc) => async (url, opts) => {
    const m = JSON.parse(opts.body).method;
    return { ok: true, status: 200, json: async () => ({ jsonrpc: '2.0', id: 1, result: m === 'eth_getTransactionByHash' ? t : rc }) };
  };
  const mined = { blockHash: '0x1', blockNumber: '0x10', input: payloadFor(day, root) };
  assert.strictEqual((await rcverify.checkAnchor({ chain: 'base', tx: TX_BASE }, day, root, evm(mined, { status: '0x0' }))).status, 'fail');
  assert.strictEqual((await rcverify.checkAnchor({ chain: 'base', tx: TX_BASE }, day, root, evm({ ...mined, blockHash: null }, { status: '0x1' }))).status, 'unknown',
    'not yet mined is unknown whatever a receipt says');
  assert.strictEqual((await rcverify.checkAnchor({ chain: 'base', tx: TX_BASE }, day, root, evm(null, null))).status, 'unknown');
  // Solana: asked at finalized; another transaction, a failed one, a memo from another program.
  const memo = `RCROOT1:${day}:${root}`;
  const sol = (over, asked) => async (url, opts) => {
    if (asked) asked.push(JSON.parse(opts.body).params[1]);
    const tx = { slot: 301, meta: { err: null }, transaction: { signatures: [SIG],
      message: { instructions: [{ programId: solana.MEMO_PROGRAMS[0], parsed: memo }] } }, ...over };
    return { ok: true, status: 200, json: async () => ({ jsonrpc: '2.0', id: 1, result: tx }) };
  };
  const asked = [];
  assert.strictEqual((await rcverify.checkAnchor({ chain: 'solana', tx: SIG }, day, root, sol({}, asked))).status, 'pass');
  assert.strictEqual(asked[0].commitment, 'finalized');
  assert.strictEqual((await rcverify.checkAnchor({ chain: 'solana', tx: SIG }, day, root,
    sol({ transaction: { signatures: ['x' + SIG.slice(1)], message: { instructions: [{ programId: solana.MEMO_PROGRAMS[0], parsed: memo }] } } }))).status, 'unknown');
  assert.strictEqual((await rcverify.checkAnchor({ chain: 'solana', tx: SIG }, day, root, sol({ meta: { err: { InstructionError: [0, 'x'] } } }))).status, 'fail');
  assert.strictEqual((await rcverify.checkAnchor({ chain: 'solana', tx: SIG }, day, root,
    sol({ transaction: { signatures: [SIG], message: { instructions: [{ programId: '11111111111111111111111111111111', parsed: memo }] } } }))).status, 'fail',
  'the root in another program\'s instruction is not a memo');
  assert.strictEqual((await rcverify.checkAnchor({ chain: 'solana', tx: SIG }, day, root,
    sol({ transaction: { signatures: [SIG], message: { instructions: [{ programId: solana.MEMO_PROGRAMS[0], parsed: `RCROOT1:${day}:${'1'.repeat(64)}` }] } } }))).status, 'fail',
  'a memo of another root');
});

test('the day and the Bitcoin statement are checked, and a still-sealed receipt is checked by its place only', async () => {
  const { bundleOf } = require('../lib/proof_bundle');
  const ots = require('../lib/opentimestamps');
  const b = (await get(`/api/call/${KEY}/bundle`)).body;
  const late = await rcverify.verifyBundle({ ...b, sealed_at: new Date(SEALED_AT.getTime() + DAY_MS).toISOString() }, { offline: true });
  assert.deepStrictEqual([late.checks.find((c) => c.name === 'day').status, late.exit], ['fail', 1]);

  const receipt = { kind: 'arena', seal: sealed.seal, seal_payload: sealed.seal_payload, sealed_at: SEALED_AT,
    anchor: { day: DAY, root: ROOT, seal_count: LEAVES.length, proof: b.merkle.proof, anchors: [] } };
  const withBtc = bundleOf(KEY, receipt, { otsRow: { day: DAY, root: ROOT, proof: 'AAEC', bitcoin_height: 900000 }, chains: [] });
  assert.deepStrictEqual([withBtc.bitcoin.statement, withBtc.bitcoin.digest], [ots.statementFor(DAY, ROOT), ots.digestFor(DAY, ROOT).toString('hex')]);
  const btc = await rcverify.verifyBundle(withBtc, { offline: true });
  assert.deepStrictEqual([btc.checks.find((c) => c.name === 'bitcoin').status, btc.exit], ['info', 0]);
  const forged = await rcverify.verifyBundle({ ...withBtc, bitcoin: { ...withBtc.bitcoin, statement: `RCROOT1:${DAY}:${'1'.repeat(64)}` } }, { offline: true });
  assert.deepStrictEqual([forged.checks.find((c) => c.name === 'bitcoin').status, forged.exit], ['fail', 1]);
  const forgedDigest = await rcverify.verifyBundle({ ...withBtc, bitcoin: { ...withBtc.bitcoin, digest: '1'.repeat(64) } }, { offline: true });
  assert.deepStrictEqual([forgedDigest.checks.find((c) => c.name === 'bitcoin').status, forgedDigest.exit], ['fail', 1]);
  assert.strictEqual(bundleOf(KEY, receipt, { otsRow: { day: DAY, root: '2'.repeat(64), proof: 'AAEC' }, chains: [] }).bitcoin, null,
    'a proof of another root is not this day\'s');
  const unread = bundleOf(KEY, receipt, { otsUnread: true, chains: [] });
  assert.deepStrictEqual([unread.bitcoin, unread.bitcoin_unread], [null, true]);
  assert.strictEqual((await rcverify.verifyBundle(unread, { offline: true })).exit, 3, 'an unread proof is unknown, not "none"');

  // What an anonymous viewer is served of a live call: the seal and its place, no payload.
  const locked = bundleOf('sig:live', { kind: 'signal', seal: sealed.seal, seal_payload: null, sealed_at: SEALED_AT,
    anchor: { day: DAY, root: ROOT, proof: b.merkle.proof, anchors: [] }, locked: true }, { chains: [] });
  assert.deepStrictEqual([locked.sealed, locked.seal_payload], [true, null]);
  const lockedOut = await rcverify.verifyBundle(locked, { offline: true });
  assert.deepStrictEqual(['hash', 'path'].map((n) => lockedOut.checks.find((c) => c.name === n).status), ['skip', 'pass']);
  // No path: the hash is checked and the anchoring is not claimed either way.
  const open = bundleOf('arena:open', { ...receipt, anchor: null }, { chains: [] });
  assert.deepStrictEqual([open.merkle, open.anchors, typeof open.merkle_note], [null, [], 'string']);
  const openOut = await rcverify.verifyBundle(open, { offline: true });
  assert.deepStrictEqual(['hash', 'path'].map((n) => openOut.checks.find((c) => c.name === n).status), ['pass', 'skip']);
  assert.strictEqual((await get('/api/call/arena:nosuchtrade00000000/bundle')).status, 404);
});

test('a Bitcoin proof the server could not read is said in the bundle, not left out', async () => {
  const rootOts = require('../lib/root_ots');
  const real = rootOts.rowFor;
  rootOts.rowFor = async () => { throw new Error('db down'); };
  try {
    const b = (await get(`/api/call/${KEY}/bundle`)).body;
    assert.deepStrictEqual([b.bitcoin, b.bitcoin_unread], [null, true]);
  } finally { rootOts.rowFor = real; }
  const b = (await get(`/api/call/${KEY}/bundle`)).body;
  assert.deepStrictEqual([b.bitcoin, 'bitcoin_unread' in b], [null, false], 'read and absent is not unread');
});

test('the command line: exit codes, --json, --save-ots and a crafted bundle', async () => {
  const os = require('node:os');
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'rcverify-'));
  try {
    const b = (await get(`/api/call/${KEY}/bundle`)).body;
    const withBtc = { ...b, bitcoin: { statement: `RCROOT1:${DAY}:${ROOT}`, digest: sha(`RCROOT1:${DAY}:${ROOT}`), ots_proof_base64: Buffer.from('proof').toString('base64') } };
    // The statement file is the verifier's own text: a bundle's forged statement fails its check and is not written.
    const forged = path.join(dir, 'forged', 'f.json');
    fs.mkdirSync(path.dirname(forged));
    fs.writeFileSync(forged, JSON.stringify({ ...withBtc, bitcoin: { ...withBtc.bitcoin, statement: 'RCROOT1:forged' } }));
    assert.strictEqual(await rcverify.main([forged, '--offline', '--save-ots'], () => {}), 1);
    assert.strictEqual(fs.readFileSync(path.join(dir, 'forged', `RCROOT1-${DAY}.txt`), 'utf8'), `RCROOT1:${DAY}:${ROOT}`);
    const file = path.join(dir, 'b.json');
    fs.writeFileSync(file, JSON.stringify(withBtc));
    const lines = [];
    assert.strictEqual(await rcverify.main([file, '--offline', '--save-ots'], (l) => lines.push(l)), 0);
    assert.strictEqual(fs.readFileSync(path.join(dir, `RCROOT1-${DAY}.txt`), 'utf8'), `RCROOT1:${DAY}:${ROOT}`);
    assert.strictEqual(fs.readFileSync(path.join(dir, `RCROOT1-${DAY}.txt.ots`), 'utf8'), 'proof');
    assert.match(lines.join('\n'), /every check that ran passed/);
    const json = [];
    assert.strictEqual(await rcverify.main([file, '--json'], (l) => json.push(l), chainsFetch()), 0);
    assert.strictEqual(JSON.parse(json[0]).exit, 0);
    // A crafted bundle: its day names no file, and its text reaches the terminal without control characters.
    const crafted = path.join(dir, 'sub', 'c.json');
    fs.mkdirSync(path.dirname(crafted));
    fs.writeFileSync(crafted, JSON.stringify({ ...withBtc, merkle: { ...b.merkle, day: '../../escape' } }));
    const craftedLines = [];
    assert.strictEqual(await rcverify.main([crafted, '--save-ots'], (l) => craftedLines.push(l), chainsFetch()), 1);
    assert.deepStrictEqual(fs.readdirSync(path.dirname(crafted)), ['c.json']);
    assert.ok(!fs.readdirSync(dir).some((n) => n.includes('escape')));
    const escaping = path.join(dir, 'e.json');
    fs.writeFileSync(escaping, JSON.stringify({ ...b, anchors: [{ chain: '\u001b[2Jx', tx: 'y' }] }));
    const escLines = [];
    assert.strictEqual(await rcverify.main([escaping], (l) => escLines.push(l), chainsFetch()), 3);
    assert.match(escLines.join('\n'), /\?\[2Jx/, 'the name is printed, its control character replaced');
    assert.ok(!escLines.join('\n').includes('\u001b'), 'no escape sequence printed');
    const bad = path.join(dir, 'bad.json');
    fs.writeFileSync(bad, '{not json');
    assert.strictEqual(await rcverify.main([bad], () => {}), 3);
    assert.strictEqual(await rcverify.main([], () => {}), 3);
    const broken = path.join(dir, 'broken.json');
    fs.writeFileSync(broken, JSON.stringify({ ...b, seal_payload: b.seal_payload + ' ' }));
    const brokenLines = [];
    assert.strictEqual(await rcverify.main([broken, '--offline'], (l) => brokenLines.push(l)), 1);
    assert.match(brokenLines.join('\n'), /A CHECK FAILED/);
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
});
