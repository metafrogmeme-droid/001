'use strict';
/**
 * POST /api/bot/sync/signals: a call's outcome only moves forward.
 *
 * The re-sync wrote status, pnl and resolved_at unconditionally, so a late NEW
 * or OPEN for a call already resolved (the publish push landing after the
 * outcome, or a walk retried from an older ledger) put the row back to pending
 * and erased its R. A stored final word now keeps all three against a
 * non-final one. Both arms are driven: pending moves forward, a final word
 * corrects a final word, and the seal is untouched throughout.
 *
 * GET /api/bot/sync/signals/pending: the backfill's read. Bot-secret only,
 * sealed NEW/OPEN rows older than the cutoff, keyset-paged by id.
 */
process.env.BOT_SYNC_SECRET = process.env.BOT_SYNC_SECRET || 's'.repeat(48);
process.env.JWT_SECRET = process.env.JWT_SECRET || 'j'.repeat(64);
delete process.env.DATABASE_URL;

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const express = require('express');

const SS = require('../public/js/signal-status-model.js');
const { pool } = require('../db');
const { codeOnly } = require('./helpers/code_only');

let server; let base;
test.before(async () => {
  const app = express();
  app.use(express.json());
  app.use('/api/bot/sync', require('../routes/sync'));
  await new Promise((r) => { server = app.listen(0, '127.0.0.1', r); });
  base = `http://127.0.0.1:${server.address().port}`;
});
test.after(() => { if (server) server.close(); });

function call(method, p, body, secret = process.env.BOT_SYNC_SECRET) {
  return new Promise((resolve, reject) => {
    const payload = body ? JSON.stringify(body) : null;
    const headers = { 'Content-Type': 'application/json' };
    if (secret !== null) headers['X-Bot-Secret'] = secret;
    const r = http.request(base + p, { method, headers }, (res) => {
      let d = ''; res.on('data', (c) => { d += c; });
      res.on('end', () => resolve({ status: res.statusCode, data: d ? JSON.parse(d) : {} }));
    });
    r.on('error', reject); if (payload) r.write(payload); r.end();
  });
}

const CREATED = '2026-11-01T10:17:00.000Z';
const row = (k, extra = {}) => ({
  signal_key: k, symbol: 'BTC/USDT', direction: 'LONG', confidence: 0.7, score: 0.7,
  entry_price: 100, stop_loss: 95, take_profit: 110, rr: 2, thesis: 't',
  status: 'NEW', pnl: null, created_at: CREATED, resolved_at: '', ...extra,
});
const send = (...rows) => call('POST', '/api/bot/sync/signals', { signals: rows });
const stored = (k) => pool.signals.find((s) => s.signal_key === k);
const AT = '2026-11-01T13:00:00.000Z';

test('a late pending word does not reopen a resolved call, nor erase its R or its time', async () => {
  pool.signals.length = 0;
  await send(row('g1'));
  const seal = stored('g1').seal; const sealedAt = stored('g1').sealed_at;
  assert.ok(seal, 'the call was sealed when it landed');
  await send(row('g1', { status: 'TARGET', pnl: 2, resolved_at: AT }));
  for (const late of ['NEW', 'OPEN', 'new', 'open']) {
    const r = await send(row('g1', { status: late, pnl: null, resolved_at: '' }));
    assert.equal(r.status, 200);
    const s = stored('g1');
    assert.equal(s.status, 'TARGET', `${late} reopened a resolved call`);
    assert.equal(Number(s.pnl), 2, `${late} erased the R`);
    assert.equal(new Date(s.resolved_at).toISOString(), AT, `${late} erased the resolution time`);
    assert.equal(s.seal, seal); assert.equal(s.sealed_at, sealedAt);
  }
  // A row that sends no word at all is NEW to the ingest, and NEW is not final.
  const bare = row('g1'); delete bare.status;
  await send(bare);
  assert.equal(stored('g1').status, 'TARGET');
  // Every final word holds, not just the two that carry an R.
  for (const word of SS.TERMINAL) {
    pool.signals.length = 0;
    await send(row('g2', { status: word, pnl: word === 'STOP' ? -1 : null, resolved_at: AT }));
    await send(row('g2'));
    assert.equal(stored('g2').status, word, `NEW reopened ${word}`);
  }
});

