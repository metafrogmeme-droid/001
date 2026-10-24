'use strict';

const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const express = require('express');

const APP = path.join(__dirname, '..');
const ROOT = path.join(APP, '..');
const indexer = require('../lib/sar/indexer');
const packet = require('../lib/sar/packet');
const metrics = require('../lib/sar/metrics');
const canary = require('../lib/sar/canary');
const sources = require('../lib/sar/sources');
const router = require('../routes/sar_oracle');
const { flag_on, sendPage } = require('../lib/sar_oracle_flag');
const { DB_FREE_API } = require('../lib/not_ready_gate');

const NOW = 1700000000000;
const NODE = 'http://127.0.0.1:3001/info';

function json(status, body) {
  return { ok: status === 200, status, json: async () => body };
}

function metaBody() {
  return [
    { universe: [{ name: 'BTC' }, { name: 'ETH' }, { name: 'SOL' }] },
    [
      { openInterest: '10', markPx: '100' },
      { openInterest: '10', markPx: '100' },
      { openInterest: '10', markPx: '100' },
    ],
  ];
}

function publicBook(bids, asks) {
  const side = (n, px) => Array.from({ length: n }, () => ({ px, sz: '10', n: 2 }));
  return { levels: [side(bids, '99'), side(asks, '101')], time: NOW };
}

function nodeBook(withUser) {
  const bid = { px: '99', sz: '8', n: 1, t: NOW - 60000 };
  const ask = { px: '101', sz: '2', n: 1, t: NOW - 60000 };
  if (withUser) {
    bid.user = '0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa';
    ask.user = '0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb';
  }
  return { levels: [[bid], [ask]], time: NOW };
}

function fakeFetch(withUser) {
  return async (url, opts) => {
    const u = String(url);
    if (!opts || opts.method === 'GET') return json(200, {});
    const body = JSON.parse(opts.body);
    if (u === NODE) {
      if (body.type === 'l4Snapshots') return json(200, nodeBook(withUser));
      if (body.type === 'liquidatable') {
        return json(200, [
          { coin: 'BTC', markPx: '100', positions: [{ positionValue: '5', liquidationPx: '99' }] },
          { coin: 'ETH', positions: [] },
          { coin: 'SOL', positions: [] },
        ]);
      }
      if (body.type === 'exchangeStatus') return json(200, { time: NOW, l1Block: 12345 });
    }
    if (body.type === 'metaAndAssetCtxs') return json(200, metaBody());
    if (body.type === 'l2Book') return json(200, publicBook(2, 2));
    return json(404, null);
  };
}

function envFor(extra) {
  return Object.assign({
    SAR_ORACLE_HL_NODE_URL: NODE,
    SAR_ORACLE_CHAIN_ID: '84532',
  }, extra || {});
}

async function runCycle(fetchImpl, extra) {
  indexer.resetForTests();
  return indexer.cycle({
    fetch: fetchImpl,
    sleep: () => Promise.resolve(),
    now: () => NOW,
    env: envFor(extra),
  });
}

function listen() {
  const app = express();
  app.set('trust proxy', 1);
  app.use('/api/sar-oracle', router);
  const server = http.createServer(app);
  return new Promise((resolve) => {
    server.listen(0, '127.0.0.1', () => {
      const base = `http://127.0.0.1:${server.address().port}`;
      resolve({
        base,
        close: () => new Promise((r) => server.close(r)),
      });
    });
  });
}

async function hit(base, p, ip) {
  const res = await fetch(base + p, { headers: { 'x-forwarded-for': ip } });
  const text = await res.text();
  return { status: res.status, text };
}

function mockRes() {
  return {
    statusCode: 0,
    body: null,
    headersSent: false,
    status(n) { this.statusCode = n; return this; },
    type() { return this; },
    setHeader() { return this; },
    send(b) { this.body = b; this.headersSent = true; return this; },
  };
}

