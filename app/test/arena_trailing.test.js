'use strict';
// Trailing stops for the paper Arena — the exit that follows the trade.
//
// The design leans on one reuse and one honesty rule. The reuse: a trailing
// position's stop LIVES IN `sl`, so firing it is the machinery that already
// existed — one exit engine, not two. The honesty rule: the Arena has no
// background job, so the ratchet only tightens on marks the account actually
// OBSERVED at read time. A high nobody saw never tightens the stop, which
// makes the trail at least as loose as a continuously-watched one — never
// tighter, never a fill manufactured from an unobserved extreme. Backfilling
// from candle highs is refused on principle: the intra-candle ordering of
// high and low is unknowable.

const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

const arena = require('../lib/arena.js');
const i18n = require('../public/js/i18n.js');
const codes = i18n.LANGS.map((l) => l.code);
const read = (...p) => fs.readFileSync(path.join(__dirname, '..', ...p), 'utf8');
const routeSrc = read('routes', 'arena.js');
const page = read('public', 'arena.html');
const db = read('db.js');

// ── the ratchet ────────────────────────────────────────────────────────────

test('the ratchet only ever tightens — long rises, short falls, neither retreats', () => {
  const long = { direction: 'LONG', trail_pct: 5, sl: null };
  assert.equal(arena.trailRatchet(long, 100), 95, 'seeds at distance from the first mark');
  long.sl = 95;
  assert.equal(arena.trailRatchet(long, 110), 104.5, 'tightens as the mark improves');
  long.sl = 104.5;
  assert.equal(arena.trailRatchet(long, 100), null, 'NEVER loosens on a pullback');
  const short = { direction: 'SHORT', trail_pct: 5, sl: null };
  assert.equal(arena.trailRatchet(short, 100), 105);
  short.sl = 105;
  assert.equal(arena.trailRatchet(short, 90), 94.5);
  short.sl = 94.5;
  assert.equal(arena.trailRatchet(short, 100), null);
});

test('no trail, no mark, no ratchet', () => {
  assert.equal(arena.trailRatchet({ direction: 'LONG', sl: 95 }, 110), null);
  assert.equal(arena.trailRatchet({ direction: 'LONG', trail_pct: 0, sl: 95 }, 110), null);
  assert.equal(arena.trailRatchet({ direction: 'LONG', trail_pct: 5, sl: 95 }, 0), null);
  assert.equal(arena.trailRatchet({ direction: 'LONG', trail_pct: 5, sl: 95 }, NaN), null);
});

test('a fired trail names its mechanism and fills at the TRIGGER, never the gap', () => {
  const pos = { direction: 'LONG', entry: 100, margin: 100, leverage: 2, sl: 114, trail_pct: 5 };
  // The mark gapped to 110, straight through the 114 trail.
  const exit = arena.exitCheck(pos, 110);
  assert.equal(exit.reason, 'trail', '"trailed out" and "stopped out" teach different lessons');
  assert.equal(exit.price, 114, 'the fill is the trigger price — same doctrine as every stop');
  // A fixed stop still says 'sl'.
  assert.equal(arena.exitCheck({ ...pos, trail_pct: null }, 110).reason, 'sl');
});

test('the trailing distance is bounded — spread noise below, not-a-stop above', () => {
  assert.equal(arena.validateTrail('').ok, true);
  assert.equal(arena.validateTrail(null).data.trail_pct, null);
  assert.equal(arena.validateTrail(5).data.trail_pct, 5);
  assert.equal(arena.validateTrail(arena.TRAIL_MIN_PCT).ok, true);
  assert.equal(arena.validateTrail(arena.TRAIL_MIN_PCT - 0.01).ok, false);
  assert.equal(arena.validateTrail(arena.TRAIL_MAX_PCT + 1).ok, false);
  assert.equal(arena.validateTrail('abc').ok, false);
});

// ── the lazy pass ──────────────────────────────────────────────────────────

test('settleLiquidations ratchets BEFORE it tests the exit, and persists', () => {
  // Order is the honest one: today's mark first tightens the stop, then the
  // SAME mark is tested against the TIGHTENED level. Reversed, a mark that
  // should ratchet-and-survive could fire on yesterday's level.
  const fn = routeSrc.slice(routeSrc.indexOf('async function settleLiquidations'),
    routeSrc.indexOf("// GET /api/arena/account"));
  const ratchetAt = fn.indexOf('arena.trailRatchet(p, mark)');
  const exitAt = fn.indexOf('arena.exitCheck(p, mark)');
  assert.ok(ratchetAt > -1 && exitAt > -1 && ratchetAt < exitAt);
  assert.match(fn, /UPDATE arena_positions SET sl = \? WHERE id = \? AND user_id = \?/,
    'an unpersisted ratchet forgets its own tightening between reads');
  // Mechanical ratchets are not user edits — the seal disclosure marker is
  // for humans moving levels.
  assert.doesNotMatch(fn, /exits_edited/);
});

test('the shim distinguishes all three UPDATE shapes by SQL, not param count', () => {
  assert.match(db, /TRAIL_PCT = \?/);
  assert.match(db, /SET SL = \?/);
  assert.match(db, /pos\.tp = params\[0\]; pos\.sl = params\[1\]; pos\.trail_pct = params\[2\]; pos\.exits_edited = 1;/);
});

test('setting a trail with no explicit stop seeds from the live mark', () => {
  assert.match(routeSrc, /mark \* \(1 - trailPct \/ 100\) : mark \* \(1 \+ trailPct \/ 100\)/);
  assert.match(routeSrc, /a trailing stop needs a stop level to trail/);
});

test('the payload carries trail_pct; the panel shows the ⟲ chip and its rule', () => {
  assert.match(routeSrc, /trail_pct: p\.trail_pct == null \? null : Number\(p\.trail_pct\)/);
  assert.match(page, /p\.trail_pct \?/);
  assert.match(page, /arena\.trail_t/, 'the tooltip stating the lazy-evaluation rule is the honesty surface');
  assert.match(page, /arena\.x_trail_q/);
  assert.match(page, /'trail' \? T\('arena\.d_reason_trail'/);
});

test('every trailing string exists in all 14 languages', () => {
  for (const k of ['arena.d_reason_trail', 'arena.trail_t', 'arena.x_trail_q']) {
    const e = i18n.STRINGS[k];
    assert.ok(e, `${k} missing from the dictionary`);
    for (const c of codes) assert.ok(String(e[c] || '').trim().length, `${k} is missing ${c}`);
  }
});

test('the never-loosens promise survives translation everywhere', () => {
  const e = i18n.STRINGS['arena.trail_t'];
  const never = {
    en: /never loosens/i, hi: /कभी ढीला नहीं/, it: /Non si allenta mai/i, es: /Nunca se afloja/i,
    zh: /永不放鬆/, pt: /Nunca afrouxa/i, fr: /ne se relâche jamais/i, de: /lockert sich nie/i,
    nl: /versoepelt nooit/i, ja: /緩むことはなく/, ko: /절대 느슨해지지 않으며/,
    ru: /никогда не ослабевает/i, tr: /Asla gevşemez/i, ar: /لا يرتخي أبدًا/,
  };
  for (const c of codes) {
    assert.match(String(e[c]), never[c], `arena.trail_t:${c} lost the "never loosens" promise`);
  }
});
