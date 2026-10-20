'use strict';
/**
 * A second reading of a market's price at an instant, from CoinGecko through
 * Flovia, for the duel's settlement to CHECK ITSELF against. The venue still
 * settles: one reading per quantity, and the settle price is the venue's
 * hourly close (lib/candles.js). This reading only says whether a second
 * source agreed with it, and a settlement whose two readings disagree past a
 * tolerance is marked DISPUTED on its receipt and its surfaces, never
 * re-decided.
 *
 * THREE ANSWERS
 *
 *   { read: true, provider: 'CoinGecko', price, at, source_at }   a point near the instant
 *   { read: false, reason }                                        nothing to compare with
 *
 * `reason` is the client's (unconfigured, refused, unreachable, malformed),
 * or 'not covered' (no CoinGecko coin for the ticker), or 'no point' (the
 * day's series has no point within the window of the instant). A
 * settlement with no second reading is not disputed and not confirmed: its
 * receipt says `disputed: null`, "not checked", which is the third value.
 *
 * The series is remembered for an hour per coin: CoinGecko's one-day chart
 * carries a point every five minutes, so one read serves every round that
 * settles in the hour, and a page load that settles five calls opens one
 * lookup, not five.
 */

const flovia = require('./flovia');
const { remember } = require('./flovia_reads');
const { resolveToken, tickerOf, COINGECKO_CREDIT } = require('./smart_money');

const SERIES_TTL_MS = 60 * 60 * 1000;
/** A point must lie within this of the instant, or there is no second reading. */
const WINDOW_MS = 90 * 60 * 1000;

async function dailySeries(id) {
  return remember(`cg:chart1d:${id}`, SERIES_TTL_MS, async () => {
    const r = await flovia.call('flovia_coingecko', {
      path: `/coins/${encodeURIComponent(id)}/market_chart`,
      query: { vs_currency: 'usd', days: 1 },
      request_text: `RUNECLAW duel settlement check: a second price reading for ${id}`,
      purpose: 'A second source beside the venue close at settlement; it flags a disagreement and decides nothing.',
    });
    if (!r.read) return r;
    const prices = r.data && Array.isArray(r.data.prices) ? r.data.prices : null;
    if (!prices) return { read: false, reason: 'malformed', detail: 'no prices series' };
    const points = prices.map((p) => [Number(p && p[0]), Number(p && p[1])])
      .filter((p) => Number.isFinite(p[0]) && Number.isFinite(p[1]) && p[1] > 0);
    return { read: true, points };
  });
}

/**
 * CoinGecko's price for `symbol` nearest `atMs` (the last point at or
 * before it, within WINDOW_MS), or why there is none.
 */
async function secondPriceAt(symbol, atMs) {
  const ticker = tickerOf(symbol);
  const at = Number(atMs);
  if (!ticker || !Number.isFinite(at) || at <= 0) return { read: false, reason: 'malformed' };
  if (!flovia.configured()) return { read: false, reason: 'unconfigured' };
  const token = await resolveToken(ticker);
  if (!token.read) return { read: false, reason: token.reason, detail: token.detail };
  if (!token.id) return { read: false, reason: 'not covered', detail: token.reason };
  const series = await dailySeries(token.id);
  if (!series.read) return { read: false, reason: series.reason, detail: series.detail };
  let best = null;
  for (const [ts, price] of series.points) {
    if (ts <= at && (best === null || ts > best[0])) best = [ts, price];
  }
  if (!best || at - best[0] > WINDOW_MS) return { read: false, reason: 'no point', detail: 'no CoinGecko point within 90 minutes before the horizon' };
  return { read: true, provider: 'CoinGecko', price: best[1], at: new Date(at).toISOString(), source_at: new Date(best[0]).toISOString(), credit: COINGECKO_CREDIT };
}

/**
 * The dispute verdict over the venue's close and a second reading:
 *   true   both read, and they disagree past `tolerancePct`
 *   false  both read, within it
 *   null   no second reading: not checked
 */
function disputed(venueClose, second, tolerancePct) {
  const v = Number(venueClose);
  if (!second || second.read !== true || !Number.isFinite(v) || v <= 0) return null;
  const p = Number(second.price);
  if (!Number.isFinite(p) || p <= 0) return null;
  const tol = Number(tolerancePct);
  return Math.abs(v - p) / v * 100 > (Number.isFinite(tol) ? tol : 0.5);
}

/** The tolerance in percent, from the environment, with a default. */
function tolerancePct() {
  const t = Number(process.env.DUEL_SETTLE_TOLERANCE_PCT);
  return Number.isFinite(t) && t > 0 ? t : 0.5;
}

module.exports = { secondPriceAt, disputed, tolerancePct, WINDOW_MS, SERIES_TTL_MS };
