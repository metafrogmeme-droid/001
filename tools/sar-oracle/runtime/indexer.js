'use strict';
/**
 * Canonical copy. app/lib/sar is a byte copy the site loads.
 * Do not edit one side.
 *
 * One cycle a minute. Requiring this module does not start it and does
 * not open a socket. start() is the caller's decision, and the caller
 * checks SAR_ORACLE_ENABLED first.
 *
 * snapshot() is the public feed: ratios and scores. A key that carries a
 * dollar amount is not on it. operatorSnapshot() is the only object that
 * carries those amounts, and the route serves it to the operator alone.
 * A field that was not read is null with a reason. It is not zero.
 */

const {
  HL_COINS, JUP_MINTS, FLAG, PERSIST_MS, CONCENTRATION_BPS,
} = require('./chains');
const metrics = require('./metrics');
const packet = require('./packet');
const sources = require('./sources');
const canary = require('./canary');
const alerts = require('./alerts');

const MIN_INTERVAL_MS = 60000;
const TWAP_MS = 5 * 60 * 1000;
const DEFAULT_BETA = '0.10';
const DEFAULT_BAND = 100;
const DEFAULT_STALE = 180;
const LABEL = 'public signals, not advice';
const LD_BPS = Object.freeze([100, 200, 500]);

const NOTES = Object.freeze([
  'public signals, not advice',
  'The public feed publishes ratios and scores. Dollar amounts are not on it.',
  'Stress size walked through the book is beta times open interest. A value-at-risk cap is not applied, so the paper stress size is not published.',
  'Account concentration needs the local node book. The public book is an order stand-in and does not feed the haircut.',
  'Jupiter perps publish no order book.',
  'The public book is at most 20 levels a side. A full book needs the local node.',
  'Adjusted slippage is published only after a five-minute window of the haircut. Until then the spot haircut is a separate field.',
  'There is no public dashboard in this version.',
]);

const HIDDEN_KEYS = new Set(['operator_amounts', 'mid_e8', 'ref', 'mark', 'px']);

let latestFull = null;
let timer = null;
let seq = 0;
let epochId = 0;
const history = new Map();

function intSetting(raw, fallback, lo, hi) {
  if (raw == null || String(raw).trim() === '') return { value: fallback, reason: null };
  const t = String(raw).trim();
  if (!/^\d+$/.test(t)) return { value: null, reason: 'unreadable' };
  const n = Number(t);
  if (!Number.isSafeInteger(n) || n < lo || n > hi) return { value: null, reason: 'unreadable' };
  return { value: n, reason: null };
}

function readBeta(env) {
  const raw = env.SAR_ORACLE_BETA;
  if (raw == null || String(raw).trim() === '') {
    return { text: DEFAULT_BETA, wad: metrics.parseDec(DEFAULT_BETA), reason: null };
  }
  const text = String(raw).trim();
  const wad = metrics.parseDec(text);
  if (wad == null || wad <= 0n || wad > metrics.WAD) {
    return { text: null, wad: null, reason: 'beta_unreadable' };
  }
  return { text: metrics.wadToDecimal(wad), wad, reason: null };
}

function readPolicy(env) {
  const src = env || process.env;
  const beta = readBeta(src);
  const band = intSetting(src.SAR_ORACLE_BAND_BPS, DEFAULT_BAND, 1, 10000);
  const stale = intSetting(src.SAR_ORACLE_MAX_STALENESS_SEC, DEFAULT_STALE, 1, 86400);
  const poll = intSetting(src.SAR_ORACLE_POLL_MS, MIN_INTERVAL_MS, 1, 86400000);
  const interval = poll.value == null ? MIN_INTERVAL_MS : Math.max(MIN_INTERVAL_MS, poll.value);
  return {
    beta,
    band: band.value,
    band_reason: band.reason ? 'band_unreadable' : null,
    max_staleness_sec: stale.value,
    staleness_reason: stale.reason ? 'max_staleness_unreadable' : null,
    interval_ms: interval,
  };
}

