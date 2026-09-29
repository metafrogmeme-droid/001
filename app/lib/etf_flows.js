'use strict';
/**
 * US spot crypto ETF flows: a READ-ONLY reading of public market facts.
 *
 * The daily net creations and redemptions of the US spot Bitcoin, Ether and
 * Solana ETFs, from SoSoValue's keyless open API. ONE reading, rendered by
 * both surfaces: the Markets panel reads `GET /api/market/etf-flows`, and
 * Telegram's `/etf` fetches the card route, which hands the bot this payload
 * to draw as a picture. There is no Python copy of any figure here.
 *
 * What is measured and what is derived, stated because a derived figure
 * printed beside a measured one reads as one:
 *   - MEASURED, as the source states them: each trading day's net flow in
 *     dollars per asset, each fund's net flow on the latest reported day, and
 *     each asset's net assets and coin holdings on that day.
 *   - DERIVED here: the week's net flow (a sum of the days that were read,
 *     with the count beside it), the coin amount of that flow (the dollar
 *     flow divided by the funds' own net-asset-implied price on the latest
 *     day, so it is an estimate, marked `approx`), and, for Bitcoin only, how
 *     many days of new issuance that amount equals (450 BTC a day is a
 *     protocol constant, not a reading).
 *
 * Nothing is read as zero. A day the source listed with no readable flow is
 * counted as unread and left out of the sum; a fund whose flow is dated
 * another day is unread for this day; an asset the source did not answer for
 * is named as unread. A reading with no asset read at all THROWS, so the
 * panel paints its failure state rather than "no flows".
 *
 * Market data only: nothing here trades or sizes anything.
 */

const { esc } = require('./esc');

const API = 'https://api.sosovalue.xyz/openapi/v2/etf';
const SOURCE = Object.freeze({
  name: 'SoSoValue',
  url: 'https://sosovalue.com/assets/etf/us-btc-spot',
});

/** The window the weekly figure covers: calendar days ending on the latest
 *  reported trading day, inclusive. Weekends list no row, so a week is
 *  usually five trading days and the payload says how many were read. */
const WINDOW_DAYS = 7;

/** Bitcoin issuance per day: 3.125 BTC a block since the April 2024 halving,
 *  about 144 blocks a day. A constant, used only for the derived takeaway. */
const BTC_ISSUANCE_PER_DAY = 450;

const ASSETS = Object.freeze([
  { key: 'btc', type: 'us-btc-spot', symbol: 'BTC', name: 'Bitcoin' },
  { key: 'eth', type: 'us-eth-spot', symbol: 'ETH', name: 'Ether' },
  { key: 'sol', type: 'us-sol-spot', symbol: 'SOL', name: 'Solana' },
]);

const DATE_RE = /^\d{4}-\d{2}-\d{2}$/;

/** A finite number, or null. `''`, a bool and junk are not zero. */
function num(v) {
  if (v == null || typeof v === 'boolean') return null;
  if (typeof v === 'string' && !v.trim()) return null;
  const n = Number(v);
  return Number.isFinite(n) ? n : null;
}

/** A `YYYY-MM-DD` date the calendar accepts, or null. */
function dateOf(s) {
  if (typeof s !== 'string' || !DATE_RE.test(s)) return null;
  const t = Date.parse(`${s}T00:00:00Z`);
  return Number.isFinite(t) && new Date(t).toISOString().slice(0, 10) === s ? s : null;
}

function dayIndex(d) { return Date.parse(`${d}T00:00:00Z`) / 86400000; }

/**
 * One metrics field: `{value, lastUpdateDate, status}`. A status other than
 * the source's `'1'` is a value it did not stand behind, so it is unread.
 */
function field(f) {
  if (!f || typeof f !== 'object') return { value: null, date: null };
  const date = dateOf(f.lastUpdateDate);
  if (f.status != null && String(f.status) !== '1') return { value: null, date };
  return { value: num(f.value), date };
}

/** A sum over the rows that were read, with the count beside it. */
function sumRead(days) {
  const read = days.filter((d) => d.net_flow_usd != null);
  return {
    net_flow_usd: read.length ? read.reduce((a, d) => a + d.net_flow_usd, 0) : null,
    days_read: read.length,
    days_listed: days.length,
  };
}

/**
 * One asset's reading from its daily history and its latest-day metrics.
 * Either input may be null (the source did not answer for it); the history
 * decides whether the asset is read at all, and the metrics only add the
 * funds and the coin estimate.
 */
