'use strict';
/**
 * Pattern watch — the engine's read, pushed to the people it concerns.
 *
 * Every two minutes the latest synced deep-scan pattern block is checked
 * against OPEN paper positions: when the engine's detectors report a NEW
 * chart pattern (Elliott, Wyckoff, H&S, …) on a symbol a user is holding,
 * that user gets ONE web push ("🧭 BTCUSDT: Elliott 5-Wave Impulse
 * (bullish, detector score 78 (unmeasured)) — you have a LONG open").
 * Dedupe is per (user, symbol, pattern) with a 12h TTL — a persistent
 * pattern never spams, and a fresh read after it fades can announce again.
 *
 * NO CONFIDENCE FLOOR. The figure is the detector's own hand-set score, and
 * nothing has measured how a pattern's calls turned out, so a floor on it
 * selected detectors by the constants they were written with, not patterns
 * by how good they are. The push says what the figure is
 * (`RCConfidenceScore.patternText`); the per-symbol cap and the 12h window
 * bound how often it speaks.
 *
 * One source of truth: patterns come ONLY from the engine's own synced
 * deepscan block — nothing is re-detected here. §4: the push goes to the
 * position's owner only and carries symbol + pattern name + the detector's
 * score (the engine's read of public market structure) — no amounts.
 */

const { pool } = require('../db');
const push = require('./push');
const CS = require('../public/js/confidence-score');

const TTL_MS = 12 * 3600 * 1000; // per (user, symbol, pattern) silence window
const MAX_PER_SYMBOL = 2;        // top patterns per symbol per sweep

const base = (sym) => String(sym || '').toUpperCase()
  .replace('/USDT', '').replace(':USDT', '').replace(/USDT$/, '');

/**
 * Pure decision core.
 * @param hits deepscan hits [{symbol, chart_patterns: [{name, signal, confidence}]}]
 * @param interests holders AND watchers: [{user_id, symbol, direction?}] —
 *   direction present = an open paper position; absent/null = a watchlist
 *   star. Holders should be listed FIRST: the (user, symbol, pattern) dedupe
 *   key means the first interest wins the notification phrasing.
 * @param seen Map "user|base|pattern" -> announced-at ts
 * @returns { notify: [{user_id, symbol, direction, name, signal, confidence}], seen: nextMap }
 */
function transitions(hits, interests, seen, now = Date.now()) {
  const positions = interests;
  const bySym = new Map();
  for (const h of hits || []) {
    const b = base(h.symbol);
    if (!b) continue;
    bySym.set(b, (h.chart_patterns || [])
      .filter((p) => p && p.name)
      .slice(0, MAX_PER_SYMBOL));
  }
  const next = new Map();
  for (const [k, ts] of seen || []) {
    if (now - ts < TTL_MS) next.set(k, ts);
  }
  const notify = [];
  for (const pos of positions || []) {
    const pats = bySym.get(base(pos.symbol));
    if (!pats || !pats.length) continue;
    for (const pat of pats) {
      const key = `${pos.user_id}|${base(pos.symbol)}|${pat.name}`;
      if (next.has(key)) continue;
      next.set(key, now);
      notify.push({
        user_id: pos.user_id, symbol: pos.symbol,
        direction: pos.direction || null,
        name: pat.name, signal: pat.signal || 'neutral',
        // The detector's figure as it was read, or null: an absent score is
        // not a score of zero.
        confidence: CS.number(pat.confidence) === null ? null : Number(pat.confidence),
      });
    }
  }
  return { notify, seen: next };
}

// What was announced, key -> when (12 h silence window), shared by every
// instance (lib/job_state). The two-minute schedule never ran at all on
// 70-second instances; run on the shared schedule, a variable here would be
// forgotten by each new instance and every pattern announced again.
const jobState = require('./job_state');
const STATE = 'pattern_watch';
let timer = null;

const health = require('./watch_health').register('pattern_watch');

// `deps` is for tests, as in lib/arena_watch.js: the push and the synced scan
// are the two things this reads from outside.
async function runOnce(deps = {}) {
  const pushMod = deps.push || push;
  if (!pushMod.isConfigured()) { health.skipped('push not configured'); return; }
  let scan;
  try {
    // Lazy require: routes/sync owns the synced scan cache; requiring at
    // call time avoids any boot-order cycle.
    scan = await (deps.getLatestScan || (() => require('../routes/sync').getLatestScan()))();
  } catch (e) { health.failed(e); return; }
  try {
    await _sweepPatterns(scan, pushMod);
    health.ok();
  } catch (e) {
    health.failed(e);
    throw e;
  }
}

async function _sweepPatterns(scan, pushMod) {
  const hits = scan && scan.deepscan && scan.deepscan.hits;
  if (!hits || !hits.length) return;
  const [positions] = await pool.execute(
    'SELECT id, user_id, symbol, direction, entry, margin, leverage FROM arena_positions');
  let watchers = [];
  try {
    const [wrows] = await pool.execute('SELECT user_id, symbol FROM user_watchlist');
    watchers = wrows.map((w) => ({ user_id: w.user_id, symbol: w.symbol, direction: null }));
  } catch (e) { /* no watchlist yet — holders still covered */ }
  // Holders FIRST: when someone both holds and watches a symbol, the dedupe
  // key makes the position phrasing win.
  const interests = positions.concat(watchers);
  if (!interests.length) return;
  const saved = await jobState.load(STATE);
  const seenBefore = new Map(Object.entries(saved.found ? saved.value || {} : {}).map(([k, t]) => [k, Number(t)]));
  const { notify, seen } = transitions(hits, interests, seenBefore);
  await jobState.save(STATE, Object.fromEntries(seen));   // before sending: a crash mid-send never re-announces
  for (const n of notify) {
    const why = n.direction
      ? `you have a ${n.direction} open`
      : `it's on your watchlist`;
    await pushMod.notifySubscribers({
      title: '🧭 Engine pattern on your symbol',
      body: `${n.symbol}: ${n.name} (${n.signal}`
        + `${CS.patternText(n.confidence) ? ', ' + CS.patternText(n.confidence) : ''}) — `
        + `${why}. See the chart in the Arena.`,
      url: '/arena',
    }, [n.user_id]).catch(() => {});
  }
}

function startPatternWatch(intervalMs = 120_000) {
  if (timer) return;
  timer = require('./shared_schedule').every('pattern_watch', intervalMs, () => runOnce());
}

/** Forget what was announced (tests). */
function resetPatternWatch() { return jobState.clear(STATE); }

module.exports = { startPatternWatch, runOnce, transitions, resetPatternWatch, TTL_MS, MAX_PER_SYMBOL };
