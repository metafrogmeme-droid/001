'use strict';
/**
 * Canonical copy. app/lib/sar is a byte copy the site loads.
 * Do not edit one side.
 *
 * Scoring for a SaR95 series: lead time, precision and recall at a
 * threshold, and Spearman rank correlation. An unread series is not a
 * zero. The October 2025 archive is a directory the operator points at.
 * Unset, or a directory with no packets file, stays not_read.
 *
 * fixtureReport() is a synthetic series used to test the arithmetic.
 * Its label is fixture. It is not an October 2025 measurement.
 */

const fs = require('node:fs');

const WINDOW_MIN = 360n;
const E6 = 1000000n;

function ratioE6(num, den) {
  return ((BigInt(num) * E6) / BigInt(den)).toString();
}

function hasTie(values) {
  const seen = new Set();
  for (const v of values) {
    const key = v.toString();
    if (seen.has(key)) return true;
    seen.add(key);
  }
  return false;
}

/** Spearman as a 1e6 integer. Ties are refused rather than ranked badly. */
function spearmanE6(xs, ys) {
  if (!Array.isArray(xs) || !Array.isArray(ys) || xs.length !== ys.length) {
    return { coefficient_e6: null, reason: 'unreadable' };
  }
  if (xs.length < 2) return { coefficient_e6: null, reason: 'too_few' };
  for (let i = 0; i < xs.length; i += 1) {
    if (typeof xs[i] !== 'bigint' || typeof ys[i] !== 'bigint') {
      return { coefficient_e6: null, reason: 'partial_set' };
    }
  }
  if (hasTie(xs) || hasTie(ys)) return { coefficient_e6: null, reason: 'ties_present' };
  const n = BigInt(xs.length);
  const rankOf = (values) => {
    const order = values.map((v, i) => ({ v, i })).sort((a, b) => (a.v < b.v ? -1 : a.v > b.v ? 1 : 0));
    const ranks = new Array(values.length);
    order.forEach((row, pos) => { ranks[row.i] = BigInt(pos + 1); });
    return ranks;
  };
  const rx = rankOf(xs);
  const ry = rankOf(ys);
  let sumD2 = 0n;
  for (let i = 0; i < xs.length; i += 1) {
    const d = rx[i] - ry[i];
    sumD2 += d * d;
  }
  const denom = n * (n * n - 1n);
  if (denom === 0n) return { coefficient_e6: null, reason: 'too_few' };
  const num = E6 * denom - 6n * sumD2 * E6;
  const neg = num < 0n;
  const abs = neg ? -num : num;
  const q = (abs + denom / 2n) / denom;
  return { coefficient_e6: (neg ? -q : q).toString(), reason: null };
}

function score(samples, thresholdBps) {
  if (!Array.isArray(samples) || samples.length === 0) {
    return { status: 'not_read', reason: 'dataset_absent' };
  }
  if (typeof thresholdBps !== 'bigint') {
    return { status: 'not_read', reason: 'threshold_unreadable' };
  }
  for (const row of samples) {
    if (!row || typeof row.t_min !== 'bigint' || typeof row.stress !== 'boolean') {
      return { status: 'not_read', reason: 'partial_set' };
    }
    if (typeof row.sar95_bps !== 'bigint') return { status: 'not_read', reason: 'partial_set' };
  }
  const alerts = samples.filter((row) => row.sar95_bps > thresholdBps);
  const events = samples.filter((row) => row.stress === true);
  const hitsEvent = (alert, event) => event.t_min >= alert.t_min && (event.t_min - alert.t_min) <= WINDOW_MIN;
  const trueAlerts = alerts.filter((alert) => events.some((event) => hitsEvent(alert, event))).length;
  const recalled = events.filter((event) => alerts.some((alert) => hitsEvent(alert, event))).length;
  let precision_e6 = null;
  let precision_reason = null;
  if (alerts.length === 0) precision_reason = 'no_alerts';
  else precision_e6 = ratioE6(trueAlerts, alerts.length);
  let recall_e6 = null;
  let recall_reason = null;
  if (events.length === 0) recall_reason = 'no_event';
  else recall_e6 = ratioE6(recalled, events.length);
  let lead_min = null;
  let lead_reason = null;
  if (events.length === 0) lead_reason = 'no_event';
  else if (alerts.length === 0) lead_reason = 'no_lead';
  else {
    const first = events.reduce((min, event) => (event.t_min < min ? event.t_min : min), events[0].t_min);
    const prior = alerts.filter((alert) => alert.t_min <= first && (first - alert.t_min) <= WINDOW_MIN);
    if (prior.length === 0) lead_reason = 'no_lead';
    else {
      const earliest = prior.reduce((min, alert) => (alert.t_min < min ? alert.t_min : min), prior[0].t_min);
      lead_min = (first - earliest).toString();
    }
  }
  return {
    status: 'read',
    reason: null,
    threshold_bps: thresholdBps.toString(),
    window_min: WINDOW_MIN.toString(),
    alerts: String(alerts.length),
    events: String(events.length),
    precision_e6,
    precision_reason,
    recall_e6,
    recall_reason,
    lead_min,
    lead_reason,
    false_alerts: events.length === 0 ? String(alerts.length) : null,
  };
}

