/**
 * Signal performance analytics (pure, in-process aggregation).
 *
 * Given resolved signal rows, break the win rate and the realized R down by
 * pattern, symbol, direction, and confidence bucket, and by the setup cell
 * setup × regime × timeframe × source × direction. Kept as a pure function
 * (no DB, no I/O) so it runs identically over the MySQL pool and the
 * in-memory mock, and is unit-testable on its own.
 *
 * `pnl` on a signal row is the R the outcome walk wrote (reward/risk at the
 * target, −1 at the stop), gross of fees: a call has no size. The aggregates
 * are named `net_r` (the sum) and `mean_r` (the sum divided by the counted
 * rows) so the public boundary can keep them. R is a ratio.
 *
 * A signal is a "win" when that R is > 0. Rows with a null, undefined or
 * non-finite `pnl` are unresolved or unreadable and are left out of every
 * count and every sum — an unreadable row is not a 0R.
 */

// Confidence buckets: [label, lo, hi) with hi exclusive except the last.
const CONF_BUCKETS = [
  ['<50%', 0, 0.5],
  ['50-60%', 0.5, 0.6],
  ['60-70%', 0.6, 0.7],
  ['70-80%', 0.7, 0.8],
  ['80-90%', 0.8, 0.9],
  ['90%+', 0.9, 1.0001],
];

function bucketFor(conf) {
  const c = Number(conf) || 0;
  for (const [label, lo, hi] of CONF_BUCKETS) {
    if (c >= lo && c < hi) return label;
  }
  return CONF_BUCKETS[CONF_BUCKETS.length - 1][0];
}

function round1(n) { return Math.round(n * 10) / 10; }
function round2(n) { return Math.round(n * 100) / 100; }

/**
 * A dimension word the row actually stored, or null when it did not.
 *
 * Only a non-empty string counts. `null`, a blank, and a number are not a
 * setup or a timeframe — `String(1)` would publish a group the row never
 * named. `pattern` is a different column (the scan trigger) and is not a
 * stand-in for `signal_type`.
 */
function recordedLabel(value) {
  if (typeof value !== 'string') return null;
  const text = value.trim();
  return text ? text : null;
}

/**
 * The five dimensions of one setup cell, or null when any one was not stored.
 *
 * Setup is the row's `signal_type` — the family the analyzer classified —
 * published under the scoreboard's word for it. A missing dimension is left
 * out of the cross-tab rather than filed as `(none)` or `unknown`. The row
 * can still count in the overall R; it just has no cell.
 */
function setupDims(row) {
  if (!row || typeof row !== 'object') return null;
  const direction = typeof row.direction === 'string'
    ? row.direction.toUpperCase()
    : row.direction;
  const dims = {
    setup: recordedLabel(row.signal_type),
    regime: recordedLabel(row.regime),
    timeframe: recordedLabel(row.timeframe),
    source: recordedLabel(row.source),
    direction: recordedLabel(direction),
  };
  for (const value of Object.values(dims)) {
    if (value == null) return null;
  }
  return dims;
}

// Accumulate one readable R into a group map keyed by `key`.
function _add(map, key, isWin, r) {
  if (key == null || key === '') key = '(none)';
  const g = map.get(key) || { key, n: 0, wins: 0, losses: 0, sumR: 0 };
  g.n += 1;
  if (isWin) g.wins += 1;
  if (r < 0) g.losses += 1;
  g.sumR += r;
  map.set(key, g);
}

/**
 * The two R aggregates, or null when nothing in the set was readable.
 *
 * `count === 0` is "no resolved row", and the accumulator's 0 is not a
 * measured flat book. A counted set whose R sums to 0 is a measurement,
 * and both fields stay 0. The mean is taken from the raw sum, then rounded
 * once, the same way `/api/signals/stats` rounds `avg_r`.
 */
function publishedR(sum, count) {
  if (!(count > 0) || !Number.isFinite(sum)) {
    return { net_r: null, mean_r: null };
  }
  return { net_r: round2(sum), mean_r: round2(sum / count) };
}

function _addSetup(map, dims, isWin, r) {
  const key = [dims.setup, dims.regime, dims.timeframe, dims.source, dims.direction].join('\0');
  const g = map.get(key) || {
    setup: dims.setup,
    regime: dims.regime,
    timeframe: dims.timeframe,
    source: dims.source,
    direction: dims.direction,
    n: 0,
    wins: 0,
    losses: 0,
    sumR: 0,
  };
  g.n += 1;
  if (isWin) g.wins += 1;
  if (r < 0) g.losses += 1;
  g.sumR += r;
  map.set(key, g);
}

