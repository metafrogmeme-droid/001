'use strict';
/**
 * What the Duel page SAYS, for planted states.
 *
 * Same pattern as engine_status_scenarios.test.js: plant a state, assert
 * MUST_SAY and MUST_NOT_SAY, and include a RED HERRING — something true but
 * misleading that a careless reading would seize on.
 *
 * The herring here is the streak. A player can have a long, entirely genuine
 * streak while not one of their calls has settled, because a streak counts
 * showing up and accuracy counts being right. Reading the streak as evidence
 * of a good record is exactly the mistake the page must not make on the
 * player's behalf.
 */

const test = require('node:test');
const assert = require('node:assert');
const D = require('../public/js/duel');

function say(html, phrases, forbidden) {
  for (const p of phrases) {
    assert.ok(html.includes(p), `expected the card to say "${p}"\n---\n${html}`);
  }
  for (const p of forbidden || []) {
    assert.ok(!html.includes(p), `the card must NOT say "${p}"\n---\n${html}`);
  }
}

// ── the four states of a call ───────────────────────────────────────────────

test('a running call says it is running, in a colour that claims nothing', () => {
  const s = D.callState({ pending: true, state: 'unresolved' });
  assert.equal(s.tone, 'muted', 'a call in flight must not be painted as a result');
  assert.equal(s.known, false);
  assert.match(s.text, /running/i);
});

test('an unresolved call says WHY, and is never a loss', () => {
  const s = D.callState({ state: 'unresolved', unresolved: true });
  assert.equal(s.tone, 'muted');
  assert.equal(s.known, false);
  assert.match(s.text, /unresolved/i);
  assert.match(s.note, /not counted/i);
  assert.ok(!/wrong/i.test(s.text), 'a price we could not read did not make anyone wrong');
});

test('a push says no result rather than picking a side', () => {
  const s = D.callState({ state: 'push' });
  assert.equal(s.tone, 'muted');
  assert.equal(s.known, true);
  assert.ok(!/wrong/i.test(s.text) && !/correct/i.test(s.text));
});

test('only a real result gets a colour', () => {
  assert.equal(D.callState({ state: 'correct' }).tone, 'up');
  assert.equal(D.callState({ state: 'wrong' }).tone, 'down');
  assert.equal(D.callState({ state: 'correct', beat_agent: true }).tone, 'up');
  assert.match(D.callState({ state: 'correct', beat_agent: true }).text, /beat the claw/i);
});

// ── the record headline ─────────────────────────────────────────────────────

test('no resolved calls shows a dash and an explanation — never 0%', () => {
  const a = D.accuracyLabel({ pct: null, scored: 0, correct: 0, unresolved: 0 });
  assert.equal(a.text, '—');
  assert.equal(a.tone, 'muted');
  assert.ok(!a.text.includes('0'), '0% would report a measured failure where there is none');
  assert.match(a.sub, /no calls have resolved/i);
});

test('a day where everything failed to settle says so, and stays uncoloured', () => {
  // THE CASE THIS FILE EXISTS FOR: three calls, three dead settles.
  const a = D.accuracyLabel({ pct: null, scored: 0, correct: 0, unresolved: 3 });
  assert.equal(a.text, '—');
  assert.equal(a.tone, 'muted', 'a red stripe here would blame the player for an outage');
  assert.match(a.sub, /nothing has settled/i);
});

test('a real rate is coloured by what it actually says', () => {
  assert.equal(D.accuracyLabel({ pct: 80, scored: 5 }).tone, 'up');
  assert.equal(D.accuracyLabel({ pct: 20, scored: 5 }).tone, 'down');
  assert.match(D.accuracyLabel({ pct: 80, scored: 5 }).sub, /5 settled/);
});

