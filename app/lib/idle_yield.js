/**
 * Idle-Asset Yield Optimizer — web side (READ-ONLY, recommendation only).
 *
 * Takes the user's on-chain wallet idle assets (lib/wallet.js) and asks the
 * bot gateway's Python optimizer for the best rate per asset — non-custodial
 * (Lido/Aave, live from DefiLlama) preferred honestly over a marginally-higher
 * custodial CEX rate. There is ONE optimizer (in Python); the web never
 * re-implements the ranking, it just supplies holdings and renders the answer.
 *
 * Nothing here moves funds — it surfaces "your idle ETH could earn X% at Lido,
 * where you keep custody". Acting on it stays a deliberate, out-of-band step.
 */

const gateway = require('./gateway');
const wallet = require('./wallet');
const { esc } = require('./esc');

// Aggregate a wallet portfolio's priced assets into optimizer holdings:
// one {asset, usd_value} per symbol (summed across chains). Unpriced assets
// are skipped — we never feed the optimizer a made-up value.
function holdingsFromWallet(portfolio) {
  const bySymbol = new Map();
  for (const a of (portfolio && portfolio.assets) || []) {
    if (a.usd == null || !isFinite(a.usd) || a.usd <= 0) continue;
    const sym = String(a.symbol || '').toUpperCase();
    if (!sym) continue;
    bySymbol.set(sym, (bySymbol.get(sym) || 0) + Number(a.usd));
  }
  return [...bySymbol.entries()].map(([asset, usd_value]) => ({
    asset, usd_value: Math.round(usd_value * 100) / 100, location: 'wallet',
  }));
}

/**
 * Build the idle-yield recommendation for a web user.
 *  `ident`  — resolved bot identity ({ id }) for the gateway call.
 *  `userId` — web user id, for the SIWE wallet lookup.
 * Fails soft to a { available: false } shape.
 */
async function buildIdleYield(ident, userId) {
  if (!gateway.isConfigured()) {
    return { read_only: true, available: false, error: 'not_configured' };
  }
  let holdings = [];
  let address = null;
  try {
    address = await wallet.walletAddressOf(userId);
    if (address) {
      const p = await wallet.getWalletPortfolio(address);
      holdings = holdingsFromWallet(p);
    }
  } catch (e) {
    holdings = [];
  }
  if (!address) {
    return { read_only: true, available: true, wallet_linked: false,
      recommendations: [], note: 'Link a wallet (Sign-In with Ethereum) to '
        + 'scan your idle on-chain assets for the best non-custodial rate.' };
  }
  if (!holdings.length) {
    return { read_only: true, available: true, wallet_linked: true,
      recommendations: [], note: 'No priced idle assets found in your wallet '
        + 'on the tracked chains.' };
  }
  try {
    const r = await gateway.postGateway('/idleyield',
      { telegram_id: ident.id, holdings, prefer_noncustodial: true }, 30000);
    if (r.status === 200 && r.data && !r.data.error) {
      return { ...r.data, available: true, wallet_linked: true };
    }
    return { read_only: true, available: false, wallet_linked: true, error: 'gateway' };
  } catch (e) {
    return { read_only: true, available: false, wallet_linked: true, error: 'gateway' };
  }
}

// ── The one card ─────────────────────────────────────────────────────────────
//
// ONE renderer. Both chat doors fetch this card over
// `/api/bot/sync/card/idleyield`. Website chat does not match the sentence
// itself. The read is this caller's linked wallet, never the operator's
// exchange book (that scan stays `/idleyield`, admin-only). Nothing here
// places, confirms, sizes, or stakes. Dollars stay on this private card.

function fmtUsd(v) {
  // null and undefined are unread. 0 is a measured empty balance.
  return v == null ? '—'
    : '$' + Number(v).toLocaleString('en-US', { maximumFractionDigits: 2 });
}

function fmtApy(v) {
  // A missing rate is not 0%. 0 is a measured zero yield.
  return typeof v === 'number' && Number.isFinite(v) ? String(v) : '—';
}

/**
 * The idle-yield card — ONE renderer for both surfaces. The bot's shared
 * door and the website's shared door both fetch this card over the sync
 * channel (`GET /api/bot/sync/card/idleyield?telegram_id=`). Website chat
 * does not match the sentence itself. `telegramId` is the identity the bot
 * keys the gateway call on (`web:<uid>` or a linked Telegram id); `userId`
 * is the web account the wallet is linked to. A caller the website cannot
 * map is unlinked, never a guessed wallet. Nothing here places, confirms,
 * sizes, or stakes. Dollars stay on this private card.
 */
async function idleyieldChatCard(telegramId, userId) {
  try {
    const y = await buildIdleYield({ id: String(telegramId || '') }, userId);
    if (!y.available) {
      return { reply_html: 'The idle-yield scanner is briefly unavailable — try again shortly.',
        intent: 'idleyield' };
    }
    if (!y.wallet_linked) {
      return { reply_html: '💤 ' + esc(y.note || ''), intent: 'idleyield' };
    }
    const recd = (y.recommendations || []).filter((r) => r && r.status === 'recommended' && r.best);
    if (!recd.length) {
      return { reply_html: '💤 ' + esc(y.note || 'No idle assets matched a known rate right now.'),
        intent: 'idleyield' };
    }
    const lines = recd.slice(0, 6).map((r) => {
      const b = r.best;
      const cust = b.custodial ? 'custodial' : 'non-custodial';
      const note = r.note ? `<br>   <i>↳ ${esc(r.note)}</i>` : '';
      return `• <b>${esc(r.asset)}</b> ${fmtUsd(r.idle_usd)} → <b>${fmtApy(b.apy)}%</b> `
        + `(${esc(b.source)}, ${cust}) ≈ ${fmtUsd(r.est_year_usd)}/yr`
        + note;
    });
    return {
      reply_html: `💤→💸 <b>Idle-yield — best rates for your wallet</b> (read-only)<br><br>`
        + lines.join('<br>')
        + `<br><br>Total if deployed: <b>${fmtUsd(y.total_est_year_usd)}/yr</b>`
        + `<br><i>Recommendation only — RUNECLAW never moves your funds. Non-custodial `
        + `means you keep the keys.</i>`,
      intent: 'idleyield',
    };
  } catch (e) {
    return { reply_html: 'Idle-yield read hiccup — try again in a moment.', intent: 'idleyield' };
  }
}

module.exports = { buildIdleYield, holdingsFromWallet, idleyieldChatCard };
