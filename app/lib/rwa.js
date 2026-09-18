/**
 * RWA & on-chain radar — READ-ONLY market intelligence.
 *
 * Tracks the tokenized real-world-asset narrative through the venue's OWN
 * live perpetual tickers: a curated, categorized universe of RWA platforms,
 * RWA-narrative chains, and RWA-adjacent DeFi, filtered at runtime to what
 * the exchange actually lists (an unlisted symbol is simply omitted — the
 * radar never shows a market you can't verify live). Aggregates are
 * volume-weighted and always computed, never cached opinions.
 *
 * Deliberately NOT wired to trading: no execution, no signals, no bot
 * behavior change. On-chain/DEX execution remains design-only pending
 * operator + legal review.
 */

const { getTickers } = require('./tickers');

// Curated universe (base coins; hand-verified against Bitget USDT-M
// listings 2026-07). Runtime filtering keeps this honest as listings churn.
const RWA_UNIVERSE = [
  {
    key: 'platforms',
    title: 'RWA platforms & issuers',
    blurb: 'Protocols that tokenize treasuries, credit and funds on-chain.',
    bases: ['ONDO', 'POLYX', 'OM', 'RSR', 'CFG', 'MPL', 'CTC', 'TRU', 'PLUME'],
  },
  {
    key: 'chains',
    title: 'RWA-narrative chains',
    blurb: 'L1/L2s positioning as settlement rails for tokenized assets.',
    bases: ['ETH', 'XRP', 'POL', 'AVAX', 'ALGO', 'HBAR', 'XDC', 'XLM', 'INJ', 'CHZ'],
  },
  {
    key: 'defi',
    title: 'RWA-adjacent DeFi',
    blurb: 'Yield tokenization and lending markets absorbing RWA collateral.',
    bases: ['PENDLE', 'AAVE', 'MKR', 'SKY', 'GFI', 'JTO', 'LDO'],
  },
];

function round2(v) { return Math.round(v * 100) / 100; }

// How many bases the curated universe tracks — a CONSTANT, not a
// measurement, so it is computed once here rather than inside the rollup
// (which must carry no arithmetic of its own; see `weightedChange`).
const UNIVERSE_SIZE = RWA_UNIVERSE.reduce((a, c) => a + c.bases.length, 0);

// ── ONE aggregate, read by the per-category loop AND the sector rollup ──────
//
// These three existed twice: cured inside the category loop, and left as the
// uncured original in the sector rollup forty lines below it. Driven on the
// same three tokens with one unreadable 24h change, the two answered -7.04%
// and -5.94% — and ONE CARD PRINTS BOTH, three lines apart, the honest
// category row directly under the diluted headline. A second copy of an
// aggregate is a second answer, and this is how the second answer arose, so
// there is one now and both levels call it.

/**
 * Volume-weighted 24h change over the tokens that REPORTED one, renormalised
 * by the share they cover — with the sample beside the mean.
 *
 * `null * volume` is 0 in JS, so an unfiltered reduce adds nothing to the
 * numerator while that token's volume stays in the denominator: every
 * unreadable row DILUTES the mean toward zero, and always in the flattering
 * direction (a loss understated, a gain understated). With no readable change
 * at all it answers a measured `0` — "unreadable is break-even", CLAUDE.md's
 * own table, on a public sector headline.
 *
 * `scored`/`total` travel with the figure for the reason `summary.scored` and
 * `average_r` do: a mean over 2 of 3 rows and a mean over 3 of 3 are different
 * claims that render identically.
 */
function weightedChange(tokens) {
  const read = tokens.filter((t) => t.change_24h_pct != null);
  const readW = read.filter((t) => t.volume_24h_usd != null);
  const readVol = readW.reduce((a, t) => a + t.volume_24h_usd, 0);
  if (!read.length) return { pct: null, scored: 0, total: tokens.length };
  // A token can only be VOLUME-weighted if its volume was read too; with no
  // readable volume anywhere the equal-weight mean is the honest fallback.
  const pct = readVol > 0
    ? readW.reduce((a, t) => a + t.change_24h_pct * t.volume_24h_usd, 0) / readVol
    : read.reduce((a, t) => a + t.change_24h_pct, 0) / read.length;
  return { pct: round2(pct), scored: read.length, total: tokens.length };
}

