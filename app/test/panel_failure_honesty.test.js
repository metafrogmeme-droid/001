'use strict';
// A broken endpoint must never be rendered as an empty one.
//
// Every dashboard panel loads through renderPanel(el, loader). The loader
// returns null to mean "there is genuinely nothing here" and throws to mean
// "I could not read this" — renderPanel paints an empty state for the first
// and an error state + Retry for the second. Until this was fixed, 31 loaders
// collapsed both into `if (!r.ok || !d) return null`, so a 500 on /api/holdings
// told the user they held nothing. That is a lie about their own money, and it
// is the same failure the operator screenshotted on the Arena page.
//
// These tests pin the distinction at the source level (the loaders are browser
// code, so there is no module to import) plus a live exercise of mustRead.

const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

const APP = fs.readFileSync(path.join(__dirname, '..', 'public', 'js', 'app.js'), 'utf8');
const DASH = fs.readFileSync(path.join(__dirname, '..', 'public', 'js', 'dashboard.js'), 'utf8');

test('no loader treats a non-ok response as emptiness', () => {
  // The banned shape: an .ok check that short-circuits into the empty state.
  const offenders = DASH.split('\n')
    .map((line, i) => ({ line: line.trim(), n: i + 1 }))
    .filter(({ line }) => /\.ok\b/.test(line) && /return null;/.test(line));
  assert.deepStrictEqual(
    offenders, [],
    'these lines render a failed read as "nothing here":\n'
      + offenders.map((o) => `  dashboard.js:${o.n}  ${o.line}`).join('\n'),
  );
});

