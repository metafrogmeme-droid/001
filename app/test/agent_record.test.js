'use strict';
/**
 * Sealed per-agent live paper record. The contract:
 * - attribution sticks ONLY when the claim verifies server-side: the agent
 *   exists in the catalogue AND its own published gates admit the signal
 *   (the same matcher the picks feed runs). Unverifiable claims are dropped
 *   — and the response says so; the open proceeds either way.
 * - attribution rides the position onto the closed trade, beside the seal
 *   that was minted at OPEN — before the outcome existed. Forward-only:
 *   nothing is ever back-filled.
 * - the public record is §4-safe: percent/ratio/count only. No vUSDT
 *   figures, no member identity. Dispersion shown (median + worst…best),
 *   low samples flagged, unreadable margins skipped (never zeroed).
 */
process.env.JWT_SECRET = process.env.JWT_SECRET || 'j'.repeat(64);
delete process.env.DATABASE_URL;

const test = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const path = require('path');
const http = require('http');
const express = require('express');
const jwt = require('jsonwebtoken');
const { pool } = require('../db');
const { computeAgentRecord } = require('../lib/agent_record');
const catalogue = require('../lib/agent_catalogue');

let server, base, token, uid;

function req(method, p, body, tok) {
  return new Promise((resolve, reject) => {
    const data = body ? JSON.stringify(body) : null;
    const r = http.request(`${base}${p}`, { method,
      headers: { 'Content-Type': 'application/json',
        ...(tok === null ? {} : { Authorization: `Bearer ${tok || token}` }),
        ...(data ? { 'Content-Length': Buffer.byteLength(data) } : {}) } }, (res) => {
      const chunks = [];
      res.on('data', (c) => chunks.push(c));
      res.on('end', () => resolve({ status: res.statusCode, body: JSON.parse(Buffer.concat(chunks) || '{}') }));
    });
    r.on('error', reject);
    if (data) r.write(data);
    r.end();
  });
}

async function seedSignal(key, symbol, conf, regime) {
  await pool.execute(
    `INSERT INTO signals (signal_key, symbol, direction, confidence, score, pattern,
       regime, entry_price, stop_loss, take_profit, rr, thesis, status, pnl,
       created_at, resolved_at, seal, seal_payload, sealed_at)
     VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
    [key, symbol, 'LONG', conf, 80, 'breakout', regime, 60000, 58000, 66000, 3,
      't', 'OPEN', null, new Date(), null, null, null, null]);
}

test.before(async () => {
  const [u] = await pool.execute(
    'INSERT INTO users (email, password_hash, created_at) VALUES (?, ?, NOW())',
    ['record@test.dev', 'x'.repeat(60)]);
  uid = u.insertId;
  token = jwt.sign({ user_id: uid, email: 'record@test.dev' }, process.env.JWT_SECRET);
  const { setTickerFetcher } = require('../lib/tickers');
  setTickerFetcher(async () => ({
    BTCUSDT: { price: 60000, change: 0, volume: 1 },
    ETHUSDT: { price: 3000, change: 0, volume: 1 },
    SOLUSDT: { price: 150, change: 0, volume: 1 },
    XRPUSDT: { price: 2, change: 0, volume: 1 },
  }));
  catalogue._setCache([
    { id: 'dip-sniper', name: 'Dip Sniper', icon: '🎯',
      scorecard: { gates: { confidence_threshold: 0.6, regime_filter: 'TREND_UP', symbols: ['BTC'] } } },
    { id: 'range-only', name: 'Range Only', icon: '🎪',
      scorecard: { gates: { regime_filter: 'RANGE' } } },
  ]);
  const app = express();
  app.use(express.json());
  app.use('/api/arena', require('../routes/arena'));
  app.use('/api/public/agent-record', require('../routes/agent_record'));
  await new Promise((res) => { server = app.listen(0, '127.0.0.1', res); });
  base = `http://127.0.0.1:${server.address().port}`;
});
test.after(() => { if (server) server.close(); catalogue._resetCache(); });

