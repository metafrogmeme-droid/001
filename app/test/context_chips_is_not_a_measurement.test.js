'use strict';
/**
 * The context chip row: five subjects off ONE scan read — the age of the
 * bot's last push, the venue, the BTC regime, the macro calendar, the entry
 * gate — each three-valued, rendered for every visitor on the home view.
 *
 * DRIVEN, not scanned. The defects this row exists to avoid are wording and
 * reachability: a default NEUTRAL printed as a measured regime, an empty
 * calendar's NORMAL printed as an all-clear, a gate nobody fully read painted
 * green, an unparseable stamp falling through to "ENGINE OFFLINE", and a
 * loader placed inside the signed-in block under an ungated shell. The model
 * is driven directly; the renderers are sliced from dashboard.js between two
 * sentinel comments and RUN in a VM; the loader and the shared read's
 * bookkeeping are sliced and driven the same way; the signed-out render is
 * the views smoke's job (a scan cannot see reachability).
 */
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { codeOnly } = require('./helpers/code_only.js');
const { blockBetween } = require('./helpers/block.js');
const { loaderBodies } = require('./helpers/loaders.js');

const APP = path.join(__dirname, '..');
const M = require('../public/js/context-chips-model.js');
const ES = require('../public/js/engine-status-model.js');
const i18n = require('../public/js/i18n');
const RAW = fs.readFileSync(path.join(APP, 'public', 'js', 'dashboard.js'), 'utf8');
const SRC = codeOnly(RAW);
const APPJS = fs.readFileSync(path.join(APP, 'public', 'js', 'app.js'), 'utf8');
const CSS = fs.readFileSync(path.join(APP, 'public', 'styles.css'), 'utf8');
const HTML = fs.readFileSync(path.join(APP, 'public', 'dashboard.html'), 'utf8');
const LANGS = ['en', 'hi', 'it', 'es', 'zh', 'pt', 'fr', 'ar', 'de', 'nl', 'ja', 'ko', 'ru', 'tr'];

const NOW = Date.parse('2026-09-14T12:00:00Z');
const run = (scan) => M.contextChips(scan, NOW, ES.engineChipState);
const chip = (out, subject) => out.chips.find((c) => c.subject === subject) || null;
const unread = (out, subject) => out.unread.find((u) => u.subject === subject) || null;
const full = () => ({
  received_at: '2026-09-14T11:58:00Z',
  regime: { label: 'BULLISH', score: 0.4, gate: 60000 },
  features: { venue: { id: 'bitget', name: 'Bitget' } },
  macro: { state: 'NORMAL', stale: false, unreadable: false, has_events: true },
  circuit_breaker: { gate: { blocked: false, unknown: false, reasons: [] }, live_mode: true },
});
const noColour = (c) => !/chip--(up|down|warn)/.test(c.cls);

// ── the regime ───────────────────────────────────────────────────────────────

test('a default NEUTRAL is not a regime reading; a measured NEUTRAL is', () => {
  // THE assertion the component is judged on: `gate` is the BTC anchor price
  // and the payload's only evidence that BTC was read at all. gate 0 is the
  // constructor's default, printed as a measured regime on the Engine view.
  const dflt = run({ regime: { label: 'NEUTRAL', gate: 0 } });
  assert.equal(chip(dflt, 'regime'), null, 'no chip for the default');
  assert.equal(unread(dflt, 'regime').whyKey, 'dd.ctx_w_regime_default');
  assert.equal(unread(run({ regime: { label: 'BULLISH' } }), 'regime').whyKey, 'dd.ctx_w_regime_default', 'no anchor at all is the default too');
  assert.equal(unread(run({ regime: { label: 'BULLISH', gate: -1 } }), 'regime').whyKey, 'dd.ctx_w_regime_default');
  assert.equal(unread(run({ regime: { label: 'BULLISH', gate: '' } }), 'regime').whyKey, 'dd.ctx_w_regime_default', 'an empty string is not an anchor (Number("") is 0)');
  // And the fix for the default must not be "hide NEUTRAL": a read neutral
  // regime is a measurement.
  const neutral = chip(run({ regime: { label: 'NEUTRAL', gate: 63500 } }), 'regime');
  assert.equal(neutral.vKey, 'dd.ctx_reg_neutral'); assert.equal(neutral.cls, '');
  const bull = chip(run({ regime: { label: 'BULLISH', gate: 63500 } }), 'regime');
  assert.equal(bull.vKey, 'dd.ctx_reg_bull'); assert.equal(bull.cls, 'chip--up');
  const bear = chip(run({ regime: { label: 'bearish', gate: '63500' } }), 'regime');
  assert.equal(bear.vKey, 'dd.ctx_reg_bear'); assert.equal(bear.cls, 'chip--down');
  // Absent, no label, or a label this page does not know: named, each with
  // its own reason, never a chip and never the default sentence.
  assert.equal(unread(run({}), 'regime').whyKey, 'dd.ctx_w_regime');
  assert.equal(unread(run({ regime: { gate: 63500 } }), 'regime').whyKey, 'dd.ctx_w_regime');
  assert.equal(unread(run({ regime: { label: 'SIDEWAYS', gate: 63500 } }), 'regime').whyKey, 'dd.ctx_w_regime_word');
  assert.equal(unread(run({ regime: 'BULLISH' }), 'regime').whyKey, 'dd.ctx_w_regime', 'a string where an object was promised is not a reading');
});