/** Public copy. Dollar keys and the operator bag never leave this function. */
function publicView(value) {
  if (Array.isArray(value)) return value.map((item) => publicView(item));
  if (value && typeof value === 'object') {
    const out = {};
    for (const key of Object.keys(value)) {
      if (HIDDEN_KEYS.has(key) || key.toLowerCase().indexOf('_usd_') !== -1) continue;
      out[key] = publicView(value[key]);
    }
    return out;
  }
  return value;
}

function blankScore(reason) {
  return {
    v: 1,
    venue: null,
    dex: null,
    asset: null,
    coin: null,
    l1Block: null,
    ts: null,
    seq: null,
    s_bps: null,
    s_reason: reason,
    s_adj_bps: null,
    s_adj_reason: reason,
    s_adj_spot_bps: null,
    s_adj_spot_reason: reason,
    neff_e2: null,
    neff_reason: reason,
    cr1_bps: null,
    cr1_reason: reason,
    ld1_oi_bps: null,
    ld2_oi_bps: null,
    ld5_oi_bps: null,
    ld_reason: reason,
    flags: null,
    flag_gaps: ['peg_not_read'],
    dominant_account: null,
    sys: { sar95_bps: null, esar95_bps: null, reason },
  };
}

function emptyAmounts() {
  return {
    mid_e8: null,
    oi_usd_e6: null,
    oi_reason: 'not_read',
    q_usd_e6: null,
    q_reason: 'var_cap_absent',
    ld1_usd_e6: null,
    ld2_usd_e6: null,
    ld5_usd_e6: null,
    ld_reason: 'not_read',
    tsar95_usd_e6: null,
    tsar_reason: 'var_cap_absent',
  };
}

function emptyMarket(venue, market) {
  const code = venue === 'hyperliquid' ? 'HL' : 'JUP';
  const schema = blankScore('not_read');
  schema.venue = code;
  schema.coin = market;
  schema.dex = code === 'HL' ? 0 : null;
  return {
    venue,
    market,
    slippage: {
      value: null,
      reason: 'not_read',
      bid: { value: null, reason: 'not_read' },
      ask: { value: null, reason: 'not_read' },
    },
    n_eff: { value: null, reason: 'not_read' },
    cr1: { value: null, reason: 'not_read' },
    order_n_eff: { value: null, reason: 'not_read' },
    order_cr1: { value: null, reason: 'not_read' },
    oi_within_band: { value: null, reason: 'not_read', unreadable_positions: null },
    observed_at: null,
    schema,
    operator_amounts: emptyAmounts(),
    proof: null,
  };
}

function universe() {
  const markets = HL_COINS.map((m) => emptyMarket('hyperliquid', m));
  for (const m of Object.keys(JUP_MINTS)) markets.push(emptyMarket('jupiter', m));
  return markets;
}

function shell(policy, cfg, node, canaryState) {
  return {
    label: LABEL,
    notes: NOTES.slice(),
    chain_id: cfg.chainId,
    signing: packet.signingLabel(cfg),
    signing_reason: cfg.ok ? null : cfg.reason,
    beta: policy.beta.text,
    beta_reason: policy.beta.reason,
    band_bps: policy.band,
    band_reason: policy.band_reason,
    max_staleness_sec: policy.max_staleness_sec,
    staleness_reason: policy.staleness_reason,
    source: node && node.configured ? 'node' : 'public_api',
    node_reason: node && node.reason ? node.reason : null,
    canary: {
      tripped: canaryState ? canaryState.tripped : null,
      reason: canaryState ? canaryState.reason : 'not_read',
    },
    epoch: {
      id: null,
      root: null,
      signature: null,
      reason: 'not_read',
      l1_block: null,
      l1_reason: 'l1_block_unreadable',
      signing_reason: cfg.ok ? null : cfg.reason,
    },
    markets: universe(),
  };
}

function sideOut(side) {
  if (!side) return { value: null, reason: 'not_read' };
  return { value: side.value, reason: side.reason };
}

function slipReason(slip) {
  if (slip.signed != null) return null;
  if (slip.bid && slip.bid.reason) return slip.bid.reason;
  if (slip.ask && slip.ask.reason) return slip.ask.reason;
  return 'not_read';
}