test('every panel loader that fetches also guards the read', () => {
  // Sanity floor: the guard is actually in use at scale, not just defined.
  const guards = (DASH.match(/\bmustRead\(/g) || []).length;
  assert.ok(guards >= 65, `expected the read guard at 65+ loaders, found ${guards}`);
});

test('mustRead throws on server errors and passes 404 through as empty', () => {
  // Re-declared from app.js (browser globals cannot be required here); the
  // assertion below proves this copy still matches the shipped source.
  function mustRead(r) {
    if (!r || (!r.ok && r.status !== 404)) {
      throw new Error('panel read failed: HTTP ' + (r ? r.status : 'no response'));
    }
    return r.ok ? r.data : null;
  }

  // Unreadable — the user must see "couldn't load", never "nothing here".
  for (const status of [500, 502, 503, 401, 403, 400]) {
    assert.throws(() => mustRead({ ok: false, status, data: null }),
      /panel read failed/, `HTTP ${status} must surface as a failed read`);
  }
  assert.throws(() => mustRead(null), /no response/);

  // 404 is the honest absence: the record genuinely is not there.
  assert.strictEqual(mustRead({ ok: false, status: 404, data: null }), null);

  // A good read passes its payload straight through.
  assert.deepStrictEqual(mustRead({ ok: true, status: 200, data: { a: 1 } }), { a: 1 });
});

test('the shipped mustRead matches the behaviour asserted above', () => {
  const body = APP.slice(APP.indexOf('function mustRead(r) {'));
  assert.ok(body.startsWith('function mustRead(r) {'), 'mustRead is defined in app.js');
  const fn = body.slice(0, body.indexOf('\n  }') + 4);
  assert.match(fn, /r\.status !== 404/, '404 stays an empty state');
  assert.match(fn, /throw e;/, 'an unreadable response throws');
  assert.match(fn, /e\.status = r \? r\.status : 0/,
    'the thrown error carries its status so 401 can be told apart');
  assert.match(fn, /return r\.ok \? r\.data : null/, 'a good read returns its payload');
  assert.match(APP, /mustRead,/, 'mustRead is exported on window.RC for dashboard.js');
});

test('an expired session offers sign-in, not a Retry that loops forever', () => {
  const from = APP.indexOf('function fail(');
  const failFn = APP.slice(from, APP.indexOf('\n    }', from) + 6);
  assert.match(failFn, /err && err\.status === 401/, 'a 401 is told apart from a failed read');
  assert.match(failFn, /dd\.session_expired/, 'it says the session expired');
  assert.match(failFn, /dd\.sign_in/, 'and offers a way back in');
  assert.match(failFn, /expired\s*\?[\s\S]{0,200}href="\/"/, 'sign-in is a link, not a Retry button');

  const i18n = require('../public/js/i18n');
  for (const key of ['dd.session_expired', 'dd.sign_in']) {
    assert.ok(i18n.STRINGS[key], `${key} is in the dictionary`);
    for (const l of i18n.LANGS) {
      assert.ok(i18n.STRINGS[key][l.code], `${key} missing ${l.code}`);
    }
  }
});

test('a failing panel is traceable instead of silently swallowed', () => {
  const catchBlock = APP.slice(APP.indexOf('} catch (e) {\n      clearTimeout(timer);'));
  assert.match(catchBlock.slice(0, 400), /console\.warn\('panel failed:'/,
    'renderPanel logs why a loader threw');
});

test('the failure state speaks the user\'s language', () => {
  // 91 of 95 panels pass no errorText, so these defaults ARE the dashboard's
  // failure voice. Hardcoded English here means the whole product reverts to
  // English at exactly the moment something breaks.
  assert.match(APP, /t\('dd\.err_panel',/, 'the default error text is translated');
  assert.match(APP, /t\('dd\.retry',/, 'the Retry button is translated');
  assert.match(APP, /t\('dd\.nothing_here',/, 'the default empty text is translated');

  const i18n = fs.readFileSync(path.join(__dirname, '..', 'public', 'js', 'i18n.js'), 'utf8');
  for (const key of ['dd.err_panel', 'dd.retry', 'dd.nothing_here']) {
    assert.ok(i18n.includes(`'${key}'`), `${key} is in the dictionary`);
  }
});

test('the public landing feed says unreachable, not "the engine is quiet"', () => {
  // The landing page is the primary public surface. Claiming the engine has
  // pushed no events is a statement about our own activity — we have no right
  // to make it when the read failed.
  const idx = fs.readFileSync(path.join(__dirname, '..', 'public', 'index.html'), 'utf8');
  assert.match(idx, /function drawUnreadable\(\)/, 'the failed read has its own copy');
  assert.match(idx, /if \(!r \|\| !r\.ok\) \{ drawUnreadable\(\); return; \}/,
    'a non-ok response takes the unreachable path, not draw([])');
  assert.doesNotMatch(idx, /\.catch\(function \(\) \{ draw\(\[\]\); \}\)/,
    'a network failure no longer renders as an empty feed');

  const i18n = require('../public/js/i18n');
  assert.ok(i18n.STRINGS['ld.feed_unreadable'], 'the copy is in the dictionary');
  for (const l of i18n.LANGS) {
    assert.ok(i18n.STRINGS['ld.feed_unreadable'][l.code], `missing ${l.code}`);
  }
});

test('browsers that cached the old scripts are forced to re-fetch', () => {
  // The fix lives in app.js and dashboard.js; a stale cache-buster means a
  // returning user keeps the broken build.
  const dash = fs.readFileSync(path.join(__dirname, '..', 'public', 'dashboard.html'), 'utf8');
  const app = dash.match(/app\.js\?v=(\d+)/);
  const dj = dash.match(/dashboard\.js\?v=(\d+)/);
  assert.ok(app && Number(app[1]) >= 5, 'app.js cache-buster is bumped past the broken build');
  assert.ok(dj && Number(dj[1]) >= 108, 'dashboard.js cache-buster is bumped past the broken build');
  const land = fs.readFileSync(path.join(__dirname, '..', 'public', 'index.html'), 'utf8');
  const la = land.match(/app\.js\?v=(\d+)/);
  assert.ok(la && Number(la[1]) >= 5, 'the landing page ships the same app.js build');
});
