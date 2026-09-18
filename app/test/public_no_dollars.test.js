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
  // ONE entry per file. `track.js` was written TWICE here — `['equity']` with
  // one reason and `['equity', 'pnl']` with another — and in a JS object
  // literal the last key wins, so the first entry's reason had been orphaned
  // prose above a line that did nothing. Both reasons are true and both are
  // below; `no file is exempted twice` keeps it that way.
  //
  // equity: published INDEXED TO 100, never a dollar figure. Verified.
  // pnl: an internal argument to profitFactor() — `trades.map(t => ({ pnl:
  //   t.pnl }))` — not a key in the JSON response, which emits pnl_pct and
  //   profit_factor only.
  'track.js': ['equity', 'pnl'],
  // The frame card's `balance` is a percentage. Verified.
  'frame.js': ['balance'],
  // `sync.js` WAS exempted here, as "not a public READ surface: the bot's
  // inbound sync, gated by botAuth". That was true, and the entry existed
  // only because the FILE-level detector called the file public — it spells
  // no `authMiddleware`. The route-level drive reads `botAuth` off the
  // dispatch chain and agrees with the comment, so the exemption became
  // stale on its own terms and `every exemption names a file that is still
  // public` said so. It is deleted rather than kept: a standing permission
  // over a file nothing checks is how this table would rot.
};

const { codeOnly } = require('./helpers/code_only');
const PUB = require('./helpers/public_routes');

/**
 * WHICH FILES ARE PUBLIC — a DRIVE now, not `!src.includes('authMiddleware')`.
 *
 * That test was FILE-level, so a file gating ONE route left the public set
 * entirely and every unauthenticated route in it went too. `reports.js` gates
 * `/yield` and serves `GET /` to anyone — and that route was publishing the
 * operator's realized `net_pnl` and `total_fees`. Driven position-aware over
 * the express dispatch chain, SEVENTEEN unauthenticated routes were invisible
 * here for exactly that reason, across seven files. The public set is 40 files
 * rather than 35; `sync.js` and `stream.js` left it (botAuth, and a module
 * that exports no Router — NOT-FOUND IS NOT NO-GATE).
 *
 * This is `tests/command_gates.py`'s lesson one runtime over: COVERAGE OF A
 * SPELLING IS NOT COVERAGE OF THE GUARD.
 */
function publicRouteFiles() { return PUB.publicRouteFiles(); }

/**
 * Files holding BOTH public and authenticated routes. The key scan below
 * reads a whole FILE, so for these it is a SUPERSET of what the public half
 * emits — it can only over-accuse, which is the safe direction, but an
 * over-accusation left standing is how a guard gets switched off.
 *
 * Each entry names the public routes as the drive reports them TODAY and the
 * verified reason. A public route added to one of these files makes its list
 * stale and fails `every mixed entry still describes its file`, so the next
 * reader re-checks rather than inheriting a permission.
 *
 * A HANDLER-BOUNDED SCAN WAS THE OBVIOUS FIX AND IS WORSE. `layer.route`
 * carries the handler function, so its source can be read exactly — and
 * arena's `/leaderboard` handler is 232 characters that call
 * `computeLeaderboard()`. Bounding the scan there ACQUITS by omission, which
 * is the quiet direction; a file-level superset does not.
 */
const MIXED = {
  'arena.js': {
    routes: ['get /leaderboard', 'get /tape', 'get /trader/:handle',
      'get /season', 'get /seasons'],
    keys: ['pnl', 'equity', 'balance', 'margin'],
    // Driven: `buildTraderCard({handle, balance, positions, marks, trades})`
    // emits return_pct / ret_pct / win_rate_pct / counts and no dollar — the
    // balance is the RATIO'S INPUT, the agent_record.js shape this file's own
    // header already names. The other three keys are on /account, /open,
    // /close and /exits, all behind authMiddleware. arena.js's header states
    // the same split: "virtual balances appear solely on the owner's private
    // account view".
    why: 'the money keys are on the authMiddleware half; the public board '
       + 'emits percent and counts, verified by driving buildTraderCard',
  },
  'learn.js': {
    routes: ['get /lessons', 'get /lessons/:slug'],
    keys: ['pnl'],
    // `tradesOfDay(userId, day)` feeds /diary, which sits below this file's
    // `router.use(authMiddleware)`. The two public routes serve static lesson
    // content and touch no account.
    why: 'pnl is in tradesOfDay, read only by /diary below the use(authMiddleware) line',
  },
};

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
    const allowed = [...(EXEMPT[f] || []), ...((MIXED[f] || {}).keys || [])];
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

