'use strict';
/**
 * xnav_quote(vault, size).
 *
 * `size` is a fraction of the vault, greater than 0 and at most 1.
 * It is not a dollar amount. Epoch flow is the caller's figure. When the
 * caller does not pass one, the swing is unreadable: an omitted flow is
 * not a measured zero. The vault haircut is still returned.
 */

const sar = require('../sar/metrics');
const { loadPlaceholders } = require('./config');
const { findVault, isAddress, publicRow } = require('./index');
const { previewRedeem } = require('./swing');

const TOTAL = 10000n;

function parseFraction(raw) {
  if (typeof raw !== 'string' && typeof raw !== 'number') return null;
  const text = String(raw).trim();
  if (text === '') return null;
  return sar.parseDec(text);
}

function quote(doc, vault, size, flow) {
  if (!isAddress(vault)) {
    return { readable: false, reason: 'invalid_vault', haircut_bps: null, swing_bps: null, stress_bps: null };
  }
  const row = findVault(doc, vault);
  if (!row) {
    return {
      readable: false,
      reason: 'not_in_snapshot',
      vault: vault.toLowerCase(),
      haircut_bps: null,
      swing_bps: null,
      stress_bps: null,
    };
  }
  const pub = publicRow(row);
  const frac = parseFraction(size);
  if (frac == null || frac <= 0n || frac > sar.WAD) {
    return {
      readable: false,
      reason: 'size_unreadable',
      vault: pub.address,
      haircut_bps: pub.haircut_bps,
      stress_bps: pub.stress_bps,
      swing_bps: null,
    };
  }
  if (pub.haircut_bps == null) {
    return {
      readable: false,
      reason: 'haircut_unreadable',
      vault: pub.address,
      haircut_bps: null,
      stress_bps: pub.stress_bps,
      swing_bps: null,
    };
  }
  if (flow == null || flow === '') {
    return {
      readable: true,
      reason: 'epoch_flow_unreadable',
      vault: pub.address,
      name: pub.name,
      haircut_bps: pub.haircut_bps,
      stress_bps: pub.stress_bps,
      swing_bps: null,
      size: String(size).trim(),
    };
  }
  const flowFrac = parseFraction(flow);
  if (flowFrac == null || flowFrac < 0n || flowFrac > sar.WAD) {
    return {
      readable: false,
      reason: 'epoch_flow_unreadable',
      vault: pub.address,
      haircut_bps: pub.haircut_bps,
      stress_bps: pub.stress_bps,
      swing_bps: null,
    };
  }
  const ph = loadPlaceholders();
  const shares = frac * TOTAL / sar.WAD;
  const epochFlow = flowFrac * TOTAL / sar.WAD;
  const swung = previewRedeem(
    TOTAL,
    TOTAL,
    shares,
    epochFlow,
    BigInt(ph.flow_threshold_bps),
    BigInt(pub.haircut_bps),
    BigInt(ph.max_swing_bps),
  );
  if (swung.readable !== true) {
    return {
      readable: false,
      reason: swung.reason,
      vault: pub.address,
      haircut_bps: pub.haircut_bps,
      stress_bps: pub.stress_bps,
      swing_bps: null,
    };
  }
  return {
    readable: true,
    reason: null,
    vault: pub.address,
    name: pub.name,
    haircut_bps: pub.haircut_bps,
    stress_bps: pub.stress_bps,
    swing_bps: Number(swung.swingBps),
    size: String(size).trim(),
    epoch_flow: String(flow).trim(),
    label: 'Basis points of account value. Not a dollar amount.',
  };
}

const TOOL = {
  name: 'xnav_quote',
  description: 'Exit haircut for a fraction of one vault, in basis points of account value. size is a fraction from 0 to 1, not a dollar amount. flow is the fraction of the vault already redeemed this epoch; omit it and the swing is unreadable.',
  inputSchema: {
    type: 'object',
    properties: {
      vault: { type: 'string', description: 'Vault address, 0x and 40 hex characters.' },
      size: { type: 'string', description: 'Fraction of the vault, greater than 0 and at most 1.' },
      flow: { type: 'string', description: 'Fraction of the vault already in this epoch\'s flow. Omit and swing_bps is unreadable.' },
    },
    required: ['vault', 'size'],
  },
};

module.exports = { quote, parseFraction, TOOL };
