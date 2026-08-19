'use strict';
/**
 * The bot said why. The panel said "Couldn't load this panel."
 *
 * Reported 2026-08-18: the News radar showed three dead panels and a Retry
 * button. Behind them, `bot/web/user_gateway.py` had answered
 *
 *     HTTP 403 {"error":"not_allowlisted",
 *               "detail":"Not approved for this bot yet. Send /start to it in
 *                         Telegram — the operator gets a request..."}
 *
 * `routes/news.js` relayed the body intact and `fetchJSON` parsed it. `mustRead`
 * then threw everything but the status away, so the panel could not have said
 * more than it did. Four distinct causes — not approved (403), site not wired to
 * the bot (503), gateway unreachable (502), loader crash — printed one sentence
 * and one button, and the button could only ever help with one of them.
 *
 * The diagnosis existed, arrived, and was discarded one function short of the
 * screen. That is this repository's recurring shape: not a missing signal, a
 * signal nothing carried the last inch.
 *
 * `renderPanel` already made this exact argument for 401 — its own comment
 * reads "Retry would loop forever against a 401" — and never generalised it.
 *
 * WHAT THIS FILE GUARDS, in order of what a regression would cost:
 *
 *   1. a recognised refusal names itself and drops the button that cannot work;
 *   2. an UNRECOGNISED code still gets the old generic message — a mapper that
 *      guesses would invent a diagnosis, which is worse than not having one;
 *   3. prose is never mistaken for a code (`{error:"News radar unavailable"}`);
 *   4. the vocabulary is CLOSED and every member is translated, so a failure
 *      state does not revert the product to English at the worst moment;
 *   5. `/api/news` no longer collapses a failed read into the empty state.
 */

const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

const M = require('../public/js/panel-error-model');
const i18n = require('../public/js/i18n');
const APP = fs.readFileSync(path.join(__dirname, '..', 'public', 'js', 'app.js'), 'utf8');
const DASH = fs.readFileSync(path.join(__dirname, '..', 'public', 'js', 'dashboard.js'), 'utf8');

// ── 1. a refusal names itself ────────────────────────────────────────────────

test('the reported 403 says what to do instead of pretending to be broken', () => {
  const v = M.panelFailure({ status: 403, code: 'not_allowlisted' });
  assert.strictEqual(v.key, 'dd.err_not_approved');
  assert.strictEqual(v.action, 'none', 'a Retry cannot approve an account');
  assert.match(v.fallback, /\/start/, 'the copy carries the actual next step');
});

test('every recognised code that cannot be retried drops the button', () => {
  // The button is the load-bearing half. Copy that explains a permanent
  // refusal, over a control that implies it might pass next time, is a
  // self-contradicting panel — and it trains people to click instead of read.
  for (const code of ['not_allowlisted', 'not_authorized', 'gateway_disabled',
                      'admin_only', 'operator_only']) {
    assert.strictEqual(M.panelFailure({ status: 403, code }).action, 'none', code);
  }
  // and the one that IS worth retrying keeps its button
  assert.strictEqual(M.panelFailure({ status: 429, code: 'rate_limited' }).action, 'retry');
});

test('the code outranks the status, because one 403 is not another', () => {
  const a = M.panelFailure({ status: 403, code: 'not_allowlisted' });
  const b = M.panelFailure({ status: 403, code: 'operator_only' });
  assert.notStrictEqual(a.key, b.key,
    'two different refusals collapsed to one sentence — the whole defect');
});

test('a site not wired to the bot says so rather than blaming the panel', () => {
  // routes/news.js returns this when isConfigured() is false: the request never
  // reached the bot, so no amount of retrying changes it, and the person who
  // can fix it is the operator.
  const v = M.panelFailure({ status: 503 });
  assert.strictEqual(v.key, 'dd.err_bot_unlinked');
  assert.strictEqual(v.action, 'none');
});

// ── 2. it does not guess ─────────────────────────────────────────────────────

test('an unrecognised code falls back to the generic message and Retry', () => {
  // THE CONTROL, and the more important half. Every code in the gateway's
  // sixty-odd vocabulary that is NOT mapped here must land exactly where it
  // landed before this change.
  for (const err of [{ status: 502 }, { status: 500, code: 'store_failed' },
                     { status: 400, code: 'bad_mint' }, {}, null]) {
    const v = M.panelFailure(err || {});
    assert.strictEqual(v.key, 'dd.err_panel', JSON.stringify(err));
    assert.strictEqual(v.action, 'retry', JSON.stringify(err));
  }
});

// ── 3. prose is not a code ───────────────────────────────────────────────────

