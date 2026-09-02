/**
 * Cross-venue exposure intelligence — READ-ONLY portfolio judgment.
 *
 * Nets the user's holdings per base asset across their open platform
 * positions (perp longs/shorts from their own trades) and their on-chain
 * wallet spot (WETH→ETH, WBTC→BTC mapped), then flags what a risk desk
 * would flag:
 *   stacked_long   — spot AND a perp long on the same asset (doubled bet)
 *   hedged         — spot with a perp short against it (note, not a warning)
 *   concentrated   — one asset is more than half of gross exposure
 *
 * Pure computation + reads; nothing here can resize, hedge, or close
 * anything — it tells the truth and stops.
 */

const { pool } = require('../db');
const wallet = require('./wallet');

function round2(v) { return Math.round(v * 100) / 100; }

// Wrapped assets net against their underlying.
const BASE_MAP = { WETH: 'ETH', WBTC: 'BTC' };
// Stables are cash, not directional exposure.
const STABLES = new Set(['USDT', 'USDC', 'DAI']);

function baseOf(symbol) {
  const raw = String(symbol || '').toUpperCase().split('/')[0].replace(/USDT.*$/, '');
  return BASE_MAP[raw] || raw;
}

/**
 * Pure exposure computation.
 * openTrades: [{symbol, direction, size_usd}] — the user's OPEN positions.
 * walletAssets: [{symbol, usd}] — priced wallet holdings (may be null).
 */
function computeExposure(openTrades, walletAssets) {
  const byBase = new Map();
  const row = (base) => {
    if (!byBase.has(base)) {
      byBase.set(base, { base, perp_long_usd: 0, perp_short_usd: 0, spot_usd: 0 });
    }
    return byBase.get(base);
  };

  for (const t of openTrades || []) {
    const usd = parseFloat(t.size_usd);
    if (!isFinite(usd) || usd <= 0) continue;
    const r = row(baseOf(t.symbol));
    if (String(t.direction).toUpperCase().includes('SHORT')) r.perp_short_usd += usd;
    else r.perp_long_usd += usd;
  }
  let cash_usd = 0;
  for (const a of walletAssets || []) {
    const usd = Number(a.usd);
    if (!isFinite(usd) || usd <= 0) continue;
    const base = baseOf(a.symbol);
    if (STABLES.has(base)) { cash_usd += usd; continue; }
    row(base).spot_usd += usd;
  }

  const assets = [];
  const warnings = [];
  let grossTotal = 0;
  for (const r of byBase.values()) {
    r.net_usd = round2(r.perp_long_usd - r.perp_short_usd + r.spot_usd);
    r.gross_usd = round2(r.perp_long_usd + r.perp_short_usd + r.spot_usd);
    r.perp_long_usd = round2(r.perp_long_usd);
    r.perp_short_usd = round2(r.perp_short_usd);
    r.spot_usd = round2(r.spot_usd);
    r.flags = [];
    if (r.spot_usd > 0 && r.perp_long_usd > 0) {
      r.flags.push('stacked_long');
      warnings.push(`${r.base}: you hold it on-chain AND are long the perp — `
        + `the same bet twice ($${r.gross_usd.toLocaleString('en-US')} gross).`);
    }
    if (r.spot_usd > 0 && r.perp_short_usd > 0) {
      r.flags.push('hedged');
    }
    grossTotal += r.gross_usd;
    assets.push(r);
  }
  for (const r of assets) {
    if (grossTotal > 0 && r.gross_usd / grossTotal > 0.5 && assets.length > 1) {
      r.flags.push('concentrated');
      warnings.push(`${r.base} is ${Math.round(r.gross_usd / grossTotal * 100)}% `
        + 'of your gross exposure — concentration risk.');
    }
  }
  assets.sort((a, b) => b.gross_usd - a.gross_usd);

  return {
    read_only: true,
    assets,
    cash_usd: round2(cash_usd),
    net_total_usd: round2(assets.reduce((a, r) => a + r.net_usd, 0)),
    gross_total_usd: round2(grossTotal),
    warnings,
    note: 'Exposure nets perp positions against on-chain spot (WETH→ETH, '
      + 'WBTC→BTC); stables count as cash. Intelligence only — nothing here '
      + 'can resize or close a position.',
    generated_at: new Date().toISOString(),
  };
}

/**
 * Load the caller's open positions + wallet and compute.
 *
 * Fails soft, and SAYS SO. The previous version caught the trades query with
 * `catch (e) { /* section empty *\/ }` — leaving `openTrades = []`, which
 * `computeExposure` turns into no assets, which the chat surface renders as
 * "No directional exposure found — no open positions". A database failure
 * therefore answered "how exposed am I?" with "you have nothing", to a user
 * holding leveraged positions, on the one surface they open to check.
 *
 * The wallet half was already honest — it reported `wallet_included` rather
 * than pretending — and this brings the positions half up to that standard.
 * Both reads now report whether they happened, so a caller can render an
 * error state instead of inheriting a confident zero.
 *
 * `wallet_state` is three-valued because `wallet_included: false` was one
 * word for three different facts — no wallet linked, the portfolio call
 * returned nothing, and the call threw — and only the first is "you have no
 * wallet". `wallet_included` is kept as the boolean it always was.
 */
