'use strict';
/**
 * The copy picks say which read failed, and never answer "no match" or "you
 * follow nobody" from a read that did not happen.
 *
 * `/api/copy/picks` read the live signal stream with `catch (e) { /* empty
 * stream is fine *\/ }`, so a failed query gave every followed agent zero
 * picks and the panel printed "No live signal matches this agent's gates
 * right now": a confident negative about the market from no read at all.
 * Its outer catch answered 200 with `agents: []`, which the panel read as a
 * user who follows nobody and hid itself. And an engine agent missing from
 * the catalogue printed "the catalogue bridge is offline" whether the
 * catalogue was unreadable or had answered without that agent.
 */
process.env.JWT_SECRET = 'j'.repeat(64);
delete process.env.DATABASE_URL;

const test = require('node:test');
const assert = require('node:assert/strict');
const http = require('node:http');
const fs = require('fs');
const path = require('path');
const express = require('express');
const { codeOnly } = require('./helpers/code_only');

const CATALOG = [
  { id: 'dip-sniper', name: 'Dip Sniper', icon: '🎯',
    scorecard: { gates: { confidence_threshold: 0.7, regime_filter: 'TREND_DOWN' } } },
];

let server, base, pool, origExecute;
let catalogueAnswer = { status: 200, data: { agents: CATALOG } };
let failSignals = false;
let failFollows = false;

test.before(async () => {
  const gateway = require('../lib/gateway');
  gateway.isConfigured = () => true;
  gateway.getGateway = async () => {
    if (catalogueAnswer instanceof Error) throw catalogueAnswer;
    return catalogueAnswer;
  };
  ({ pool } = require('../db'));
  origExecute = pool.execute.bind(pool);
  pool.execute = async (sql, params) => {
    if (failSignals && /FROM signals WHERE status/.test(sql)) throw new Error('ER_LOCK_WAIT_TIMEOUT');
    if (failFollows && /FROM copy_subscriptions WHERE user_id/.test(sql) && /SELECT agent_id/.test(sql)) {
      throw new Error('ER_CON_COUNT_ERROR');
    }
    return origExecute(sql, params);
  };
  await origExecute(
    `INSERT INTO signals (signal_key, symbol, direction, confidence, score, pattern,
       regime, entry_price, stop_loss, take_profit, rr, thesis, status, pnl,
       created_at, resolved_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)`,
    ['n1', 'SOL/USDT', 'LONG', 0.5, 0.5, 'x', 'TREND_UP', 100, 95, 110, 2, '', 'OPEN',
     null, new Date().toISOString(), null]);
  const app = express();
  app.use(express.json());
  app.use('/api/auth', require('../auth').router);
  app.use('/api/copy', require('../routes/copy'));
  await new Promise((res) => { server = app.listen(0, '127.0.0.1', res); });
  base = `http://127.0.0.1:${server.address().port}`;
});

test.after(() => { if (server) server.close(); });

test.beforeEach(() => {
  failSignals = false;
  failFollows = false;
  catalogueAnswer = { status: 200, data: { agents: CATALOG } };
  require('../lib/agent_catalogue')._resetCache();
});

function req(method, p, { token, body } = {}) {
  return new Promise((resolve, reject) => {
    const payload = body ? JSON.stringify(body) : null;
    const r = http.request(`${base}${p}`, {
      method,
      headers: {
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
        ...(payload ? { 'Content-Type': 'application/json' } : {}),
      },
    }, (res) => {
      let d = ''; res.on('data', c => d += c);
      res.on('end', () => resolve({ status: res.statusCode, data: d ? JSON.parse(d) : {} }));
    });
    r.on('error', reject);
    if (payload) r.write(payload);
    r.end();
  });
}

let n = 0;
async function follower(...ids) {
  const reg = await req('POST', '/api/auth/register',
    { body: { email: `picks${++n}@test.io`, password: 'x'.repeat(12) } });
  const token = reg.data.token;
  for (const id of ids) {
    const f = await req('POST', '/api/copy/follow', { token, body: { agent_id: id } });
    assert.equal(f.status, 200, JSON.stringify(f.data));
  }
  return token;
}

