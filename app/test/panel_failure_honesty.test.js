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
const { codeOnly } = require('./helpers/code_only');
const M = require('../public/js/panel-error-model');

const APP = fs.readFileSync(path.join(__dirname, '..', 'public', 'js', 'app.js'), 'utf8');
// COMMENTS BLANKED FIRST — the rule CLAUDE.md states and this file was missing.
//
// `loaderBodies` finds a loader's end by counting parentheses, and a comment
// can hold one that never closes. Prose about a method call writes it exactly
// that way (`.referralTierState(`), and one such comment made the walker run
// past its panel and swallow the eighteen that followed. The overlong body then
// INHERITS a later panel's `mustRead(` and the honesty check `continue`s over a
// panel it never actually inspected — a false negative on the structural
// enforcer of this repo's central rule, produced by a comment.
//
// Blanking is in place, so `line` still points at the right line, and string
// contents survive verbatim, so the wording assertions below are unaffected.
// The `mustRead(` floor is unchanged either way: no comment was propping it up,
// and the blanked and raw counts still agree.
//
// THAT SENTENCE USED TO CARRY AN INTEGER — "unchanged at 70" — and it was one
// of FOUR copies of a number that disagreed: this comment said 70, the
// assertion below says 65, rc_helper_imports.test.js says "65+", and a count
// says 71 (on 69 lines; one line carries three calls). Only the assertion is
// load-bearing, so only the assertion states a number now. A number in prose
// is the part that rots first, and prose cannot be driven.
const DASH = codeOnly(
  fs.readFileSync(path.join(__dirname, '..', 'public', 'js', 'dashboard.js'), 'utf8'));

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
  //
  // It counts CALLS, not loaders, and those are different numbers — one line
  // carries three of them, and six calls sit outside any renderPanel body. The
  // floor sits deliberately below the measurement so that retiring a panel does
  // not fail it; it exists to catch the guard falling out of use wholesale.
  const guards = (DASH.match(/\bmustRead\(/g) || []).length;
  assert.ok(guards >= 65, `expected the read guard at 65+ call sites, found ${guards}`);
});

/**
 * The body of every renderPanel(el, loader) — brace-matched from the call.
 *
 * The single-line scan above only catches `if (!r.ok) return null;` written on
 * ONE line. Three live escapes were found by hand afterwards, all of which
 * simply split the fetch from the null-check:
 *
 *     const load = async () => { ...; data = r.ok ? r.data : null; return data; };
 *     renderPanel(el, async () => { await load(); if (!data) return null; ... });
 *
 * A guard whose coverage is narrower than the rule it states is the same
 * defect it exists to prevent. This one walks the whole loader body.
 */