test('a partial marks total travels with what it could not include', () => {
  const m = D.marksLabel({ marks: 7, pending: 2 });
  assert.equal(m.text, '7');
  assert.match(m.sub, /2 still running/,
    '"7 marks" alone is a sum over a set that quietly excluded two rows');

  const done = D.marksLabel({ marks: 7, pending: 0 });
  assert.equal(done.sub, '', 'nothing outstanding, nothing to qualify');
});

// ── the agent's stance ──────────────────────────────────────────────────────

test('hidden, passed and called are three different sentences', () => {
  const hidden = D.agentLabel({ symbol: 'BTCUSDT' });          // field absent
  assert.equal(hidden.revealed, false);
  assert.match(hidden.text, /hidden until you call/i);

  const passed = D.agentLabel({ agent_direction: null });      // field present, null
  assert.equal(passed.revealed, true);
  assert.match(passed.text, /passed/i);
  // The distinction the API preserves by omitting rather than nulling: if
  // these two read the same, a player cannot tell "we are not telling you"
  // from "there was nothing to tell".
  assert.notEqual(hidden.text, passed.text);

  assert.match(D.agentLabel({ agent_direction: 'long' }).text, /LONG/);
  assert.match(D.agentLabel({ agent_direction: 'short' }).text, /SHORT/);
});

// ── whole cards ─────────────────────────────────────────────────────────────

test('an uncalled round offers the three calls and hides the stance', () => {
  const html = D.roundCard({ id: 4, symbol: 'BTCUSDT', callable: true });
  say(html, ['BTCUSDT', 'LONG', 'SHORT', 'PASS', 'Hidden until you call'], ['tone-up', 'tone-down']);
});

test('a called round shows the call, the stance, and no result yet', () => {
  const html = D.roundCard({
    id: 4, symbol: 'ETHUSDT', callable: true, agent_direction: 'short',
    my_call: { pick: 'long', pending: true },
    my_result: { state: 'unresolved', marks: 0, beat_agent: false },
  });
  say(html, ['ETHUSDT', 'd-pill--you', '>LONG<', 'd-pill--claw', '>SHORT<', 'Running', 'tone-muted'],
    ['tone-up', 'tone-down', 'Correct', 'Wrong']);
});

test('a settled win says it was a win, and says it beat the agent', () => {
  const html = D.roundCard({
    id: 4, symbol: 'SOLUSDT', callable: true, agent_direction: 'short',
    my_call: { pick: 'long', outcome: 'up', move_pct: 3.2 },
    my_result: { state: 'correct', marks: 2, beat_agent: true },
  });
  say(html, ['d-pill--you', '>LONG<', 'beat the Claw', 'tone-up'], ['tone-down', 'Wrong']);
});

test('THE RED HERRING: a long streak with nothing settled must not read as a good record', () => {
  // Everything about this player is true and encouraging: they have called
  // every round for six days running. Not one call has settled. A page that
  // let the streak stand in for a record would show a green 0% or, worse, a
  // green rate computed over nothing.
  const acc = D.accuracyLabel({ pct: null, scored: 0, correct: 0, unresolved: 18 });
  const marks = D.marksLabel({ marks: 0, pending: 18 });
  const card = D.roundCard({
    id: 9, symbol: 'BTCUSDT', callable: true, agent_direction: 'long',
    my_call: { pick: 'long', pending: true },
    my_result: { state: 'unresolved', marks: 0, beat_agent: false },
  });

  assert.equal(acc.text, '—');
  assert.equal(acc.tone, 'muted');
  assert.equal(marks.text, '0');
  assert.match(marks.sub, /18 still running/,
    'a zero total with nothing pending would claim a measured zero');
  say(card, ['tone-muted', 'Running'], ['tone-up', 'tone-down', 'Correct', 'Wrong']);

  // And the streak is still allowed to be what it is — a count of days the
  // player showed up. It just may not colour the record.
  assert.equal(acc.tone, 'muted');
});

