'use strict';
/**
 * Canonical copy. app/lib/sar is a byte copy the site loads.
 * Do not edit one side.
 *
 * Hyperliquid info (POST, weight 2 for l2Book and clearinghouseState,
 * weight 20 for every other info request including metaAndAssetCtxs) and
 * Jupiter perps (GET only). Hyperliquid's published aggregate cap is
 * 1200 weight per minute per IP. This cycle stays under its own cap of
 * 80, which is the whole of one pass: one meta, one book per coin, and
 * at most five clearinghouse reads.
 *
 * A 429 stops the Hyperliquid walk. It is not retried inside the cycle.
 * Calls are at least 250ms apart. Nothing here places an order.
 *
 * Price fields are read only far enough to compute a ratio, then dropped.
 * They are not returned.
 */

const { HL_INFO, JUP_BASE, HL_COINS, JUP_MINTS, PUBLIC_BOOK_CAP } = require('./chains');

const WEIGHT = Object.freeze({
  metaAndAssetCtxs: 20,
  l2Book: 2,
  clearinghouseState: 2,
});
const CYCLE_CAP = 80;
const GAP_MS = 250;
const ADDR_RE = /^0x[0-9a-fA-F]{40}$/;
const SOL_RE = /^[1-9A-HJ-NP-Za-km-z]{32,44}$/;

function splitList(raw, cap, ok) {
  if (raw == null || String(raw).trim() === '') {
    return { values: [], skipped: 0, unset: true };
  }
  const parts = String(raw).split(',').map((s) => s.trim()).filter((s) => s.length > 0);
  const values = [];
  let skipped = 0;
  for (const part of parts) {
    if (values.length >= cap || !ok(part)) {
      skipped += 1;
      continue;
    }
    values.push(part);
  }
  return { values, skipped, unset: false };
}

function addressLists(env) {
  const src = env || process.env;
  return {
    hl: splitList(src.SAR_ORACLE_HL_ADDRESSES, 5, (s) => ADDR_RE.test(s)),
    jup: splitList(src.SAR_ORACLE_JUP_WALLETS, 5, (s) => SOL_RE.test(s)),
  };
}

async function readBody(res) {
  try {
    return await res.json();
  } catch (err) {
    return { _parse: err && err.name ? err.name : 'Error' };
  }
}

function infoTarget(env) {
  const raw = env && env.SAR_ORACLE_HL_NODE_URL;
  if (raw == null || String(raw).trim() === '') return { url: HL_INFO, node: false, reason: null };
  const url = String(raw).trim().replace(/\/$/, '');
  if (!/^https?:\/\/[^ ]+$/.test(url)) return { url: null, node: true, reason: 'node_unreadable' };
  return { url, node: true, reason: null };
}

async function hlPost(fetchImpl, body, url) {
  const res = await fetchImpl(url || HL_INFO, {
    method: 'POST',
    headers: { 'content-type': 'application/json', accept: 'application/json' },
    body: JSON.stringify(body),
  });
  if (res.status === 429) return { status: 429, body: null };
  if (!res.ok) return { status: res.status, body: null };
  const parsed = await readBody(res);
  if (parsed && parsed._parse) return { status: res.status, body: null, parse: parsed._parse };
  return { status: res.status, body: parsed };
}

async function jupGet(fetchImpl, pathAndQuery) {
  const res = await fetchImpl(`${JUP_BASE}${pathAndQuery}`, {
    method: 'GET',
    headers: { accept: 'application/json' },
  });
  if (res.status === 429) return { status: 429, body: null };
  if (!res.ok) return { status: res.status, body: null };
  const parsed = await readBody(res);
  if (parsed && parsed._parse) return { status: res.status, body: null, parse: parsed._parse };
  return { status: res.status, body: parsed };
}

function level(raw) {
  if (!raw || typeof raw !== 'object') return { px: null, sz: null, n: null };
  return {
    px: typeof raw.px === 'string' ? raw.px : null,
    sz: typeof raw.sz === 'string' ? raw.sz : null,
    n: (typeof raw.n === 'string' || typeof raw.n === 'number') ? raw.n : null,
  };
}

function parseBook(body) {
  if (!body || !Array.isArray(body.levels) || body.levels.length < 2) return null;
  if (!Array.isArray(body.levels[0]) || !Array.isArray(body.levels[1])) return null;
  const time = Number.isFinite(body.time) ? Math.trunc(body.time) : null;
  return {
    bids: body.levels[0].map(level),
    asks: body.levels[1].map(level),
    time,
  };
}

