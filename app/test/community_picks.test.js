'use strict';
/**
 * Community Copy Picks — member-published strategies become followable with
 * HONEST degraded matching, and their copies feed live paper records through
 * the same sealed attribution engine agents use. The contract:
 * - only signal-checkable rules project onto gates (rulesToGates):
 *   confidence, symbol allow/deny, direction, the regime chip. Size caps,
 *   drawdown halts and R:R floors are the member's risk config, NOT match
 *   criteria — projecting them would fabricate filters the data can't run;
 * - engine matching is byte-identical to before (the new gates are additive
 *   and engine gate sets never carry them);
 * - a followed community strategy gets a picks feed, never "unavailable";
 * - a community slug verifies for arena attribution exactly like an engine
 *   agent, so community records accumulate through the same sealed flow.
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
const { rulesToGates, agentWouldTake, picksForAgent } = require('../lib/agent_match');

let server, base, token, uid, slug;

function req(method, p, body) {
  return new Promise((resolve, reject) => {
    const data = body ? JSON.stringify(body) : null;
    const r = http.request(`${base}${p}`, { method,
      headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}`,
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

async function seedSignal(key, symbol, direction, conf, regime) {
  await pool.execute(
    `INSERT INTO signals (signal_key, symbol, direction, confidence, score, pattern,
       regime, entry_price, stop_loss, take_profit, rr, thesis, status, pnl,
       created_at, resolved_at, seal, seal_payload, sealed_at)
     VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
    [key, symbol, direction, conf, 80, 'x', regime, 60000, 58000, 66000, 3,
      't', 'OPEN', null, new Date(), null, null, null, null]);
}

test.before(async () => {
  const [u] = await pool.execute(
    'INSERT INTO users (email, password_hash, created_at) VALUES (?, ?, NOW())',
    ['commpicks@test.dev', 'x'.repeat(60)]);
  uid = u.insertId;
  token = jwt.sign({ user_id: uid, email: 'commpicks@test.dev' }, process.env.JWT_SECRET);
  const { setTickerFetcher } = require('../lib/tickers');
  setTickerFetcher(async () => ({
    BTCUSDT: { price: 60000, change: 0, volume: 1 },
    ETHUSDT: { price: 3000, change: 0, volume: 1 },
  }));
  const app = express();
  app.use(express.json());
  app.use('/api/strategies', require('../routes/user_strategies'));
  app.use('/api/copy', require('../routes/copy'));
  app.use('/api/arena', require('../routes/arena'));
  app.use('/api/public/agent-record', require('../routes/agent_record'));
  await new Promise((res) => { server = app.listen(0, '127.0.0.1', res); });
  base = `http://127.0.0.1:${server.address().port}`;

  // A published community strategy: long-only, conf >= 0.7, BTC/ETH only,
  // in a trend-up regime — PLUS a size cap that must NOT become a gate.
  const c = await req('POST', '/api/strategies', {
    name: 'Long Majors', regime: 'trend_up',
    rules: [{ type: 'direction', value: 'long_only' },
      { type: 'min_confidence', value: 0.7 },
      { type: 'allowed_symbols', value: ['BTC', 'ETH'] },
      { type: 'max_position_pct', value: 5 }] });
  slug = c.body.slug;
  const mine = await req('GET', '/api/strategies');
  const row = mine.body.strategies.find((s) => s.slug === slug);
  await req('POST', `/api/strategies/${row.dbId}/publish`);
});
test.after(() => { if (server) server.close(); });

test('rulesToGates projects ONLY signal-checkable rules', () => {
  const g = rulesToGates([
    { type: 'min_confidence', value: 0.7 },
    { type: 'allowed_symbols', value: ['BTC', 'ETH'] },
    { type: 'blocked_symbols', value: ['DOGE'] },
    { type: 'direction', value: 'long_only' },
    { type: 'max_position_pct', value: 5 },        // risk config, never a gate
    { type: 'max_drawdown_pct', value: 10 },       // risk config, never a gate
  ], 'trend_up');
  assert.deepEqual(Object.keys(g).sort(),
    ['blocked_symbols', 'confidence_threshold', 'direction', 'regime_filter', 'symbols']);
  assert.strictEqual(g.confidence_threshold, 0.7);
  assert.strictEqual(g.regime_filter, 'trend_up');
  assert.deepEqual(rulesToGates([], 'any'), {}, "'any' regime is not a filter");
});

test('the new gates work, and engine-shaped gate sets are untouched', () => {
  const sig = { symbol: 'BTC/USDT', direction: 'SHORT', confidence: 0.9, regime: 'TREND_UP' };
  assert.ok(!agentWouldTake(sig, { direction: 'long_only' }), 'long-only rejects a SHORT');
  assert.ok(agentWouldTake({ ...sig, direction: 'LONG' }, { direction: 'long_only' }));
  assert.ok(!agentWouldTake({ ...sig, symbol: 'DOGEUSDT' }, { blocked_symbols: ['DOGE'] }));
  // an engine gate set (no direction/deny) behaves exactly as before
  const engine = { confidence_threshold: 0.7, regime_filter: 'TREND_UP', symbols: ['BTC'] };
  assert.ok(agentWouldTake({ symbol: 'BTC/USDT', direction: 'SHORT', confidence: 0.9, regime: 'TREND_UP' }, engine),
    'direction is not filtered unless the gate set carries it');
});

test('a followed community strategy gets a picks feed, never "unavailable"', async () => {
  await seedSignal('cp-take', 'BTC/USDT', 'LONG', 0.85, 'TREND_UP');   // admitted
  await seedSignal('cp-short', 'ETH/USDT', 'SHORT', 0.9, 'TREND_UP'); // direction reject
  await seedSignal('cp-low', 'ETH/USDT', 'LONG', 0.5, 'TREND_UP');    // confidence reject
  assert.strictEqual((await req('POST', '/api/copy/follow', { agent_id: slug })).status, 200);
  const r = await req('GET', '/api/copy/picks');
  assert.strictEqual(r.status, 200);
  const g = r.body.agents.find((a) => a.id === slug);
  assert.ok(g, 'the community group is present');
  assert.ok(!g.unavailable, 'a community strategy is never "gates unavailable"');
  assert.strictEqual(g.community, true);
  assert.strictEqual(g.name, 'Long Majors');
  assert.deepEqual(g.picks.map((p) => p.signal_key), ['cp-take'],
    'only the signal its rules admit');
  assert.ok(g.matched_on.includes('direction') && g.matched_on.includes('confidence'),
    'matched_on states exactly which gates were applied');
  assert.match(r.body.note,
    /size caps and halts are yours|community strategies still match on their own rules/,
    'either note branch states community matching honestly');
});

test('a community slug verifies for sealed arena attribution and feeds its record', async () => {
  const open = await req('POST', '/api/arena/open-signal',
    { signal_key: 'cp-take', agent_slug: slug, margin: 100, leverage: 2 });
  assert.strictEqual(open.status, 200, JSON.stringify(open.body));
  assert.deepEqual(open.body.attribution, { attributed: true, agent_slug: slug });

  // A claim its rules reject is dropped, named — same contract as engine agents.
  const bad = await req('POST', '/api/arena/open-signal',
    { signal_key: 'cp-short', agent_slug: slug, margin: 100, leverage: 2 });
  assert.strictEqual(bad.status, 200);
  assert.deepEqual(bad.body.attribution, { attributed: false, reason: 'gates_reject' });

  // Close the attributed one → the community strategy's live paper record.
  const [pos] = await pool.execute(
    'SELECT id, user_id, symbol, direction, entry, margin, leverage, source, tp, sl, exits_edited, trail_pct, trade_key, seal, seal_payload, sealed_at, signal_key, agent_slug, opened_at FROM arena_positions WHERE user_id = ? ORDER BY id DESC', [uid]);
  const att = pos.find((x) => x.agent_slug === slug);
  assert.ok(att, 'the attributed position exists');
  assert.strictEqual((await req('POST', '/api/arena/close', { position_id: att.id })).status, 200);
  require('../routes/agent_record')._resetCache();
  const rec = await req('GET', `/api/public/agent-record/${slug}`);
  assert.strictEqual(rec.status, 200);
  assert.strictEqual(rec.body.trades, 1, 'community records accumulate through the same flow');
  assert.ok(rec.body.recent[0].trade_key, 'sealed at open, same as engine agents');
});

test('the picks panel never hides behind an engine-catalogue outage', () => {
  // The follow list loads inside loadAgentPicks itself — a 503 catalogue
  // must never hide followed COMMUNITY strategies (independent sources).
  const dash = fs.readFileSync(path.join(__dirname, '..', 'public', 'js', 'dashboard.js'), 'utf8');
  assert.match(dash, /The follow list loads HERE, not only in the engine-catalogue path/);
  const block = dash.slice(dash.indexOf('async function loadAgentPicks'),
    dash.indexOf('loadAgentPicks();'));
  assert.match(block, /fetchJSON\('\/api\/copy'/, 'picks panel fetches its own follows');
});

test('the dashboard marks community groups', () => {
  const dash = fs.readFileSync(path.join(__dirname, '..', 'public', 'js', 'dashboard.js'), 'utf8');
  assert.match(dash, /g\.community \?/);
  assert.ok(dash.includes("T('cp.comm', 'Community')"));
  const i18n = fs.readFileSync(path.join(__dirname, '..', 'public', 'js', 'i18n.js'), 'utf8');
  assert.ok(i18n.includes("'cp.comm'"));
});