// ── the macro calendar ───────────────────────────────────────────────────────

test('the macro word is a reading only when the calendar says it loaded, and a non-normal state is the calendar’s own', () => {
  const normal = run({ macro: { state: 'NORMAL', stale: false, unreadable: false, has_events: true } });
  const c = chip(normal, 'macro');
  assert.equal(c.vKey, 'dd.ctx_mac_normal'); assert.equal(c.cls, '', 'Normal is bare, never green');
  assert.equal(normal.notes.length, 0, 'a read Normal needs no caveat line');
  // NORMAL is exactly what an EMPTY calendar evaluates to, so without the
  // producer's word that one was loaded it is not a reading.
  assert.equal(unread(run({ macro: { state: 'NORMAL' } }), 'macro').whyKey, 'dd.ctx_w_macro_unstated');
  assert.equal(unread(run({ macro: { state: 'NORMAL', has_events: false } }), 'macro').whyKey, 'dd.ctx_w_macro_empty');
  // An exhausted schedule is the producer's `stale`; a crashed evaluation is
  // `unreadable`, and it outranks stale — a crash says nothing about the
  // schedule.
  assert.equal(unread(run({ macro: { state: 'BLACKOUT', stale: true, has_events: true } }), 'macro').whyKey, 'dd.ctx_w_macro_exhausted');
  assert.equal(chip(run({ macro: { state: 'BLACKOUT', stale: true, has_events: true } }), 'macro'), null, 'an exhausted calendar is not a blackout window');
  assert.equal(unread(run({ macro: { state: 'BLACKOUT', stale: true, unreadable: true } }), 'macro').whyKey, 'dd.ctx_w_macro_failed');
  assert.equal(unread(run({ macro: { state: 'BLACKOUT', unreadable: true, has_events: true } }), 'macro').whyKey, 'dd.ctx_w_macro_failed');
  // A real window: the chip warns, and the visible note says whose reading
  // it is — the calendar's, not the risk gate's.
  const pre = run({ macro: { state: 'PRE_EVENT_CAUTION', stale: false, unreadable: false, has_events: true } });
  const pc = chip(pre, 'macro');
  assert.equal(pc.vKey, 'dd.ctx_mac_pre'); assert.equal(pc.cls, 'chip--warn');
  assert.deepEqual(pre.notes.map((n) => n.key), ['dd.ctx_n_macro']);
  assert.equal(pre.notes[0].vars.stateKey, 'dd.ctx_mac_pre');
  assert.equal(chip(run({ macro: { state: 'POST_EVENT_VOLATILITY', has_events: true } }), 'macro').vKey, 'dd.ctx_mac_post');
  assert.equal(chip(run({ macro: { state: 'BLACKOUT', has_events: true } }), 'macro').vKey, 'dd.ctx_mac_blackout');
  // A state word this page does not know is shown as the producer spelled
  // it, uncoloured by a verdict this page cannot make — warn, since it is
  // not Normal — never mapped onto a known one.
  const odd = chip(run({ macro: { state: 'LOCKDOWN', has_events: true } }), 'macro');
  assert.equal(odd.vKey, null); assert.equal(odd.v, 'Lockdown'); assert.equal(odd.cls, 'chip--warn');
  assert.equal(unread(run({}), 'macro').whyKey, 'dd.ctx_w_macro');
  assert.equal(unread(run({ macro: { stale: false } }), 'macro').whyKey, 'dd.ctx_w_macro', 'no state word is no reading');
  assert.equal(unread(run({ macro: { state: '' } }), 'macro').whyKey, 'dd.ctx_w_macro');
});

// ── the gate ─────────────────────────────────────────────────────────────────

