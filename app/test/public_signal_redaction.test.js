'use strict';
/**
 * Three anonymous surfaces published the raw `signals.pnl` column.
 *
 *     GET /api/signals            SELECT ... , pnl, ...  → res.json({signals: rows})
 *     GET /api/signals/stats      SUM(pnl) AS net_pnl    → emitted
 *     MCP get_signals             SELECT ... , pnl, ...  → return {signals: rows}
 *
 * All three are unauthenticated (server.js mounts /api/signals and /mcp with no
 * auth), and §4 allows percent, ratio and count on a public payload — never an
 * amount. Nothing had leaked YET only because the bot has never populated the
 * outcome column, which is exactly the shape of a latent finding: one
 * bot-side change away from dollar P&L on three surfaces at once. So every
 * assertion here plants a POPULATED pnl — the state that would have leaked.
 *
 * WHY THE FIELD IS REPLACED RATHER THAN DELETED. The stream table reads `pnl`
 * for two facts that are not amounts — whether a signal resolved, and which way
 * it went. A missing key makes `s.pnl == null` TRUE for every resolved signal,
 * which would have offered a "Trade" button on calls the engine had already
 * closed. The sign is public; the magnitude is not.
 *
 * The break-even case came free. The old chip renderer was
 * `Number(s.pnl) > 0 ? '✓ WIN' : '✗ LOSS'`, filing a signal that resolved at
 * exactly 0.00 as a defeat — while `/api/signals/stats`, one screen away, had
 * already been fixed to count wins/losses/flat separately.
 */
process.env.JWT_SECRET = process.env.JWT_SECRET || 'j'.repeat(64);

const test = require('node:test');
const assert = require('node:assert');
const path = require('node:path');
const http = require('node:http');
const express = require('express');

const APP = path.join(__dirname, '..');
const { signalOutcome, publicSignal, publicAnalytics, dollarKeys } =
  require('../lib/public_signal');
const { computeAnalytics } = require('../lib/signal_analytics');

// A row shaped like the SELECT in routes/signals.js, with a populated pnl.
const row = (pnl, over = {}) => ({
  signal_key: 'k1', symbol: 'BTC/USDT', direction: 'LONG', confidence: 0.8,
  score: 7, pattern: 'breakout', regime: 'TREND', entry_price: 100,
  stop_loss: 95, take_profit: 110, rr: 2, thesis: 'clean break', status: 'RESOLVED',
  pnl, created_at: new Date('2026-08-01T00:00:00Z'), resolved_at: null, seal: null,
  ...over,
});

// ── the outcome label ──────────────────────────────────────────────────────

test('the outcome names all three resolutions, and absence as absence', () => {
  assert.strictEqual(signalOutcome(12.5), 'WIN');
  assert.strictEqual(signalOutcome(-3), 'LOSS');
  assert.strictEqual(signalOutcome(0), 'FLAT',
    'a break-even resolution is an outcome, not a defeat');
  assert.strictEqual(signalOutcome(null), null, 'unresolved is not a loss');
  assert.strictEqual(signalOutcome(undefined), null);
  // DECIMAL columns arrive as strings through mysql2.
  assert.strictEqual(signalOutcome('4.20'), 'WIN');
  assert.strictEqual(signalOutcome('-0.01'), 'LOSS');
  assert.strictEqual(signalOutcome('0.00'), 'FLAT');
  // An unparseable value is not a verdict either way.
  assert.strictEqual(signalOutcome('n/a'), null);
  assert.strictEqual(signalOutcome(NaN), null);
});

test('a public signal carries the sign and never the amount', () => {
  const p = publicSignal(row(42.5));
  assert.deepStrictEqual(dollarKeys(p), [], 'an amount survived the boundary');
  assert.ok(!('pnl' in p));
  assert.strictEqual(p.outcome, 'WIN');
  // Everything the panel actually renders survives untouched, prices included
  // (public market data, already on /api/insight).
  for (const k of ['signal_key', 'symbol', 'direction', 'confidence', 'pattern',
                   'entry_price', 'stop_loss', 'take_profit', 'rr', 'status']) {
    assert.deepStrictEqual(p[k], row(42.5)[k], `${k} was lost in redaction`);
  }
  assert.ok(p.created_at instanceof Date, 'the timestamp must survive as a date');
});

