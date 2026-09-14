'use strict';
/**
 * The status strip: which account is trading, and why the strip says so.
 *
 * It fails if the strip starts asserting a mode it did not read, or starts
 * reporting two different facts with one sentence. It DRIVES the model with
 * the payloads the route REALLY sends — through helpers/portfolio_route.js,
 * against the shipped routes/portfolio.js — and through the real readMode
 * sliced out of dashboard.js, rather than scanning for a literal: a scan
 * cannot see reachability, and this repo records two scans of exactly this
 * kind passing against an inverted branch and an `if False:`.
 *
 * WHAT WAS WRONG BEFORE THE STRIP EXISTED. The home view resolved the mode
 * from up to three separate HTTP responses (the hero's forced fetch, the
 * command bar's cached-or-fetched one, and the topbar chip fed by whichever
 * won), and nothing on the page said WHERE a mode came from or what the
 * figures under it therefore were. A stale operator push read "bot offline"
 * over a healthy bot; a deployment with no bot at all read PAPER in the same
 * amber as a bot that is simulating.
 */
const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { codeOnly } = require('./helpers/code_only.js');
const { get, scanRow, FRESH, OLD } = require('./helpers/portfolio_route');

const APP = path.join(__dirname, '..');
const M = require('../public/js/mode-strip-model.js');
const i18n = require('../public/js/i18n');
const SRC = codeOnly(fs.readFileSync(path.join(APP, 'public', 'js', 'dashboard.js'), 'utf8'));

// The strip's verdict is readMode's, so drive readMode too.
const readMode = (() => {
  const s = SRC.indexOf('  function readMode(pf) {');
  assert.ok(s > 0, 'readMode is still in dashboard.js');
  return vm.runInNewContext(SRC.slice(s, SRC.indexOf('\n  }\n', s) + 4) + '\nreadMode;');
})();
const strip = (pf) => M.modeStrip({ mode: readMode(pf), pf });

// ── the payloads the route really emits ─────────────────────────────────────

const ROUTE = {
  // operator path: the scan cache is the reading
  op_live:        () => get({ scan: scanRow({ live_mode: true, equity: 8300, live_unavailable: false }) }),
  op_live_nobal:  () => get({ scan: scanRow({ live_mode: true, equity: null, live_unavailable: true }), snapAt: OLD }),
  op_paper:       () => get({ scan: scanRow({ live_mode: false }) }),
  op_unread:      () => get({ scan: [] }),                                   // cold start
  op_unread_junk: () => get({ scan: [{ scan_json: '{not json', updated_at: FRESH() }] }),
  op_stale_live:  () => get({ scan: scanRow({ live_mode: true, equity: 8300, live_unavailable: false }, OLD), snapAt: OLD }),
  // per-user path: the gateway is the reading
  user_live:      () => get({ operator: false, gateway: { data: { mode: 'LIVE', equity: 12.5 } } }),
  user_mixed:     () => get({ operator: false, gateway: { data: { mode: 'MIXED', equity: 12.5 } } }),
  user_paper:     () => get({ operator: false, gateway: { data: { mode: 'PAPER', equity: 10000 } } }),
  user_unreached: () => get({ operator: false, gateway: { status: 503 } }),
  user_threw:     () => get({ operator: false, gateway: { throws: true } }),
  user_no_bot:    () => get({ operator: false, gateway: { configured: false } }),
  user_odd_word:  () => get({ operator: false, gateway: { data: { mode: 'SHADOW', equity: 1 } } }),
};

const bodies = {};
test('the route answers every scenario with HTTP 200 — the strip renders in place, never from a throw', async () => {
  for (const [name, call] of Object.entries(ROUTE)) {
    const { status, body } = await call();
    assert.equal(status, 200, `${name} answered ${status}`);
    bodies[name] = body;
  }
});

test('a mode nobody read is never PAPER, and never green or red', () => {
  for (const k of ['op_unread', 'op_unread_junk', 'user_unreached', 'user_threw', 'user_odd_word']) {
    const s = strip(bodies[k]);
    assert.equal(s.kind, 'unreadable', `${k}: ${JSON.stringify(bodies[k])}`);
    assert.equal(s.word, 'MODE ?', `${k} prints the dashboard's one spelling of unknown`);
    assert.equal(s.cls, 'chip--offline', `${k} wears the muted token`);
    assert.equal(s.tone, 'unknown', k);
    assert.notEqual(s.why.key, 'dd.ms_why_paper', k);
  }
  // Stale on a payload that is NOT the operator feed has already lost its mode.
  for (const m of ['PAPER', 'LIVE']) assert.equal(strip({ mode: m, stale: true }).kind, 'unreadable');
  // And a null pf — nothing to read at all — is unknown, not a crash and not paper.
  assert.equal(M.modeStrip({ mode: null, pf: null }).kind, 'unreadable');
  assert.equal(M.modeStrip(null).kind, 'unreadable');
});

