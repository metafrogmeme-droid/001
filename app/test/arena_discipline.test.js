'use strict';
// The discipline read — did the plan close your trades, or did panic and
// liquidation? Derived only from recorded closes; private only; a read,
// never a verdict; and below its minimum sample it declines to exist.

const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

const { computeDiscipline, MIN_CLOSES } = require('../lib/arena_discipline.js');
const i18n = require('../public/js/i18n.js');
const codes = i18n.LANGS.map((l) => l.code);
const read = (...p) => fs.readFileSync(path.join(__dirname, '..', ...p), 'utf8');

const t = (reason, pnl) => ({ reason, pnl });

test('below the minimum sample the read declines to exist', () => {
  // Grading four closes is grading noise — and noise graded is invented.
  const few = [t('tp', 5), t('sl', -3), t('manual', 2), t('manual', -1)];
  const r = computeDiscipline(few);
  assert.equal(r.enough, false);
  assert.equal(r.closes, 4);
  assert.equal(r.min_closes, MIN_CLOSES);
  assert.ok(!('level' in r), 'a level on an insufficient sample is a fabricated judgment');
});

test('planned closes are tp, sl and trail — the exits set before the fact', () => {
  const rows = [t('tp', 5), t('sl', -3), t('trail', 4), t('manual', 2), t('manual', -1), t('liquidated', -10)];
  const r = computeDiscipline(rows);
  assert.equal(r.enough, true);
  assert.equal(r.closes, 6);
  assert.equal(r.planned_rate_pct, 50);
  assert.deepEqual(r.by, { tp: 1, sl: 1, trail: 1, manual: 2, liquidated: 1, other: 0 });
});

test('the planned-vs-improvised win comparison needs both sides', () => {
  const allPlanned = computeDiscipline([t('tp', 5), t('tp', 3), t('sl', -2), t('trail', 1), t('tp', 2)]);
  assert.equal(allPlanned.win_rate_planned_pct, 80);
  assert.equal(allPlanned.win_rate_manual_pct, null,
    'a comparison with nothing on one side is not a comparison');
});

test('a liquidation habit overrides everything else', () => {
  // 60% planned would read as disciplined — but one close in five going to
  // liquidation means the market keeps finishing these trades.
  const rows = [t('tp', 5), t('tp', 3), t('sl', -2), t('liquidated', -10), t('liquidated', -10)];
  const r = computeDiscipline(rows);
  assert.equal(r.planned_rate_pct, 60);
  assert.equal(r.liquidation_rate_pct, 40);
  assert.equal(r.level, 'improvised');
});

test('the levels sit where the thresholds say', () => {
  const mk = (planned, manual) => computeDiscipline([
    ...Array.from({ length: planned }, () => t('tp', 1)),
    ...Array.from({ length: manual }, () => t('manual', 1)),
  ]);
  assert.equal(mk(5, 5).level, 'planned');       // 50%
  assert.equal(mk(3, 7).level, 'mixed');         // 30%
  assert.equal(mk(1, 9).level, 'improvised');    // 10%
});

test('a zero-pnl close is not a win on either side', () => {
  const r = computeDiscipline([t('tp', 0), t('tp', 1), t('manual', 0), t('manual', 0), t('sl', -1)]);
  assert.equal(r.win_rate_planned_pct, 33.3);
  assert.equal(r.win_rate_manual_pct, 0);
});

test('the read is wired into the PRIVATE account payload only', () => {
  const routeSrc = read('routes', 'arena.js');
  assert.match(routeSrc, /discipline: require\('\.\.\/lib\/arena_discipline'\)\.computeDiscipline\(allTrades\)/);
  // Not on any public surface: leaderboard, tape, trader card, seasons.
  for (const fn of ['/leaderboard', '/tape', "'/trader/:handle'"]) {
    const at = routeSrc.indexOf(fn);
    if (at === -1) continue;
    const slice = routeSrc.slice(at, at + 2500);
    assert.ok(!slice.includes('discipline'), `discipline leaked onto the public ${fn} surface`);
  }
});

test('the panel is a read, not a verdict — and says so in every language', () => {
  const page = read('public', 'arena.html');
  assert.match(page, /disc && disc\.enough/);
  assert.match(page, /arena\.disc_h/);
  const e = i18n.STRINGS['arena.disc_h'];
  const readNotVerdict = {
    en: /a read, not a verdict/i, hi: /फ़ैसला नहीं/, it: /non un verdetto/i,
    es: /no un veredicto/i, zh: /不是裁決/, pt: /não um veredicto/i, fr: /pas un verdict/i,
    de: /kein Urteil/i, nl: /geen oordeel/i, ja: /判定ではなく/, ko: /판결이 아닌/,
    ru: /не вердикт/i, tr: /hüküm değil/i, ar: /لا حكم/,
  };
  for (const c of codes) {
    assert.match(String(e[c]), readNotVerdict[c],
      `arena.disc_h:${c} lost the "read, not a verdict" framing`);
  }
});

test('every discipline string exists in all 14 languages', () => {
  for (const k of ['arena.disc_h', 'arena.disc_planned', 'arena.disc_mixed',
    'arena.disc_improvised', 'arena.disc_rate', 'arena.disc_liq', 'arena.disc_noliq',
    'arena.disc_vs']) {
    const e = i18n.STRINGS[k];
    assert.ok(e, `${k} missing from the dictionary`);
    for (const c of codes) assert.ok(String(e[c] || '').trim().length, `${k} is missing ${c}`);
  }
});
