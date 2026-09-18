/**
 * Meme & AI-agent token radar — READ-ONLY on-chain market intelligence.
 *
 * Sources live DEX pairs from DEXScreener (public API, no key) and presents
 * them with a SAFETY-FORWARD risk read: liquidity depth, pair age, and 24h
 * buy/sell balance — the same signals that will later gate any agent buy.
 * Deliberately non-extractive: the radar never launches tokens and never
 * trades; it exists to help a human (or the agent, read-only) see what's
 * moving on-chain AND how dangerous it is, not to shill.
 *
 * Pure core (`buildRadar`) takes an array of DEXScreener pair objects, so it's
 * deterministic in tests; the network fetch is injectable + best-effort.
 *
 * WHAT THE FEED DID NOT REPORT IS NOT ZERO, and this module used to coerce at
 * the NORMALIZER, which is the earliest place the distinction can be lost:
 * `num(p.volume && p.volume.h24) || 0` stamped an absent volume block as a
 * measured `0`, and that one value then decided the row's rank (the header
 * claims "ranked by real volume"), its survival of the payload cap, the
 * sector total, the chain total, the chain order and `top_by_volume`. The
 * buy/sell counts went the same way into the SAFETY read, where an unreported
 * `sells` was indistinguishable from a measured zero — so a pair whose sells
 * count the feed did not carry fired `no-sells-yet` ("can't exit?") AND
 * `buys-only-skew` and was escalated to `extreme`, which is a fabricated
 * safety finding on the read a future agent-buy is meant to gate on. Every
 * one of those fields is three-valued now, and every aggregate over them
 * carries its sample.
 */

const CHAINS = {
  solana: 'Solana', base: 'Base', ethereum: 'Ethereum', bsc: 'BNB Chain',
  arbitrum: 'Arbitrum', polygon: 'Polygon', avalanche: 'Avalanche',
};

function num(v) { return (v == null || !isFinite(Number(v))) ? null : Number(v); }
function round2(v) { return v == null ? null : Math.round(v * 100) / 100; }

/** The three things the safety read looks at. The denominator for `unread`. */
const RISK_SUBJECTS = ['liquidity', 'age', 'flow'];

/**
 * Honest risk read from the on-chain signals DEXScreener gives us. This is the
 * SAME liquidity/age/flow read a future agent-buy will gate on — surfaced here
 * so nothing is hidden behind a green number.
 *
 * `tier` is a FLOOR, not an all-clear: memecoins are high-risk by default and
 * that holds without reading anything, so an unread signal can neither lower
 * it nor raise it. What an unread signal does is go in `unread`, because
 * `high` over one measured signal and `high` over three are different facts
 * and the caller must be able to say which it is looking at.
 *
 * The flow flags need BOTH counts. `totalTx` is a total, and a threshold over
 * a total that includes an unread row is the partial-sum shape: with the old
 * coercion `buys=400, sells=<unreported>` cleared the `>= 20` floor on the
 * buys alone and then read `sells === 0` as a measurement.
 */
function riskRead(liqUsd, ageHours, buys, sells) {
  const flags = [];
  const unread = [];

  if (liqUsd == null) unread.push('liquidity');
  else if (liqUsd < 10_000) flags.push('very-low-liquidity');
  else if (liqUsd < 50_000) flags.push('low-liquidity');

  if (ageHours == null) unread.push('age');
  else if (ageHours < 24) flags.push('under-24h-old');
  else if (ageHours < 168) flags.push('under-1w-old');

  if (buys == null || sells == null) {
    unread.push('flow');
  } else {
    const totalTx = buys + sells;
    if (totalTx >= 20 && sells === 0) flags.push('no-sells-yet');       // can't exit?
    if (totalTx >= 50 && buys / Math.max(totalTx, 1) > 0.9) flags.push('buys-only-skew');
  }

  // Tier: memecoins are high-risk by default; escalate on the flags above —
  // and only on a flag, which is a MEASUREMENT. An unread signal is in
  // `unread`, never an escalation and never a discount.
  let tier = 'high';
  if (flags.includes('very-low-liquidity') || flags.includes('under-24h-old')
      || flags.includes('no-sells-yet')) tier = 'extreme';
  return { tier, flags, unread };
}

