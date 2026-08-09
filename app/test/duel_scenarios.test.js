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
  say(html, ['ETHUSDT', 'You called LONG', 'The Claw called SHORT', 'Running', 'tone-muted'],
    ['tone-up', 'tone-down', 'Correct', 'Wrong']);
});

test('a settled win says it was a win, and says it beat the agent', () => {
  const html = D.roundCard({
    id: 4, symbol: 'SOLUSDT', callable: true, agent_direction: 'short',
    my_call: { pick: 'long', outcome: 'up', move_pct: 3.2 },
    my_result: { state: 'correct', marks: 2, beat_agent: true },
  });
  say(html, ['You called LONG', 'beat the Claw', 'tone-up'], ['tone-down', 'Wrong']);
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