function _finaliseSetups(map, top = 12) {
  return [...map.values()]
    .map(g => ({
      setup: g.setup,
      regime: g.regime,
      timeframe: g.timeframe,
      source: g.source,
      direction: g.direction,
      n: g.n,
      wins: g.wins,
      losses: g.losses,
      flat: Math.max(0, g.n - g.wins - g.losses),
      win_rate: g.n > 0 ? round1((g.wins / g.n) * 100) : null,
      ...publishedR(g.sumR, g.n),
    }))
    .sort((a, b) => {
      if (b.n !== a.n) return b.n - a.n;
      const ak = [a.setup, a.regime, a.timeframe, a.source, a.direction].join('\0');
      const bk = [b.setup, b.regime, b.timeframe, b.source, b.direction].join('\0');
      return ak < bk ? -1 : ak > bk ? 1 : 0;
    })
    .slice(0, top);
}

// Finalise a group map into a win_rate-annotated array, sorted by sample count
// desc (most-traded first), capped at `top`.
function _finalise(map, top = 12) {
  return [...map.values()]
    .map(g => ({
      key: g.key,
      n: g.n,
      wins: g.wins,
      losses: g.losses,
      flat: Math.max(0, g.n - g.wins - g.losses),
      win_rate: g.n > 0 ? round1((g.wins / g.n) * 100) : null,
      ...publishedR(g.sumR, g.n),
    }))
    .sort((a, b) => b.n - a.n)
    .slice(0, top);
}

function computeAnalytics(signals, { top = 12 } = {}) {
  const byPattern = new Map();
  const bySymbol = new Map();
  const byDirection = new Map();
  const byConfidence = new Map();
  const bySetup = new Map();
  let n = 0, wins = 0, losses = 0, sumR = 0;

  for (const s of signals || []) {
    // `== null` before Number(): Number(null) is 0, and a missing R is not
    // a break-even. A non-finite value (NaN, a word) is unreadable, same skip.
    const r = Number(s.pnl);
    if (s.pnl == null || !Number.isFinite(r)) continue;
    const isWin = r > 0;
    n += 1; if (isWin) wins += 1; if (r < 0) losses += 1; sumR += r;
    _add(byPattern, s.pattern, isWin, r);
    _add(bySymbol, s.symbol, isWin, r);
    _add(byDirection, (s.direction || '').toUpperCase(), isWin, r);
    _add(byConfidence, bucketFor(s.confidence), isWin, r);
    const dims = setupDims(s);
    if (dims) _addSetup(bySetup, dims, isWin, r);
  }

  // Confidence buckets keep their natural order (not by count).
  const confOrder = CONF_BUCKETS.map(b => b[0]);
  const byConfidenceArr = _finalise(byConfidence, confOrder.length)
    .sort((a, b) => confOrder.indexOf(a.key) - confOrder.indexOf(b.key));

  return {
    // Same word `/api/signals/stats` stamps. Gross: the walk does not charge
    // fees to a call, so `net_r` is the signed sum of that R, not a fee-net.
    r_basis: 'gross',
    overall: {
      resolved: n,
      wins,
      // COUNTED. The loop above already skips unresolved rows, so `n - wins`
      // is losses PLUS the flat ones — a signal that resolved at exactly
      // break-even is an outcome, not a defeat.
      losses,
      flat: Math.max(0, n - wins - losses),
      // null, not 0: nothing resolved is not a 0% win rate, and a sum of 0
      // over an empty set is not a measured flat book.
      win_rate: n > 0 ? round1((wins / n) * 100) : null,
      ...publishedR(sumR, n),
    },
    by_pattern: _finalise(byPattern, top),
    by_symbol: _finalise(bySymbol, top),
    by_direction: _finalise(byDirection, 4),
    by_confidence: byConfidenceArr,
    // One cell per recorded setup × regime × timeframe × source × direction.
    // A row missing any of those is absent here, not keyed under a filler.
    by_setup: _finaliseSetups(bySetup, top),
  };
}

module.exports = { computeAnalytics, bucketFor, CONF_BUCKETS };