test('and it says what the figures below it therefore are', () => {
  // The half a blanked strip would lose: the metric cluster underneath is
  // still on screen, and these numbers came from this site's DB rows.
  for (const k of ['op_unread', 'user_unreached', 'user_threw', 'user_odd_word']) {
    assert.equal(strip(bodies[k]).below.key, 'dd.ms_below_site', k);
    assert.equal(strip(bodies[k]).src.key, 'dd.ms_src_site', k);
  }
  // The two unreadable events keep their own sentence: nobody reached the bot
  // vs the bot answered with a word this dashboard does not know.
  assert.equal(strip(bodies.user_unreached).why.key, 'dd.ms_why_unreach');
  assert.equal(strip(bodies.user_odd_word).why.key, 'dd.ms_why_nomode');
});

test('PAPER from a bot and PAPER from a site with no bot are different facts, in word, sentence AND colour', () => {
  const a = strip(bodies.user_no_bot), b = strip(bodies.user_paper);
  assert.equal(a.word, 'PAPER'); assert.equal(b.word, 'PAPER');
  assert.equal(a.kind, 'absent'); assert.equal(b.kind, 'paper');
  assert.notEqual(a.why.key, b.why.key, 'a configuration is not a measurement');
  assert.notEqual(a.src.key, b.src.key);
  // The first draft of this design painted both amber: the guard asserted
  // word/kind/why/src and never the tone, so the collapse was invisible to
  // the test written against it. Amber claims *simulating*; no bot claims
  // nothing.
  assert.notEqual(a.tone, b.tone, 'absent wears the paper tone');
  assert.notEqual(a.cls, b.cls, 'absent wears the paper chip');
  assert.equal(a.cls, 'chip--offline');
});

test('a read mode says where it was read from', () => {
  assert.equal(strip(bodies.op_live).src.key, 'dd.ms_src_sync');
  assert.equal(strip(bodies.op_paper).src.key, 'dd.ms_src_sync');
  assert.equal(strip(bodies.user_live).src.key, 'dd.ms_src_bot');
  assert.equal(strip(bodies.user_paper).src.key, 'dd.ms_src_bot');
  assert.equal(strip(bodies.op_live).why.key, 'dd.ms_why_live_sync');
  assert.equal(strip(bodies.user_live).why.key, 'dd.ms_why_live');
});

test('MIXED keeps the LIVE verdict and does not call the book below live', () => {
  const m = strip(bodies.user_mixed);
  assert.equal(m.word, 'LIVE', 'the verdict is readMode\'s, not this model\'s');
  assert.equal(m.below.key, 'dd.ms_below_paper', 'the book shown IS the paper one');
  assert.notEqual(m.why.key, strip(bodies.user_live).why.key);
});

test('LIVE with an unreadable balance promises no figure', () => {
  const s = strip(bodies.op_live_nobal);
  assert.equal(s.word, 'LIVE');
  assert.equal(s.why.key, 'dd.ms_why_live_nobal');
  assert.equal(s.below.key, 'dd.ms_below_nobal');
});

test('stale on the operator feed ages the FIGURES, not the mode', () => {
  const op = strip(bodies.op_stale_live);
  assert.equal(bodies.op_stale_live.stale, true, 'the fixture is stale on the wire');
  assert.equal(op.word, 'LIVE', 'source:sync stale means the push is old, not that nobody was asked');
  assert.ok(op.age && op.age.key === 'dd.ms_age_old', 'and it says so');
  assert.equal(strip(bodies.op_live).age, null, 'a fresh feed raises no alarm');
  assert.equal(strip(bodies.user_live).age, null, 'the gateway path never carries a figures-old flag');
});