test('every user-visible string can be translated', () => {
  // The renderer never bakes English in: the page passes RCI18N through, and
  // a missing key falls back to the English the caller supplied.
  const t = (k) => 'XX' + k;
  const html = D.roundCard({ id: 1, symbol: 'BTCUSDT', callable: true }, t);
  assert.ok(html.includes('XXduel.b_long'));
  assert.ok(html.includes('XXduel.ag_hidden'));
  assert.equal(D.accuracyLabel({ pct: null, scored: 0 }, t).sub, 'XXduel.acc_none');
  assert.equal(D.callState({ state: 'wrong' }, t).text, 'XXduel.st_wrong');
});

test('symbols and picks are escaped on the way into the card', () => {
  const html = D.roundCard({ id: 1, symbol: '<img src=x onerror=alert(1)>', callable: true });
  assert.ok(!html.includes('<img'), 'a symbol is data, not markup');
  assert.ok(html.includes('&lt;img'));
});

// ── the categories ──────────────────────────────────────────────────────────

test('each category asks its question in words and offers the round\'s own options', () => {
  const direction = D.roundCard({ id: 1, kind: 'direction', symbol: 'BTCUSDT', callable: true, horizon_hours: 24,
    options: ['long', 'short', 'pass'] });
  say(direction, ['Direction', 'Higher or lower in 24h?', 'Hidden until you call', 'data-pick="long"', 'data-pick="short"', 'data-pick="pass"']);

  const backfade = D.roundCard({ id: 2, kind: 'backfade', symbol: 'SOLUSDT', callable: true, agent_direction: 'short',
    options: ['back', 'fade', 'pass'] });
  say(backfade, ['Back or fade', 'The Claw called SHORT on SOL. Back it, or fade it?', 'BACK THE CLAW', 'FADE THE CLAW', 'data-pick="fade"'],
    ['Hidden until you call', 'data-pick="long"']);

  const range = D.roundCard({ id: 3, kind: 'range', symbol: 'BNBUSDT', callable: true, threshold_pct: 2, horizon_hours: 24,
    options: ['break', 'hold', 'pass'] });
  say(range, ['Range', 'Does BNB move more than ±2% either way in 24h?', 'BREAKS ±2%', 'HOLDS ±2%'],
    ['Hidden until you call', 'The Claw passed', 'data-pick="long"']);

  const mover = D.roundCard({ id: 4, kind: 'mover', symbol: 'XRPUSDT', symbol_b: 'ARBUSDT', callable: true, horizon_hours: 24,
    options: ['first', 'second', 'pass'] });
  say(mover, ['Bigger mover', 'XRP', 'ARB', 'which moves more in 24h', 'XRP MOVES MORE', 'ARB MOVES MORE', 'data-pick="first"', 'data-pick="second"'],
    ['Hidden until you call', 'The Claw passed']);
});

test('the buttons are the round\'s options in the round\'s order, never a word the server would refuse', () => {
  const html = D.roundCard({ id: 3, kind: 'range', symbol: 'BNBUSDT', callable: true, threshold_pct: 4,
    options: ['hold', 'break', 'pass'] });
  const picks = [...html.matchAll(/data-pick="([a-z]+)"/g)].map((m) => m[1]);
  assert.deepEqual(picks, ['hold', 'break', 'pass']);
  // A round without an options list, of the classic kind, offers the classic three.
  const classic = D.roundCard({ id: 1, symbol: 'BTCUSDT', callable: true });
  assert.deepEqual([...classic.matchAll(/data-pick="([a-z]+)"/g)].map((m) => m[1]), ['long', 'short', 'pass']);
});

test('a round of a kind this page does not know offers nothing and says so', () => {
  const html = D.roundCard({ id: 9, kind: 'weather', symbol: 'BTCUSDT', callable: true, options: ['rain', 'sun', 'pass'] });
  say(html, ['Unknown round type', 'cannot show'], ['data-pick=', '<button']);
});

