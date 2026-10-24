'use strict';
/**
 * Canonical copy. app/lib/sar is a byte copy the site loads.
 * Do not edit one side.
 *
 * Advisory HIP-3 open-interest cap. The documented action field is
 * setOpenInterestCaps: an array of [coin, cap] pairs. The cap integer
 * must be at least max(1_000_000, half the current open interest), in
 * the same integer unit the operator supplied. The Hyperliquid comment
 * calls 1_000_000 one size unit of collateral. This module does not
 * rescale, does not read a price, and does not sign or submit.
 *
 * HIP-3 is not in the v0 coin list. With no coin configured there is
 * no suggestion. An unreadable open interest is not a zero cap.
 */

const { readSar, THREE_BPS } = require('./alerts');

const UNIT_FLOOR = 1000000n;

function suggest(env, snapshot) {
  const src = env || {};
  const base = { signs: false, submits: false, action: null };
  const coinRaw = src.SAR_ORACLE_HIP3_COIN;
  if (coinRaw == null || String(coinRaw).trim() === '') {
    return { ...base, status: 'not_configured', reason: 'no_hip3_market' };
  }
  const read = readSar(snapshot);
  if (read.bps == null) return { ...base, status: 'not_read', reason: read.reason };
  if (read.bps <= THREE_BPS) {
    return { ...base, status: 'below_threshold', reason: null, sar95_bps: read.bps.toString() };
  }
  const oiRaw = src.SAR_ORACLE_HIP3_OI_UNITS;
  if (oiRaw == null || String(oiRaw).trim() === '') {
    return { ...base, status: 'not_read', reason: 'oi_unreadable' };
  }
  const oiText = String(oiRaw).trim();
  if (!/^\d+$/.test(oiText)) return { ...base, status: 'not_read', reason: 'unit_unreadable' };
  let oi;
  try {
    oi = BigInt(oiText);
  } catch {
    return { ...base, status: 'not_read', reason: 'unit_unreadable' };
  }
  const half = oi / 2n;
  const floor = half > UNIT_FLOOR ? half : UNIT_FLOOR;
  if (floor > BigInt(Number.MAX_SAFE_INTEGER)) {
    return { ...base, status: 'not_read', reason: 'overflow' };
  }
  const coin = String(coinRaw).trim();
  const cap = Number(floor);
  return {
    status: 'advisory',
    reason: null,
    signs: false,
    submits: false,
    floor: floor.toString(),
    floor_rule: 'max(1000000, oi_units/2)',
    sar95_bps: read.bps.toString(),
    action: {
      type: 'perpDeploy',
      setOpenInterestCaps: [[coin, cap]],
    },
  };
}

module.exports = { UNIT_FLOOR, suggest };