/**
 * 24h quote volume over the tokens that reported one, with the sample.
 *
 * Arithmetically identical to coercing the nulls (`a + null` is `a`) — the
 * filter states the INTENT rather than leaning on a coercion a later edit
 * could change out from under it, and `scored` is what stops the total being
 * "a partial total, printed as whole". Mutating the filter away is an
 * equivalent mutant; mutating `scored` away is not, which is the honest
 * reason the count is on the payload.
 */
function sumVolume(tokens) {
  const read = tokens.filter((t) => t.volume_24h_usd != null);
  return {
    // `null` when nothing reported a volume, for the same reason
    // `weightedChange` answers a null pct: a `0` total reads as "this sector
    // traded nothing", which is a measurement, and the first draft of THIS
    // function published it — the defect one level up, rebuilt inside the seam
    // written to remove it, and the card is what said so.
    usd: read.length ? read.reduce((a, t) => a + t.volume_24h_usd, 0) : null,
    scored: read.length,
    total: tokens.length,
  };
}

/**
 * Tokens ranked by 24h change, best first — unreadable rows EXCLUDED, not
 * sorted last.
 *
 * The sector rollup used the raw `b.change - a.change`, where `null` coerces
 * to 0: in a DOWN market the token nobody could read sorted above every real
 * loser and was published as `top_gainer`, rendering "Top: RSR +null%"; in an
 * UP market it was the `top_loser`. `meme.js` filters before sorting — the
 * cure, one file over. Here the extremes NAME a token, so an unreadable row
 * must not be nameable as either; the per-category table still lists it,
 * sorted last, because there the row is the reading.
 */
function rankByChange(tokens) {
  return tokens.filter((t) => t.change_24h_pct != null)
    .sort((a, b) => b.change_24h_pct - a.change_24h_pct);
}

/**
 * Build the radar snapshot from a ticker map ({ BTCUSDT: {price, change,
 * volume} }). Pure — injectable tickers make it deterministic in tests.
 */