function bpsText(wad) {
  if (typeof wad !== 'bigint' || wad < 0n) return { text: null, reason: 'unreadable' };
  const bps = metrics.bpsFromWad(wad);
  if (bps == null) return { text: null, reason: 'unreadable' };
  if (bps >= (1n << 32n)) return { text: null, reason: 'overflow' };
  return { text: bps.toString(), reason: null };
}

function hundredthsText(wad) {
  const h = metrics.hundredths(wad);
  if (h == null) return { text: null, reason: 'unreadable' };
  if (h >= (1n << 32n)) return { text: null, reason: 'overflow' };
  return { text: h.toString(), reason: null };
}

function e6Text(wad) {
  if (wad == null) return { text: null, reason: 'not_read' };
  const n = metrics.e6FromWad(wad);
  if (n == null) return { text: null, reason: 'unreadable' };
  if (n >= (1n << 128n)) return { text: null, reason: 'overflow' };
  return { text: n.toString(), reason: null };
}

function e8Text(wad) {
  if (wad == null) return { text: null, reason: 'not_read' };
  const n = metrics.e8FromWad(wad);
  if (n == null) return { text: null, reason: 'unreadable' };
  if (n >= (1n << 64n)) return { text: null, reason: 'overflow' };
  return { text: n.toString(), reason: null };
}

function rowsFor(positions, coin, ref) {
  const mine = [];
  for (const row of positions) {
    const key = row.coin != null ? row.coin : row.asset;
    if (key == null) {
      if (row.unreadable) mine.push({ unreadable: true });
      continue;
    }
    if (key !== coin) continue;
    if (row.unreadable) {
      mine.push({ unreadable: true });
      continue;
    }
    mine.push({
      size: row.size,
      liq: row.liq,
      ref: row.ref != null ? row.ref : ref,
    });
  }
  return mine;
}

function extreme(rows, side) {
  let best = null;
  for (const row of rows || []) {
    const px = typeof row.px === 'bigint' ? row.px : metrics.parseDec(row && row.px);
    if (px == null) return null;
    if (best == null) best = px;
    else if (side === 'bid' && px > best) best = px;
    else if (side === 'ask' && px < best) best = px;
  }
  return best;
}

function nodeMid(book) {
  if (!book) return null;
  const bid = extreme(book.bids, 'bid');
  const ask = extreme(book.asks, 'ask');
  if (bid == null || ask == null) return null;
  return (bid + ask) / 2n;
}

function publicMid(book) {
  if (!book) return null;
  const bids = metrics.parseSide(book.bids, 'bid');
  const asks = metrics.parseSide(book.asks, 'ask');
  if (bids.reason || asks.reason) return null;
  return (bids.levels[0].px + asks.levels[0].px) / 2n;
}

function noteSample(coin, nowMs, wad) {
  let slot = history.get(coin);
  if (!slot) {
    slot = { since: nowMs, samples: [] };
    history.set(coin, slot);
  }
  slot.samples.push({ t: nowMs, wad });
  const cutoff = nowMs - TWAP_MS;
  slot.samples = slot.samples.filter((s) => s.t >= cutoff);
}

function twapOf(coin, nowMs) {
  const slot = history.get(coin);
  if (!slot || slot.samples.length === 0) return { wad: null, reason: 'twap_incomplete' };
  if (nowMs - slot.since < TWAP_MS) return { wad: null, reason: 'twap_incomplete' };
  let sum = 0n;
  for (const s of slot.samples) sum += s.wad;
  return { wad: sum / BigInt(slot.samples.length), reason: null };
}

