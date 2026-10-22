'use strict';
/**
 * The anchors, watched: what an operator must hear about the daily proof,
 * found by the same live checks /roots shows (lib/day_quorum.js).
 *
 * Every thirty minutes one instance (lib/shared_schedule.js) checks the last
 * RECENT_DAYS completed days and records its findings. An instance lives
 * about seventy seconds, so the pass is kept in the shared job state
 * (lib/job_state.js) and /diagz reads it there, under `anchors`.
 *
 * ALERTS GO TO DISCORD (the owner's choice, 22 October 2026;
 * lib/discord_alerts.js). After each pass, one message says what changed: a
 * finding that is new, at once; one still open, again after REPEAT_MS of its
 * level; one that cleared, or whose day left the window, once. A finding
 * gone only because its check could not be read this time has not cleared,
 * and is not said to have: it stays open, unsaid, until a pass can tell.
 * What was said
 * is shared state too, so a new instance neither repeats an alert nor loses a
 * clearance. Without a configured hook nothing is recorded as said, so the
 * first message after the owner sets one carries everything open.
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
const jobState = require('./job_state');
const discord = require('./discord_alerts');

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

/** The shared state that holds what the alerts have said, and their own liveness. */
const ALERTS = 'anchor_alerts';
const alertHealth = require('./watch_health').register(ALERTS);
/** How long a finding still open waits before it is said again, by level. */
const REPEAT_MS = Object.freeze({ alarm: 6 * HOUR, warn: 24 * HOUR, todo: 24 * HOUR });
const LEVEL_ORDER = ['alarm', 'warn', 'todo'];
const ICON = { alarm: '\u{1F534}', warn: '\u{1F7E0}', todo: '\u{1F535}' };

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

/** The chains whose check could not be read this pass: what they said before still stands. */
function unreadOf(q) {
  const checks = [...(q.anchors || []), ...(q.bitcoin ? [{ ...q.bitcoin, chain: 'bitcoin' }] : [])];
  return checks.filter((c) => c.status === 'unknown').map((c) => c.chain);
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
      days.push({ day, status: q.status, groups: q.groups, unread: unreadOf(q) });
      findings.push(...findingsFor(q, now));
    } catch (e) {
      days.push({ day, status: 'check_failed', groups: null, unread: null });
      findings.push({ day, kind: 'check_failed', level: 'warn', detail: e && e.name ? e.name : 'Error' });
    }
  }
  return { checked_at: new Date(now).toISOString(), days, findings, error: null };
}

/** A finding's identity across passes. */
function keyOf(f) { return [f.kind, f.day || '', f.chain || ''].join('|'); }
function entryOf(f) { return { kind: f.kind, level: f.level, day: f.day || null, chain: f.chain || null, detail: f.detail || null }; }

/** The pass as findings to alert on: a pass that could not read is one warning of its own. */
function alertable(p) {
  if (Array.isArray(p.findings)) return p.findings;
  return [{ day: null, kind: 'watch_unreadable', level: 'warn',
    detail: `the anchor watch could not read the roots (${p.error || 'Error'})` }];
}

/**
 * Whether this pass (its `days`) can say that `s`, a finding said before and
 * not found now, has cleared: only when what it was about was read. A finding
 * gone because its check could not be read this time is unknown, not cleared.
 */
function canTell(s, days) {
  // A pass that could not read the roots has no days, so it can tell nothing
  // about a day; the one finding with no day is its own, still open.
  if (!s.day) return true;
  const d = days.get(s.day);
  // A day whose check threw has no reading at all (`unread: null`).
  if (!d || !Array.isArray(d.unread)) return false;
  if (s.kind === 'check_failed') return true;
  if (s.chain) return !d.unread.includes(s.chain);
  return d.status !== 'unknown';
}

/**
 * What to say after pass `p`, given what was said before (`sent`, key ->
 * entry with `first` and `last` times): pure, for the tests.
 * → { fresh, again, cleared, aged, next }, `next` being what will have been said.
 * A finding that is gone is cleared only when its check was read (canTell);
 * one whose day left the window is aged; anything else stays as it was.
 */
function plan(p, sent, now) {
  const current = new Map(alertable(p).map((f) => [keyOf(f), f]));
  const days = new Map((p.days || []).map((d) => [d.day, d]));
  const fresh = []; const again = []; const cleared = []; const aged = [];
  const next = {};
  for (const [k, f] of current) {
    const s = sent[k];
    if (!s) {
      fresh.push(f);
      next[k] = { ...entryOf(f), first: now, last: now };
    } else if (now - s.last >= (REPEAT_MS[f.level] || REPEAT_MS.warn)) {
      again.push({ ...f, first: s.first });
      next[k] = { ...entryOf(f), first: s.first, last: now };
    } else {
      next[k] = s;
    }
  }
  for (const [k, s] of Object.entries(sent || {})) {
    if (current.has(k)) continue;
    if (Array.isArray(p.findings) && s.day && !days.has(s.day)) aged.push(s);
    else if (canTell(s, days)) cleared.push(s);
    else next[k] = s;
  }
  const byLevel = (a, b) => LEVEL_ORDER.indexOf(a.level) - LEVEL_ORDER.indexOf(b.level);
  return { fresh: fresh.sort(byLevel), again: again.sort(byLevel), cleared, aged, next };
}

