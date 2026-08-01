'use strict';
/**
 * §4 on the public surface: percent / ratio / count, never dollars.
 *
 * The rule was held by convention. Individual routes had their own tests —
 * public_flight, public_leaderboard, mcp_public_records — but nothing
 * enumerated the PUBLIC SET and checked it, so a new public route emitting
 * `pnl_usd` would have failed nothing.
 *
 * Two live violations were found the moment the set was enumerated:
 *
 *   mcp.js get_track_record  net_pnl_usd + a per-trade dollar `pnl`, on an
 *     endpoint mounted with rate limiting only (the router.use above it is a
 *     64 KB body cap, not auth). Its own `source` string claimed "same data
 *     as the public /track page" — /track publishes equity INDEXED TO 100
 *     precisely to avoid dollars, so the tool was strictly more revealing
 *     than the page it said it mirrored.
 *
 *   call.js  emitted `pnl` on the public /call/<key> receipt. Always null in
 *     practice, because the sole production caller of build_signal_payload
 *     posts status="NEW" and never passes pnl — but the path was fully built
 *     and that function's docstring names status/pnl as the intended outcome
 *     channel. §4 held there only because a parameter was never passed.
 *
 *     A RULE THAT HOLDS ONLY BECAUSE NOBODY HAS EXERCISED THE PATH IS NOT
 *     ENFORCED. IT IS UNTESTED.
 *
 * WHY THIS IS A FIELD-NAME ALLOWLIST AND NOT A `$` REGEX
 *
 * Twice already a §4 check written as a dollar-sign regex was wrong: it
 * matched `${...}` template literals, then the `$(id)` DOM helper. Both times
 * it flagged syntax rather than money. This names the ACCOUNT-MONEY fields
 * that must not appear as emitted keys, and nothing else.
 *
 * Market facts are deliberately NOT forbidden. §4 permits prices, volume, OI
 * and gas as public market facts, so `price`, `entry_price`, `stop_loss`,
 * `take_profit`, `volume` and friends are absent from the list on purpose —
 * a check that flagged them would be wrong about the rule it enforces.
 */

const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

const ROUTES = path.join(__dirname, '..', 'routes');

/** Account-money field names. Emitted as a JSON key, each is a §4 breach. */
const FORBIDDEN = [
  'pnl', 'pnl_usd', 'net_pnl_usd', 'realized_pnl', 'unrealized_pnl',
  'equity', 'equity_usd', 'balance', 'balance_usd',
  'margin', 'margin_usd', 'notional', 'notional_usd',
  'amount_usd', 'usd_value', 'vusdt', 'collateral_usd',
];

/**
 * Exemptions, each with the reason it is safe. An unexplained entry here
 * would quietly become the hole this file exists to close.
 */
const EXEMPT = {
  // Publishes equity INDEXED TO 100, never a dollar figure. Verified.
  'track.js': ['equity'],
  // The frame card's `balance` is a percentage. Verified.
  'frame.js': ['balance'],
  // Not a public READ surface: the bot's inbound sync, gated by botAuth.
  // Its `equity`/`pnl` keys are fields being written INTO the database from
  // an authenticated agent, not values served to anyone.
  'sync.js': ['equity', 'pnl'],
};

function publicRouteFiles() {
  return fs.readdirSync(ROUTES)
    .filter((f) => f.endsWith('.js'))
    .filter((f) => !fs.readFileSync(path.join(ROUTES, f), 'utf8')
      .includes('authMiddleware'));
}

const { codeOnly } = require('./helpers/code_only');

test('the public route set is non-trivial', () => {
  const files = publicRouteFiles();
  assert.ok(files.length > 10,
    `only ${files.length} public routes found — the detector is probably `
    + 'broken, and a check that inspects nothing passes everything');
});

test('no public route emits an account-money field', () => {
  const offenders = [];
  for (const f of publicRouteFiles()) {
    const src = codeOnly(fs.readFileSync(path.join(ROUTES, f), 'utf8'));
    const allowed = EXEMPT[f] || [];
    for (const name of FORBIDDEN) {
      if (allowed.includes(name)) continue;
      // As an emitted OBJECT KEY -- `pnl:` or `'pnl':`. A SELECT listing the
      // column inside a template string does not match, which is correct:
      // agent_record.js reads margin and pnl and emits only rom_pct from
      // them. Reading money to compute a ratio is exactly right.
      const re = new RegExp(`(^|[{,\\s])['"\`]?${name}['"\`]?\\s*:`, 'm');
      if (re.test(src)) offenders.push(`${f} -> ${name}`);
    }
  }
  assert.deepStrictEqual(offenders, [],
    '§4: public surfaces carry percent/ratio/count only. Offenders:\n  '
    + offenders.join('\n  '));
});

