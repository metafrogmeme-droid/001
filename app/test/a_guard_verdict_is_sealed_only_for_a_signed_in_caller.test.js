'use strict';
/**
 * POST /api/guard/check — the route, end to end: one verdict for anyone, a
 * sealed receipt only for a signed-in caller, and the request itself never
 * stored by either.
 *
 * The simulation is Base's own recorded answer (test/fixtures/claw_guard/),
 * served through a stubbed fetch, so the route reads what Base sends and the
 * test never leaves the machine. Requests to the route go over node:http,
 * which the stub does not touch.
 */
process.env.JWT_SECRET = process.env.JWT_SECRET || 'j'.repeat(64);
delete process.env.DATABASE_URL;

const test = require('node:test');
const assert = require('node:assert');
const crypto = require('node:crypto');
const fs = require('node:fs');
const http = require('node:http');
const path = require('node:path');
const express = require('express');

const { pool } = require('../db');
const scanSeal = require('../lib/scan_seal');
const { sealsForDay } = require('../lib/seal_roots');
const SR = require('../public/js/sigray-model.js');

const fixture = (name) => JSON.parse(fs.readFileSync(
  path.join(__dirname, 'fixtures', 'claw_guard', `sim_${name}.json`), 'utf8'));

function serve(userId) {
  const app = express();
  app.use(express.json({ limit: '256kb' }));
  // A planted session, as optionalAuth would leave it; with no token sent,
  // optionalAuth itself leaves req.user as it found it.
  app.use((req, _res, next) => { if (userId) req.user = { user_id: userId }; next(); });
  delete require.cache[require.resolve('../routes/guard')];
  app.use('/api/guard', require('../routes/guard'));
  return http.createServer(app);
}

function post(userId, body) {
  return new Promise((resolve, reject) => {
    const s = serve(userId);
    s.listen(0, '127.0.0.1', () => {
      const data = JSON.stringify(body);
      const req = http.request({ port: s.address().port, path: '/api/guard/check', method: 'POST',
        headers: { 'content-type': 'application/json', 'content-length': Buffer.byteLength(data) } }, (res) => {
        let b = '';
        res.on('data', (d) => { b += d; });
        res.on('end', () => { s.close(); resolve({ status: res.statusCode, body: JSON.parse(b || '{}') }); });
      });
      req.on('error', (e) => { s.close(); reject(e); });
      req.end(data);
    });
  });
}

/** Base answers with the recorded reply; every call it was asked is kept. */
function stubBase(answer, asked) {
  const real = global.fetch;
  global.fetch = async (url, init) => {
    asked.push({ url, body: JSON.parse(init.body) });
    if (answer instanceof Error) throw answer;
    return { ok: true, status: 200, json: async () => answer };
  };
  return () => { global.fetch = real; };
}

async function sealCount() {
  const [rows] = await pool.execute('SELECT scan_key FROM scan_seals', []);
  return rows.length;
}

const approve = fixture('unlimited_approve');

test('anyone gets the verdict, simulated on Base, and nothing is stored', async () => {
  const asked = [];
  const restore = stubBase(approve.answer, asked);
  try {
    const before = await sealCount();
    const { status, body } = await post(null, { request: 'tx', tx: approve.tx });
    assert.strictEqual(status, 200);
    assert.strictEqual(body.level, 'stop');
    assert.ok(body.reasons.some((r) => r.id === 'unlimited_approval' && r.sev === 'high'));
    assert.deepStrictEqual(body.simulation.ran, true);
    assert.strictEqual(body.block, body.simulation.block);
    assert.strictEqual(body.receipt, null);
    assert.match(body.note, /Nothing was stored/);
    assert.strictEqual(await sealCount(), before);
    assert.strictEqual(asked.length, 1);
    assert.strictEqual(asked[0].body.method, 'eth_simulateV1');
  } finally {
    restore();
  }
});

