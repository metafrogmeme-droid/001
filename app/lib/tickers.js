/**
 * Shared live-ticker source (Bitget USDT-M public tickers).
 *
 * One fetch, one 30s cache, used by the alert engine and the RWA radar.
 * Map shape: { BTCUSDT: { price, change, volume } } — change is a percent
 * (Bitget's decimal fraction ×100), volume is 24h quote volume in USDT.
 * ALERTS_TICKERS_URL overrides the source (tests / alternate routing).
 *
 * READ WITH https.get (lib/http_cache.js's fetchJSON), NOT THE GLOBAL fetch.
 * On 2026-10-17 the live Arena showed every position as "mark unavailable"
 * and refused every close. Every request that read this map took its full
 * 6s budget and got nothing, while /api/market/ticker, which reads the same
 * Bitget endpoint with fetchJSON, answered in about a second from the same
 * host. The host is Function Compute, which may freeze an instance between
 * requests; a fetch started in one request and left running cannot be
 * counted on to finish. So this reader uses the transport that demonstrably
 * answers there, abandons an in-flight fetch that has stalled (STALLED_MS)
 * instead of joining it forever, and logs why a read failed: the old reader
 * swallowed every failure, and nothing said why the Arena had no prices.
 */

const { fetchJSON } = require('./http_cache');

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
let inflightAt = 0;
// A fetch still pending this long is not coming back: fetchJSON gives up on
// a silent socket at 8s. Past this, the next caller starts its own instead of
// joining one that a frozen instance left behind.
const STALLED_MS = 12_000;

// Injectable fetch (tests / alternate transports). Null restores the default.
let fetchImpl = null;
function setTickerFetcher(fn) { fetchImpl = fn || null; cache = { at: 0, map: null }; inflightFetch = null; }

// When a failure was last logged (noteFailure); a dead feed is read on every request.
let lastLoggedAt = 0;

// Injectable TRANSPORT: what the real path calls for the response body, so
// tests drive the production getTickers (cache, in-flight, parse, logging)
// rather than replacing it. Null restores fetchJSON(TICKERS_URL).
let transport = null;
function setTickerTransport(fn) {
  transport = fn || null;
  cache = { at: 0, map: null };
  inflightFetch = null;
  inflightAt = 0;
  lastLoggedAt = 0;
}

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
  // Join the fetch already running, unless it has stalled.
  if (inflightFetch && now - inflightAt < STALLED_MS) return inflightFetch;
  const mine = doFetch(now).finally(() => {
    // A stalled fetch that settles late must not clear the one that replaced it.
    if (inflightFetch === mine) inflightFetch = null;
  });
  inflightFetch = mine;
  inflightAt = now;
  return mine;
}

/** Why a read failed, with its stack, at most once a minute (a dead feed is read on every request). */
function noteFailure(err) {
  const t = Date.now();
  if (t - lastLoggedAt < 60_000) return;
  lastLoggedAt = t;
  console.error('Arena tickers unreadable:', err.stack || err.message);
}

async function doFetch(now) {
  try {
    const map = parseTickers(await (transport ? transport() : fetchJSON(TICKERS_URL)));
    cache = { at: now, map };
    return map;
  } catch (err) {
    noteFailure(err);
    throw err;
  }
}

/**
 * Bitget's answer → { SYMBOL: { price, change, volume } }. Throws when the
 * answer holds no tickers: an error body ({ code, msg, data: null }) or an
 * empty list is a feed that could not be read, and caching it as a fresh,
 * empty map would price nothing for the next 30 seconds while looking healthy.
 */
function parseTickers(data) {
  const rows = data && Array.isArray(data.data) ? data.data : null;
  if (!rows) {
    throw new Error(`tickers answered without a list${data && data.code ? ` (code ${data.code})` : ''}`);
  }
  const map = {};
  for (const t of rows) {
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
    // say what it means.
    //
    // `volume` was left with its `|| 0` when `change` was fixed, on the
    // reasoning that an unreadable volume drops a token OUT of volume
    // weighting rather than into it. That was true of the weighting and false
    // everywhere else, and the sharpest counter-example is `token_safety.js`,
    // which holds exactly the right guard:
    //
    //     const volume = ticker ? num(ticker.volume) : null;
    //     if (volume != null && volume < THIN_VOLUME_USD) flags.push('thin-cex-volume')
    //
    // `num()` maps a non-finite value to null, so that guard is correct and
    // the `|| 0` upstream defeated it: a token the venue reported no volume
    // for was flagged "thin books slip harder and are easier to push around"
    // — a SAFETY claim manufactured from absence, on the surface whose whole
    // job is telling someone whether a token is safe to touch.
    const chg = parseFloat(t.change24h);
    const vol = parseFloat(t.usdtVolume ?? t.quoteVolume);
    map[t.symbol] = {
      price,
      change: Number.isFinite(chg) ? chg * 100 : null,
      volume: Number.isFinite(vol) ? vol : null,
    };
  }
  if (!Object.keys(map).length) throw new Error('tickers answered no priced symbol');
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
 * exists. The in-flight fetch is left running and may populate the cache for
 * the next caller; on a host that freezes between requests it may not, which
 * is why getTickers stops joining it after STALLED_MS.
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

module.exports = {
  getTickers, getTickersWithin, lastKnownTickers, setTickerFetcher, setTickerTransport, parseTickers,
  TTL_MS, FILL_MAX_AGE_MS, STALLED_MS,
};