/** One planned alert as Discord markdown: never an address, an account or an amount. */
function lineOf(what, f) {
  const where = [f.kind, f.day, f.chain].filter(Boolean).join(' · ');
  const said = f.detail ? `: ${f.detail}` : '';
  if (what === 'fresh') return `${ICON[f.level] || ''} **${f.level}** · ${where}${said}`;
  if (what === 'again') return `\u{1F501} still open since ${new Date(f.first).toISOString().slice(0, 16)}Z · ${f.level} · ${where}${said}`;
  if (what === 'cleared') {
    return f.kind === 'watch_unreadable' ? '\u{1F7E2} the anchor watch reads the roots again' : `\u{1F7E2} cleared · ${where}`;
  }
  return `\u{26AA} no longer watched (older than ${RECENT_DAYS} days), ${f.level} when last seen · ${where}`;
}

/** The pass's message: counts first, then each line, then where the full pass is. */
function messageOf(planned, env = process.env) {
  const n = (xs, word) => (xs.length ? `${xs.length} ${word}` : null);
  const counts = [n(planned.fresh, 'new'), n(planned.again, 'still open'), n(planned.cleared, 'cleared'),
    n(planned.aged, 'no longer watched')].filter(Boolean).join(', ');
  const base = String(env.APP_BASE_URL || '').trim().replace(/\/+$/, '');
  return discord.compose([
    `**RUNECLAW anchor watch** · ${counts}`,
    ...planned.fresh.map((f) => lineOf('fresh', f)),
    ...planned.again.map((f) => lineOf('again', f)),
    ...planned.cleared.map((f) => lineOf('cleared', f)),
    ...planned.aged.map((f) => lineOf('aged', f)),
    /^https:\/\//.test(base) ? `${base}/roots · the full pass is on /diagz` : 'The full pass is on /diagz.',
  ]);
}

/**
 * Say what changed in pass `p` to the Discord hook, and remember it. A send
 * that fails leaves the record as it was, so the next pass says it again.
 */
async function alert(p, { now = Date.now(), env = process.env, fetchImpl } = {}) {
  const hook = discord.hookOf(env);
  if (!hook.configured) {
    alertHealth.skipped(`no Discord webhook: ${hook.reason}`);
    return { said: 0, skipped: hook.reason };
  }
  const loaded = await jobState.load(ALERTS);
  const state = loaded.found && loaded.value && typeof loaded.value === 'object' ? loaded.value : {};
  const sent = state.sent && typeof state.sent === 'object' ? state.sent : {};
  const planned = plan(p, sent, now);
  const said = planned.fresh.length + planned.again.length + planned.cleared.length + planned.aged.length;
  if (!said) {
    alertHealth.ok();
    return { said: 0 };
  }
  try {
    await discord.post(messageOf(planned, env), { env, fetchImpl });
  } catch (e) {
    alertHealth.failed(e);
    await jobState.save(ALERTS, { ...state, sent, last_error: (e && e.name) || 'Error', last_error_at: new Date(now).toISOString() });
    return { said: 0, error: (e && e.name) || 'Error' };
  }
  await jobState.save(ALERTS, { sent: planned.next, last_sent_at: new Date(now).toISOString(), last_error: null, last_error_at: null });
  alertHealth.ok();
  return { said };
}

async function runOnce(opts = {}) {
  try {
    last = await pass(opts);
    health.ok();
  } catch (e) {
    last = { checked_at: new Date(opts.now || Date.now()).toISOString(), findings: null, days: [], error: e && e.name ? e.name : 'Error' };
    health.failed(e);
  }
  await jobState.save(JOB, last);
  // An alert that throws past its own handling is named by its class: its
  // message is not known to be free of the hook's URL.
  try { await alert(last, opts); } catch (e) { alertHealth.failed(new Error((e && e.name) || 'Error')); }
  return last;
}

/** A shared-state read that gives up after `ms`: a diagnostic must answer while the database does not. */
function loadWithin(job, ms) {
  return Promise.race([
    jobState.load(job),
    new Promise((resolve) => { const t = setTimeout(() => resolve({ found: false, timeout: true }), ms); if (t.unref) t.unref(); }),
  ]);
}

/**
 * The latest pass, for /diagz, from the shared state (any instance's pass),
 * with its findings counted by level (null when it could not read) and the
 * alerts' state, which never includes the hook's URL.
 */
async function snapshot({ env = process.env, timeoutMs = 1500 } = {}) {
  const [p, a] = await Promise.all([loadWithin(JOB, timeoutMs), loadWithin(ALERTS, timeoutMs)]);
  // The newer of the shared pass and this instance's own: a save the
  // database refused leaves the shared copy a pass behind.
  const at = (x) => (x && x.checked_at ? Date.parse(x.checked_at) : -Infinity);
  const shared = p.found && p.value && typeof p.value === 'object' && at(p.value) >= at(last);
  const cur = shared ? p.value : last;
  const counts = !Array.isArray(cur.findings) ? null
    : cur.findings.reduce((acc, f) => { acc[f.level] = (acc[f.level] || 0) + 1; return acc; }, { alarm: 0, warn: 0, todo: 0 });
  const av = a.found && a.value && typeof a.value === 'object' ? a.value : null;
  return {
    ...cur, counts,
    source: shared ? (p.local ? 'this instance' : 'shared') : (last.checked_at ? 'this instance' : 'none'),
    alerts: {
      channel: 'discord', ...discord.describe(env),
      open: av && av.sent ? Object.keys(av.sent).length : null,
      last_sent_at: av ? av.last_sent_at || null : null,
      last_error: av ? av.last_error || null : null,
    },
  };
}

let stop = null;
function startAnchorWatch(intervalMs = INTERVAL_MS) {
  if (stop) return;
  stop = require('./shared_schedule').every(JOB, intervalMs, () => runOnce());
}

module.exports = { JOB, ALERTS, INTERVAL_MS, RECENT_DAYS, OTS_STUCK_MS, OTS_MISSING_MS, QUORUM_DUE_MS, REPEAT_MS,
  findingsFor, pass, plan, messageOf, alert, runOnce, snapshot, startAnchorWatch };
