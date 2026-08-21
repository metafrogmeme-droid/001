/**
 * Unified cross-venue net worth — READ-ONLY aggregation.
 *
 * One view of everything the user holds, combined from three sources that
 * already exist and are already read-only:
 *   cex    — their connected exchange, fetched BOT-side over the gateway
 *            (keys never leave the bot process; same read-only call the
 *            connect-time validators make)
 *   wallet — their SIWE-linked wallet (lib/wallet.js chain reads)
 *   paper  — their simulated paper portfolio
 *
 * Honesty rule: the REAL total sums only real money (cex + wallet). Paper
 * equity is shown as its own clearly-labelled simulated line and is NEVER
 * added into the total.
 */

const gateway = require('./gateway');
const wallet = require('./wallet');

function round2(v) { return Math.round(v * 100) / 100; }

/**
 * Build the unified snapshot for a web user. `ident` is the resolved bot
 * identity ({ id }); `userId` the web user id (for the wallet lookup).
 * Every section fails soft to an { available: false } shape.
 */
async function buildNetWorth(ident, userId) {
  const sections = { cex: null, wallet: null, paper: null };

  // Bot gateway: paper + connected-CEX equity in one call.
  if (gateway.isConfigured()) {
    try {
      const r = await gateway.getGateway(
        `/networth?telegram_id=${encodeURIComponent(ident.id)}`, 30000);
      if (r.status === 200 && r.data) {
        sections.paper = r.data.paper || null;
        sections.cex = r.data.cex || null;
      } else {
        sections.cex = { available: false, error: 'gateway' };
      }
    } catch (e) {
      sections.cex = { available: false, error: 'gateway' };
    }
  } else {
    sections.cex = { available: false, error: 'not_configured' };
  }

  // The gateway is the only source of exchange EQUITY, but not of whether an
  // exchange is connected — the web stores that itself in exchange_status, and
  // the venues panel reads it. When the gateway cannot answer, saying "none
  // connected" contradicts the panel directly above it and sends a user who
  // already linked Bitget off to re-enter keys that are sitting right there.
  //
  // "I cannot reach the bot" and "you have no exchange" are different answers.
  if (sections.cex && !sections.cex.connected) {
    try {
      const { pool } = require('../db');
      const [rows] = await pool.execute(
        'SELECT exchange FROM exchange_status WHERE user_id = ? AND connected = 1 LIMIT 1',
        [userId]);
      if (rows && rows.length) {
        sections.cex = {
          ...sections.cex,
          connected: true,
          ok: false,
          venue: rows[0].exchange || 'bitget',
          // Read by the UI instead of the misleading "none connected" line.
          detail: sections.cex.error === 'not_configured'
            ? 'equity unreadable — the bot link is not configured here'
            : 'equity unreadable — the bot did not answer just now',
        };
      }
    } catch (e) { /* leave the gateway's answer as-is */ }
  }

  // SIWE wallet (web-side chain reads).
  try {
    const address = await wallet.walletAddressOf(userId);
    if (!address) {
      sections.wallet = { linked: false };
    } else {
      const p = await wallet.getWalletPortfolio(address);
      // KEEPING total_usd AND DROPPING p.chains DESTROYED THE ONLY EVIDENCE.
      //
      // readChain() never throws — a chain whose RPC is down returns
      // `{assets: [], total_usd: 0, error: 'rpc unreadable'}` — and readWallet
      // sums those zeros. So a dead chain contributes 0 to a total that is
      // then presented as the whole wallet. Discarding `chains` here meant
      // nothing downstream could tell a $0 chain from an unread one.
      //
      // The sibling reading the identical payload gets this right:
      // holdings.js does `total_usd: c.error ? null : ...` plus an
      // `anyUnreadable` flag, and the /web3 panel on the same dashboard says
      // "ethereum, arbitrum unreadable right now (RPC)". Two panels, one
      // dataset, opposite claims — the fix landed on one and not the other.
      // test/networth_connected_honesty.test.js records the production case:
      // "Three chains all reported 'rpc unreadable'".
      //
      // OMIT is wrong here and GUARD is right: this is a single-source total,
      // and a wallet total missing a chain is not a smaller wallet, it is an
      // unknown one. dashboard.js ALREADY renders `total_usd != null ? ... :
      // 'unreadable'` — the honest branch existed and was simply never
      // reachable, because this line could not produce null.
      const _chains = (p && Array.isArray(p.chains)) ? p.chains : [];
      const _unreadable = _chains.filter((c) => c && c.error);
      sections.wallet = p
        ? { linked: true, address: p.address,
            total_usd: _unreadable.length ? null : p.total_usd,
            assets: p.assets.length, unpriced: p.unpriced,
            // Carry WHY, and which — an operator cannot act on "unreadable".
            unreadable_chains: _unreadable.map((c) => c.label || c.chain),
            partial: _unreadable.length > 0 }
        : { linked: true, available: false };
    }
  } catch (e) {
    sections.wallet = { linked: true, available: false };
  }

  // NFT collectibles on the linked wallet — CONTEXT ONLY. Floors are asks,
  // not liquidation values, and NFT markets are thin: collectibles are shown
  // but their floor value is NEVER summed into the real total.
  try {
    const address = sections.wallet && sections.wallet.address;
    if (address) {
      const nfts = await require('./opensea').getWalletNfts(address);
      sections.collectibles = nfts.available
        ? { available: true, count: nfts.count,
            collections: [...new Set(nfts.items.map(i => i.collection).filter(Boolean))].slice(0, 5),
            valuation_note: 'floors are asks, not liquidation values — '
              + 'collectibles are never counted in the total' }
        : { available: false, reason: nfts.reason };
    } else {
      sections.collectibles = { available: false, reason: 'no_wallet' };
    }
  } catch (e) {
    sections.collectibles = { available: false, reason: 'error' };
  }

  // Real total: only real money. Paper stays out by design.
  let total = 0;
  let counted = 0;
  // `Number.isFinite`, not the global. The global COERCES first, so
  // `isFinite(null)` is TRUE (Number(null) === 0) and `isFinite('')` is TRUE
  // — both would count an absent reading as a measured zero, and the null
  // this function now produces for an unreadable wallet would have sailed
  // straight through the guard meant to catch it. Number.isFinite(null) is
  // false, which is the question actually being asked.
  let unknown = 0;
  if (sections.cex && sections.cex.connected && sections.cex.ok
      && Number.isFinite(sections.cex.equity_usd)) {
    total += Number(sections.cex.equity_usd); counted++;
  } else if (sections.cex && sections.cex.connected) {
    unknown++;
  }
  if (sections.wallet && sections.wallet.linked
      && Number.isFinite(sections.wallet.total_usd)) {
    total += Number(sections.wallet.total_usd); counted++;
  } else if (sections.wallet && sections.wallet.linked) {
    unknown++;
  }

  return {
    read_only: true,
    sections,
    total_real_usd: counted ? round2(total) : null,
    sources_counted: counted,
    // OMIT, with the omission stated. A composite of two sources should not
    // blank because one died — but a partial sum printed as "Real net worth"
    // is a wrong number wearing a measured number's authority, so the caller
    // is told how many sources it is missing rather than being left to infer
    // it from `sources_counted` alone.
    sources_unknown: unknown,
    partial: counted > 0 && unknown > 0,
    note: 'Real total = connected exchange + on-chain wallet. '
      + 'Paper equity is simulated and never included.'
      + (counted > 0 && unknown > 0
          ? ` ${unknown} source${unknown === 1 ? '' : 's'} could not be read, `
            + 'so this total is incomplete.'
          : ''),
    generated_at: new Date().toISOString(),
  };
}

