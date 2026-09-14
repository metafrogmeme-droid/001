'use strict';
/**
 * An OMIT panel has no door, so it had no way to tell "could not read" from
 * "read as empty".
 *
 * `mustRead` is the GUARD strategy's door: throw, and renderPanel paints the
 * error state. A composite panel that `.catch()`es each source has no door at
 * all — a caught null and an unparseable 2xx both fall to `r?.data?.x || []`,
 * and `[]` is a claim: "no alerts", "you watch nothing", "nothing connected",
 * "not configured on the server yet", "no chart patterns detected", two
 * unchecked notification toggles, an analysis bridge counted as UP because an
 * HTML interstitial arrived with status 200. The strict read (#114) closed
 * every site that tests `r` or `r.ok`; these are the ones that test INSIDE
 * `r.data`, where the two nulls collapse to one path.
 *
 * `wasRead(r)` is the one reading — read means the server answered AND the
 * answer parsed — and every omit site asks it. The pure halves are driven
 * (`wasRead` itself, sliced out of the shipped app.js; the venue picker's
 * fourth state); the wiring is pinned per site with comments stripped, because
 * the loaders are browser code and a scan is what reaches them.
 */

const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const { codeOnly } = require('./helpers/code_only');
const Picker = require('../public/js/venue-picker-model');
const i18n = require('../public/js/i18n');

const PUB = path.join(__dirname, '..', 'public', 'js');
const APP = fs.readFileSync(path.join(PUB, 'app.js'), 'utf8');
const DASH = codeOnly(fs.readFileSync(path.join(PUB, 'dashboard.js'), 'utf8'));

/** The REAL wasRead, evaluated out of the shipped app.js — no second copy. */
function shippedWasRead() {
  const from = APP.slice(APP.indexOf('function wasRead(r) {'));
  const src = from.slice(0, from.indexOf('\n  }') + 4);
  // eslint-disable-next-line no-new-func
  return new Function(`${src}; return wasRead;`)();
}

test('wasRead: read means answered AND parsed; a JSON null is a reading', () => {
  const wasRead = shippedWasRead();
  assert.strictEqual(wasRead({ ok: true, status: 200, data: { a: 1 }, unreadable: false }), true);
  assert.strictEqual(wasRead({ ok: true, status: 200, data: null, unreadable: false }), true,
    'a clean parse of the JSON literal null is a reading');
  assert.strictEqual(wasRead({ ok: true, status: 200, data: null, unreadable: true }), false,
    'an unparseable 2xx is exactly the case this exists for');
  assert.strictEqual(wasRead({ ok: false, status: 500, data: null, unreadable: false }), false);
  assert.strictEqual(wasRead({ ok: false, status: 404, data: null, unreadable: true }), false);
  assert.strictEqual(wasRead(null), false, 'a caught fetch failure');
  assert.strictEqual(wasRead(undefined), false);
  assert.match(APP, /mustRead, wasRead, connectStream/, 'wasRead is not exported on RC');
});

test('the venue picker: an unread connection list is a fourth state, not "nothing connected"', () => {
  const status = { venues: ['bitget', 'bybit'], venues_pending: null, venues_mode: 'multi' };
  const unread = Picker.pickerState(status, null);
  assert.deepStrictEqual(unread.rows, [
    { venue: 'bitget', checked: true, disconnected: false, unknown: true },
    { venue: 'bybit', checked: true, disconnected: false, unknown: true },
  ], 'the ticks are real (the selection); the connection is simply not known');
  assert.strictEqual(unread.notice.tone, 'unknown');
  assert.match(unread.notice.text, /could not be read/);
  assert.strictEqual(unread.canSave, false, 'nothing can be saved against a list nobody read');
  // `[]` is still a READING — nothing connected — and keeps its old words.
  const none = Picker.pickerState(status, []);
  assert.deepStrictEqual(none.rows.map((r) => [r.venue, r.disconnected, r.unknown]),
    [['bitget', true, false], ['bybit', true, false]]);
  assert.notStrictEqual(none.notice && none.notice.tone, 'unknown');
  assert.strictEqual(none.canSave, false);
  // and a real connection is unchanged
  const live = Picker.pickerState(status, [{ venue: 'bitget', connected: true }]);
  assert.deepStrictEqual(live.rows[0], { venue: 'bitget', checked: true, disconnected: false, unknown: false });
  assert.strictEqual(live.canSave, true);
});