function assetReading(asset, hist, metrics) {
  const base = { key: asset.key, symbol: asset.symbol, name: asset.name };
  if (!Array.isArray(hist)) return { ...base, read: false, reason: 'unavailable' };
  const byDate = new Map();
  for (const r of hist) {
    if (!r || typeof r !== 'object') continue;
    const d = dateOf(r.date);
    if (!d || byDate.has(d)) continue;
    byDate.set(d, num(r.totalNetInflow));
  }
  if (!byDate.size) return { ...base, read: false, reason: 'no_rows' };
  const dates = [...byDate.keys()].sort();
  const latest = dates[dates.length - 1];
  const last = dayIndex(latest);
  const span = (lo, hi) => dates
    .filter((d) => { const n = dayIndex(d); return n > last - hi && n <= last - lo; })
    .map((d) => ({ date: d, net_flow_usd: byDate.get(d) }));
  const days = span(0, WINDOW_DAYS);
  const week = sumRead(days);

  const m = metrics && typeof metrics === 'object' ? metrics : null;
  const assets = m ? field(m.totalNetAssets) : { value: null, date: null };
  const holdings = m ? field(m.totalTokenHoldings) : { value: null, date: null };
  // The funds' own price for one coin on the latest day: net assets over
  // coins held, both stated for the SAME day. A different day, a zero or an
  // unread field leaves no price, and so no coin estimate.
  const price = assets.value > 0 && holdings.value > 0 && assets.date && assets.date === holdings.date
    ? assets.value / holdings.value : null;
  const coins = price != null && week.net_flow_usd != null ? week.net_flow_usd / price : null;

  const fundsDate = m ? field(m.dailyNetInflow).date : null;
  const funds = m && Array.isArray(m.list) ? m.list.filter((f) => f && typeof f === 'object').map((f) => {
    const flow = field(f.dailyNetInflow);
    const onDay = flow.value != null && fundsDate != null && flow.date === fundsDate;
    return {
      ticker: String(f.ticker || '').trim().slice(0, 12) || null,
      provider: String(f.institute || '').trim().slice(0, 40) || null,
      net_flow_usd: onDay ? flow.value : null,
    };
  }) : null;

  return {
    ...base,
    read: true,
    latest_date: latest,
    days,
    week: { ...week, coins, approx: coins != null },
    latest_day_flow_usd: byDate.get(latest),
    implied_price: price,
    implied_price_date: price != null ? assets.date : null,
    funds_date: funds ? fundsDate : null,
    funds,
    providers: funds ? providerRows(funds) : null,
  };
}

/**
 * The funds grouped by provider (Grayscale runs two). A provider's figure is
 * the sum of its funds that were read; `funds_read` beside `funds` says when
 * that sum is partial. Ranked by the size of the flow either way.
 */
function providerRows(funds) {
  const groups = new Map();
  for (const f of funds) {
    const name = f.provider || f.ticker || 'unnamed fund';
    const g = groups.get(name) || { provider: name, tickers: [], net_flow_usd: null, funds: 0, funds_read: 0 };
    g.funds += 1;
    if (f.ticker) g.tickers.push(f.ticker);
    if (f.net_flow_usd != null) {
      g.funds_read += 1;
      // The first fund read starts the sum; a provider none of whose funds
      // were read keeps null, never a measured zero.
      g.net_flow_usd = g.net_flow_usd == null ? f.net_flow_usd : g.net_flow_usd + f.net_flow_usd;
    }
    groups.set(name, g);
  }
  return [...groups.values()].sort((a, b) => {
    if (a.net_flow_usd == null) return b.net_flow_usd == null ? 0 : 1;
    if (b.net_flow_usd == null) return -1;
    return Math.abs(b.net_flow_usd) - Math.abs(a.net_flow_usd);
  });
}

/**
 * The whole reading. `raw` is `{btc: {hist, metrics}, ...}` with null for a
 * part the source did not answer. The headline total sums the assets whose
 * week ends on the same latest day; an asset reported on an earlier day is
 * named rather than folded into a total for a different week.
 */