const FIXTURE = [
  { t_min: 0n, sar95_bps: 100n, stress: false },
  { t_min: 10n, sar95_bps: 400n, stress: false },
  { t_min: 11n, sar95_bps: 400n, stress: false },
  { t_min: 12n, sar95_bps: 600n, stress: false },
  { t_min: 13n, sar95_bps: 600n, stress: true },
];

const PLACEBO = [
  { t_min: 0n, sar95_bps: 100n, stress: false },
  { t_min: 1n, sar95_bps: 400n, stress: false },
];

function fixtureReport() {
  return {
    label: 'fixture',
    source: 'synthetic',
    not_october_2025: true,
    at_3pct: score(FIXTURE, 300n),
    at_5pct: score(FIXTURE, 500n),
    placebo_3pct: score(PLACEBO, 300n),
    spearman_agree: spearmanE6([1n, 2n, 3n, 4n], [1n, 2n, 3n, 4n]),
    spearman_oppose: spearmanE6([1n, 2n, 3n, 4n], [4n, 3n, 2n, 1n]),
  };
}

/**
 * The real replay. Unset directory: dataset_absent. A directory without
 * packets.json: dataset_unreadable. packets.json is a series already
 * reduced to integer bps. This does not rebuild a book, and it does not
 * fill a missing file with zeros.
 */
function archiveStatus(dir, fsImpl) {
  const disk = fsImpl || fs;
  if (dir == null || String(dir).trim() === '') {
    return { status: 'not_read', reason: 'dataset_absent', label: 'archive' };
  }
  const root = String(dir);
  let st;
  try {
    st = disk.statSync(root);
  } catch {
    return { status: 'not_read', reason: 'dataset_absent', label: 'archive' };
  }
  if (!st.isDirectory()) {
    return { status: 'not_read', reason: 'dataset_unreadable', label: 'archive' };
  }
  const file = `${root.replace(/\/$/, '')}/packets.json`;
  let raw;
  try {
    raw = disk.readFileSync(file, 'utf8');
  } catch {
    return { status: 'not_read', reason: 'dataset_unreadable', label: 'archive' };
  }
  let parsed;
  try {
    parsed = JSON.parse(raw);
  } catch {
    return { status: 'not_read', reason: 'dataset_unreadable', label: 'archive' };
  }
  if (!Array.isArray(parsed) || parsed.length === 0) {
    return { status: 'not_read', reason: 'dataset_unreadable', label: 'archive' };
  }
  const samples = [];
  for (const row of parsed) {
    if (!row || typeof row.t_min !== 'number' || typeof row.sar95_bps !== 'number' || typeof row.stress !== 'boolean') {
      return { status: 'not_read', reason: 'partial_set', label: 'archive' };
    }
    if (!Number.isSafeInteger(row.t_min) || !Number.isSafeInteger(row.sar95_bps) || row.sar95_bps < 0) {
      return { status: 'not_read', reason: 'partial_set', label: 'archive' };
    }
    samples.push({ t_min: BigInt(row.t_min), sar95_bps: BigInt(row.sar95_bps), stress: row.stress });
  }
  return {
    status: 'read',
    reason: null,
    label: 'archive',
    source: 'packets.json',
    at_3pct: score(samples, 300n),
    at_5pct: score(samples, 500n),
  };
}

module.exports = {
  WINDOW_MIN, score, spearmanE6, fixtureReport, archiveStatus,
};