test('a called round reports the call in the words of its category', () => {
  const mover = D.roundCard({ id: 4, kind: 'mover', symbol: 'XRPUSDT', symbol_b: 'ARBUSDT', callable: true,
    options: ['first', 'second', 'pass'],
    my_call: { pick: 'second', outcome: 'second', move_pct: 1, move_pct_b: 10 },
    my_result: { state: 'correct', marks: 1, beat_agent: false } });
  say(mover, ['d-pill--you', '>ARB MOVES MORE<', 'Correct', 'tone-up'], ['SECOND', 'tone-down']);
  const range = D.roundCard({ id: 3, kind: 'range', symbol: 'BNBUSDT', callable: true, threshold_pct: 2,
    options: ['break', 'hold', 'pass'],
    my_call: { pick: 'hold', pending: true },
    my_result: { state: 'unresolved', marks: 0, beat_agent: false } });
  say(range, ['d-pill--you', '>HOLDS ±2%<', 'Running', 'tone-muted'], ['tone-up', 'tone-down']);
  const fade = D.roundCard({ id: 2, kind: 'backfade', symbol: 'SOLUSDT', callable: true, agent_direction: 'short',
    options: ['back', 'fade', 'pass'],
    my_call: { pick: 'fade', outcome: 'up', move_pct: 3 },
    my_result: { state: 'correct', marks: 2, beat_agent: true } });
  say(fade, ['d-pill--you', '>FADE THE CLAW<', 'beat the Claw', 'tone-up']);
});

test('the category strings can all be translated, placeholders intact', () => {
  const t = (k, en) => 'XX' + k + '|' + en;
  const seen = (html) => [...html.matchAll(/XX(duel\.[a-z_]+)\|/g)].map((m) => m[1]);
  const keys = new Set();
  for (const r of [
    { id: 1, kind: 'direction', symbol: 'BTCUSDT', callable: true, options: ['long', 'short', 'pass'] },
    { id: 2, kind: 'backfade', symbol: 'SOLUSDT', callable: true, agent_direction: 'long', options: ['back', 'fade', 'pass'] },
    { id: 3, kind: 'range', symbol: 'BNBUSDT', callable: true, threshold_pct: 2, options: ['break', 'hold', 'pass'] },
    { id: 4, kind: 'mover', symbol: 'XRPUSDT', symbol_b: 'ARBUSDT', callable: true, options: ['first', 'second', 'pass'] },
    { id: 5, kind: 'weather', symbol: 'BTCUSDT', callable: true, options: [] },
  ]) seen(D.roundCard(r, t)).forEach((k) => keys.add(k));
  for (const k of ['duel.k_direction', 'duel.k_backfade', 'duel.k_range', 'duel.k_mover', 'duel.k_unknown',
    'duel.q_direction', 'duel.q_backfade', 'duel.q_range', 'duel.q_mover', 'duel.q_unknown',
    'duel.b_back', 'duel.b_fade', 'duel.b_break', 'duel.b_hold', 'duel.b_more', 'duel.vs']) {
    assert.ok(keys.has(k), `${k} is baked in English`);
  }
  // Every key the renderer asks for is in the dictionary, in every language.
  const i18n = require('../public/js/i18n.js');
  for (const k of keys) {
    assert.ok(i18n.STRINGS[k], `${k} missing from the dictionary`);
    for (const l of i18n.LANGS) assert.ok(i18n.STRINGS[k][l.code], `${k} is missing ${l.code}`);
  }
});

