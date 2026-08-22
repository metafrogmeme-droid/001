'use strict';
// A rejected promise nobody caught must not be silent.
//
// Every panel loader is async. A throw inside one — a TypeError on an
// unexpected payload shape — produces an unhandled rejection, which fires
// NEITHER window.onerror (synchronous throws only) nor any console entry the
// page controls. The page renders less than it should and leaves no trace,
// which is one of the two mechanisms that could produce a half-drawn Arena
// with no error anywhere.

const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

const pub = (f) => fs.readFileSync(path.join(__dirname, '..', 'public', f), 'utf8');

test('every page reports unhandled rejections, via app.js', () => {
  const app = pub('js/app.js');
  assert.match(app, /addEventListener\('unhandledrejection'/,
    'app.js installs a global rejection reporter — it loads on every page');
  const h = app.slice(app.indexOf("addEventListener('unhandledrejection'"));
  assert.match(h.slice(0, 400), /console\.error\('UNHANDLED REJECTION/,
    'and names it loudly');
  assert.match(h.slice(0, 400), /stack \|\| .*message/,
    'with the stack, not just the message');
});

test('a page with a visible error banner shows rejections too, not just throws', () => {
  const arena = pub('arena.html');
  assert.match(arena, /window\.addEventListener\('error', rcReportPageError\)/,
    'the reporter is registered for synchronous errors');
  assert.match(arena, /window\.addEventListener\('unhandledrejection', rcReportPageError\)/,
    'AND for unhandled rejections — the gap that hid an async renderer throw');
  // The banner must be able to name a rejection reason, which has no .message
  // at the event level the way an ErrorEvent does.
  assert.match(arena, /e\.reason && \(e\.reason\.message \|\| e\.reason\)/,
    'and can name the reason of a rejection, not only an ErrorEvent');
});

test('the reporter can never make things worse', () => {
  // THE WRAP WAS PINNED TO THE COMMENT INSIDE IT — the pattern literally
  // included `\/\* the reporter itself must never throw`. Rewording that
  // sentence broke the test on unchanged code, and replacing the empty body
  // with something that CAN throw would have left it green as long as the
  // comment stayed. On a handler whose entire job is to survive a page that is
  // already failing.
  //
  // Ask what the catch DOES instead, which is: nothing. A reporter that
  // rethrows, logs through a helper, or touches the DOM again inside its own
  // failure path is the defect — and an empty body is the only shape that
  // cannot be any of them.
  const arena = pub('arena.html');
  const at = arena.indexOf('function rcReportPageError');
  assert.ok(at > 0, 'the page error reporter is gone');
  const fn = arena.slice(at, arena.indexOf('window.addEventListener(\'error\'', at));
  const m = fn.match(/catch\s*\(\w*\)\s*\{([\s\S]*?)\}/);
  assert.ok(m, 'the reporter is no longer wrapped at all — it can now replace '
    + 'the very failure it exists to display');
  const { codeOnly } = require('./helpers/code_only');
  assert.strictEqual(codeOnly(m[1]).trim(), '',
    'the reporter\'s own catch does something now; anything it does can throw, '
    + 'and then the page fails twice and shows neither');
  assert.match(fn.slice(0, 400), /getElementById\('jsErr'\)/,
    'and shows at most one banner');
});

test('the Arena follow toggle upserts — a PRIMARY KEY makes a bare INSERT a 500', () => {
  // arena_follows.user_id is the PRIMARY KEY, so the SECOND toggle duplicate-
  // keys. It passed locally only because the in-memory shim implements this
  // statement as an upsert, so real MySQL behaviour was never exercised.
  const src = fs.readFileSync(path.join(__dirname, '..', 'routes', 'arena.js'), 'utf8');
  const i = src.indexOf('INSERT INTO arena_follows');
  assert.ok(i > 0, 'the follow insert exists');
  assert.match(src.slice(i, i + 500), /ON DUPLICATE KEY UPDATE/,
    'it upserts rather than duplicate-keying on the second toggle');
  assert.match(src.slice(i, i + 500), /last_signal_id = VALUES\(last_signal_id\)/,
    'and refreshes the cursor, so re-enabling never back-fills old calls');

  const j = src.indexOf('INSERT INTO arena_accounts');
  assert.ok(j > 0, 'the account provisioning insert exists');
  assert.match(src.slice(j, j + 400), /ON DUPLICATE KEY UPDATE user_id = user_id/,
    'provisioning is idempotent under a concurrent first load');
  assert.doesNotMatch(src.slice(j, j + 400), /balance = VALUES\(balance\)/,
    'and must never reset the balance of an account that already exists');
});

test('no writer anywhere bare-INSERTs a full primary key or unique', () => {
  // The class, not the instance: an INSERT that supplies a whole PK or UNIQUE
  // and has no ON DUPLICATE / IGNORE is a duplicate-key 500 waiting for a
  // second call or a concurrent one.
  const db = fs.readFileSync(path.join(__dirname, '..', 'db.js'), 'utf8');
  const keys = {};
  for (const m of db.matchAll(/CREATE TABLE IF NOT EXISTS (\w+) \(([\s\S]*?)\n\s*\)/g)) {
    const cols = m[2].split('\n');
    const pk = cols.filter((c) => /PRIMARY KEY/.test(c) && !/^\s*PRIMARY KEY/.test(c))
      .map((c) => c.trim().split(/\s+/)[0]);
    const uniq = [...m[2].matchAll(/UNIQUE(?: KEY)?\s*\w*\s*\(([^)]+)\)/g)]
      .map((u) => u[1].replace(/\s/g, '').split(','));
    // Inline form: `week_key VARCHAR(10) NOT NULL UNIQUE`. Missing this is how
    // the letter-generation race got past the first version of this guard.
    for (const c of cols) {
      const t = c.trim();
      if (/\bUNIQUE\b/.test(t) && !/^UNIQUE/i.test(t) && !/\(/.test(t.split(/\s+/)[0])) {
        uniq.push([t.split(/\s+/)[0]]);
      }
    }
    if (pk.length || uniq.length) keys[m[1].toLowerCase()] = { pk, uniq };
  }
  // Everywhere that writes — the first version scanned only routes/, and the
  // letter race lives in lib/.
  const roots = ['routes', 'lib'];
  const files = [];
  for (const r of roots) {
    const d = path.join(__dirname, '..', r);
    for (const f of fs.readdirSync(d).filter((x) => x.endsWith('.js'))) files.push(path.join(r, f));
  }
  for (const f of ['auth.js', 'server.js']) {
    if (fs.existsSync(path.join(__dirname, '..', f))) files.push(f);
  }
  const offenders = [];
  for (const f of files) {
    const src = fs.readFileSync(path.join(__dirname, '..', f), 'utf8');
    for (const m of src.matchAll(/INSERT(\s+IGNORE)?\s+INTO\s+(\w+)\s*\(([^)]*)\)/gi)) {
      if (m[1]) continue;                                   // INSERT IGNORE is deliberate
      const t = keys[m[2].toLowerCase()];
      if (!t) continue;
      if (/ON DUPLICATE KEY UPDATE/i.test(src.slice(m.index, m.index + 600))) continue;
      // A duplicate-key that is deliberately CAUGHT is fine, and sometimes it
      // is the only correct answer: /auth/register must let the insert fail
      // and return a uniform error, because a pre-check would leak whether an
      // email is registered. Accept an insert whose file handles ER_DUP_ENTRY.
      if (/catch \(e\) \{ \/\* concurrent/.test(src.slice(m.index, m.index + 700))) continue;
      if (/ER_DUP_ENTRY/.test(src)) continue;
      const cols = m[3].split(',').map((c) => c.trim());
      const full = (t.pk.length && t.pk.every((k) => cols.includes(k)))
        || t.uniq.some((u) => u.every((c) => cols.includes(c)));
      if (full) offenders.push(`${f}:${src.slice(0, m.index).split('\n').length} INSERT INTO ${m[2]}`);
    }
  }
  assert.deepStrictEqual(offenders, [],
    'these will duplicate-key on a second or concurrent call:\n  ' + offenders.join('\n  '));
});
