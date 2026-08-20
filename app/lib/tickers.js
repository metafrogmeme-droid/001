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
// The oldest a mark may be and still be allowed to price a FILL or settle an
// exit. Deliberately equal to TTL_MS: display may degrade, money may not.
const FILL_MAX_AGE_MS = TTL_MS;

let cache = { at: 0, map: null };
// One upstream fetch at a time. Without this, N concurrent cold requests each
// open their own 10s fetch — multiplying the very latency that made the Arena
// account exceed its client budget in the first place.
let inflightFetch = null;

// Injectable fetch (tests / alternate transports). Null restores the default.
let fetchImpl = null;
function setTickerFetcher(fn) { fetchImpl = fn || null; cache = { at: 0, map: null }; inflightFetch = null; }

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
  if (inflightFetch) return inflightFetch;          // join the fetch already running
  inflightFetch = doFetch(now).finally(() => { inflightFetch = null; });
  return inflightFetch;
}

async function doFetch(now) {
  const res = await fetch(TICKERS_URL, { signal: AbortSignal.timeout(10_000) });
  if (!res.ok) throw new Error(`tickers HTTP ${res.status}`);
  const data = await res.json();
  const map = {};
  for (const t of (data && data.data) || []) {
    const price = parseFloat(t.lastPr);
    if (!t.symbol || !isFinite(price)) continue;
    // `change: (parseFloat(t.change24h) || 0) * 100` was CLAUDE.md's banned
    // shape at the root of a map twelve modules read — and note that `price`
    // two lines up IS guarded, so the inconsistency was visible in place.
    //
    // A venue that reported no 24h change produced a measured 0.0%, and
    // "flat" is a real reading, so nothing downstream could tell them apart:
    //
    //   - the RWA radar counted it as a flat token in a VOLUME-WEIGHTED
    //     sector average, pulling the sector toward neutral
    //   - `/api/market/rwa` coloured the cell green, because 0 >= 0
    //   - a price ALERT is the sharp one. `evaluateAlert` guards with
    //     `isFinite(v)`, and `isFinite(null)` is TRUE in JS — but with the
    //     `|| 0` the value was already 0, so "notify me when 24h change < 5%"
    //     FIRED for every token whose change could not be read, reporting a
    //     0% move as a threshold crossing.
    //
    // null is not a number and cannot be compared, so each consumer has to
    // say what it means. `volume` keeps its `|| 0` for now: an unreadable
    // volume drops the token out of volume weighting rather than into it,
    // which is the safe direction — but it is the same shape and is noted.
    const chg = parseFloat(t.change24h);
    map[t.symbol] = {
      price,
      change: Number.isFinite(chg) ? chg * 100 : null,
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
 * marks for DISPLAY, and the caller renders a null mark honestly when neither
 * exists. The in-flight fetch is left running; it will populate the cache for
 * the next caller rather than being wasted.
 *
 * Returns { map, ageMs }. ageMs is 0 for a live fetch, the cache age for a
 * stale hit, and Infinity when there is nothing. Callers that WRITE — that
 * settle an exit or seal a fill — must check it. FILL_MAX_AGE_MS is the
 * bound they should use.
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
    if (won) return { map: won, ageMs: 0 };
    const stale = lastKnownTickers();
    // ageMs is the caller's ONLY way to tell a live mark from a stale one.
    // Returning them indistinguishably is how a stale price ends up priced
    // into a sealed fill.
    return stale ? { map: stale, ageMs: Date.now() - cache.at } : { map: {}, ageMs: Infinity };
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

module.exports = { getTickers, getTickersWithin, lastKnownTickers, setTickerFetcher, TTL_MS, FILL_MAX_AGE_MS };