test('SaR public feed', { concurrency: false }, async (t) => {
  const saved = process.env.SAR_ORACLE_ENABLED;
  t.after(() => {
    indexer.resetForTests();
    canary.resetForTests();
    router.setOperatorCheckForTests(null);
    if (saved == null) delete process.env.SAR_ORACLE_ENABLED;
    else process.env.SAR_ORACLE_ENABLED = saved;
  });

  await t.test('public payloads carry no *_usd_* fields', async () => {
    const pub = await runCycle(fakeFetch(true));
    const op = indexer.operatorSnapshot(NOW, envFor());
    const btc = op.markets.find((m) => m.market === 'BTC' && m.venue === 'hyperliquid');
    assert.equal(btc.operator_amounts.oi_usd_e6, '1000000000');
    assert.equal(typeof btc.operator_amounts.oi_usd_e6, 'string');
    const pubText = JSON.stringify(pub);
    const snapText = JSON.stringify(indexer.snapshot(NOW, envFor()));
    assert.equal(pubText.includes('_usd_'), false);
    assert.equal(snapText.includes('_usd_'), false);
    assert.equal(pubText.includes('operator_amounts'), false);
    process.env.SAR_ORACLE_ENABLED = '1';
    let checks = 0;
    router.setOperatorCheckForTests(() => { checks += 1; return false; });
    const srv = await listen();
    try {
      const open = await hit(srv.base, '/api/sar-oracle/packets', '198.51.100.21');
      assert.equal(open.status, 200);
      assert.equal(open.text.includes('_usd_'), false);
      assert.equal(checks, 0);
      const denied = await hit(srv.base, '/api/sar-oracle/admin/packets', '198.51.100.22');
      assert.equal(denied.status, 404);
      router.setOperatorCheckForTests(() => true);
      const allowed = await hit(srv.base, '/api/sar-oracle/admin/packets', '198.51.100.23');
      assert.equal(allowed.status, 200);
      assert.equal(allowed.text.includes('oi_usd_e6'), true);
      assert.equal(JSON.parse(allowed.text).markets.find((m) => m.market === 'BTC' && m.venue === 'hyperliquid').operator_amounts.oi_usd_e6, '1000000000');
    } finally {
      await srv.close();
    }
  });

  await t.test('non-admins never see dollar fields', async () => {
    await runCycle(fakeFetch(true));
    const op = indexer.operatorSnapshot(NOW, envFor());
    const row = op.markets.find((m) => m.market === 'BTC' && m.venue === 'hyperliquid');
    const keys = ['oi_usd_e6', 'ld1_usd_e6', 'ld2_usd_e6', 'ld5_usd_e6', 'tsar95_usd_e6'];
    for (const k of keys) {
      assert.equal(Object.prototype.hasOwnProperty.call(row.operator_amounts, k), true, k);
    }
    assert.equal(row.operator_amounts.oi_usd_e6, '1000000000');
    assert.equal(row.operator_amounts.ld1_usd_e6, '5000000');
    assert.equal(row.operator_amounts.ld2_usd_e6, '5000000');
    assert.equal(row.operator_amounts.ld5_usd_e6, '5000000');
    assert.equal(row.operator_amounts.tsar95_usd_e6, null);
    process.env.SAR_ORACLE_ENABLED = '1';
    router.setOperatorCheckForTests((req) => req.get('x-plan') === 'admin');
    const srv = await listen();
    const absent = (text, who) => {
      assert.equal(text.includes('_usd_'), false, who);
      for (const k of keys) assert.equal(text.includes(k), false, `${who} ${k}`);
    };
    const ask = (p, ip, plan) => fetch(srv.base + p, {
      headers: Object.assign({ 'x-forwarded-for': ip }, plan ? { 'x-plan': plan } : {}),
    }).then(async (res) => ({ status: res.status, text: await res.text() }));
    try {
      const callers = [
        ['198.51.100.71', null],
        ['198.51.100.72', 'member'],
        ['198.51.100.73', 'signed-in'],
      ];
      const paths = [
        '/api/sar-oracle/packets',
        '/api/sar-oracle/packets?market=BTC',
        '/api/sar-oracle/admin/packets',
        '/api/sar-oracle/admin/packets?market=BTC',
      ];
      for (const [ip, plan] of callers) {
        for (const p of paths) {
          const res = await ask(p, ip, plan);
          if (p.includes('/admin/')) assert.equal(res.status, 404, p);
          else assert.equal(res.status, 200, p);
          absent(res.text, `${plan || 'anonymous'} ${p}`);
        }
      }
      const adminPublic = await ask('/api/sar-oracle/packets?market=BTC', '198.51.100.74', 'admin');
      assert.equal(adminPublic.status, 200);
      absent(adminPublic.text, 'admin public');
      const admin = await ask('/api/sar-oracle/admin/packets?market=BTC', '198.51.100.75', 'admin');
      assert.equal(admin.status, 200);
      const got = JSON.parse(admin.text).markets.find((m) => m.market === 'BTC' && m.venue === 'hyperliquid');
      for (const k of keys) {
        assert.equal(Object.prototype.hasOwnProperty.call(got.operator_amounts, k), true, k);
      }
      assert.equal(got.operator_amounts.oi_usd_e6, '1000000000');
      assert.equal(got.operator_amounts.ld2_usd_e6, '5000000');
      const closed = mockRes();
      await sendPage({ get() { return null; } }, closed, { isOperator() { return false; } });
      assert.equal(closed.statusCode, 404);
      absent(String(closed.body), 'page');
    } finally {
      await srv.close();
    }
  });

  await t.test('a closed flag does not ask who is calling and does not spend the window', async () => {
    delete process.env.SAR_ORACLE_ENABLED;
    assert.equal(flag_on(), false);
    let checks = 0;
    router.setOperatorCheckForTests(() => { checks += 1; return true; });
    const srv = await listen();
    try {
      for (let i = 0; i < 40; i += 1) {
        const r = await hit(srv.base, '/api/sar-oracle/packets', '198.51.100.30');
        assert.equal(r.status, 404);
        const a = await hit(srv.base, '/api/sar-oracle/admin/packets', '198.51.100.30');
        assert.equal(a.status, 404);
      }
      assert.equal(checks, 0);
      process.env.SAR_ORACLE_ENABLED = '1';
      const next = await hit(srv.base, '/api/sar-oracle/packets', '198.51.100.30');
      assert.equal(next.status, 200);
    } finally {
      await srv.close();
    }
  });

  await t.test('the 31st public read is refused, and a bad market is not', async () => {
    process.env.SAR_ORACLE_ENABLED = '1';
    const srv = await listen();
    try {
      for (let i = 0; i < 30; i += 1) {
        const r = await hit(srv.base, '/api/sar-oracle/packets?market=BTC', '198.51.100.40');
        assert.equal(r.status, 200);
      }
      const over = await hit(srv.base, '/api/sar-oracle/packets', '198.51.100.40');
      assert.equal(over.status, 429);
      for (let i = 0; i < 40; i += 1) {
        const bad = await hit(srv.base, '/api/sar-oracle/packets?market=DOGE', '198.51.100.41');
        assert.equal(bad.status, 400);
      }
      const still = await hit(srv.base, '/api/sar-oracle/packets?market=ETH', '198.51.100.41');
      assert.equal(still.status, 200);
    } finally {
      await srv.close();
    }
  });

  await t.test('admin refusals do not spend the operator window', async () => {
    process.env.SAR_ORACLE_ENABLED = '1';
    router.setOperatorCheckForTests(() => false);
    const srv = await listen();
    try {
      for (let i = 0; i < 31; i += 1) {
        const r = await hit(srv.base, '/api/sar-oracle/admin/packets', '198.51.100.50');
        assert.equal(r.status, 404);
      }
      router.setOperatorCheckForTests(() => true);
      const ok = await hit(srv.base, '/api/sar-oracle/admin/packets', '198.51.100.50');
      assert.equal(ok.status, 200);
    } finally {
      await srv.close();
    }
  });

  await t.test('the route file has no write method and no dollar key', () => {
    const src = fs.readFileSync(path.join(APP, 'routes', 'sar_oracle.js'), 'utf8');
    assert.equal(/router\.(post|put|delete|patch)\s*\(/i.test(src), false);
    assert.equal(src.includes('_usd_'), false);
    assert.equal(DB_FREE_API.has('/api/sar-oracle/packets'), true);
    assert.equal(DB_FREE_API.has('/api/sar-oracle/admin/packets'), false);
  });

  await t.test('a signer key is not read off Base Sepolia', () => {
    let reads = 0;
    const cfg = packet.signerConfig({
      SAR_ORACLE_CHAIN_ID: '1',
      get SAR_ORACLE_SIGNING_KEY() { reads += 1; return '0x' + 'ab'.repeat(32); },
    });
    assert.equal(cfg.reason, 'not_base_sepolia');
    assert.equal(reads, 0);
  });

  await t.test('the runtime copy matches the canonical files', () => {
    const a = path.join(ROOT, 'tools', 'sar-oracle', 'runtime');
    const b = path.join(APP, 'lib', 'sar');
    const names = fs.readdirSync(a).sort();
    assert.deepStrictEqual(fs.readdirSync(b).sort(), names);
    for (const name of names) {
      assert.equal(
        fs.readFileSync(path.join(a, name)).equals(fs.readFileSync(path.join(b, name))),
        true,
        name,
      );
    }
  });

  await t.test('the pinned metric leaf matches', () => {
    const leaf = packet.metricLeaf({
      asset: 0n, dex: 0n, l1Block: 100n, ts: 1000n, seq: 1n,
      sAdjBps: 150n, neffE2: 160n, cr1Bps: 7500n,
      ld2UsdE6: 5000000n, tsar95UsdE6: 0n, flags: 0n,
    });
    assert.equal(leaf, '0xe6567fc691c1dc84feceef775c4646f1be33369e086c85e48f338f8998ef44dd');
  });

  await t.test('a public book does not become account concentration', async () => {
    const pub = await runCycle(fakeFetch(false), { SAR_ORACLE_HL_NODE_URL: '' });
    const btc = pub.markets.find((m) => m.market === 'BTC' && m.venue === 'hyperliquid');
    assert.equal(btc.n_eff.reason, 'no_account_book');
    assert.equal(btc.schema.s_adj_bps, null);
    assert.equal(btc.schema.neff_e2, null);
  });

  await t.test('twenty public levels set the truncated bit and a missing user does not', async () => {
    const wide = async (url, opts) => {
      const base = await fakeFetch(false)(url, opts);
      if (opts && opts.body && JSON.parse(opts.body).type === 'l2Book') return json(200, publicBook(20, 1));
      return base;
    };
    const pub = await runCycle(wide);
    const btc = pub.markets.find((m) => m.market === 'BTC' && m.venue === 'hyperliquid');
    assert.equal(btc.schema.flags & 2, 2);
    assert.equal(btc.schema.neff_reason, 'no_account_book');
    const op = indexer.operatorSnapshot(NOW, envFor());
    assert.equal(JSON.stringify(indexer.snapshot(NOW, envFor())).includes('_usd_'), false);
    assert.equal(op.markets.find((m) => m.market === 'BTC').operator_amounts.oi_usd_e6, '1000000000');
  });

  await t.test('account haircut is not the order stand-in', async () => {
    const pub = await runCycle(fakeFetch(true));
    const btc = pub.markets.find((m) => m.market === 'BTC' && m.venue === 'hyperliquid');
    assert.equal(btc.order_n_eff.reason, 'order_stand_in');
    assert.equal(btc.n_eff.reason, null);
    assert.notEqual(btc.n_eff.value, btc.order_n_eff.value);
    assert.equal(btc.schema.s_adj_reason, 'twap_incomplete');
    assert.equal(btc.schema.dominant_account, true);
    assert.equal(JSON.stringify(pub).includes('bigint'), false);
  });

  await t.test('the canary latch only tightens', async () => {
    canary.resetForTests();
    const forced = await canary.readCanary({ env: { SAR_ORACLE_CANARY_TRIPPED: '1' } });
    assert.equal(forced.tripped, true);
    const failed = await canary.readCanary({
      env: {
        SAR_ORACLE_CANARY_ADDRESS: '0x1111111111111111111111111111111111111111',
        SAR_ORACLE_CANARY_RPC: 'http://127.0.0.1:9',
        SAR_ORACLE_CANARY_BASE_NONCE: '0',
      },
      fetch: async () => { throw new Error('down'); },
    });
    assert.equal(failed.tripped, true);
    const plain = sources.parseL4({ levels: [[{ px: '1', sz: '1', n: 1 }], [{ px: '2', sz: '1', n: 1 }]] });
    assert.equal(plain.reason, 'no_account_book');
  });

  await t.test('haircut constants match the stated formula', () => {
    const h0 = metrics.haircut(15n * metrics.WAD, metrics.WAD / 4n);
    assert.equal(h0.h, 0n);
    const h = metrics.haircut((15n * metrics.WAD) / 2n, metrics.WAD / 4n);
    assert.equal(h.h, metrics.WAD / 2n);
    const adj = metrics.adjustSlippage(metrics.WAD / 100n, h.h);
    assert.equal(metrics.bpsFromWad(adj), 150n);
    const acct = metrics.fromAccountSizes([3n * metrics.WAD, metrics.WAD]);
    assert.equal(acct.dominant, true);
    assert.equal(acct.cr1.wad, 3n * metrics.WAD / 4n);
  });

  await t.test('the page is operator-only after the flag, and the card stays closed', async () => {
    delete process.env.SAR_ORACLE_ENABLED;
    let checks = 0;
    const closed = mockRes();
    await sendPage({}, closed, { isOperator() { checks += 1; return true; } });
    assert.equal(closed.statusCode, 404);
    assert.equal(checks, 0);
    process.env.SAR_ORACLE_ENABLED = '1';
    const denied = mockRes();
    await sendPage({}, denied, { isOperator() { checks += 1; return false; } });
    assert.equal(denied.statusCode, 404);
    assert.equal(checks, 1);
    const page = mockRes();
    await sendPage({}, page, { isOperator() { return true; } });
    assert.equal(page.headersSent, true);
    assert.equal(page.statusCode, 0);
    assert.match(String(page.body), /Coming soon/);
    assert.equal(String(page.body).includes('_usd_'), false);
    assert.equal(String(page.body).includes('$'), false);
    const server = fs.readFileSync(path.join(APP, 'server.js'), 'utf8');
    const at = server.indexOf("require('./lib/sar/indexer').start()");
    assert.ok(at > 0);
    assert.match(server.slice(at - 200, at), /flag_on\(\)/);
    assert.match(server, /app\.get\('\/tools'/);
  });
});