test('pickLabel and questionText fill the band and the symbols, and never a dash for a band that is there', () => {
  const range = { kind: 'range', symbol: 'DOGEUSDT', threshold_pct: 4, horizon_hours: 24 };
  assert.equal(D.pickLabel('break', range), 'BREAKS ±4%');
  assert.equal(D.pickLabel('hold', { kind: 'range', symbol: 'X' }), 'HOLDS —', 'a round with no band says so');
  assert.equal(D.pickLabel('first', { symbol: 'XRPUSDT', symbol_b: 'ARBUSDT' }), 'XRP MOVES MORE');
  assert.equal(D.pickLabel('second', { symbol: 'XRPUSDT', symbol_b: 'ARBUSDT' }), 'ARB MOVES MORE');
  assert.equal(D.pickLabel('pass', {}), 'PASS');
  assert.equal(D.questionText(range), 'Does DOGE move more than ±4% either way in 24h?');
  assert.equal(D.questionText({ kind: 'backfade', symbol: 'SOLUSDT', agent_direction: 'long' }),
    'The Claw called LONG on SOL. Back it, or fade it?');
  assert.equal(D.kindLabel('mover'), 'Bigger mover');
  assert.equal(D.kindLabel('weather'), 'Unknown round type');
  assert.equal(D.base('BTCUSDT'), 'BTC');
  assert.equal(D.base('PI'), 'PI');
});

// ── football (2026-10-19) ───────────────────────────────────────────────────

const MATCH_ROUND = { id: 7, kind: 'sports', symbol: 'FD:441', callable: true, options: ['home', 'draw', 'away', 'pass'],
  match: { home: 'Arsenal FC', away: 'Chelsea FC', home_short: 'Arsenal', away_short: 'Chelsea',
    competition: 'Premier League', kickoff: '2026-10-19T19:00:00.000Z' } };

test('a football round asks who wins, offers both teams and the draw, and never a stance', () => {
  const html = D.roundCard(MATCH_ROUND);
  say(html, ['Football', 'Arsenal', 'Chelsea', 'who wins?', 'Premier League', 'kick-off',
    'data-pick="home"', 'data-pick="draw"', 'data-pick="away"', 'data-pick="pass"', '>ARSENAL<', '>DRAW<', '>CHELSEA<'],
  ['Hidden until you call', 'The Claw passed', 'data-pick="long"', 'FD:441']);
  // Closed at kick-off: its own sentence.
  say(D.roundCard({ ...MATCH_ROUND, callable: false }), ['Kick-off has passed'], ['data-pick=', 'This round has closed.']);
});

test('a settled football call reports the final score, and a running one no score at all', () => {
  const won = D.roundCard({ ...MATCH_ROUND, callable: false,
    my_call: { pick: 'draw', entry_price: null, outcome: 'draw', score: { home: 1, away: 1 } },
    my_result: { state: 'correct', marks: 1, beat_agent: false } });
  say(won, ['d-pill--you', '>DRAW<', 'Correct', 'Final score 1–1', 'tone-up'], ['tone-down', 'MOVES MORE']);
  const running = D.roundCard({ ...MATCH_ROUND,
    my_call: { pick: 'home', entry_price: null, pending: true },
    my_result: { state: 'unresolved', marks: 0, beat_agent: false } });
  // A running match settles on its final score, not at a clock time, so its pill says so.
  say(running, ['d-pill--you', '>ARSENAL<', 'Settles after full time', 'tone-muted'], ['Final score', 'tone-up', 'tone-down', 'Settles in']);
  // A goalless draw is a score, not an absence.
  say(D.roundCard({ ...MATCH_ROUND, callable: false,
    my_call: { pick: 'away', outcome: 'draw', score: { home: 0, away: 0 } },
    my_result: { state: 'wrong', marks: 0, beat_agent: false } }), ['Final score 0–0', 'Wrong']);
});

test('the football strings can be translated, and a team without a short name is still named', () => {
  const t = (k, en) => 'XX' + k + '|' + en;
  const html = D.roundCard(MATCH_ROUND, t);
  for (const k of ['duel.k_sports', 'duel.q_sports', 'duel.b_draw']) assert.ok(html.includes('XX' + k + '|'), k);
  assert.ok(D.roundCard({ ...MATCH_ROUND, callable: false }, t).includes('XXduel.closed_kickoff|'));
  const i18n = require('../public/js/i18n.js');
  for (const k of ['duel.k_sports', 'duel.q_sports', 'duel.b_draw', 'duel.closed_kickoff', 'duel.final_score', 'duel.sports_unreadable']) {
    assert.ok(i18n.STRINGS[k], `${k} missing from the dictionary`);
    for (const l of i18n.LANGS) assert.ok(i18n.STRINGS[k][l.code], `${k} is missing ${l.code}`);
  }
  assert.equal(D.teamText({ home: 'Arsenal FC' }, 'home'), 'Arsenal FC');
  assert.equal(D.teamText({}, 'away'), '—');
  assert.equal(D.pickLabel('home', { match: { home: 'Arsenal FC' } }), 'ARSENAL FC');
  assert.equal(D.kickoffText({ kickoff: 'nonsense' }), '—');
  assert.equal(D.kindLabel('sports'), 'Football');
});