function buildRadar(tickers) {
  // A read that failed is not an empty venue: `getTickers` THROWS on a
  // non-ok HTTP, so a null here is a caller that swallowed one. Treated as
  // zero markets read rather than crashing on a property access, and
  // `markets_read` below is what tells the renderer which.
  const map = tickers || {};
  const btc = map.BTCUSDT || null;
  const categories = [];
  const all = [];

  for (const cat of RWA_UNIVERSE) {
    const tokens = [];
    for (const base of cat.bases) {
      const tk = map[`${base}USDT`];
      if (!tk || !isFinite(tk.price)) continue;      // unlisted → omitted
      tokens.push({
        base,
        price: tk.price,
        // The guard above covers PRICE only. `round2(tk.change)` turned an
        // absent change into 0 (`Math.round(null*100)/100`) — a measured flat
        // token — and an undefined one into NaN, which then poisoned the sort
        // below and every average built from it.
        change_24h_pct: tk.change == null ? null : round2(tk.change),
        // `a + null` is `a` in JS, so a null volume would slide through every
        // reduce below as a silent zero. Kept explicitly nullable and handled
        // at each aggregate instead.
        volume_24h_usd: tk.volume == null ? null : Math.round(tk.volume),
      });
    }
    // A row with no readable change cannot be ranked BY that change, and a
    // NaN comparator returns NaN, which leaves the whole category in an
    // implementation-defined order — one unreadable token scrambled the
    // ranking of every other one. Unrankable rows sort last, deterministically.
    tokens.sort((a, b) => {
      const av = a.change_24h_pct, bv = b.change_24h_pct;
      if (av == null && bv == null) return a.base.localeCompare(b.base);
      if (av == null) return 1;
      if (bv == null) return -1;
      return bv - av;
    });
    // Both aggregates are the shared reading now. They were WRITTEN here and
    // the sector rollup below had its own uncured copy; see `weightedChange`.
    const vol = sumVolume(tokens);
    const wChange = weightedChange(tokens);
    categories.push({
      key: cat.key,
      title: cat.title,
      blurb: cat.blurb,
      tokens,
      listed: tokens.length,
      tracked: cat.bases.length,
      volume_24h_usd: vol.usd,
      volume_scored: vol.scored,
      change_24h_pct: wChange.pct,
      change_scored: wChange.scored,
    });
    all.push(...tokens);
  }

  const totalVol = sumVolume(all);
  const sectorChange = weightedChange(all);
  const ranked = rankByChange(all);
  // `round2(null)` is 0 — `Math.round(null * 100) / 100` — so a BTC whose 24h
  // change the venue did not report was published as a MEASURED FLAT BTC, and
  // `vs_btc_pct` one line above already guarded `btc.change != null` correctly.
  // The inconsistency was visible in place, which is the same shape
  // `tickers.js` records about its own guarded `price` beside an unguarded
  // `change`.
  const btcChange = (btc && btc.change != null) ? round2(btc.change) : null;

  return {
    generated_at: new Date().toISOString(),
    source: 'Bitget USDT-M perpetual tickers (live, public)',
    read_only: true,
    // How many markets the venue read carried AT ALL. `listed: 0` over a
    // universe of 26 is a delisting story if the venue answered with its
    // hundreds of perps and a FAILED READ if it answered with none — a 200
    // that carried no rows, a productType rename, an upstream shape change —
    // and both reached the card as "none of the tracked tokens are listed
    // right now", a claim about the venue's listings. No threshold is
    // invented: the sample is stated and the renderer says which.
    markets_read: Object.keys(map).length,
    universe: UNIVERSE_SIZE,
    sector: {
      listed: all.length,
      volume_24h_usd: totalVol.usd,
      volume_scored: totalVol.scored,
      change_24h_pct: sectorChange.pct,
      change_scored: sectorChange.scored,
      // `btc.change` is nullable too, and `x - null` is `x` — a sector would
      // have been reported as beating a BTC that was never read.
      vs_btc_pct: (sectorChange.pct !== null && btcChange !== null)
        ? round2(sectorChange.pct - btcChange) : null,
      // Named extremes come off the RANKED (readable-only) list: the raw
      // subtraction put an unreadable row at whichever end the market was
      // leaning away from, so a token nobody could read was published as the
      // sector's best in a down market and its worst in an up one.
      top_gainer: ranked[0] || null,
      top_loser: ranked.length ? ranked[ranked.length - 1] : null,
    },
    btc_change_24h_pct: btcChange,
    categories,
  };
}

let fetchTickers = getTickers;
function setTickerFetcher(fn) { fetchTickers = fn || getTickers; }

async function getRadar() {
  return buildRadar(await fetchTickers());
}

// ── Chat intercept ───────────────────────────────────────────────────────────
//
// ONE renderer. `bot/skills/telegram_handler._format_rwa` was a second copy of
// this card, kept in step by hand — the shape CLAUDE.md records for maps and
// gates — and it had diverged in the direction that matters: its `_pct` did
// `float(v)`, and `.get('change_24h_pct', 0)` does not fire for a key PRESENT
// with `null`, which is exactly what this module publishes for an unreadable
// change. Driven, `/rwa` raised `TypeError` and the WHOLE card was gone for
// any listed token the venue did not report a 24h change for. The cure
// upstream was what crashed the reader downstream, so the reader is deleted:
// Telegram fetches this card over `/api/bot/sync/card/rwa` and renders it,
// the mechanism nine other website cards already use.

const CHAT_RE = /\b(rwa|real[- ]world assets?|tokeni[sz]ed (assets?|treasuries))\b/i;

/** A signed percent, or an em dash. NEVER `+null%`, never a manufactured 0. */
function pct(v) {
  return v == null ? '—' : `${v >= 0 ? '+' : ''}${v}%`;
}

/**
 * A volume, or an em dash.
 *
 * `const n = Number(v) || 0` printed `$0` for a total nobody could read —
 * or-zero on a figure a reader takes as "this sector traded nothing".
 */
