'use strict';
/**
 * Smart-money context for a symbol: Nansen's net smart-money flow over the
 * last 24 hours, read through Flovia, remembered for a day, and printed on
 * the cards as a LINE beside the score. Never a verdict: nothing in the
 * engine, the risk gate or the duel's settlement reads it (a test scans for
 * that), and a reader that says "not read" is the normal case on a day the
 * free window is spent, the symbol is not on a chain Nansen tracks, or the
 * row is simply missing ("Missing rows are not zero", Nansen's own note).
 *
 * TWO LOOKUPS, BOTH REMEMBERED
 *
 * Nansen keys a token by chain and contract address and the bot keys it by
 * ticker, so the ticker is resolved first through CoinGecko (search, then
 * the coin's platforms), remembered for a week; then the flow is read,
 * remembered for a day. A native coin (ETH, SOL, BNB) is asked for with
 * Nansen's include_native_tokens flag on its own chain; a coin with no
 * contract on a chain Nansen tracks (BTC) is "not covered", a reason of its
 * own, never a zero flow.
 *
 * `peek` is what every page reads: the remembered line or nothing, and never
 * a lookup. Lookups run from the one place allowed to spend: the bot's card
 * render (one symbol at a time, through the bot-secret route) and the day's
 * deal of the duel card.
 */

const flovia = require('./flovia');
const { remember, peek } = require('./flovia_reads');

const NANSEN_CREDIT = 'Data provided by Nansen';
const COINGECKO_CREDIT = 'Data provided by CoinGecko';
const TOKEN_TTL_MS = 7 * 24 * 60 * 60 * 1000;
const FLOW_TTL_MS = 24 * 60 * 60 * 1000;
const WINDOW = '24h';

/** CoinGecko platform ids → Nansen chain names, for the chains both know. */
const PLATFORM_TO_NANSEN = Object.freeze({
  ethereum: 'ethereum',
  solana: 'solana',
  base: 'base',
  'arbitrum-one': 'arbitrum',
  'binance-smart-chain': 'bnb',
  'polygon-pos': 'polygon',
  avalanche: 'avalanche',
  'optimistic-ethereum': 'optimism',
});
/** CoinGecko ids of native coins and the Nansen chain they are native to. */
const NATIVE = Object.freeze({ ethereum: 'ethereum', solana: 'solana', binancecoin: 'bnb' });
/** The order Nansen's chains are preferred in when a token lives on several. */
const CHAIN_ORDER = ['ethereum', 'solana', 'base', 'arbitrum', 'bnb', 'polygon', 'avalanche', 'optimism'];

function tickerOf(symbol) {
  // 'BTC/USDT:USDT' → 'BTC'; 'btc' → 'BTC'.
  return String(symbol || '').trim().toUpperCase().split(/[\/:]/)[0].replace(/USDT$/, '') || '';
}

function requestText(what, ticker) {
  return `RUNECLAW card context: ${what} for ${ticker}`;
}

/**
 * The CoinGecko coin behind a ticker, and where Nansen can look for it:
 *   { read: true, id, chain, token_address|null, native }   covered
 *   { read: true, id, covered: false, reason }               known coin, no tracked chain
 *   { read: false, reason }                                  could not be resolved
 */
