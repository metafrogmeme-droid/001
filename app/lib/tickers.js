/**
 * Shared live-ticker source (Bitget USDT-M public tickers).
 *
 * One fetch, one 30s cache, used by the alert engine and the RWA radar.
 * Map shape: { BTCUSDT: { price, change, volume } } — change is a percent
 * (Bitget's decimal fraction ×100), volume is 24h quote volume in USDT.
 * ALERTS_TICKERS_URL overrides the source (tests / alternate routing).
 */

const TICKERS_URL = process.env.ALERTS_TICKERS_URL
  || 'https://api.bitget.com/api/v2/mix/market/tickers?productType=USDT-FUTURES';
const TTL_MS = 30_000;

let cache = { at: 0, map: null };

// Injectable fetch (tests / alternate transports). Null restores the default.
let fetchImpl = null;
function setTickerFetcher(fn) { fetchImpl = fn || null; cache = { at: 0, map: null }; }

async function getTickers() {
  // The injectable fetcher deliberately skips the TTL — tests change prices
  // between calls and must see them — but it still records the result, so the
  // stale-fallback path below behaves identically under test and in production.
  if (fetchImpl) {
    const m = await fetchImpl();
    if (m) cache = { at: Date.now(), map: m };
    return m;
  }
  const now = Date.now();
  if (cache.map && now - cache.at < TTL_MS) return cache.map;
  const res = await fetch(TICKERS_URL, { signal: AbortSignal.timeout(10_000) });
  if (!res.ok) throw new Error(`tickers HTTP ${res.status}`);
  const data = await res.json();
  const map = {};
  for (const t of (data && data.data) || []) {
    const price = parseFloat(t.lastPr);
    if (!t.symbol || !isFinite(price)) continue;
    map[t.symbol] = {
      price,
      change: (parseFloat(t.change24h) || 0) * 100,
      volume: parseFloat(t.usdtVolume ?? t.quoteVolume) || 0,
    };
  }
  cache = { at: now, map };
  return map;
}

/**
 * Marks for a request that has its OWN deadline — a user is waiting.
 *
 * getTickers() may spend up to 10s on a cold cache. That is fine for a
 * background sweep and far too slow for a page load: the Arena client gives
 * the whole /account request 14s, so one slow upstream fetch eats the entire
 * budget and the browser gives up. From the user's side an abort is
 * indistinguishable from a 500 — the panel just says it could not load, while
 * the server was about to answer perfectly well.
 *
 * So: wait `budgetMs`, no longer. On timeout fall back to the last map we
 * successfully fetched even if it is past its TTL — stale marks beat no
 * marks, and the caller renders a null mark honestly when neither exists.
 * The in-flight fetch is left running; it will populate the cache for the
 * next caller rather than being wasted.
 */
async function getTickersWithin(budgetMs) {
  const inflight = getTickers();
  // A rejected fetch must not surface as an unhandled rejection when the
  // race has already been decided by the timer.
  inflight.catch(() => {});
  let timer;
  const deadline = new Promise((resolve) => {
    timer = setTimeout(() => resolve(null), Math.max(0, Number(budgetMs) || 0));
  });
  try {
    const won = await Promise.race([inflight.catch(() => null), deadline]);
    return won || lastKnownTickers() || {};
  } finally {
    clearTimeout(timer);
  }
}

/**
 * The last successfully fetched map, if it is still defensible to show.
 *
 * A mark a few seconds past its TTL is the normal compromise every trading UI
 * makes. A mark from an hour ago presented as the current price is not — it
 * would put a fabricated number next to a real position and compute PnL from
 * it. Past STALE_OK_MS we return nothing and the caller renders an honest
 * blank instead. Never fetches.
 */
const STALE_OK_MS = 120_000;
function lastKnownTickers() {
  if (!cache.map) return null;
  return (Date.now() - cache.at) <= STALE_OK_MS ? cache.map : null;
}

module.exports = { getTickers, getTickersWithin, lastKnownTickers, setTickerFetcher };
