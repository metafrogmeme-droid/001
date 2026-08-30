'use strict';
/**
 * No dollar amounts on the public, unauthenticated tool surface.
 *
 * `get_alpha_intel` has no `requiresKey`, so it is served to anyone through
 * POST /api/tool/invoke — the endpoint the published ERC-8257 manifest names.
 * It returned `getUserIntel` verbatim, which carries `net_pnl_usd`,
 * `expectancy_usd`, `avg_win_usd`, `avg_loss_usd` and `max_drawdown_usd`.
 *
 * The fix is at the BOUNDARY, not at the source, and that distinction is the
 * reachability check this repo insists on. `getUserIntel` has two callers and
 * only one of them is public: `routes/portfolio.js` serves `req.user.user_id`,
 * an authenticated per-user surface where dollars are explicitly permitted.
 * Stripping the fields in `computeIntel` would have broken a surface entitled
 * to them — the rule is about who is reading, not about the number.
 *
 * The last test is the one that matters most: `mcp_public_records.test.js`
 * checks a CURATED list of tools, so a tool added later — as this one was —
 * is outside what it looks at. This scans every public entry in TOOLS, so the
 * next `*_usd` cannot arrive unnoticed.
 */

process.env.JWT_SECRET = process.env.JWT_SECRET || 'j'.repeat(64);
delete process.env.DATABASE_URL;

const test = require('node:test');
const assert = require('node:assert');

const { publicIntel } = require('../lib/intel');
const mcp = require('../routes/mcp');

const FULL = {
  trades: 12, skipped: 1, wins: 7, losses: 5,
  win_rate_pct: 58.33,
  net_pnl_usd: 412.5,
  expectancy_usd: 34.38,
  avg_win_usd: 120.0,
  avg_loss_usd: -80.0,
  payoff_ratio: 1.5,
  profit_factor: 1.31,
  max_drawdown_usd: -210.0,
  longest_win_streak: 3,
  longest_loss_streak: 2,
  alpha: { priced: 10, unpriced: 2, mean_alpha_pct: 2.4, edge_usd: 55.0 },
};

test('every _usd field is stripped, at every depth', () => {
  const out = publicIntel(FULL);
  const leaked = JSON.stringify(out).match(/"[a-z_]*_usd"/g);
  assert.equal(leaked, null, `dollar fields survived: ${leaked}`);
  assert.ok(!('edge_usd' in out.alpha), 'a NESTED dollar field survived');
});

test('the percent, ratio and count fields all survive', () => {
  const out = publicIntel(FULL);
  for (const k of ['trades', 'wins', 'losses', 'win_rate_pct', 'payoff_ratio',
    'profit_factor', 'longest_win_streak', 'skipped']) {
    assert.ok(k in out, `${k} was stripped — the tool now says less than it may`);
  }
  assert.equal(out.alpha.mean_alpha_pct, 2.4);
});

test('a null or absent value is preserved as null, not dropped', () => {
  // Absent is a measurement here: `win_rate_pct: null` at zero trades says
  // "undefined", and dropping the key would let a reader assume it was simply
  // not offered.
  const out = publicIntel({ trades: 0, win_rate_pct: null, net_pnl_usd: 0 });
  assert.ok('win_rate_pct' in out);
  assert.equal(out.win_rate_pct, null);
  assert.ok(!('net_pnl_usd' in out));
});

test('non-objects pass through rather than throwing', () => {
  assert.equal(publicIntel(null), null);
  assert.equal(publicIntel(undefined), undefined);
  assert.equal(publicIntel('x'), 'x');
});

test('arrays are left alone', () => {
  const out = publicIntel({ rows: [{ net_pnl_usd: 5 }], trades: 1 });
  assert.ok(Array.isArray(out.rows));
});

test('the public tool is wired to the stripped projection', () => {
  // Reachability: publicIntel existing and get_alpha_intel not using it would
  // be #58 — a function nothing calls, indistinguishable from one that does
  // not work.
  //
  // Asked of the REGISTRY and the handler's own source, not of a window cut
  // out of the file. Two window attempts failed here for two different
  // reasons: a fixed 900 characters overran into the next tool and picked up
  // ITS requiresKey, and a next-tool regex ran 2097 characters into
  // WRITE_TOOLS because get_alpha_intel is the last entry. The object knows
  // exactly what the object is.
  const tool = mcp.TOOLS.get_alpha_intel;
  assert.ok(tool, 'get_alpha_intel is gone');
  assert.ok(!tool.requiresKey,
    'this tool is now key-gated — the strip may no longer be needed, but '
    + 'check before relaxing it');
  // COMMENTS STRIPPED FIRST. The comment explaining this wiring originally
  // sat between `async () =>` and its body, so Function.prototype.toString
  // included it and this assertion passed with the call itself deleted — a
  // false PASS of the family CLAUDE.md documents four false FAILURES of.
  const code = String(tool.handler).replace(/\/\/[^\n]*/g, '');
  assert.match(code, /publicIntel\s*\(/,
    'get_alpha_intel still serves the raw intel');
});

//: Public tools whose dollar figures are NOT account data, with the reason.
//: Checked rather than assumed — the first version of the scan below flagged
//: both of these and would have had legitimate fields stripped.
const DOLLARS_ALLOWED = {
  //: "Market prices, volume, OI and gas are public market facts and are fine."
  get_gas: 'gas cost is a public market fact, not this account\'s money',
  //: A hypothetical the CALLER supplies. Their own number, echoed back to
  //: them; nothing about the agent's account is disclosed by it.
  run_what_if: 'caller-supplied hypothetical stake, not published account data',
};

test('NO public tool in the registry advertises a _usd OUTPUT', () => {
  // The curated-list problem, generalised. mcp_public_records.test.js checks a
  // hand-written set of tool names; get_alpha_intel was added after it and so
  // was never in scope. This asks the registry itself.
  //
  // OUTPUT only. inputSchema carries what the caller sends — their own values,
  // which disclose nothing about the agent — and reading it as a fallback made
  // run_what_if's caller-supplied `stake_usd` look like a leak.
  const offenders = [];
  for (const [name, tool] of Object.entries(mcp.TOOLS)) {
    if (tool.requiresKey) continue;            // key-gated: per-user, dollars ok
    if (name in DOLLARS_ALLOWED) continue;
    const shape = JSON.stringify(tool.outputSchema || {})
      + ' ' + String(tool.description || '');
    for (const m of shape.match(/[a-z_]*_usd/g) || []) offenders.push(`${name}: ${m}`);
  }
  assert.deepEqual(offenders, [],
    'a public tool advertises dollar fields — percent, ratio and count only. '
    + 'If it is a market fact or a caller-supplied value, add it to '
    + 'DOLLARS_ALLOWED with the reason.');
});

test('the allow-list names only tools that still exist', () => {
  // A stale exemption is a hole nobody remembers opening.
  for (const name of Object.keys(DOLLARS_ALLOWED)) {
    assert.ok(name in mcp.TOOLS, `${name} is exempted but no longer exists`);
  }
});