test('pending words move forward, and a final word still corrects a final word', async () => {
  pool.signals.length = 0;
  await send(row('f1'));
  await send(row('f1', { status: 'OPEN' }));
  assert.equal(stored('f1').status, 'OPEN');
  await send(row('f1', { status: 'NEW' }));
  assert.equal(stored('f1').status, 'NEW', 'pending over pending is the bot\'s word, not guarded');
  await send(row('f1', { status: 'STOP', pnl: -1, resolved_at: AT }));
  assert.deepEqual([stored('f1').status, Number(stored('f1').pnl)], ['STOP', -1]);
  // A backfill reading deeper history corrects UNSCORED; the bot corrects itself.
  await send(row('f1', { status: 'UNSCORED', pnl: null, resolved_at: '' }));
  assert.equal(stored('f1').status, 'UNSCORED');
  assert.equal(stored('f1').pnl, null);
  const later = '2026-11-01T15:00:00.000Z';
  await send(row('f1', { status: 'TARGET', pnl: 2, resolved_at: later }));
  const s = stored('f1');
  assert.deepEqual([s.status, Number(s.pnl), new Date(s.resolved_at).toISOString()],
    ['TARGET', 2, later]);
});

test('both INSERT copies carry the guard, and status is assigned last', () => {
  const src = codeOnly(fs.readFileSync(path.join(__dirname, '..', 'routes', 'sync.js'), 'utf8'));
  const from = src.indexOf("router.post('/signals'");
  const to = src.indexOf('upserted++', from);
  const ingest = src.slice(from, to);
  assert.equal((ingest.match(/ON DUPLICATE KEY UPDATE \$\{OUTCOME_ASSIGNMENTS\}/g) || []).length, 2,
    'the full INSERT and the missing-column fallback both use the guarded clause');
  assert.doesNotMatch(ingest, /status\s*=\s*VALUES\(status\)/,
    'an unguarded status assignment is back in the ingest');
  const decl = src.slice(src.indexOf('const OUTCOME_ASSIGNMENTS'));
  const clause = decl.slice(0, decl.indexOf('`;'));
  const cols = [...clause.matchAll(/^\s*(\w+)\s*=\s*IF\(/gm)].map((m) => m[1]);
  assert.deepEqual(cols, ['pnl', 'resolved_at', 'status']);
});

test('the in-memory database evaluates the clause left to right, as MySQL does', async () => {
  // Not the ingest's clause: one where order DOES change the answer, so the
  // double is shown to read a column's new value after it is assigned.
  pool.signals.length = 0;
  await send(row('o1', { status: 'TARGET', pnl: 2, resolved_at: AT }));
  const cols = 'signal_key, symbol, direction, status, pnl';
  const guard = "IF(status IN ('TARGET') AND VALUES(status) NOT IN ('TARGET'), pnl, VALUES(pnl))";
  await pool.execute(`INSERT INTO signals (${cols}) VALUES (?, ?, ?, ?, ?)
    ON DUPLICATE KEY UPDATE status = VALUES(status), pnl = ${guard}`,
  ['o1', 'BTC/USDT', 'LONG', 'NEW', null]);
  assert.equal(stored('o1').pnl, null, 'status went first, so the guard read NEW');
  await send(row('o1', { status: 'TARGET', pnl: 2, resolved_at: AT }));
  await pool.execute(`INSERT INTO signals (${cols}) VALUES (?, ?, ?, ?, ?)
    ON DUPLICATE KEY UPDATE pnl = ${guard}, status = VALUES(status)`,
  ['o1', 'BTC/USDT', 'LONG', 'NEW', null]);
  assert.equal(Number(stored('o1').pnl), 2, 'pnl went first, so the guard read TARGET');
  await assert.rejects(pool.execute(`INSERT INTO signals (${cols}) VALUES (?, ?, ?, ?, ?)
    ON DUPLICATE KEY UPDATE pnl = COALESCE(VALUES(pnl), pnl)`,
  ['o1', 'BTC/USDT', 'LONG', 'NEW', null]), /cannot evaluate/);
});

// ── GET /signals/pending ────────────────────────────────────────────────────

function seed(n, extra = {}) {
  const old = new Date(Date.now() - 3 * 3600000);
  pool.signals.push({ id: n, signal_key: `p${n}`, symbol: 'ETH/USDT', direction: 'SHORT',
    entry_price: '100.00000000', stop_loss: '105.00000000', take_profit: '90.00000000',
    status: 'NEW', pnl: null, created_at: old, resolved_at: null,
    seal: 'ab'.repeat(32), sealed_at: old, ...extra });
}

test('the pending read needs the bot secret and returns only sealed pending calls old enough', async () => {
  pool.signals.length = 0;
  seed(1); seed(2, { status: 'OPEN' }); seed(3, { status: 'TARGET', pnl: 2 });
  seed(4, { seal: null, sealed_at: null });                     // unsealed: not counted anyway
  seed(5, { created_at: new Date(Date.now() - 10 * 60000) });   // too young for a closed bar
  seed(6, { status: 'EXPIRED' });
  assert.equal((await call('GET', '/api/bot/sync/signals/pending', null, null)).status, 403);
  assert.equal((await call('GET', '/api/bot/sync/signals/pending', null, 'x'.repeat(48))).status, 403);
  const r = await call('GET', '/api/bot/sync/signals/pending');
  assert.equal(r.status, 200);
  assert.deepEqual(r.data.signals.map((s) => s.signal_key), ['p1', 'p2']);
  const s = r.data.signals[0];
  assert.deepEqual([s.entry_price, s.stop_loss, s.take_profit], [100, 105, 90]);
  assert.equal(typeof s.created_at, 'string');
  assert.equal(r.data.next_after_id, null, 'a short page is the last page');
  const young = await call('GET', '/api/bot/sync/signals/pending?older_than_s=0');
  assert.deepEqual(young.data.signals.map((x) => x.signal_key), ['p1', 'p2', 'p5']);
});

test('the pending read pages by id with no gap and no repeat', async () => {
  pool.signals.length = 0;
  for (let i = 1; i <= 7; i += 1) seed(i, i === 4 ? { status: 'STOP', pnl: -1 } : {});
  const seen = [];
  let after = 0; let pages = 0;
  while (after !== null && pages < 10) {
    const r = await call('GET', `/api/bot/sync/signals/pending?limit=2&after_id=${after}`);
    seen.push(...r.data.signals.map((s) => s.signal_key));
    after = r.data.next_after_id; pages += 1;
  }
  assert.deepEqual(seen, ['p1', 'p2', 'p3', 'p5', 'p6', 'p7']);
  const capped = await call('GET', '/api/bot/sync/signals/pending?limit=100000');
  assert.equal(capped.status, 200, 'an over-large limit is clamped, never interpolated raw');
});

test('a database that does not answer is a 503 naming the class, never an empty page', async () => {
  const real = pool.execute;
  pool.execute = async () => { const e = new Error('connect ECONNREFUSED 10.0.0.9:3306'); e.name = 'ConnectionError'; throw e; };
  let r;
  try { r = await call('GET', '/api/bot/sync/signals/pending'); } finally { pool.execute = real; }
  assert.equal(r.status, 503);
  assert.equal(r.data.exception, 'ConnectionError');
  assert.ok(!JSON.stringify(r.data).includes('10.0.0.9'));
});