function fmtVol(v) {
  const n = (v == null || !isFinite(Number(v))) ? null : Number(v);
  if (n == null) return '—';
  if (n >= 1e9) return '$' + (n / 1e9).toFixed(1) + 'B';
  if (n >= 1e6) return '$' + (n / 1e6).toFixed(1) + 'M';
  return '$' + Math.round(n).toLocaleString('en-US');
}

/**
 * `(2 of 3 reported one)` when the figure is PARTIAL — never over an absence.
 *
 * `scored === 0` means the figure itself is an em dash, and a sample caveat
 * beside a dash is a hedge about a number that is not there: the rule the
 * prompt's bounded lists already state, where the empty and unreadable
 * outcomes get no bound caveat at all.
 */
function cover(value, scored, total) {
  return (value != null && scored > 0 && total != null && scored < total)
    ? ` <i>(${scored} of ${total} reported one)</i>` : '';
}

/**
 * The RWA radar card — the reading BOTH surfaces render.
 *
 * Three top-level answers, because three different things happen:
 *   - the venue read carried no markets at all -> a FAILED read, said so;
 *   - it carried markets and none of ours -> a real listing fact;
 *   - it carried ours -> the radar.
 * The first two used to be one sentence ("None of the tracked tokens are
 * listed on the venue right now"), so a 200 that carried no rows was
 * published as the venue having delisted the entire sector.
 */
async function rwaChatCard() {
  const r = await getRadar();
  const s = r.sector;
  if (!r.markets_read) {
    return {
      reply_html: '🏦 <b>RWA radar</b><br><br>The venue ticker feed answered with '
        + 'no markets at all, so nothing could be read — this is not a report '
        + 'that the sector is unlisted. Try again in a moment.',
      intent: 'rwa',
    };
  }
  if (!s.listed) {
    return {
      reply_html: `🏦 <b>RWA radar</b><br><br>None of the ${r.universe} tracked `
        + `tokens is listed among the ${r.markets_read} markets read right now.`,
      intent: 'rwa',
    };
  }
  const catLines = r.categories.filter(c => c.listed).map((c) => {
    const top = c.tokens.slice(0, 3).map(t => `${t.base} ${pct(t.change_24h_pct)}`).join(' · ');
    return `• <b>${c.title}</b> (${c.listed} listed, ${pct(c.change_24h_pct)} wtd): ${top}`;
  });
  const extremes = [
    s.top_gainer ? `Top: ${s.top_gainer.base} ${pct(s.top_gainer.change_24h_pct)}` : '',
    s.top_loser ? `Laggard: ${s.top_loser.base} ${pct(s.top_loser.change_24h_pct)}` : '',
  ].filter(Boolean).join(' · ');
  return {
    reply_html:
      '🏦 <b>RWA radar</b> — live venue tickers, read-only<br><br>'
      + `Sector: <b>${pct(s.change_24h_pct)}</b> (24h, volume-weighted)`
      + cover(s.change_24h_pct, s.change_scored, s.listed)
      + (s.vs_btc_pct != null ? ` — ${pct(s.vs_btc_pct)} vs BTC` : '')
      + ` · ${s.listed} tokens · ${fmtVol(s.volume_24h_usd)} volume`
      + cover(s.volume_24h_usd, s.volume_scored, s.listed) + '<br>'
      + (extremes ? extremes + '<br><br>' : '<br>')
      + catLines.join('<br>')
      + '<br><br><i>Market intelligence only — the radar never trades. Full table on the Markets view.</i>',
    intent: 'rwa',
  };
}

/** The web chat intercept: a regex test in front of the one renderer. */
async function maybeHandleRwaChat(userId, text) {
  if (!CHAT_RE.test(String(text || ''))) return null;
  try {
    return await rwaChatCard();
  } catch (e) {
    return { reply_html: 'RWA radar is refreshing — try again in a moment.', intent: 'rwa' };
  }
}

module.exports = {
  RWA_UNIVERSE, buildRadar, getRadar, setTickerFetcher, maybeHandleRwaChat,
  rwaChatCard, CHAT_RE, weightedChange, sumVolume, rankByChange,
};
