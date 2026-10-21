'use strict';
/**
 * The anchors, watched: what an operator must hear about the daily proof,
 * found by the same live checks /roots shows (lib/day_quorum.js).
 *
 * Every thirty minutes one instance (lib/shared_schedule.js) checks the last
 * RECENT_DAYS completed days and records its findings; /diagz serves the
 * latest pass under `anchors`. Nothing is pushed anywhere yet: which channel
 * an alert goes to (the Telegram admin chat, a Discord ops channel) is the
 * owner's call, and the findings are kept ready for it.
 *
 * Each finding is { day, kind, level, chain?, detail }:
 *   alarm  reader_disagreement  one anchor's readers contradict each other:
 *                               a reader is lying, lagging or reorged
 *   alarm  anchor_mismatch      a recorded anchor (or the Bitcoin proof) no
 *                               longer checks out against its chain
 *   warn   ots_stuck            sent to Bitcoin over a day ago, not in a block
 *   warn   ots_missing          a completed day never sent to Bitcoin: the
 *                               stamping job is not doing its work
 *   warn   unreadable           a check could not be read, and could change
 *                               whether the day is multi-anchored
 *   warn   check_failed         the day's check threw (its class only)
 *   todo   quorum_short         not multi-anchored a day and a half after the
 *                               day ended: anchor it from a wallet on /roots
 *
 * Unreadable is never zero: a pass that could not read the roots records no
 * findings list at all (`findings: null`) and the error's class, never an
 * empty list that would read as "all clear".
 */
const { pool } = require('../db');
const { dayQuorum } = require('./day_quorum');

const JOB = 'anchor_watch';
const health = require('./watch_health').register(JOB);
const INTERVAL_MS = 30 * 60 * 1000;
/** Completed days checked each pass. */
const RECENT_DAYS = 3;
const HOUR = 3600 * 1000;
/** A proof the calendars have not completed this long after it was sent. */
const OTS_STUCK_MS = 24 * HOUR;
/** A completed day the stamping job (every ten minutes) has not sent by now. */
const OTS_MISSING_MS = 2 * HOUR;
/** When a day still short of its quorum becomes an operator's to-do. */
const QUORUM_DUE_MS = 36 * HOUR;

let last = { checked_at: null, findings: null, days: [], error: null };

/** The findings for one day's quorum (dayQuorum's answer) at `now` (ms). */
function findingsFor(q, now) {
  const out = [];
  const day = q.day;
  const age = now - (Date.parse(`${day}T00:00:00Z`) + 24 * HOUR);
  const checks = [...(q.anchors || []), ...(q.bitcoin ? [{ ...q.bitcoin, chain: 'bitcoin' }] : [])];
  for (const c of checks) {
    if (c.disagreement) out.push({ day, kind: 'reader_disagreement', level: 'alarm', chain: c.chain, detail: c.reason || null });
  }
  for (const key of q.mismatched || []) {
    const c = checks.find((x) => x.chain === key);
    out.push({ day, kind: 'anchor_mismatch', level: 'alarm', chain: key, detail: (c && c.reason) || null });
  }
  const b = q.bitcoin || {};
  if (b.status === 'not_submitted' && age > OTS_MISSING_MS) {
    out.push({ day, kind: 'ots_missing', level: 'warn', chain: 'bitcoin', detail: 'no Bitcoin timestamp was requested for this day' });
  }
  const sent = Date.parse(b.submitted_at);
  if (b.status === 'pending' && Number.isFinite(sent) && now - sent > OTS_STUCK_MS) {
    out.push({ day, kind: 'ots_stuck', level: 'warn', chain: 'bitcoin',
      detail: `sent ${Math.floor((now - sent) / HOUR)} h ago, not yet in a Bitcoin block` });
  }
  if (q.status === 'unknown') {
    out.push({ day, kind: 'unreadable', level: 'warn', detail: `could not be checked: ${(q.unreadable || []).join(', ')}` });
  }
  if (q.status === 'short' && age > QUORUM_DUE_MS) {
    out.push({ day, kind: 'quorum_short', level: 'todo',
      detail: `${(q.groups || []).length} of ${q.k} independent chains${q.pending && q.pending.length ? `; waiting: ${q.pending.join(', ')}` : ''}` });
  }
  return out;
}

/** One pass: the recent completed days, each checked live. Throws when the roots cannot be read. */
async function pass({ now = Date.now(), check = dayQuorum } = {}) {
  const [rows] = await pool.execute(
    `SELECT day, root FROM seal_roots ORDER BY day DESC LIMIT ${RECENT_DAYS}`);
  const days = [];
  const findings = [];
  // The newest RECENT_DAYS, in code as well as in SQL: a reader that ignored
  // the LIMIT would otherwise widen every pass to the whole history.
  for (const row of (rows || []).slice(0, RECENT_DAYS)) {
    const day = String(row.day);
    try {
      const q = await check({ day, root: row.root });
      days.push({ day, status: q.status, groups: q.groups });
      findings.push(...findingsFor(q, now));
    } catch (e) {
      days.push({ day, status: 'check_failed', groups: null });
      findings.push({ day, kind: 'check_failed', level: 'warn', detail: e && e.name ? e.name : 'Error' });
    }
  }
  return { checked_at: new Date(now).toISOString(), days, findings, error: null };
}

async function runOnce(opts = {}) {
  try {
    last = await pass(opts);
    health.ok();
  } catch (e) {
    last = { checked_at: new Date(opts.now || Date.now()).toISOString(), findings: null, days: [], error: e && e.name ? e.name : 'Error' };
    health.failed(e);
  }
  return last;
}

/** The latest pass, for /diagz, with its findings counted by level (null when it could not read). */
function snapshot() {
  const counts = last.findings === null ? null
    : last.findings.reduce((acc, f) => { acc[f.level] = (acc[f.level] || 0) + 1; return acc; }, { alarm: 0, warn: 0, todo: 0 });
  return { ...last, counts };
}

let stop = null;
function startAnchorWatch(intervalMs = INTERVAL_MS) {
  if (stop) return;
  stop = require('./shared_schedule').every(JOB, intervalMs, () => runOnce());
}

module.exports = { JOB, INTERVAL_MS, RECENT_DAYS, OTS_STUCK_MS, OTS_MISSING_MS, QUORUM_DUE_MS,
  findingsFor, pass, runOnce, snapshot, startAnchorWatch };