function normalizePair(p) {
  if (!p || typeof p !== 'object') return null;
  const price = num(p.priceUsd);
  if (price == null) return null;
  const liq = num(p.liquidity && p.liquidity.usd);
  const vol = num(p.volume && p.volume.h24);
  const chg = num(p.priceChange && p.priceChange.h24);
  const created = num(p.pairCreatedAt);          // ms epoch
  const buys = num(p.txns && p.txns.h24 && p.txns.h24.buys);
  const sells = num(p.txns && p.txns.h24 && p.txns.h24.sells);
  return {
    chain: p.chainId || 'unknown',
    chain_label: CHAINS[p.chainId] || p.chainId || 'unknown',
    dex: p.dexId || null,
    symbol: (p.baseToken && p.baseToken.symbol) || '?',
    name: (p.baseToken && p.baseToken.name) || null,
    address: (p.baseToken && p.baseToken.address) || null,
    quote: (p.quoteToken && p.quoteToken.symbol) || null,
    price_usd: price,
    change_24h_pct: round2(chg),
    volume_24h_usd: vol == null ? null : Math.round(vol),
    liquidity_usd: liq == null ? null : Math.round(liq),
    fdv_usd: num(p.fdv) == null ? null : Math.round(num(p.fdv)),
    buys_24h: buys, sells_24h: sells,
    created_at: created,
    url: p.url || null,
    _created: created,
  };
}

/**
 * A 24h volume total over the rows that REPORTED one, with its sample.
 *
 * `null` for nothing read, never `0`: "this chain traded nothing" and "the
 * feed carried no volume for any of its pairs" are different facts and `$0`
 * is only one of them. Same shape as `rwa.js`'s `sumVolume`, for the reason
 * that slice recorded.
 */
function volumeTotal(rows) {
  const read = rows.filter((t) => t.volume_24h_usd != null);
  return {
    usd: read.length ? read.reduce((a, t) => a + t.volume_24h_usd, 0) : null,
    scored: read.length,
    total: rows.length,
  };
}

/**
 * Descending by 24h volume, with an UNREAD volume never ranked.
 *
 * A raw `(b.volume || 0) - (a.volume || 0)` gave an unreported volume the
 * bottom of the ranking as though it had been measured there. Unread rows go
 * after every ranked one — they are listed, not placed — and the payload
 * publishes how many, because a listing whose order claims a ranking has to
 * say which of its rows are not in it.
 */
function byVolumeDesc(a, b) {
  const av = a.volume_24h_usd, bv = b.volume_24h_usd;
  if (av == null && bv == null) return 0;
  if (av == null) return 1;
  if (bv == null) return -1;
  return bv - av;
}

/**
 * Build the radar from an array of DEXScreener pair objects. Pure.
 *
 * @param {Array|null} pairs raw DEXScreener pairs, or `null` for a FAILED
 *   read — which is not an empty market. `feed_read` carries the difference
 *   so no reader has to guess: every consumer of an empty radar used to
 *   render a measured "nothing is out there".
 * @param {number} nowMs current time (ms) — injected so age is deterministic
 */
