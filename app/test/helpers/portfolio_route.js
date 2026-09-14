'use strict';
/**
 * Serve the REAL routes/portfolio.js against stub modules, one server per
 * request, and hand back the parsed body.
 *
 * ONE harness for the three suites that drive this route — the operator path
 * (BOT_USER_ID === the caller), the per-user path (gateway-fed), and the mode
 * strip that renders whatever either of them publishes. Each of the first two
 * carried its own copy of this; a third copy for the strip would have been a
 * third set of stubs that could drift from the route the other two exercise.
 *
 *   operator  — true routes the caller as the bot's own account (the scan-cache
 *               path); false routes through the gateway.
 *   scan      — what `SELECT … FROM scan_cache` answers: a row list, or a
 *               function (a thrower).
 *   snapAt    — the equity snapshot's timestamp (freshness of the "memory").
 *   equity    — the snapshot's equity string.
 *   gateway   — { configured, status, throws, data } for the per-user path.
 *   snapshots — false answers the equity-snapshot query with no row at all
 *               (an account this site never stored an equity for).
 *   stored    — whether the trades table holds ANY row for the account (the
 *               "ever synced" question dbFallback asks); default true.
 *   intercept — (sql) => rows | undefined; a test that needs to SEE a query
 *               (count the INSERTs, say) answers it here and falls through
 *               to the defaults for everything else.
 */
const path = require('node:path');
const http = require('node:http');
const express = require('express');

const APP = path.join(__dirname, '..', '..');

const FRESH = () => new Date();
const OLD = () => new Date(Date.now() - 2 * 60 * 60 * 1000);   // two hours

function stub(rel, exports) {
  const p = require.resolve(path.join(APP, rel));
  require.cache[p] = { id: p, filename: p, loaded: true, exports };
}

function server({
  operator = true, scan = [], snapAt = FRESH, equity = '8200.50',
  gateway = {}, userId = operator ? 1 : 7, intercept = null,
  snapshots = true, stored = true,
} = {}) {
  process.env.JWT_SECRET = process.env.JWT_SECRET || 'j'.repeat(64);
  process.env.BOT_USER_ID = operator ? String(userId) : '999';
  const gw = { configured: true, status: 200, throws: false, data: {}, ...gateway };
  const pool = {
    execute: async (sql) => {
      if (intercept) { const hit = intercept(sql); if (hit !== undefined) return hit; }
      if (/FROM equity_snapshots/.test(sql)) return snapshots ? [[{ equity, snapshot_at: snapAt() }]] : [[]];
      if (/AS stored FROM trades/.test(sql)) return [[{ stored: stored ? 3 : 0 }]];
      if (/FROM scan_cache/.test(sql)) {
        if (typeof scan === 'function') return scan();
        return [scan];
      }
      if (/status = 'OPEN'/.test(sql)) return [[]];
      if (/FROM trades/.test(sql)) return [[{ total: 0, scored: 0, wins: 0, net_pnl: null }]];
      if (/INSERT INTO equity_snapshots/.test(sql)) return [{ affectedRows: 1 }];
      return [[]];
    },
  };
  stub('db.js', { pool });
  stub('auth.js', { authMiddleware: (req, _res, next) => {
    req.user = { user_id: userId, email: operator ? 'op@test.io' : 'u@test.io' }; next();
  } });
  stub(path.join('lib', 'gateway.js'), {
    isConfigured: () => gw.configured,
    getGateway: async () => {
      if (gw.throws) throw new Error('socket hang up');
      return { status: gw.status, data: gw.data };
    },
  });
  stub(path.join('lib', 'identity.js'), {
    resolveBotIdentity: async () => ({ id: String(userId), linked: true }),
  });
  delete require.cache[require.resolve(path.join(APP, 'routes', 'portfolio.js'))];
  const app = express();
  app.use(express.json());
  app.use('/api/portfolio', require(path.join(APP, 'routes', 'portfolio.js')));
  return http.createServer(app);
}

/** GET /api/portfolio once against a fresh server; resolves {status, body}. */
function get(opts) {
  return new Promise((resolve, reject) => {
    const s = server(opts);
    s.listen(0, '127.0.0.1', () => {
      http.get({ port: s.address().port, path: '/api/portfolio' }, (res) => {
        let b = '';
        res.on('data', (d) => { b += d; });
        res.on('end', () => { s.close(); resolve({ status: res.statusCode, body: JSON.parse(b || '{}') }); });
      }).on('error', (e) => { s.close(); reject(e); });
    });
  });
}

/** One scan_cache row carrying `cb` as the payload's circuit_breaker. */
const scanRow = (cb, received = FRESH) => [{
  scan_json: JSON.stringify({ circuit_breaker: cb, received_at: received().toISOString() }),
  updated_at: received(),
}];

module.exports = { get, server, scanRow, FRESH, OLD };
