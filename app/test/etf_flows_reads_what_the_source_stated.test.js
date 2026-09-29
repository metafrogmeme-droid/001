'use strict';
/**
 * US SPOT CRYPTO ETF FLOWS: ONE READING, AND NOTHING IN IT IS A ZERO THE SOURCE
 * DID NOT STATE.
 *
 * `app/lib/etf_flows.js` reads SoSoValue's daily US spot ETF flows and is the
 * one reading both surfaces render: the Markets panel reads
 * `GET /api/market/etf-flows`, and Telegram's `/etf` fetches the card route,
 * which carries the payload as `data` so the bot's picture draws the same
 * figures. These drive the reading on planted payloads (never the network):
 *
 *   - a day listed with no readable flow is counted unread and left out of
 *     the week's sum, never added as zero;
 *   - an asset the source did not answer for is named, and is not in the
 *     total; an asset reported on an earlier day is named too, and its week
 *     is not summed into a different week's total;
 *   - the coin estimate needs the funds' net assets and holdings stated for
 *     the same day, and is marked `approx`;
 *   - a fund whose flow is dated another day is unread for this day;
 *   - a read that reached no asset THROWS, so the panel paints its failure
 *     state and the route answers 502, never "no flows".
 */
process.env.JWT_SECRET = 'j'.repeat(64);
process.env.BOT_SYNC_SECRET = 's'.repeat(48);
delete process.env.DATABASE_URL;

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const http = require('node:http');
const path = require('node:path');
const vm = require('node:vm');
const express = require('express');

const etf = require('../lib/etf_flows');

const SECRET = process.env.BOT_SYNC_SECRET;

/** A daily history: `[[date, flow], ...]`, newest first as the source sends. */
function hist(rows) {
  return rows.map(([date, flow]) => ({ date, totalNetInflow: flow, totalValueTraded: 1e9 }));
}

/** One metrics field, the source's shape. */
function f(value, date = '2026-09-28', status = '1') {
  return { value: value == null ? value : String(value), lastUpdateDate: date, status };
}

function metrics({ assets = 1000, holdings = 10, date = '2026-09-28', funds = [] } = {}) {
  return {
    totalNetAssets: f(assets, date),
    totalTokenHoldings: f(holdings, date),
    dailyNetInflow: f(0, date),
    list: funds.map(([ticker, institute, flow, fdate]) => ({
      ticker, institute, dailyNetInflow: f(flow, fdate || date),
    })),
  };
}

const BTC_WEEK = [
  ['2026-09-28', 30e6], ['2026-09-25', 100e6], ['2026-09-24', 200e6],
  ['2026-09-23', -50e6], ['2026-09-22', 20e6],
  ['2026-09-19', 5e6], ['2026-09-18', 7e6],
];

test('the week is the seven calendar days ending on the latest day; the days before it are not in it', () => {
  const r = etf.buildEtfFlows({ btc: { hist: hist(BTC_WEEK), metrics: null } }, 0);
  const btc = r.assets.find((a) => a.key === 'btc');
  assert.equal(btc.latest_date, '2026-09-28');
  assert.deepEqual(btc.days.map((d) => d.date), ['2026-09-22', '2026-09-23', '2026-09-24', '2026-09-25', '2026-09-28']);
  assert.equal(btc.week.net_flow_usd, 300e6);
  // No week-over-week figure: nothing prints one, and a field no surface
  // reads is one the next reader trusts because it is there.
  assert.equal(btc.prior_week, undefined);
  assert.equal(r.window_start, '2026-09-22');
  assert.equal(r.as_of, '2026-09-28');
});

test('a day listed with no readable flow is unread and left out of the sum, not added as zero', () => {
  const rows = hist(BTC_WEEK);
  rows[1].totalNetInflow = null;           // 2026-09-25
  rows[2].totalNetInflow = '';             // 2026-09-24: an empty string is not zero
  const btc = etf.buildEtfFlows({ btc: { hist: rows } }, 0).assets[0];
  assert.equal(btc.week.days_listed, 5);
  assert.equal(btc.week.days_read, 3);
  assert.equal(btc.week.net_flow_usd, 30e6 - 50e6 + 20e6);
  assert.equal(btc.days.find((d) => d.date === '2026-09-25').net_flow_usd, null);
});

