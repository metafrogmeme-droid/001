'use strict';
/**
 * Memeclaw's bonding curve, as the page and the server quote it.
 *
 * THE CONTRACT IS THE CURVE. contracts/memeclaw/MemeclawFactory.sol prices
 * every trade; this module only says, before a wallet is asked, what the
 * contract will pay. The constants below are the contract's own, and
 * app/test/memeclaw_curve.test.js reads them back out of the committed
 * artifact's bytecode by running it, so a change on either side fails there.
 *
 * All amounts are BigInt in base units (18 decimals for both the native coin
 * and every Memeclaw coin). Rounding is the contract's: down, on both sides.
 */
const E18 = 10n ** 18n;
const SUPPLY = 1_000_000_000n * E18;
const VIRTUAL_DOGE = 10n * E18;
const VIRTUAL_TOKENS = 250_000_000n * E18;

function big(v, what) {
  if (typeof v === 'bigint') return v;
  if (typeof v === 'string' && /^\d+$/.test(v)) return BigInt(v);
  throw new TypeError(`${what} must be a non-negative integer in base units`);
}

function reserves(curve) {
  const dogeReserve = big(curve.dogeReserve, 'dogeReserve');
  const tokenReserve = big(curve.tokenReserve, 'tokenReserve');
  if (tokenReserve > SUPPLY) throw new RangeError('tokenReserve above the supply');
  return { x: VIRTUAL_DOGE + dogeReserve, y: VIRTUAL_TOKENS + tokenReserve, dogeReserve, tokenReserve };
}

/**
 * Coins a buy of `dogeIn` receives now, or null when the buy is larger than
 * the supply left (the contract refuses it as "sold out").
 */
function quoteBuy(curve, dogeIn) {
  const { x, y, tokenReserve } = reserves(curve);
  const d = big(dogeIn, 'dogeIn');
  const out = d * y / (x + d);
  return out > tokenReserve ? null : out;
}

/** Native coin a sale of `tokenIn` receives now. */
function quoteSell(curve, tokenIn) {
  const { x, y } = reserves(curve);
  const t = big(tokenIn, 'tokenIn');
  return t * x / (y + t);
}

/** The most native coin one buy can spend and stay within the supply left. */
function maxBuy(curve) {
  const { x, tokenReserve } = reserves(curve);
  return x * tokenReserve / VIRTUAL_TOKENS;
}

/**
 * The marginal price, native coin per whole coin, as a decimal string with
 * `digits` significant figures. A display, never an input to a trade.
 */
function priceText(curve, digits = 4) {
  const { x, y } = reserves(curve);
  // x / y with both in base units; scale up to keep precision.
  const SCALE = 10n ** 30n;
  const scaled = x * SCALE / y; // price * 1e30
  const s = scaled.toString();
  if (scaled === 0n) return '0';
  const intDigits = s.length - 30;
  const sig = s.slice(0, digits);
  if (intDigits > 0) {
    return intDigits >= digits ? s.slice(0, intDigits) : `${sig.slice(0, intDigits)}.${sig.slice(intDigits)}`;
  }
  return `0.${'0'.repeat(-intDigits)}${sig}`.replace(/0+$/, '').replace(/\.$/, '');
}

/** Share of the supply sold off the curve, in basis points (0 to 10000). */
function soldBps(curve) {
  const { tokenReserve } = reserves(curve);
  return Number((SUPPLY - tokenReserve) * 10000n / SUPPLY);
}

module.exports = {
  E18, SUPPLY, VIRTUAL_DOGE, VIRTUAL_TOKENS,
  quoteBuy, quoteSell, maxBuy, priceText, soldBps,
};