/** open interest and the reference, and nothing else from the context. */
function parseMeta(body) {
  if (!Array.isArray(body) || body.length < 2) return null;
  const meta = body[0];
  const ctxs = body[1];
  if (!meta || !Array.isArray(meta.universe) || !Array.isArray(ctxs)) return null;
  const out = {};
  const want = new Set(HL_COINS);
  for (let i = 0; i < meta.universe.length; i += 1) {
    const name = meta.universe[i] && meta.universe[i].name;
    if (!want.has(name)) continue;
    const ctx = ctxs[i];
    if (!ctx || typeof ctx !== 'object') {
      out[name] = { oi: null, ref: null };
      continue;
    }
    out[name] = {
      oi: typeof ctx.openInterest === 'string' ? ctx.openInterest : null,
      ref: typeof ctx.markPx === 'string' ? ctx.markPx : null,
      index: i,
    };
  }
  return out;
}

function parseClearinghouse(body) {
  if (!body || !Array.isArray(body.assetPositions)) return null;
  const rows = [];
  for (const row of body.assetPositions) {
    const p = row && row.position;
    if (!p || typeof p !== 'object' || typeof p.coin !== 'string' || typeof p.szi !== 'string') {
      rows.push({ unreadable: true, coin: p && typeof p.coin === 'string' ? p.coin : null });
      continue;
    }
    rows.push({
      coin: p.coin,
      size: p.szi,
      liq: typeof p.liquidationPx === 'string' ? p.liquidationPx : null,
    });
  }
  return rows;
}

function parseJupPositions(body) {
  if (!body || !Array.isArray(body.dataList)) return null;
  return body.dataList.map((p) => {
    if (!p || typeof p !== 'object' || typeof p.asset !== 'string' || typeof p.sizeTokenAmount !== 'string') {
      return { unreadable: true, asset: p && typeof p.asset === 'string' ? p.asset : null };
    }
    return {
      asset: p.asset,
      size: p.sizeTokenAmount,
      liq: typeof p.liquidationPriceUsd === 'string' ? p.liquidationPriceUsd : null,
      ref: typeof p.markPriceUsd === 'string' ? p.markPriceUsd : null,
    };
  });
}

/**
 * One Hyperliquid pass. `sleep` runs between calls, including after a
 * refusal, so a 429 is not followed at once by the next request.
 * Returned books and contexts carry no price field names onward except
 * the internal `ref` the band math consumes inside this process.
 */
async function readHyperliquid({ fetchImpl, sleep, addresses }) {
  const out = {
    meta: null,
    metaReason: null,
    books: {},
    positions: null,
    positionReason: null,
    weight: 0,
  };
  let halted = false;
  const spend = async (weight, run) => {
    if (halted) return { status: 429, body: null, skipped: true };
    if (out.weight + weight > CYCLE_CAP) {
      halted = true;
      return { status: 0, body: null, skipped: true, reason: 'weight_cap' };
    }
    out.weight += weight;
    if (out.weight > weight) await sleep(GAP_MS);
    try {
      const res = await run();
      if (res.status === 429) halted = true;
      return res;
    } catch (err) {
      return { status: 0, body: null, reason: err && err.name ? err.name : 'Error' };
    }
  };

  const metaRes = await spend(WEIGHT.metaAndAssetCtxs, () => hlPost(fetchImpl, { type: 'metaAndAssetCtxs' }));
  if (metaRes.status === 429 || metaRes.skipped) {
    out.metaReason = metaRes.status === 429 ? 'rate_limited' : (metaRes.reason || 'weight_cap');
  } else if (metaRes.status !== 200) {
    out.metaReason = 'source_unreadable';
  } else {
    out.meta = parseMeta(metaRes.body);
    if (!out.meta) out.metaReason = 'source_unreadable';
  }

  for (const coin of HL_COINS) {
    const res = await spend(WEIGHT.l2Book, () => hlPost(fetchImpl, { type: 'l2Book', coin }));
    if (res.status === 429 || res.skipped) {
      out.books[coin] = { book: null, reason: res.status === 429 ? 'rate_limited' : (res.reason || 'weight_cap') };
      continue;
    }
    if (res.status !== 200) {
      out.books[coin] = { book: null, reason: 'source_unreadable' };
      continue;
    }
    const book = parseBook(res.body);
    if (!book) {
      out.books[coin] = { book: null, reason: 'source_unreadable', truncated: null };
      continue;
    }
    const truncated = book.bids.length >= PUBLIC_BOOK_CAP || book.asks.length >= PUBLIC_BOOK_CAP;
    out.books[coin] = { book, reason: null, truncated, source: 'public_api' };
  }

  if (!addresses || addresses.unset) {
    out.positionReason = 'no_position_universe';
    return out;
  }
  if (addresses.values.length === 0) {
    out.positionReason = 'addresses_unreadable';
    return out;
  }
  const rows = [];
  let failed = false;
  let failReason = null;
  for (const user of addresses.values) {
    const res = await spend(WEIGHT.clearinghouseState, () => hlPost(fetchImpl, { type: 'clearinghouseState', user }));
    if (res.status === 429 || res.skipped) {
      failed = true;
      failReason = res.status === 429 ? 'rate_limited' : (res.reason || 'weight_cap');
      break;
    }
    if (res.status !== 200) {
      failed = true;
      failReason = 'source_unreadable';
      break;
    }
    const parsed = parseClearinghouse(res.body);
    if (!parsed) {
      failed = true;
      failReason = 'source_unreadable';
      break;
    }
    rows.push(...parsed);
  }
  if (failed) {
    // A partial user set is not the universe. Drop what was read.
    out.positions = null;
    out.positionReason = failReason;
    return out;
  }
  out.positions = rows;
  out.positionReason = null;
  return out;
}

