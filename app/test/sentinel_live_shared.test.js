'use strict';
// The Sentinel's surge flag is a DELTA against the previous poll, so the
// previous poll is state, not a cache. The website and the MCP tool both read
// the Sentinel now. If each kept its own baseline, whichever polled less
// recently would compare against an older snapshot and report a LARGER surge —
// two surfaces describing the same market differently, with neither of them
// wrong and no way for a user to tell which to believe.
//
// These tests pin the sharing. They drive the real modules with a stubbed
// ticker feed so the assertions are about the baseline, not about Bitget.

process.env.JWT_SECRET = 'j'.repeat(64);
delete process.env.DATABASE_URL;

const test = require('node:test');
const assert = require('node:assert');
const path = require('node:path');
const fs = require('node:fs');

const httpCache = require('../lib/http_cache');
const live = require('../lib/sentinel_live');
const { TOOLS } = require('../routes/mcp');

// A tiny universe whose open interest jumps between polls. ΔOI ≥ +12% is the
// Sentinel's "leverage piling in" threshold, so 1000 → 3000 (+200%) is an
// unambiguous surge that can only be seen against the PREVIOUS poll.
function tickers(oi) {
  const coin = (symbol, price, high, low, open, vol, funding) => ({
    symbol, lastPr: price, change24h: '0.05', baseVolume: '1000',
    usdtVolume: vol, holdingAmount: String(oi), fundingRate: funding,
    high24h: high, low24h: low, openUtc: open,
  });
  return {
    data: [
      coin('BTCUSDT', '100', '110', '90', '95', '100000', '0.0006'),
      coin('ETHUSDT', '50', '55', '45', '48', '90000', '0.0007'),
    ],
  };
}

/** Make the next read see exactly this OI, bypassing the network. */
async function feed(oi) {
  httpCache._clearCache();
  // Seed the shared cache directly: cached() resolves its fetcher internally,
  // so seeding the entry is more honest than patching a function reference.
  await httpCache.cached('tickers', 60_000, async () => tickers(oi))();
}

const surging = (payload) => ((payload.leverage || {}).surging || []).map((c) => c.base).sort();

test('the route and the MCP tool go through the SAME live read', () => {
  const market = fs.readFileSync(path.join(__dirname, '..', 'routes', 'market.js'), 'utf8');
  const mcp = fs.readFileSync(path.join(__dirname, '..', 'routes', 'mcp.js'), 'utf8');
  assert.match(market, /require\('\.\.\/lib\/sentinel_live'\)/,
    'the website route must not rebuild the Sentinel itself');
  assert.match(mcp, /require\('\.\.\/lib\/sentinel_live'\)/,
    'the MCP tool must not rebuild the Sentinel itself');
  // Neither may hold its own baseline any more.
  assert.doesNotMatch(market, /_sentinelOi/,
    'the route kept a private ΔOI baseline — that is the drift this prevents');
  assert.doesNotMatch(mcp, /buildStrengthMap/,
    'the MCP tool built its own strength map instead of sharing the live read');
});

test('a read has no baseline to surge against until one exists', async () => {
  // Pins the premise the two tests below rely on: a first read CANNOT report a
  // surge, so a surge appearing later is proof a previous read left a baseline.
  live._resetBaseline();
  await feed(1000);
  assert.deepEqual(surging(await live.getSentinel()), [],
    'a first read reported a surge with nothing to compare against');
});

test("the MCP tool's read leaves the baseline the WEBSITE then compares against", async () => {
  live._resetBaseline();
  await feed(1000);
  await TOOLS.get_systemic_risk.handler({});   // agent polls first

  await feed(3000);
  const viaSite = await live.getSentinel();    // website polls second
  assert.deepEqual(surging(viaSite), ['BTC', 'ETH'],
    'the website saw no surge after the agent polled — the tool is keeping its '
      + 'own ΔOI baseline, so the two surfaces read the same market differently');
});

test("the website's read leaves the baseline the MCP TOOL then compares against", async () => {
  live._resetBaseline();
  await feed(1000);
  await live.getSentinel();                    // website polls first

  await feed(3000);
  const viaTool = await TOOLS.get_systemic_risk.handler({});  // agent polls second
  assert.deepEqual(surging(viaTool), ['BTC', 'ETH'],
    'the agent saw no surge after the website polled — the two surfaces are on '
      + 'separate baselines');
  assert.equal(viaTool.heuristic, true);
});

test('the tool is registered, documented, and refuses to sound like a verdict', () => {
  const t = TOOLS.get_systemic_risk;
  assert.ok(t, 'get_systemic_risk is registered');
  assert.ok(t.description.length > 120, 'it explains itself to an agent');
  assert.match(t.description, /never a verdict/i,
    'a crowding read advertised without its limit gets used as a signal');
  assert.match(t.description, /never a forecast|nothing about direction/i,
    'crowding is not a direction call and the description must say so');
  assert.equal(t.inputSchema.additionalProperties, false);
});

test('it is published-data, not caller-input — the manifest must say so', () => {
  // It reads no caller input, so it belongs to the family that serves data the
  // public site already publishes. Getting this wrong would make the ERC-8257
  // manifest describe it incorrectly.
  assert.ok(!TOOLS.get_systemic_risk.computesOnInput,
    'get_systemic_risk takes no caller input; marking it computesOnInput would '
      + 'misfile it in the on-chain manifest');
  const m = require('../lib/tool8257').buildManifest({ tools: TOOLS });
  assert.ok(m.toolFamilies.publishedData.includes('get_systemic_risk'));
  assert.ok(!m.toolFamilies.callerInput.includes('get_systemic_risk'));
});

test('a market with no tickers is an error, never an empty all-clear', () => {
  // The honest failure: "unavailable", not a Sentinel showing zero risk.
  const src = fs.readFileSync(path.join(__dirname, '..', 'lib', 'sentinel_live.js'), 'utf8');
  assert.match(src, /if \(!tickers\.length\) throw new Error/,
    'an empty feed must throw, not render as a calm market');
});
