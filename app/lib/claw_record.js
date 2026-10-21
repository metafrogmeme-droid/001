'use strict';
/**
 * The Claw Record: the agent's own calls, counted only where the record can
 * prove it was made before the market answered.
 *
 * WHAT COUNTS. A call (a row of `signals`, the engine's own) counts when it
 * was sealed (lib/callseal.js, at the moment it reached this server) and the
 * seal is not later than its outcome. A call sealed after it resolved proves
 * nothing about foresight and is reported as such, apart; so is a call with
 * no seal at all (rows from before sealing began). `/api/signals/stats` counts
 * every resolved row; this record counts only these. Every sealed call is a
 * leaf of its day's Merkle root (lib/seal_roots.js), so the anchors on
 * /roots date the whole set.
 *
 * WHAT IS SCORED. A resolved call's own R (`pnl`, written by the bot's
 * outcome walk: +reward/risk at the target, -1 at the stop): a win above
 * zero, a loss below, flat at zero. A call with no outcome (live, expired,
 * ambiguous) is not a loss and not in the rate: it is counted under its own
 * status word.
 *
 * NO VERDICT BELOW THE FLOOR. Under SAMPLE_FLOOR resolved sealed calls the
 * rate is shown and marked too few to judge. A power analysis for AI
 * forecasting agents put the sample needed to detect a 2-point edge near
 * 350 resolved predictions (Foresight Arena, arXiv, May 2026); a record that
 * calls itself a track record on fewer is making a claim its own sample
 * cannot support.
 *
 * PUBLIC, SO: counts, percent and R only, no amount of any currency; a live
 * call is counted, never shown (its side is a member's, lib/signal_access.js);
 * a resolved call is shown in full with its receipt.
 *
 * Pure: rows in, record out. routes/claw_record.js reads the rows.
 */

const { signalOutcome } = require('./public_signal');

const SAMPLE_FLOOR = 350;
const WINDOWS = Object.freeze([7, 30, 90, 365]);
const DEFAULT_WINDOW = 30;
const RECENT = 50;

const ms = (v) => (v == null ? NaN : new Date(v).getTime());
const dayOf = (t) => new Date(t).toISOString().slice(0, 10);

/**
 * Whether a call can be counted: 'sealed' (sealed, and not after its
 * outcome), 'unsealed', or 'sealed_after_outcome'. A seal time that cannot
 * be read is no seal.
 */
function eligibility(row) {
  const sealedAt = ms(row && row.sealed_at);
  if (!row || !row.seal || !Number.isFinite(sealedAt)) return 'unsealed';
  const resolvedAt = ms(row.resolved_at);
  if (Number.isFinite(resolvedAt) && sealedAt > resolvedAt) return 'sealed_after_outcome';
  return 'sealed';
}

/** The window asked for, from the list; anything else is the default. */
function windowOf(raw) {
  const n = Number(raw);
  return WINDOWS.includes(n) ? n : DEFAULT_WINDOW;
}

const r2 = (x) => Math.round(x * 100) / 100;

/**
 * The record over `rows` (signals created in the window, newest first or
 * not), as of `now`.
 */
function computeClawRecord(rows, { now = new Date(), windowDays = DEFAULT_WINDOW } = {}) {
  const since = ms(now) - windowDays * 86400000;
  const inWindow = (rows || []).filter((r) => Number.isFinite(ms(r.created_at)) && ms(r.created_at) >= since);
  const excluded = { unsealed: 0, sealed_after_outcome: 0 };
  const counted = [];
  for (const r of inWindow) {
    const e = eligibility(r);
    if (e === 'sealed') counted.push(r);
    else excluded[e] += 1;
  }

  let wins = 0; let losses = 0; let flat = 0; let sumR = 0;
  const noOutcome = {};          // status word -> count, for calls with no outcome
  const days = new Map();
  const resolvedRows = [];
  for (const r of counted) {
    const d = dayOf(ms(r.created_at));
    if (!days.has(d)) days.set(d, { day: d, sealed: 0, resolved: 0, wins: 0, losses: 0, flat: 0 });
    const day = days.get(d);
    day.sealed += 1;
    const o = signalOutcome(r.pnl);
    if (o === null) {
      const word = String(r.status || 'unknown').toLowerCase();
      noOutcome[word] = (noOutcome[word] || 0) + 1;
      continue;
    }
    day.resolved += 1;
    resolvedRows.push(r);
    sumR += Number(r.pnl);
    if (o === 'WIN') { wins += 1; day.wins += 1; } else if (o === 'LOSS') { losses += 1; day.losses += 1; } else { flat += 1; day.flat += 1; }
  }
  const resolved = wins + losses + flat;

  const recent = resolvedRows
    .sort((a, b) => ms(b.resolved_at || b.created_at) - ms(a.resolved_at || a.created_at))
    .slice(0, RECENT)
    .map((r) => ({
      key: String(r.signal_key),
      symbol: String(r.symbol || ''),
      direction: String(r.direction || '').toLowerCase(),
      status: String(r.status || ''),
      outcome: signalOutcome(r.pnl).toLowerCase(),
      r: r2(Number(r.pnl)),
      created_at: new Date(ms(r.created_at)).toISOString(),
      sealed_at: new Date(ms(r.sealed_at)).toISOString(),
      resolved_at: Number.isFinite(ms(r.resolved_at)) ? new Date(ms(r.resolved_at)).toISOString() : null,
      receipt: `/call/${encodeURIComponent(String(r.signal_key))}`,
    }));

  return {
    as_of: new Date(ms(now)).toISOString(),
    window_days: windowDays,
    windows: WINDOWS.slice(),
    floor: SAMPLE_FLOOR,
    // 'too_few' below the floor: the numbers are shown, the verdict is not.
    verdict: resolved >= SAMPLE_FLOOR ? 'measured' : 'too_few',
    sealed_calls: counted.length,
    resolved,
    wins,
    losses,
    flat,
    // null over nothing resolved, never 0: "0% of nothing" is not a record.
    win_rate: resolved > 0 ? Math.round((wins / resolved) * 1000) / 10 : null,
    avg_r: resolved > 0 ? r2(sumR / resolved) : null,
    r_basis: 'gross',
    no_outcome: noOutcome,
    excluded,
    days: [...days.values()].sort((a, b) => (a.day < b.day ? 1 : -1)),
    recent,
    counts_only: true,
  };
}

module.exports = { computeClawRecord, eligibility, windowOf, SAMPLE_FLOOR, WINDOWS, DEFAULT_WINDOW, RECENT };
