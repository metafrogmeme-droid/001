'use strict';
/**
 * The arena board, driven at the values a competition surface lies about.
 *
 * CLAUDE.md's list of what has already gone wrong in this repo is almost
 * entirely leaderboards and track records: win rates, "12 (7W/4L)", a public
 * record that published a fabricated tally, an edge-metrics panel whose own
 * comment promised "nothing is invented". Every one of those shapes is
 * reachable from this file — a rank, a return, a win rate, a trade count, a
 * countdown.
 *
 * So these tests are written the way that table says to write them: plant the
 * unreadable value, assert what the BOARD SAYS. Not what the formatter returns
 * in isolation — what a person looking at the screen would believe.
 */

const test = require('node:test');
const assert = require('node:assert');
const V = require('../public/js/embed-arena-view');

const T0 = Date.UTC(2026, 7, 24, 12, 0, 0);

// ── the reader distinction the whole board rests on ───────────────────────

test('an empty board is returned; an unreadable one throws', () => {
  // These two must never collapse. "Nobody has joined" is a measurement and is
  // printed as one; "we could not ask" is not, and the caller paints an error.
  // The signals board announced "No open signals right now" on every load for
  // its whole life because exactly this distinction was lost one level up.
  assert.deepEqual(V.readBoard({ rows: [], ranked_total: 0 }), [],
    'a genuinely empty board should come back as an empty list');

  for (const bad of [null, undefined, {}, { rows: null }, { rows: 'nope' }, 42, []]) {
    assert.throws(() => V.readBoard(bad), /unreadable/,
      `readBoard(${JSON.stringify(bad)}) invented an empty leaderboard`);
  }
});

test('a null season is an answer; a missing season key is not', () => {
  // `{season: null}` is the API saying no season has been authored — real.
  // A payload with no season key at all is a shape we do not understand.
  assert.equal(V.readSeason({ season: null }), null);
  assert.throws(() => V.readSeason({}), /unreadable/,
    'a payload with no season key was read as "no season"');
  assert.throws(() => V.readSeason(null), /unreadable/);
});

// ── returns: the +0.00% sin, by name ──────────────────────────────────────

test('an unreadable return is an em dash, never +0.00%', () => {
  // Named in CLAUDE.md as one of the original three: "an unfetchable price
  // shown as +0.00% beside a green stripe".
  for (const bad of [null, undefined, '', 'abc', NaN, Infinity, {}]) {
    assert.equal(V.pct(bad), '—', `pct(${JSON.stringify(bad)}) invented a return`);
  }
});

test('an unreadable return is NOT coloured as a win', () => {
  // `(x || 0) >= 0` is true for a missing number, so the naive version paints
  // every unreadable row green — "unreadable won", the second row of the table.
  assert.equal(V.toneFor(null), 'a-flat');
  assert.equal(V.toneFor(undefined), 'a-flat');
  assert.equal(V.toneFor('garbage'), 'a-flat');
  // And a REAL zero is its own thing: measured break-even, not unknown.
  assert.equal(V.toneFor(0), 'a-even');
  assert.equal(V.toneFor(3.47), 'a-up');
  assert.equal(V.toneFor(-1.2), 'a-down');
});

test('a real return renders with its sign', () => {
  assert.equal(V.pct(3.47), '+3.47%');
  assert.equal(V.pct(-1.5), '−1.50%');
  assert.equal(V.pct(0), '0.00%');
});

// ── win rate: 0% means lost every one ─────────────────────────────────────

test('an absent win rate is an em dash, not 0%', () => {
  // The API sends null when there are no resolved trades to rate. "0%" reads
  // as "lost every single one", which is a verdict on a trader who has not
  // finished a trade yet.
  assert.equal(V.winRate(null), '—');
  assert.equal(V.winRate(undefined), '—');
  assert.equal(V.winRate(73.3), '73.3%');
  assert.equal(V.winRate(0), '0%', 'a genuinely measured 0% is a real result');
});

// ── counts: 0 is real, missing is not ─────────────────────────────────────

test('a missing count is an em dash but a real zero prints', () => {
  assert.equal(V.count(null), '—');
  assert.equal(V.count(0), '0');
  assert.equal(V.count(48), '48');
});

// ── the countdown ─────────────────────────────────────────────────────────

test('an unreadable end date produces no countdown, not "0d left"', () => {
  // A countdown of zero on a live season says it is finishing right now — a
  // specific and alarming claim to manufacture from a broken date.
  for (const bad of [null, undefined, '', 'soon', {}]) {
    assert.equal(V.remaining(bad, T0), null,
      `remaining(${JSON.stringify(bad)}) invented a countdown`);
  }
});

test('a finished season has no countdown', () => {
  assert.equal(V.remaining(new Date(T0 - 1000).toISOString(), T0), null);
});

test('countdowns read in the units a competitor cares about', () => {
  const at = (ms) => V.remaining(new Date(T0 + ms).toISOString(), T0);
  assert.equal(at(31 * 86400000), '31d left');
  assert.equal(at(5 * 3600000), '5h left');
  assert.equal(at(20 * 60000), 'ends soon');
});