function accountFromNode(book, nowMs, mid) {
  const bids = metrics.applyPersistence(book.bids, nowMs, PERSIST_MS);
  const asks = metrics.applyPersistence(book.asks, nowMs, PERSIST_MS);
  if (bids.reason || asks.reason) {
    const reason = bids.reason || asks.reason;
    return { nEff: metrics.blank(reason), cr1: metrics.blank(reason), dominant: null };
  }
  const rows = bids.levels.concat(asks.levels);
  if (rows.length === 0) {
    return {
      nEff: metrics.blank('no_persistent_size'),
      cr1: metrics.blank('no_persistent_size'),
      dominant: null,
    };
  }
  if (mid == null) {
    return { nEff: metrics.blank('book_unreadable'), cr1: metrics.blank('book_unreadable'), dominant: null };
  }
  return metrics.accountConcentration(rows, mid, CONCENTRATION_BPS);
}

function oiUsd(oi, ref) {
  const o = metrics.parseDec(oi);
  const r = metrics.parseDec(ref);
  if (o == null || r == null || o < 0n || r <= 0n) return { wad: null, refWad: r, reason: 'oi_unreadable' };
  return { wad: o * r / metrics.WAD, refWad: r, reason: null };
}

function density(node, coin, ref) {
  if (!node || !node.configured) return { reason: 'node_unconfigured', bands: null };
  if (node.reason && !node.liquidatable) return { reason: node.reason, bands: null };
  const liq = node.liquidatable;
  if (!liq || liq.reason) return { reason: (liq && liq.reason) || 'source_unreadable', bands: null };
  if (!Object.prototype.hasOwnProperty.call(liq.byCoin, coin)) {
    return { reason: 'source_unreadable', bands: null };
  }
  const rows = liq.byCoin[coin].map((row) => ({
    value: row.value,
    liq: row.liq,
    ref: row.ref != null ? row.ref : ref,
  }));
  if (rows.length === 0) return { reason: null, bands: { 100: 0n, 200: 0n, 500: 0n } };
  const bands = {};
  for (const bps of LD_BPS) {
    const d = metrics.liquidationDensity(rows, bps);
    if (d.wad == null) return { reason: d.reason, bands: null };
    bands[bps] = d.wad;
  }
  return { reason: null, bands };
}

function ratioBps(ldWad, oiWad, ldReason, oiReason) {
  if (ldReason) return { text: null, reason: ldReason };
  if (oiReason || oiWad == null) return { text: null, reason: oiReason || 'oi_unreadable' };
  if (oiWad === 0n) return { text: null, reason: 'oi_is_zero' };
  if (ldWad == null) return { text: null, reason: 'not_read' };
  return bpsText(ldWad * metrics.WAD / oiWad);
}

function nextSeq() {
  seq += 1;
  return seq;
}

function inputStale(timeMs, nowMs, maxSec) {
  if (timeMs == null || maxSec == null) return false;
  if (timeMs > nowMs) return false;
  return (nowMs - timeMs) > maxSec * 1000;
}

