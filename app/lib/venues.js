/**
 * Connectable exchange venues — the web's single source of truth for which
 * venues a user can link and what fields each one needs. Mirrors the bot's
 * bot/core/exchange_credentials._VENUE_FIELDS (Bitget: api_key/api_secret/
 * passphrase; Hyperliquid: wallet_address/agent_private_key). The credential
 * route validates against this, and /config publishes it so the Account UI can
 * render the right form per venue without hardcoding field lists in the client.
 *
 * `fields[].type` drives the input type (password vs text). No secrets here.
 *
 * `market` is what the venue lists for this bot: 'perps', or 'spot' for Bybit
 * EU, which has no perpetual futures. The landing page's "perps across N
 * venues" counts the perps venues and no other.
 *
 * `orders` says whether an order from this bot can route there. It mirrors the
 * bot's `PER_USER_EXECUTION_VENUES` (bot/core/venues.py), and
 * app/test/a_balances_only_venue_says_so.test.js holds the two equal. False is
 * "linked for balances only": the keys are stored and the balance is read.
 */

const VENUES = [
  {
    id: 'bitget',
    label: 'Bitget',
    market: 'perps',
    orders: true,
    balance_coin: 'USDT',
    help: 'Create USDT-M futures API keys with read + trade permission. Keep withdrawals disabled.',
    fields: [
      { key: 'api_key', label: 'API key', type: 'text' },
      { key: 'api_secret', label: 'API secret', type: 'password' },
      { key: 'passphrase', label: 'Passphrase', type: 'password' },
    ],
  },
  {
    id: 'bybit',
    label: 'Bybit',
    market: 'perps',
    orders: true,
    balance_coin: 'USDT',
    help: 'USDT perpetuals. Create API keys with derivatives trade permission; account must be in ONE-WAY position mode.',
    fields: [
      { key: 'api_key', label: 'API key', type: 'text' },
      { key: 'api_secret', label: 'API secret', type: 'password' },
    ],
  },
  {
    id: 'bybiteu',
    label: 'Bybit EU',
    market: 'spot',
    orders: false,
    balance_coin: 'USD',
    help: 'Balances only. Bybit EU offers spot, not the perpetual futures this bot trades, so no order is placed there. Create a read-only API key on bybit.eu; keep withdrawals disabled.',
    fields: [
      { key: 'api_key', label: 'API key', type: 'text' },
      { key: 'api_secret', label: 'API secret', type: 'password' },
    ],
  },
  {
    id: 'bingx',
    label: 'BingX',
    market: 'perps',
    orders: true,
    balance_coin: 'USDT',
    help: 'USDT perpetuals ($2 min notional). Create API keys with perpetual-futures trade permission; account must be in ONE-WAY position mode.',
    fields: [
      { key: 'api_key', label: 'API key', type: 'text' },
      { key: 'api_secret', label: 'API secret', type: 'password' },
    ],
  },
  {
    id: 'okx',
    label: 'OKX',
    market: 'perps',
    orders: false,
    balance_coin: 'USDT',
    help: 'USDT perpetual swaps. Create API keys with trade permission and set an API passphrase; keep withdrawals disabled.',
    fields: [
      { key: 'api_key', label: 'API key', type: 'text' },
      { key: 'api_secret', label: 'API secret', type: 'password' },
      { key: 'passphrase', label: 'Passphrase', type: 'password' },
    ],
  },
  {
    id: 'gate',
    label: 'Gate.io',
    market: 'perps',
    orders: false,
    balance_coin: 'USDT',
    help: 'USDT perpetual swaps. Create API keys with futures trade permission; keep withdrawals disabled.',
    fields: [
      { key: 'api_key', label: 'API key', type: 'text' },
      { key: 'api_secret', label: 'API secret', type: 'password' },
    ],
  },
  {
    id: 'kucoin',
    label: 'KuCoin Futures',
    market: 'perps',
    orders: false,
    balance_coin: 'USDT',
    help: 'USDT perpetual futures. Create Futures API keys with trade permission and set an API passphrase; keep withdrawals disabled.',
    fields: [
      { key: 'api_key', label: 'API key', type: 'text' },
      { key: 'api_secret', label: 'API secret', type: 'password' },
      { key: 'passphrase', label: 'Passphrase', type: 'password' },
    ],
  },
  {
    id: 'hyperliquid',
    label: 'Hyperliquid (DEX)',
    market: 'perps',
    orders: true,
    balance_coin: 'USDC',
    help: 'On-chain perps DEX. Create an API (agent) wallet and use ITS private key — never your main wallet key.',
    fields: [
      { key: 'wallet_address', label: 'Wallet address', type: 'text' },
      { key: 'agent_private_key', label: 'Agent private key', type: 'password' },
    ],
  },
  {
    id: 'paradex',
    label: 'Paradex (DEX)',
    market: 'perps',
    orders: false,
    balance_coin: 'USDC',
    help: 'On-chain perps DEX (StarkEx L2). Create an API (agent) wallet and use ITS private key — never your main wallet key.',
    fields: [
      { key: 'wallet_address', label: 'Wallet address', type: 'text' },
      { key: 'agent_private_key', label: 'Agent private key', type: 'password' },
    ],
  },
];

const byId = Object.fromEntries(VENUES.map((v) => [v.id, v]));

function isVenue(id) {
  return Object.prototype.hasOwnProperty.call(byId, id);
}

// The ordered field keys a venue requires (for validation + payload assembly).
function venueFields(id) {
  return (byId[id]?.fields || []).map((f) => f.key);
}

// Whether an order from this bot can route to the venue: true, false, or null
// for an id this list does not know (unknown is not "balances only").
function venueTakesOrders(id) {
  return isVenue(id) ? byId[id].orders === true : null;
}

module.exports = { VENUES, byId, isVenue, venueFields, venueTakesOrders };