test('computeAgentRecord: dispersion, distinct copiers, honest gaps', () => {
  const rows = [
    { user_id: 1, symbol: 'BTCUSDT', direction: 'LONG', margin: 100, pnl: 20, reason: 'tp', trade_key: 'k1', closed_at: 3 },
    { user_id: 2, symbol: 'BTCUSDT', direction: 'LONG', margin: 200, pnl: -10, reason: 'sl', trade_key: 'k2', closed_at: 2 },
    { user_id: 1, symbol: 'ETHUSDT', direction: 'SHORT', margin: 100, pnl: -100, reason: 'liquidated', trade_key: 'k3', closed_at: 1 },
    { user_id: 3, symbol: 'X', direction: 'LONG', margin: 0, pnl: 5, reason: 'tp', trade_key: 'k4', closed_at: 0 },
  ];
  const r = computeAgentRecord(rows);
  assert.strictEqual(r.trades, 3, 'the zero-margin row is skipped, never counted as 0%');
  assert.strictEqual(r.copiers, 2, 'distinct accounts among counted rows');
  assert.strictEqual(r.wins, 1);
  assert.strictEqual(r.losses, 2);
  assert.strictEqual(r.liquidations, 1);
  assert.strictEqual(r.median_rom_pct, -5, 'median of [+20, -5, -100]');
  assert.strictEqual(r.best_rom_pct, 20);
  assert.strictEqual(r.worst_rom_pct, -100);
  assert.strictEqual(r.low_sample, true);
  assert.ok(!('pnl' in r.recent[0]) && !('margin' in r.recent[0]) && !('user_id' in r.recent[0]),
    'recent rows carry no vUSDT figure and no identity');
  assert.strictEqual(r.recent[0].trade_key, 'k1', 'each row cites its sealed receipt');
  assert.deepEqual(computeAgentRecord([]).trades, 0);
});

test('attribution sticks only when the agent\'s own gates admit the signal', async () => {
  // One symbol per open — the arena (correctly) refuses stacking a symbol.
  await seedSignal('sig-take', 'BTCUSDT', 0.8, 'TREND_UP');
  await seedSignal('sig-eth', 'ETHUSDT', 0.9, 'TREND_UP');
  await seedSignal('sig-sol', 'SOLUSDT', 0.9, 'TREND_UP');
  await seedSignal('sig-xrp', 'XRPUSDT', 0.9, 'TREND_UP');
  const r = await req('POST', '/api/arena/open-signal',
    { signal_key: 'sig-take', agent_slug: 'dip-sniper', margin: 100, leverage: 2 });
  assert.strictEqual(r.status, 200, JSON.stringify(r.body));
  assert.deepEqual(r.body.attribution, { attributed: true, agent_slug: 'dip-sniper' });

  // A claim for an agent whose gates REJECT the signal (regime mismatch):
  // the open proceeds, the tag is dropped, and the response says why.
  const r2 = await req('POST', '/api/arena/open-signal',
    { signal_key: 'sig-eth', agent_slug: 'range-only', margin: 100, leverage: 2 });
  assert.strictEqual(r2.status, 200, JSON.stringify(r2.body));
  assert.deepEqual(r2.body.attribution, { attributed: false, reason: 'gates_reject' });

  // Unknown agent, readable catalogue → honest absence claim.
  const r3 = await req('POST', '/api/arena/open-signal',
    { signal_key: 'sig-sol', agent_slug: 'ghost-agent', margin: 100, leverage: 2 });
  assert.deepEqual(r3.body.attribution, { attributed: false, reason: 'agent_unknown' });

  // No claim → no attribution field noise, but signal_key is still recorded.
  const r4 = await req('POST', '/api/arena/open-signal',
    { signal_key: 'sig-xrp', margin: 100, leverage: 2 });
  assert.strictEqual(r4.status, 200, JSON.stringify(r4.body));
  assert.strictEqual(r4.body.attribution, undefined);
  const [pos] = await pool.execute(
    'SELECT id, user_id, symbol, direction, entry, margin, leverage, source, tp, sl, exits_edited, trail_pct, trade_key, seal, seal_payload, sealed_at, signal_key, agent_slug, opened_at FROM arena_positions WHERE user_id = ? ORDER BY id DESC', [uid]);
  assert.strictEqual(pos[0].signal_key, 'sig-xrp', 'provenance recorded even unattributed');
  assert.strictEqual(pos[0].agent_slug, null);
  const att = pos.find((x) => x.agent_slug === 'dip-sniper');
  assert.ok(att, 'the verified claim stuck');
  assert.strictEqual(att.signal_key, 'sig-take');
  assert.ok(att.trade_key && att.sealed_at, 'sealed at open, before any outcome');
});

