'use strict';
/**
 * The PUBLIC shape of a signal: outcome as a label, never as an amount.
 *
 * §4 of CLAUDE.md keeps dollar amounts off public, community, leaderboard and
 * marketplace payloads — percent, ratio and count only. Three unauthenticated
 * surfaces emitted the raw `signals.pnl` column straight out of the SELECT:
 * `GET /api/signals` (per row), `GET /api/signals/stats` (`net_pnl` = SUM), and
 * the MCP `get_signals` tool. Nothing leaked *yet* only because the bot has
 * never populated the outcome column — so the finding was latent, and one
 * bot-side change away from publishing dollar P&L on all three at once.
 *
 * WHY NOT JUST DROP THE FIELD. The stream table reads `pnl` for two things
 * that are not amounts: whether a signal has resolved at all, and which way it
 * went. Deleting the key would have made every resolved signal look actionable
 * again — `canTrade = s.pnl == null` is true for a field that is simply gone —
 * and offered a "Trade" button on calls the engine had already closed. The
 * sign is public; the magnitude is not. So the sign is what we publish.
 *
 * THE FLAT CASE CAME FREE. The old renderer was
 *
 *     Number(s.pnl) > 0 ? '✓ WIN' : '✗ LOSS'
 *
 * which files a signal that resolved at exactly break-even as a defeat — the
 * `losses = total - wins` shape from CLAUDE.md's table, wearing a ternary.
 * `/api/signals/stats` had already been fixed to count wins/losses/flat
 * separately; the stream beside it had not. Naming FLAT as its own outcome is
 * what stops the two from disagreeing.
 *
 * Redaction lives at the HTTP boundary, not in the aggregator: `computeAnalytics`
 * still computes `net_pnl` honestly (null over an empty set, never 0) and keeps
 * its own unit tests. The same split `sanitizeRecord` uses for flight records.
 */

const { DOLLAR_KEY } = require('./flight');

/**
 * WIN / LOSS / FLAT from a P&L, or null when it cannot be read.
 *
 * null is not an outcome: an unresolved signal and one whose P&L failed to
 * parse are both "no verdict", and neither is a loss. A recorded 0 IS a
 * verdict, and gets FLAT rather than being folded into either column.
 */
function signalOutcome(pnl) {
  if (pnl === null || pnl === undefined) return null;
  const f = typeof pnl === 'number' ? pnl : parseFloat(pnl);
  if (!Number.isFinite(f)) return null;
  if (f > 0) return 'WIN';
  if (f < 0) return 'LOSS';
  return 'FLAT';
}

// Everything a public signal row may carry. An ALLOWLIST on purpose: a column
// added to the SELECT later is not published until someone adds it here, which
// is the opposite default from a denylist and the right one for an anonymous
// payload. Prices are public market data (already on /api/insight) and stay.
const PUBLIC_FIELDS = [
  'signal_key', 'symbol', 'direction', 'confidence', 'score', 'pattern',
  'regime', 'entry_price', 'stop_loss', 'take_profit', 'rr', 'thesis',
  'status', 'created_at', 'resolved_at', 'seal',
];

/** One signal row, public-safe: allowlisted fields plus the outcome label. */
function publicSignal(row) {
  if (!row || typeof row !== 'object') return row;
  const out = {};
  for (const k of PUBLIC_FIELDS) {
    if (k in row) out[k] = row[k];
  }
  out.outcome = signalOutcome(row.pnl);
  return out;
}

/**
 * `computeAnalytics` output with the dollar totals removed.
 *
 * Win rate, the counts and the group keys are all ratios/counts and survive
 * untouched — they are the entire content of the insights panel. Only
 * `net_pnl` (overall and per group) is an amount.
 */
function publicAnalytics(a) {
  if (!a || typeof a !== 'object') return a;
  const dropNet = (g) => {
    const out = {};
    for (const k of Object.keys(g || {})) {
      if (k === 'net_pnl') continue;
      out[k] = g[k];
    }
    return out;
  };
  const out = {};
  for (const k of Object.keys(a)) {
    out[k] = Array.isArray(a[k]) ? a[k].map(dropNet)
      : (k === 'overall' ? dropNet(a[k]) : a[k]);
  }
  return out;
}

/**
 * Every dollar-named key in an object tree, recursively — the assertion the
 * audit asked the tests to make ("no dollar-key fields even when pnl is
 * populated"). Reuses `flight.js`'s DOLLAR_KEY so one regex governs which
 * names count as amounts across every public surface.
 */
function dollarKeys(value, path = '') {
  if (value == null || typeof value !== 'object') return [];
  if (value instanceof Date) return [];
  if (Array.isArray(value)) {
    return value.flatMap((v, i) => dollarKeys(v, `${path}[${i}]`));
  }
  const hits = [];
  for (const k of Object.keys(value)) {
    const here = path ? `${path}.${k}` : k;
    if (DOLLAR_KEY.test(k)) hits.push(here);
    hits.push(...dollarKeys(value[k], here));
  }
  return hits;
}

module.exports = { signalOutcome, publicSignal, publicAnalytics, dollarKeys,
                   PUBLIC_FIELDS };
