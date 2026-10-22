'use strict';
/**
 * A day's root anchored on Solana (app/lib/solana_anchor.js): one Memo
 * instruction carrying RCROOT1:<day>:<root>, sent by a wallet's owner, read
 * back from two keyless JSON-RPC readers at `finalized` commitment.
 *
 *   the plan      a dry run: the memo and the CLI command, nothing signed
 *   one reader    each way a transaction can fail to be this anchor, and the
 *                 one way it is
 *   two readers   agreement on the memo and the slot, or "unknown": one
 *                 reader down, lagging or lying never decides
 *   the route     a base58 signature keeps its case from the request to the
 *                 table to the feed (a hex hash is lower-cased; this one
 *                 must not be), and Solana is its own independent witness in
 *                 the day's quorum
 *   no leak       a configured RPC (which may carry a key) is never named
 *                 and never served
 */
process.env.JWT_SECRET = process.env.JWT_SECRET || 'test-secret-'.repeat(4);
delete process.env.SOLANA_RPC_URLS;

const test = require('node:test');
const assert = require('node:assert');
const http = require('node:http');
const express = require('express');

const sol = require('../lib/solana_anchor');
const { chainInfo, publicChains, buildAnchorPlan, verifyAnchor, caseSensitiveTx } = require('../lib/root_anchor');
const { pool } = require('../db');
const authModule = require('../auth');
const roots = require('../lib/seal_roots');

const DAY = roots.dayOf(new Date(Date.now() - 4 * 24 * 3600 * 1000));
const ROOT = '9'.repeat(64);
const MEMO = `RCROOT1:${DAY}:${ROOT}`;
// Base58 signatures, mixed case on purpose: case is the value.
const SIG = '5VERv8NMvzbJMEkV8xnrLkEaWRtSz9CosKDYjCJjBRnbJLgp8uirBgmQpjKhoR4tjF3ZpRzrFmBV6UjKdiSZkQUW';
const SIG_FAILED = '3kQh5S2Tq1qmjTPMv8V9sSCwFQrA5c7NSuGVa5o6eXQFxZPyGCrz9LuU7oU1fPvLtq3qTkGtDsBpYVqNZrMhWbxa';
const PAYER = '7xKXtg2CW87d97TXJSDpbD5jBkheTqA83TZRuJosgAsU';

const MEMO_ID = sol.MEMO_PROGRAMS[0];
const parsedTx = (sig, memos, over = {}) => ({
  slot: 301234567, blockTime: 1761200000,
  meta: { err: null },
  transaction: {
    signatures: [sig],
    message: {
      accountKeys: [{ pubkey: PAYER, signer: true, writable: true }],
      instructions: [
        { program: 'system', programId: '11111111111111111111111111111111', parsed: { type: 'transfer' } },
        ...memos.map((m) => ({ program: 'spl-memo', programId: MEMO_ID, parsed: m })),
      ],
    },
  },
  ...over,
});

test('the plan is a dry run: the memo, its size, and a command the owner runs', () => {
  assert.strictEqual(sol.memoFor(DAY, ROOT), MEMO);
  assert.strictEqual(sol.memoFor('22-10-2026', ROOT), null);
  assert.strictEqual(sol.memoFor(DAY, 'Z'.repeat(64)), null);
  const p = buildAnchorPlan(DAY, ROOT, 'solana');
  assert.deepStrictEqual([p.dry_run, p.chain_key, p.method, p.memo, p.memo_bytes, p.value], [true, 'solana', 'solana_memo', MEMO, 83, '0']);
  assert.ok(p.instructions.some((l) => l.includes(`--with-memo "${MEMO}"`)), 'the command carries the memo');
  assert.match(p.non_custodial_note, /never holds a key/);
  assert.ok(!JSON.stringify(p).includes('private'), 'no key is asked for');
});

