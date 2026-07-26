'use strict';
/**
 * The Systemic Risk Sentinel's live read — ONE snapshot, shared by every caller.
 *
 * Why this is a module and not two call sites:
 *
 * The Sentinel's "leverage piling in" flag is a DELTA — open interest now
 * against the previous poll. That makes the previous poll load-bearing state,
 * not a cache. While the website was the only reader, keeping it in the route
 * was fine. The MCP tool reads the same Sentinel now, and a second copy of that
 * state would give the two surfaces genuinely different surge reads of the same
 * market: whichever polled less recently would compare against an older
 * baseline and report a bigger surge. Nobody would be lying, and the numbers
 * would still disagree — which is exactly the drift the one-source-of-truth
 * design exists to prevent.
 *
 * So the baseline lives here, both callers advance it, and the website and an
 * agent always describe the same market the same way.
 *
 * Everything served is public market fact (venue tickers, open interest,
 * funding). The output is heuristic flags with reasons, never a verdict.
 */

const { fetchJSON, cached } = require('./http_cache');
const { buildStrengthMap } = require('./strengthmap');
const { buildSentinel } = require('./sentinel');

const TICKERS_URL =
  'https://api.bitget.com/api/v2/mix/market/tickers?productType=USDT-FUTURES';

// The previous poll's open interest. Shared on purpose — see the note above.
let _oiBaseline = null;

// The universe size the Sentinel reads. Kept here rather than at each call site
// so the tool and the page cannot drift to different universes.
const UNIVERSE = 400;

/**
 * getSentinel() → the Sentinel payload, or throws.
 *
 * Advances the shared ΔOI baseline as a side effect: that IS the contract, and
 * it is why both surfaces call this rather than rebuilding it themselves.
 */
async function getSentinel() {
  const raw = await cached('tickers', 5000, () => fetchJSON(TICKERS_URL))();
  const tickers = (raw && Array.isArray(raw.data)) ? raw.data : [];
  if (!tickers.length) throw new Error('Market data unavailable');
  const { coins, oiSnapshot } = buildStrengthMap(tickers, _oiBaseline, UNIVERSE);
  _oiBaseline = oiSnapshot;
  return buildSentinel(coins, Date.now());
}

/** Test seam: forget the baseline so a test starts from a known state. */
function _resetBaseline() {
  _oiBaseline = null;
}

module.exports = { getSentinel, UNIVERSE, _resetBaseline };
