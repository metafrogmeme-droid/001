'use strict';
/**
 * The risk backstop panel on the Engine view: the drawdown the breaker gates
 * on against the threshold that halts it, the slots in use against the
 * binding cap, whether entries are refused, and the override — the card an
 * operator reads to decide how much real money the bot may lose before it
 * halts, and four separate claims about the live gate, each worse wrong than
 * absent.
 *
 * DRIVEN, not scanned. Five states at the top (undated, stale, absent build,
 * engine fault, read) and three per row; a bar only over numbers AND a
 * verdict that were all read; the colour the server's verdict word and never
 * a comparison here; the age read FIRST, so a memory is a memory whatever it
 * holds. The model is driven directly; the renderers are sliced from
 * dashboard.js between two sentinel comments and RUN in a VM; the loader is
 * sliced and driven the same way; the browser render is the views smoke's
 * job (a scan cannot see reachability).
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
const M = require('../public/js/risk-backstop-model.js');
const ES = require('../public/js/engine-status-model.js');
const CX = require('../public/js/context-chips-model.js');
const i18n = require('../public/js/i18n');
const RAW = fs.readFileSync(path.join(APP, 'public', 'js', 'dashboard.js'), 'utf8');
const SRC = codeOnly(RAW);
const MODEL_SRC = fs.readFileSync(path.join(APP, 'public', 'js', 'risk-backstop-model.js'), 'utf8');
const APPJS = fs.readFileSync(path.join(APP, 'public', 'js', 'app.js'), 'utf8');
const CSS = fs.readFileSync(path.join(APP, 'public', 'styles.css'), 'utf8');
const HTML = fs.readFileSync(path.join(APP, 'public', 'dashboard.html'), 'utf8');
const LANGS = ['en', 'hi', 'it', 'es', 'zh', 'pt', 'fr', 'ar', 'de', 'nl', 'ja', 'ko', 'ru', 'tr'];

const MAX = ES.STALE_MAX_S;
const FRESH = 30;
// The block as bot/formatters/risk_backstop.py publishes it, every field read.
const full = () => ({
  drawdown_pct: 3.2, limit_pct: 7.0, source: 'live', verdict: 'Healthy', override_pct: null, default_limit_pct: 7.0, hardening: true,
  slots_used: 2, slots_cap: 5, slots_floor: false, slots_note: '', slots_person: 'unset', gate: { blocked: false, unknown: false, reasons: [] },
});
const read = (b) => M.readBackstop(b, FRESH, MAX);
const dd = (b) => M.drawdownRow(b);

// ── the age is read first ────────────────────────────────────────────────────

test('the age is read first: a stale or undated scan is not a reading whatever it carried, and the floor is the topbar’s', () => {
  for (const b of [full(), { unreadable: true }, undefined, null, 'x']) {
    const stale = M.readBackstop(b, MAX + 1, MAX);
    assert.equal(stale.state, 'stale', `stale: ${JSON.stringify(b)}`); assert.equal(stale.head, 'dd.rb_h_stale'); assert.equal(stale.why, 'dd.rb_stale');
    assert.equal(stale.ageSec, MAX + 1); assert.equal(stale.maxAgeS, MAX);
    assert.ok(!('drawdown' in stale) && !('gate' in stale) && !('slots' in stale), 'no figures ride on a memory');
    for (const age of [null, undefined, NaN, '30', '', Infinity]) {
      const u = M.readBackstop(b, age, MAX);
      assert.equal(u.state, 'undated', `age ${String(age)}`); assert.equal(u.head, 'dd.rb_h_undated'); assert.equal(u.why, 'dd.rb_no_age'); assert.equal(u.ageSec, null);
    }
    assert.equal(M.readBackstop(b, FRESH, null).state, 'undated', 'no floor to judge the age against is undated too');
    assert.equal(M.readBackstop(b, FRESH, NaN).state, 'undated');
    assert.equal(M.readBackstop(b, FRESH, '1800').state, 'undated');
  }
  // At the floor it is still read; one second past it is a memory. A clock a
  // few seconds behind the ingest is a fresh scan, not a stale one.
  assert.equal(read(full()).state, 'read');
  assert.equal(M.readBackstop(full(), 0, MAX).state, 'read');
  assert.equal(M.readBackstop(full(), -5, MAX).state, 'read');
  assert.equal(M.readBackstop(full(), MAX, MAX).state, 'read');
  assert.equal(M.readBackstop(full(), MAX + 1, MAX).state, 'stale');
  assert.equal(MAX, 1800, 'the floor is the topbar’s OFFLINE threshold — one age vocabulary on the page');
});

// ── the two absences at the top ──────────────────────────────────────────────

test('an absent block is a fact about the build and the fault marker is a fact about the engine — two states, two sentences, neither a flat book', () => {
  for (const b of [undefined, null, 'x', 7, [], true, '']) {
    const a = read(b);
    assert.equal(a.state, 'absent', `absent: ${String(b)}`); assert.equal(a.head, 'dd.rb_h_absent'); assert.equal(a.why, 'dd.rb_absent'); assert.equal(a.ageSec, FRESH);
  }
  const u = read({ unreadable: true });
  assert.equal(u.state, 'unreadable'); assert.equal(u.head, 'dd.rb_h_unread'); assert.equal(u.why, 'dd.rb_unread_engine'); assert.equal(u.ageSec, FRESH);
  assert.equal(read(Object.assign(full(), { unreadable: true })).state, 'unreadable', 'the marker beside figures is still the marker');
  // The marker is the boolean, not a word: a string is a shape error and reads
  // as a published block with nothing in it, never as the fault — and an
  // empty block is a published block whose every row says unread.
  for (const b of [{}, { unreadable: 'yes' }, { unreadable: false }, { unreadable: 1 }]) {
    const r = read(b);
    assert.equal(r.state, 'read', JSON.stringify(b));
    assert.equal(r.drawdown.state, 'unread'); assert.equal(r.slots.state, 'unread'); assert.equal(r.gate.state, 'absent');
    assert.equal(r.drawdown.fill, null); assert.equal(r.slots.fill, null); assert.equal(r.drawdown.cls, ''); assert.equal(r.slots.cls, '');
  }
  assert.ok(!/flat|clear|healthy|active/i.test(JSON.stringify([read(undefined), read({ unreadable: true })])), 'neither state claims anything about the book');
});

// ── the drawdown row ─────────────────────────────────────────────────────────

test('the drawdown bar is drawn only over a pair of numbers AND a verdict that were all read, and its colour is the server’s word', () => {
  const ok = dd(full());
  assert.equal(ok.state, 'read'); assert.equal(ok.pct, 3.2); assert.equal(ok.limit, 7); assert.equal(ok.verdict, 'Healthy'); assert.equal(ok.cls, 'rb-up');
  assert.ok(Math.abs(ok.fill - 45.714) < 0.01); assert.equal(ok.why, null); assert.equal(ok.src, 'dd.rb_src_live');
  assert.equal(dd({ drawdown_pct: 5, limit_pct: 7, verdict: 'Warning' }).cls, 'rb-warn');
  assert.equal(dd({ drawdown_pct: 6.9, limit_pct: 7, verdict: 'Critical' }).cls, 'rb-down');
  // THE assertion the study named: two numbers with a verdict word this page
  // does not know would paint a full-length track with an invisible fill —
  // which on this card reads as 0% drawdown, full headroom.
  for (const v of ['Nonsense', 'healthy', 'HEALTHY', 'Healthy ', '', null, undefined, 7, true, { w: 'Healthy' }]) {
    const r = dd({ drawdown_pct: 3, limit_pct: 7, verdict: v });
    assert.equal(r.state, 'read', `verdict ${JSON.stringify(v)}`); assert.equal(r.pct, 3); assert.equal(r.limit, 7, 'the numbers are still reported');
    assert.equal(r.fill, null, `no bar for verdict ${JSON.stringify(v)}`); assert.equal(r.cls, ''); assert.equal(r.why, 'dd.rb_no_verdict');
  }
  assert.equal(dd({ drawdown_pct: 3, limit_pct: 7, verdict: 'Nonsense' }).verdict, 'Nonsense', 'the word is carried, uncoloured, never mapped onto a known one');
  // A measured zero is a bar at zero — a flat equity curve is a measurement —
  // and a breach runs past the track, so the fill is clamped.
  const flat = dd({ drawdown_pct: 0, limit_pct: 7, verdict: 'Healthy' });
  assert.equal(flat.state, 'read'); assert.equal(flat.fill, 0); assert.equal(flat.cls, 'rb-up'); assert.equal(flat.pct, 0);
  assert.equal(dd({ drawdown_pct: 9, limit_pct: 7, verdict: 'Critical' }).fill, 100);
  assert.equal(dd({ drawdown_pct: -1, limit_pct: 7, verdict: 'Healthy' }).fill, 0);
  // Each absence with its own reason and no colour on any of them — INCLUDING
  // when the payload carries the calmest verdict word beside the hole.
  for (const v of [undefined, null, NaN, '', '3.2', false, Infinity]) {
    const r = dd({ drawdown_pct: v, limit_pct: 7, verdict: 'Healthy' });
    assert.equal(r.state, 'partial', `pct ${String(v)}`); assert.equal(r.pct, null); assert.equal(r.limit, 7);
    assert.equal(r.fill, null); assert.equal(r.cls, ''); assert.equal(r.verdict, null, 'a verdict without a number is not a verdict'); assert.equal(r.why, 'dd.rb_no_dd');
  }
  for (const v of [undefined, null, 0, -7, '7', NaN, '', Infinity]) {
    const r = dd({ drawdown_pct: 3.2, limit_pct: v, verdict: 'Healthy' });
    assert.equal(r.state, 'partial', `limit ${String(v)}`); assert.equal(r.pct, 3.2); assert.equal(r.limit, null);
    assert.equal(r.fill, null); assert.equal(r.cls, ''); assert.equal(r.verdict, null); assert.equal(r.why, 'dd.rb_no_limit');
  }
  const none = dd({ verdict: 'Healthy', source: 'live' });
  assert.equal(none.state, 'unread'); assert.equal(none.fill, null); assert.equal(none.cls, ''); assert.equal(none.verdict, null); assert.equal(none.why, 'dd.rb_dd_unread');
  // The source is a word from a closed vocabulary; anything else says so.
  assert.equal(dd({ source: 'paper' }).src, 'dd.rb_src_paper');
  for (const s of [undefined, null, '', 'LIVE', 'exchange', 7]) assert.equal(dd({ source: s }).src, 'dd.rb_src_unknown', `source ${String(s)}`);
});

// ── the slots row ────────────────────────────────────────────────────────────

test('the slots row: a count over the binding cap, a FLOOR that says so and wears warn, and an unread count that is not an empty book', () => {
  assert.deepEqual(M.slotsRow(full()), { state: 'read', used: 2, cap: 5, floor: false, note: null, cls: 'rb-cap', valCls: '', fill: 40, why: null });
  const flat = M.slotsRow({ slots_used: 0, slots_cap: 5 });
  assert.equal(flat.state, 'read'); assert.equal(flat.fill, 0); assert.equal(flat.used, 0, 'a flat book is a measurement');
  const fl = M.slotsRow({ slots_used: 2, slots_cap: 5, slots_floor: true, slots_note: 'could not read hyperliquid' });
  assert.equal(fl.state, 'floor'); assert.equal(fl.floor, true); assert.equal(fl.cls, 'rb-warn'); assert.equal(fl.valCls, 'rb-warn');
  assert.equal(fl.fill, 40); assert.equal(fl.note, 'could not read hyperliquid'); assert.equal(fl.why, null);
  assert.equal(M.slotsRow({ slots_used: 7, slots_cap: 5, slots_floor: true }).fill, 100, 'a floor past the cap is clamped');
  assert.equal(M.slotsRow({ slots_used: 2, slots_cap: 5, slots_floor: 'yes' }).state, 'read', 'a floor is the boolean, not a word');
  assert.equal(M.slotsRow({ slots_used: 2, slots_cap: 5, slots_floor: 1 }).valCls, '');
  for (const v of [undefined, null, NaN, '', '2', true, Infinity]) {
    const r = M.slotsRow({ slots_used: v, slots_cap: 5, slots_floor: true, slots_note: 'n' });
    assert.equal(r.state, 'unread', `used ${String(v)}`); assert.equal(r.used, null); assert.equal(r.cap, 5, 'a read cap is still reported');
    assert.equal(r.fill, null); assert.equal(r.cls, ''); assert.equal(r.valCls, ''); assert.equal(r.why, 'dd.rb_slots_unread');
    assert.equal(r.floor, false, 'an unread count is not a floor either');
  }
  for (const v of [undefined, null, 0, -1, '5', NaN, Infinity]) {
    const r = M.slotsRow({ slots_used: 2, slots_cap: v });
    assert.equal(r.state, 'partial', `cap ${String(v)}`); assert.equal(r.used, 2); assert.equal(r.cap, null);
    assert.equal(r.fill, null); assert.equal(r.cls, ''); assert.equal(r.valCls, ''); assert.equal(r.why, 'dd.rb_cap_unread');
  }
  assert.equal(M.slotsRow({ slots_used: 2, slots_cap: 5, slots_floor: true, slots_note: 7 }).note, null, 'a note is a string or nothing');
  assert.equal(M.slotsRow({ slots_used: 2.4, slots_cap: 5.6 }).used, 2); assert.equal(M.slotsRow({ slots_used: 2.4, slots_cap: 5.6 }).cap, 6);
});

// ── the gate row ─────────────────────────────────────────────────────────────

test('the gate is Active only on a positive reading of BOTH flags; blocked outranks unknown; an absent gate is not published, not clear', () => {
  const g = (gate) => M.gateRow({ gate });
  assert.deepEqual(g({ blocked: false, unknown: false, reasons: [] }), { state: 'active', cls: 'chip--up', label: 'dd.rb_gate_active', why: null, reasons: [] });
  const bl = g({ blocked: true, unknown: false, reasons: ['kill switch engaged', 'daily_loss'] });
  assert.equal(bl.state, 'blocked'); assert.equal(bl.cls, 'chip--down'); assert.equal(bl.label, 'dd.rb_gate_paused'); assert.equal(bl.why, null);
  assert.deepEqual(bl.reasons, ['kill switch engaged', 'daily_loss']);
  assert.equal(g({ blocked: true, unknown: true, reasons: [] }).state, 'blocked', 'a confirmed breaker beside an unreadable field is still blocked');
  assert.deepEqual(g({ blocked: true, unknown: false, reasons: [3, '', 'x', null] }).reasons, ['x']);
  assert.deepEqual(g({ blocked: true, unknown: false, reasons: 'x' }).reasons, [], 'reasons is a list or nothing');
  const un = g({ blocked: false, unknown: true, reasons: ['venue unread'] });
  assert.equal(un.state, 'unknown'); assert.equal(un.cls, 'chip--warn'); assert.equal(un.label, 'dd.rb_gate_unknown'); assert.equal(un.why, 'dd.rb_gate_unknown_why');
  assert.deepEqual(un.reasons, ['venue unread']);
  // THE MALFORMATION SWEEP: no shape short of two read booleans reaches the green word.
  const shapes = [{ blocked: false }, { unknown: false }, { blocked: false, unknown: null }, { blocked: null, unknown: false }, { blocked: 'false', unknown: false },
    { blocked: false, unknown: 'false' }, { blocked: 0, unknown: 0 }, { blocked: false, unknown: undefined }, { blocked: 'no', unknown: false }, {}, { reasons: [] }];
  for (const s of shapes) {
    const r = g(s);
    assert.equal(r.state, 'unknown', JSON.stringify(s)); assert.equal(r.cls, 'chip--warn'); assert.notEqual(r.label, 'dd.rb_gate_active');
  }
  for (const v of [undefined, null, 'clear', 7, [], true]) {
    const r = g(v);
    assert.equal(r.state, 'absent', `gate ${String(v)}`); assert.equal(r.cls, 'chip--offline'); assert.equal(r.label, 'dd.rb_gate_absent');
    assert.equal(r.why, 'dd.rb_gate_absent_why'); assert.deepEqual(r.reasons, []);
  }
});

// ── the override row ─────────────────────────────────────────────────────────

test('the override is claimed OFF only by the boolean, and its figures are numbers or nothing', () => {
  assert.deepEqual(M.overrideRow(full()), { pct: null, defaultPct: 7, hardeningOff: false });
  assert.deepEqual(M.overrideRow({ override_pct: 4.5, default_limit_pct: 7, hardening: false }), { pct: 4.5, defaultPct: 7, hardeningOff: true });
  for (const v of [undefined, null, 'false', 0, '', 'off']) assert.equal(M.overrideRow({ hardening: v }).hardeningOff, false, `hardening ${String(v)}`);
  for (const v of [undefined, null, '', '4.5', NaN]) assert.equal(M.overrideRow({ override_pct: v }).pct, null, `override ${String(v)}`);
  for (const v of [undefined, null, '', '7', NaN]) assert.equal(M.overrideRow({ default_limit_pct: v }).defaultPct, null, `default ${String(v)}`);
  assert.equal(M.overrideRow({ override_pct: 0 }).pct, 0, 'a zero override is a number the bot refused upstream; this page does not re-decide it');
});

// ── red herrings ─────────────────────────────────────────────────────────────

test('red herrings: a green gate beside an unread drawdown, and a Healthy word beside no number, do not make the card clear', () => {
  const r = read({ verdict: 'Healthy', source: 'live', gate: { blocked: false, unknown: false, reasons: [] }, slots_used: 0, slots_cap: 5 });
  assert.equal(r.state, 'read');
  assert.equal(r.gate.state, 'active', 'the gate row is its own reading and IS active');
  assert.equal(r.slots.fill, 0, 'the slots row is its own reading and IS a flat book');
  assert.equal(r.drawdown.state, 'unread'); assert.equal(r.drawdown.cls, ''); assert.equal(r.drawdown.fill, null); assert.equal(r.drawdown.verdict, null);
  assert.equal(r.drawdown.why, 'dd.rb_dd_unread', 'the row the card is named for says it could not be read — the verdict word beside it is not evidence');
  // Numbers with the calmest word and no limit is a number, not a reading of headroom.
  const noLimit = read({ drawdown_pct: 0.1, verdict: 'Healthy', slots_used: 1, slots_cap: 5, gate: { blocked: false, unknown: false } });
  assert.equal(noLimit.drawdown.state, 'partial'); assert.equal(noLimit.drawdown.cls, ''); assert.equal(noLimit.drawdown.fill, null); assert.equal(noLimit.drawdown.pct, 0.1);
  // And a FLOOR under the cap beside a clear gate is not headroom: it keeps its warning.
  const floor = read(Object.assign(full(), { slots_floor: true, slots_note: 'could not read bybit' }));
  assert.equal(floor.gate.state, 'active'); assert.equal(floor.slots.state, 'floor'); assert.equal(floor.slots.cls, 'rb-warn');
});

// ── words: every key declared, listed as a literal, translated ───────────────

test('every key the model can emit is declared, listed in the renderer as a literal, and present in fourteen languages with its slots', () => {
  assert.deepEqual(M.KEYS, Object.keys(M.W));
  const renderer = blockBetween(RAW, '// ── risk backstop: renderers ──', '// ── risk backstop: renderers end ──', { label: 'risk backstop renderers' });
  const literal = [...renderer.matchAll(/T\('(dd\.rb_[\w]+)'/g)].map((m) => m[1]);
  for (const k of M.KEYS) assert.ok(literal.includes(k), `${k} is not a literal T() call in the renderer's map`);
  for (const k of literal) assert.ok(M.KEYS.includes(k), `${k} is in the renderer's map and the model cannot emit it`);
  for (const k of [...M.KEYS, 'dd.e_ebackstop', 'dp.ebackstop', 'aria.rb_dd_bar', 'aria.rb_slots_bar']) {
    const entry = i18n.STRINGS[k];
    assert.ok(entry, `${k} missing from the dictionary`);
    for (const lang of LANGS) {
      assert.ok(typeof entry[lang] === 'string' && entry[lang].trim(), `${k} has no ${lang}`);
      for (const slot of entry.en.match(/\{\w+\}/g) || []) assert.ok(entry[lang].includes(slot), `${k}: ${lang} lost the ${slot} slot`);
    }
  }
  // Driven: every key the model emits over a corpus is one it declares.
  const corpus = [full(), {}, undefined, { unreadable: true },
    { drawdown_pct: 3, limit_pct: 7, verdict: 'Nonsense', slots_used: 2, slots_floor: true, slots_note: 'n', gate: { blocked: true, unknown: true, reasons: ['r'] } },
    { limit_pct: 7, verdict: 'Healthy', slots_used: 2, slots_cap: 5, source: 'paper', gate: { blocked: false, unknown: true } },
    { drawdown_pct: 3, source: 'x', gate: null }];
  const seen = new Set();
  const add = (k) => { if (typeof k === 'string' && k.startsWith('dd.')) seen.add(k); };
  for (const b of corpus) for (const age of [FRESH, MAX + 1, null]) {
    const out = M.readBackstop(b, age, MAX);
    add(out.head); add(out.why);
    if (out.state === 'read') { add(out.drawdown.src); add(out.drawdown.why); add(out.slots.why); add(out.gate.label); add(out.gate.why); }
  }
  for (const k of seen) assert.ok(M.KEYS.includes(k), `${k} was emitted and is not declared`);
  // 23 of the 33 keys are the model's to emit; the other ten are the renderer's row labels and templates.
  assert.equal(seen.size, 23, `the corpus reaches the whole of the model's own vocabulary (${seen.size})`);
});

// ── the renderers, sliced and run ────────────────────────────────────────────

function renderers() {
  const block = blockBetween(RAW, '// ── risk backstop: renderers ──', '// ── risk backstop: renderers end ──', { label: 'risk backstop renderers' });
  const escFn = APPJS.slice(APPJS.indexOf('function esc('), APPJS.indexOf('\n  }\n', APPJS.indexOf('function esc(')) + 4);
  const fmtLine = APPJS.split('\n').find((l) => /^\s*function fmt\(n, d = 2\)/.test(l));
  assert.ok(/function esc\(/.test(escFn) && fmtLine, 'esc and fmt sliced from app.js');
  const ctx = { T: (k) => '[' + k + ']', fmtAgo: (iso) => 'ago(' + iso + ')' };
  vm.createContext(ctx);
  vm.runInContext(escFn + '\n' + fmtLine + '\n' + block + '\nthis.riskBackstopHtml = riskBackstopHtml; this.rbWords = rbWords;', ctx);
  return ctx;
}
const rows = (html) => [...html.matchAll(/<div class="rb-row([^"]*)">/g)].map((m) => m[1].trim());
const labels = (html) => [...html.matchAll(/class="rb-label">([^<]*)</g)].map((m) => m[1]);

test('the read card renders four rows, a bar over the drawdown and the slots only, the gate as a chip, and no title attribute', () => {
  const R = renderers();
  const html = R.riskBackstopHtml(read(full()), '2026-09-14T11:58:00.000Z', R.rbWords());
  assert.deepEqual(labels(html), ['[dd.rb_dd]', '[dd.rb_slots]', '[dd.rb_gate]', '[dd.rb_override]']);
  assert.deepEqual(rows(html), ['', '', '', '']);
  assert.equal((html.match(/class="rb-track"/g) || []).length, 2);
  assert.ok(html.includes('<span class="rb-fill rb-up" style="width:45.7%" role="img" aria-label="[aria.rb_dd_bar]"></span>'), 'the drawdown bar is the verdict’s colour at pct/limit');
  assert.ok(html.includes('<span class="rb-fill rb-cap" style="width:40.0%" role="img" aria-label="[aria.rb_slots_bar]"></span>'), 'the slots bar is capacity-coloured');
  assert.ok(html.includes('<span class="rb-val rb-up">3.2% / 7.0%</span><span class="rb-src">[dd.rb_src_live]</span>'));
  assert.ok(html.includes('<span class="rb-val ">2 / 5</span>'), 'a count is not coloured');
  assert.ok(html.includes('<span class="chip chip--up">[dd.rb_gate_active]</span>'));
  assert.ok(html.includes('<p class="rb-note">[dd.rb_operator]</p>'), 'the operator note is on the card');
  assert.ok(!/rb-why/.test(html), 'nothing to explain on a full read');
  assert.ok(!/title=/.test(html), 'no caveat hides in a title attribute');
  const words = Object.assign({}, R.rbWords(), { 'dd.rb_override_none': 'none (default {d})', 'dd.rb_override_set': '{p} (default {d})' });
  assert.ok(R.riskBackstopHtml(read(full()), null, words).includes('<span class="rb-val">none (default 7.0%)</span>'));
  assert.ok(R.riskBackstopHtml(read(Object.assign(full(), { override_pct: 4.5 })), null, words).includes('<span class="rb-val">4.5% (default 7.0%)</span>'));
  assert.ok(R.riskBackstopHtml(read(Object.assign(full(), { override_pct: 4.5, default_limit_pct: null })), null, words).includes('<span class="rb-val">4.5% (default —)</span>'));
  const off = R.riskBackstopHtml(read(Object.assign(full(), { hardening: false })), null, R.rbWords());
  assert.ok(off.includes('<span class="rb-why">[dd.rb_hardening_off]</span>'));
});

test('a verdict this page does not know keeps the numbers and declines the bar; an unread count prints a dash and never "0 / n"', () => {
  const R = renderers();
  const html = R.riskBackstopHtml(read({ drawdown_pct: 3, limit_pct: 7, verdict: 'Nonsense', slots_cap: 5, gate: { blocked: false, unknown: true } }), null, R.rbWords());
  assert.deepEqual(rows(html), ['rb-row--unread', 'rb-row--unread', '', '']);
  assert.ok(!/rb-track|rb-fill/.test(html), 'no track anywhere: a full track with an invisible fill reads as zero');
  assert.ok(html.includes('<span class="rb-val ">3.0% / 7.0%</span>'), 'the numbers are still printed, uncoloured');
  assert.ok(html.includes('<span class="rb-why">[dd.rb_no_verdict]</span>'));
  assert.ok(html.includes('<span class="rb-val ">— / 5</span>'), 'an unread count against a read cap');
  assert.ok(!/\b0 \/ \d/.test(html), 'the one zero the unreadable-is-not-zero smoke cannot see');
  assert.ok(html.includes('<span class="rb-why">[dd.rb_slots_unread]</span>'));
  assert.ok(html.includes('<span class="chip chip--warn">[dd.rb_gate_unknown]</span>'));
  assert.ok(html.includes('<span class="rb-why">[dd.rb_gate_unknown_why]</span>'));
  const none = R.riskBackstopHtml(read({}), null, R.rbWords());
  assert.ok(none.includes('<span class="rb-val ">—</span><span class="rb-src">[dd.rb_src_unknown]</span>'), 'nothing read is a dash');
  assert.ok(none.includes('<span class="rb-val ">—</span></div>'));
  assert.ok(none.includes('<span class="chip chip--offline">[dd.rb_gate_absent]</span>'));
  assert.ok(none.includes('<span class="rb-why">[dd.rb_gate_absent_why]</span>'));
  assert.ok(!/\b0(\.0)?%|\b0 \/|width:/.test(none), 'and never a zero, never a bar');
  // Escaping: reasons and the floor note are producer text.
  const words = Object.assign({}, R.rbWords(), { 'dd.rb_floor': 'A floor — {note}.' });
  const esc = R.riskBackstopHtml(read({ slots_used: 2, slots_cap: 5, slots_floor: true, slots_note: 'could not read <b>x</b>', gate: { blocked: true, unknown: false, reasons: ['<i>kill</i> switch', 'daily_loss'] } }), null, words);
  assert.ok(esc.includes('<span class="rb-why">A floor — could not read &lt;b&gt;x&lt;/b&gt;.</span>'));
  assert.ok(esc.includes('<span class="rb-reasons">[dd.rb_gate_refused] &lt;i&gt;kill&lt;/i&gt; switch; daily_loss</span>'));
  assert.ok(!esc.includes('<b>x') && !esc.includes('<i>kill'));
  assert.ok(esc.includes('<span class="rb-val rb-warn">≥2 / 5</span>'), 'a floor is marked as one and coloured warn');
  assert.ok(esc.includes('<span class="rb-fill rb-warn" style="width:40.0%"'));
  assert.ok(esc.includes('<span class="chip chip--down">[dd.rb_gate_paused]</span>'));
  assert.ok(!/rb-why">\[dd\.rb_floor\]/.test(R.riskBackstopHtml(read({ slots_used: 2, slots_cap: 5, slots_floor: true }), null, R.rbWords())), 'a floor with no note prints no empty sentence');
});

test('the four non-read states render one row with their own headline word, the stale one carrying its age, and no bar', () => {
  const R = renderers();
  const words = Object.assign({}, R.rbWords(), { 'dd.rb_h_stale': 'Last read {when}' });
  const cases = [
    [M.readBackstop(full(), MAX + 1, MAX), 'Last read ago(2026-09-14T11:00:00.000Z)', '[dd.rb_stale]'],
    [M.readBackstop(full(), null, MAX), '[dd.rb_h_undated]', '[dd.rb_no_age]'],
    [read(undefined), '[dd.rb_h_absent]', '[dd.rb_absent]'],
    [read({ unreadable: true }), '[dd.rb_h_unread]', '[dd.rb_unread_engine]'],
  ];
  for (const [m, head, why] of cases) {
    const html = R.riskBackstopHtml(m, '2026-09-14T11:00:00.000Z', words);
    assert.deepEqual(rows(html), ['rb-row--unread'], m.state);
    assert.deepEqual(labels(html), ['[dd.rb_dd]']);
    assert.ok(html.includes('<span class="rb-val">' + head + '</span>'), `${m.state}: ${head}`);
    assert.ok(html.includes('<span class="rb-why">' + why + '</span>'));
    assert.ok(!/rb-track|rb-fill|chip--|rb-up|rb-down|rb-warn/.test(html), 'no bar, no chip, no colour');
    assert.ok(html.includes('<p class="rb-note">[dd.rb_operator]</p>'));
    assert.ok(!/\d+(\.\d+)?%/.test(html), 'no figure is printed off a memory, a fault or an absence');
  }
  assert.ok(R.riskBackstopHtml(M.readBackstop(full(), MAX + 1, MAX), null, words).includes('<span class="rb-val">Last read </span>'), 'a stale head with no stamp to render does not crash');
});

// ── the loader, sliced and driven ────────────────────────────────────────────

function loader() {
  const p = loaderBodies(SRC).find((x) => x.target === "C('ebackstop')");
  assert.ok(p, 'the risk backstop loader exists');
  return p;
}
const NOW = Date.parse('2026-09-14T12:00:00Z');

test('the loader adopts the outcome before it guards, guards THIS read, dates the block by the scan’s own stamp, and throws when a model is missing', async () => {
  const p = loader();
  const mustReadFn = APPJS.slice(APPJS.indexOf('function mustRead(r)'), APPJS.indexOf('\n  }\n', APPJS.indexOf('function mustRead(r)')) + 4);
  const drive = async (envelope, { model = M, es = ES, cx = CX } = {}) => {
    const log = [];
    const ctx = {
      self: { RiskBackstopModel: model, EngineStatusModel: es, ContextChipsModel: cx },
      scanRead: Promise.resolve(envelope), window: {},
      Date: { now: () => NOW, parse: Date.parse },
      adoptScanRead: (r) => { log.push('adopt'); return r; },
      riskBackstopHtml: (m, at) => { log.push('render'); return '<card>' + m.state + (m.drawdown ? ':' + m.drawdown.state : '') + '|' + String(at) + '|' + (m.ageSec === null ? 'null' : Math.round(m.ageSec)) + '</card>'; },
      rbWords: () => ({}), T: (k) => k, C: (x) => x,
      renderPanel: (el, fn, opts) => ({ el, fn, opts }),
    };
    vm.createContext(ctx);
    const call = vm.runInContext(mustReadFn + '\nthis.PanelErrorModel = { codeOf: () => "" };\n' + p.body, ctx);
    let out, err = null;
    try { out = await call.fn(); } catch (e) { err = e; }
    return { out, err, log, opts: call.opts };
  };
  const scan = (over) => Object.assign({ received_at: '2026-09-14T11:58:00Z', timestamp: '2026-09-14 11:55 UTC', circuit_breaker: { backstop: full() } }, over);
  const refused = await drive({ ok: false, status: 503, data: { error: 'gateway_down' } });
  assert.ok(refused.err, 'a refused read throws'); assert.equal(refused.err.status, 503);
  assert.deepEqual(refused.log, ['adopt'], 'the topbar is told BEFORE the throw, and nothing renders');
  const dead = await drive({ ok: false, status: 0, data: null });
  assert.ok(dead.err); assert.deepEqual(dead.log, ['adopt']);
  const unparsed = await drive({ ok: true, status: 200, data: null, unreadable: true });
  assert.ok(unparsed.err, 'a 2xx whose body did not parse is an error, not the absent state'); assert.deepEqual(unparsed.log, ['adopt']);
  const empty = await drive({ ok: true, status: 200, data: { scan: null, message: 'No scan data yet.' } });
  assert.equal(empty.err, null); assert.equal(empty.out, null, 'a read with no scan is the empty state'); assert.deepEqual(empty.log, ['adopt']);
  const ok = await drive({ ok: true, status: 200, data: { scan: scan() } });
  assert.equal(ok.err, null); assert.equal(ok.out, '<card>read:read|2026-09-14T11:58:00.000Z|120</card>', 'the block is read off circuit_breaker.backstop, not off the breaker itself'); assert.deepEqual(ok.log, ['adopt', 'render']);
  // The ingest stamp dates the block; the bot's own spelling is the fallback; neither is the request clock.
  const botStamp = await drive({ ok: true, status: 200, data: { scan: scan({ received_at: undefined }) } });
  assert.equal(botStamp.out, '<card>read:read|2026-09-14T11:55:00.000Z|300</card>');
  const noStamp = await drive({ ok: true, status: 200, data: { scan: scan({ received_at: 'junk', timestamp: '' }) } });
  assert.equal(noStamp.out, '<card>undated|null|null</card>');
  const old = await drive({ ok: true, status: 200, data: { scan: scan({ received_at: '2026-09-14T09:00:00Z' }) } });
  assert.equal(old.out, '<card>stale|2026-09-14T09:00:00.000Z|10800</card>');
  const past = await drive({ ok: true, status: 200, data: { scan: scan({ received_at: '2026-09-14T11:15:00Z' }) } });
  assert.equal(past.out, '<card>stale|2026-09-14T11:15:00.000Z|2700</card>', 'forty-five minutes is past the topbar’s floor, not some looser one of this panel’s own');
  const noCb = await drive({ ok: true, status: 200, data: { scan: scan({ circuit_breaker: undefined }) } });
  assert.equal(noCb.out, '<card>absent|2026-09-14T11:58:00.000Z|120</card>', 'an older bot with no breaker block');
  const cbString = await drive({ ok: true, status: 200, data: { scan: scan({ circuit_breaker: 'x' }) } });
  assert.equal(cbString.out, '<card>absent|2026-09-14T11:58:00.000Z|120</card>');
  const marker = await drive({ ok: true, status: 200, data: { scan: scan({ circuit_breaker: { backstop: { unreadable: true } } }) } });
  assert.equal(marker.out, '<card>unreadable|2026-09-14T11:58:00.000Z|120</card>');
  for (const [k, v] of [['model', null], ['es', null], ['cx', null]]) {
    const missing = await drive({ ok: true, status: 200, data: { scan: scan() } }, { [k]: v });
    assert.ok(missing.err, `${k} missing throws`); assert.deepEqual(missing.log, [], 'before the read is touched');
    assert.match(String(missing.err.message), /risk backstop model unavailable/);
  }
  assert.equal(ok.opts.timeoutMs, 12000);
  assert.equal(ok.opts.empty.text, 'dd.e_ebackstop', 'the empty sentence is the dictionary’s');
});

// ── wiring ───────────────────────────────────────────────────────────────────

function renderEngineBody() {
  const i = SRC.indexOf('async function renderEngine()');
  assert.ok(i > -1);
  let depth = 0, j = SRC.indexOf('{', i);
  for (; j < SRC.length; j++) { if (SRC[j] === '{') depth++; else if (SRC[j] === '}') { depth--; if (depth === 0) break; } }
  return SRC.slice(i, j + 1);
}

test('one /scan read per engine render, shared by the hoisted panels and guarded by this one, with the panel’s budget above the read’s', () => {
  const eng = renderEngineBody();
  const reads = [...eng.matchAll(/fetchJSON\('\/api\/bot\/sync\/scan'[^)]*\)/g)].map((m) => m[0]);
  assert.equal(reads.length, 1, `renderEngine fetches /scan once, saw ${reads.length}`);
  assert.match(reads[0], /timeoutMs: 10000/);
  assert.ok(/const scanRead = fetchJSON\('\/api\/bot\/sync\/scan'/.test(eng), 'the read is the shared scanRead');
  assert.ok(!/\bgetScan\(\)/.test(eng), 'no bare getScan() in renderEngine: the hoisted panels consume the shared read');
  assert.ok(/const scan = await getScan\(45000, scanRead\)/.test(eng), 'the hoisted panels consume the shared read');
  assert.ok(!/updateConnChip\(\)/.test(eng), 'the topbar is told by adoptScanRead, not by a second writer in the view');
  const p = loader();
  assert.match(p.body, /await scanRead/); assert.match(p.body, /adoptScanRead\(r\)/); assert.match(p.body, /mustRead\(r\)/);
  assert.ok(!/fetchJSON\(/.test(p.body), 'the loader fetches nothing of its own');
  assert.ok(!/getScan\(/.test(p.body), 'and is not built on the swallowing cache');
  assert.match(p.body, /timeoutMs: 12000/);
  assert.match(p.body, /throw new Error\('risk backstop model unavailable'\)/);
  assert.match(p.body, /CX\.stamp\(sc\.received_at\) \|\| CX\.stamp\(sc\.timestamp\)/, 'the stamp is the context row’s one reader, ingest first');
  assert.match(p.body, /ES\.STALE_MAX_S/, 'the staleness floor is the topbar’s');
  assert.ok(!/new Date\(\)|toISOString/.test(p.body), 'no stamp is manufactured from the request clock');
});

test('the shell sits between the engine account and the modules, the loader in the same place, and the model script precedes dashboard.js', () => {
  const eng = renderEngineBody();
  const ids = [...eng.matchAll(/id="(p-[a-z]+)"/g)].map((m) => m[1]);
  assert.deepEqual(ids.slice(ids.indexOf('p-ecb'), ids.indexOf('p-ecb') + 3), ['p-ecb', 'p-ebackstop', 'p-emods']);
  const at = (s) => { const i = eng.indexOf(s); assert.ok(i > -1, s); return i; };
  assert.ok(at("renderPanel(C('ecb')") < at("renderPanel(C('ebackstop')") && at("renderPanel(C('ebackstop')") < at("renderPanel(C('emods')"));
  assert.ok(at('const scanRead = fetchJSON') < at("renderPanel(C('ebackstop')"), 'the read starts before the panel that guards it');
  const shell = eng.split('\n').find((l) => l.includes('id="p-ebackstop"'));
  assert.ok(shell && /data-i18n="dp\.ebackstop"/.test(shell) && /id="c-ebackstop"/.test(shell));
  const model = HTML.indexOf('/js/risk-backstop-model.js?v=');
  const cx = HTML.indexOf('/js/context-chips-model.js?v=');
  const dash = HTML.indexOf('/js/dashboard.js?v=');
  assert.ok(model > -1 && model < dash, 'risk-backstop-model.js is a script tag before dashboard.js');
  assert.ok(cx > -1 && cx < dash, 'and so is the stamp reader it leans on');
});

test('the styles hide the track on an unread row, paint capacity in the brand accent, and neither they nor the renderer invent a colour', () => {
  const start = CSS.indexOf('/* ---- Risk backstop (Engine');
  assert.ok(start > -1, 'the CSS block exists');
  const next = CSS.indexOf('/* ---- ', start + 10);
  const css = CSS.slice(start, next === -1 ? CSS.length : next).replace(/\/\*[\s\S]*?\*\//g, '');
  assert.match(css, /\.rb-row--unread \.rb-track \{ display: none; \}/);
  assert.match(css, /\.rb-fill\.rb-cap\s*\{ background: var\(--gold\); \}/);
  assert.match(css, /\.rb-fill\.rb-up\s*\{ background: var\(--up\); \}/);
  assert.match(css, /\.rb-fill\.rb-warn\s*\{ background: var\(--warn\); \}/);
  assert.match(css, /\.rb-val\.rb-down \{ color: var\(--down\); \}/);
  assert.ok(!/#[0-9a-f]{3,8}\b/i.test(css), 'no literal colour in the appended layer');
  assert.ok(!/@media/.test(css), 'no breakpoint of its own');
  assert.ok(!/\.rb-fill\s*\{[^}]*background/.test(css), 'a fill with no verdict class paints nothing — and the row hides the track anyway');
  const renderer = codeOnly(blockBetween(RAW, '// ── risk backstop: renderers ──', '// ── risk backstop: renderers end ──'));
  assert.ok(!/rb-(up|warn|down|cap)|chip--(up|down|warn|offline)/.test(renderer), 'no colour class is spelled in the renderer: every one is the model’s word');
  assert.ok(!/[<>]=?\s*0\s*\?/.test(renderer), 'and no comparison decides a verdict there');
  const model = codeOnly(MODEL_SRC).replace(/Math\.max\(0,/g, '');
  assert.ok(!/\|\|\s*0\b|>=\s*0\s*\?|>\s*0\s*\?/.test(model), 'the model carries none of the or-zero shapes (an `|| \'\'` on a CLASS is an abstention, not a zero)');
  assert.ok(!/VERDICT_CLS\s*=\s*\{[^}]*\bok\b/i.test(model) && /VERDICT_CLS = \{ Healthy: 'rb-up', Warning: 'rb-warn', Critical: 'rb-down' \}/.test(model), 'three verdict words, the bot’s own spelling');
});

test('the fixture the smoke serves renders the populated card, so the DOM test is a test of four rows and two bars', () => {
  const FX = require('./fixtures/dashboard_smoke_fixtures.js');
  const row = FX.find(([prefix]) => prefix === '/api/bot/sync/scan');
  assert.ok(row, 'the smoke serves /api/bot/sync/scan');
  const sc = row[1].scan;
  const at = CX.stamp(sc.received_at) || CX.stamp(sc.timestamp);
  assert.ok(at, 'the fixture scan is dated');
  const m = M.readBackstop(sc.circuit_breaker.backstop, (Date.now() - Date.parse(at)) / 1000, ES.STALE_MAX_S);
  assert.equal(m.state, 'read');
  assert.ok(m.drawdown.fill !== null && m.slots.fill !== null, 'two bars');
  assert.equal(m.gate.state, 'active'); assert.equal(m.drawdown.cls, 'rb-up'); assert.equal(m.slots.state, 'read');
});