// ── Chat intercept ───────────────────────────────────────────────────────────

const CHAT_RE = /\b(net ?worth|total (balance|holdings|equity)( across| everywhere)?|balance across (all )?(exchanges|venues)|everything i (own|hold))\b/i;

function fmtUsd(v) {
  return v == null ? '—'
    : '$' + Number(v).toLocaleString('en-US', { maximumFractionDigits: 2 });
}

/**
 * Chat handler. Needs the resolved bot identity, which only the chat route
 * has — so unlike the other intercepts this one takes (ident, userId, text).
 */
async function maybeHandleNetWorthChat(ident, userId, text) {
  if (!CHAT_RE.test(String(text || ''))) return null;
  try {
    const n = await buildNetWorth(ident, userId);
    const lines = [];
    const c = n.sections.cex;
    if (c && c.connected) {
      lines.push(c.ok
        ? `• <b>${(c.venue || 'exchange').toUpperCase()}</b> (connected exchange): <b>${fmtUsd(c.equity_usd)}</b>`
        : `• <b>${(c.venue || 'exchange').toUpperCase()}</b>: unreadable right now (${c.detail || 'venue error'})`);
    } else {
      lines.push('• Exchange: none connected — /connect in Telegram links one (read-only here).');
    }
    const w = n.sections.wallet;
    if (w && w.linked) {
      lines.push(w.total_usd != null
        ? `• <b>Wallet</b> (on-chain, read-only): <b>${fmtUsd(w.total_usd)}</b> across ${w.assets} asset(s)`
        : '• <b>Wallet</b>: linked but unreadable right now.');
    } else {
      lines.push('• Wallet: none linked — Sign-In with Ethereum adds a read-only mirror.');
    }
    const p = n.sections.paper;
    if (p && p.equity_usd != null) {
      lines.push(`• <i>Paper portfolio (simulated, not counted): ${fmtUsd(p.equity_usd)}</i>`);
    }
    return {
      reply_html: `💼 <b>Net worth — everywhere</b> (read-only)<br><br>${lines.join('<br>')}`
        + `<br><br>Real total: <b>${fmtUsd(n.total_real_usd)}</b>`
        + `<br><i>${n.note} RUNECLAW can read these balances, never move them.</i>`,
      intent: 'networth',
    };
  } catch (e) {
    return { reply_html: 'Net-worth read hiccup — try again in a moment.', intent: 'networth' };
  }
}

module.exports = { buildNetWorth, maybeHandleNetWorthChat };