function buildEtfFlows(raw, nowMs) {
  const src = raw && typeof raw === 'object' ? raw : {};
  const assets = ASSETS.map((a) => {
    const part = src[a.key] && typeof src[a.key] === 'object' ? src[a.key] : {};
    return assetReading(a, part.hist ?? null, part.metrics ?? null);
  });
  const read = assets.filter((a) => a.read);
  const asOf = read.length ? read.map((a) => a.latest_date).sort().slice(-1)[0] : null;
  const inTotal = read.filter((a) => a.latest_date === asOf && a.week.net_flow_usd != null);
  const total = inTotal.length ? inTotal.reduce((s, a) => s + a.week.net_flow_usd, 0) : null;
  const btc = read.find((a) => a.key === 'btc');
  let takeaway = null;
  if (btc && btc.week.coins != null && btc.week.coins !== 0) {
    takeaway = {
      symbol: 'BTC',
      coins: btc.week.coins,
      issuance_per_day: BTC_ISSUANCE_PER_DAY,
      days_of_issuance: Math.abs(btc.week.coins) / BTC_ISSUANCE_PER_DAY,
      direction: btc.week.coins > 0 ? 'inflow' : 'outflow',
    };
  }
  return {
    source: SOURCE,
    window_days: WINDOW_DAYS,
    as_of: asOf,
    window_start: asOf ? new Date((dayIndex(asOf) - (WINDOW_DAYS - 1)) * 86400000).toISOString().slice(0, 10) : null,
    assets,
    total: {
      net_flow_usd: total,
      assets_in_total: inTotal.map((a) => a.symbol),
      assets_read: read.length,
      assets_tracked: ASSETS.length,
    },
    takeaway,
    fetched_at: Number.isFinite(nowMs) ? new Date(nowMs).toISOString() : null,
  };
}

// ── Fetch (injectable) ───────────────────────────────────────────────────────

async function sosoPostHttp(endpoint, type) {
  const ctl = new AbortController();
  const timer = setTimeout(() => ctl.abort(), 10000);
  try {
    const r = await fetch(`${API}/${endpoint}`, {
      method: 'POST',
      signal: ctl.signal,
      headers: { 'content-type': 'application/json', accept: 'application/json' },
      body: JSON.stringify({ type }),
    });
    if (!r.ok) return null;
    const d = await r.json();
    // `code: 0` is the source's success; anything else is a refusal whatever
    // `data` holds.
    return d && d.code === 0 ? d.data : null;
  } catch (e) {
    return null;
  } finally {
    clearTimeout(timer);
  }
}

let sosoPost = sosoPostHttp;
function setSosoFetcher(fn) { sosoPost = fn || sosoPostHttp; _cache = null; }

const CACHE_MS = 10 * 60_000;
let _cache = null;                  // { at, flows }

/** The reading, cached ten minutes (the source is daily). Throws when no
 *  asset could be read: that is a failed read, never "no flows". */
async function getEtfFlows() {
  if (_cache && Date.now() - _cache.at < CACHE_MS) return _cache.flows;
  const raw = {};
  // Sequential on purpose: six small calls to one public API, no burst.
  for (const a of ASSETS) {
    let hist = null;
    let metrics = null;
    try { hist = await sosoPost('historicalInflowChart', a.type); } catch (e) { hist = null; }
    try { metrics = await sosoPost('currentEtfDataMetrics', a.type); } catch (e) { metrics = null; }
    raw[a.key] = { hist, metrics };
  }
  const flows = buildEtfFlows(raw, Date.now());
  if (!flows.total.assets_read) throw new Error('ETF flows unavailable');
  _cache = { at: Date.now(), flows };
  return flows;
}

// ── Rendering (shared by the chat card) ──────────────────────────────────────

/** A signed dollar figure: `+$310.2M`, `-$1.3B`, or an em dash. */
function usdSigned(v) {
  const n = num(v);
  if (n == null) return '—';
  const a = Math.abs(n);
  const sign = n > 0 ? '+' : n < 0 ? '-' : '';
  if (a >= 1e9) return `${sign}$${(a / 1e9).toFixed(2)}B`;
  if (a >= 1e6) return `${sign}$${(a / 1e6).toFixed(1)}M`;
  if (a >= 1e3) return `${sign}$${(a / 1e3).toFixed(1)}K`;
  return `${sign}$${a.toFixed(0)}`;
}

/** A signed coin amount, marked as the estimate it is: `≈ +3,710 BTC`. */
function coinsSigned(v, symbol) {
  const n = num(v);
  if (n == null) return null;
  const a = Math.abs(n);
  const digits = a >= 100 ? 0 : a >= 1 ? 1 : 3;
  return `≈ ${n > 0 ? '+' : n < 0 ? '-' : ''}${a.toLocaleString('en-US', { maximumFractionDigits: digits })} ${symbol}`;
}

function shortDate(d) {
  if (!dateOf(d)) return '—';
  return new Date(`${d}T00:00:00Z`).toLocaleDateString('en-US', { month: 'short', day: 'numeric', timeZone: 'UTC' });
}

