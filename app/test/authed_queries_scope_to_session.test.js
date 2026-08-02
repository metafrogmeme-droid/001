'use strict';
/**
 * Every authenticated query must scope to the SESSION user, not a supplied one.
 *
 * The shape this exists to prevent is one line long and reads fine:
 *
 *     pool.execute('SELECT ... WHERE user_id = ?', [req.params.uid])
 *
 * That is one user reading another's rows. The correct pattern, used by all
 * 159 authenticated queries in this app today, binds the session identity and
 * lets a caller-supplied value select only WITHIN it:
 *
 *     'WHERE id = ? AND user_id = ?', [posId, userId]
 *
 * Audited by hand across app/routes: 51 modules use authMiddleware, 34 read
 * req.user, and every user_id-scoped query binds a session-derived value.
 * Nothing checked it. A new route could scope on req.params and no test in
 * this repo would notice — the same "true by convention" state §4 was in on
 * the public routes before #1037 enumerated them, and F-15 was in on the
 * handlers before #1038.
 *
 *     THE PROPERTY HELD. NOTHING WAS HOLDING IT.
 *
 * WHAT THIS DELIBERATELY DOES NOT DO
 *
 * It does not try to work out which `?` the user_id maps to — that needs real
 * SQL parsing, and a wrong answer would either miss the defect or flag
 * correct code until someone silences it. It asks the cruder question that
 * cannot be answered wrongly: does a user_id-scoped query bind ANY
 * session-derived value at all? A query that scopes by user_id and never
 * mentions the session is the defect, whatever the parameter order.
 */

const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

const { codeOnly } = require('./helpers/code_only');

const ROUTES = path.join(__dirname, '..', 'routes');

/** `const uid = req.user.user_id` and friends. */
const SESSION_VAR = /(?:const|let|var)\s+(\w+)\s*=\s*[^;\n]*req\.user\b/g;
/** A query filtered on user_id. */
const USER_SCOPED = /user_id\s*=\s*\?/;

/**
 * Exemptions, each with the reason it is safe AND a test below that checks
 * the reason still holds. An exemption nobody re-verifies is how a guard
 * becomes a permission.
 *
 * leaderboard.js — its query lives in a HELPER, `userStats(uid)`, so the
 * scoping happens at the call site and this scan cannot see it. Verified by
 * hand and pinned by `the leaderboard reads only opted-in members` below:
 * the id comes from `SELECT id FROM users WHERE leaderboard_handle IS NOT
 * NULL` — members who opted in — never from the request, and the output is
 * percent/count only per §4.
 *
 * THIS IS THE SCAN'S REAL LIMITATION, stated rather than hidden: a query
 * inside a helper that takes the id as a parameter is invisible here, so a
 * helper is where an IDOR could still hide. Every such site in this app is
 * enumerated in EXEMPT, which is what makes the list meaningful — if it
 * grows, each entry needs its own call-site check.
 */
const EXEMPT = new Set(['leaderboard.js']);

/**
 * The enclosing `function NAME(` for an offset, or null.
 *
 * Four of this app's user-scoped queries live in helpers that take the id as
 * a PARAMETER — tradesOfDay, loadProfile, loadClosed, userStats — so the
 * scoping happens at the call site. Exempting all four would have left the
 * exemption list doing the work and the guard checking almost nothing, which
 * is the shape of a check that exists and protects nothing.
 */
