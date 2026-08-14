'use strict';
/**
 * An unauthenticated MCP tool served the operator's live position sizes.
 *
 * `get_flight_record` returned the sealed decision ledger verbatim —
 * `records: all.slice(0, limit)` and `{ record: rec || null }` — and those
 * objects are exactly what POST /api/bot/sync/flight stores and getLatestFlight
 * hands back unmodified. `flight_recorder.py` puts `size_usd` and `pnl_usd` on
 * every one. `/mcp` is mounted at server.js:349 with no auth, and the same
 * registry is reachable again through POST /api/tool/invoke.
 *
 * So any anonymous caller read per-decision position sizes and realized dollar
 * P&L, from which account scale follows.
 *
 * THE SCRUBBER ALREADY EXISTED AND ALREADY HAD A CALLER. `public_flight.js`
 * does `records.map(sanitizeRecord)` over the identical getLatestFlight output,
 * and `lib/flight.js` says in its own header that the full dollar-carrying
 * record is reserved for the authed view while the public view "strips every
 * dollar figure". The rule was written down, the tool was written, and one of
 * the two public surfaces used it. That is the shape this repository keeps
 * finding: not an unknown rule, an unapplied one.
 *
 * WHY THIS TEST SWEEPS ALL 30 TOOLS AND NOT JUST THE ONE
 *
 * The audit asked for a test planting size_usd/pnl_usd and asserting the MCP
 * output carries neither. That is the first half below. The second half exists
 * because the same question — "which OTHER surface makes the same claim?" — is
 * what found this defect two audits after the sibling surface was fixed. A test
 * that pins get_flight_record alone would leave the next tool to be written
 * exactly as unguarded as this one was.
 *
 * MARKET FACTS ARE NOT ACCOUNT FACTS. §4 permits market prices, volume, OI and
 * gas on public payloads — they are facts about the world, not about the
 * operator's book — so the sweep allows dollar-shaped keys on tools whose whole
 * purpose is market data, and names each one. A blanket scrub over every tool
 * would have stripped `get_gas` and the DEX volume comparisons, which is why
 * the fix is targeted rather than applied at the router boundary.
 */
process.env.JWT_SECRET = process.env.JWT_SECRET || 'j'.repeat(64);
delete process.env.DATABASE_URL;

const test = require('node:test');
const assert = require('node:assert');
const { pool } = require('../db');
const { TOOLS } = require('../routes/mcp');
const { DOLLAR_KEY } = require('../lib/flight');

// A record in the shape flight_recorder.py emits, with the two dollar fields
// the audit names plus a percent field that MUST survive — a scrubber that
// stripped everything would pass a leak test and empty the surface.
const PLANTED = {
  decision_id: 'D-2026-08-14-001',
  symbol: 'SOL/USDT',
  action: 'OPEN',
  size_usd: 4200.55,
  pnl_usd: -318.9,
  pnl_pct: -7.6,
  r_multiple: -1.2,
  confidence: 0.71,
  chain: { sequence: 41, entry_hash: 'a'.repeat(64) },
};

async function plantFlight() {
  const flight = {
    records: [PLANTED],
    chain: { ok: true, length: 41, tip_hash: 'b'.repeat(64) },
    updated_at: '2026-08-14T00:00:00Z',
  };
  await pool.execute('REPLACE INTO flight_cache (id, flight_json) VALUES (1, ?)',
    [JSON.stringify(flight)]);
  // getLatestFlight memoises into a module-level `latestFlight`, so a test that
  // only wrote the row could read a stale null and pass while asserting
  // nothing. Force the read through the cache the same way the route does.
  const sync = require('../routes/sync');
  if (typeof sync.__setLatestFlight === 'function') sync.__setLatestFlight(flight);
  return flight;
}

/** Every key in a nested structure, flattened to dotted paths. */
function keyPaths(value, prefix = '', out = []) {
  if (Array.isArray(value)) {
    value.forEach((v, i) => keyPaths(v, `${prefix}[${i}]`, out));
  } else if (value && typeof value === 'object') {
    for (const [k, v] of Object.entries(value)) {
      const path = prefix ? `${prefix}.${k}` : k;
      out.push(path);
      keyPaths(v, path, out);
    }
  }
  return out;
}