function hlMarket(coin, hl, node, policy, observedSec, nowMs, canaryState) {
  const bookSlot = hl.books[coin] || { book: null, reason: 'not_read', truncated: null };
  const meta = hl.meta && hl.meta[coin];
  const oi = meta ? meta.oi : null;
  const ref = meta ? meta.ref : null;
  let slip;
  let order;
  if (!bookSlot.book) {
    const reason = bookSlot.reason || 'source_unreadable';
    slip = { bid: metrics.blank(reason), ask: metrics.blank(reason), signed: null };
    order = { nEff: metrics.blank(reason), cr1: metrics.blank(reason) };
  } else {
    const q = metrics.qFromOi(policy.beta.wad, hl.metaReason ? null : oi);
    const qReason = hl.metaReason ? 'oi_unreadable' : q.reason;
    slip = metrics.slippage(bookSlot.book, q.q, qReason);
    order = metrics.concentration(bookSlot.book);
  }
  const nodeSlot = node && node.books ? node.books[coin] : null;
  const mid = publicMid(bookSlot.book) || (nodeSlot && nodeSlot.book ? nodeMid(nodeSlot.book) : null);
  let account;
  if (!node || !node.configured) {
    account = { nEff: metrics.blank('no_account_book'), cr1: metrics.blank('no_account_book'), dominant: null };
  } else if (!nodeSlot || !nodeSlot.book) {
    const reason = (nodeSlot && nodeSlot.reason) || node.reason || 'no_account_book';
    account = { nEff: metrics.blank(reason), cr1: metrics.blank(reason), dominant: null };
  } else {
    account = accountFromNode(nodeSlot.book, nowMs, mid, CONCENTRATION_BPS);
  }
  let spotAdj = null;
  let spotReason = account.nEff.reason || 'no_account_book';
  if (account.nEff.wad != null && account.cr1.wad != null && slip.signed != null) {
    const cut = metrics.haircut(account.nEff.wad, account.cr1.wad);
    if (cut.h == null) spotReason = cut.reason;
    else {
      spotAdj = metrics.adjustSlippage(slip.signed, cut.h);
      spotReason = spotAdj == null ? 'unreadable' : null;
    }
  } else if (slip.signed == null && account.nEff.reason == null) {
    spotReason = slipReason(slip);
  }
  if (spotAdj != null) noteSample(coin, nowMs, spotAdj);
  let published = null;
  let pubReason = spotReason;
  if (spotAdj != null) {
    const twap = twapOf(coin, nowMs);
    if (twap.wad == null) pubReason = 'twap_incomplete';
    else {
      published = spotAdj > twap.wad ? spotAdj : twap.wad;
      pubReason = null;
    }
  }
  const money = oiUsd(hl.metaReason ? null : oi, ref);
  const dens = density(node, coin, ref);
  const amounts = emptyAmounts();
  const midField = e8Text(money.refWad);
  amounts.mid_e8 = midField.text;
  const oiField = e6Text(money.wad);
  amounts.oi_usd_e6 = oiField.text;
  amounts.oi_reason = money.reason || oiField.reason;
  amounts.q_usd_e6 = null;
  amounts.q_reason = 'var_cap_absent';
  amounts.ld_reason = dens.reason;
  if (dens.bands) {
    const a = e6Text(dens.bands[100]);
    const b = e6Text(dens.bands[200]);
    const c = e6Text(dens.bands[500]);
    amounts.ld1_usd_e6 = a.text;
    amounts.ld2_usd_e6 = b.text;
    amounts.ld5_usd_e6 = c.text;
    amounts.ld_reason = a.reason || b.reason || c.reason;
  }
  const r1 = ratioBps(dens.bands && dens.bands[100], money.wad, amounts.ld_reason, money.reason);
  const r2 = ratioBps(dens.bands && dens.bands[200], money.wad, amounts.ld_reason, money.reason);
  const r5 = ratioBps(dens.bands && dens.bands[500], money.wad, amounts.ld_reason, money.reason);
  const sBps = slip.signed == null ? { text: null, reason: slipReason(slip) } : bpsText(slip.signed);
  const adjBps = published == null ? { text: null, reason: pubReason } : bpsText(published);
  const spotBps = spotAdj == null ? { text: null, reason: spotReason } : bpsText(spotAdj);
  const neff = account.nEff.wad == null
    ? { text: null, reason: account.nEff.reason || 'no_account_book' }
    : hundredthsText(account.nEff.wad);
  const cr = account.cr1.wad == null
    ? { text: null, reason: account.cr1.reason || 'no_account_book' }
    : bpsText(account.cr1.wad);
  const gaps = ['peg_not_read', 'hip3_not_in_universe'];
  let flags = 0;
  if (bookSlot.truncated === true) flags |= FLAG.bookTruncated;
  else if (bookSlot.truncated == null) gaps.push('book_truncation_unreadable');
  const status = node && node.status;
  if (status && status.lagged === true) flags |= FLAG.nodeLag;
  else if (!status || status.reason) gaps.push((status && status.reason) || 'node_lag_unreadable');
  if (canaryState && canaryState.tripped === true) flags |= FLAG.quantumCanary;
  const timeMs = bookSlot.book && bookSlot.book.time != null ? bookSlot.book.time : null;
  if (timeMs == null) gaps.push('book_time_unreadable');
  else if (inputStale(timeMs, nowMs, policy.max_staleness_sec)) flags |= FLAG.staleInput;
  const asset = meta && Number.isSafeInteger(meta.index) && meta.index >= 0 ? meta.index : null;
  const l1 = status && Number.isSafeInteger(status.l1Block) && status.l1Block >= 0 ? status.l1Block : null;
  const observed = timeMs != null ? Math.trunc(timeMs / 1000) : (bookSlot.book ? observedSec : null);
  let oiBand;
  if (hl.positionReason) oiBand = metrics.blank(hl.positionReason);
  else if (policy.band_reason) oiBand = metrics.blank(policy.band_reason);
  else if (!ref) oiBand = metrics.blank('reference_unreadable');
  else oiBand = metrics.oiWithinBand(rowsFor(hl.positions || [], coin, ref), policy.band);
  const schema = blankScore('not_read');
  schema.venue = 'HL';
  schema.dex = 0;
  schema.asset = asset;
  schema.coin = coin;
  schema.l1Block = l1;
  schema.ts = observed;
  schema.seq = nextSeq();
  schema.s_bps = sBps.text;
  schema.s_reason = sBps.reason;
  schema.s_adj_bps = adjBps.text;
  schema.s_adj_reason = adjBps.reason;
  schema.s_adj_spot_bps = spotBps.text;
  schema.s_adj_spot_reason = spotBps.reason;
  schema.neff_e2 = neff.text;
  schema.neff_reason = neff.reason;
  schema.cr1_bps = cr.text;
  schema.cr1_reason = cr.reason;
  schema.ld1_oi_bps = r1.text;
  schema.ld2_oi_bps = r2.text;
  schema.ld5_oi_bps = r5.text;
  schema.ld_reason = r1.reason || r2.reason || r5.reason;
  schema.flags = flags;
  schema.flag_gaps = gaps;
  schema.dominant_account = account.dominant;
  schema.sys = { sar95_bps: null, esar95_bps: null, reason: 'partial_set' };
  const orderOut = order.nEff.reason == null
    ? { value: order.nEff.value, reason: 'order_stand_in' }
    : { value: null, reason: order.nEff.reason };
  const orderCr = order.cr1.reason == null
    ? { value: order.cr1.value, reason: 'order_stand_in' }
    : { value: null, reason: order.cr1.reason };
  return {
    venue: 'hyperliquid',
    market: coin,
    slippage: {
      value: slip.signed == null ? null : metrics.wadToDecimal(slip.signed),
      reason: slipReason(slip),
      bid: sideOut(slip.bid),
      ask: sideOut(slip.ask),
    },
    n_eff: { value: account.nEff.value, reason: account.nEff.reason },
    cr1: { value: account.cr1.value, reason: account.cr1.reason },
    order_n_eff: orderOut,
    order_cr1: orderCr,
    oi_within_band: {
      value: oiBand.value,
      reason: oiBand.reason,
      unreadable_positions: oiBand.unreadable_positions,
    },
    observed_at: observed,
    schema,
    operator_amounts: amounts,
    proof: null,
    _pubAdj: published,
    _l1: l1,
  };
}