function loaderBodies(src) {
  const out = [];
  const re = /renderPanel\(/g;
  let m;
  while ((m = re.exec(src))) {
    let depth = 0, i = m.index + 'renderPanel'.length;
    for (; i < src.length; i++) {
      const c = src[i];
      if (c === '(') depth++;
      else if (c === ')') { depth--; if (depth === 0) break; }
    }
    out.push({ body: src.slice(m.index, i + 1), line: src.slice(0, m.index).split('\n').length });
  }
  return out;
}

test('a loader that reads the network guards that read, however it is written', () => {
  // A loader is honest by one of two strategies, and the test accepts either:
  //
  //   GUARD    — mustRead()/throw, so renderPanel paints the error state and
  //              a Retry button. Right for a panel with one source.
  //   OMIT     — every fetch individually .catch()'d, so no rejection can
  //              reach the render and each missing source is simply left out.
  //              Right for a composite panel where one dead source must not
  //              blank the other four. c-agent states the rule in place:
  //              "anything unavailable is omitted, never invented".
  //
  // What is banned is NEITHER: a fetch whose failure falls through into the
  // empty state, which is how the leaderboard came to tell users there were
  // no ranked traders because our own request 500'd.
  //
  // Honest limit: "every fetch is caught" proves the panel still RENDERS, not
  // that the render omits rather than invents. It is a structural proxy, and
  // the wording tests below cover the claims themselves.
  const offenders = [];
  for (const { body, line } of loaderBodies(DASH)) {
    if (/mustRead\(/.test(body) || /\bthrow\b/.test(body)) continue;
    // Any load() helper it delegates to must itself guard.
    if (/\bload\(\)/.test(body)) {
      if (/const load = async[\s\S]{0,500}?mustRead\(/.test(DASH)) continue;
      offenders.push(`dashboard.js:${line} (delegates to an unguarded load())`);
      continue;
    }
    const unc = [...body.matchAll(/fetchJSON\(/g)]
      .filter((m) => !/\.catch\(/.test(body.slice(m.index, m.index + 200)));
    if (unc.length) offenders.push(`dashboard.js:${line} (${unc.length} unguarded fetch)`);
  }
  assert.deepStrictEqual(offenders, [],
    'these panels can render a failed read as an empty one:\n  ' + offenders.join('\n  '));
});

test('a claim about emptiness never comes from an unread response', () => {
  // Direct-innerHTML writers are outside renderPanel entirely, so neither
  // scan above sees them. Both of these told the user their own account was
  // empty — "No strategies yet", and a pinned-strategy bar that simply
  // vanished — when the request had merely failed.
  for (const [anchor, needle] of [
    ['async function refreshMyStrat', 'if (!readOk)'],
    ['async function loadBotStrat', 'if (!readOk)'],
  ]) {
    const i = DASH.indexOf(anchor);
    assert.ok(i > 0, `${anchor} not found`);
    const body = DASH.slice(i, i + 1600);
    assert.ok(body.includes(needle),
      `${anchor} must distinguish an unreadable response from an empty one`);
  }
});

test('the unreadable branches say the user has lost nothing', () => {
  // A read failure changes nothing on the server. Saying so is the difference
  // between a display glitch and a user believing their strategy was dropped.
  assert.match(DASH, /not an empty list\. Nothing has been deleted\./);
  assert.match(DASH, /Your bot is unaffected/);
});

test('mustRead throws on server errors and passes 404 through as empty', () => {
  // Re-declared from app.js (browser globals cannot be required here); the
  // assertion below proves this copy still matches the shipped source.
  function mustRead(r) {
    if (!r || (!r.ok && r.status !== 404)) {
      throw new Error('panel read failed: HTTP ' + (r ? r.status : 'no response'));
    }
    if (r.ok && r.unreadable) {
      const e = new Error('panel read failed: HTTP ' + r.status + ' body did not parse');
      e.status = r.status;
      e.code = 'unreadable_body';
      throw e;
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

/**
 * The REAL mustRead, sliced out of the shipped app.js and made callable.
 *
 * The test above re-declares it by hand, and that copy is pinned against the
 * source by the "shipped mustRead matches" test — a design this file chose
 * deliberately and which stays. But a copy can only be pinned on the lines
 * somebody remembered to pin, and the branch below was added precisely because
 * nobody could see the case it handles. So this one EVALUATES the shipped text:
 * there is no second answer to keep in step, and a branch deleted from app.js
 * fails here on the first assertion rather than on a regex nobody updated.
 */
function shippedMustRead() {
  const from = APP.slice(APP.indexOf('function mustRead(r) {'));
  const src = from.slice(0, from.indexOf('\n  }') + 4);
  // `window` is the only global the body reaches for, and only to look up the
  // error model that is required directly here.
  // eslint-disable-next-line no-new-func
  const make = new Function('window', `${src}; return mustRead;`);
  return { mustRead: make({ PanelErrorModel: M }) };
}

/** The REAL fetchJSON, sliced out of app.js. It closes over exactly two things
 *  this harness must supply — `fetch` and `authHeaders` — so it can be driven
 *  against a real Response rather than a hand-built object. */
function shippedFetchJSON(fetchStub) {
  const from = APP.slice(APP.indexOf('async function fetchJSON('));
  const src = from.slice(0, from.indexOf('\n  }') + 4);
  // eslint-disable-next-line no-new-func
  return new Function('fetch', 'authHeaders', `${src}; return fetchJSON;`)(
    fetchStub, () => ({}));
}

test('fetchJSON records WHICH of the two nulls it is holding', async () => {
  // THE HALF THAT MAKES THE REST WORK, and the half a hand-built response
  // object cannot see. mustRead's new branch reads `r.unreadable`; if fetchJSON
  // stops setting it the fix is inert and every assertion below mustRead still
  // passes. This drives the shipped function against real Response bodies.
  // Drives the shipped function against a real Response — `of(...)` performs
  // the read, it does not merely build the reader.
  const of = (body, init) =>
    shippedFetchJSON(async () => new Response(body, init))('/api/anything');

  const html = await of('<html>gateway interstitial</html>', { status: 200 });
  assert.strictEqual(html.ok, true, 'the server did answer');
  assert.strictEqual(html.data, null);
  assert.strictEqual(html.unreadable, true, 'and the parse failure is on the record');

  // THE DISCRIMINATOR. Both of these carry `data: null`; only one of them is a
  // failed read. routes/insight.js ends `res.json(r.data)`, so a bot-supplied
  // JSON null reaches the browser and parses cleanly — and must keep reading
  // as a reading.
  const jsonNull = await of('null', { status: 200, headers: { 'content-type': 'application/json' } });
  assert.strictEqual(jsonNull.data, null);
  assert.strictEqual(jsonNull.unreadable, false,
    'a clean parse of the JSON literal null is NOT an unreadable body');

  const good = await of('{"a":1}', { status: 200, headers: { 'content-type': 'application/json' } });
  assert.deepStrictEqual(good.data, { a: 1 });
  assert.strictEqual(good.unreadable, false);

  // An empty body does not parse, and nothing reachable through fetchJSON
  // answers one today (no 204, no res.sendStatus, no no-arg res.json). Pinned
  // so that adding such a route is a deliberate decision rather than a panel
  // that quietly starts erroring.
  const empty = await of('', { status: 200 });
  assert.strictEqual(empty.unreadable, true);
});

test('a 2xx whose body did not parse is a failed read, not an empty one', () => {
  // THE CASE THIS FUNCTION COULD NOT SEE. The status says the server answered;
  // it says nothing about whether an ANSWER arrived. A proxy interstitial, a
  // body truncated after the headers landed, a gzip fault: fetchJSON caught the
  // parse error, returned {ok:true, data:null}, and mustRead handed that null
  // on — byte-identical to its 404 case, which renderPanel paints as EMPTY.
  //
  // Driven rather than scanned, because the whole defect was that the shape
  // looked right. The two inputs below differ in one field.
  const { mustRead } = shippedMustRead();

  let e = null;
  try { mustRead({ ok: true, status: 200, data: null, unreadable: true }); }
  catch (err) { e = err; }
  assert.ok(e, 'an unparseable 2xx must throw, or renderPanel paints the empty state');
  assert.match(String(e.message), /did not parse/);
  assert.strictEqual(e.status, 200, 'the status travels so the model can read it');
  assert.strictEqual(e.code, 'unreadable_body', 'and the code, so the sentence is the true one');

  // AND THE HALF THAT MUST NOT THROW. `data === null` cannot carry the parse
  // outcome: routes/insight.js ends `res.json(r.data)`, so a bot-supplied JSON
  // `null` reaches the browser and parses CLEANLY to null. Keying the throw on
  // `data == null` would manufacture a failure on a panel that read perfectly.
  assert.strictEqual(mustRead({ ok: true, status: 200, data: null, unreadable: false }), null,
    'a clean parse of the JSON literal null is a real reading');

  // The 404 doctrine is unreachable from the new branch by construction: 404 is
  // not 2xx. Pinned so a later edit cannot gate the throw on `!r.unreadable`
  // alone and start throwing on the one absence that is honest.
  assert.strictEqual(mustRead({ ok: false, status: 404, data: null, unreadable: true }), null,
    'a 404 stays an empty state even when its body was unparseable HTML');

  // The panel then says something true rather than "Couldn't load this panel."
  const chosen = M.panelFailure({ status: 200, code: 'unreadable_body' });
  assert.strictEqual(chosen.action, 'retry');
  assert.notStrictEqual(chosen.key, M.GENERIC.key,
    'its own key, or a panel errorText outranks it and names the wrong cause');
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
  // THIS PINNED A SPELLING AND BLOCKED ITS OWN GENERALISATION. It required the
  // literal `err && err.status === 401` inside fail(), so extending the same
  // reasoning — "a Retry that cannot work is worse than no Retry" — to every
  // other final refusal failed here, on the test that argues for it. The claim
  // worth keeping is the OUTCOME: a 401 says the session expired and offers a
  // link back in. That is asserted through the model now, and the rendering is
  // pinned on the action rather than on how the action is decided.
  const M = require('../public/js/panel-error-model');
  const v = M.panelFailure({ status: 401 });
  assert.strictEqual(v.key, 'dd.session_expired', 'a 401 is told apart from a failed read');
  assert.strictEqual(v.action, 'signin', 'and is not sent to the Retry button');

  const from = APP.indexOf('function fail(');
  const failFn = APP.slice(from, APP.indexOf('\n    }', from) + 6);
  assert.match(failFn, /dd\.sign_in/, 'it offers a way back in');
  assert.match(failFn, /'signin'\s*\?[\s\S]{0,200}href="\/"/, 'sign-in is a link, not a Retry button');

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