test('the gate has three read answers and three unread ones, and only every-condition-read-clear is green', () => {
  const clear = run({ circuit_breaker: { gate: { blocked: false, unknown: false, reasons: [] } } });
  assert.equal(chip(clear, 'gate').vKey, 'dd.ctx_gate_clear'); assert.equal(chip(clear, 'gate').cls, 'chip--up');
  assert.equal(clear.notes.length, 0);
  // A clear list beside `unknown: true` is consistent with a gate nobody
  // fully read: no colour, and the caveat is a visible line.
  const partial = run({ circuit_breaker: { gate: { blocked: false, unknown: true, reasons: [] } } });
  assert.equal(chip(partial, 'gate').vKey, 'dd.ctx_gate_noblock'); assert.equal(chip(partial, 'gate').cls, '');
  assert.deepEqual(partial.notes.map((n) => n.key), ['dd.ctx_n_gate_partial']);
  assert.ok(partial.chips.every(noColour), 'nothing in that row is green');
  const blocked = run({ circuit_breaker: { gate: { blocked: true, unknown: false, reasons: ['kill switch engaged', 'daily_loss'] } } });
  assert.equal(chip(blocked, 'gate').vKey, 'dd.ctx_gate_blocked'); assert.equal(chip(blocked, 'gate').cls, 'chip--down');
  assert.deepEqual(blocked.notes, [{ key: 'dd.ctx_n_gate_blocked', vars: { reasons: 'kill switch engaged · daily_loss' } }]);
  const bare = run({ circuit_breaker: { gate: { blocked: true, unknown: true, reasons: [] } } });
  assert.equal(chip(bare, 'gate').vKey, 'dd.ctx_gate_blocked', 'blocked outranks unknown — a confirmed blocker beside an unreadable field is still a block');
  assert.deepEqual(bare.notes.map((n) => n.key), ['dd.ctx_n_gate_blocked_nr']);
  assert.deepEqual(run({ circuit_breaker: { gate: { blocked: true, unknown: false, reasons: [3, '', 'x'] } } }).notes[0].vars, { reasons: 'x' });
  // The three unread answers, each with its own sentence.
  assert.equal(unread(run({}), 'gate').whyKey, 'dd.ctx_w_gate');
  assert.equal(unread(run({ circuit_breaker: { rules: [] } }), 'gate').whyKey, 'dd.ctx_w_gate', 'an older bot with rules and no gate block');
  assert.equal(unread(run({ circuit_breaker: { gate: null } }), 'gate').whyKey, 'dd.ctx_w_gate_unasked');
  assert.equal(unread(run({ circuit_breaker: { gate: { blocked: 'no', unknown: false } } }), 'gate').whyKey, 'dd.ctx_w_gate_shape');
  assert.equal(unread(run({ circuit_breaker: { gate: { blocked: false } } }), 'gate').whyKey, 'dd.ctx_w_gate_shape', 'an absent `unknown` is not a false one');
  assert.equal(unread(run({ circuit_breaker: { gate: 'clear' } }), 'gate').whyKey, 'dd.ctx_w_gate_shape');
  for (const s of [run({}), run({ circuit_breaker: { gate: null } }), run({ circuit_breaker: { gate: { blocked: false } } })]) {
    assert.equal(chip(s, 'gate'), null, 'no gate chip for an unread gate');
  }
});

// ── the tick ─────────────────────────────────────────────────────────────────

test('an unparseable stamp is not an offline engine, and the bot’s own spelling parses in every engine', () => {
  const junk = run({ received_at: 'not a date' });
  assert.equal(chip(junk, 'tick'), null);
  assert.equal(unread(junk, 'tick').whyKey, 'dd.ctx_w_tick');
  assert.ok(!junk.chips.some((c) => c.vKey === 'dd.ctx_tick_offline' || /OFFLINE/.test(String(c.v))), 'a bad string is not evidence the engine is down');
  assert.equal(junk.at, null);
  // scan_skill.py stamps `%Y-%m-%d %H:%M UTC`, which is not ISO 8601 and
  // whose Date.parse is implementation-defined: normalised before parsing.
  assert.equal(M.stamp('2026-09-14 11:55 UTC'), '2026-09-14T11:55:00.000Z');
  assert.equal(M.stamp('2026-09-14 11:55:30 UTC'), '2026-09-14T11:55:30.000Z');
  assert.equal(M.stamp('2026-09-14T11:55:00Z'), '2026-09-14T11:55:00.000Z');
  assert.equal(M.stamp(''), null); assert.equal(M.stamp(0), null); assert.equal(M.stamp(null), null); assert.equal(M.stamp('2026-99-99 11:55 UTC'), null);
  const bot = run({ timestamp: '2026-09-14 11:55 UTC' });
  assert.equal(chip(bot, 'tick').vKey, 'dd.ctx_tick_live'); assert.equal(bot.at, '2026-09-14T11:55:00.000Z');
  // The ingest stamp wins over the bot's build time when both are readable;
  // the bot's is the fallback for a payload that has none.
  assert.equal(run({ received_at: '2026-09-14T09:00:00Z', timestamp: '2026-09-14 11:55 UTC' }).at, '2026-09-14T09:00:00.000Z');
  assert.equal(run({ received_at: 'junk', timestamp: '2026-09-14 11:55 UTC' }).at, '2026-09-14T11:55:00.000Z');
  // The three ages, keyed, and the visible note carrying the stamp.
  const stale = run({ received_at: '2026-09-14T11:40:00Z' });
  assert.equal(chip(stale, 'tick').vKey, 'dd.ctx_tick_stale'); assert.equal(chip(stale, 'tick').cls, 'chip--warn');
  const off = run({ received_at: '2026-09-14T09:00:00Z' });
  assert.equal(chip(off, 'tick').vKey, 'dd.ctx_tick_offline'); assert.equal(chip(off, 'tick').cls, 'chip--offline');
  assert.deepEqual(off.notes, [{ key: 'dd.ctx_n_tick', vars: { at: '2026-09-14T09:00:00.000Z' } }]);
});

