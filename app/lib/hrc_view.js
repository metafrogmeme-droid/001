'use strict';
/**
 * The admin dashboard: utilization and rate points.
 *
 * rate_shock_loss_24h is a dollar amount. dashboard() does not compute it.
 * adminPacket() does, and only when borrows were read and the oracle is 1.
 * A missing file is unreadable, not an empty book of zeros.
 */

const fs = require('node:fs');
const path = require('node:path');
const M = require('./hrc_model');

const BORROW_CAP_HUMAN = 500000000n;
const RAW_PER_TOKEN = 100000000n;
// Hyperliquid's per-user borrow cap, in USDC tokens. scenario_b withdraws one cap.
// The pool cap above is the cap-hit denominator.
const PER_USER_BORROW_CAP = 50000000n;
const SCENARIOS = [['scenario_a', 21500000n], ['scenario_b', PER_USER_BORROW_CAP]];
const SOURCES = new Set(['precompile', 'signed_feed', 'lower_of', 'none', 'info_api']);

function historyPath(env) {
  const src = env || process.env;
  const direct = src.HRC_HISTORY_PATH;
  if (typeof direct === 'string' && direct.trim()) return direct;
  const folder = src.HRC_STATE_DIR;
  if (typeof folder === 'string' && folder.trim()) return path.join(folder, 'history.json');
  return null;
}

function parseKeepingWideInts(text) {
  let out = '';
  let i = 0;
  let inStr = false;
  while (i < text.length) {
    const c = text[i];
    if (inStr) {
      out += c;
      if (c === '\\') {
        out += text[i + 1] || '';
        i += 2;
        continue;
      }
      if (c === '"') inStr = false;
      i += 1;
      continue;
    }
    if (c === '"') {
      inStr = true;
      out += c;
      i += 1;
      continue;
    }
    const m = /^-?\d{16,}(?![.\d])/.exec(text.slice(i));
    if (m) {
      out += '"' + m[0] + '"';
      i += m[0].length;
      continue;
    }
    out += c;
    i += 1;
  }
  return JSON.parse(out);
}

function loadReading(env) {
  const file = historyPath(env);
  if (!file || !fs.existsSync(file)) return { readable: false, reason: 'no_history' };
  let payload;
  try {
    payload = parseKeepingWideInts(fs.readFileSync(file, 'utf8'));
  } catch (err) {
    return { readable: false, reason: 'unreadable' };
  }
  if (!payload || typeof payload !== 'object' || Array.isArray(payload)) {
    return { readable: false, reason: 'unreadable' };
  }
  return payload;
}

function asDec(value) {
  if (M.isDec(value)) return value;
  if (value && typeof value === 'object' && typeof value.__decimal__ === 'string') {
    return M.parseDec(value.__decimal__);
  }
  if (typeof value === 'bigint') return M.fromInt(value);
  if (typeof value === 'number' && Number.isFinite(value)) return M.parseDec(String(value));
  if (typeof value === 'string') return M.parseDec(value);
  return null;
}

function asInt(value) {
  if (typeof value === 'bigint') return value;
  if (typeof value === 'number' && Number.isSafeInteger(value)) return BigInt(value);
  if (typeof value === 'string' && /^-?\d+$/.test(value)) return BigInt(value);
  const text = M.fmt(asDec(value));
  if (text && /^-?\d+$/.test(text)) return BigInt(text);
  return null;
}

function display(value) {
  const text = M.fmt(value);
  if (text === null) return null;
  const m = /^(-?)(\d+)\.(\d+)$/.exec(text);
  if (!m || m[3].length <= 12) return text;
  let keep = m[3].slice(0, 12);
  let whole = m[2];
  if (m[3][12] >= '5') {
    const digits = keep.split('');
    let i = digits.length - 1;
    while (i >= 0 && digits[i] === '9') {
      digits[i] = '0';
      i -= 1;
    }
    if (i >= 0) digits[i] = String(Number(digits[i]) + 1);
    else whole = (BigInt(whole) + 1n).toString();
    keep = digits.join('');
  }
  const frac = keep.replace(/0+$/, '');
  const shown = frac ? whole + '.' + frac : whole;
  return m[1] + shown;
}

function put(out, key, value) {
  const text = display(value);
  if (text !== null) out[key] = text;
}

function humanBorrows(reading) {
  const amount = asDec(reading.borrowed);
  if (!amount || M.cmp(amount, M.ZERO) < 0) return null;
  if (reading.unit === 'human') return amount;
  if (reading.unit === 'raw') return M.div(amount, M.fromInt(RAW_PER_TOKEN));
  return null;
}

function integrals(reading) {
  const supplied = asDec(reading.supplied);
  const borrowed = asDec(reading.borrowed);
  if (!supplied || !borrowed || M.cmp(supplied, M.ZERO) <= 0 || M.cmp(borrowed, M.ZERO) < 0) {
    return [null, null, null];
  }
  const s = asInt(reading.supplied);
  const b = asInt(reading.borrowed);
  if (reading.unit === 'human' && s !== null && b !== null) return [s, b, 'human'];
  if (reading.unit === 'raw' && s !== null && b !== null) return [s, b, 'raw'];
  if (reading.unit === 'human') {
    const rawS = M.mul(supplied, M.fromInt(RAW_PER_TOKEN));
    const rawB = M.mul(borrowed, M.fromInt(RAW_PER_TOKEN));
    return [asInt(M.fmt(rawS)), asInt(M.fmt(rawB)), 'raw'];
  }
  return [null, null, null];
}