test('a week of no readable day is no figure, and a measured zero stays zero', () => {
  const none = etf.buildEtfFlows({ btc: { hist: hist([['2026-09-28', null], ['2026-09-25', 'x']]) } }, 0);
  assert.equal(none.assets[0].week.net_flow_usd, null);
  assert.equal(none.total.net_flow_usd, null);
  const zero = etf.buildEtfFlows({ btc: { hist: hist([['2026-09-28', 0], ['2026-09-25', 0]]) } }, 0);
  assert.equal(zero.assets[0].week.net_flow_usd, 0);
  assert.equal(zero.total.net_flow_usd, 0);
});

test('num: a bool, an empty string and junk are not figures; "0" is a zero', () => {
  for (const v of [true, false, '', '  ', 'x', null, undefined, NaN, Infinity]) assert.equal(etf.num(v), null, String(v));
  assert.equal(etf.num('0'), 0);
  assert.equal(etf.num('12.5'), 12.5);
});

test('an asset the source did not answer for is named and kept out of the total', () => {
  const r = etf.buildEtfFlows({ btc: { hist: hist(BTC_WEEK) }, eth: { hist: null } }, 0);
  const eth = r.assets.find((a) => a.key === 'eth');
  assert.equal(eth.read, false);
  assert.equal(eth.reason, 'unavailable');
  assert.deepEqual(r.total.assets_in_total, ['BTC']);
  assert.equal(r.total.assets_read, 1);
  const empty = etf.buildEtfFlows({ sol: { hist: [] } }, 0).assets.find((a) => a.key === 'sol');
  assert.equal(empty.reason, 'no_rows');
});

test('an asset reported on an earlier day is named and not summed into another week', () => {
  const r = etf.buildEtfFlows({
    btc: { hist: hist(BTC_WEEK) },
    sol: { hist: hist([['2026-09-25', 9e6], ['2026-09-24', 1e6]]) },
  }, 0);
  const sol = r.assets.find((a) => a.key === 'sol');
  assert.equal(sol.latest_date, '2026-09-25');
  assert.deepEqual(r.total.assets_in_total, ['BTC']);
  assert.equal(r.total.net_flow_usd, 300e6);
});

test('the coin estimate needs net assets and holdings stated for the same day', () => {
  const same = etf.buildEtfFlows({ btc: { hist: hist(BTC_WEEK), metrics: metrics({ assets: 1000, holdings: 10 }) } }, 0).assets[0];
  assert.equal(same.implied_price, 100);
  assert.equal(same.week.coins, 3e6);
  assert.equal(same.week.approx, true);
  const skew = metrics({ assets: 1000, holdings: 10 });
  skew.totalTokenHoldings.lastUpdateDate = '2026-09-25';
  const other = etf.buildEtfFlows({ btc: { hist: hist(BTC_WEEK), metrics: skew } }, 0).assets[0];
  assert.equal(other.implied_price, null);
  assert.equal(other.week.coins, null);
  assert.equal(other.week.approx, false);
  const zeroHold = etf.buildEtfFlows({ btc: { hist: hist(BTC_WEEK), metrics: metrics({ holdings: 0 }) } }, 0).assets[0];
  assert.equal(zeroHold.week.coins, null, 'no holdings is no price, never a division');
});

test('a field the source did not stand behind (status other than 1) is unread', () => {
  const m = metrics({ assets: 1000, holdings: 10 });
  m.totalNetAssets.status = '0';
  const a = etf.buildEtfFlows({ btc: { hist: hist(BTC_WEEK), metrics: m } }, 0).assets[0];
  assert.equal(a.implied_price, null);
  assert.deepEqual(etf.field({ value: '5', lastUpdateDate: '2026-09-28', status: 1 }), { value: 5, date: '2026-09-28' });
});

test('a fund dated another day is unread for this day; a provider sums its funds and says when partial', () => {
  const m = metrics({ funds: [
    ['IBIT', 'BlackRock ', 54e6],
    ['GBTC', 'Grayscale', -23e6],
    ['BTC', 'Grayscale', 10e6],
    ['FBTC', 'Fidelity', 0],
    ['ARKB', 'Ark', 3e6, '2026-09-25'],
  ] });
  const btc = etf.buildEtfFlows({ btc: { hist: hist(BTC_WEEK), metrics: m } }, 0).assets[0];
  assert.equal(btc.funds_date, '2026-09-28');
  const ark = btc.funds.find((x) => x.ticker === 'ARKB');
  assert.equal(ark.net_flow_usd, null, 'a flow dated another day is not this day\'s flow');
  const by = Object.fromEntries(btc.providers.map((p) => [p.provider, p]));
  assert.equal(by.BlackRock.net_flow_usd, 54e6, 'the provider name is trimmed');
  assert.equal(by.Grayscale.net_flow_usd, -13e6);
  assert.deepEqual([by.Grayscale.funds, by.Grayscale.funds_read], [2, 2]);
  assert.equal(by.Fidelity.net_flow_usd, 0, 'a measured zero stays zero');
  assert.equal(by.Ark.net_flow_usd, null);
  assert.deepEqual([by.Ark.funds, by.Ark.funds_read], [1, 0]);
  assert.deepEqual(btc.providers.map((p) => p.provider), ['BlackRock', 'Grayscale', 'Fidelity', 'Ark'],
    'ranked by the size of the flow, an unread one last');
});