// ── the rendered board, as a person reads it ──────────────────────────────

const GENESIS = {
  name: 'Genesis', status: 'live',
  starts_at: '2026-07-24T15:45:00.000Z', ends_at: '2026-09-24T15:45:00.000Z',
};
const ROWS = [
  { rank: 1, handle: 'RUNECLAW', return_pct: 6.52, trades: 47, sealed: 46, closes: 47 },
  { rank: 2, handle: 'Buddy', return_pct: 0.03, trades: 1, sealed: 1, closes: 1 },
];

test('a live season shows its name, that it is live, and how long is left', () => {
  const html = V.seasonHtml(GENESIS, { nowMs: T0 });
  assert.match(html, /Genesis/);
  assert.match(html, /a-badge--live/);
  assert.match(html, /31d left/);
});

test('no season at all says so without implying an empty competition', () => {
  const html = V.seasonHtml(null, { nowMs: T0 });
  assert.match(html, /No season is running/);
  assert.ok(!/live/.test(html), 'a page with no season claimed one was live');
});

test('an empty board states nobody has joined — because the read succeeded', () => {
  // Only reachable when readBoard returned an EMPTY array, which it does only
  // for a payload it understood. A failed read never gets here.
  const html = V.standingsHtml([]);
  assert.match(html, /No one has joined/);
});

test('a standings row shows rank, handle, trades and a coloured return', () => {
  const html = V.standingRow(ROWS[0]);
  assert.match(html, /RUNECLAW/);
  assert.match(html, /\+6\.52%/);
  assert.match(html, /47 trades/);
  assert.match(html, /a-up/);
});

test('a row whose return could not be read is muted, not green', () => {
  const html = V.standingRow({ rank: 3, handle: 'Ghost', return_pct: null, trades: 5 });
  assert.match(html, /a-flat/, 'an unreadable return was given a win colour');
  assert.ok(!/a-up/.test(html));
  assert.match(html, /—/);
  assert.match(html, /Ghost/, 'the row lost the trader it is about');
  assert.match(html, /5 trades/, 'a readable count was dropped with the unreadable return');
});

test('no dollar amount can reach the board', () => {
  // §4. The arena publishes percent against a uniform virtual stake; a balance
  // must not appear even if a future payload carries one.
  const html = V.standingRow(
    Object.assign({}, ROWS[0], { balance: 10345.22, equity: 10345.22, pnl: 345.22 }));
  assert.ok(!html.includes('10345'), 'a balance reached a public board');
  assert.ok(!html.includes('345.22'), 'a P&L amount reached a public board');
  assert.ok(!html.includes('$'));
});

test('a handle cannot inject markup', () => {
  // Handles are user-chosen and this is built by string concatenation.
  const html = V.standingRow({ rank: 1, handle: '<img src=x onerror=alert(1)>', return_pct: 1 });
  assert.ok(!html.includes('<img'), 'a handle injected a tag');
});

// ── the tape is optional; the standings are not ───────────────────────────

test('an empty tape renders nothing rather than an empty heading', () => {
  // The `_status_lines` failure from CLAUDE.md: a heading that announces itself
  // and then says nothing reads as "nothing to report", which is the third
  // thing the guard/omit table warns about. No rows, no section.
  assert.equal(V.tapeHtml([]), '');
  assert.equal(V.tapeHtml(null), '');
});

test('the tape shows direction, symbol, who, and a toned percent', () => {
  const html = V.tapeHtml([
    { handle: 'RUNECLAW', symbol: 'LTCUSDT', direction: 'LONG', pct: 2.91 },
  ]);
  assert.match(html, /Latest closes/);
  assert.match(html, /LTCUSDT/);
  assert.match(html, /\+2\.9%/);
  assert.match(html, /a-up/);
});

// ── the cast, which becomes a public post ─────────────────────────────────

test('the share text names the season and the leader', () => {
  const t = V.shareText(GENESIS, ROWS, { rankedTotal: 2, suffix: 'https://x.test/embed/arena' });
  assert.match(t, /Genesis/);
  assert.match(t, /RUNECLAW leads at \+6\.52%/);
  assert.match(t, /2 traders/);
  assert.match(t, /x\.test/);
});

test('a leader whose return is unreadable is not published with a number', () => {
  // This text goes onto somebody's timeline permanently. "leads at +0.00%"
  // assembled from a missing column is a claim they did not make.
  const t = V.shareText(GENESIS, [{ rank: 1, handle: 'Ghost', return_pct: null }], {});
  assert.match(t, /Ghost leads/);
  assert.ok(!t.includes('0.00'), 'an absent return was published as zero');
  assert.ok(!t.includes('—'), 'an em dash leaked into a cast');
});

test('an empty board produces a cast that claims no standings', () => {
  const t = V.shareText(GENESIS, [], { rankedTotal: 0 });
  assert.match(t, /Genesis/);
  assert.ok(!/leads/.test(t), 'a cast named a leader on an empty board');
  assert.ok(!/0 traders/.test(t), 'a cast advertised zero participants');
});
