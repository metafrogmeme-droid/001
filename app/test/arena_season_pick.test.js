'use strict';
/**
 * Every season read picks the SAME season — including the ones that gate trades.
 *
 * `SELECT ... FROM arena_seasons LIMIT 1` with no ORDER BY is correct only
 * while exactly one season has ever existed. MySQL may return any row; the
 * in-memory shim sorts newest-first. So the two disagree BY CONSTRUCTION and
 * nothing shows it until a second season is authored.
 *
 * A previous pass fixed the public `/season` board and stopped there, which was
 * the more visible half and the less important one. Three sites were left on
 * the old query and all three sit on the WRITE path, feeding `checkSeasonRules`
 * — the guard that enforces a season's leverage and symbol limits at the moment
 * a position opens:
 *
 *     sweepFollows        copy-trading opens
 *     openForUser         POST /open
 *     POST /open-signal   opening from a signal
 *
 * A board naming the wrong season is a wrong label. A trade admitted because
 * the WRONG season's rules were consulted is a position that should not exist,
 * and the operator has no way to see it happened — `checkSeasonRules` returns
 * ok and the trade simply opens.
 *
 * That is why this file tests the picker against every reader rather than the
 * board alone, and why the second season is not hypothetical here: it is
 * planted in every case.
 */

const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

const SRC = fs.readFileSync(path.join(__dirname, '..', 'routes', 'arena.js'), 'utf8');
const { codeOnly } = require('./helpers/code_only');
const CODE = codeOnly(SRC);

// ── no reader is left on the unordered query ──────────────────────────────

test('nothing reads arena_seasons with an unordered LIMIT 1', () => {
  // The exact shape of the bug. Comments are blanked first — the file
  // documents the old query at length, and a scan that matched the warning
  // would fail on the fix describing itself.
  const hits = CODE.match(/FROM arena_seasons LIMIT 1/g) || [];
  assert.deepEqual(hits, [],
    `${hits.length} site(s) still read a season with an unordered LIMIT 1. With `
    + 'more than one season MySQL may return either row, so a trade can be '
    + 'admitted against the wrong season\'s rules.');
});

test('every season read that needs the CURRENT season uses the picker', () => {
  // Counted rather than eyeballed: a new reader added later with its own
  // ad-hoc ordering would pass the test above and still disagree with the rest.
  //
  // Two kinds of read legitimately do NOT pick, and they are excluded by what
  // they are rather than by a magic number — a count with slack in it stops
  // failing the moment somebody adds the reader it was meant to catch:
  //
  //   WHERE id = ?   addressing one named season (the admin delete). The
  //                  caller already knows which row it means.
  //   the ENDED list  /seasons ranks finished seasons; there is no current one
  //                  to pick.
  const reads = (CODE.match(/FROM arena_seasons[^']*/g) || []);
  assert.ok(reads.length >= 5, `only ${reads.length} season reads found; the scan is reading nothing`);
  const needPick = reads.filter((r) => !/WHERE id = \?/.test(r));
  const picks = (CODE.match(/pickCurrentSeason\(/g) || []).length;
  // -1 for the ended-seasons list, which is the one remaining non-picker.
  assert.ok(picks >= needPick.length - 1,
    `${needPick.length} reads want the current season but only ${picks} calls to `
    + 'pickCurrentSeason — at least one reader is choosing a season by itself');
});

test('the three write-path readers each call the picker', () => {
  // Named individually because these are the ones that were missed, and the
  // cost of missing them again is a position opened under the wrong rules.
  for (const [marker, what] of [
    ['seasonRow = pickCurrentSeason(', 'sweepFollows (copy-trading opens)'],
    ['checkSeasonRules(\n        pickCurrentSeason(srows, new Date()), v.data)', 'openForUser (POST /open)'],
    ['checkSeasonRules(\n        pickCurrentSeason(srows, new Date()), d)', 'POST /open-signal'],
  ]) {
    assert.ok(SRC.includes(marker), `${what} no longer picks the current season`);
  }
});

// ── the picker itself, driven with two seasons ────────────────────────────

/** The picker, lifted out of the module so it can be driven directly. */
function loadPicker() {
  const start = SRC.indexOf('function pickCurrentSeason');
  assert.ok(start > 0, 'pickCurrentSeason is gone');
  const body = SRC.slice(start, SRC.indexOf('\n}', start) + 2);
  const seasons = require('../lib/arena_seasons');
  // eslint-disable-next-line no-new-func
  return new Function('seasons', `${body}; return pickCurrentSeason;`)(seasons);
}

const pick = loadPicker();
const S = (name, from, to) => ({ name, starts_at: from, ends_at: to });
const NOW = new Date('2026-08-24T18:00:00Z');

test('a live season wins over an upcoming one', () => {
  // Exactly today's shape: Genesis running, a second season authored to start
  // later. The board and the trade gate must both still say Genesis.
  const rows = [
    S('Genesis', '2026-07-24T15:45:00Z', '2026-09-24T15:45:00Z'),
    S('FARCASTER', '2026-09-24T16:00:00Z', '2026-11-24T16:00:00Z'),
  ];
  assert.equal(pick(rows, NOW).name, 'Genesis');
  // Row order must not change the answer — an unordered query returns them
  // either way round, which is the entire defect.
  assert.equal(pick(rows.slice().reverse(), NOW).name, 'Genesis');
});

test('once the successor starts, it wins', () => {
  const rows = [
    S('Genesis', '2026-07-24T15:45:00Z', '2026-09-24T15:45:00Z'),
    S('FARCASTER', '2026-09-24T16:00:00Z', '2026-11-24T16:00:00Z'),
  ];
  const later = new Date('2026-10-01T00:00:00Z');
  assert.equal(pick(rows, later).name, 'FARCASTER');
  assert.equal(pick(rows.slice().reverse(), later).name, 'FARCASTER');
});

test('two live seasons resolve to the newer one, deterministically', () => {
  // Overlapping windows are an authoring mistake, but "arbitrary" is not an
  // acceptable answer to it: the gate must at least be consistent with the
  // board, or a trade is admitted under rules the board is not showing.
  const rows = [
    S('Older', '2026-08-01T00:00:00Z', '2026-09-30T00:00:00Z'),
    S('Newer', '2026-08-20T00:00:00Z', '2026-10-30T00:00:00Z'),
  ];
  assert.equal(pick(rows, NOW).name, 'Newer');
  assert.equal(pick(rows.slice().reverse(), NOW).name, 'Newer');
});

test('with nothing live, the most recent by start is used rather than null', () => {
  // An ended season keeps showing until its successor begins, instead of the
  // board blinking to "no season" in the gap between two.
  const rows = [
    S('First', '2026-01-01T00:00:00Z', '2026-02-01T00:00:00Z'),
    S('Second', '2026-03-01T00:00:00Z', '2026-04-01T00:00:00Z'),
  ];
  assert.equal(pick(rows, NOW).name, 'Second');
});

test('no seasons at all is null, not a fabricated one', () => {
  assert.equal(pick([], NOW), null);
  assert.equal(pick(null, NOW), null);
  assert.equal(pick(undefined, NOW), null);
});

test('an unreadable start date does not become the current season', () => {
  // A row whose window cannot be read is not evidence of a live season, and
  // sorting NaN would otherwise let it land anywhere in the order.
  const rows = [
    S('Genesis', '2026-07-24T15:45:00Z', '2026-09-24T15:45:00Z'),
    S('Broken', 'not-a-date', 'also-not-a-date'),
  ];
  assert.equal(pick(rows, NOW).name, 'Genesis',
    'a season with an unreadable window was chosen over a genuinely live one');
});