// ── the 2027 card (2026-10-19): status pill, time to settle, two pills ─────

test('a running call says when it settles, from its own horizon, or Running when that cannot be read', () => {
  const now = new Date('2026-10-19T12:00:00Z');
  assert.equal(D.timeLeftText('2026-10-20T09:12:00Z', now), '21h 12m');
  assert.equal(D.timeLeftText('2026-10-19T12:40:00Z', now), '40m');
  assert.equal(D.timeLeftText('2026-10-19T11:00:00Z', now), 'any moment');
  assert.strictEqual(D.timeLeftText('soon', now), null);
  assert.strictEqual(D.timeLeftText(null, now), null);
  assert.equal(D.callState({ pending: true, resolves_at: '2026-10-20T09:12:00Z' }, null, now).text, 'Settles in 21h 12m');
  assert.equal(D.callState({ pending: true }, null, now).text, 'Running');
  assert.equal(D.callState({ pending: true, resolves_at: 'soon' }, null, now).text, 'Running', 'an unreadable horizon is not a time');
  const html = D.roundCard({ id: 1, symbol: 'BTCUSDT', callable: true, agent_direction: 'long',
    my_call: { pick: 'long', pending: true, resolves_at: '2026-10-20T09:12:00Z' },
    my_result: { state: 'unresolved', marks: 0, beat_agent: false } }, null, now);
  say(html, ['Settles in 21h 12m', 'd-round--called', 'tone-muted'], ['Running', 'tone-up', 'tone-down']);
});

test('the status pill: Open on a callable round, Closed on a closed one, none on a kind this page cannot show', () => {
  say(D.roundCard({ id: 1, symbol: 'BTCUSDT', callable: true }), ['d-status', '>Open<', 'd-round--open', 'd-bi'], ['>Closed<']);
  say(D.roundCard({ id: 1, symbol: 'BTCUSDT', callable: false }), ['>Closed<', 'd-round--closed', 'This round has closed.'], ['d-btn', '>Open<']);
  say(D.roundCard({ id: 1, kind: 'later', symbol: 'X', callable: true, options: ['a'] }), ['d-round--unknown', 'Update the app'], ['d-status', 'data-pick']);
});

test('the Claw’s pill sits beside yours only where there was a stance to reveal', () => {
  const bf = D.roundCard({ id: 2, kind: 'backfade', symbol: 'SOLUSDT', callable: true, agent_direction: 'short',
    options: ['back', 'fade', 'pass'], my_call: { pick: 'back', pending: true },
    my_result: { state: 'unresolved', marks: 0, beat_agent: false } });
  say(bf, ['d-pill--you', '>BACK THE CLAW<', 'd-pill--claw', '>SHORT<']);
  // A range round never held a stance; a field that arrives on one anyway
  // (an older server, a hand edit) is not dressed as the Claw's word.
  const range = D.roundCard({ id: 3, kind: 'range', symbol: 'BNBUSDT', callable: true, threshold_pct: 2,
    agent_direction: 'long', options: ['break', 'hold', 'pass'], my_call: { pick: 'hold', pending: true },
    my_result: { state: 'unresolved', marks: 0, beat_agent: false } });
  say(range, ['d-pill--you', '>HOLDS ±2%<'], ['d-pill--claw']);
  // The Claw passed: its pill says PASS, the word a pass has on every surface.
  const passed = D.roundCard({ id: 4, symbol: 'ETHUSDT', callable: true, agent_direction: null,
    my_call: { pick: 'long', pending: true }, my_result: { state: 'unresolved', marks: 0, beat_agent: false } });
  say(passed, ['d-pill--claw', '>PASS<'], ['The Claw passed']);
  // Uncalled: the stance line names the Claw and says it is hidden; no pill yet.
  say(D.roundCard({ id: 5, symbol: 'XRPUSDT', callable: true }), ['d-agent', 'Hidden until you call'], ['d-pill--claw']);
  assert.equal(D.kindIcon('sports'), '⚽');
  assert.equal(D.pickIcon('long'), '▲');
  assert.equal(D.pickIcon('home'), '', 'a team name needs no glyph');
});