async function resolveToken(symbol) {
  const ticker = tickerOf(symbol);
  if (!ticker) return { read: false, reason: 'malformed' };
  return remember(`cg:token:${ticker}`, TOKEN_TTL_MS, async () => {
    const found = await flovia.call('flovia_coingecko', {
      path: '/search', query: { query: ticker },
      request_text: requestText('which coin a ticker is', ticker),
      purpose: 'Resolving a card symbol to the coin and contract Nansen keys by, for the smart-money line.',
    });
    if (!found.read) return found;
    const coins = found.data && Array.isArray(found.data.coins) ? found.data.coins : [];
    const same = coins.filter((c) => c && String(c.symbol || '').toUpperCase() === ticker);
    if (!same.length) return { read: true, id: null, covered: false, reason: 'no such coin' };
    // The best-known coin with this ticker: the lowest market-cap rank, with
    // an unranked coin after every ranked one.
    same.sort((a, b) => (a.market_cap_rank == null ? 1e9 : a.market_cap_rank) - (b.market_cap_rank == null ? 1e9 : b.market_cap_rank));
    const id = String(same[0].id);
    if (NATIVE[id]) return { read: true, id, chain: NATIVE[id], token_address: null, native: true };
    const coin = await flovia.call('flovia_coingecko', {
      path: `/coins/${encodeURIComponent(id)}`,
      query: { localization: false, tickers: false, market_data: false, community_data: false, developer_data: false },
      request_text: requestText('the contract addresses of a coin', ticker),
      purpose: 'Resolving a card symbol to the coin and contract Nansen keys by, for the smart-money line.',
    });
    if (!coin.read) return coin;
    const platforms = coin.data && coin.data.platforms && typeof coin.data.platforms === 'object' ? coin.data.platforms : {};
    const options = [];
    for (const [platform, address] of Object.entries(platforms)) {
      const chain = PLATFORM_TO_NANSEN[platform];
      if (chain && typeof address === 'string' && address.trim()) options.push({ chain, token_address: address.trim() });
    }
    if (!options.length) return { read: true, id, covered: false, reason: 'no contract on a chain Nansen tracks' };
    options.sort((a, b) => CHAIN_ORDER.indexOf(a.chain) - CHAIN_ORDER.indexOf(b.chain));
    return { read: true, id, chain: options[0].chain, token_address: options[0].token_address, native: false };
  });
}

/** Nansen's row for this token, if the answer carries one. */
function rowFor(data, token) {
  const rows = data && Array.isArray(data.data) ? data.data : (Array.isArray(data) ? data : []);
  const want = token.native ? null : String(token.token_address).toLowerCase();
  return rows.find((r) => r && String(r.chain || '').toLowerCase() === token.chain
    && (token.native ? (r.is_native === true || /^(eth|sol|bnb)$/i.test(String(r.token_symbol || r.symbol || '')))
      : String(r.token_address || '').toLowerCase() === want)) || null;
}

/**
 * The smart-money line for a symbol, read (and remembered for a day) when
 * it is not already remembered:
 *   { read: true, provider: 'Nansen', window: '24h', net_flow_usd, chain, token_address, read_at, credit }
 *   { read: false, reason: 'unconfigured' | 'not covered' | 'absent' | 'refused' | 'unreachable' | 'malformed', detail? }
 */
async function smartMoneyReading(symbol) {
  const ticker = tickerOf(symbol);
  if (!ticker) return { read: false, reason: 'malformed' };
  if (!flovia.configured()) return { read: false, reason: 'unconfigured' };
  return remember(`nansen:netflow:${ticker}`, FLOW_TTL_MS, async () => {
    const token = await resolveToken(ticker);
    if (!token.read) return { read: false, reason: token.reason, detail: token.detail };
    if (token.covered === false) return { read: false, reason: 'not covered', detail: token.reason };
    const body = { chains: [token.chain], pagination: { page: 1, per_page: 10 } };
    body.filters = token.native ? { include_native_tokens: true } : { token_address: token.token_address };
    const r = await flovia.call('flovia_nansen', {
      endpoint: '/api/v1/smart-money/netflow', body,
      request_text: requestText('smart money net flow over 24 hours', ticker),
      purpose: 'A context line on a signal or duel card; it changes no decision.',
    });
    if (!r.read) return r;
    const row = rowFor(r.data, token);
    const v = row ? Number(row.net_flow_24h_usd) : NaN;
    if (!row || !Number.isFinite(v)) return { read: false, reason: 'absent', detail: 'no row for this token in the answer' };
    return {
      read: true, provider: 'Nansen', window: WINDOW, net_flow_usd: v, chain: token.chain,
      token_address: token.token_address, credit: NANSEN_CREDIT,
    };
  });
}

/** The remembered line, or null. Never a lookup. */
async function peekSmartMoney(symbol) {
  const ticker = tickerOf(symbol);
  if (!ticker) return null;
  const hit = await peek(`nansen:netflow:${ticker}`);
  return hit && hit.value && hit.value.read ? { ...hit.value, read_at: hit.value.read_at || hit.read_at } : null;
}

module.exports = {
  resolveToken, smartMoneyReading, peekSmartMoney, tickerOf,
  NANSEN_CREDIT, COINGECKO_CREDIT, PLATFORM_TO_NANSEN, NATIVE, WINDOW,
};