test('the allowlist does not publish a column added to the SELECT later', () => {
  // The opposite default from a denylist, and the right one for an anonymous
  // payload: a new field is invisible until someone adds it deliberately.
  const p = publicSignal(row(1, { size_usd: 5000, operator_note: 'private' }));
  assert.ok(!('size_usd' in p));
  assert.ok(!('operator_note' in p));
  assert.deepStrictEqual(dollarKeys(p), []);
});

test('analytics keeps every ratio and drops every total', () => {
  const a = computeAnalytics([row(10), row(-5), row(0)]);
  assert.strictEqual(a.overall.net_pnl, 5, 'the aggregator still computes it');
  const p = publicAnalytics(a);
  assert.deepStrictEqual(dollarKeys(p), [],
    'a dollar total survived onto the anonymous analytics payload');
  assert.strictEqual(p.overall.resolved, 3);
  assert.strictEqual(p.overall.wins, 1);
  assert.strictEqual(p.overall.losses, 1);
  assert.strictEqual(p.overall.flat, 1);
  assert.strictEqual(p.overall.win_rate, a.overall.win_rate);
  // …including one level down, per group.
  const g = p.by_pattern.find((x) => x.key === 'breakout');
  assert.ok(g && !('net_pnl' in g), 'per-group totals are amounts too');
  assert.strictEqual(g.win_rate, a.by_pattern[0].win_rate);
});

// ── the wires: both routes must actually use it ────────────────────────────

function withPool(rows, fn) {
  const pool = {
    execute: async () => [rows],
  };
  const dbPath = require.resolve(path.join(APP, 'db.js'));
  const prev = require.cache[dbPath];
  require.cache[dbPath] = { id: dbPath, filename: dbPath, loaded: true,
                            exports: { pool } };
  try { return fn(); } finally {
    if (prev) require.cache[dbPath] = prev; else delete require.cache[dbPath];
  }
}

function getSignals() {
  return new Promise((resolve, reject) => {
    const s = withPool([row(31.5), row(0), row(null, { status: 'NEW' })], () => {
      delete require.cache[require.resolve(path.join(APP, 'routes', 'signals.js'))];
      const app = express();
      app.use('/api/signals', require(path.join(APP, 'routes', 'signals.js')));
      return http.createServer(app);
    });
    s.listen(0, '127.0.0.1', () => {
      http.get({ port: s.address().port, path: '/api/signals?limit=40' }, (res) => {
        let b = '';
        res.on('data', (d) => { b += d; });
        res.on('end', () => { s.close(); resolve(JSON.parse(b || '{}')); });
      }).on('error', (e) => { s.close(); reject(e); });
    });
  });
}

test('GET /api/signals publishes outcomes, not amounts', async () => {
  const body = await getSignals();
  assert.deepStrictEqual(dollarKeys(body), [],
    'the route emitted the raw column straight out of the SELECT');
  assert.deepStrictEqual(body.signals.map((s) => s.outcome),
    ['WIN', 'FLAT', null]);
  // The two facts the stream table needs are still derivable.
  assert.strictEqual(body.signals.filter((s) => s.outcome == null).length, 1,
    'exactly one signal is still actionable');
});

test('MCP get_signals redacts the same way', async () => {
  const out = await withPool([row(88), row(-2)], async () => {
    delete require.cache[require.resolve(path.join(APP, 'routes', 'mcp.js'))];
    const { TOOLS } = require(path.join(APP, 'routes', 'mcp.js'));
    return TOOLS.get_signals.handler({ limit: 5 });
  });
  assert.deepStrictEqual(dollarKeys(out), [],
    '/mcp is unauthenticated and was emitting pnl verbatim');
  assert.deepStrictEqual(out.signals.map((s) => s.outcome), ['WIN', 'LOSS']);
});
