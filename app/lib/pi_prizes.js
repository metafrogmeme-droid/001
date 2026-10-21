'use strict';
/**
 * Season prizes in Pi: the prize table and the payout plan. OFF unless the
 * operator turns it on, and it HOLDS NO KEY.
 *
 * WHAT THIS IS. A free contest: the top names on a season's board with enough
 * closed trades are paid in Pi after the season ends. Entry is never paid and
 * nothing is staked, so no player can lose anything by playing.
 *
 * WHAT THIS IS NOT. A payer. Paying a Pioneer from the app (Pi's App-to-User
 * payment) needs the app wallet's private seed on our server, which no code
 * here reads and no environment key names. The plan says who is owed what;
 * the payment is the operator's, outside this code, until the owner decides
 * otherwise (docs/hackathon/SEASON_PRIZES_PLAN.md).
 *
 * THE SWITCH. Off unless PI_SEASON_PRIZES_ENABLED is exactly "1" AND
 * PI_SEASON_PRIZES is a table that reads. A table that does not read turns
 * the prizes off, with the reason for the operator: a typo must not announce
 * a different prize.
 *
 *   PI_SEASON_PRIZES             "50,30,20": Pi for 1st, 2nd, 3rd (at most 10
 *                                places, each above 0 and at most 10000 Pi,
 *                                up to 7 decimals, never rising down the table)
 *   PI_SEASON_PRIZES_MIN_TRADES  closed trades a name needs to place (default 5)
 *
 * THE PLAN IS READ FROM THE TRADES, not from the board's rows. The board
 * (arena_seasons.seasonRanking) reads an unreadable pnl as 0, which is a fair
 * shortcut for a public ranking and the wrong one for paying people: a row
 * that cannot be read is not break-even. So the plan counts each name's
 * unreadable closes, and a plan where a name in the running has one is not
 * final, with that name held for the operator to look at.
 *
 * TIES SHARE. Names with the same return that span prize places split those
 * places' prizes equally, whatever order the database returned them in.
 */

const START_BALANCE = 10000;
const MAX_PLACES = 10;
const MAX_PRIZE_PI = 10000;
const AMOUNT_RE = /^\d+(\.\d{1,7})?$/;

/** Pi amounts to 7 decimals, as Pi itself counts them. */
function pi7(x) {
  return Math.round(x * 1e7) / 1e7;
}

/**
 * The prize table, or why there is none:
 *   { on: true, places: [50, 30, 20], minTrades: 5, total: 100 }
 *   { on: false, reason: 'off' | 'no_table' | 'bad_amount' | 'too_many_places' | 'rising' }
 */
function schedule(env = process.env) {
  if (String(env.PI_SEASON_PRIZES_ENABLED || '').trim() !== '1') return { on: false, reason: 'off' };
  const raw = String(env.PI_SEASON_PRIZES || '').trim();
  if (!raw) return { on: false, reason: 'no_table' };
  const parts = raw.split(',').map((s) => s.trim());
  if (parts.length > MAX_PLACES) return { on: false, reason: 'too_many_places' };
  const places = [];
  for (const p of parts) {
    if (!AMOUNT_RE.test(p)) return { on: false, reason: 'bad_amount' };
    const n = Number(p);
    if (!(n > 0 && n <= MAX_PRIZE_PI)) return { on: false, reason: 'bad_amount' };
    places.push(n);
  }
  for (let i = 1; i < places.length; i += 1) {
    if (places[i] > places[i - 1]) return { on: false, reason: 'rising' };
  }
  const rawMin = String(env.PI_SEASON_PRIZES_MIN_TRADES || '').trim();
  const min = rawMin === '' ? 5 : Number(rawMin);
  if (!Number.isInteger(min) || min < 1 || min > 1000) return { on: false, reason: 'bad_min_trades' };
  return { on: true, places, minTrades: min, total: pi7(places.reduce((a, b) => a + b, 0)) };
}

/** A pnl that reads as a number, or null. Absent is not zero. */
function readPnl(v) {
  if (v === null || v === undefined || v === '' || typeof v === 'boolean') return null;
  const n = Number(v);
  return Number.isFinite(n) ? n : null;
}

/**
 * The payout plan for one season's closed trades.
 *
 *   trades:   [{ user_id, pnl }]  closes inside the season's window
 *   handleOf: Map user_id -> leaderboard handle (only opted-in names place)
 *   sched:    schedule(env), on
 *
 * Returns { final, places: [{ place, user_id, handle, return_pct, trades, amount }],
 *           held: [{ user_id, handle, unreadable }], unawarded, total }.
 * `final` is false when a name that could place has an unreadable close.
 */
function plan(trades, handleOf, sched) {
  if (!sched || !sched.on) throw new Error('season prizes are off');
  const get = (id) => (handleOf && typeof handleOf.get === 'function' ? handleOf.get(id) : (handleOf || {})[id]);
  const byUser = new Map();
  for (const t of trades || []) {
    if (!t || t.user_id === null || t.user_id === undefined) continue;
    const u = byUser.get(t.user_id) || { pnl: 0, n: 0, unreadable: 0 };
    const p = readPnl(t.pnl);
    if (p === null) u.unreadable += 1;
    else u.pnl += p;
    u.n += 1;
    byUser.set(t.user_id, u);
  }
  const rows = [];
  for (const [userId, u] of byUser) {
    const handle = get(userId);
    if (!handle || u.n < sched.minTrades) continue;
    rows.push({ user_id: userId, handle, trades: u.n, unreadable: u.unreadable,
      return_pct: Math.round(u.pnl / START_BALANCE * 10000) / 100 });
  }
  // Return first; equal returns by handle, so the order never depends on the
  // database's. Ties then share, below.
  rows.sort((a, b) => (b.return_pct - a.return_pct) || String(a.handle).localeCompare(String(b.handle)));

  const places = [];
  const held = [];
  const n = sched.places.length;
  let i = 0;
  while (i < rows.length && i < n) {
    let j = i;
    while (j + 1 < rows.length && rows[j + 1].return_pct === rows[i].return_pct) j += 1;
    // The group i..j occupies places i..j; those inside the table carry prizes.
    let pot = 0;
    for (let k = i; k <= j && k < n; k += 1) pot += sched.places[k];
    const share = pi7(pot / (j - i + 1));
    for (let k = i; k <= j; k += 1) {
      const r = rows[k];
      places.push({ place: i + 1, user_id: r.user_id, handle: r.handle, return_pct: r.return_pct,
        trades: r.trades, amount: share });
      if (r.unreadable) held.push({ user_id: r.user_id, handle: r.handle, unreadable: r.unreadable });
    }
    i = j + 1;
  }
  // A name just outside the paid places with an unreadable close might be
  // inside them once it is read: that also holds the plan.
  if (i < rows.length && rows[i].unreadable) {
    held.push({ user_id: rows[i].user_id, handle: rows[i].handle, unreadable: rows[i].unreadable });
  }
  const awarded = pi7(places.reduce((a, p) => a + p.amount, 0));
  return { final: held.length === 0, places, held, total: sched.total, unawarded: pi7(sched.total - awarded) };
}

module.exports = { schedule, plan, MAX_PLACES, MAX_PRIZE_PI };
