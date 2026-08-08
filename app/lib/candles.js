'use strict';
/**
 * Time-addressed candle reads (Bitget USDT-M).
 *
 * `closeAt(symbol, ms)` answers the close of the 1h candle that CONTAINS that
 * moment — so the answer is a function of the timestamp, not of when we asked.
 * That is what lets the Duel settle lazily: a round settled six hours late
 * yields the same price as one settled on the minute, and anyone can re-derive
 * it from the public API. A spot mark could not do that; it would silently
 * record "the price when someone happened to load the page".
 *
 * Every failure path answers null. There is no fallback price, because a
 * fallback price is a guess wearing the authority of a measurement — and the
 * caller's whole contract is that an unreadable settle means UNRESOLVED.
 */

const HOUR_MS = 3600000;
const REQUEST_TIMEOUT_MS = 10000;
const BASE_URL = process.env.DUEL_CANDLES_URL
  || 'https://api.bitget.com/api/v2/mix/market/candles';

// Injectable fetch (tests / alternate transports). Null restores the default.
// Mirrors setTickerFetcher() in lib/tickers.js so the two seams behave alike.
let fetchImpl = null;
function setCandleFetcher(fn) { fetchImpl = fn || null; }

/** Start of the 1h bucket containing `ms`. */
function hourStart(ms) {
  return Math.floor(ms / HOUR_MS) * HOUR_MS;
}

/**
 * Raw rows for a window. Bitget returns arrays:
 *   [ ts, open, high, low, close, baseVol, quoteVol ]
 * Answers null (not []) when the read failed, so "no data" and "could not ask"
 * stay distinguishable to the caller.
 */
async function fetchWindow(symbol, startMs, endMs) {
  if (fetchImpl) return fetchImpl(symbol, startMs, endMs);
  const url = `${BASE_URL}?symbol=${encodeURIComponent(symbol)}`
    + `&productType=USDT-FUTURES&granularity=1H&limit=10`
    + `&startTime=${startMs}&endTime=${endMs}`;
  const res = await fetch(url, { signal: AbortSignal.timeout(REQUEST_TIMEOUT_MS) });
  if (!res.ok) throw new Error(`candles HTTP ${res.status}`);
  const body = await res.json();
  // Bitget can return a business error inside an HTTP 200 (the "1h vs 1H"
  // trap documented in routes/market.js). A non-array payload is a failure,
  // not an empty market.
  if (!body || !Array.isArray(body.data)) throw new Error('candles payload');
  return body.data;
}

/**
 * The close of the 1h candle containing `atMs`, or null if it cannot be read.
 *
 * Null covers every distinct failure — network, business error, missing bucket,
 * unparseable close — deliberately, because the caller does the same thing with
 * all of them: leaves the round unresolved and tries again later.
 */
async function closeAt(symbol, atMs) {
  const sym = String(symbol == null ? '' : symbol).trim().toUpperCase();
  // `Number(null)` is 0 — a perfectly finite number, and a real moment in
  // 1970. Absence has to be rejected before the coercion, or a missing
  // horizon silently becomes a timestamp we would then go and query.
  if (atMs == null || atMs === '') return null;
  const at = Number(atMs);
  if (!sym || !Number.isFinite(at) || at <= 0) return null;
  const bucket = hourStart(at);
  let rows;
  try {
    // Ask for a small window around the bucket: Bitget's window bounds are
    // inclusive/exclusive in ways that have moved before, so we locate the
    // bucket ourselves rather than trusting it to return exactly one row.
    rows = await fetchWindow(sym, bucket, bucket + 2 * HOUR_MS);
  } catch (e) {
    return null;
  }
  if (!Array.isArray(rows)) return null;
  for (const row of rows) {
    if (!Array.isArray(row) || row.length < 5) continue;
    const ts = Number(row[0]);
    if (!Number.isFinite(ts) || hourStart(ts) !== bucket) continue;
    const close = Number(row[4]);
    // A close of zero is not a price; it is a failed read wearing a number.
    if (!Number.isFinite(close) || close <= 0) return null;
    return close;
  }
  return null;
}

module.exports = { closeAt, hourStart, setCandleFetcher, HOUR_MS };
