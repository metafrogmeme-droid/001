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
 */

const MIN_SAMPLE = 10;
const RECENT_MAX = 20;

const round1 = (v) => Math.round(v * 10) / 10;

function romPct(t) {
  const m = Number(t.margin), p = Number(t.pnl);
  if (!(m > 0) || !Number.isFinite(p)) return null;   // unreadable ≠ zero
  return round1((p / m) * 100);
}

/**
 * @param rows attributed closed trades, newest first:
 *   { user_id, symbol, direction, margin, pnl, reason, trade_key, sealed_at, closed_at }
 * @returns the §4-safe public record.
 */
function computeAgentRecord(rows) {
  const rs = (Array.isArray(rows) ? rows : []).filter((t) => romPct(t) != null);
  const roms = rs.map(romPct).sort((a, b) => a - b);
  const wins = rs.filter((t) => Number(t.pnl) > 0).length;
  const losses = rs.filter((t) => Number(t.pnl) < 0).length;
  const liquidations = rs.filter((t) => t.reason === 'liquidated').length;
  const copiers = new Set(rs.map((t) => t.user_id)).size;
  const n = roms.length;
  const median = n ? (n % 2 ? roms[(n - 1) / 2] : round1((roms[n / 2 - 1] + roms[n / 2]) / 2)) : null;
  return {
    trades: n,
    copiers,
    wins,
    losses,
    flats: n - wins - losses,
    liquidations,
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
  };
}

module.exports = { computeAgentRecord, romPct, MIN_SAMPLE };