// ── the provable duel (2026-10-19): receipts on the card ──────────────────

test('a called round quotes its receipts; the website links them, the Pi app only quotes them', () => {
  const a = 'a'.repeat(64), b = 'b'.repeat(64), c = 'c'.repeat(64);
  const round = { id: 1, symbol: 'BTCUSDT', callable: true, agent_direction: 'short', seal: a,
    my_call: { pick: 'long', pending: true, seal: b, settle_seal: null },
    my_result: { state: 'unresolved', marks: 0, beat_agent: false } };
  const quoted = D.roundCard(round);
  say(quoted, ['d-receipts', 'Receipt bbbbbbbb…', 'Dealt sealed aaaaaaaa…', 'title="' + b + '"'], ['href=', 'Settlement']);
  const linked = D.roundCard(round, null, null, { links: true });
  say(linked, ['href="/call/duel:' + b + '"', 'href="/call/round:' + a + '"'], ['href="/call/settle:']);
  // Settled: the settlement's receipt joins the line.
  const settled = D.roundCard({ ...round, my_call: { pick: 'long', outcome: 'up', move_pct: 2, seal: b, settle_seal: c },
    my_result: { state: 'correct', marks: 1, beat_agent: false } }, null, null, { links: true });
  say(settled, ['Settlement cccccccc…', 'href="/call/settle:' + c + '"']);
  // A round dealt before the seals, or a call without one, quotes nothing and invents nothing.
  say(D.roundCard({ id: 2, symbol: 'ETHUSDT', callable: true, agent_direction: 'long',
    my_call: { pick: 'long', pending: true }, my_result: { state: 'unresolved', marks: 0, beat_agent: false } }),
  ['d-pill--you'], ['d-receipts', 'Receipt', 'Dealt sealed']);
  assert.strictEqual(D.shortSeal('nothex'), null);
  assert.equal(D.shortSeal(a), 'aaaaaaaa…');
});

test('an open classic round shows the commitment beside the hidden stance; a range round does not', () => {
  const a = 'a'.repeat(64);
  const open = D.roundCard({ id: 1, symbol: 'BTCUSDT', callable: true, seal: a });
  say(open, ['Hidden until you call', 'Dealt sealed aaaaaaaa…', 'd-receipts--inline'], ['href=']);
  const openLinked = D.roundCard({ id: 1, symbol: 'BTCUSDT', callable: true, seal: a }, null, null, { links: true });
  say(openLinked, ['href="/call/round:' + a + '"']);
  const range = D.roundCard({ id: 3, kind: 'range', symbol: 'BNBUSDT', callable: true, threshold_pct: 2, seal: a, options: ['break', 'hold', 'pass'] });
  say(range, ['BREAKS ±2%'], ['Dealt sealed', 'd-receipts']);
  const t = (k, en) => 'XX' + k + '|' + en;
  assert.ok(D.roundCard({ id: 1, symbol: 'BTCUSDT', callable: true, seal: a }, t).includes('XXduel.rc_round|'));
  const i18n = require('../public/js/i18n.js');
  for (const k of ['duel.rc_call', 'duel.rc_settle', 'duel.rc_round']) {
    for (const l of i18n.LANGS) assert.ok(i18n.STRINGS[k] && i18n.STRINGS[k][l.code], `${k} is missing ${l.code}`);
  }
});

