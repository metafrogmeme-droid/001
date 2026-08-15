'use strict';
/**
 * "While you were away" reported a failed read as a quiet night — and then
 * destroyed the evidence.
 *
 * GET /api/since initialised its three counts to zero and swallowed each
 * query's exception into a comment that named the wrong cause:
 *
 *     out = { signals_new: 0, events_new: 0, arena: { closes: 0, pnl: 0 } };
 *     try { ...signals... }  catch (e) { /* stream quiet → 0 *␘/ }
 *     try { ...events...  }  catch (e) { /* mind stream quiet → 0 *␘/ }
 *     try { ...arena...   }  catch (e) { /* no arena activity → 0 *␘/ }
 *
 * "stream quiet" and "the query threw" are not the same event, and the digest
 * printed the first when it meant the second.
 *
 * The second half is what makes it more than cosmetic. `last_seen_at` was
 * advanced at the TOP of the handler, before any count ran:
 *
 *     await pool.execute('UPDATE users SET last_seen_at = ? WHERE id = ?', ...)
 *
 * so a failed read reported nothing happened AND consumed the window it had
 * failed to read. The next visit measured from `now`; whatever landed during
 * the outage was unrecoverable. A retry could not fix it because there was
 * nothing left to retry against.
 *
 * The digest is the COMPOSITE case from CLAUDE.md's table — three independent
 * sources, one card — so the strategy is OMIT, not guard: a dead source leaves
 * itself out and names itself, rather than blanking the two that still read.
 */
process.env.JWT_SECRET = process.env.JWT_SECRET || 'j'.repeat(64);

const test = require('node:test');
const assert = require('node:assert');
const path = require('node:path');
const http = require('node:http');
const express = require('express');

const APP = path.join(__dirname, '..');

const LAST_SEEN = new Date('2026-08-01T00:00:00Z');

/**
 * Serve routes/since.js. `dead` names which sections throw; `updates` collects
 * every last_seen_at write so a test can assert the window survived.
 */
function server(dead, updates, { firstVisit = false, quiet = false } = {}) {
  const pool = {
    execute: async (sql) => {
      if (/UPDATE users SET last_seen_at/.test(sql)) { updates.push(sql); return [{}]; }
      if (/FROM users WHERE id/.test(sql)) {
        return [[{ id: 7, last_seen_at: firstVisit ? null : LAST_SEEN }]];
      }
      if (/FROM signals/.test(sql)) {
        if (dead.includes('signals')) throw new Error('ER_LOCK_WAIT_TIMEOUT');
        return [[{ n: quiet ? 0 : 12 }]];
      }
      if (/FROM agent_events/.test(sql)) {
        if (dead.includes('events')) throw new Error('ER_LOCK_WAIT_TIMEOUT');
        return [[{ n: quiet ? 0 : 5 }]];
      }
      if (/FROM arena_trades/.test(sql)) {
        if (dead.includes('arena')) throw new Error('ER_LOCK_WAIT_TIMEOUT');
        return [quiet ? [] : [{ pnl: 3 }, { pnl: -1 }]];
      }
      return [[]];
    },
  };
  const dbPath = require.resolve(path.join(APP, 'db.js'));
  require.cache[dbPath] = { id: dbPath, filename: dbPath, loaded: true,
                            exports: { pool } };
  const authPath = require.resolve(path.join(APP, 'auth.js'));
  require.cache[authPath] = { id: authPath, filename: authPath, loaded: true,
    exports: { authMiddleware: (req, _res, next) => {
      req.user = { user_id: 7 }; next();
    } } };
  delete require.cache[require.resolve(path.join(APP, 'routes', 'since.js'))];
  const app = express();
  app.use('/api/since', require(path.join(APP, 'routes', 'since.js')));
  return http.createServer(app);
}

function since(dead = [], opts) {
  const updates = [];
  return new Promise((resolve, reject) => {
    const s = server(dead, updates, opts);
    s.listen(0, '127.0.0.1', () => {
      http.get({ port: s.address().port, path: '/api/since' }, (res) => {
        let b = '';
        res.on('data', (d) => { b += d; });
        res.on('end', () => {
          s.close();
          resolve({ status: res.statusCode, body: JSON.parse(b || '{}'), updates });
        });
      }).on('error', (e) => { s.close(); reject(e); });
    });
  });
}

test('a clean sweep counts everything and advances the window', async () => {
  const { body, updates } = await since();
  assert.strictEqual(body.signals_new, 12);
  assert.strictEqual(body.events_new, 5);
  assert.deepStrictEqual(body.arena, { closes: 2, pnl: 2 });
  assert.deepStrictEqual(body.unreadable, []);
  assert.strictEqual(updates.length, 1,
    'a complete read must move last_seen_at forward');
});

test('a failed section is absent and named, not zero', async () => {
  const { body } = await since(['signals']);
  assert.strictEqual(body.signals_new, null,
    '0 says the engine found nothing all night');
  assert.deepStrictEqual(body.unreadable, ['signals'],
    'the caller must be able to say WHICH part it could not read');
  // …and the sections that DID read are untouched. That is the whole point of
  // omit over guard here: one dead source must not blank the card.
  assert.strictEqual(body.events_new, 5);
  assert.deepStrictEqual(body.arena, { closes: 2, pnl: 2 });
});

test('a partial read does not consume the window', async () => {
  const { updates } = await since(['arena']);
  assert.strictEqual(updates.length, 0,
    'last_seen_at advanced past events that were never actually read — '
    + 'the digest is unrecoverable on the next visit');
});

test('every section dead is three absences, not a quiet night', async () => {
  const { body, updates } = await since(['signals', 'events', 'arena']);
  assert.strictEqual(body.signals_new, null);
  assert.strictEqual(body.events_new, null);
  assert.strictEqual(body.arena, null);
  assert.deepStrictEqual(body.unreadable, ['signals', 'events', 'arena']);
  assert.strictEqual(updates.length, 0);
});

test('a genuinely quiet night is still reported as zero', async () => {
  // The fix must not turn a real measurement into an absence. This is the
  // failure facing the other way, and it is the one an over-eager null-guard
  // introduces: 0 new signals IS a reading, and the card must say so.
  const { body, updates } = await since([], { quiet: true });
  assert.strictEqual(body.signals_new, 0, 'a measured zero became an absence');
  assert.strictEqual(body.events_new, 0);
  assert.deepStrictEqual(body.arena, { closes: 0, pnl: 0 });
  assert.deepStrictEqual(body.unreadable, [],
    'nothing failed to read — a quiet night is not an unreadable one');
  assert.strictEqual(updates.length, 1,
    'a quiet night is a complete read and DOES advance the window');
});

test('a first visit stamps the window and back-fills nothing', async () => {
  const { body, updates } = await since([], { firstVisit: true });
  assert.strictEqual(body.first_visit, true);
  assert.ok(!('signals_new' in body), 'a first visit invents no history');
  assert.strictEqual(updates.length, 1,
    'the window has to START somewhere or every later digest is unbounded');
});