function buildRadar(pairs, nowMs) {
  const feed_read = Array.isArray(pairs);
  const now = num(nowMs) || 0;
  const seen = new Set();
  const rows = [];
  for (const raw of (feed_read ? pairs : [])) {
    const t = normalizePair(raw);
    if (!t || !t.address) continue;
    const key = `${t.chain}:${t.address}`;
    if (seen.has(key)) continue;                    // dedupe by chain+token
    seen.add(key);
    const ageHours = (t._created && now) ? Math.max(0, (now - t._created) / 3_600_000) : null;
    t.age_hours = ageHours == null ? null : Math.round(ageHours * 10) / 10;
    t.risk = riskRead(t.liquidity_usd, ageHours, t.buys_24h, t.sells_24h);
    delete t._created;
    rows.push(t);
  }

  // Rank by 24h volume (real activity), not price change (pumps).
  rows.sort(byVolumeDesc);

  const byChain = {};
  for (const t of rows) (byChain[t.chain] = byChain[t.chain] || []).push(t);
  const chains = Object.keys(byChain).map((c) => {
    const vol = volumeTotal(byChain[c]);
    return {
      chain: c,
      chain_label: CHAINS[c] || c,
      count: byChain[c].length,
      volume_24h_usd: vol.usd,
      volume_scored: vol.scored,
    };
  }).sort(byVolumeDesc);

  const extreme = rows.filter(t => t.risk.tier === 'extreme').length;
  const sorted = [...rows].filter(t => t.change_24h_pct != null)
    .sort((a, b) => b.change_24h_pct - a.change_24h_pct);
  const vol = volumeTotal(rows);
  const ranked = rows.filter(t => t.volume_24h_usd != null);

  return {
    generated_at: new Date().toISOString(),
    source: 'DEXScreener live DEX pairs (public, read-only)',
    read_only: true,
    // False means the feed did not answer — NOT that nothing is trending.
    feed_read,
    disclaimer: 'Memecoins are extremely high risk — most go to zero. This is '
      + 'market intelligence with an explicit safety read, NOT advice and NOT a '
      + 'launch tool. The agent never mints tokens.',
    summary: {
      tokens: rows.length,
      volume_24h_usd: vol.usd,
      volume_scored: vol.scored,
      volume_total: vol.total,
      extreme_risk: extreme,
      // Rows for which the feed did not report at least one safety signal.
      // `0 at extreme risk` over these reads as "all three checked on every
      // row", which is the claim the MCP tool description also makes.
      risk_unread: rows.filter(t => t.risk.unread.length > 0).length,
      risk_subjects: RISK_SUBJECTS.length,
      // Only a row that reported a volume can be the top by volume.
      top_gainer: sorted[0] || null,
      top_by_volume: ranked[0] || null,
    },
    chains,
    tokens: rows.slice(0, 40),          // cap payload
  };
}

// ── Network fetch (best-effort, injectable) ─────────────────────────────────

const DS = 'https://api.dexscreener.com';

/**
 * Default: trending "boosted" tokens → hydrate to full pairs. Boost is itself
 * a promotion signal (surfaced via the risk read), so we never treat it as
 * quality.
 *
 * `null` for a READ THAT FAILED, `[]` only for a read that succeeded and
 * carried nothing. This used to answer `[]` for four different facts — the
 * boost endpoint refusing, the boost list being genuinely empty, the pairs
 * endpoint refusing, and any throw — so a network fault reached every reader
 * as a measured empty universe: the card said the feed "may be refreshing"
 * (a guess) and the dashboard panel said, in fourteen languages, that no pair
 * cleared a liquidity and age floor this radar does not have.
 */
async function fetchTrendingPairs() {
  try {
    const boostRes = await fetch(`${DS}/token-boosts/top/v1`,
      { signal: AbortSignal.timeout(10_000) });
    if (!boostRes.ok) return null;
    const boosts = await boostRes.json();
    if (!Array.isArray(boosts)) return null;      // a 200 that carried no rows
    const addrs = boosts.map(b => b && b.tokenAddress).filter(Boolean).slice(0, 30);
    if (!addrs.length) return [];                 // read, and nothing is boosted
    const res = await fetch(`${DS}/latest/dex/tokens/${addrs.join(',')}`,
      { signal: AbortSignal.timeout(10_000) });
    if (!res.ok) return null;
    const data = await res.json();
    return (data && Array.isArray(data.pairs)) ? data.pairs : null;
  } catch (e) {
    return null;
  }
}

let fetchPairs = fetchTrendingPairs;
function setPairFetcher(fn) { fetchPairs = fn || fetchTrendingPairs; }

async function getRadar() {
  return buildRadar(await fetchPairs(), Date.now());
}

// ── Chat intercept ──────────────────────────────────────────────────────────

// Must cover the Hub one-tap chip's exact ask ("meme radar") — a chip whose
// phrase misses this regex falls through to the bot LLM, which honestly
// reports it has no radar access (live incident, 2026-07-20 screenshot).
const CHAT_RE = /\b(meme ?(radar|coins?|tokens?)|dexscreener|degen|pump\.?fun|ai[- ]agent tokens?)\b/i;