function jupMarket(market, jup, policy, observedSec, canaryState) {
  const pool = jup.pools[market] || { answered: false, reason: 'source_unreadable' };
  const slip = {
    bid: metrics.blank('no_order_book'),
    ask: metrics.blank('no_order_book'),
    signed: null,
  };
  let oiBand;
  if (jup.positions.reason) oiBand = metrics.blank(jup.positions.reason);
  else if (policy.band_reason) oiBand = metrics.blank(policy.band_reason);
  else oiBand = metrics.oiWithinBand(rowsFor(jup.positions.rows || [], market, null), policy.band);
  const observed = pool.answered ? observedSec : null;
  const flags = canaryState && canaryState.tripped === true ? FLAG.quantumCanary : 0;
  const schema = blankScore('no_order_book');
  schema.venue = 'JUP';
  schema.coin = market;
  schema.ts = observed;
  schema.flags = pool.answered ? flags : null;
  schema.flag_gaps = ['peg_not_read', 'no_order_book', 'unit_unreadable'];
  schema.ld_reason = 'unit_unreadable';
  schema.sys = { sar95_bps: null, esar95_bps: null, reason: 'no_order_book' };
  const amounts = emptyAmounts();
  amounts.oi_reason = 'unit_unreadable';
  amounts.ld_reason = 'unit_unreadable';
  const row = {
    venue: 'jupiter',
    market,
    slippage: {
      value: null,
      reason: 'no_order_book',
      bid: sideOut(slip.bid),
      ask: sideOut(slip.ask),
    },
    n_eff: { value: null, reason: 'no_order_book' },
    cr1: { value: null, reason: 'no_order_book' },
    order_n_eff: { value: null, reason: 'no_order_book' },
    order_cr1: { value: null, reason: 'no_order_book' },
    oi_within_band: {
      value: oiBand.value,
      reason: oiBand.reason,
      unreadable_positions: oiBand.unreadable_positions,
    },
    observed_at: observed,
    schema,
    operator_amounts: amounts,
    proof: null,
  };
  if (!pool.answered && pool.reason) row.source_reason = pool.reason;
  return row;
}

