'use strict';
/**
 * The $RCLAW token's DEX pool, as CoinGecko's on-chain data reports it,
 * through Flovia (lib/flovia.js), for the /token page's pool panel.
 *
 * THREE ANSWERS, AND "NOT LISTED" IS ONE OF THEM
 *
 *   { listed: true,  pool: { address, name, dex, reserve_usd, volume_24h_usd, price_usd, url },
 *     note, credit, read_at }                       a pool holds the mint
 *   { listed: false, reason: 'no_pool', read_at }   CoinGecko answered and listed none
 *   { listed: null,  reason, detail? }              could not be read: unconfigured, refused,
 *                                                    unreachable, malformed, record_unreadable
 *
 * Before the token trades anywhere the honest answer is the second, and it
 * is distinct from the third: "no pool yet" is a fact CoinGecko reported,
 * "could not be read" is a fact about this read. The page paints them with
 * different sentences, and never a zero liquidity.
 *
 * The largest pool ON THE FIRST PAGE, by the pool's own liquidity: CoinGecko's
 * note is that a rank across every pool is not claimed from one page, so the
 * panel says "largest on the first page" and no more. Market facts only:
 * liquidity, a day's volume and a price are public market data (CLAUDE.md,
 * public-surface rules), and no account figure is anywhere near this.
 */

const flovia = require('./flovia');
const { remember } = require('./flovia_reads');
const rclaw = require('./rclaw_token');
const { COINGECKO_CREDIT } = require('./smart_money');

const POOL_TTL_MS = 10 * 60 * 1000;
const NETWORK = 'solana';
const GECKO_POOL_URL = 'https://www.geckoterminal.com/solana/pools/';

function num(x) {
  // `Number(null)` is 0: an absent figure is null, never a zero price.
  if (x === null || x === undefined || x === '') return null;
  const n = Number(x);
  return Number.isFinite(n) ? n : null;
}

/** The pool rows CoinGecko answered, each as this panel reads it. */
function poolsOf(data) {
  const rows = data && Array.isArray(data.data) ? data.data : [];
  return rows.filter((r) => r && typeof r === 'object').map((r) => {
    const a = (r && r.attributes) || {};
    const rel = (r && r.relationships) || {};
    const dex = rel.dex && rel.dex.data && rel.dex.data.id ? String(rel.dex.data.id) : null;
    const address = typeof a.address === 'string' ? a.address : (typeof r.id === 'string' ? r.id.replace(/^solana_/, '') : null);
    const vol = a.volume_usd && typeof a.volume_usd === 'object' ? num(a.volume_usd.h24) : null;
    return {
      address, name: typeof a.name === 'string' ? a.name : null, dex,
      reserve_usd: num(a.reserve_in_usd), volume_24h_usd: vol,
      price_usd: num(a.base_token_price_usd),
      url: address ? GECKO_POOL_URL + address : null,
    };
  }).filter((p) => p.address);
}

async function readPools(mint) {
  const r = await flovia.call('flovia_coingecko', {
    path: `/onchain/networks/${NETWORK}/tokens/${encodeURIComponent(mint)}/pools`,
    query: { page: 1 },
    request_text: 'RUNECLAW token page: the $RCLAW DEX pool\'s liquidity, volume and price',
    purpose: 'Showing the token\'s DEX pool on the public token page; market facts only.',
  });
  if (!r.read) return { listed: null, reason: r.reason, detail: r.detail };
  const pools = poolsOf(r.data);
  if (!pools.length) return { listed: false, reason: 'no_pool' };
  pools.sort((a, b) => (b.reserve_usd == null ? -1 : b.reserve_usd) - (a.reserve_usd == null ? -1 : a.reserve_usd));
  return {
    listed: true, pool: pools[0], pools_on_page: pools.length,
    note: 'largest on the first page CoinGecko answered', credit: COINGECKO_CREDIT,
  };
}

/** The panel's answer, remembered for ten minutes (a refusal for ten too). */
async function tokenPool() {
  let record;
  try { record = rclaw.readRecord(); } catch (e) {
    return { listed: null, reason: 'record_unreadable', detail: (e && e.name) || 'Error' };
  }
  if (!flovia.configured()) return { listed: null, reason: 'unconfigured' };
  const out = await remember(`cg:pool:${record.mint}`, POOL_TTL_MS, async () => {
    const r = await readPools(record.mint);
    // The cache keys on `read`; a listed-or-not answer is a read.
    return { read: r.listed !== null, ...r };
  });
  const { read, remembered, ...rest } = out;
  return rest;
}

module.exports = { tokenPool, poolsOf, POOL_TTL_MS, NETWORK };
