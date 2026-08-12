'use strict';
/**
 * A database outage published as a record of zero signals.
 *
 * `GET /api/signals/stats` ended:
 *
 *     } catch (err) {
 *       res.json({ resolved: 0, wins: 0, losses: 0, win_rate: 0, net_pnl: 0 });
 *     }
 *
 * HTTP **200**, so the dashboard's `mustRead(r)` passed, `!s.resolved` was
 * true, and the panel rendered its empty state:
 *
 *     "No resolved signals yet — outcomes appear once signals hit target or stop."
 *
 * A confident claim about the signal record, manufactured by a failed read.
 * That is the sentence CLAUDE.md opens with — a 503 shown as "No venues
 * found" — reached through a 200 rather than around one, which is worse: the
 * caller cannot tell and has no seam at which to.
 *
 * Two smaller ones on the success path. `losses: resolved - wins` over a
 * query whose WHERE is `pnl IS NOT NULL` means the leftover is losses PLUS
 * break-evens, so a signal that resolved at exactly 0.00 was filed as a
 * defeat. And `win_rate: resolved > 0 ? … : 0` plus
 * `COALESCE(SUM(pnl), 0)` both state a measurement over the empty set.
 */
process.env.JWT_SECRET = process.env.JWT_SECRET || 'j'.repeat(64);

const test = require('node:test');
const assert = require('node:assert');
const path = require('node:path');
const http = require('node:http');
const express = require('express');

const APP = path.join(__dirname, '..');

/** Serve routes/signals.js against a planted `signals` table. */
function server(rows, { explode = false } = {}) {
  const pool = {
    execute: async (sql) => {
      if (explode) throw new Error('ER_LOCK_WAIT_TIMEOUT');
      if (!/FROM signals WHERE pnl IS NOT NULL/.test(sql)) return [[]];
      const priced = rows.filter((r) => r.pnl !== null && r.pnl !== undefined);
      return [[{
        resolved: priced.length,
        wins: priced.filter((r) => r.pnl > 0).length,
        losses: priced.filter((r) => r.pnl < 0).length,
        net_pnl: priced.length
          ? priced.reduce((a, r) => a + Number(r.pnl), 0) : null,
      }]];
    },
  };
  const dbPath = require.resolve(path.join(APP, 'db.js'));
  require.cache[dbPath] = { id: dbPath, filename: dbPath, loaded: true,
                            exports: { pool } };
  delete require.cache[require.resolve(path.join(APP, 'routes', 'signals.js'))];
  const app = express();
  app.use(express.json());
  app.use('/api/signals', require(path.join(APP, 'routes', 'signals.js')));
  return http.createServer(app);
}

function stats(rows, opts) {
  return new Promise((resolve, reject) => {
    const s = server(rows, opts);
    s.listen(0, '127.0.0.1', () => {
      http.get({ port: s.address().port, path: '/api/signals/stats' }, (res) => {
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

const sig = (pnl) => ({ pnl });

test('a failed read is a 503, not a record of zero signals', async () => {
  const { status, body } = await stats([], { explode: true });
  assert.strictEqual(status, 503,
    'a database failure returned 200 with an all-zeros record, which the '
    + 'dashboard rendered as "No resolved signals yet"');
  assert.ok(body.error, 'the failure must be nameable by the caller');
  for (const k of ['resolved', 'wins', 'losses', 'win_rate', 'net_pnl']) {
    assert.ok(!(k in body), `the error payload still carries a ${k} figure`);
  }
});

test('the error code is coarse and leaks no driver detail', async () => {
  // Same rule /readyz follows: a fixed vocabulary, never the driver message.
  const { body } = await stats([], { explode: true });
  assert.strictEqual(body.error, 'signal_stats_unavailable');
  assert.ok(!/ER_LOCK_WAIT|mysql|Error:/i.test(JSON.stringify(body)));
});

test('a break-even signal is not a loss', async () => {
  const { body } = await stats([sig(10), sig(-5), sig(0)]);
  assert.strictEqual(body.resolved, 3);
  assert.strictEqual(body.wins, 1);
  assert.strictEqual(body.losses, 1, '0.00 is flat, not down');
  assert.strictEqual(body.flat, 1);
  assert.strictEqual(body.wins + body.losses + body.flat, body.resolved);
});

test('nothing resolved is null, not 0%', async () => {
  const { status, body } = await stats([]);
  assert.strictEqual(status, 200);
  assert.strictEqual(body.resolved, 0);
  assert.strictEqual(body.win_rate, null, '0% claims every signal lost');
  assert.strictEqual(body.net_pnl, null, 'COALESCE(SUM(pnl),0) claims break-even');
});

test('a measured zero still reports itself', async () => {
  // Three signals, all flat. That is a real result and must not be hidden.
  const { body } = await stats([sig(0), sig(0), sig(0)]);
  assert.strictEqual(body.win_rate, 0, 'three resolved, none won — that IS 0%');
  assert.strictEqual(body.net_pnl, 0);
  assert.strictEqual(body.flat, 3);
  assert.strictEqual(body.losses, 0);
});

test('a real record still reads correctly', async () => {
  const { body } = await stats([sig(10), sig(20), sig(-5), sig(-5)]);
  assert.strictEqual(body.resolved, 4);
  assert.strictEqual(body.wins, 2);
  assert.strictEqual(body.losses, 2);
  assert.strictEqual(body.win_rate, 50);
  assert.strictEqual(body.net_pnl, 20);
});