function fillSys(markets) {
  const hl = markets.filter((m) => m.venue === 'hyperliquid');
  const wads = [];
  for (const m of hl) {
    if (m._pubAdj == null) {
      const sys = { sar95_bps: null, esar95_bps: null, reason: 'partial_set' };
      for (const row of hl) row.schema.sys = sys;
      return;
    }
    wads.push(m._pubAdj);
  }
  const q = metrics.systemQuantile(wads);
  if (q.reason || q.sar == null || q.esar == null) {
    const sys = { sar95_bps: null, esar95_bps: null, reason: q.reason || 'partial_set' };
    for (const row of hl) row.schema.sys = sys;
    return;
  }
  const sar = bpsText(q.sar);
  const esar = bpsText(q.esar);
  const sys = {
    sar95_bps: sar.text,
    esar95_bps: esar.text,
    reason: sar.reason || esar.reason,
  };
  for (const row of hl) row.schema.sys = sys;
}

function metricOf(m) {
  const s = m.schema;
  const a = m.operator_amounts;
  const num = (v) => (v == null ? null : BigInt(v));
  return {
    asset: s.asset == null ? null : BigInt(s.asset),
    dex: s.dex == null ? null : BigInt(s.dex),
    l1Block: s.l1Block == null ? null : BigInt(s.l1Block),
    ts: s.ts == null ? null : BigInt(s.ts),
    seq: s.seq == null ? null : BigInt(s.seq),
    sAdjBps: num(s.s_adj_bps),
    neffE2: num(s.neff_e2),
    cr1Bps: num(s.cr1_bps),
    ld2UsdE6: num(a.ld2_usd_e6),
    tsar95UsdE6: num(a.tsar95_usd_e6),
    flags: s.flags == null ? null : BigInt(s.flags),
  };
}

async function attachEpoch(body, markets, cfg) {
  const rows = [];
  let l1 = null;
  let l1Disagree = false;
  for (const m of markets) {
    if (m.venue !== 'hyperliquid') continue;
    if (l1 == null) l1 = m._l1 == null ? null : m._l1;
    else if (m._l1 !== l1) l1Disagree = true;
    const leaf = packet.metricLeaf(metricOf(m));
    if (leaf) rows.push({ market: m, leaf });
  }
  const epoch = {
    id: null,
    root: null,
    signature: null,
    signer: null,
    scheme: null,
    scheme_id: null,
    reason: rows.length === 0 ? 'no_complete_metric' : null,
    l1_block: l1Disagree ? null : l1,
    l1_reason: l1 == null || l1Disagree ? 'l1_block_unreadable' : null,
    signing_reason: cfg.ok ? null : cfg.reason,
  };
  if (rows.length > 0 && (l1 == null || l1Disagree)) epoch.reason = 'l1_block_unreadable';
  else if (rows.length > 0 && !cfg.ok) epoch.reason = cfg.reason;
  if (rows.length > 0 && epoch.reason == null) {
    const built = packet.merkle(rows.map((r) => r.leaf));
    epochId += 1;
    try {
      const signed = await packet.signEpoch(cfg, {
        epoch: epochId,
        root: built.root,
        l1Block: l1,
      });
      epoch.id = epochId;
      epoch.root = built.root;
      epoch.signature = signed.signature;
      epoch.signer = signed.signer;
      epoch.scheme = signed.scheme;
      epoch.scheme_id = signed.scheme_id;
      rows.forEach((r, i) => { r.market.proof = built.proofs[i]; });
    } catch (err) {
      epoch.reason = err && err.name ? err.name : 'Error';
    }
  }
  for (const m of markets) {
    delete m._pubAdj;
    delete m._l1;
  }
  body.epoch = epoch;
}

