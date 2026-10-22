'use strict';
/**
 * Two things the server knows that must not leave it, found while building
 * Memeclaw on the chain registry and the daily roots (21 October 2026).
 *
 * 1. An RPC URL from the environment. A provider's URL usually carries its
 *    key, and the registry handed the override to every page and wallet
 *    (/api/roots/chains, and the anchor plan's instructions).
 * 2. A day's root computed from a partial read. The root is stored once, for
 *    good; a seal table that failed to answer was skipped as if it were
 *    empty, so its seals could never be proven for that day.
 */
process.env.JWT_SECRET = process.env.JWT_SECRET || 'j'.repeat(64);
delete process.env.DATABASE_URL;

const test = require('node:test');
const assert = require('node:assert');

const KEYED = 'https://dogeos-testnet.example-provider.io/v1/sk_live_SECRETKEY123';
const KEYED_BASE = 'https://base.example-provider.io/v2/k3y-abc';

test('an RPC override is used by the server and never served to a page or a wallet', async () => {
  const anchor = require('../lib/root_anchor');
  const env = { DOGEOS_TESTNET_RPC_URLS: KEYED, WEB3_RPC_URL_BASE_ANCHOR: KEYED_BASE };
  // The server's reads use it ...
  assert.deepEqual(anchor.chainInfo('dogeos-testnet', env).rpcs, [KEYED]);
  // ... and nothing a page is handed carries it.
  const served = JSON.stringify(anchor.publicChains(env));
  assert.ok(!served.includes('SECRETKEY') && !served.includes('k3y-abc'), served);
  const doge = anchor.publicChains(env).find((c) => c.key === 'dogeos-testnet');
  assert.deepEqual(doge.rpcs, anchor.DOGEOS_TESTNET_PUBLIC_RPCS, 'pages get the keyless default');
  // Unset, the server reads through the official endpoint and dRPC's; a page
  // is still handed the official one only.
  assert.deepEqual(anchor.chainInfo('dogeos-testnet', {}).rpcs, anchor.DOGEOS_TESTNET_RPCS);
  assert.ok(anchor.DOGEOS_TESTNET_RPCS.length >= 2, 'the server has a second reader');
  assert.deepEqual(anchor.publicChains({}).find((c) => c.key === 'dogeos-testnet').rpcs, anchor.DOGEOS_TESTNET_PUBLIC_RPCS);

  // The route, end to end, with the override in the process environment.
  const before = { ...process.env };
  Object.assign(process.env, env);
  try {
    const express = require('express');
    const app = express();
    app.use('/api/roots', require('../routes/roots'));
    const server = app.listen(0, '127.0.0.1');
    await new Promise((r) => server.once('listening', r));
    try {
      const base = `http://127.0.0.1:${server.address().port}`;
      const body = await (await fetch(`${base}/api/roots/chains`)).text();
      assert.ok(!body.includes('SECRETKEY') && !body.includes('k3y-abc'), body);
      assert.ok(body.includes('rpc.testnet.dogeos.com'), 'the public endpoint is still served');
    } finally {
      server.close();
    }
  } finally {
    for (const k of Object.keys(env)) {
      if (before[k] === undefined) delete process.env[k]; else process.env[k] = before[k];
    }
  }
});

test('the anchor plan names the public endpoint, not the override', () => {
  const anchor = require('../lib/root_anchor');
  const before = process.env.DOGEOS_TESTNET_RPC_URLS;
  process.env.DOGEOS_TESTNET_RPC_URLS = KEYED;
  try {
    const plan = anchor.buildAnchorPlan('2026-10-20', 'a'.repeat(64), 'dogeos-testnet');
    const text = JSON.stringify(plan);
    assert.ok(!text.includes('SECRETKEY'), text);
    assert.ok(text.includes('rpc.testnet.dogeos.com'));
  } finally {
    if (before === undefined) delete process.env.DOGEOS_TESTNET_RPC_URLS;
    else process.env.DOGEOS_TESTNET_RPC_URLS = before;
  }
});

test('a seal table that fails to answer leaves the day uncomputed; a missing table is skipped', async () => {
  const db = require('../db');
  const roots = require('../lib/seal_roots');
  const day = '2026-10-19';
  const real = db.pool.execute.bind(db.pool);
  const seen = [];
  let failWith = null;
  db.pool.execute = async (sql, params) => {
    if (/FROM agents WHERE sealed_at/.test(sql)) {
      seen.push(sql);
      if (failWith) throw failWith;
      return [[{ seal: 'b'.repeat(64) }], []];
    }
    if (/^SELECT seal FROM|^SELECT settle_seal/.test(sql)) return [[], []];
    if (/FROM seal_roots WHERE day = \?/.test(sql)) return [[], []];
    if (/^INSERT INTO seal_roots/.test(sql)) { seen.push('STORED'); return [{ affectedRows: 1 }, []]; }
    return real(sql, params);
  };
  try {
    // A read that did not happen: nothing is stored, and the caller is told.
    failWith = Object.assign(new Error('Connection lost'), { code: 'PROTOCOL_CONNECTION_LOST' });
    await assert.rejects(roots.rootForDay(day), /Connection lost/);
    assert.ok(!seen.includes('STORED'), 'a root without that table\'s seals must not be stored');

    // A table this deployment has not created holds no seals: skipped.
    seen.length = 0;
    failWith = Object.assign(new Error("Table 'x.agents' doesn't exist"), { code: 'ER_NO_SUCH_TABLE', errno: 1146 });
    assert.equal(await roots.rootForDay(day), null, 'nothing else sealed that day');

    // The other arm: the table answers, and its seal is in the stored root.
    seen.length = 0;
    failWith = null;
    const row = await roots.rootForDay(day);
    assert.ok(row && row.seal_count === 1 && JSON.parse(row.leaves)[0] === 'b'.repeat(64));
    assert.ok(seen.includes('STORED'));

    // The feed still renders the days it has: a failing day is left out.
    failWith = Object.assign(new Error('Connection lost'), { code: 'PROTOCOL_CONNECTION_LOST' });
    await assert.doesNotReject(roots.listRoots(5, 2));
  } finally {
    db.pool.execute = real;
  }
});