test('attribution rides the close onto the trade, and the record serves it', async () => {
  const [pos] = await pool.execute(
    'SELECT id, user_id, symbol, direction, entry, margin, leverage, source, tp, sl, exits_edited, trail_pct, trade_key, seal, seal_payload, sealed_at, signal_key, agent_slug, opened_at FROM arena_positions WHERE user_id = ? ORDER BY id DESC', [uid]);
  const attributed = pos.filter((p) => p.agent_slug === 'dip-sniper');
  for (const p of attributed) {
    const c = await req('POST', '/api/arena/close', { position_id: p.id });
    assert.strictEqual(c.status, 200, JSON.stringify(c.body));
  }
  const [tr] = await pool.execute(
    'SELECT * FROM arena_trades WHERE agent_slug = ? ORDER BY closed_at DESC LIMIT 500', ['dip-sniper']);
  assert.strictEqual(tr.length, 1);
  assert.strictEqual(tr[0].signal_key, 'sig-take');
  assert.ok(tr[0].trade_key, 'the seal followed the trade');

  const rec = await req('GET', '/api/public/agent-record/dip-sniper', null, null);
  assert.strictEqual(rec.status, 200);
  assert.strictEqual(rec.body.trades, 1);
  assert.strictEqual(rec.body.copiers, 1);
  assert.strictEqual(rec.body.low_sample, true);
  assert.strictEqual(rec.body.recent[0].trade_key, tr[0].trade_key);
  const wire = JSON.stringify(rec.body);
  assert.ok(!wire.includes('"pnl"') && !wire.includes('"margin"') && !wire.includes('"user_id"'),
    '§4 on the wire: no vUSDT figures, no identity');
  assert.ok(!wire.includes('$'));
});

test('an unrecorded agent serves an EMPTY record, never an invented one', async () => {
  const rec = await req('GET', '/api/public/agent-record/range-only', null, null);
  assert.strictEqual(rec.status, 200);
  assert.strictEqual(rec.body.trades, 0);
  assert.strictEqual(rec.body.median_rom_pct, null, 'no data → null, never 0');
  const bad = await req('GET', '/api/public/agent-record/NOT%20A%20SLUG', null, null);
  assert.strictEqual(bad.status, 400);
});

test('both close paths carry attribution AND provenance (source pins)', () => {
  const src = fs.readFileSync(path.join(__dirname, '..', 'routes', 'arena.js'), 'utf8');
  const carries = src.match(/closed_at, signal_key, agent_slug, source\)/g) || [];
  assert.strictEqual(carries.length, 2, 'sweep close AND manual close both carry the tag');
  // `source` joined the column list when an agent gained the ability to trade
  // as itself: the per-agent record is built from CLOSED rows, so a close path
  // that dropped it would file the agent's own trade under its copiers — and
  // WHICH record a trade lands in would depend on how it happened to close.
  // Anchored on `]);` — the LAST argument of the insert's params array. The
  // unanchored version counted 3, because the positions listing also carries a
  // `source: p.source || 'manual'` response field that is not a close path at
  // all. A scan that matches the right string in the wrong place reports a
  // number, and the number was wrong in the safe direction here; next time it
  // would be wrong in the other one.
  const provenance = src.match(/p\.source \|\| 'manual'\]\);/g) || [];
  assert.strictEqual(provenance.length, 2, 'both close paths must carry p.source');
  assert.match(src, /p\.signal_key \|\| null, p\.agent_slug \|\| null/);
  assert.match(src, /agentWouldTake/, 'verification uses the same matcher the picks feed runs');
});

test('the UI wires the record and the attributed copy flow', () => {
  const page = fs.readFileSync(path.join(__dirname, '..', 'public', 'strategy.html'), 'utf8');
  assert.match(page, /\/api\/public\/agent-record\//);
  assert.match(page, /omit, never invent/, 'a failed record read renders nothing');
  assert.match(page, /rec\.note/, 'the honesty note ships with the record');
  const dash = fs.readFileSync(path.join(__dirname, '..', 'public', 'js', 'dashboard.js'), 'utf8');
  assert.match(dash, /data-pcopy/);
  assert.match(dash, /agent_slug: req_\.a/);
  assert.match(dash, /attribution dropped|rec\.copied_unattr/, 'a dropped tag is SAID, not hidden');
  const i18n = fs.readFileSync(path.join(__dirname, '..', 'public', 'js', 'i18n.js'), 'utf8');
  for (const k of ['rec.h', 'rec.note', 'rec.low', 'rec.copy_b', 'rec.copied_unattr']) {
    assert.ok(i18n.includes(`'${k}'`), `${k} in the dictionary`);
  }
  const srv = fs.readFileSync(path.join(__dirname, '..', 'server.js'), 'utf8');
  assert.ok(srv.includes("app.use('/api/public/agent-record', require('./routes/agent_record'))"));
});
