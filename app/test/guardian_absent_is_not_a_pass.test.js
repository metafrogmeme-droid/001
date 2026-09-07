'use strict';
/**
 * Two axes of the Guardian Readiness Score scored 100 off nothing at all.
 *
 * The pure scorer in lib/guardian_readiness.js is careful: every axis is
 * nullable, a null is "not yet observed", the weights renormalise over the
 * observed axes, and an all-null read returns `score: null`. Its own header
 * says so — "any missing one arrives as null and is scored 'not yet observed'
 * (never silently 100)".
 *
 * The route gathering those signals broke the promise twice, and both times by
 * converting an absence into a value BEFORE the scorer could see it was one:
 *
 *   recorderOk = flight.chain.ok !== false
 *
 * `chain.ok` is three-valued at the source — engine.guardian_status() sets it
 * to `None` and only promotes it to a bool when `audit_chain.verify()` actually
 * ran — so an unverifiable evidence chain arrived as null, `null !== false` is
 * true, and the axis scored **100, "Decision→outcome chain is intact"** about
 * provenance nobody had checked. The Telegram card has distinguished the three
 * all along (✅ verified / ⚠️ UNVERIFIED / · unchecked); this had two.
 *
 *   } else { liveState = { live_enabled: false, ... } }   // no controls row
 *
 * scored **100, "Paper only — no live capital exposed"** off a MISSING row.
 * routes/webtrade.js states what `user_controls` actually is: "written only for
 * web-originated control changes, so it is empty for a user who enabled live in
 * Telegram AND for every web-only live user". So the accounts this branch fired
 * on were, precisely, the ones most likely to be live — and it handed them full
 * marks on the highest-consequence axis the score has.
 *
 * And one more downstream of both: routes/guardian.js answered a FAILED read of
 * the flight cache with 200 and "No decisions recorded yet." — a measurement
 * ("the recorder ran and sealed nothing") published from a database error, on
 * the page an operator opens to check whether the evidence is intact.
 */

const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const express = require('express');

process.env.JWT_SECRET = process.env.JWT_SECRET || 'j'.repeat(64);
delete process.env.DATABASE_URL;

const ROOT = path.join(__dirname, '..');
const {
  scoreRecorder, scoreLivegate, scoreReadiness,
} = require(path.join(ROOT, 'lib', 'guardian_readiness'));
const { codeOnly } = require('./helpers/code_only');

// ── the recorder axis ────────────────────────────────────────────────────

test('an unchecked chain is not scored as an intact one', () => {
  assert.strictEqual(scoreRecorder(null).score, null);
  assert.strictEqual(scoreRecorder(undefined).score, null);
  assert.match(scoreRecorder(null).note, /No recorded decisions yet/);
});

test('a verified chain scores, a broken one scores zero', () => {
  assert.strictEqual(scoreRecorder(true).score, 100);
  assert.strictEqual(scoreRecorder(false).score, 0);
  assert.match(scoreRecorder(false).note, /break/i);
});

// ── the live-exposure axis ───────────────────────────────────────────────

test('no live-gate reading is not "paper only"', () => {
  const s = scoreLivegate(null);
  assert.strictEqual(s.score, null);
  assert.match(s.note, /not yet observed/);
});

test('a reading says where it came from', () => {
  // The `drawdown_source` lesson: a number whose provenance is unstated is a
  // number the reader cannot weigh. `user_controls` is a MIRROR of what the
  // website set; the gateway is the gate the confirm path itself consults.
  const mirror = scoreLivegate({ live_enabled: false, allowlisted: false, paused: false, source: 'controls' });
  const gate = scoreLivegate({ live_enabled: false, allowlisted: false, paused: false, source: 'gateway' });
  assert.strictEqual(mirror.score, 100);
  assert.strictEqual(gate.score, 100);
  assert.match(mirror.note, /website controls/);
  assert.match(gate.note, /live gate/i);
  assert.notStrictEqual(mirror.note, gate.note,
    'two different sources produced the same sentence');
});

test('the live scores themselves are unchanged', () => {
  assert.strictEqual(scoreLivegate({ paused: true, live_enabled: true, allowlisted: true }).score, 100);
  assert.strictEqual(scoreLivegate({ live_enabled: true, allowlisted: true }).score, 70);
  assert.strictEqual(scoreLivegate({ live_enabled: true, allowlisted: false }).score, 30);
  assert.strictEqual(scoreLivegate({ live_enabled: false }).score, 100);
});

// ── what the two nulls do to the composed number ─────────────────────────

test('a null axis is excluded rather than counted as a pass', () => {
  const weak = { envelope: { mode: 'off', bound: false } };   // scores 15
  const withUnknowns = scoreReadiness(weak);
  assert.strictEqual(withUnknowns.observed, 1);
  assert.strictEqual(withUnknowns.score, 15,
    'the five unobserved axes must not lift the score — renormalise, never impute');
  // The pre-fix behaviour, reproduced deliberately: had recorder and livegate
  // been imputed as 100, this same input would have scored far higher.
  const imputed = scoreReadiness({ ...weak, recorderOk: true,
    liveState: { live_enabled: false, allowlisted: false, paused: false } });
  assert.ok(imputed.score > withUnknowns.score,
    'sanity: imputing those two really does inflate the number');
});

