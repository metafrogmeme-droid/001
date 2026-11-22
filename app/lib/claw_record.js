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
 * EXACT OVER ANY WINDOW. The route used to read the window's newest 20,000
 * rows and count them here, so a 90- or 365-day window at ~700 calls a day was
 * a partial count. It now counts in SQL (groupsSql: one row per day,
 * eligibility, outcome and word, with its count and R sum) and folds the
 * groups here, and lists the recent resolved calls with a second, paged query
 * (recentSql). The pure path (`computeClawRecord(rows)`) groups rows the same
 * way in JS and runs the SAME fold, so the two cannot count differently.
 * ELIGIBILITY_SQL is `eligibility()` in SQL; both sit here, side by side.
 *
 * Pure: rows or groups in, record out. routes/claw_record.js reads them.
 */

const { signalOutcome } = require('./public_signal');

const SAMPLE_FLOOR = 350;
const WINDOWS = Object.freeze([7, 30, 90, 365]);
const DEFAULT_WINDOW = 30;
/** Resolved calls listed by default, and the most one page may ask for. */
const RECENT = 50;
const MAX_RECENT = 200;

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

/**
 * `eligibility()` in SQL, for the counts the database makes: the same three
 * answers over the same columns. A seal that is empty or has no time is no
 * seal; a seal after a recorded outcome is after the outcome.
 */
const ELIGIBILITY_SQL = `CASE
    WHEN seal IS NULL OR seal = '' OR sealed_at IS NULL THEN 'unsealed'
    WHEN resolved_at IS NOT NULL AND sealed_at > resolved_at THEN 'sealed_after_outcome'
    ELSE 'sealed' END`;

/**
 * One row per (UTC day, eligibility, outcome, word) over the calls created
 * since `?`, with its count and R sum. The day is the epoch day number,
 * which UNIX_TIMESTAMP reads off a TIMESTAMP column whatever the session time
 * zone. The outcome is the sign of the R (`signalOutcome`); the word is kept
 * only for a call with no outcome, which is all the record says of those.
 * `maxGroups` is inlined (a bound LIMIT is refused): a read that reaches it is
 * reported partial.
 */
function groupsSql(maxGroups) {
  return `SELECT FLOOR(UNIX_TIMESTAMP(created_at) / 86400) AS day_n,
      ${ELIGIBILITY_SQL} AS elig,
      CASE WHEN pnl IS NULL THEN NULL WHEN pnl > 0 THEN 'win' WHEN pnl < 0 THEN 'loss'
           ELSE 'flat' END AS outcome,
      CASE WHEN pnl IS NULL THEN status ELSE NULL END AS word,
      COUNT(*) AS n, SUM(pnl) AS sum_r
    FROM signals WHERE created_at >= ?
    GROUP BY day_n, elig, outcome, word
    LIMIT ${Math.floor(Number(maxGroups))}`;
}

/**
 * A page of the window's resolved, sealed-before-outcome calls, newest
 * resolution first. Many calls resolve on the same bar close, so the order is
 * (resolution time, key) and the cursor is both: `before` alone would skip
 * the rows tied with a page's last one. Asks for `limit + 1` so the caller
 * knows whether there is a next page. A live call has no R and is never here.
 */
function recentSql(limit, cursor) {
  const at = 'COALESCE(resolved_at, created_at)';
  let after = '';
  if (cursor && cursor.key !== null) after = `AND (${at} < ? OR (${at} = ? AND signal_key < ?))`;
  else if (cursor) after = `AND ${at} < ?`;
  return `SELECT signal_key, symbol, direction, status, pnl, created_at, resolved_at, sealed_at
    FROM signals
    WHERE created_at >= ? AND pnl IS NOT NULL AND (${ELIGIBILITY_SQL}) = 'sealed'
      ${after}
    ORDER BY ${at} DESC, signal_key DESC
    LIMIT ${Math.floor(Number(limit)) + 1}`;
}

/** The parameters `recentSql(limit, cursor)` binds, in order. */
function recentParams(since, cursor) {
  if (cursor && cursor.key !== null) return [since, cursor.at, cursor.at, cursor.key];
  if (cursor) return [since, cursor.at];
  return [since];
}

/** The window asked for, from the list; anything else is the default. */
function windowOf(raw) {
  const n = Number(raw);
  return WINDOWS.includes(n) ? n : DEFAULT_WINDOW;
}

/** How many resolved calls a page lists: RECENT unless asked, at most MAX_RECENT. */
function recentLimitOf(raw) {
  if (raw === undefined || raw === null || raw === '') return RECENT;
  const n = Math.floor(Number(raw));
  if (!Number.isFinite(n)) return RECENT;
  return Math.min(MAX_RECENT, Math.max(1, n));
}

/**
 * The page cursor: `{at, key}` from `?before=<resolved_at>` and
 * `?before_key=<signal_key>` (key null when only a time was sent), null when
 * no cursor was sent, and undefined when `before` is not a time, which the
 * route refuses rather than reading as "from the top".
 */
function cursorOf(before, beforeKey) {
  if (before === undefined || before === null || before === '') return null;
  if (typeof before !== 'string') return undefined;
  const t = new Date(before).getTime();
  if (!Number.isFinite(t)) return undefined;
  const key = typeof beforeKey === 'string' && beforeKey !== '' ? beforeKey.slice(0, 128) : null;
  return { at: new Date(t), key };
}