/**
 * The source of ONE tool, bounded by the next tool key rather than a fixed
 * width.
 *
 * Both assertions below used a magic window (2600 / 500 chars). Adding six
 * lines to get_track_record pushed `profit_factor` to offset 2990 and the
 * window stopped covering the return object — so a §4 guard silently became a
 * test of where a string sits rather than whether the payload has it. The
 * failure at least surfaced; the dangerous direction is the same drift making
 * a banned key fall OUTSIDE the window and stop being checked.
 */
function toolBlock(src, name) {
  const i = src.indexOf(`${name}: {`);
  assert.ok(i > 0, `${name} not found in mcp.js`);
  const rest = src.slice(i);
  const next = rest.slice(name.length).match(/\n {2}[a-z_][a-z0-9_]*:\s*\{/);
  return next ? rest.slice(0, name.length + next.index) : rest;
}

test('mcp get_track_record publishes no dollar amount', () => {
  const src = codeOnly(fs.readFileSync(path.join(ROUTES, 'mcp.js'), 'utf8'));
  const block = toolBlock(src, 'get_track_record');
  assert.ok(!/net_pnl_usd\s*:/.test(block), 'net_pnl_usd is a dollar figure');
  assert.ok(/profit_factor\s*:/.test(block),
    'the ratio must survive — it carries the signal the dollar figure did');
  assert.ok(/win_rate_pct\s*:/.test(block));
});

test('mcp recent trades report an outcome, not an amount', () => {
  const src = codeOnly(fs.readFileSync(path.join(ROUTES, 'mcp.js'), 'utf8'));
  const block = toolBlock(src, 'get_track_record');
  assert.ok(/recent_trades\s*:/.test(block));
  assert.ok(/result\s*:/.test(block));
  assert.ok(!/\bpnl\s*:/.test(block), 'a per-trade dollar figure');

  // A scratch is not a losing trade — and a close nobody priced is not a
  // scratch. This used to assert the literal `'flat'` appeared here; the
  // four-way mapping now lives in track.js `outcomeOf`, shared with the public
  // page precisely so these two surfaces cannot disagree again (audit M9).
  // What must hold HERE is that this handler does not re-derive it.
  assert.ok(/outcomeOf\(/.test(block),
    'the outcome is derived locally again; track.js and mcp.js drifted apart '
    + 'once already and published different verdicts for the same trade');
  assert.ok(!/\|\|\s*0\s*\)\s*[<>]/.test(block),
    'an unpriced close is being compared as if it were a measured zero');
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

test('every mixed entry still describes its file', () => {
  // A stale entry is how this table would rot into permission: a public route
  // added to a mixed file is covered by its key list without anybody looking.
  const pub = PUB.publicRoutes();
  for (const [file, entry] of Object.entries(MIXED)) {
    const live = pub.filter((r) => r.file === file)
      .map((r) => `${r.methods.join('|')} ${r.path}`).sort();
    assert.ok(live.length, `${file} has no public route any more — delete the entry`);
    assert.deepStrictEqual(live, [...entry.routes].sort(),
      `${file}'s public routes moved. The key exemption below was verified `
      + 'against the old list and has not been re-checked against this one:\n'
      + `  was: ${entry.routes.join(', ')}\n  now: ${live.join(', ')}`);
    assert.ok(entry.why && entry.why.length > 20, `${file} needs a reason`);
    // A mixed file must actually be mixed — one whose routes are ALL public
    // gets the strict scan, not a permission.
    const all = PUB.routes().filter((r) => r.file === file);
    assert.ok(all.length > live.length,
      `${file} is wholly public now — it must take the strict scan`);
  }
});

/**
 * THE VOCABULARY'S UNKNOWN CASE HAS TO BE LOUD.
 *
 * FORBIDDEN is hand-written, and until this test a money field it did not
 * name was simply unchecked — the surface read clean because nobody had
 * thought of the word. `tests/command_gates.py` has a hand-written vocabulary
 * too and it is SAFE, for one reason its own header states: a spelling it
 * does not know reads as `none`, which demands a reason. This one read as
 * nothing at all.
 *
 * So a key that LOOKS like account money and is not in FORBIDDEN must be
 * named here with why it is not — a market fact, a stated constant, a
 * caller's own input. That is the `none`-needs-a-reason rule, one vocabulary
 * over, and it is what `earned_usd` would have tripped.
 */
const MONEY_SHAPED = /(^|_)(usd|usdt)$|^usd_|_usd_/;

const NOT_ACCOUNT_MONEY = {
  // §4 permits market facts outright.
  market_cap_usd: 'a market fact — the asset\'s cap, not an account balance',
  mcap_usd: 'a market fact, same as market_cap_usd',
  vol_usd: 'a market fact — traded volume',
  volume_24h_usd: 'a market fact — traded volume',
  // The caller's own number, echoed back under a label.
  stake_usd: 'the CALLER\'s own hypothetical stake, emitted beside '
    + 'hypothetical:true — arithmetic on their input discloses nothing about '
    + 'RUNECLAW\'s capital (pinned by its own test below)',
  // `notional_usd` is NOT here, and the mutation round is what said so. It is
  // in FORBIDDEN, and this check skips a forbidden key first — so declaring it
  // safe as well was a dead line that a mutation could delete with nothing
  // failing. The same "declared twice, one wins" shape as the track.js
  // duplicate above, and the two entries said opposite things.
  //
  // FORBIDDEN is right for a ROUTE file: a route emitting `notional_usd` is
  // emitting a position's notional. The one legitimate `notional_usd` in this
  // product is `PAPER_NOTIONAL_USD` on the arb payload — a published constant
  // the BOT emits, which `tests/test_the_public_report_carries_no_dollar.py`
  // declares safe with its reason, on the side where that key exists.
  // These two are on authenticated routes only; listed because the scan below
  // reads whole files.
  size_usd: 'meme.js and trades.js — both wholly behind use(authMiddleware)',
  total_usd: 'wallet.js — wholly behind use(authMiddleware)',
};

test('a money-shaped key the vocabulary does not name is declared, not silent', () => {
  const unnamed = [];
  const forbidden = new Set(FORBIDDEN);
  for (const f of fs.readdirSync(ROUTES).filter((x) => x.endsWith('.js'))) {
    const src = codeOnly(fs.readFileSync(path.join(ROUTES, f), 'utf8'));
    for (const m of src.matchAll(/(^|[{,\s])['"`]?([a-z][a-z0-9_]*)['"`]?\s*:/gm)) {
      const k = m[2];
      if (forbidden.has(k) || NOT_ACCOUNT_MONEY[k]) continue;
      if (MONEY_SHAPED.test(k)) unnamed.push(`${f} -> ${k}`);
    }
  }
  assert.deepStrictEqual([...new Set(unnamed)].sort(), [],
    'a key that looks like account money is neither forbidden nor declared '
    + 'safe. Add it to FORBIDDEN, or to NOT_ACCOUNT_MONEY with the reason:\n  '
    + [...new Set(unnamed)].sort().join('\n  '));
});

test('the unknown-key rule can actually fail', () => {
  // The property a guard needs before any other property matters.
  for (const k of ['earned_usd', 'carry_usd', 'usd_balance', 'fees_usd']) {
    assert.ok(MONEY_SHAPED.test(k), `${k} must read as money-shaped`);
    assert.ok(!NOT_ACCOUNT_MONEY[k], `${k} must not already be declared safe`);
  }
  // And it must not fire on things that merely contain the letters.
  for (const k of ['used', 'user_id', 'usage', 'unused', 'status']) {
    assert.ok(!MONEY_SHAPED.test(k), `${k} is not a money field`);
  }
});

test('every declared-safe key names a reason', () => {
  for (const [k, why] of Object.entries(NOT_ACCOUNT_MONEY)) {
    assert.ok(typeof why === 'string' && why.length > 20,
      `${k} is declared safe with no reason — an unexplained entry is the `
      + 'hole this file exists to close');
  }
});

test('the public set is a DRIVE, and it reports what it could not read', () => {
  // NOT-FOUND IS NOT NO-GATE: a module that exports no Router must never be
  // folded into "public", and it must not vanish silently either.
  //
  // THE FIRST DRAFT LOOPED OVER `unreadable()` AND ASSERTED NOTHING WHEN IT
  // WAS EMPTY — so the mutation that folds an unreadable router into the
  // public set survived a green suite, because it emptied the very list the
  // loop reads. A loop over a possibly-empty list is not an assertion. The
  // one router in this tree that exports a plain object is named.
  const unreadable = PUB.unreadable();
  const byFile = new Map(unreadable.map((u) => [u.file, u.unreadable]));
  assert.ok(byFile.has('stream.js'),
    'stream.js exports a plain object, not an express Router. If that changed, '
    + 'name whichever router is unreadable now — an empty list here means this '
    + 'test checks nothing');
  assert.match(byFile.get('stream.js'), /exports no express Router/);
  for (const u of unreadable) {
    assert.ok(/exports no express Router|load failed/.test(u.unreadable), u.unreadable);
    assert.ok(!publicRouteFiles().includes(u.file),
      `${u.file} could not be read and must not be reported as public`);
    assert.ok(!PUB.routes().some((r) => r.file === u.file),
      `${u.file} is unreadable and must contribute no route at all`);
  }
  // The drive has to be non-trivial, same reason as the set-size check above.
  assert.ok(PUB.routes().length > 200,
    `only ${PUB.routes().length} routes driven — the walk is probably broken`);
});

test('the auth vocabulary names only middleware this tree has', () => {
  // A name nothing has is not protection; it is a claim that there is a check.
  // The first draft listed `requireAdmin` and `adminMiddleware`, and neither
  // exists anywhere. Driven against the chains the walk actually reports.
  const seen = new Set();
  for (const r of PUB.routes()) r.chain.forEach((g) => seen.add(g));
  for (const name of PUB.AUTH_FAMILY) {
    assert.ok(seen.has(name),
      `${name} is in AUTH_FAMILY and appears in no dispatch chain — remove it `
      + 'rather than leaving a row no input can reach');
  }
});

test('the rate limiter is identified, and is not mistaken for a gate', () => {
  // The one tag that is load-bearing: `rateLimit({...})` returns an anonymous
  // closure, so without it the walk cannot tell a limiter from anything else.
  const chains = PUB.routes().flatMap((r) => r.chain);
  assert.ok(chains.includes('rateLimit'),
    'the rateLimit factory is not being tagged — every limiter reads as <anon>');
  assert.ok(!PUB.AUTH_FAMILY.has('rateLimit'),
    'a limiter answers how OFTEN, never WHO');
  // And a limited-but-unauthenticated route is still public.
  const limitedOnly = PUB.publicRoutes().filter((r) => r.chain.includes('rateLimit'));
  assert.ok(limitedOnly.length > 5,
    `${limitedOnly.length} rate-limited public routes — a limiter must not `
    + 'remove a route from the public set');
});

test('the route-level set sees a public route in a file that gates another', () => {
  // The defect this replaced the file-level detector for. reports.js gates
  // /yield and serves GET / to anyone; the old check excluded the whole file.
  assert.ok(publicRouteFiles().includes('reports.js'),
    'reports.js serves GET / with no auth — it is a public route file');
  const legacy = fs.readFileSync(path.join(ROUTES, 'reports.js'), 'utf8')
    .includes('authMiddleware');
  assert.ok(legacy,
    'reports.js must still mention authMiddleware, or this test no longer '
    + 'demonstrates the difference between the two detectors');
});

test('no file is exempted twice', () => {
  // A JS object literal takes the LAST key, so a file written twice here has
  // one live entry and one comment above a dead line — which is how
  // `track.js` came to carry two reasons and use one. Read the source rather
  // than the parsed object, because the parsed object cannot see it.
  const src = fs.readFileSync(__filename, 'utf8');
  const block = src.slice(src.indexOf('const EXEMPT = {'), src.indexOf('const { codeOnly }'));
  const seen = new Set();
  const dupes = [];
  for (const m of block.matchAll(/^\s*'([\w.-]+\.js)':\s*\[/gm)) {
    if (seen.has(m[1])) dupes.push(m[1]);
    seen.add(m[1]);
  }
  assert.deepStrictEqual(dupes, [],
    'exempted twice — the second wins and the first reason is dead prose: '
    + dupes.join(', '));
});

test('the mixed table fails when a public route is added to one of its files', () => {
  // The MUTATION of this rule is unreachable while the table is honest — the
  // assertion never fires either way — so it is driven from the side that IS
  // reachable: the table going stale. This re-implements the rule against a
  // planted entry rather than weakening the real one.
  const live = PUB.publicRoutes().filter((r) => r.file === 'arena.js')
    .map((r) => `${r.methods.join('|')} ${r.path}`).sort();
  assert.ok(live.length >= 5, 'arena.js must still have public board routes');

  const stale = { routes: live.slice(0, -1), keys: ['pnl'], why: 'x'.repeat(30) };
  assert.throws(
    () => assert.deepStrictEqual(live, [...stale.routes].sort()),
    'a mixed entry one route short of reality must fail');

  const fresh = { routes: [...live], keys: ['pnl'], why: 'x'.repeat(30) };
  assert.doesNotThrow(() => assert.deepStrictEqual(live, [...fresh.routes].sort()));
  // And the real table is fresh, which is what the sibling test asserts.
  assert.deepStrictEqual(live, [...MIXED['arena.js'].routes].sort());
});

test('the unknown-key rule fires on a money key nobody declared', () => {
  // Same reasoning: `if (false && ...)` is unreachable while every money-shaped
  // key is declared. Drive the rule over a planted source instead.
  const forbidden = new Set(FORBIDDEN);
  const scan = (src) => {
    const out = [];
    for (const m of src.matchAll(/(^|[{,\s])['"`]?([a-z][a-z0-9_]*)['"`]?\s*:/gm)) {
      const k = m[2];
      if (forbidden.has(k) || NOT_ACCOUNT_MONEY[k]) continue;
      if (MONEY_SHAPED.test(k)) out.push(k);
    }
    return out;
  };
  assert.deepStrictEqual(scan('res.json({ base: "BTC", earned_usd: 1.5 });'),
    ['earned_usd'], 'an undeclared money key must be reported');
  assert.deepStrictEqual(scan('res.json({ notional_usd: 1000, held_hours: 4 });'),
    [], 'a declared-safe key and an ordinary one must not be');
  assert.deepStrictEqual(scan('const used = 1; res.json({ status: "ok" });'), []);
});

test('no key is both forbidden and declared safe', () => {
  // The check skips a forbidden key BEFORE consulting the declarations, so a
  // key in both lists has one live entry and one that no input can reach —
  // and the two say opposite things. The round found `notional_usd` that way.
  const both = FORBIDDEN.filter((k) => NOT_ACCOUNT_MONEY[k]);
  assert.deepStrictEqual(both, [],
    'in both lists — the declaration is unreachable and contradicts the ban: '
    + both.join(', '));
});