function utilization(reading) {
  const supplied = asDec(reading.supplied);
  const borrowed = asDec(reading.borrowed);
  if (!supplied || !borrowed || M.cmp(supplied, M.ZERO) <= 0 || M.cmp(borrowed, M.ZERO) < 0) return null;
  if (reading.unit === 'human') return M.div(borrowed, supplied);
  if (reading.unit === 'raw') {
    const s = asInt(reading.supplied);
    const b = asInt(reading.borrowed);
    if (s === null || b === null) return null;
    return M.utilizationOf(s, b);
  }
  return null;
}

function rateWindow(raw) {
  if (!Array.isArray(raw)) return null;
  return raw.map((item) => (item === null ? null : asDec(item)));
}

function replayOne(supplied, borrowed, size) {
  if (supplied === null || borrowed === null) return { readable: false, reason: 'unreadable' };
  if (size < 0n || size >= supplied) return { readable: false, reason: 'withdrawal_exceeds_supply' };
  const util = M.utilizationOf(supplied - size, borrowed);
  const rate = M.borrowApy(util);
  if (!util || !rate) return { readable: false, reason: 'unreadable' };
  const shown = { readable: true };
  const u = display(util);
  const r = display(rate);
  if (u !== null) shown.utilization = u;
  if (r !== null) shown.borrow_apy = r;
  return shown;
}

function replays(supplied, borrowed, unit) {
  const out = {};
  for (const [name, humanSize] of SCENARIOS) {
    if (supplied === null || borrowed === null || (unit !== 'human' && unit !== 'raw')) {
      out[name] = { readable: false, reason: 'unreadable' };
      continue;
    }
    const size = unit === 'human' ? humanSize : humanSize * RAW_PER_TOKEN;
    out[name] = replayOne(supplied, borrowed, size);
  }
  return out;
}

function dashboard(reading) {
  if (!reading || typeof reading !== 'object' || reading.readable === false) {
    const reason = reading && typeof reading.reason === 'string' ? reading.reason : 'unreadable';
    return { readable: false, reason: reason };
  }
  const util = utilization(reading);
  const rate = M.borrowApy(util);
  const out = {
    readable: true,
    network: reading.network === 'mainnet' || reading.network === 'testnet' ? reading.network : null,
    synthetic: typeof reading.synthetic === 'boolean' ? reading.synthetic : null,
    source: SOURCES.has(reading.source) ? reading.source : 'none',
    kind: 'spread',
    ceiling: '0.40',
  };
  put(out, 'utilization', util);
  put(out, 'borrow_apy', rate);
  put(out, 'points_to_kink', util ? M.mul(M.sub(M.KINK, util), M.HUNDRED) : null);
  const cross = M.baselineCrossover();
  put(out, 'points_to_crossover', cross && util ? M.mul(M.sub(cross, util), M.HUNDRED) : null);
  const human = humanBorrows(reading);
  if (human) put(out, 'borrow_cap_ratio', M.div(human, M.fromInt(BORROW_CAP_HUMAN)));
  const [suppliedI, borrowedI, unit] = integrals(reading);
  let hit = null;
  if (borrowedI !== null && unit === 'raw') {
    hit = borrowedI * 100n >= BORROW_CAP_HUMAN * RAW_PER_TOKEN * 98n;
  } else if (borrowedI !== null && unit === 'human') {
    hit = borrowedI * 100n >= BORROW_CAP_HUMAN * 98n;
  }
  if (hit !== null) out.cap_hit = hit;
  const rates = rateWindow(reading.rates);
  const mean = rates ? M.trailingMean(rates) : null;
  put(out, 'trailing_mean', mean);
  const strike = asDec(reading.strike);
  if (mean && strike) {
    let capped = mean;
    if (M.cmp(mean, M.SPREAD_CEIL) > 0) capped = M.SPREAD_CEIL;
    let gap = M.sub(capped, strike);
    if (M.cmp(gap, M.ZERO) < 0) gap = M.ZERO;
    put(out, 'premium', M.div(M.mul(gap, M.DAY), M.HOURS));
  }
  const [s, b, u] = [suppliedI, borrowedI, unit];
  out.replays = replays(s, b, u);
  return out;
}

function adminPacket(reading) {
  const view = dashboard(reading);
  const borrows = reading && typeof reading === 'object' ? humanBorrows(reading) : null;
  const oracle = reading && typeof reading === 'object' ? asDec(reading.oracle) : null;
  let shock = null;
  if (borrows && oracle && M.cmp(oracle, M.fromInt(1n)) === 0 && M.cmp(borrows, M.ZERO) >= 0) {
    shock = M.div(M.mul(M.mul(borrows, M.SHOCK_RATE), M.DAY), M.HOURS);
  }
  view.rate_shock_loss_24h = M.fmt(shock);
  return view;
}

module.exports = {
  historyPath, loadReading, dashboard, adminPacket, parseKeepingWideInts,
};