function dollarKeys(payload) {
  return keyPaths(payload).filter((p) => {
    const leaf = p.split('.').pop().replace(/\[\d+\]$/, '');
    return DOLLAR_KEY.test(leaf) && !/_pct$|_percent$|_ratio$|_bps$|_r$|_multiple$|_rate$/i.test(leaf);
  });
}

// ── the reported defect ──────────────────────────────────────────────

test('get_flight_record does not publish position sizes or dollar P&L', async () => {
  await plantFlight();
  const out = await TOOLS.get_flight_record.handler({});
  const leaked = dollarKeys(out);
  assert.deepEqual(leaked, [],
    `an unauthenticated caller received ${leaked.join(', ')} — the operator's `
    + 'live position sizing');
});

test('nor on the single-record path', async () => {
  await plantFlight();
  const out = await TOOLS.get_flight_record.handler({ decision_id: PLANTED.decision_id });
  assert.ok(out.record, 'the decision_id lookup stopped finding the record');
  const leaked = dollarKeys(out);
  assert.deepEqual(leaked, [],
    `the decision_id path leaked ${leaked.join(', ')} — it was fixed on the `
    + 'list path only');
});

test('the record is still worth serving', async () => {
  // The other half of a redaction test. Stripping everything passes a leak
  // assertion and destroys the surface — the public ledger IS the percent and
  // R-multiple outcome, and lib/flight.js keeps a RATIO_KEY allowlist for
  // exactly this reason.
  await plantFlight();
  const out = await TOOLS.get_flight_record.handler({});
  const rec = out.records[0];
  assert.ok(rec, 'no records survived the scrubber');
  assert.equal(rec.decision_id, PLANTED.decision_id);
  assert.equal(rec.pnl_pct, PLANTED.pnl_pct, 'the percent outcome was stripped');
  assert.equal(rec.r_multiple, PLANTED.r_multiple, 'the R-multiple was stripped');
  assert.equal(rec.symbol, 'SOL/USDT');
});

test('a missing decision_id answers null, not a silent empty record', async () => {
  await plantFlight();
  const out = await TOOLS.get_flight_record.handler({ decision_id: 'nope' });
  assert.equal(out.record, null);
});

test('the payload says what it is', async () => {
  // public_flight.js carries a disclosure line for the same data. A consumer
  // who cannot tell a redacted ledger from a complete one may read absent
  // dollars as zero dollars.
  await plantFlight();
  const out = await TOOLS.get_flight_record.handler({});
  assert.match(String(out.disclosure || ''), /no dollar amounts/i);
});

// ── which OTHER surface makes the same claim ─────────────────────────

// Tools whose subject IS market data. §4: "Market prices, volume, OI and gas
// are public market facts and are fine." Named individually rather than
// pattern-matched, so adding a tool cannot quietly join the exemption.
const MARKET_DATA_TOOLS = new Set([
  'get_gas',            // gas price, in gwei and its fiat equivalent
  'get_dex_compare',    // pool liquidity and volume across venues
  'get_meme_radar',     // token market caps and volume
  'get_rwa_radar',      // tokenised-asset market data
  'research_token',     // market cap, liquidity, volume for one token
  'scan_token_safety',  // liquidity depth as a rug-risk signal
  'get_alpha_intel',    // market-wide flow
  'get_airdrop_radar',  // campaign sizes, a published market fact
  'xray_transaction',   // an on-chain transaction's own value
  'scan_transaction',   // ditto
  'get_systemic_risk',  // protocol TVL
  'get_rune_stats',     // mint price of a public collection
]);

// A different exemption, for a different reason, kept separate so neither
// borrows the other's justification.
//
// `run_what_if` returns `net_pnl_usd` and `final_usd`, and the sweep flagged
// it — correctly, as a match, and wrongly as a defect. The dollars are the
// CALLER'S: `stake_usd` is their own input (default 1000), and lib/replay.js
// scales each recorded trade by `stake / recorded notional`, so the operator's
// absolute `size_usd` appears only as a divisor and cancels. What survives into
// the output is a percentage return times a number the caller chose.
//
// Checking that before "fixing" it is the point. Scrubbing this tool would have
// emptied a working feature to satisfy a pattern match — the cost CLAUDE.md
// names for a refactor bought with no safety. The property is asserted below
// rather than trusted, so a future change that returned the real notional stops
// being exempt.
const CALLER_STAKE_TOOLS = new Set(['run_what_if']);