/**
 * Jupiter: one pool-info GET per mint, to see that the market answered,
 * and one positions GET per wallet. The pool body is discarded. There is
 * no order book on this venue. No request uses POST.
 */
async function readJupiter({ fetchImpl, wallets }) {
  const pools = {};
  for (const [market, mint] of Object.entries(JUP_MINTS)) {
    try {
      const res = await jupGet(fetchImpl, `/pool-info?mint=${encodeURIComponent(mint)}`);
      if (res.status === 429) pools[market] = { answered: false, reason: 'rate_limited' };
      else if (res.status !== 200 || res.body == null || typeof res.body !== 'object') {
        pools[market] = { answered: false, reason: 'source_unreadable' };
      } else {
        pools[market] = { answered: true, reason: null };
      }
    } catch (err) {
      pools[market] = { answered: false, reason: err && err.name ? err.name : 'Error' };
    }
  }
  const positions = { rows: null, reason: null };
  if (!wallets || wallets.unset) {
    positions.reason = 'no_position_universe';
    return { pools, positions };
  }
  if (wallets.values.length === 0) {
    positions.reason = 'addresses_unreadable';
    return { pools, positions };
  }
  const rows = [];
  for (const wallet of wallets.values) {
    try {
      const q = `/positions?walletAddress=${encodeURIComponent(wallet)}`;
      const res = await jupGet(fetchImpl, q);
      if (res.status === 429) {
        return { pools, positions: { rows: null, reason: 'rate_limited' } };
      }
      if (res.status !== 200) {
        return { pools, positions: { rows: null, reason: 'source_unreadable' } };
      }
      const parsed = parseJupPositions(res.body);
      if (!parsed) return { pools, positions: { rows: null, reason: 'source_unreadable' } };
      rows.push(...parsed);
    } catch (err) {
      return { pools, positions: { rows: null, reason: err && err.name ? err.name : 'Error' } };
    }
  }
  positions.rows = rows;
  return { pools, positions };
}

/**
 * l4 shape this build reads. Anything else is unreadable, not a guess.
 * A level is {px, sz, user, t} with t a millisecond timestamp, or
 * {px, sz, users: [{user, sz, t}]}. A public level (px, sz, n) is not
 * an account book.
 */
function parseL4(body) {
  if (!body || typeof body !== 'object') return { book: null, reason: 'source_unreadable' };
  let bids = null;
  let asks = null;
  if (Array.isArray(body.levels) && body.levels.length >= 2) {
    bids = body.levels[0];
    asks = body.levels[1];
  } else if (Array.isArray(body.bids) && Array.isArray(body.asks)) {
    bids = body.bids;
    asks = body.asks;
  } else {
    return { book: null, reason: 'source_unreadable' };
  }
  const side = (levels) => {
    if (!Array.isArray(levels)) return { rows: null, reason: 'source_unreadable' };
    const rows = [];
    for (const lvl of levels) {
      if (!lvl || typeof lvl !== 'object') return { rows: null, reason: 'bad_level' };
      if (Array.isArray(lvl.users)) {
        for (const u of lvl.users) {
          if (!u || typeof u.user !== 'string' || typeof u.sz !== 'string') {
            return { rows: null, reason: 'no_account_book' };
          }
          rows.push({
            px: typeof lvl.px === 'string' ? lvl.px : null,
            sz: u.sz,
            user: u.user,
            sinceMs: typeof u.t === 'number' ? u.t : null,
            restedMs: typeof u.restedMs === 'number' ? u.restedMs : null,
          });
        }
        continue;
      }
      if (typeof lvl.user !== 'string') return { rows: null, reason: 'no_account_book' };
      rows.push({
        px: typeof lvl.px === 'string' ? lvl.px : null,
        sz: typeof lvl.sz === 'string' ? lvl.sz : null,
        user: lvl.user,
        sinceMs: typeof lvl.t === 'number' ? lvl.t : null,
        restedMs: typeof lvl.restedMs === 'number' ? lvl.restedMs : null,
      });
    }
    return { rows, reason: null };
  };
  const b = side(bids);
  const a = side(asks);
  if (b.reason || a.reason) return { book: null, reason: b.reason || a.reason };
  const time = Number.isFinite(body.time) ? Math.trunc(body.time) : null;
  return { book: { bids: b.rows, asks: a.rows, time, accounts: true }, reason: null };
}

