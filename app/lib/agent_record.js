'use strict';
/**
 * Per-agent live paper record — the forward-only track record built from
 * arena trades that members explicitly copied from an agent's picks.
 *
 * What makes this record trustworthy is what it refuses to be:
 * - FORWARD-ONLY: a row exists only because a member copied a pick through
 *   the verified flow, sealed at open — before its outcome existed. Nothing
 *   is ever back-filled.
 * - THE COPIERS' RECORD, NOT THE ENGINE'S: fills are live marks at copy
 *   time, sized by each member. It answers "how did copying this agent's
 *   picks actually go", not "how good is the agent".
 * - §4: percent / ratio / count only. Return-on-margin percent per trade
 *   (pnl/margin), never a vUSDT figure, never a member identity.
 * - DISPERSION SHOWN: median plus best and worst — an average that hides
 *   the spread would flatter or slander; we show the shape instead.
 * - LOW-SAMPLE FLAGGED: under MIN_SAMPLE trades the record says so.
 *
 * TWO RECORDS, NEVER ONE NUMBER
 *
 * An agent can now trade the Arena ITSELF, through an Arena key bound to its
 * slug. Those rows land in the same table under the same `agent_slug`, and
 * summing them with the copiers' rows would quietly redefine every field above:
 * "how did copying this agent go" would start including trades nobody copied,
 * sized by the agent rather than by members, with `copiers` counting the agent
 * as one of its own followers.
 *
 * So they are split by `source` and never added together. The TOP-LEVEL fields
 * keep their existing meaning exactly — the copiers' record — and the agent's
 * own trading appears under `own`, which carries no `copiers` count because the
 * question does not apply to it. `own: null` means the agent has never traded
 * for itself, which is a different fact from having traded and lost.
 */

const MIN_SAMPLE = 10;
const RECENT_MAX = 20;

/** `source` written by the arena open path when an agent trades as itself. */
const OWN_SOURCE = 'agent';

const round1 = (v) => Math.round(v * 10) / 10;

function romPct(t) {
  const m = Number(t.margin), p = Number(t.pnl);
  if (!(m > 0) || !Number.isFinite(p)) return null;   // unreadable ≠ zero
  return round1((p / m) * 100);
}

/**
 * The shape shared by both records. Pure arithmetic over one set of rows —
 * it never sees the other set, which is the point.
 */
function summarise(rows) {
  const rs = (Array.isArray(rows) ? rows : []).filter((t) => romPct(t) != null);
  const roms = rs.map(romPct).sort((a, b) => a - b);
  const wins = rs.filter((t) => Number(t.pnl) > 0).length;
  const losses = rs.filter((t) => Number(t.pnl) < 0).length;
  const n = roms.length;
  const median = n ? (n % 2 ? roms[(n - 1) / 2] : round1((roms[n / 2 - 1] + roms[n / 2]) / 2)) : null;
  return {
    rows: rs,
    stats: {
      trades: n,
      wins,
      losses,
      flats: n - wins - losses,
      liquidations: rs.filter((t) => t.reason === 'liquidated').length,
      median_rom_pct: median,
      best_rom_pct: n ? roms[n - 1] : null,
      worst_rom_pct: n ? roms[0] : null,
      low_sample: n > 0 && n < MIN_SAMPLE,
      recent: rs.slice(0, RECENT_MAX).map((t) => ({
        symbol: t.symbol, direction: t.direction, rom_pct: romPct(t),
        reason: t.reason, closed_at: t.closed_at,
        // The receipt: sealed at OPEN, verifiable by anyone at /call/<key>.
        trade_key: t.trade_key || null, sealed_at: t.sealed_at || null,
      })),
    },
  };
}

/**
 * @param rows attributed closed trades, newest first:
 *   { user_id, symbol, direction, margin, pnl, reason, source, trade_key,
 *     sealed_at, closed_at }
 * @returns the §4-safe public record. Top level is the COPIERS' record, as it
 *   has always been; `own` is the agent's own Arena trading, or null.
 */
function computeAgentRecord(rows) {
  const all = Array.isArray(rows) ? rows : [];
  // A row with no `source` predates the column and cannot be an agent's own
  // trade — the agent path has always written 'agent'. Defaulting the OTHER
  // way would reclassify every historical copier row as the agent's own.
  const isOwn = (t) => String(t.source || '') === OWN_SOURCE;
  const copied = summarise(all.filter((t) => !isOwn(t)));
  const own = summarise(all.filter(isOwn));

  return {
    ...copied.stats,
    copiers: new Set(copied.rows.map((t) => t.user_id)).size,
    // null, not a zeroed block: "has never traded for itself" and "traded and
    // scored nothing" are different facts and a reader acts on them
    // differently. `trades: 0` beside a median of null reads as the second.
    own: own.stats.trades ? own.stats : null,
  };
}

module.exports = { computeAgentRecord, summarise, romPct, MIN_SAMPLE, OWN_SOURCE };