test('one reader: each way a transaction is not this anchor, and the way it is', () => {
  assert.deepStrictEqual(sol.judge(parsedTx(SIG, [MEMO]), SIG, MEMO),
    { status: 'verified', block_time: new Date(1761200000 * 1000).toISOString(), same: '301234567', slot: 301234567, from: PAYER, final: true });
  assert.strictEqual(sol.judge(null, SIG, MEMO).status, 'unknown', 'not finalized is not "no such transaction"');
  assert.strictEqual(sol.judge(parsedTx('other', [MEMO]), SIG, MEMO).status, 'unknown');
  assert.match(sol.judge(parsedTx(SIG, [MEMO], { meta: { err: { InstructionError: [1, 'x'] } } }), SIG, MEMO).reason, /failed/);
  assert.match(sol.judge(parsedTx(SIG, []), SIG, MEMO).reason, /no memo/);
  assert.match(sol.judge(parsedTx(SIG, [MEMO, MEMO]), SIG, MEMO).reason, /more than one memo/);
  assert.match(sol.judge(parsedTx(SIG, [`${MEMO} gm`]), SIG, MEMO).reason, /not this day's root/, 'a longer memo is not this anchor');
  assert.match(sol.judge(parsedTx(SIG, [`RCROOT1:${DAY}:${'1'.repeat(64)}`]), SIG, MEMO).reason, /not this day's root/);
  assert.strictEqual(sol.judge(parsedTx(SIG, [MEMO], { blockTime: null }), SIG, MEMO).status, 'unknown');
  // Memo v1 is read too; a look-alike program id is not.
  const v1 = parsedTx(SIG, [MEMO]);
  v1.transaction.message.instructions[1].programId = sol.MEMO_PROGRAMS[1];
  assert.strictEqual(sol.judge(v1, SIG, MEMO).status, 'verified');
  const fake = parsedTx(SIG, [MEMO]);
  fake.transaction.message.instructions[1].programId = 'MemoFakeProgram1111111111111111111111111111';
  assert.match(sol.judge(fake, SIG, MEMO).reason, /no memo/);
});

/** A fetch answering each reader from its own ledger: { [url]: (sig) => result | Error | { rpcError } }. */
function readersFetch(byUrl, seen = []) {
  return async (url, opts) => {
    const body = JSON.parse(opts.body);
    seen.push({ url, body });
    const answer = byUrl[url] ? byUrl[url](body.params[0]) : new Error('down');
    if (answer instanceof Error) throw answer;
    if (answer && answer.rpcError) return { ok: true, status: 200, json: async () => ({ jsonrpc: '2.0', id: 1, error: { code: answer.rpcError, message: 'busy' } }) };
    return { ok: true, status: 200, json: async () => ({ jsonrpc: '2.0', id: 1, result: answer }) };
  };
}
const [A, B] = sol.DEFAULT_RPCS;
const chain = chainInfo('solana');

test('two readers: agreement verifies; one down, lagging, lying or in another slot is unknown', async () => {
  const seen = [];
  const both = await sol.verify(SIG, DAY, ROOT, chain, readersFetch({ [A]: () => parsedTx(SIG, [MEMO]), [B]: () => parsedTx(SIG, [MEMO]) }, seen));
  assert.deepStrictEqual([both.status, both.slot, both.from, both.readers], ['verified', 301234567, PAYER, ['api.mainnet-beta.solana.com', 'solana-rpc.publicnode.com']]);
  assert.deepStrictEqual(seen[0].body, { jsonrpc: '2.0', id: 1, method: 'getTransaction',
    params: [SIG, { encoding: 'jsonParsed', commitment: 'finalized', maxSupportedTransactionVersion: 0 }] }, 'asked at finalized');
  const down = await sol.verify(SIG, DAY, ROOT, chain, readersFetch({ [A]: () => parsedTx(SIG, [MEMO]) }));
  assert.strictEqual(down.status, 'unknown', 'one reader alone decides nothing');
  const busy = await sol.verify(SIG, DAY, ROOT, chain, readersFetch({ [A]: () => parsedTx(SIG, [MEMO]), [B]: () => ({ rpcError: -32429 }) }));
  assert.strictEqual(busy.status, 'unknown');
  assert.doesNotMatch(busy.reason, /busy/, 'a reader\'s own message is never repeated');
  assert.match(busy.reason, /could not be read/, 'an RPC error is a reader that did not answer, not "not finalized"');
  const lags = await sol.verify(SIG, DAY, ROOT, chain, readersFetch({ [A]: () => parsedTx(SIG, [MEMO]), [B]: () => null }));
  assert.strictEqual(lags.status, 'unknown');
  const lies = await sol.verify(SIG, DAY, ROOT, chain, readersFetch({ [A]: () => parsedTx(SIG, [MEMO]), [B]: () => parsedTx(SIG, []) }));
  assert.deepStrictEqual([lies.status, lies.disagreement], ['unknown', true]);
  const forked = await sol.verify(SIG, DAY, ROOT, chain, readersFetch({ [A]: () => parsedTx(SIG, [MEMO]), [B]: () => parsedTx(SIG, [MEMO], { slot: 301234568 }) }));
  assert.deepStrictEqual([forked.status, forked.disagreement], ['unknown', true]);
  const wrong = await sol.verify(SIG, DAY, ROOT, chain, readersFetch({ [A]: () => parsedTx(SIG, []), [B]: () => parsedTx(SIG, []) }));
  assert.strictEqual(wrong.status, 'mismatch', 'both readers agreeing it is not the anchor is a verdict');
  // A malformed signature is refused before any reader is asked.
  const asked = [];
  for (const bad of ['0x' + 'a'.repeat(64), 'short', SIG + '0', `${SIG.slice(0, 40)}O${SIG.slice(41)}`]) {
    const r = await sol.verify(bad, DAY, ROOT, chain, readersFetch({}, asked));
    assert.deepStrictEqual([r.status, r.reason], ['mismatch', 'malformed transaction signature'], bad);
  }
  assert.deepStrictEqual(asked, []);
});

test('a configured RPC is read but never named and never served', () => {
  const withKey = 'https://solana-mainnet.example-provider.com/v2/SECRETKEY123';
  const c = chainInfo('solana', { SOLANA_RPC_URLS: withKey });
  assert.deepStrictEqual(c.rpcs, [withKey]);
  assert.strictEqual(sol.readerName(withKey, 0), 'configured solana rpc 1');
  const served = JSON.stringify(publicChains({ SOLANA_RPC_URLS: withKey }));
  assert.ok(!served.includes('SECRETKEY') && !served.includes('example-provider'));
  const pub = publicChains().find((x) => x.key === 'solana');
  assert.deepStrictEqual([pub.method, pub.independence_group, pub.counts_toward_quorum, pub.rpcs, pub.chain_id],
    ['solana_memo', 'solana', true, [], null]);
  assert.strictEqual(caseSensitiveTx(c), true);
  assert.strictEqual(caseSensitiveTx(chainInfo('base')), false);
});

// ── through the route ───────────────────────────────────────────────────────

let server, base;
function request(method, p, { token, body } = {}) {
  return new Promise((resolve, reject) => {
    const data = body ? JSON.stringify(body) : null;
    const req = http.request(`${base}${p}`, { method, headers: {
      accept: 'application/json', ...(data ? { 'content-type': 'application/json' } : {}),
      ...(token ? { Authorization: `Bearer ${token}` } : {}) } }, (res) => {
      let d = ''; res.on('data', (c) => { d += c; });
      res.on('end', () => resolve({ status: res.statusCode, data: d ? JSON.parse(d) : {} }));
    });
    req.on('error', reject); if (data) req.write(data); req.end();
  });
}
const realFetch = global.fetch;
test.before(async () => {
  const app = express();
  app.use(express.json());
  app.use('/api/roots', require('../routes/roots'));
  await new Promise((res) => { server = app.listen(0, '127.0.0.1', res); });
  base = `http://127.0.0.1:${server.address().port}`;
  global.fetch = readersFetch({
    [A]: (s) => (s === SIG ? parsedTx(SIG, [MEMO]) : s === SIG_FAILED ? parsedTx(SIG_FAILED, [MEMO], { meta: { err: 'x' } }) : null),
    [B]: (s) => (s === SIG ? parsedTx(SIG, [MEMO]) : s === SIG_FAILED ? parsedTx(SIG_FAILED, [MEMO], { meta: { err: 'x' } }) : null),
  });
});
test.after(() => { global.fetch = realFetch; if (server) server.close(); });

test('a signature keeps its case from the request to the table to the feed, and Solana counts in the quorum', async () => {
  await pool.execute('INSERT INTO seal_roots (day, root, seal_count, leaves, computed_at) VALUES (?, ?, ?, ?, ?)',
    [DAY, ROOT, 1, JSON.stringify([ROOT]), new Date()]);
  const bcrypt = require('bcryptjs');
  const email = `sol${Date.now()}@example.com`;
  await pool.execute('INSERT INTO users (email, password_hash) VALUES (?, ?)', [email, await bcrypt.hash('longenoughpassword1', 4)]);
  const [rows] = await pool.execute('SELECT id, token_epoch FROM users WHERE email = ?', [email]);
  const token = authModule.signToken({ id: rows[0].id, email, token_epoch: rows[0].token_epoch });

  const plan = await request('GET', `/api/roots/anchor-plan/${DAY}?chain=solana`);
  assert.deepStrictEqual([plan.status, plan.data.memo], [200, MEMO]);
  const failed = await request('POST', '/api/roots/anchor', { token, body: { day: DAY, tx_hash: SIG_FAILED, chain: 'solana' } });
  assert.strictEqual(failed.status, 400, 'a failed transaction anchors nothing');
  const ok = await request('POST', '/api/roots/anchor', { token, body: { day: DAY, tx_hash: `  ${SIG} `, chain: 'solana' } });
  assert.strictEqual(ok.status, 200, JSON.stringify(ok.data));
  assert.strictEqual(ok.data.anchor_tx, SIG, 'exactly as signed: never lower-cased');
  assert.strictEqual(ok.data.explorer, `https://solscan.io/tx/${SIG}`);
  const [stored] = await pool.execute('SELECT tx, from_addr FROM seal_root_anchors WHERE day = ? AND chain = ?', [DAY, 'solana']);
  assert.deepStrictEqual([stored[0].tx, stored[0].from_addr], [SIG, PAYER]);
  const again = await request('POST', '/api/roots/anchor', { token, body: { day: DAY, tx_hash: SIG.toLowerCase(), chain: 'solana' } });
  assert.strictEqual(again.status, 409, 'one anchor per chain per day');
  const q = await request('GET', `/api/roots/quorum/${DAY}`);
  assert.strictEqual(q.status, 200);
  assert.ok(q.data.groups.includes('solana'), JSON.stringify(q.data.groups));
  const a = q.data.anchors.find((x) => x.chain === 'solana');
  assert.deepStrictEqual([a.status, a.anchor_tx, a.counts_toward_quorum], ['verified', SIG, true]);
});
