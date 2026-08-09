'use strict';
/**
 * Duel standings and Squads — the public read of the Daily Duel.
 *
 * A SQUAD is a player plus everyone who signed up on their referral code. The
 * referral graph already existed (users.referred_by, auth.js) and pointed at
 * nothing a user could see; this gives it something to be. Inviting people is
 * how a squad gets big enough to rank, which is the growth loop — and it costs
 * nothing and promises nothing, because a squad's standing is made of the same
 * counts and percentages as everything else here.
 *
 * Pure: no I/O. The route loads rows, this decides what they mean.
 *
 * §4: handles, percentages and counts only. No email, no user id, no amount of
 * any currency reaches a caller.
 *
 * HONESTY: a record too short to read is not a bad record. Players and squads
 * below the readability floor are counted and reported as "warming up" — never
 * ranked at 0%, and never quietly dropped, since a board that silently omits
 * them would misreport how many people are playing.
 */

const { accuracy, computeMarks } = require('./duel');

/** Calls that must have resolved before a rate means anything. Mirrors
 *  arena_discipline's MIN_CLOSES: the same argument, about the same problem. */
const MIN_SCORED = 5;

/**
 * One player's public standing. `accuracy_pct` is null until the floor is met,
 * and null is rendered as "warming up" — not as zero.
 */
function playerStanding(handle, entries) {
  const acc = accuracy(entries);
  const marks = computeMarks(entries);
  const readable = acc.scored >= MIN_SCORED;
  return {
    handle,
    accuracy_pct: readable ? acc.pct : null,
    readable,
    calls: acc.scored + acc.push,
    correct: acc.correct,
    beat_agent: marks.beat_agent,
    marks: marks.marks,
    pending: marks.pending,
    unresolved: acc.unresolved,
  };
}

/**
 * The board: readable players ranked, everyone else counted.
 *
 * Ranked on accuracy first and marks second, so volume alone cannot buy a
 * position — but marks break ties, so between two equally accurate players the
 * one who called more, and beat the agent more, is ahead.
 */
function buildBoard(standings, { limit = 50 } = {}) {
  const readable = standings.filter((s) => s.readable);
  const warming = standings.filter((s) => !s.readable);
  readable.sort((a, b) => (b.accuracy_pct - a.accuracy_pct) || (b.marks - a.marks)
    || String(a.handle).localeCompare(String(b.handle)));
  return {
    rows: readable.slice(0, limit).map((s, i) => ({ rank: i + 1, ...s })),
    ranked_total: readable.length,
    // Reported rather than dropped: "12 players ranked" alongside "and 30 more
    // still warming up" is the truth; "12 players" on its own is not.
    warming_up: warming.length,
    min_calls: MIN_SCORED,
  };
}

/**
 * Squads, built from the referral graph.
 *
 * `members` is every opted-in player as { id, handle, referred_by }, and
 * `standingOf` answers a player's standing by id. A squad is listed only when
 * its captain has opted in, because otherwise there is no name to put on it.
 *
 * The squad's rate is the mean of its READABLE members' rates. A squad none of
 * whose members has played enough answers null — it is not yet readable, which
 * is a different thing from being bad at this.
 */
function buildSquads(members, standingOf, { limit = 50 } = {}) {
  const byId = new Map();
  for (const m of members || []) if (m && m.id != null) byId.set(String(m.id), m);

  // Who recruited whom, among players actually on the board. A referrer who
  // never opted in is not a captain anybody can be listed under, so their
  // recruits fall back to standing on their own rather than vanishing.
  const recruits = new Map();
  const captainKeyOf = (m) => {
    const ref = m.referred_by == null ? null : String(m.referred_by);
    return ref && byId.has(ref) && ref !== String(m.id) ? ref : null;
  };
  for (const m of byId.values()) {
    const cap = captainKeyOf(m);
    if (!cap) continue;
    if (!recruits.has(cap)) recruits.set(cap, []);
    recruits.get(cap).push(m);
  }

  // A squad exists for a player who has recruited anybody, and for a player
  // who joined on nobody's code (a squad of one, until they invite someone).
  // A recruit who has not recruited in turn is listed in their captain's
  // squad and nowhere else — otherwise every player would also appear as a
  // squad of one and the table would be mostly noise.
  const squads = [];
  for (const m of byId.values()) {
    const mine = recruits.get(String(m.id)) || [];
    if (!mine.length && captainKeyOf(m)) continue;
    squads.push({ captain: m, members: [m, ...mine] });
  }

  const rows = [];
  for (const { captain, members: mem } of squads) {
    const standings = mem.map((m) => standingOf(m.id)).filter(Boolean);
    const readable = standings.filter((s) => s.readable);
    const mean = readable.length
      ? Math.round((readable.reduce((a, s) => a + s.accuracy_pct, 0) / readable.length) * 10) / 10
      : null;
    rows.push({
      captain: captain.handle,
      size: mem.length,
      readable_members: readable.length,
      accuracy_pct: mean,                     // null = not yet readable
      marks: standings.reduce((a, s) => a + s.marks, 0),
      beat_agent: standings.reduce((a, s) => a + s.beat_agent, 0),
    });
  }

  const ranked = rows.filter((r) => r.accuracy_pct !== null);
  const warming = rows.filter((r) => r.accuracy_pct === null);
  ranked.sort((a, b) => (b.accuracy_pct - a.accuracy_pct) || (b.marks - a.marks)
    || (b.size - a.size) || String(a.captain).localeCompare(String(b.captain)));
  return {
    rows: ranked.slice(0, limit).map((r, i) => ({ rank: i + 1, ...r })),
    ranked_total: ranked.length,
    warming_up: warming.length,
    min_calls: MIN_SCORED,
  };
}

module.exports = { MIN_SCORED, playerStanding, buildBoard, buildSquads };
