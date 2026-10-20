'use strict';
/**
 * A day's root is anchored on more than one chain, each anchor its own fact.
 *
 * Before 2026-10-19 an anchor was one column on the root's row and one chain,
 * Base. The Vibe Code on DOGE entry anchors the same roots on the DogeOS
 * testnet, so an anchor is a row per (day, chain), written only after THAT
 * chain confirmed the calldata, first write per chain wins, and the Base leg
 * is mirrored into the older column that receipts and pages still read.
 *
 * Driven end to end through the routes with the chain stubbed at the RPC:
 * the plan names the chain the caller asked for, the submit route verifies
 * on that chain and no other, the feed lists every chain's anchor, the
 * verify route answers per chain, and a chain the registry does not know is
 * refused before anything is read.
 */
// The submit route is authenticated; a token needs the secret the auth module signs with.
process.env.JWT_SECRET = process.env.JWT_SECRET || 'test-secret-'.repeat(4);

const test = require('node:test');
const assert = require('node:assert');
const http = require('node:http');
const express = require('express');

const { pool } = require('../db');
const authModule = require('../auth');
const roots = require('../lib/seal_roots');
const { payloadFor, chains, DOGEOS_TESTNET_RPCS, BASE_RPCS } = require('../lib/root_anchor');

let server, base;
test.before(async () => {
  const app = express();
  app.use(express.json());
  app.use('/api/roots', require('../routes/roots'));
  await new Promise((res) => { server = app.listen(0, '127.0.0.1', res); });
  base = `http://127.0.0.1:${server.address().port}`;
});
test.after(() => { if (server) server.close(); });

function request(method, p, { token, body } = {}) {
  return new Promise((resolve, reject) => {
    const data = body ? JSON.stringify(body) : null;
    const req = http.request(`${base}${p}`, {
      method,
      headers: {
        accept: 'application/json',
        ...(data ? { 'content-type': 'application/json' } : {}),
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
      },
    }, (res) => {
      let d = ''; res.on('data', (c) => d += c);
      res.on('end', () => resolve({ status: res.statusCode, data: d ? JSON.parse(d) : {} }));
    });
    req.on('error', reject);
    if (data) req.write(data);
    req.end();
  });
}

let seq = 0;
async function newUser() {
  const bcrypt = require('bcryptjs');
  const email = `anchor${Date.now()}_${seq++}@example.com`;
  await pool.execute('INSERT INTO users (email, password_hash) VALUES (?, ?)', [email, await bcrypt.hash('longenoughpassword1', 4)]);
  const [rows] = await pool.execute('SELECT id, token_epoch FROM users WHERE email = ?', [email]);
  return { id: rows[0].id, token: authModule.signToken({ id: rows[0].id, email, token_epoch: rows[0].token_epoch }) };
}

// A completed day with a stored root: the feed reads the stored row first,
// so no seals need minting to drive the anchor routes.
const DAY = roots.dayOf(new Date(Date.now() - 3 * 24 * 3600 * 1000));
const ROOT = '7'.repeat(64);
const TX_DOGE = '0x' + 'd'.repeat(64);
const TX_BASE = '0x' + 'b'.repeat(64);
const TX_UNMINED = '0x' + 'e'.repeat(64);
const TX_WRONG = '0x' + 'f'.repeat(64);

/**
 * The chains, stubbed at the RPC: each endpoint knows only the transactions
 * sent on ITS chain, so a hash verified on DogeOS is unknown to Base and the
 * other way round — the claim "anchored on X" is checked on X.
 */
const LEDGER = {
  dogeos: {
    [TX_DOGE]: { hash: TX_DOGE, blockHash: '0x1', input: payloadFor(DAY, ROOT), from: '0xd06e' },
    [TX_UNMINED]: { hash: TX_UNMINED, blockHash: null, input: payloadFor(DAY, ROOT) },
    [TX_WRONG]: { hash: TX_WRONG, blockHash: '0x2', input: payloadFor(DAY, '1'.repeat(64)), from: '0xd06e' },
  },
  base: {
    [TX_BASE]: { hash: TX_BASE, blockHash: '0x3', input: payloadFor(DAY, ROOT), from: '0xba5e' },
  },
};
const BLOCKS = { '0x1': { timestamp: '0x68f4d2c0' }, '0x2': { timestamp: '0x68f4d2c1' }, '0x3': { timestamp: '0x68f4d2c2' } };
const realFetch = global.fetch;
const rpcCalls = [];
test.before(() => {
  global.fetch = async (url, opts) => {
    const { method, params } = JSON.parse(opts.body);
    const ledger = DOGEOS_TESTNET_RPCS.includes(url) ? LEDGER.dogeos : BASE_RPCS.includes(url) ? LEDGER.base : null;
    rpcCalls.push({ url, method, hash: params[0] });
    if (!ledger) return { ok: false, status: 502, json: async () => ({}) };
    const result = method === 'eth_getTransactionByHash' ? (ledger[params[0]] || null)
      : method === 'eth_getBlockByHash' ? (BLOCKS[params[0]] || null) : null;
    return { ok: true, json: async () => ({ jsonrpc: '2.0', result }) };
  };
});
test.after(() => { global.fetch = realFetch; });