test('no OTHER unauthenticated MCP tool publishes account dollars', async () => {
  await plantFlight();
  const leaks = [];
  const unexercised = [];

  for (const [name, tool] of Object.entries(TOOLS)) {
    if (MARKET_DATA_TOOLS.has(name) || CALLER_STAKE_TOOLS.has(name)) continue;
    let out;
    try {
      out = await tool.handler({});
    } catch (err) {
      // A tool that needs the bot gateway or a live contract cannot be driven
      // here. RECORDED, not skipped silently — a sweep that quietly covered
      // half its surface and reported success is this repository's founding
      // defect wearing a test's clothes.
      unexercised.push(`${name} (${String(err && err.message).slice(0, 60)})`);
      continue;
    }
    const found = dollarKeys(out);
    if (found.length) leaks.push(`${name}: ${found.join(', ')}`);
  }

  assert.deepEqual(leaks, [],
    'unauthenticated MCP tools published account dollar figures:\n  '
    + leaks.join('\n  '));

  // Not an assertion — a printed inventory, so the next reader knows exactly
  // how much of the surface this sweep actually reached.
  if (unexercised.length) {
    console.log(`[mcp dollar sweep] not exercised here (${unexercised.length}/`
      + `${Object.keys(TOOLS).length}): ${unexercised.join('; ')}`);
  }
});

test('the sweep actually reaches tools, and the detector actually detects', async () => {
  // Guards the guard, twice. A sweep where every tool threw would pass the
  // assertion above while checking nothing, and a dollarKeys() that matched
  // nothing would do the same.
  await plantFlight();
  let exercised = 0;
  for (const [name, tool] of Object.entries(TOOLS)) {
    if (MARKET_DATA_TOOLS.has(name) || CALLER_STAKE_TOOLS.has(name)) continue;
    try { await tool.handler({}); exercised++; } catch { /* counted above */ }
  }
  assert.ok(exercised >= 5,
    `only ${exercised} tools ran — the sweep is not covering the surface`);

  assert.deepEqual(
    dollarKeys({ records: [{ size_usd: 1, pnl_pct: 2 }] }),
    ['records[0].size_usd'],
    'the leak detector does not detect');
});

test('every market-data exemption names a real tool', async () => {
  // An exemption for a tool that no longer exists is a stale allowlist entry
  // that would silently exempt a future tool of the same name.
  const missing = [...MARKET_DATA_TOOLS, ...CALLER_STAKE_TOOLS].filter((n) => !(n in TOOLS));
  assert.deepEqual(missing, [],
    `exempted tools that do not exist: ${missing.join(', ')}`);
});

test("run_what_if's dollars are the caller's, not the operator's", async () => {
  // The property that earns the exemption above, asserted rather than assumed.
  //
  // Double every recorded position size AND its PnL — same percentage return,
  // twice the operator's account scale. If the replay output moves, it is
  // carrying the operator's absolute sizing and the exemption is wrong.
  const { computeReplay } = require('../lib/replay');
  const small = [
    { symbol: 'SOL/USDT', pnl: 50, size_usd: 500, closed_at: '2026-08-01' },
    { symbol: 'BTC/USDT', pnl: -20, size_usd: 400, closed_at: '2026-08-02' },
  ];
  const large = small.map((t) => ({ ...t, pnl: t.pnl * 100, size_usd: t.size_usd * 100 }));

  const a = computeReplay(small, 1000);
  const b = computeReplay(large, 1000);
  assert.deepEqual(b, a,
    'the replay output changed when only the operator ACCOUNT SCALE changed — '
    + 'run_what_if is publishing position sizing and must not be exempt');
});

test("...and they DO move with the caller's own stake", async () => {
  // The other half: a replay that ignored the stake would pass the test above
  // by being constant, which is not the property claimed.
  const { computeReplay } = require('../lib/replay');
  const trades = [{ symbol: 'SOL/USDT', pnl: 50, size_usd: 500, closed_at: '2026-08-01' }];
  const at1k = computeReplay(trades, 1000);
  const at2k = computeReplay(trades, 2000);
  assert.notDeepEqual(at2k, at1k, 'the replay ignores the stake it is given');
  assert.equal(at2k.fixed.net_pnl_usd, at1k.fixed.net_pnl_usd * 2,
    'the caller\'s dollars do not scale with the caller\'s stake');
});