test('a sentence in the error field is not treated as a reason code', () => {
  // `routes/news.js` answers a dead gateway with {error:'News radar
  // unavailable'} — a sentence some route wrote, not a member of any
  // vocabulary. Reading it as a key would miss silently and forever.
  assert.strictEqual(M.codeOf({ error: 'News radar unavailable' }), '');
  assert.strictEqual(M.codeOf({ error: 'Not configured' }), '');
  assert.strictEqual(M.codeOf({ error: 'not_allowlisted' }), 'not_allowlisted');
  for (const junk of [null, undefined, 'a string', { error: 42 }, { error: null }, {}]) {
    assert.strictEqual(M.codeOf(junk), '', JSON.stringify(junk));
  }
});

// ── 4. the vocabulary is closed, and translated ──────────────────────────────

test('every sentence this model can show is in all fourteen languages', () => {
  // A failure state is exactly where a product reverts to English if nobody
  // checks, and it is the worst moment for that to happen.
  const keys = new Set([...Object.values(M.BY_CODE), ...Object.values(M.BY_STATUS), M.GENERIC]
    .map((v) => v.key));
  assert.ok(keys.size >= 5, 'the vocabulary shrank unexpectedly');
  for (const key of keys) {
    assert.ok(i18n.STRINGS[key], `${key} is not in the dictionary at all`);
    for (const l of i18n.LANGS) {
      assert.ok(i18n.STRINGS[key][l.code], `${key} is missing ${l.code}`);
    }
  }
});

test('the vocabulary stays closed — no free-form server text reaches the screen', () => {
  // The upstream `detail` strings read well and are still the wrong shape:
  // untranslatable, and an error path is where internal config leaks. /readyz
  // answers with a coarse code from a fixed set for this reason; so does this.
  const SRC = fs.readFileSync(
    path.join(__dirname, '..', 'public', 'js', 'panel-error-model.js'), 'utf8');
  assert.ok(!/\bdetail\b\s*[;),\]]/.test(SRC.replace(/\/\*[\s\S]*?\*\//g, '')),
    'the model reads an upstream `detail` — codes cross the wire, words are chosen here');
  for (const v of [...Object.values(M.BY_CODE), ...Object.values(M.BY_STATUS), M.GENERIC]) {
    assert.ok(['retry', 'signin', 'none'].includes(v.action), `unknown action ${v.action}`);
    assert.ok(v.key && v.fallback && v.icon, `incomplete entry for ${v.key}`);
  }
});

// ── 5. the wiring, which none of the above can see ───────────────────────────

test('mustRead carries the code, and renderPanel spends it', () => {
  // Every assertion above calls the model directly, so all of them would pass
  // with nothing wired to it — the failure this repository is organised
  // against. Checked from the callers, which is the only place it is visible.
  const from = APP.indexOf('function mustRead(r) {');
  const mr = APP.slice(from, APP.indexOf('\n  }', from));
  assert.match(mr, /e\.code = /, 'mustRead drops the reason on the floor again');
  assert.match(mr, /PanelErrorModel\.codeOf/, 'the code is extracted through the model');

  const ff = APP.indexOf('function fail(');
  const fail = APP.slice(ff, APP.indexOf('\n    }', ff) + 6);
  assert.match(fail, /PanelErrorModel/, 'renderPanel no longer consults the model');
  assert.match(fail, /'none'/, 'the no-action branch is gone — every failure got a button back');
});

test('the model is loaded before app.js on every page that has panels', () => {
  const pub = path.join(__dirname, '..', 'public');
  for (const f of fs.readdirSync(pub).filter((x) => x.endsWith('.html'))) {
    const src = fs.readFileSync(path.join(pub, f), 'utf8');
    const app = src.indexOf('/js/app.js');
    if (app < 0) continue;
    const model = src.indexOf('/js/panel-error-model.js');
    assert.ok(model >= 0, `${f} loads app.js without the error model`);
    assert.ok(model < app, `${f} loads the model after app.js`);
  }
});

test('a failed news read is a failure, not an empty radar', () => {
  // `data = r.ok ? r.data : null` turned a 403 into the same `null` a genuinely
  // empty feed produces, and renderPanel renders null as `empty.text` — so the
  // panel said "The news radar is unavailable right now", the reason code never
  // reached fail(), and nothing above this line could have helped.
  const at = DASH.indexOf("fetchJSON('/api/news')");
  assert.ok(at > 0, 'the news loader moved — re-derive this test');
  const line = DASH.slice(DASH.lastIndexOf('\n', at), DASH.indexOf('\n', at));
  assert.match(line, /mustRead\(/,
    'the news loader swallows its failure into the empty state again');
  assert.ok(!/r\.ok \? r\.data : null/.test(line),
    'a failed read is being collapsed to null — absent is not unreadable');
});