test('the takeaway is days of issuance, derived, and says which way', () => {
  const up = etf.buildEtfFlows({ btc: { hist: hist(BTC_WEEK), metrics: metrics({ assets: 1000, holdings: 10 }) } }, 0);
  assert.equal(up.takeaway.direction, 'inflow');
  assert.equal(up.takeaway.days_of_issuance, 3e6 / 450);
  const down = etf.buildEtfFlows({ btc: { hist: hist([['2026-09-28', -900e6]]), metrics: metrics({ assets: 1000, holdings: 10 }) } }, 0);
  assert.equal(down.takeaway.direction, 'outflow');
  assert.equal(down.takeaway.days_of_issuance, 9e6 / 450);
  assert.match(etf.takeawayLine(down), /^Net selling of ≈ 9,000,000 BTC is about 20000 days/);
  const noPrice = etf.buildEtfFlows({ btc: { hist: hist(BTC_WEEK) } }, 0);
  assert.equal(noPrice.takeaway, null, 'no coin estimate, no takeaway');
});

test('usdSigned and coinsSigned: signs, units, and a dash for an absence', () => {
  assert.equal(etf.usdSigned(1.42e9), '+$1.42B');
  assert.equal(etf.usdSigned(-12.9e6), '-$12.9M');
  assert.equal(etf.usdSigned(0), '$0');
  assert.equal(etf.usdSigned(null), '—');
  assert.equal(etf.coinsSigned(16971.2, 'BTC'), '≈ +16,971 BTC');
  assert.equal(etf.coinsSigned(null, 'BTC'), null);
});

// ── the fetch, the cache and the refusal ─────────────────────────────────────

function fetcherFrom(answers) {
  const calls = [];
  const fn = async (endpoint, type) => { calls.push([endpoint, type]); return answers(endpoint, type); };
  fn.calls = calls;
  return fn;
}

test('a read that reached no asset throws: that is a failed read, never "no flows"', async () => {
  etf.setSosoFetcher(fetcherFrom(() => null));
  await assert.rejects(etf.getEtfFlows(), /ETF flows unavailable/);
  etf.setSosoFetcher(null);
});

test('the reading is cached, and a partial source still answers with the rest named', async () => {
  const fn = fetcherFrom((endpoint, type) => (type === 'us-btc-spot' && endpoint === 'historicalInflowChart'
    ? hist(BTC_WEEK) : null));
  etf.setSosoFetcher(fn);
  const a = await etf.getEtfFlows();
  const n = fn.calls.length;
  const b = await etf.getEtfFlows();
  assert.equal(fn.calls.length, n, 'the second read is the cache');
  assert.equal(a, b);
  assert.deepEqual(a.assets.map((x) => x.read), [true, false, false]);
  const card = await etf.etfChatCard();
  assert.match(card.reply_html, /ETH: not read \(the source did not answer\)/);
  assert.equal(card.data, a, 'the card carries the payload it was rendered from');
  etf.setSosoFetcher(null);
});

test('a fetcher that raises is a part the source did not answer, not a crash', async () => {
  etf.setSosoFetcher(async (endpoint, type) => {
    if (type === 'us-btc-spot' && endpoint === 'historicalInflowChart') return hist(BTC_WEEK);
    throw new Error('boom');
  });
  const r = await etf.getEtfFlows();
  assert.equal(r.total.assets_read, 1);
  etf.setSosoFetcher(null);
});

// ── the routes ───────────────────────────────────────────────────────────────

let server, base;