async function cycle(deps) {
  const d = deps || {};
  const env = d.env || process.env;
  const fetchImpl = d.fetch || globalThis.fetch;
  const sleep = d.sleep || ((ms) => new Promise((r) => { setTimeout(r, ms); }));
  const now = d.now ? d.now() : Date.now();
  const policy = readPolicy(env);
  const cfg = packet.signerConfig(env);
  const lists = sources.addressLists(env);
  const hl = await sources.readHyperliquid({ fetchImpl, sleep, addresses: lists.hl });
  const jup = await sources.readJupiter({ fetchImpl, wallets: lists.jup });
  const node = await sources.readNode({ fetchImpl, sleep, env, nowMs: now });
  const canaryState = await canary.readCanary({ fetch: fetchImpl, env });
  const observedSec = Math.trunc(now / 1000);
  const markets = HL_COINS.map((c) => hlMarket(c, hl, node, policy, observedSec, now, canaryState));
  fillSys(markets);
  for (const m of Object.keys(JUP_MINTS)) {
    markets.push(jupMarket(m, jup, policy, observedSec, canaryState));
  }
  const body = shell(policy, cfg, node, canaryState);
  body.markets = markets;
  body.generated_at = observedSec;
  await attachEpoch(body, markets, cfg);
  const encoded = JSON.stringify(body);
  latestFull = JSON.parse(encoded);
  return publicView(latestFull);
}

function withStaleness(body, nowMs) {
  const nowSec = Math.trunc(nowMs / 1000);
  const max = body.max_staleness_sec;
  const markets = body.markets.map((m) => {
    let staleness = null;
    let stale = null;
    if (m.observed_at != null && m.observed_at <= nowSec) {
      staleness = nowSec - m.observed_at;
      stale = max == null ? null : staleness > max;
    }
    return { ...m, staleness_sec: staleness, stale };
  });
  return { ...body, markets };
}

function fullNow(nowMs, env) {
  const now = nowMs == null ? Date.now() : nowMs;
  if (!latestFull) {
    const policy = readPolicy(env || process.env);
    const cfg = packet.signerConfig(env || process.env);
    return withStaleness(shell(policy, cfg, null, null), now);
  }
  return withStaleness(latestFull, now);
}

function snapshot(nowMs, env) {
  return publicView(fullNow(nowMs, env));
}

function operatorSnapshot(nowMs, env) {
  return fullNow(nowMs, env);
}

function start(deps) {
  if (timer) return stop;
  const env = (deps && deps.env) || process.env;
  const every = readPolicy(env).interval_ms;
  const run = () => {
    const fetchImpl = (deps && deps.fetch) || globalThis.fetch;
    cycle(deps).then((pub) => alerts.maybeSend(pub, env, fetchImpl)).catch((err) => {
      const name = err && err.name ? err.name : 'Error';
      console.error('SaR Oracle cycle:', name);
    });
  };
  run();
  timer = setInterval(run, every);
  if (timer.unref) timer.unref();
  return stop;
}

function stop() {
  if (timer) clearInterval(timer);
  timer = null;
}

function resetForTests() {
  stop();
  latestFull = null;
  seq = 0;
  epochId = 0;
  history.clear();
  canary.resetForTests();
}

function running() {
  return timer != null;
}

module.exports = {
  MIN_INTERVAL_MS, TWAP_MS, LABEL, NOTES, readPolicy, publicView,
  cycle, snapshot, operatorSnapshot, start, stop, resetForTests, running,
};