/**
 * Local `liquidatable` rows. position value is kept only inside the
 * density sum; this parser does not rename it onto a public field.
 */
function parseLiquidatable(body) {
  const list = Array.isArray(body) ? body : (body && Array.isArray(body.liquidatable) ? body.liquidatable : null);
  if (!list) return { byCoin: null, reason: 'source_unreadable' };
  const byCoin = {};
  for (const row of list) {
    if (!row || typeof row.coin !== 'string' || !Array.isArray(row.positions)) {
      return { byCoin: null, reason: 'source_unreadable' };
    }
    const positions = [];
    for (const p of row.positions) {
      if (!p || typeof p !== 'object') return { byCoin: null, reason: 'partial_book' };
      const value = typeof p.positionValue === 'string' ? p.positionValue : null;
      const liq = typeof p.liquidationPx === 'string' ? p.liquidationPx : null;
      if (value == null || liq == null) return { byCoin: null, reason: 'partial_book' };
      positions.push({ value, liq, ref: typeof row.markPx === 'string' ? row.markPx : null });
    }
    byCoin[row.coin] = positions;
  }
  return { byCoin, reason: null };
}

/** `time` is the node's L1 clock in milliseconds, or the status is unreadable. */
function parseExchangeStatus(body, nowMs) {
  if (!body || typeof body.time !== 'number' || !Number.isFinite(body.time)) {
    return { lagMs: null, lagged: false, reason: 'node_lag_unreadable' };
  }
  if (typeof nowMs !== 'number') return { lagMs: null, lagged: false, reason: 'node_lag_unreadable' };
  const lagMs = nowMs - body.time;
  const abs = lagMs < 0 ? -lagMs : lagMs;
  const l1 = Number.isSafeInteger(body.l1Block) && body.l1Block >= 0 ? body.l1Block : null;
  return {
    lagMs: abs,
    lagged: abs > 5000,
    reason: null,
    l1Block: l1,
    l1_reason: l1 == null ? 'l1_block_unreadable' : null,
  };
}

/**
 * The local node, when SAR_ORACLE_HL_NODE_URL is set. A failure here does
 * not fall through to the public book: the public book cannot supply
 * accounts. Weight caps are the public IP rule and are not applied.
 */
async function readNode(deps) {
  const target = infoTarget(deps.env || process.env);
  if (!target.node) return { configured: false, reason: null };
  if (!target.url) return { configured: true, reason: target.reason, books: {}, liquidatable: null, status: null };
  const fetchImpl = deps.fetchImpl;
  const sleep = deps.sleep || (() => Promise.resolve());
  const nowMs = deps.nowMs;
  const out = { configured: true, reason: null, books: {}, liquidatable: null, status: null, source: 'node' };
  let first = true;
  const call = async (body) => {
    if (!first) await sleep(GAP_MS);
    first = false;
    try {
      return await hlPost(fetchImpl, body, target.url);
    } catch (err) {
      return { status: 0, body: null, reason: err && err.name ? err.name : 'Error' };
    }
  };
  for (const coin of HL_COINS) {
    const res = await call({ type: 'l4Snapshots', coin, includeUsers: true });
    if (res.status !== 200) {
      out.books[coin] = { book: null, reason: res.status === 429 ? 'rate_limited' : 'source_unreadable' };
      continue;
    }
    const parsed = parseL4(res.body);
    out.books[coin] = parsed.book
      ? { book: parsed.book, reason: null, truncated: false, source: 'node' }
      : { book: null, reason: parsed.reason, truncated: null, source: 'node' };
  }
  const liq = await call({ type: 'liquidatable' });
  if (liq.status !== 200) out.liquidatable = { byCoin: null, reason: 'source_unreadable' };
  else out.liquidatable = parseLiquidatable(liq.body);
  const status = await call({ type: 'exchangeStatus' });
  out.status = status.status === 200 ? parseExchangeStatus(status.body, nowMs) : { lagMs: null, lagged: false, reason: 'node_lag_unreadable' };
  return out;
}

module.exports = {
  WEIGHT, CYCLE_CAP, GAP_MS, addressLists, infoTarget, readHyperliquid, readJupiter, readNode,
  parseBook, parseMeta, parseClearinghouse, parseJupPositions, parseL4, parseLiquidatable, parseExchangeStatus,
};