// ── the wiring: each omit site asks the one reading ─────────────────────────

const SITES = [
  ['the insight-bridge probe counts an HTML 200 as up', 'cache.insightOk = wasRead(r);', '!!(r && r.ok)'],
  ['the alerts list says "no alerts" over an unread body', "fetchJSON('/api/alerts').catch(() => null);\n    if (!wasRead(r)) {", null],
  ['mission control drops the ⚠️ chips when positions were not read', "const posRead = wasRead(posR);", null],
  ['mission control says positions were unread', "'⚠️ Positions', '<b>unread</b>'", null],
  ['the letter archive renders an unread list as no past letters', 'const weeks = wasRead(arc) ? (arc.data?.letters || []) : null;', null],
  ['the watchlist strip says "star symbols" over an unread list', '_watchUnread = !wasRead(r);', null],
  ['…and names it in the user\'s language', "T('dd.w_unread'", null],
  ['the pattern read says "no chart patterns" over an unread body', 'const pd = wasRead(pat) && pat.data;', 'pat && pat.ok && pat.data'],
  ['…with its own sentence', "could not be loaded just now", null],
  ['the insight block says "no directional read" over an HTML 200', "if (!wasRead(res)) {\n      return '<p class=\"muted small\">The analysis bridge answered, but its reply '", null],
  ['the watch toggle threw on r.data.watching for an unparseable 2xx', 'if (wasRead(r) && r.data) {\n          await getWatchlist(true);', null],
  ['the scan probe counts an HTML 200 as a scan', 'cache.scanOk = wasRead(r);', '!!(r && r.ok)'],
  ['the pinned-strategy bar renders an unparseable 2xx as nothing pinned', "fetchJSON('/api/bot-strategy', { timeoutMs: 12000 });\n        readOk = wasRead(r);", '!!(r && r.ok)'],
  ['the strategies list renders an unparseable 2xx as an empty list', "fetchJSON('/api/strategies', { timeoutMs: 12000 });\n        readOk = wasRead(r);", '!!(r && r.ok)'],
  ['two unchecked push toggles were a claim about settings', 'if (!wasRead(prof)) {', null],
  ['the venue picker got [] for an unread credentials status', 'venuePickerHtml(c, wasRead(cs) ? (cs.data?.venues || []) : null)', 'venuePickerHtml(c, (cs && cs.ok'],
  ['…and renders the fourth state', "r.unknown ? ' <span class=\"muted small\">— connection unread</span>' : ''", null],
  ['the hub\'s push line said "not configured" over an unread key', 'if (!wasRead(k)) {', null],
];

test('every omit site asks wasRead, and the old shape is gone from each', () => {
  for (const [what, must, mustNot] of SITES) {
    const at = DASH.indexOf(must);
    assert.ok(at >= 0, `${what}: missing \`${must.slice(0, 60)}\``);
    // The old shape must be gone FROM THIS SITE — the same expression can be
    // an honest read elsewhere (`!!(r && r.ok)` guards a POST's outcome), so
    // the check is local to the loader, not global to the file.
    if (mustNot) {
      const near = DASH.slice(Math.max(0, at - 800), at + 800);
      assert.ok(!near.includes(mustNot), `${what}: the old shape \`${mustNot}\` is back beside it`);
    }
  }
  const calls = (DASH.match(/\bwasRead\(/g) || []).length;
  assert.ok(calls >= 16, `expected the omit reading at 16+ sites, found ${calls}`);
  assert.match(DASH, /mustRead, wasRead, connectStream \} = RC;/, 'dashboard.js does not take wasRead from RC');
});

test('the watchlist sentence is in all fourteen languages', () => {
  const e = i18n.STRINGS['dd.w_unread'];
  assert.ok(e, 'dd.w_unread missing from the dictionary');
  for (const l of i18n.LANGS) assert.ok(String(e[l.code] || '').trim().length, `dd.w_unread is missing ${l.code}`);
});
