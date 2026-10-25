'use strict';
/**
 * Swing on a redemption or a deposit.
 *
 * The portion of this flow that still sits under the epoch threshold is
 * at net asset value. The portion above it pays a pro-rata slice of the
 * vault haircut, capped by maxSwingBps. The haircut that is not paid out
 * stays in the vault. A missing haircut is not a zero swing.
 *
 * The same split is what SwingVault4626.previewRedeem runs. The contract
 * test compares the two.
 */

function split(totalAssets, amount, epochFlow, thresholdBps, haircutBps, maxSwingBps) {
  if (typeof totalAssets !== 'bigint' || typeof amount !== 'bigint' || typeof epochFlow !== 'bigint') {
    return { readable: false, reason: 'unreadable', credited: null, swingBps: null, retained: null };
  }
  if (typeof haircutBps !== 'bigint' || typeof thresholdBps !== 'bigint' || typeof maxSwingBps !== 'bigint') {
    return { readable: false, reason: 'haircut_unreadable', credited: null, swingBps: null, retained: null };
  }
  if (totalAssets < 0n || amount < 0n || epochFlow < 0n || haircutBps < 0n) {
    return { readable: false, reason: 'unreadable', credited: null, swingBps: null, retained: null };
  }
  const threshold = totalAssets * thresholdBps / 10000n;
  const room = epochFlow >= threshold ? 0n : threshold - epochFlow;
  const under = amount < room ? amount : room;
  const taxed = amount - under;
  let swingBps = 0n;
  if (taxed > 0n && totalAssets > 0n) {
    const sliceBps = taxed * 10000n / totalAssets;
    swingBps = haircutBps * sliceBps / 10000n;
    if (swingBps > maxSwingBps) swingBps = maxSwingBps;
  }
  const paid = taxed * (10000n - swingBps) / 10000n;
  return {
    readable: true,
    reason: null,
    credited: under + paid,
    swingBps,
    retained: taxed - paid,
  };
}

function previewRedeem(totalAssets, supply, shares, epochFlow, thresholdBps, haircutBps, maxSwingBps) {
  if (typeof supply !== 'bigint' || typeof shares !== 'bigint') {
    return { readable: false, reason: 'unreadable', assetsOut: null, swingBps: null, retained: null };
  }
  if (supply <= 0n) {
    return { readable: false, reason: 'no_supply', assetsOut: null, swingBps: null, retained: null };
  }
  if (typeof totalAssets !== 'bigint' || totalAssets < 0n) {
    return { readable: false, reason: 'equity_unreadable', assetsOut: null, swingBps: null, retained: null };
  }
  const atNav = shares * totalAssets / supply;
  const swung = split(totalAssets, atNav, epochFlow, thresholdBps, haircutBps, maxSwingBps);
  if (swung.readable !== true) {
    return { readable: false, reason: swung.reason, assetsOut: null, swingBps: null, retained: null };
  }
  return {
    readable: true,
    reason: null,
    assetsOut: swung.credited,
    swingBps: swung.swingBps,
    retained: swung.retained,
  };
}

function previewDeposit(totalAssets, supply, assets, epochFlow, thresholdBps, haircutBps, maxSwingBps) {
  if (typeof assets !== 'bigint' || assets <= 0n) {
    return { readable: false, reason: 'unreadable', shares: null, swingBps: null, retained: null };
  }
  if (typeof supply !== 'bigint') {
    return { readable: false, reason: 'unreadable', shares: null, swingBps: null, retained: null };
  }
  if (supply === 0n) {
    return { readable: true, reason: null, shares: assets, swingBps: 0n, retained: 0n };
  }
  const swung = split(totalAssets, assets, epochFlow, thresholdBps, haircutBps, maxSwingBps);
  if (swung.readable !== true || swung.credited == null || swung.credited <= 0n || totalAssets <= 0n) {
    return {
      readable: false,
      reason: swung.reason || 'unreadable',
      shares: null,
      swingBps: null,
      retained: null,
    };
  }
  const shares = swung.credited * supply / totalAssets;
  if (shares <= 0n) {
    return { readable: false, reason: 'unreadable', shares: null, swingBps: null, retained: null };
  }
  return { readable: true, reason: null, shares, swingBps: swung.swingBps, retained: swung.retained };
}

module.exports = { split, previewRedeem, previewDeposit };