function enclosingFn(src, offset) {
  const re = /(?:async\s+)?function\s+(\w+)\s*\(/g;
  let name = null;
  let m;
  while ((m = re.exec(src)) !== null && m.index < offset) name = m[1];
  return name;
}

/** True when EVERY call to `name` passes a session-derived value. */
function everyCallSiteIsScoped(src, name, names) {
  const re = new RegExp(`(?<!function\\s)\\b${name}\\s*\\(`, 'g');
  let m;
  let seen = 0;
  while ((m = re.exec(src)) !== null) {
    const head = src.slice(Math.max(0, m.index - 60), m.index);
    if (/(?:async\s+)?function\s+$/.test(head)) continue;   // the definition
    let depth = 1;
    let j = m.index + m[0].length;
    while (j < src.length && depth) {
      if (src[j] === '(') depth += 1;
      else if (src[j] === ')') depth -= 1;
      j += 1;
    }
    seen += 1;
    if (!bindsSession(src.slice(m.index, j), names)) return false;
  }
  return seen > 0;
}

function authedRoutes() {
  return fs.readdirSync(ROUTES)
    .filter((f) => f.endsWith('.js'))
    .filter((f) => fs.readFileSync(path.join(ROUTES, f), 'utf8')
      .includes('authMiddleware'));
}

/** Every `pool.execute(...)` call, read to its balanced paren. */
function queries(src) {
  const out = [];
  const re = /pool\.execute\s*\(/g;
  let m;
  while ((m = re.exec(src)) !== null) {
    let depth = 1;
    let j = m.index + m[0].length;
    while (j < src.length && depth) {
      if (src[j] === '(') depth += 1;
      else if (src[j] === ')') depth -= 1;
      j += 1;
    }
    out.push({ line: src.slice(0, m.index).split('\n').length,
               text: src.slice(m.index, j) });
  }
  return out;
}

function sessionNames(src) {
  const out = new Set(['req.user.user_id']);
  SESSION_VAR.lastIndex = 0;
  let m;
  while ((m = SESSION_VAR.exec(src)) !== null) out.add(m[1]);
  return out;
}

function bindsSession(call, names) {
  for (const n of names) {
    if (n === 'req.user.user_id') {
      if (/req\.user\.user_id/.test(call)) return true;
      continue;
    }
    if (new RegExp(`\\b${n}\\b`).test(call)) return true;
  }
  return false;
}

test('the scan finds a real number of authenticated queries', () => {
  // A check that inspects nothing passes everything.
  let n = 0;
  for (const f of authedRoutes()) {
    n += queries(codeOnly(fs.readFileSync(path.join(ROUTES, f), 'utf8'))).length;
  }
  assert.ok(n > 100, `only ${n} authenticated pool.execute calls found`);
});

test('the authed route set is non-trivial', () => {
  assert.ok(authedRoutes().length > 30,
    `only ${authedRoutes().length} authMiddleware routes found`);
});

test('every user_id-scoped query binds the session identity', () => {
  const offenders = [];
  for (const f of authedRoutes()) {
    if (EXEMPT.has(f)) continue;
    const src = codeOnly(fs.readFileSync(path.join(ROUTES, f), 'utf8'));
    const names = sessionNames(src);
    for (const q of queries(src)) {
      if (!USER_SCOPED.test(q.text)) continue;
      if (bindsSession(q.text, names)) continue;
      // The query does not bind the session directly. It may be a helper
      // that receives the id — in which case the CALL SITES are what matter.
      const fn = enclosingFn(src, src.indexOf(q.text));
      if (fn && everyCallSiteIsScoped(src, fn, names)) continue;
      offenders.push(`${f}:${q.line}`);
    }
  }
  assert.deepStrictEqual(offenders, [],
    'these queries filter on user_id without binding the session user — one '
    + `user can read another's rows:\n  ${offenders.join('\n  ')}`);
});

test('a caller-supplied id ALONGSIDE the session user is fine', () => {
  // The correct and common pattern; flagging it would make the check
  // unusable and it would be silenced.
  const src = "const uid = req.user.user_id;\n"
    + "pool.execute('UPDATE t SET x = ? WHERE id = ? AND user_id = ?', [x, id, uid]);";
  const names = sessionNames(src);
  const q = queries(src)[0];
  assert.ok(USER_SCOPED.test(q.text));
  assert.ok(bindsSession(q.text, names));
});

test('the check can actually fail', () => {
  // The defect, verbatim.
  const src = "pool.execute('SELECT * FROM t WHERE user_id = ?', [req.params.uid]);";
  const q = queries(src)[0];
  assert.ok(USER_SCOPED.test(q.text));
  assert.ok(!bindsSession(q.text, sessionNames(src)),
    'a query scoped by a caller-supplied user_id must be flagged');
});

test('a laundered request value is still not the session', () => {
  // `const uid = req.params.uid` reads exactly like the session pattern at a
  // glance, which is what makes it worth a test rather than a code review.
  const src = "const uid = req.params.uid;\n"
    + "pool.execute('SELECT * FROM t WHERE user_id = ?', [uid]);";
  assert.ok(!bindsSession(queries(src)[0].text, sessionNames(src)));
});

test('queries spanning multiple lines are read whole', () => {
  // Nearly every real one is wrapped. A single-line scan sees none of them.
  const src = "const uid = req.user.user_id;\n"
    + "pool.execute(\n  'SELECT x FROM t WHERE user_id = ?',\n  [uid]);";
  const q = queries(src)[0];
  assert.ok(q.text.includes('\n'));
  assert.ok(bindsSession(q.text, sessionNames(src)));
});

test('commented-out code is not scanned', () => {
  const src = "// pool.execute('SELECT * FROM t WHERE user_id = ?', [req.params.uid]);\n"
    + "const x = 1;";
  assert.equal(queries(codeOnly(src)).length, 0);
});

test('the leaderboard reads only opted-in members, never a request id', () => {
  // The exemption above, converted from a claim into a check.
  const src = codeOnly(fs.readFileSync(path.join(ROUTES, 'leaderboard.js'), 'utf8'));
  assert.ok(/leaderboard_handle IS NOT NULL/.test(src),
    'the member list must be an explicit opt-in, not every user');
  const i = src.indexOf('userStats(');
  const call = src.indexOf('userStats(', i + 1);   // the CALL, not the def
  assert.ok(call > 0);
  const line = src.slice(src.lastIndexOf('\n', call), call + 40);
  assert.ok(!/req\.(params|query|body)/.test(line),
    'the leaderboard id must never come from the request');
});

test('the leaderboard publishes percent and counts, not dollars', () => {
  // §4 is what makes a cross-user read acceptable at all here.
  const src = codeOnly(fs.readFileSync(path.join(ROUTES, 'leaderboard.js'), 'utf8'));
  const i = src.indexOf('return {');
  const body = src.slice(i, i + 400);
  assert.ok(/return_pct/.test(body) && /win_rate/.test(body));
  for (const banned of ['net_pnl:', 'pnl_usd', 'total_fees:', 'equity']) {
    assert.ok(!body.includes(banned), `${banned} on a cross-user surface`);
  }
});

test('every exemption is still an authenticated route', () => {
  // A stale exemption is how this file would rot into permission.
  const authed = new Set(authedRoutes());
  for (const f of EXEMPT) {
    assert.ok(authed.has(f), `${f} is exempted but no longer authed — remove it`);
  }
});
