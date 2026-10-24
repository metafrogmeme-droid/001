'use strict';
/**
 * Canonical copy. app/lib/sar is a byte copy the site loads.
 * Do not edit one side.
 *
 * Chains this feed covers, and the markets it asks about.
 * Hyperliquid is a venue, not an EVM chain. Base is where the verifier
 * runs. Solana is where the Jupiter perps pool sits.
 */

const COVERS = Object.freeze(['hyperliquid', 'base', 'solana']);
const HL_COINS = Object.freeze(['BTC', 'ETH', 'SOL']);
const JUP_MINTS = Object.freeze({
  SOL: 'So11111111111111111111111111111111111111112',
  ETH: '7vfCXTUXx5WJV5JADk17DUJ4ksgau7utNKj4b963voxs',
  BTC: '3NZ9JMVBmGAqocybic2c7LQCJScmgsAZ6vQqTDzcqmJh',
});
const BASE_SEPOLIA = 84532;
const HL_INFO = 'https://api.hyperliquid.xyz/info';
const JUP_BASE = 'https://perps-api.jup.ag/v1';
/** Public l2Book returns at most this many levels a side. */
const PUBLIC_BOOK_CAP = 20;
/** An order counts toward depth only after it has rested this long. */
const PERSIST_MS = 30000;
/** Account concentration is resting size inside this band of the mid. */
const CONCENTRATION_BPS = 100;
const FLAG = Object.freeze({
  staleInput: 1,
  bookTruncated: 2,
  nodeLag: 4,
  hip3: 8,
  pegDeviation: 16,
  quantumCanary: 32,
});

module.exports = {
  COVERS, HL_COINS, JUP_MINTS, BASE_SEPOLIA, HL_INFO, JUP_BASE,
  PUBLIC_BOOK_CAP, PERSIST_MS, CONCENTRATION_BPS, FLAG,
};