// ── the 19 Oct review: a match settles after full time, a kick-off names its
// zone and its day, a mark says what it is ───────────────────────────────────

test('a running football call settles after full time, never on a countdown; a market call keeps its clock', () => {
  const now = new Date('2026-10-19T12:00:00Z');
  assert.equal(D.callState({ kind: 'sports', pending: true, resolves_at: '2026-10-19T21:30:00Z' }, null, now).text, 'Settles after full time');
  assert.equal(D.callState({ kind: 'direction', pending: true, resolves_at: '2026-10-20T09:12:00Z' }, null, now).text, 'Settles in 21h 12m');
  const html = D.roundCard({ ...MATCH_ROUND,
    my_call: { pick: 'home', entry_price: null, pending: true, resolves_at: '2026-10-19T21:30:00Z' },
    my_result: { state: 'unresolved', marks: 0, beat_agent: false } }, null, now);
  say(html, ['Settles after full time', 'tone-muted', '>ARSENAL<'], ['Settles in', '>Running<']);
  const t = (k, en) => 'XX' + k + '|' + en;
  assert.ok(D.callState({ kind: 'sports', pending: true }, t).text.startsWith('XXduel.st_fulltime|'));
});

test('a kick-off is printed in the reader’s clock with its zone, and with its day when that is not today', () => {
  const ref = new Date('2026-10-19T12:00:00Z');
  const kickoff = '2026-10-19T19:00:00.000Z';
  const d = new Date(kickoff);
  const hhmm = String(d.getHours()).padStart(2, '0') + ':' + String(d.getMinutes()).padStart(2, '0');
  const text = D.kickoffText({ kickoff }, ref);
  // The clock reading, then a zone name: "19:00 UTC", "21:00 GMT+2".
  assert.match(text, new RegExp('(^|^[A-Z][a-z]{2} )' + hhmm + ' [A-Z][A-Za-z0-9+\\-:]*$'), text);
  // The day appears exactly when the reader's local date differs from the reference day.
  const sameLocalDay = d.getFullYear() === ref.getFullYear() && d.getMonth() === ref.getMonth() && d.getDate() === ref.getDate();
  assert.equal(/^[A-Z][a-z]{2} /.test(text), !sameLocalDay, text);
  // A kick-off a week on always carries its day.
  assert.match(D.kickoffText({ kickoff: '2026-10-25T19:00:00.000Z' }, ref), /^(Sun|Mon|Tue|Wed|Thu|Fri|Sat) \d\d:\d\d /);
  assert.equal(D.kickoffText({ kickoff: 'nonsense' }, ref), '—');
  assert.ok(D.roundCard(MATCH_ROUND, null, ref).includes(hhmm), 'the card prints the kick-off through the same reader');
});

test('the marks figure says what a mark is, beside the count still running', () => {
  const how = '1 per correct call, 2 for beating the Claw';
  assert.deepEqual(D.marksLabel({ marks: 4, pending: 1 }), { text: '4', tone: 'muted', sub: '1 still running', how });
  assert.deepEqual(D.marksLabel({ marks: 0, pending: 0 }), { text: '0', tone: 'muted', sub: '', how });
  assert.equal(D.marksLabel({}).text, '—');
  assert.equal(D.marksLabel({ marks: 2 }, (k, en) => 'XX' + k + '|' + en).how, 'XXduel.marks_how|' + how);
  const i18n = require('../public/js/i18n.js');
  for (const k of ['duel.marks_how', 'duel.st_fulltime']) {
    assert.ok(i18n.STRINGS[k], `${k} missing from the dictionary`);
    for (const l of i18n.LANGS) assert.ok(i18n.STRINGS[k][l.code], `${k} is missing ${l.code}`);
  }
});