const r2 = (x) => Math.round(x * 100) / 100;

/**
 * Rows grouped the way groupsSql groups them, in JS: the pure path's half of
 * the one fold. Rows outside the window, or with no readable creation time,
 * are left out, as the SQL's WHERE leaves them out.
 */
function groupRows(rows, { now = new Date(), windowDays = DEFAULT_WINDOW } = {}) {
  const since = ms(now) - windowDays * 86400000;
  const groups = new Map();
  for (const r of rows || []) {
    const created = ms(r.created_at);
    if (!Number.isFinite(created) || created < since) continue;
    const o = signalOutcome(r.pnl);
    const g = {
      day: dayOf(created),
      elig: eligibility(r),
      outcome: o === null ? null : o.toLowerCase(),
      word: o === null ? r.status : null,
    };
    const k = JSON.stringify([g.day, g.elig, g.outcome, g.word]);
    if (!groups.has(k)) groups.set(k, { ...g, n: 0, sumR: 0 });
    const into = groups.get(k);
    into.n += 1;
    if (o !== null) into.sumR += Number(r.pnl);
  }
  return [...groups.values()];
}

/** groupsSql's rows in the fold's shape. A count that does not read throws. */
function groupsFromSql(sqlRows) {
  return sqlRows.map((g) => {
    const n = Number(g.n);
    const day = Number(g.day_n);
    if (!Number.isInteger(n) || n < 0 || !Number.isFinite(day)) {
      throw new TypeError('a grouped count did not read');
    }
    return {
      day: dayOf(day * 86400000),
      elig: String(g.elig),
      outcome: g.outcome == null ? null : String(g.outcome),
      word: g.word,
      n,
      sumR: g.sum_r == null ? 0 : Number(g.sum_r),
    };
  });
}

/** One listed resolved call. */
function recentItem(r) {
  return {
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
  };
}

/** Newest resolution first, then the larger key: recentSql's order. */
function byRecent(a, b) {
  const d = ms(b.resolved_at || b.created_at) - ms(a.resolved_at || a.created_at);
  if (d !== 0) return d;
  const ka = String(a.signal_key); const kb = String(b.signal_key);
  if (ka === kb) return 0;
  return ka < kb ? 1 : -1;
}

/**
 * The record over `rows` (signals created in the window, newest first or
 * not), as of `now`: the pure path, grouped in JS and folded by the same fold
 * the route uses for the database's groups.
 */
function computeClawRecord(rows, { now = new Date(), windowDays = DEFAULT_WINDOW, limit = RECENT } = {}) {
  const since = ms(now) - windowDays * 86400000;
  const recent = (rows || [])
    .filter((r) => Number.isFinite(ms(r.created_at)) && ms(r.created_at) >= since
      && eligibility(r) === 'sealed' && signalOutcome(r.pnl) !== null)
    .sort(byRecent)
    .slice(0, limit);
  return recordFromGroups(groupRows(rows, { now, windowDays }), recent, { now, windowDays });
}

/**
 * The record from grouped counts and the recent resolved rows to list.
 * `groups` are `{day, elig, outcome, word, n, sumR}` (groupRows, or
 * groupsFromSql over groupsSql).
 */
function recordFromGroups(groups, recentRows, { now = new Date(), windowDays = DEFAULT_WINDOW } = {}) {
  const excluded = { unsealed: 0, sealed_after_outcome: 0 };
  let sealedCalls = 0;
  let wins = 0; let losses = 0; let flat = 0; let sumR = 0;
  const noOutcome = {};          // status word -> count, for calls with no outcome
  const days = new Map();
  for (const g of groups) {
    if (g.elig !== 'sealed') {
      if (!(g.elig in excluded)) throw new TypeError(`an eligibility this record does not know: ${g.elig}`);
      excluded[g.elig] += g.n;
      continue;
    }
    sealedCalls += g.n;
    if (!days.has(g.day)) days.set(g.day, { day: g.day, sealed: 0, resolved: 0, wins: 0, losses: 0, flat: 0 });
    const day = days.get(g.day);
    day.sealed += g.n;
    if (g.outcome === null) {
      const word = String(g.word || 'unknown').toLowerCase();
      noOutcome[word] = (noOutcome[word] || 0) + g.n;
      continue;
    }
    day.resolved += g.n;
    sumR += g.sumR;
    if (g.outcome === 'win') { wins += g.n; day.wins += g.n; } else if (g.outcome === 'loss') { losses += g.n; day.losses += g.n; } else { flat += g.n; day.flat += g.n; }
  }
  const resolved = wins + losses + flat;
  const recent = (recentRows || []).map(recentItem);

  return {
    as_of: new Date(ms(now)).toISOString(),
    window_days: windowDays,
    windows: WINDOWS.slice(),
    floor: SAMPLE_FLOOR,
    // 'too_few' below the floor: the numbers are shown, the verdict is not.
    verdict: resolved >= SAMPLE_FLOOR ? 'measured' : 'too_few',
    sealed_calls: sealedCalls,
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

module.exports = {
  computeClawRecord, recordFromGroups, groupRows, groupsFromSql, eligibility, windowOf,
  recentLimitOf, cursorOf, groupsSql, recentSql, recentParams, ELIGIBILITY_SQL,
  SAMPLE_FLOOR, WINDOWS, DEFAULT_WINDOW, RECENT, MAX_RECENT,
};