test('the tick’s verdict is the topbar’s, not a second copy of its thresholds', () => {
  // Drive both at the boundaries: the row's class is the chip's class.
  for (const secs of [0, 899, 900, 901, 1799, 1800, 1801, 7200]) {
    const at = new Date(NOW - secs * 1000).toISOString();
    const own = ES.engineChipState(at, true, NOW);
    assert.equal(chip(run({ received_at: at }), 'tick').cls, own.cls, `${secs}s`);
  }
  // A class the row's map does not know keeps the chip's own words and no
  // colour rather than guessing a state; a missing verdict is a render that
  // cannot be done, and the model says so loudly.
  const odd = M.contextChips({ received_at: '2026-09-14T11:58:00Z' }, NOW, () => ({ text: '● SOMETHING', cls: 'chip--new' }));
  assert.equal(chip(odd, 'tick').vKey, null); assert.equal(chip(odd, 'tick').v, '● SOMETHING'); assert.equal(chip(odd, 'tick').cls, '');
  assert.throws(() => M.contextChips(full(), NOW, null), TypeError);
  assert.throws(() => M.contextChips(full(), NOW, undefined), TypeError);
  assert.throws(() => M.contextChips({}, NOW, null), TypeError, 'even with nothing to judge: the row cannot be rendered without its verdict');
});

// ── the venue ────────────────────────────────────────────────────────────────

test('the venue is a reading with no colour, omitted on a paper bot, and named on a live one', () => {
  const v = chip(run(full()), 'venue');
  assert.equal(v.v, 'BITGET'); assert.equal(v.vKey, null); assert.equal(v.cls, '');
  assert.equal(chip(run({ features: { venue: { id: 'hyperliquid' } } }), 'venue').v, 'HYPERLIQUID', 'the id when there is no display name');
  // Absent by CONFIGURATION: a bot that says live_mode false has no live
  // executor to be bound to a venue. Naming that on every scan forever
  // trains the reader to stop reading the list.
  const paper = run({ circuit_breaker: { live_mode: false } });
  assert.equal(chip(paper, 'venue'), null); assert.equal(unread(paper, 'venue'), null);
  assert.deepEqual(paper.omitted, [{ subject: 'venue', why: 'paper' }]);
  // Absent beside a live bot, or beside no word about the mode, is not
  // reported — and says so.
  assert.equal(unread(run({ circuit_breaker: { live_mode: true } }), 'venue').whyKey, 'dd.ctx_w_venue');
  assert.equal(unread(run({}), 'venue').whyKey, 'dd.ctx_w_venue');
  assert.equal(unread(run({ circuit_breaker: { live_mode: 'false' } }), 'venue').whyKey, 'dd.ctx_w_venue', 'a string is not the read fact');
  assert.equal(unread(run({ features: { venue: {} } }), 'venue').whyKey, 'dd.ctx_w_venue');
  assert.equal(unread(run({ features: { venue: { name: '  ' } } }), 'venue').whyKey, 'dd.ctx_w_venue');
});

// ── the row as a whole ───────────────────────────────────────────────────────

test('nothing read is one honest chip that names all five, not five silent ones — and no scan is no row', () => {
  const none = run({});
  assert.equal(none.chips.length, 1);
  assert.equal(none.chips[0].subject, 'unread'); assert.equal(none.chips[0].cls, 'chip--offline');
  assert.deepEqual(none.unread.map((u) => u.subject), ['tick', 'venue', 'regime', 'macro', 'gate']);
  assert.deepEqual(none.unread.map((u) => u.nameKey), ['dd.ctx_u_tick', 'dd.ctx_u_venue', 'dd.ctx_u_regime', 'dd.ctx_u_macro', 'dd.ctx_u_gate']);
  assert.deepEqual(none.notes, []); assert.deepEqual(none.omitted, []); assert.equal(none.at, null);
  assert.ok(!/LIVE|NORMAL|BULLISH|CLEAR|no block/i.test(JSON.stringify(none.chips)), 'nothing in the output claims a reading');
  for (const scan of [null, undefined, 'x', 7, [], true]) assert.equal(M.contextChips(scan, NOW, ES.engineChipState), null, `no scan: ${String(scan)}`);
});

test('everything read is five chips in a fixed order, nothing named, and the fixture the smoke serves is that payload', () => {
  const all = run(full());
  assert.deepEqual(all.chips.map((c) => c.subject), ['tick', 'venue', 'regime', 'macro', 'gate']);
  assert.deepEqual(all.unread, []); assert.deepEqual(all.omitted, []);
  assert.deepEqual(all.notes.map((n) => n.key), ['dd.ctx_n_tick']);
  // A subject can never be both a chip and named: every payload in a small
  // corpus keeps the two sets disjoint and the order fixed.
  const corpus = [full(), {}, { regime: { label: 'NEUTRAL', gate: 0 }, macro: { state: 'BLACKOUT', stale: true }, circuit_breaker: { gate: null, live_mode: false } },
    { received_at: 'x', features: { venue: { id: 'v' } }, circuit_breaker: { gate: { blocked: true, unknown: true, reasons: ['r'] } } }];
  const order = ['tick', 'venue', 'regime', 'macro', 'gate'];
  for (const scan of corpus) {
    const out = run(scan);
    const shown = out.chips.filter((c) => c.subject !== 'unread').map((c) => c.subject);
    const named = out.unread.map((u) => u.subject);
    assert.deepEqual(shown, order.filter((s) => shown.includes(s)), 'chips in the fixed order');
    assert.ok(shown.every((s) => !named.includes(s)), 'no subject is both shown and named');
    assert.equal(out.chips.some((c) => c.subject === 'unread'), named.length > 0, 'the NOT REPORTED chip exists exactly when something is named');
  }
  // The browser smoke's fixture renders the populated row, so the DOM test
  // is a test of five chips and not of an empty state.
  const FX = require('./fixtures/dashboard_smoke_fixtures.js');
  const row = FX.find(([prefix]) => prefix === '/api/bot/sync/scan');
  assert.ok(row, 'the smoke serves /api/bot/sync/scan');
  const fx = M.contextChips(row[1].scan, Date.now(), ES.engineChipState);
  assert.deepEqual(fx.chips.map((c) => c.subject), ['tick', 'venue', 'regime', 'macro', 'gate']);
  assert.equal(fx.chips[0].vKey, 'dd.ctx_tick_live');
});