test('the registry is served, and the plan names the chain the caller asked for', async () => {
  await pool.execute('INSERT INTO seal_roots (day, root, seal_count, leaves, computed_at) VALUES (?, ?, ?, ?, ?)',
    [DAY, ROOT, 3, JSON.stringify(['1'.repeat(64), '2'.repeat(64), '3'.repeat(64)]), new Date()]);
  const c = await request('GET', '/api/roots/chains');
  assert.equal(c.status, 200);
  assert.equal(c.data.default, 'base');
  const doge = c.data.chains.find((x) => x.key === 'dogeos-testnet');
  assert.ok(doge, 'the DogeOS testnet is in the registry');
  assert.equal(doge.chain_id, 6281971);
  assert.equal(doge.native.symbol, 'DOGE');
  assert.ok(doge.rpcs.length >= 1 && doge.explorer_tx.startsWith('https://'));
  assert.deepEqual(Object.keys(chains()).sort(), ['base', 'dogeos-testnet']);

  const plan = await request('GET', `/api/roots/anchor-plan/${DAY}?chain=dogeos-testnet`);
  assert.equal(plan.status, 200);
  assert.equal(plan.data.chain_id, 6281971);
  assert.equal(plan.data.chain_key, 'dogeos-testnet');
  assert.equal(plan.data.data, payloadFor(DAY, ROOT));
  assert.equal(plan.data.dry_run, true);
  const basePlan = await request('GET', `/api/roots/anchor-plan/${DAY}`);
  assert.equal(basePlan.data.chain_id, 8453, 'no chain named is Base, as before');
  const nope = await request('GET', `/api/roots/anchor-plan/${DAY}?chain=solana`);
  assert.equal(nope.status, 400);
});