async function buildExposure(userId) {
  let openTrades = [];
  let positionsRead = true;
  try {
    const [rows] = await pool.execute(
      `SELECT symbol, direction, size_usd FROM trades
        WHERE user_id = ? AND status = 'OPEN' ORDER BY opened_at DESC`, [userId]);
    openTrades = rows;
  } catch (e) {
    // NOT an empty book. Nothing below may present it as one.
    positionsRead = false;
  }

  let walletAssets = null;
  let walletState = 'not_linked';
  try {
    const address = await wallet.walletAddressOf(userId);
    if (address) {
      const p = await wallet.getWalletPortfolio(address);
      if (p) {
        walletAssets = p.assets;
        walletState = 'included';
      } else {
        // Linked, and the portfolio call gave us nothing back. We cannot tell
        // an empty wallet from a failed fetch here, so we do not claim either.
        walletState = 'unreadable';
      }
    }
  } catch (e) {
    walletState = 'unreadable';
  }

  return {
    ...computeExposure(openTrades, walletAssets),
    positions_read: positionsRead,
    wallet_state: walletState,
    wallet_included: walletState === 'included',
    // null, not 0: an unread book has an unknown number of positions, and 0
    // is a real answer that a client is entitled to render as "you are flat".
    open_positions: positionsRead ? openTrades.length : null,
  };
}

// ── Chat intercept ───────────────────────────────────────────────────────────

const CHAT_RE = /\b((my|total|current) exposure|exposure (across|check)|overexposed|doubled? (up|exposure)|how (exposed|leveraged) am i)\b/i;

function fmtUsd(v) {
  return '$' + Number(v || 0).toLocaleString('en-US', { maximumFractionDigits: 2 });
}

async function maybeHandleExposureChat(userId, text) {
  if (!CHAT_RE.test(String(text || ''))) return null;
  try {
    const e = await buildExposure(userId);

    // GUARD, not omit. The question is "how exposed am I?" — there is no
    // partial answer to that worth giving, and every wrong answer here is the
    // reassuring one. An unread book must never reach the sentence below.
    if (!e.positions_read) {
      return {
        reply_html: "I couldn't read your open positions just now, so I can't "
          + 'tell you your exposure. <b>This is not a report that you have '
          + 'none</b> — it is a failed read. Try again in a moment, or check '
          + 'your positions directly.',
        intent: 'exposure',
      };
    }

    if (!e.assets.length) {
      // Positions read, and there are none. The wallet clause is three-valued
      // for the same reason: "no wallet linked" was being printed for a wallet
      // that was linked and simply could not be fetched.
      const walletBit = e.wallet_state === 'included'
        ? ' and no non-stable wallet holdings.'
        : e.wallet_state === 'not_linked'
          ? ', and no wallet linked.'
          : ', and your wallet could not be read just now — any spot holdings '
            + 'are NOT included here.';
      return {
        reply_html: 'No directional exposure found — no open positions' + walletBit,
        intent: 'exposure',
      };
    }
    const rows = e.assets.slice(0, 8).map((r) => {
      const bits = [];
      if (r.perp_long_usd) bits.push(`long ${fmtUsd(r.perp_long_usd)}`);
      if (r.perp_short_usd) bits.push(`short ${fmtUsd(r.perp_short_usd)}`);
      if (r.spot_usd) bits.push(`spot ${fmtUsd(r.spot_usd)}`);
      const flag = r.flags.includes('stacked_long') ? ' ⚠️'
        : r.flags.includes('hedged') ? ' 🛡 hedged' : '';
      return `• <b>${r.base}</b> net ${fmtUsd(r.net_usd)} (${bits.join(' · ')})${flag}`;
    });
    const warn = e.warnings.length
      ? `<br><br>⚠️ <b>Worth knowing:</b><br>${e.warnings.map(w => `• ${w}`).join('<br>')}`
      : '';
    // "everywhere" is a claim. When the wallet could not be read this is a
    // perp-only view, and the note below says exposure nets perps against
    // on-chain spot — so without this line the reader takes a partial total
    // for a whole one.
    const gap = e.wallet_state === 'unreadable'
      ? '<br><br>⚠️ <b>Wallet not included</b> — your on-chain holdings could '
        + 'not be read just now, so this covers perps only.'
      : '';
    return {
      reply_html: `🧭 <b>Your exposure</b> (read-only)<br><br>${rows.join('<br>')}`
        + `<br><br>Net ${fmtUsd(e.net_total_usd)} · Gross ${fmtUsd(e.gross_total_usd)}`
        + (e.cash_usd ? ` · Cash (stables) ${fmtUsd(e.cash_usd)}` : '')
        + gap
        + warn
        + `<br><br><i>${e.note}</i>`,
      intent: 'exposure',
    };
  } catch (err) {
    return { reply_html: 'Exposure read hiccup — try again in a moment.', intent: 'exposure' };
  }
}

module.exports = { computeExposure, buildExposure, maybeHandleExposureChat };
