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

test('analytics keeps the R aggregates and drops every dollar total', () => {
  // 10, −5 and a flat: sum 5R, mean 1.67R. The old name `net_pnl` made the
  // boundary throw that ratio away as if it were dollars.
  const a = computeAnalytics([row(10), row(-5), row(0)]);
  assert.strictEqual(a.overall.net_r, 5);
  assert.strictEqual(a.overall.mean_r, 1.67);
  assert.ok(!('net_pnl' in a.overall));
  const p = publicAnalytics(a);
  assert.deepStrictEqual(dollarKeys(p), [],
    'a dollar total survived onto the anonymous analytics payload');
  assert.strictEqual(p.r_basis, 'gross');
  assert.strictEqual(p.overall.resolved, 3);
  assert.strictEqual(p.overall.wins, 1);
  assert.strictEqual(p.overall.losses, 1);
  assert.strictEqual(p.overall.flat, 1);
  assert.strictEqual(p.overall.win_rate, a.overall.win_rate);
  assert.strictEqual(p.overall.net_r, 5, 'the sum of R is a ratio and stays');
  assert.strictEqual(p.overall.mean_r, 1.67, 'the mean R stays with the sum');
  const g = p.by_pattern.find((x) => x.key === 'breakout');
  assert.ok(g);
  assert.strictEqual(g.net_r, 5, 'a group keeps its own sum');
  assert.strictEqual(g.mean_r, 1.67);
  assert.strictEqual(g.win_rate, a.by_pattern[0].win_rate);
});

test('a planted dollar total is dropped beside the R it must not replace', () => {
  // Both arms, and one level down. Keeping only `overall` would leave the
  // group's `net_pnl` on the wire. A measured 0R must survive the same pass:
  // the scrubber drops keys, not zeros.
  const planted = {
    r_basis: 'gross',
    overall: { resolved: 2, net_r: 1.5, mean_r: 0.75, net_pnl: 40, pnl_usd: 9 },
    by_pattern: [{ key: 'breakout', n: 2, net_r: 1.5, mean_r: 0.75, net_pnl: 40 }],
    by_symbol: [{ key: 'BTC/USDT', n: 1, net_r: 0, mean_r: 0, win_rate: 0, fee_usd: 3 }],
  };
  const p = publicAnalytics(planted);
  assert.deepStrictEqual(dollarKeys(p), []);
  assert.strictEqual(p.overall.net_r, 1.5);
  assert.strictEqual(p.overall.mean_r, 0.75);
  assert.ok(!('net_pnl' in p.overall));
  assert.ok(!('pnl_usd' in p.overall));
  const pattern = p.by_pattern[0];
  assert.strictEqual(pattern.net_r, 1.5);
  assert.strictEqual(pattern.mean_r, 0.75);
  assert.ok(!('net_pnl' in pattern), 'the group total was the row beside the verdict');
  const symbol = p.by_symbol[0];
  assert.strictEqual(symbol.net_r, 0, 'a measured 0R is not dropped with the dollars');
  assert.strictEqual(symbol.mean_r, 0);
  assert.strictEqual(symbol.win_rate, 0);
  assert.ok(!('fee_usd' in symbol));
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

function getAnalytics(rows) {
  return new Promise((resolve, reject) => {
    const s = withPool(rows, () => {
      delete require.cache[require.resolve(path.join(APP, 'routes', 'signals.js'))];
      const app = express();
      app.use('/api/signals', require(path.join(APP, 'routes', 'signals.js')));
      return http.createServer(app);
    });
    s.listen(0, '127.0.0.1', () => {
      http.get({ port: s.address().port, path: '/api/signals/analytics' }, (res) => {
        let b = '';
        res.on('data', (d) => { b += d; });
        res.on('end', () => {
          s.close();
          resolve({ status: res.statusCode, body: JSON.parse(b || '{}') });
        });
      }).on('error', (e) => { s.close(); reject(e); });
    });
  });
}

test('GET /api/signals/analytics publishes net_r and mean_r, not a dollar total', async () => {
  // Two groups with different means, plus an unresolved row. A wire that
  // still drops `net_pnl` and publishes nothing in its place fails the
  // overall figure; a wire that copies one mean onto every group fails
  // the sweep's measured 0.
  const { status, body } = await getAnalytics([
    row(2, { pattern: 'breakout', symbol: 'BTC/USDT' }),
    row(-1, { pattern: 'breakout', symbol: 'BTC/USDT' }),
    row(0, { pattern: 'sweep', symbol: 'ETH/USDT' }),
    row(null, { pattern: 'breakout', status: 'NEW' }),
  ]);
  assert.strictEqual(status, 200);
  assert.deepStrictEqual(dollarKeys(body), []);
  assert.strictEqual(body.r_basis, 'gross');
  assert.strictEqual(body.overall.resolved, 3);
  assert.strictEqual(body.overall.net_r, 1);
  assert.strictEqual(body.overall.mean_r, 0.33);
  assert.ok(!('net_pnl' in body.overall));
  const breakout = body.by_pattern.find((g) => g.key === 'breakout');
  const sweep = body.by_pattern.find((g) => g.key === 'sweep');
  assert.strictEqual(breakout.mean_r, 0.5);
  assert.strictEqual(breakout.net_r, 1);
  assert.strictEqual(sweep.mean_r, 0);
  assert.strictEqual(sweep.net_r, 0);
  assert.ok(!('net_pnl' in breakout) && !('net_pnl' in sweep));
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