// ── words: every key declared, listed as a literal, translated ───────────────

test('every key the model can emit is declared, listed in the renderer as a literal, and present in fourteen languages with its slots', () => {
  assert.deepEqual(M.KEYS, Object.keys(M.W));
  const renderer = blockBetween(RAW, '// ── context chips: renderers ──', '// ── context chips: renderers end ──', { label: 'context chip renderers' });
  const literal = [...renderer.matchAll(/T\('(dd\.ctx_[\w]+)'/g)].map((m) => m[1]);
  for (const k of M.KEYS) assert.ok(literal.includes(k), `${k} is not a literal T() call in the renderer's map`);
  for (const k of literal) assert.ok(M.KEYS.includes(k), `${k} is in the renderer's map and the model cannot emit it`);
  for (const k of [...M.KEYS, 'dd.e_ctx', 'dp.ctx', 'aria.ctxrow']) {
    const entry = i18n.STRINGS[k];
    assert.ok(entry, `${k} missing from the dictionary`);
    for (const lang of LANGS) {
      assert.ok(typeof entry[lang] === 'string' && entry[lang].trim(), `${k} has no ${lang}`);
      for (const slot of entry.en.match(/\{\w+\}/g) || []) assert.ok(entry[lang].includes(slot), `${k}: ${lang} lost the ${slot} slot`);
    }
  }
  // Driven: every key the model emits over a corpus is one it declares.
  const corpus = [full(), {}, { regime: { label: 'NEUTRAL', gate: 0 }, macro: { state: 'LOCKDOWN', has_events: true }, circuit_breaker: { gate: { blocked: true, unknown: false, reasons: [] }, live_mode: false } },
    { received_at: 'x', timestamp: '2026-09-14 09:00 UTC', macro: { state: 'NORMAL', unreadable: true }, circuit_breaker: { gate: { blocked: false, unknown: true, reasons: [] } } },
    { macro: { state: 'NORMAL', has_events: false }, circuit_breaker: { gate: 'x' } }, { regime: { label: 'ODD', gate: 1 }, macro: { state: 'NORMAL', stale: true } }];
  const seen = new Set();
  for (const scan of corpus) {
    const out = run(scan);
    for (const c of out.chips) { seen.add(c.kKey); if (c.vKey) seen.add(c.vKey); }
    for (const u of out.unread) { seen.add(u.nameKey); seen.add(u.whyKey); }
    for (const n of out.notes) { seen.add(n.key); if (n.vars.stateKey) seen.add(n.vars.stateKey); }
  }
  for (const k of seen) assert.ok(M.KEYS.includes(k), `${k} was emitted and is not declared`);
  assert.ok(seen.size >= 30, `the corpus reaches most of the vocabulary (${seen.size})`);
});

// ── the renderers, sliced and run ────────────────────────────────────────────

function renderers() {
  const block = blockBetween(RAW, '// ── context chips: renderers ──', '// ── context chips: renderers end ──', { label: 'context chip renderers' });
  const escFn = APPJS.slice(APPJS.indexOf('function esc('), APPJS.indexOf('\n  }\n', APPJS.indexOf('function esc(')) + 4);
  assert.ok(/function esc\(/.test(escFn), 'esc sliced from app.js');
  const ctx = { T: (k) => '[' + k + ']', fmtAgo: (iso) => 'ago(' + iso + ')' };
  vm.createContext(ctx);
  vm.runInContext(escFn + '\n' + block + '\nthis.contextRowHtml = contextRowHtml; this.ctxWords = ctxWords;', ctx);
  return ctx;
}

test('the row renders every chip with its key and its word, the caveats as visible lines, and no title attribute', () => {
  const R = renderers();
  const html = R.contextRowHtml(run(full()), R.ctxWords());
  const keys = [...html.matchAll(/class="ctx-k">([^<]*)</g)].map((m) => m[1]);
  assert.deepEqual(keys, ['[dd.ctx_tick]', '[dd.ctx_venue]', '[dd.ctx_regime]', '[dd.ctx_macro]', '[dd.ctx_gate]']);
  const vals = [...html.matchAll(/class="ctx-v">([^<]*)</g)].map((m) => m[1]);
  assert.deepEqual(vals, ['[dd.ctx_tick_live]', 'BITGET', '[dd.ctx_reg_bull]', '[dd.ctx_mac_normal]', '[dd.ctx_gate_clear]']);
  assert.ok(html.includes('class="chip chip--up"><span class="ctx-k">[dd.ctx_gate]'), 'the clear gate is green');
  assert.ok(html.includes('class="chip "><span class="ctx-k">[dd.ctx_venue]'), 'the venue chip carries no colour');
  assert.ok(html.includes('<p class="ctx-note">[dd.ctx_n_tick]</p>'), 'the tick note is keyed');
  const filled = R.contextRowHtml(run(full()), Object.assign({}, R.ctxWords(), { 'dd.ctx_n_tick': 'Pushed {when}.' }));
  assert.ok(filled.includes('<p class="ctx-note">Pushed ago(2026-09-14T11:58:00.000Z).</p>'), 'the stamp is rendered as an age by the renderer, not the model');
  assert.ok(!/title=/.test(html), 'no caveat hides in a title attribute');
  assert.ok(html.includes('role="group" aria-label="[aria.ctxrow]"'));
});

test('the NOT REPORTED chip lists its subjects and the notes give each its reason; reasons and producer text are escaped', () => {
  const R = renderers();
  const html = R.contextRowHtml(run({}), R.ctxWords());
  assert.equal((html.match(/class="chip /g) || []).length, 1);
  assert.ok(html.includes('class="chip chip--offline"><span class="ctx-k">[dd.ctx_unread]</span><span class="ctx-v">[dd.ctx_u_tick] · [dd.ctx_u_venue] · [dd.ctx_u_regime] · [dd.ctx_u_macro] · [dd.ctx_u_gate]</span>'));
  const notes = [...html.matchAll(/<p class="ctx-note">([^<]*)<\/p>/g)].map((m) => m[1]);
  assert.deepEqual(notes, ['[dd.ctx_u_tick]: [dd.ctx_w_tick]', '[dd.ctx_u_venue]: [dd.ctx_w_venue]', '[dd.ctx_u_regime]: [dd.ctx_w_regime]', '[dd.ctx_u_macro]: [dd.ctx_w_macro]', '[dd.ctx_u_gate]: [dd.ctx_w_gate]']);
  const blocked = R.contextRowHtml(run({ circuit_breaker: { gate: { blocked: true, unknown: false, reasons: ['<b>kill</b> switch'] }, live_mode: false } }),
    Object.assign({}, R.ctxWords(), { 'dd.ctx_n_gate_blocked': 'Blocked: {reasons}.' }));
  assert.ok(blocked.includes('<p class="ctx-note">Blocked: &lt;b&gt;kill&lt;/b&gt; switch.</p>'), 'a reason is escaped');
  assert.ok(!blocked.includes('<b>kill'));
  assert.ok(blocked.includes('class="chip chip--down"><span class="ctx-k">[dd.ctx_gate]</span><span class="ctx-v">[dd.ctx_gate_blocked]</span>'));
  assert.ok(!blocked.includes('[dd.ctx_u_venue]'), 'a paper bot’s missing venue is omitted, not named');
  const pre = R.contextRowHtml(run({ macro: { state: 'PRE_EVENT_CAUTION', has_events: true } }), R.ctxWords());
  assert.ok(pre.includes('<p class="ctx-note">[dd.ctx_n_macro]</p>'), 'the macro note is keyed; its {state} slot resolves through the map');
  const odd = R.contextRowHtml(run({ macro: { state: 'LOCK<DOWN', has_events: true } }), R.ctxWords());
  assert.ok(odd.includes('Lock&lt;down'), 'a producer state word is escaped and shown as spelled');
  const ven = R.contextRowHtml(run({ features: { venue: { name: 'X<Y' } } }), R.ctxWords());
  assert.ok(ven.includes('X&lt;Y'));
});

test('the map resolves the macro state slot through the dictionary and falls back to the producer word', () => {
  const R = renderers();
  const W = R.ctxWords();
  // With a real dictionary word the slot carries it; the test stub returns
  // keys, so drive the fill with a map that holds words.
  const words = Object.assign({}, W, { 'dd.ctx_n_macro': 'Calendar: {state}.', 'dd.ctx_mac_pre': 'Pre-event' });
  const html = R.contextRowHtml(run({ macro: { state: 'PRE_EVENT_CAUTION', has_events: true } }), words);
  assert.ok(html.includes('<p class="ctx-note">Calendar: Pre-event.</p>'));
  const raw = R.contextRowHtml(run({ macro: { state: 'LOCKDOWN', has_events: true } }), words);
  assert.ok(raw.includes('<p class="ctx-note">Calendar: Lockdown.</p>'));
});

// ── the shared read's bookkeeping and the loader, sliced and driven ──────────

function sliceFn(src, head) {
  const i = src.indexOf(head);
  assert.ok(i > -1, `${head} not found`);
  const j = src.indexOf('\n  }\n', i);
  return src.slice(i, j + 4);
}

test('adoptScanRead records the tri-state, caches only a read scan, and tells the topbar every time', () => {
  const fn = sliceFn(RAW, 'function adoptScanRead(r)');
  const ctx = { cache: { scan: null, scanAt: 0, scanOk: null }, chip: 0, Date, wasRead: (r) => !!(r && r.ok && !r.unreadable) };
  ctx.updateConnChip = () => { ctx.chip += 1; };
  vm.createContext(ctx);
  vm.runInContext(fn + '\nthis.adopt = adoptScanRead;', ctx);
  ctx.adopt({ ok: false, status: 0, data: null });
  assert.equal(ctx.cache.scanOk, false); assert.equal(ctx.cache.scan, null); assert.equal(ctx.chip, 1, 'a failed read tells the topbar');
  ctx.adopt({ ok: true, status: 200, data: { scan: { received_at: 'x' } } });
  assert.equal(ctx.cache.scanOk, true); assert.deepEqual(ctx.cache.scan, { received_at: 'x' }); assert.ok(ctx.cache.scanAt > 0); assert.equal(ctx.chip, 2);
  ctx.adopt({ ok: true, status: 200, data: null, unreadable: true });
  assert.equal(ctx.cache.scanOk, false, 'an unparseable 2xx is a failed read'); assert.deepEqual(ctx.cache.scan, { received_at: 'x' }, 'stale beats blank in the cache');
  ctx.adopt({ ok: true, status: 200, data: { scan: null } });
  assert.equal(ctx.cache.scanOk, true, 'a 200 with no scan is a read'); assert.deepEqual(ctx.cache.scan, { received_at: 'x' });
});

function loader() {
  const p = loaderBodies(SRC).find((x) => x.target === "C('ctx')");
  assert.ok(p, 'the context row loader exists');
  return p;
}

test('the loader adopts the outcome before it guards, guards this read, and empties only on a read that carried no scan', async () => {
  const p = loader();
  const mustReadFn = APPJS.slice(APPJS.indexOf('function mustRead(r)'), APPJS.indexOf('\n  }\n', APPJS.indexOf('function mustRead(r)')) + 4);
  const drive = async (envelope, { model = M, es = ES.engineChipState } = {}) => {
    const log = [];
    const ctx = {
      self: { ContextChipsModel: model, EngineStatusModel: { engineChipState: es } },
      scanRead: Promise.resolve(envelope), Date, window: {},
      adoptScanRead: (r) => { log.push('adopt'); return r; },
      contextRowHtml: (out) => { log.push('render'); return '<row>' + out.chips.length + '</row>'; },
      ctxWords: () => ({}), T: (k) => k, C: (x) => x,
      renderPanel: (el, fn, opts) => ({ el, fn, opts }),
    };
    vm.createContext(ctx);
    const call = vm.runInContext(mustReadFn + '\nthis.PanelErrorModel = { codeOf: () => "" };\n' + p.body, ctx);
    let out, err = null;
    try { out = await call.fn(); } catch (e) { err = e; }
    return { out, err, log, opts: call.opts };
  };
  const refused = await drive({ ok: false, status: 503, data: { error: 'gateway_down' } });
  assert.ok(refused.err, 'a refused read throws');
  assert.equal(refused.err.status, 503);
  assert.deepEqual(refused.log, ['adopt'], 'the topbar is told BEFORE the throw, and nothing renders');
  const dead = await drive({ ok: false, status: 0, data: null });
  assert.ok(dead.err); assert.deepEqual(dead.log, ['adopt']);
  const empty = await drive({ ok: true, status: 200, data: { scan: null, message: 'No scan data yet.' } });
  assert.equal(empty.err, null); assert.equal(empty.out, null, 'a read with no scan is the empty state'); assert.deepEqual(empty.log, ['adopt']);
  const read = await drive({ ok: true, status: 200, data: { scan: full() } });
  assert.equal(read.err, null); assert.equal(read.out, '<row>5</row>'); assert.deepEqual(read.log, ['adopt', 'render']);
  const unparsed = await drive({ ok: true, status: 200, data: null, unreadable: true });
  assert.ok(unparsed.err, 'a 2xx whose body did not parse is an error, not an empty row');
  const noModel = await drive({ ok: true, status: 200, data: { scan: full() } }, { model: null });
  assert.ok(noModel.err); assert.deepEqual(noModel.log, [], 'a missing model throws before the read is touched');
  const noVerdict = await drive({ ok: true, status: 200, data: { scan: full() } }, { es: null });
  assert.ok(noVerdict.err);
  assert.equal(read.opts.timeoutMs, 12000);
});

// ── wiring ───────────────────────────────────────────────────────────────────

function renderHomeBody() {
  const i = SRC.indexOf('async function renderHome()');
  assert.ok(i > -1);
  let depth = 0, j = SRC.indexOf('{', i);
  for (; j < SRC.length; j++) { if (SRC[j] === '{') depth++; else if (SRC[j] === '}') { depth--; if (depth === 0) break; } }
  return SRC.slice(i, j + 1);
}

test('one /scan read per home render, shared by the command bar and the row, with the row’s budget above the read’s', () => {
  const home = renderHomeBody();
  const reads = [...home.matchAll(/fetchJSON\('\/api\/bot\/sync\/scan'[^)]*\)/g)].map((m) => m[0]);
  assert.equal(reads.length, 1, `renderHome fetches /scan once, saw ${reads.length}`);
  assert.match(reads[0], /timeoutMs: 10000/);
  assert.ok(/const scanRead = fetchJSON\('\/api\/bot\/sync\/scan'/.test(home), 'the read is the shared scanRead');
  const bare = home.split('\n').filter((l) => /\bgetScan\(\)/.test(l));
  assert.ok(bare.length === 1 && /every\(60000/.test(bare[0]), `the only bare getScan() in renderHome is the minute refresh, saw ${JSON.stringify(bare)}`);
  assert.ok(/getScan\(45000, scanRead\)/.test(home), 'the command bar consumes the shared read');
  const p = loader();
  assert.match(p.body, /await scanRead/); assert.match(p.body, /adoptScanRead\(r\)/); assert.match(p.body, /mustRead\(r\)/);
  assert.ok(!/fetchJSON\(/.test(p.body), 'the loader fetches nothing of its own');
  assert.ok(!/getScan\(/.test(p.body), 'and is not built on the swallowing cache');
  assert.match(p.body, /timeoutMs: 12000/);
  assert.match(p.body, /throw new Error\('context chip model unavailable'\)/);
});

test('the shell is ungated and the loader sits below the signed-in block, right above the mind-stream', () => {
  const home = renderHomeBody();
  const shell = home.split('\n').find((l) => l.includes('id="p-ctx"'));
  assert.ok(shell, 'the shell exists in the home template');
  assert.ok(!/LOGGED_IN/.test(shell), 'the shell is not gated');
  const ids = [...home.matchAll(/id="(p-[a-z]+)"/g)].map((m) => m[1]);
  assert.deepEqual(ids.slice(ids.indexOf('p-cmd'), ids.indexOf('p-cmd') + 3), ['p-cmd', 'p-ctx', 'p-next'], 'under the command bar, above the checklist');
  // The loader: after the signed-in block closes, before the mind-stream's.
  const blockOpen = home.indexOf('if (LOGGED_IN) {', home.indexOf("renderPanel(C('hero')"));
  let depth = 0, j = home.indexOf('{', blockOpen);
  for (; j < home.length; j++) { if (home[j] === '{') depth++; else if (home[j] === '}') { depth--; if (depth === 0) break; } }
  const ctxAt = home.indexOf("renderPanel(C('ctx')");
  const mindAt = home.indexOf("renderPanel(C('mind')");
  assert.ok(ctxAt > j, 'the loader is outside the signed-in block');
  assert.ok(ctxAt < mindAt, 'and precedes the mind-stream loader');
  // The read for it is started for every visitor, not only signed-in ones.
  const readLine = home.split('\n').find((l) => l.includes("const scanRead = fetchJSON('/api/bot/sync/scan'"));
  assert.ok(readLine && !/LOGGED_IN/.test(readLine), 'the read is unconditional');
});

test('getScan takes a read in flight and records it through the one seam', () => {
  const fn = sliceFn(SRC, 'async function getScan(');
  assert.match(fn, /async function getScan\(maxAgeMs = 45000, read = null\)/);
  assert.match(fn, /if \(!read && cache\.scan/, 'a supplied read is always consumed');
  assert.match(fn, /await \(read \|\| fetchJSON\('\/api\/bot\/sync\/scan'\)\)/);
  assert.match(fn, /adoptScanRead\(r\)/);
  assert.ok(!/cache\.scanOk =/.test(fn), 'the bookkeeping lives in adoptScanRead alone');
  assert.equal((SRC.match(/cache\.scanOk = /g) || []).length, 1, 'one writer of the tri-state');
});

test('the model loads before dashboard.js, the styles let the not-reported chip wrap, and no colour is invented', () => {
  const model = HTML.indexOf('/js/context-chips-model.js?v=');
  const dash = HTML.indexOf('/js/dashboard.js?v=');
  assert.ok(model > -1 && model < dash, 'context-chips-model.js is a script tag before dashboard.js');
  const css = blockBetween(CSS, '/* ---- Context chip row (Home', '.ctx-note {', { pad: 200, label: 'ctx css' });
  assert.match(css, /\.ctxrow \{[^}]*flex-wrap: wrap/);
  assert.match(css, /\.ctxrow \.chip--offline \.ctx-v \{[^}]*white-space: normal/);
  assert.match(css, /\.ctxrow \.ctx-v \{[^}]*text-overflow: ellipsis/);
  assert.match(css, /\.ctx-note \{[^}]*var\(--text-3\)/);
  assert.ok(!/#[0-9a-f]{3,8}\b/i.test(css.replace(/\/\*[\s\S]*?\*\//g, '')), 'no literal colour in the appended layer');
  assert.ok(!/(up|down|warn):|--up|--down|--warn/.test(css.replace(/\/\*[\s\S]*?\*\//g, '')), 'the row paints no verdict colour of its own');
  // The renderer assigns a colour class only from the model's class.
  const renderer = codeOnly(blockBetween(RAW, '// ── context chips: renderers ──', '// ── context chips: renderers end ──'));
  assert.ok(!/chip--(up|down|warn)/.test(renderer), 'no colour class is spelled in the renderer');
});