function get(p, headers = {}) {
  return new Promise((resolve, reject) => {
    const r = http.request(`${base}${p}`, { method: 'GET', headers }, (res) => {
      let d = '';
      res.on('data', (c) => { d += c; });
      res.on('end', () => resolve({ status: res.statusCode, data: d ? JSON.parse(d) : {} }));
    });
    r.on('error', reject);
    r.end();
  });
}

test.before(async () => {
  const app = express();
  app.use(express.json());
  app.use('/api/market', require('../routes/market'));
  app.use('/api/bot/sync', require('../routes/sync'));
  await new Promise((res) => { server = app.listen(0, '127.0.0.1', res); });
  base = `http://127.0.0.1:${server.address().port}`;
});

test.after(() => {
  if (server) server.close();
  etf.setSosoFetcher(null);
});

test('GET /api/market/etf-flows: the payload, or a 502 when nothing was read', async () => {
  etf.setSosoFetcher(fetcherFrom(() => null));
  const bad = await get('/api/market/etf-flows');
  assert.equal(bad.status, 502);
  assert.equal(bad.data.error, 'ETF flows unavailable');
  etf.setSosoFetcher(fetcherFrom((endpoint, type) => (endpoint === 'historicalInflowChart' && type === 'us-btc-spot'
    ? hist(BTC_WEEK) : null)));
  const ok = await get('/api/market/etf-flows');
  assert.equal(ok.status, 200);
  assert.equal(ok.data.total.net_flow_usd, 300e6);
  etf.setSosoFetcher(null);
});

test('the card route carries the card and the payload it was built from', async () => {
  etf.setSosoFetcher(fetcherFrom((endpoint, type) => (endpoint === 'historicalInflowChart' && type === 'us-btc-spot'
    ? hist(BTC_WEEK) : null)));
  const card = await etf.etfChatCard();
  const r = await get('/api/bot/sync/card/etf_flows', { 'X-Bot-Secret': SECRET });
  assert.equal(r.status, 200);
  assert.equal(r.data.reply_html, card.reply_html);
  assert.equal(r.data.intent, 'etf_flows');
  assert.equal(r.data.data.total.net_flow_usd, 300e6);
  etf.setSosoFetcher(fetcherFrom(() => null));
  const down = await get('/api/bot/sync/card/etf_flows', { 'X-Bot-Secret': SECRET });
  assert.equal(down.status, 500, 'a source that answered nothing is no card, never a card of zeros');
  etf.setSosoFetcher(null);
});

test('a card with no data carries no data key: the other cards\' answer is unchanged', async () => {
  // The rwa card has no `data`; the route adds the key only for a card that
  // carries a plain object.
  const rwa = require('../lib/rwa');
  rwa.setTickerFetcher(async () => ({ BTCUSDT: { price: 1, change: 1, volume: 1 } }));
  const r = await get('/api/bot/sync/card/rwa', { 'X-Bot-Secret': SECRET });
  assert.equal(r.status, 200);
  assert.deepEqual(Object.keys(r.data).sort(), ['intent', 'reply_html']);
  rwa.setTickerFetcher(null);
});

// ── the panel renderer ───────────────────────────────────────────────────────

const RAW = fs.readFileSync(path.join(__dirname, '..', 'public', 'js', 'dashboard.js'), 'utf8');

function panel() {
  const a = RAW.indexOf('// ── the ETF flows panel: renderer start ─');
  const b = RAW.indexOf('// ── the ETF flows panel: renderer end ─');
  assert.ok(a > 0 && b > a, 'the ETF panel renderer lost its markers; this harness slices between them');
  const ctx = {
    esc: (v) => String(v == null ? '' : v)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;'),
    out: null,
  };
  vm.runInNewContext(RAW.slice(a, b) + '\nout = { etfPanelHtml, etfUsd, etfClass, etfCoins };', ctx, { timeout: 5000 });
  return ctx.out;
}