test('nothing observed is null, not zero and not a hundred', () => {
  const s = scoreReadiness({});
  assert.strictEqual(s.score, null);
  assert.strictEqual(s.band, 'unknown');
  assert.strictEqual(s.observed, 0);
  assert.deepStrictEqual(s.weakest_links, [],
    'an unobserved axis is not a weak link — it is not a link at all');
});

// ── the route wiring, driven ─────────────────────────────────────────────
//
// routes/guardian.js mounts `optionalAuth`, so /flight answers an anonymous
// request — which makes the failed-read path drivable without minting a token.

function appWith(router) {
  const app = express();
  app.use('/api/guardian', router);
  return app;
}

async function get(app, url) {
  const server = await new Promise((r) => {
    const s = app.listen(0, '127.0.0.1', () => r(s));
  });
  try {
    const { port } = server.address();
    return await new Promise((resolve, reject) => {
      http.get(`http://127.0.0.1:${port}${url}`, (res) => {
        let body = '';
        res.on('data', (c) => { body += c; });
        res.on('end', () => resolve({ status: res.statusCode, body: JSON.parse(body || '{}') }));
      }).on('error', reject);
    });
  } finally {
    await new Promise((r) => server.close(r));
  }
}

test('a failed flight-cache read is 503, not "No decisions recorded yet"', async () => {
  const { pool } = require(path.join(ROOT, 'db'));
  const sync = require(path.join(ROOT, 'routes', 'sync'));
  const guardian = require(path.join(ROOT, 'routes', 'guardian'));
  const real = pool.execute;
  pool.execute = async () => { throw new Error('pool exhausted'); };
  try {
    const r = await get(appWith(guardian), '/api/guardian/flight');
    assert.strictEqual(r.status, 503,
      'an unreadable evidence chain answered 200 with an all-clear');
    assert.ok(!/No decisions recorded yet/.test(JSON.stringify(r.body)),
      `a database failure was published as a measurement: ${JSON.stringify(r.body)}`);
    assert.match(r.body.note, /not the same as/i,
      'the reply must say the difference out loud');
    assert.strictEqual(sync.getLatestFlight.lastReadFailed, true);
  } finally {
    pool.execute = real;
  }
});

test('a genuinely empty flight cache still says so, with 200', async () => {
  const { pool } = require(path.join(ROOT, 'db'));
  const sync = require(path.join(ROOT, 'routes', 'sync'));
  const guardian = require(path.join(ROOT, 'routes', 'guardian'));
  const real = pool.execute;
  pool.execute = async () => [[]];             // read fine, nothing stored
  try {
    const r = await get(appWith(guardian), '/api/guardian/flight');
    assert.strictEqual(r.status, 200);
    assert.match(r.body.note, /No decisions recorded yet/,
      'a real empty record must keep its plain answer — the fix must not make '
      + 'every quiet recorder look broken');
    assert.strictEqual(sync.getLatestFlight.lastReadFailed, false);
  } finally {
    pool.execute = real;
  }
});

// ── the two route expressions, locked ────────────────────────────────────
//
// WIRING, not behaviour: the behaviour above is the scorer's, and driving the
// readiness route itself would need a minted session on top of six stubbed
// dependencies to reach two lines. Same justification test_trade_live_mode.py
// gives in its own docstring — and anchored to the whole statement, because
// "asserting a short string is ABSENT is the assertion that keeps misfiring".

const READINESS = codeOnly(fs.readFileSync(
  path.join(ROOT, 'routes', 'guardian_readiness.js'), 'utf8'));

test('the recorder axis passes the chain tri-state through', () => {
  assert.ok(!/recorderOk\s*=\s*flight\.chain\.ok\s*!==\s*false/.test(READINESS),
    'the axis is back to collapsing an unchecked chain onto the pass');
  assert.match(READINESS, /if\s*\(ok === true \|\| ok === false\) recorderOk = ok;/,
    'the tri-state read is gone');
});

test('a missing controls row asks the authority instead of assuming paper', () => {
  assert.ok(!/liveState = \{ live_enabled: false, allowlisted: false, paused: false \}/
    .test(READINESS),
    'the absent-row branch is inventing a paper reading again');
  assert.match(READINESS, /\/trade\/live_mode\?telegram_id=/,
    'nothing asks the bot whether this identity is actually live-capable');
  // And the fallback when the gateway cannot answer must remain no answer.
  assert.ok(!/liveState = \{[^}]*\}\s*;\s*\}\s*\}\s*catch/.test(READINESS)
    || /typeof lm\.data\.live_allowed === 'boolean'/.test(READINESS),
    'the gateway reply is read without checking it actually carried a verdict');
});

// The sync ingest's equity coercion belongs with its siblings and is covered
// there — test/sync_scan_ingest_null_equity.test.js drives both cache writers
// over real HTTP, which is a better test than a second copy of the same regex
// would be. Duplicating a pin in two files means one of them rots.