/** The window as words: `Sep 22 – Sep 28`. */
function windowWords(flows) {
  return flows.as_of ? `${shortDate(flows.window_start)} – ${shortDate(flows.as_of)}` : '—';
}

/** Why an asset was not read, in words. */
const UNREAD_WORDS = Object.freeze({
  unavailable: 'the source did not answer',
  no_rows: 'the source listed no days',
});

/** The provider line for one asset's latest day, or null with no funds. */
function providerLine(a) {
  if (!a.providers || !a.providers.length) return null;
  const moved = a.providers.filter((p) => p.net_flow_usd != null && p.net_flow_usd !== 0);
  const flat = a.providers.filter((p) => p.net_flow_usd === 0 && p.funds_read === p.funds).length;
  const unread = a.providers.filter((p) => p.funds_read < p.funds).length;
  const top = moved.slice(0, 4).map((p) => `${esc(p.provider)} ${usdSigned(p.net_flow_usd)}`);
  const parts = [top.length ? top.join(' · ') : 'no provider reported a flow'];
  if (flat) parts.push(`${flat} of ${a.providers.length} providers reported no flow`);
  if (unread) parts.push(`${unread} not read for this day`);
  return `${esc(a.symbol)} funds on ${shortDate(a.funds_date)}: ${parts.join(' · ')}`;
}

/** The takeaway sentence, labelled as derived, or null. */
function takeawayLine(flows) {
  const t = flows.takeaway;
  if (!t) return null;
  const days = t.days_of_issuance >= 10 ? t.days_of_issuance.toFixed(0) : t.days_of_issuance.toFixed(1);
  const verb = t.direction === 'inflow' ? 'Net buying' : 'Net selling';
  return `${verb} of ${coinsSigned(t.coins, t.symbol).replace(/^≈ [+-]/, '≈ ')} is about ${days} days of `
    + `newly mined bitcoin (derived: ${t.issuance_per_day} BTC a day since the 2024 halving).`;
}

/**
 * The ETF flows card — the reading both surfaces render. It carries the
 * payload as `data` too, so the Telegram command can draw the picture from
 * the same figures rather than a second reading of the source.
 */
async function etfChatCard() {
  const flows = await getEtfFlows();
  const lines = [];
  lines.push(`📊 <b>US spot crypto ETF flows</b> — ${windowWords(flows)}`);
  const inTotal = flows.total.assets_in_total;
  lines.push(`Net: <b>${usdSigned(flows.total.net_flow_usd)}</b>`
    + (inTotal.length ? ` across ${inTotal.join(', ')}` : ''));
  lines.push('');
  for (const a of flows.assets) {
    if (!a.read) {
      lines.push(`• ${esc(a.symbol)}: not read (${UNREAD_WORDS[a.reason] || 'unreadable'})`);
      continue;
    }
    const coins = coinsSigned(a.week.coins, a.symbol);
    const partial = a.week.days_read < a.week.days_listed
      ? ` (${a.week.days_read} of ${a.week.days_listed} days read)` : '';
    const stale = a.latest_date !== flows.as_of ? ` — last reported ${shortDate(a.latest_date)}, not in the total` : '';
    lines.push(`• ${esc(a.symbol)} <b>${usdSigned(a.week.net_flow_usd)}</b>`
      + (coins ? ` (${coins})` : '') + `${partial} · last day ${usdSigned(a.latest_day_flow_usd)}${stale}`);
  }
  const btc = flows.assets.find((a) => a.key === 'btc' && a.read);
  const prov = btc ? providerLine(btc) : null;
  if (prov) { lines.push(''); lines.push(prov); }
  const take = takeawayLine(flows);
  if (take) { lines.push(''); lines.push(take); }
  lines.push('');
  lines.push(`<i>Source: ${esc(flows.source.name)}, daily US spot ETF net creations and redemptions. `
    + 'Coin amounts are estimates at the funds\' net-asset-implied price on the latest day. '
    + 'Market data only — nothing here trades.</i>');
  return { reply_html: lines.join('<br>'), intent: 'etf_flows', data: flows };
}

module.exports = {
  ASSETS, SOURCE, WINDOW_DAYS, BTC_ISSUANCE_PER_DAY,
  num, dateOf, field, assetReading, providerRows, buildEtfFlows,
  getEtfFlows, setSosoFetcher, etfChatCard,
  usdSigned, coinsSigned, windowWords, providerLine, takeawayLine,
};