const { esc } = require('./esc');
// One volume rendering, one percent rendering and one sample caveat across
// the three cards that print them; `card_nums.js` records why this file's
// private copy was the one that rotted.
const { pct, fmtVol, cover } = require('./card_nums');

/**
 * The meme radar as the chat card — ONE renderer for both surfaces; the bot's
 * /meme_radar fetches it over the sync channel
 * (`GET /api/bot/sync/card/meme_radar`). Public: DEXScreener's feed, no
 * account in it. Token symbols and chain labels are the FEED's text, so they
 * are escaped: a token named `<b` must not break the card on either surface.
 *
 * Three top-level answers, because three different things happen:
 *   - the feed did not answer -> a FAILED read, said so;
 *   - it answered and carried no trending pairs -> a real market fact;
 *   - it carried pairs -> the radar.
 * The first two used to be one sentence ("found nothing live right now — the
 * DEXScreener feed may be refreshing"), which both guesses a cause and reads
 * as a measurement of a quiet market.
 */
async function memeChatCard() {
  try {
    const r = await getRadar();
    if (!r.feed_read) {
      return {
        reply_html: 'The DEXScreener feed could not be read, so the meme radar '
          + 'has nothing to report. That is a failed read, not a quiet market.',
        intent: 'meme',
      };
    }
    if (!r.summary.tokens) {
      return {
        reply_html: 'The DEXScreener feed answered and is carrying no trending '
          + 'on-chain pairs right now.',
        intent: 'meme',
      };
    }
    const s = r.summary;
    const top = r.tokens.slice(0, 5).map((t) => {
      const chg = t.change_24h_pct == null ? '' : ` ${pct(t.change_24h_pct)}`;
      const risk = t.risk.tier === 'extreme' ? ' ⚠️ extreme' : '';
      // A row whose safety signals were not all reported says so: `high` over
      // one measured signal and `high` over three are different facts. It
      // counts what is UNREAD rather than what was read — `safety 2/3` on a
      // risk marker reads as two checks PASSED, which is the opposite claim.
      const n = t.risk.unread.length;
      const partial = n ? ` · ${n} safety signal${n > 1 ? 's' : ''} unread` : '';
      return `• <b>${esc(t.symbol)}</b> (${esc(t.chain_label)})${chg} · `
        + `${fmtVol(t.volume_24h_usd)} vol · ${fmtVol(t.liquidity_usd)} liq${risk}${partial}`;
    });
    const partialNote = s.risk_unread
      ? `<br><br><i>The safety read is liquidity depth, pair age and 24h `
        + `buy/sell flow. ${s.risk_unread} of ${s.tokens} `
        + `row${s.tokens > 1 ? 's' : ''} ${s.risk_unread > 1 ? 'are' : 'is'} `
        + `missing at least one, `
        + `so the risk tier there was reached from fewer signals.</i>`
      : '';
    return {
      reply_html:
        `🟣 <b>Meme &amp; AI-token radar</b> — DEXScreener, read-only<br><br>`
        + `${s.tokens} tokens · ${fmtVol(s.volume_24h_usd)} 24h volume`
        + `${cover(s.volume_24h_usd, s.volume_scored, s.volume_total)} · `
        + `<b>${s.extreme_risk}</b> flagged extreme-risk<br><br>`
        + top.join('<br>')
        + partialNote
        + '<br><br><i>Memecoins are extremely high risk — most go to zero. This is '
        + 'intelligence with a safety read, not advice. The agent never launches tokens.</i>',
      intent: 'meme',
    };
  } catch (e) {
    return {
      reply_html: 'The meme radar could not be read just now — that is a '
        + 'failed read, not a quiet market. Try again in a moment.',
      intent: 'meme',
    };
  }
}

async function maybeHandleMemeChat(userId, text) {
  if (!CHAT_RE.test(String(text || ''))) return null;
  return memeChatCard();
}

module.exports = { CHAT_RE,
  CHAINS, RISK_SUBJECTS, riskRead, normalizePair, buildRadar,
  volumeTotal, byVolumeDesc,
  getRadar, setPairFetcher, maybeHandleMemeChat, memeChatCard,
};