test('a signed-in caller gets a v10 receipt that commits to the input without holding it', async () => {
  const asked = [];
  const restore = stubBase(approve.answer, asked);
  try {
    const { body } = await post(42, { request: 'tx', tx: approve.tx });
    assert.ok(body.receipt, 'a signed-in caller was not given a receipt');
    assert.strictEqual(body.receipt.verify, '/call/' + body.receipt.scan_key);
    const row = await scanSeal.byKey(body.receipt.scan_key);
    assert.strictEqual(row.tool, 'claw_guard');
    assert.strictEqual(row.seal, crypto.createHash('sha256').update(row.seal_payload, 'utf8').digest('hex'));
    const p = JSON.parse(row.seal_payload);
    assert.strictEqual(p.v, 10);
    assert.strictEqual(p.kind, 'presign_verdict');
    assert.strictEqual(p.request, 'tx');
    assert.strictEqual(p.level, 'stop');
    assert.ok(p.reasons.includes('unlimited_approval:high'));
    assert.deepStrictEqual(p.simulation, { ran: true, chain_id: 8453, block: body.block, status: 'success', changes: 1 });
    assert.strictEqual(p.input_sha256,
      crypto.createHash('sha256').update(JSON.stringify(approve.tx), 'utf8').digest('hex'));
    // The request is not in the receipt: not the calldata, not the sender, not the spender.
    const raw = row.seal_payload.toLowerCase();
    for (const s of [approve.tx.data.slice(10, 74), approve.tx.from.slice(2), '11'.repeat(20)]) {
      assert.ok(!raw.includes(s.toLowerCase()), `the receipt carries ${s}`);
    }
    // A leaf of the day's root, like every other seal.
    const day = new Date(body.receipt.sealed_at).toISOString().slice(0, 10);
    assert.ok((await sealsForDay(day)).includes(row.seal), 'the verdict is not in its day\'s root');
  } finally {
    restore();
  }
});

test('a simulation that could not run is sealed as not run, and said in the verdict', async () => {
  const restore = stubBase(new Error('down'), []);
  try {
    const { body } = await post(42, { request: 'tx', tx: { ...approve.tx, data: '0xdeadbeef' } });
    assert.strictEqual(body.level, 'unknown');
    assert.deepStrictEqual(body.simulation, { ran: false, reason: 'unreachable' });
    assert.ok(body.reasons.some((r) => r.id === 'sim_unavailable'));
    const p = JSON.parse((await scanSeal.byKey(body.receipt.scan_key)).seal_payload);
    assert.deepStrictEqual(p.simulation, { ran: false, chain_id: 8453, reason: 'unreachable' });
    assert.strictEqual(p.unknown, true);
  } finally {
    restore();
  }
});

test('without a sender nothing is simulated, and Base is not asked', async () => {
  const asked = [];
  const restore = stubBase(approve.answer, asked);
  try {
    const { body } = await post(null, { request: 'tx', tx: { to: approve.tx.to, data: approve.tx.data } });
    assert.strictEqual(body.simulation, null);
    assert.strictEqual(body.level, 'stop');
    assert.strictEqual(asked.length, 0);
  } finally {
    restore();
  }
});

test('a signature and a delegation are checked and sealed with no simulation', async () => {
  const now = Math.floor(Date.now() / 1000);
  const permit = {
    domain: { name: 'T', version: '1', chainId: 8453, verifyingContract: '0x' + '1a'.repeat(20) },
    types: { Permit: SR.STANDARD.Permit2612 }, primaryType: 'Permit',
    message: { owner: '0x' + 'dd'.repeat(20), spender: '0x' + 'ee'.repeat(20),
      value: ((1n << 256n) - 1n).toString(), nonce: '0', deadline: String(now + 400 * 86400) },
  };
  const sig = await post(42, { request: 'typed_data', typed_data: permit });
  assert.strictEqual(sig.body.level, 'stop');
  assert.deepStrictEqual(sig.body.reasons.map((r) => r.id).sort(), ['gasless_permit', 'long_lived', 'unlimited_approval']);
  const p = JSON.parse((await scanSeal.byKey(sig.body.receipt.scan_key)).seal_payload);
  assert.strictEqual(p.request, 'typed_data');
  assert.strictEqual(p.simulation, null);
  assert.deepStrictEqual(p.actions, ['sgr.a_permit']);

  const del = await post(null, { request: 'authorization', authorization: { chainId: 0, address: '0x' + 'ee'.repeat(20), nonce: 0 } });
  assert.strictEqual(del.body.level, 'stop');
  assert.strictEqual(del.body.receipt, null);
});

test('input nothing could read gets no receipt, and a request outside the set is refused', async () => {
  const before = await sealCount();
  const bad = await post(42, { request: 'typed_data', typed_data: 'not typed data' });
  assert.strictEqual(bad.status, 200);
  assert.strictEqual(bad.body.level, 'invalid');
  assert.strictEqual(bad.body.receipt, null);
  assert.strictEqual(await sealCount(), before);
  const other = await post(null, { request: 'swap' });
  assert.strictEqual(other.status, 400);
  const chain = await post(null, { request: 'tx', tx: approve.tx, chain_id: 1 });
  assert.strictEqual(chain.status, 400);
});