// THE MUTATION KILLER. Collapsing any two of these onto one sentence is one
// state being reported as another — which is the whole defect, and exactly
// what a "does it render" test cannot see.
test('every distinct state gets a distinct sentence', () => {
  const distinct = ['op_live', 'user_live', 'op_live_nobal', 'user_mixed', 'user_paper',
                    'user_no_bot', 'user_unreached', 'user_odd_word'];
  const keys = distinct.map((k) => strip(bodies[k]).why.key);
  assert.equal(new Set(keys).size, keys.length, 'two states share one why: ' + keys.join(', '));
  // And no sentence the model can emit is unreachable from the wire.
  const emitted = new Set(Object.values(bodies).map((b) => strip(b).why.key));
  const whys = M.KEYS.filter((k) => k.startsWith('dd.ms_why_'));
  assert.deepEqual(whys.filter((k) => !emitted.has(k)), [], 'a sentence no payload produces is a door painted on a wall');
  // And the other direction: every key the model emits is declared in KEYS
  // (the list the translation guard sweeps) — a sentence emitted but not
  // listed would be printed untranslated in thirteen languages unseen.
  for (const [name, b] of Object.entries(bodies)) {
    const s = strip(b);
    for (const part of [s.src, s.why, s.below, s.age].filter(Boolean)) {
      assert.ok(M.KEYS.includes(part.key), `${name} emits ${part.key}, which KEYS does not list`);
      assert.ok(i18n.STRINGS[part.key], `${name} emits ${part.key}, which the dictionary lacks`);
    }
  }
});

// dashboard.js calls T() with a VARIABLE key, so the i18n sweep that greps for
// the literal `T('dd.…')` cannot see one of these. Resolve them here — through
// STRINGS, not translate(): translate() falls back to English for a key that
// exists in one language, so it can only detect a wholly-missing key.
test('every sentence the strip can print exists in all fourteen languages', () => {
  const missing = [];
  for (const key of [...M.KEYS, 'dd.e_mode']) {
    assert.ok(i18n.STRINGS[key], `${key} is in the dictionary`);
    for (const { code } of i18n.LANGS) {
      const v = i18n.STRINGS[key][code];
      if (typeof v !== 'string' || !v.length) missing.push(key + '/' + code);
    }
  }
  assert.deepEqual(missing, [], 'dangling strip keys');
  assert.equal(i18n.LANGS.length, 14);
});

// WIRING. The model can be perfect and never reached — #999's lesson.
test('the strip guards its one source and spells no mode word of its own', () => {
  const at = SRC.indexOf("renderPanel(C('mode')");
  assert.ok(at > 0, 'the mode strip is mounted');
  let depth = 0, i = SRC.indexOf('(', at);
  for (; i < SRC.length; i++) {
    if (SRC[i] === '(') depth++;
    else if (SRC[i] === ')') { depth--; if (depth === 0) break; }
  }
  const body = SRC.slice(at, i + 1);
  assert.match(body, /mustRead\(/, 'single source, so it GUARDS');
  assert.match(body, /readMode\(pf\)/, 'the verdict comes from the one reading');
  assert.match(body, /ModeStripModel/, 'the sentence comes from the model');
  assert.match(body, /await portfolioRead/, 'the strip reads the shared render read, not a fetch of its own');
  assert.ok(!/cache\.portfolio/.test(body), 'a cached payload is a claim about an earlier read');
  assert.ok(!/['"](LIVE|PAPER|MODE \?)['"]/.test(body),
    'no mode word is written in the renderer — a literal here is a second answer');
  assert.ok(!/\.ok\b[\s\S]{0,60}return null;/.test(body),
    'a failed read is never rendered as an empty strip');
});

test('the home view makes ONE portfolio read and the hero and command bar consume it', () => {
  const home = SRC.slice(SRC.indexOf('async function renderHome()'));
  const reads = home.match(/fetchJSON\('\/api\/portfolio'/g) || [];
  assert.equal(reads.length, 1, `renderHome fetches /api/portfolio ${reads.length} times`);
  assert.match(home, /const portfolioRead = LOGGED_IN \? fetchJSON\('\/api\/portfolio'/);
  assert.match(home, /getPortfolio\(true, portfolioRead\)/, 'the hero consumes the shared read');
  assert.match(home, /getPortfolio\(false, portfolioRead\)/, 'the command bar consumes the shared read');
  // getPortfolio itself still fetches when nobody hands it a read.
  assert.match(SRC, /const r = await \(read \|\| fetchJSON\('\/api\/portfolio'/);
});

test('the strip is mounted where the figures it caveats are', () => {
  assert.match(SRC, /id="p-mode"/, 'the panel shell exists');
  assert.ok(SRC.indexOf('id="p-mode"') < SRC.indexOf('id="p-hero"'),
    'the caveat sits above the numbers it is about');
  const html = fs.readFileSync(path.join(APP, 'public', 'dashboard.html'), 'utf8');
  const model = html.indexOf('/js/mode-strip-model.js');
  assert.ok(model > 0 && model < html.indexOf('/js/dashboard.js'), 'the model loads before dashboard.js');
});