test('market facts are not treated as forbidden', () => {
  // §4 permits market prices, volume, OI and gas as public facts. A check
  // that flagged them would be wrong about the rule it enforces, and would
  // be silenced by exemptions until it protected nothing.
  for (const fact of ['price', 'entry_price', 'stop_loss', 'take_profit',
    'volume', 'open_interest', 'gas_gwei', 'funding_rate']) {
    assert.ok(!FORBIDDEN.includes(fact), `${fact} is a permitted market fact`);
  }
});

test('the check can actually fail', () => {
  // The property a guard must have before any other property matters. A
  // previous smoke test in this repo reported OK for a process that had never
  // existed, because it was matching itself.
  const re = new RegExp(`(^|[{,\\s])['"\`]?net_pnl_usd['"\`]?\\s*:`, 'm');
  assert.ok(re.test('  return { trades: 3, net_pnl_usd: 12.5 };'));
  assert.ok(re.test("  { 'net_pnl_usd': 1 }"));
  assert.ok(!re.test('SELECT net_pnl_usd FROM t'), 'a SELECT is not an emit');
});

test('mcp get_track_record publishes no dollar amount', () => {
  const src = codeOnly(fs.readFileSync(path.join(ROUTES, 'mcp.js'), 'utf8'));
  const i = src.indexOf('get_track_record');
  assert.ok(i > 0);
  const block = src.slice(i, i + 2600);
  assert.ok(!/net_pnl_usd\s*:/.test(block), 'net_pnl_usd is a dollar figure');
  assert.ok(/profit_factor\s*:/.test(block),
    'the ratio must survive — it carries the signal the dollar figure did');
  assert.ok(/win_rate_pct\s*:/.test(block));
});

test('mcp recent trades report an outcome, not an amount', () => {
  const src = codeOnly(fs.readFileSync(path.join(ROUTES, 'mcp.js'), 'utf8'));
  const i = src.indexOf('recent_trades');
  const block = src.slice(i, i + 500);
  assert.ok(/result\s*:/.test(block));
  assert.ok(!/\bpnl\s*:/.test(block), 'a per-trade dollar figure');
  // A scratch is not a losing trade.
  assert.ok(block.includes("'flat'"), 'flat must be its own outcome');
});

test('the public call receipt carries no pnl', () => {
  // call.js has THREE receipt paths. The first `outcome:` is the arena one,
  // which emits `pct` = pnl/margin*100 — already correct. Asserting on the
  // WORD pnl there matched `Number(t.pnl)`, the ratio's own input, and failed
  // on correct code: the same false positive as agent_record.js, where margin
  // and pnl are read and only rom_pct is emitted. Reading money to compute a
  // ratio is exactly right; the rule is about what is EMITTED.
  const src = codeOnly(fs.readFileSync(path.join(ROUTES, 'call.js'), 'utf8'));
  const KEY = /(^|[{,\s])['"`]?pnl['"`]?\s*:/m;

  const arena = src.slice(src.indexOf('outcome:'), src.indexOf('outcome:') + 300);
  assert.ok(/pct\s*:/.test(arena), 'the arena receipt reports percent return');
  assert.ok(!KEY.test(arena), 'and never a dollar amount');

  const i = src.lastIndexOf('outcome:');
  const signals = src.slice(i, i + 200);
  assert.ok(!KEY.test(signals), 'the signals receipt must not emit pnl');
  // The receipt still has to do its job: prove WHAT was called and WHEN.
  assert.ok(/status\s*:/.test(signals) && /resolved_at\s*:/.test(signals));
});

test('a hypothetical on caller-supplied capital stays labelled', () => {
  // SCOPE LIMIT, stated rather than left to be discovered: this scan reads
  // the route files, so a dollar field returned from a LIB and spread into a
  // response is invisible to it. run_what_if is exactly that shape —
  // `...(await require('../lib/replay').runReplay(...))` yields
  // fixed.net_pnl_usd.
  //
  // It is correct as-is, and the distinction is the point of §4 rather than
  // an exception to it: the figure is arithmetic on the CALLER's own
  // stake_usd, so it discloses nothing about RUNECLAW's capital. The same
  // split the `computesOnInput` marker draws for the ERC-8257 manifest.
  //
  // What makes that safe is the label. If `hypothetical` ever stopped being
  // emitted, the tool would be publishing a bare dollar figure with no
  // indication it was the caller's own number scaled — so the label is what
  // this pins.
  const src = codeOnly(fs.readFileSync(path.join(ROUTES, 'mcp.js'), 'utf8'));
  const i = src.indexOf('run_what_if');
  assert.ok(i > 0);
  const block = src.slice(i, i + 900);
  assert.ok(/hypothetical\s*:\s*true/.test(block),
    'a scaled dollar figure must never be served unlabelled');
  assert.ok(/stake_usd/.test(block), 'and must be driven by caller input');
});

test('every exemption names a file that is still public', () => {
  // A stale exemption is how this file would rot into permission.
  const pub = new Set(publicRouteFiles());
  for (const f of Object.keys(EXEMPT)) {
    assert.ok(pub.has(f),
      `${f} is exempted but is no longer a public route — remove the entry `
      + 'rather than leaving a standing permission nobody re-checked');
  }
});