test('the panel: an unread asset is named, a zero is muted, an absence has no colour', () => {
  const { etfPanelHtml, etfClass } = panel();
  const m = metrics({ assets: 1000, holdings: 10, funds: [['IBIT', '<b>Black</b>Rock', 54e6], ['FBTC', 'Fidelity', 0]] });
  const d = etf.buildEtfFlows({ btc: { hist: hist(BTC_WEEK), metrics: m }, eth: { hist: null } }, 0);
  const html = etfPanelHtml(d);
  assert.match(html, /ETH<\/b><\/td><td class="muted" colspan="4">not read — the source did not answer/);
  assert.match(html, /class="num up">\+\$300\.0M/);
  assert.match(html, /1 of 2 providers reported no flow/);
  assert.ok(!html.includes('<b>Black</b>Rock'), 'a provider name is escaped');
  assert.match(html, /&lt;b&gt;Black&lt;\/b&gt;Rock/);
  assert.match(html, /about <b>6667 days<\/b> of newly mined bitcoin/);
  assert.match(html, /derived: 450 BTC a day/);
  assert.equal(etfClass(0), 'muted');
  assert.equal(etfClass(null), 'muted');
  assert.equal(etfClass(''), 'muted');
  assert.equal(etfClass(true), 'muted');
  assert.equal(etfClass(-1), 'down');
  assert.equal(etfClass(1), 'up');
});

test('the panel: the loader throws on a payload that names no asset read, never an empty table', () => {
  const src = RAW.slice(RAW.indexOf("renderPanel(C('etf')"));
  const body = src.slice(0, src.indexOf('}, { timeoutMs: 17000 });'));
  assert.match(body, /mustRead\(r\);/);
  assert.match(body, /!d\.total\.assets_read\) \{\s*throw new Error/);
  assert.ok(!/return null/.test(body), 'an empty state would read as "no flows"');
});

test('the panel prints each figure as the reading does: one table, both copies', () => {
  // The browser cannot load app/lib/etf_flows.js, so the panel carries its
  // own two formatters. A second copy is a second answer unless something
  // holds the two equal: this table does, ties and absences included.
  const { etfUsd, etfCoins } = panel();
  const table = [0, -0, 1, -7, 2.5, 3.25, -3.25, 0.0125, 99.95, 100, 999.5, 1000, 1234.55, 12500,
    999999, 2250000, -1300000000, 1.005e9, 5e11, null, undefined, true, false, '12', '', '  ', 'n/a',
    Infinity, -Infinity, NaN];
  const diff = [];
  for (const v of table) {
    if (etfUsd(v) !== etf.usdSigned(v)) diff.push(['usd', v, etfUsd(v), etf.usdSigned(v)]);
    const lib = etf.coinsSigned(v, 'BTC');
    if (etfCoins(v, 'BTC') !== (lib == null ? '—' : lib)) diff.push(['coins', v, etfCoins(v, 'BTC'), lib]);
  }
  assert.deepEqual(diff, []);
});

test('a week whose coin estimate is a measured zero has no takeaway', () => {
  // "Net selling of 0 BTC is about 0.0 days of newly mined bitcoin" is a
  // sentence about a flow that did not happen; a measured zero week names no
  // direction at all.
  const m = metrics({ assets: 1000, holdings: 10 });
  const d = etf.buildEtfFlows({ btc: { hist: hist(BTC_WEEK.map(([date]) => [date, 0])), metrics: m } }, 0);
  const btc = d.assets.find((a) => a.key === 'btc');
  assert.equal(btc.week.coins, 0, 'the fixture must reach a measured zero estimate');
  assert.equal(d.takeaway, null);
});

test('the source\'s own refusal code is no answer, whatever `data` holds', async () => {
  // The HTTP half, driven through `fetch` itself: every other test injects a
  // fetcher, so this is the one place the success rule is read.
  const real = global.fetch;
  const answer = (code) => async () => ({ ok: true, json: async () => ({ code, data: hist(BTC_WEEK) }) });
  try {
    global.fetch = answer(40001);
    etf.setSosoFetcher(null);
    await assert.rejects(etf.getEtfFlows(), /ETF flows unavailable/);
    global.fetch = answer(0);
    etf.setSosoFetcher(null);
    const r = await etf.getEtfFlows();
    assert.equal(r.total.assets_read, 3, 'code 0 is the success the reading takes');
  } finally {
    global.fetch = real;
    etf.setSosoFetcher(null);
  }
});

test('the card route forwards only a plain object as data', async () => {
  // A renderer that answers a list (or anything else) is not a payload a
  // picture can draw; the route leaves the key out rather than forwarding it.
  const realCard = etf.etfChatCard;
  try {
    etf.etfChatCard = async () => ({ reply_html: 'card', data: [1, 2, 3] });
    const r = await get('/api/bot/sync/card/etf_flows', { 'X-Bot-Secret': SECRET });
    assert.equal(r.status, 200);
    assert.deepEqual(Object.keys(r.data).sort(), ['intent', 'reply_html']);
  } finally {
    etf.etfChatCard = realCard;
  }
});