test('a hash is verified on the chain it names, recorded once per chain, and listed per chain', async () => {
  const u = await newUser();
  // A transaction on DogeOS does not anchor the day on Base: the chain is the one asked.
  const wrongChain = await request('POST', '/api/roots/anchor', { token: u.token, body: { day: DAY, tx_hash: TX_DOGE, chain: 'base' } });
  assert.equal(wrongChain.status, 400, 'Base has never seen this hash');
  assert.match(wrongChain.data.reason, /no such transaction on Base/);

  const unmined = await request('POST', '/api/roots/anchor', { token: u.token, body: { day: DAY, tx_hash: TX_UNMINED, chain: 'dogeos-testnet' } });
  assert.equal(unmined.status, 503, 'not yet mined is not a verdict — try again');
  const wrong = await request('POST', '/api/roots/anchor', { token: u.token, body: { day: DAY, tx_hash: TX_WRONG, chain: 'dogeos-testnet' } });
  assert.equal(wrong.status, 400, 'calldata for another root does not anchor this one');

  const ok = await request('POST', '/api/roots/anchor', { token: u.token, body: { day: DAY, tx_hash: TX_DOGE, chain: 'dogeos-testnet' } });
  assert.equal(ok.status, 200, JSON.stringify(ok.data));
  assert.equal(ok.data.chain, 'dogeos-testnet');
  assert.equal(ok.data.from, '0xd06e');
  assert.equal(ok.data.block_time, new Date(0x68f4d2c0 * 1000).toISOString());
  assert.match(ok.data.explorer, /dogeos-testnet\.l2scan\.co\/tx\/0xd/);

  const again = await request('POST', '/api/roots/anchor', { token: u.token, body: { day: DAY, tx_hash: TX_DOGE, chain: 'dogeos-testnet' } });
  assert.equal(again.status, 409, 'first anchor per chain wins');

  // The Base column stays empty: the DogeOS anchor is DogeOS's.
  const [rows] = await pool.execute('SELECT anchor_tx FROM seal_roots WHERE day = ?', [DAY]);
  assert.equal(rows[0].anchor_tx || null, null);
  const vBase = await request('GET', `/api/roots/verify/${DAY}?chain=base`);
  assert.equal(vBase.data.status, 'unanchored');
  const vDoge = await request('GET', `/api/roots/verify/${DAY}?chain=dogeos-testnet`);
  assert.equal(vDoge.data.status, 'verified');
  assert.equal(vDoge.data.chain_name, 'DogeOS testnet');
  assert.equal(vDoge.data.anchor_tx, TX_DOGE);

  // Base gets its own anchor, mirrored into the older column. A second
  // player submits it: the submit route allows six a minute per user, and
  // that budget is the route's, not this test's to spend on one account.
  const u2 = await newUser();
  const okBase = await request('POST', '/api/roots/anchor', { token: u2.token, body: { day: DAY, tx_hash: TX_BASE, chain: 'base' } });
  assert.equal(okBase.status, 200, JSON.stringify(okBase.data));
  const [after] = await pool.execute('SELECT anchor_tx FROM seal_roots WHERE day = ?', [DAY]);
  assert.equal(after[0].anchor_tx, TX_BASE, 'the Base leg keeps its older spelling for the pages that read it');

  const feed = await request('GET', '/api/roots');
  const row = feed.data.roots.find((r) => r.day === DAY);
  assert.ok(row, 'the day is in the feed');
  assert.deepEqual(row.anchors.map((a) => [a.chain, a.tx]), [['base', TX_BASE], ['dogeos-testnet', TX_DOGE]]);
  assert.equal(row.anchor_tx, TX_BASE);
  assert.ok(Array.isArray(feed.data.chains) && feed.data.chains.length === 2, 'the feed carries the registry for the pages');

  // Every verification went to the chain it was about, and only there.
  const dogeChecks = rpcCalls.filter((c) => c.hash === TX_DOGE && c.method === 'eth_getTransactionByHash');
  assert.ok(dogeChecks.length >= 1 && dogeChecks.every((c) => DOGEOS_TESTNET_RPCS.includes(c.url) || BASE_RPCS.includes(c.url)));
  const unknownChain = await request('POST', '/api/roots/anchor', { token: u2.token, body: { day: DAY, tx_hash: TX_DOGE, chain: 'solana' } });
  assert.equal(unknownChain.status, 400);
});

test('the receipt payload carries every chain\'s anchor, the Base leg in its older spelling too', async () => {
  const a = await roots.anchorFor('1'.repeat(64), new Date(DAY + 'T12:00:00Z'));
  assert.ok(a, 'a committed leaf has an anchor');
  assert.deepEqual(a.anchors.map((x) => x.chain), ['base', 'dogeos-testnet']);
  assert.equal(a.anchor_tx, TX_BASE);
  assert.equal(new Date(a.anchored_at).toISOString(), new Date(0x68f4d2c2 * 1000).toISOString());
});

test('an older Base-only row is read as a Base anchor, and the shim is first-write-wins per chain', async () => {
  const OLD = roots.dayOf(new Date(Date.now() - 9 * 24 * 3600 * 1000));
  await pool.execute('INSERT INTO seal_roots (day, root, seal_count, leaves, computed_at) VALUES (?, ?, ?, ?, ?)',
    [OLD, '8'.repeat(64), 1, JSON.stringify(['8'.repeat(64)]), new Date()]);
  await pool.execute('UPDATE seal_roots SET anchor_tx = ?, anchored_at = ? WHERE day = ? AND anchor_tx IS NULL', ['0x' + '9'.repeat(64), new Date(), OLD]);
  const row = await roots.rootForDay(OLD);
  const list = await roots.anchorsFor(OLD, row);
  assert.deepEqual(list.map((x) => [x.chain, x.tx, !!x.legacy]), [['base', '0x' + '9'.repeat(64), true]]);
  // The table refuses a second row for the same (day, chain).
  const first = await roots.recordAnchor(OLD, 'dogeos-testnet', '0x' + 'a'.repeat(64), { block_time: '2026-10-10T00:00:00Z', from: '0x1' });
  const second = await roots.recordAnchor(OLD, 'dogeos-testnet', '0x' + 'c'.repeat(64), { block_time: '2026-10-10T00:00:00Z', from: '0x2' });
  assert.equal(first, true);
  assert.equal(second, false);
  const now = await roots.anchorsFor(OLD, row);
  assert.deepEqual(now.map((x) => [x.chain, x.tx]), [['base', '0x' + '9'.repeat(64)], ['dogeos-testnet', '0x' + 'a'.repeat(64)]]);
});