test('a stream nobody could read is null picks, never zero', async () => {
  const token = await follower('dip-sniper');
  failSignals = true;
  const r = await req('GET', '/api/copy/picks', { token });
  assert.equal(r.status, 200);
  assert.equal(r.data.signals_read, false);
  assert.equal(r.data.agents[0].picks, null);
});

test('a stream that was read and matched nothing is an empty list', async () => {
  const token = await follower('dip-sniper');
  const r = await req('GET', '/api/copy/picks', { token });
  assert.equal(r.data.signals_read, true);
  assert.deepEqual(r.data.agents[0].picks, []);
});

test('an unreadable catalogue is not an agent that left it', async () => {
  const token = await follower('dip-sniper');
  // Following read the catalogue and cached it; a cached answer is readable
  // by design (stale beats blind), so the unreadable case needs no cache.
  require('../lib/agent_catalogue')._resetCache();
  catalogueAnswer = new Error('bridge down');
  let r = await req('GET', '/api/copy/picks', { token });
  assert.equal(r.data.agents[0].unavailable, true);
  assert.equal(r.data.agents[0].reason, 'catalogue_unreadable');
  assert.equal(r.data.agents[0].picks, null);

  catalogueAnswer = { status: 200, data: { agents: [] } };
  require('../lib/agent_catalogue')._resetCache();
  r = await req('GET', '/api/copy/picks', { token });
  assert.equal(r.data.agents[0].reason, 'unknown_agent');
});

test('a failed read is an error, never "you follow nobody"', async () => {
  const token = await follower('dip-sniper');
  failFollows = true;
  const r = await req('GET', '/api/copy/picks', { token });
  assert.equal(r.status, 500);
  assert.equal(r.data.error, 'picks_failed');
  assert.equal(r.data.agents, undefined);
});

// ── the panel (inline in dashboard.js, so its branches are read as source) ──

const DASH = codeOnly(fs.readFileSync(path.join(__dirname, '..', 'public', 'js', 'dashboard.js'), 'utf8'));
const PANEL = DASH.slice(DASH.indexOf('async function loadAgentPicks'),
                        DASH.indexOf('loadAgentPicks();'));

test('the panel says a failed read failed instead of hiding', () => {
  assert.ok(PANEL.length > 200, 'the panel slice is empty; the anchors moved');
  const failed = PANEL.indexOf("Could not read your agents' picks");
  const hideOnEmpty = PANEL.indexOf('if (!groups.length) { panel.hidden = true; return; }');
  assert.ok(failed > 0 && hideOnEmpty > failed,
    'the failed-read branch must come before the empty-list hide');
});

test('the panel says an unread stream is unknown before it says "no match"', () => {
  const unread = PANEL.indexOf('if (g.picks == null)');
  const noMatch = PANEL.indexOf('No live signal matches');
  assert.ok(unread > 0 && noMatch > unread);
  assert.match(PANEL, /could not be read/);
  assert.match(PANEL, /no longer in the catalogue/);
  assert.doesNotMatch(PANEL, /catalogue bridge is offline/, 'a guessed cause');
});

test('a followed community strategy also says its stream was not read', async () => {
  const store = require('../lib/user_strategies');
  const c = await store.create(9001, { name: 'Long Only', rules: [{ type: 'direction', value: 'long_only' }] });
  const slug = c.slug;
  const row = (await store.listMine(9001)).find((s) => s.slug === slug);
  const pub = await store.setVisibility(9001, row.dbId, 'public');
  assert.ok(pub.ok, 'the fixture could not publish a strategy');
  const token = await follower(slug);
  failSignals = true;
  const r = await req('GET', '/api/copy/picks', { token });
  const g = r.data.agents.find((a) => a.id === slug);
  assert.equal(g.community, true);
  assert.equal(g.picks, null);
});
