'use strict';
/**
 * PAPER is a claim about which account the reader is trading, and the
 * OPERATOR path manufactured it from six failed reads.
 *
 * `mode_is_not_asserted_from_a_failed_read.test.js` fixed the per-user path
 * and its own fixture sets BOT_USER_ID='999' with req.user.user_id=7 -- it
 * routes deliberately AROUND `operatorPortfolio()`, and its line asserting
 * `readMode({mode:'LIVE', stale:true, source:'sync'}) === 'LIVE'` carves the
 * sync path OUT on purpose, because a stale operator push is still a real
 * reading. Both decisions are right, and together they left the one account
 * with real money uncovered.
 *
 * Driven through the real route with a stub pool, `operatorPortfolio()` did
 * `let live = false` ... `catch (e) { mode stays PAPER }` (a comment that
 * quoted the closing token here would end this docstring early) ...
 * `mode: live ? 'LIVE' : 'PAPER'`, so SEVEN scenarios produced one payload:
 *
 *   1 no scan_cache row (cold start)      -> mode "PAPER"
 *   2 row present, scan_json NULL         -> mode "PAPER"
 *   3 parses, no circuit_breaker key      -> mode "PAPER"
 *   4 circuit_breaker with no live_mode   -> mode "PAPER"
 *   5 malformed scan_json (parse throws)  -> mode "PAPER"
 *   6 the SELECT itself throws            -> mode "PAPER"
 *   7 A REAL READING of live_mode:false   -> mode "PAPER"
 *
 * Row 7 is the whole point: no field in the payload separated "read it, it
 * said paper" from "never read it". And the client's stale escape hatch is
 * `pf.stale && pf.source !== 'sync'` while this path hardcodes
 * `source: 'sync'`, so a two-hour-old snapshot still rendered a confident
 * PAPER chip. Six ways to be told your live account is paper.
 *
 * The other half: `stale: !fresh` described the SNAPSHOT row's age while
 * `equity` may have been replaced by the scan cache's seconds-old reading,
 * so a fresh number went out as `stale: true` and the client printed "bot
 * offline -- last known" over it.
 *
 * Scenarios 3 and 4 need a circuit_breaker payload the current bot does not
 * emit (scan_skill.py always writes live_mode), so they are reachable only
 * under bot/web version skew or a partial write; 1, 2, 5 and 6 are live.
 */
process.env.JWT_SECRET = process.env.JWT_SECRET || 'j'.repeat(64);

const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const APP = path.join(__dirname, '..');
const SRC = fs.readFileSync(path.join(APP, 'public', 'js', 'dashboard.js'), 'utf8');
// One harness for every suite that drives this route — see helpers/portfolio_route.js.
const { get: getRoute, scanRow, FRESH, OLD } = require('./helpers/portfolio_route');

/** The OPERATOR path (user 1 === BOT_USER_ID). */
const get = (opts = {}) => getRoute({ operator: true, ...opts });

// ── the six unread cases ───────────────────────────────────────────────────

const UNREAD = [
  ['1 no scan_cache row at all',         { scan: [] }],
  ['2 row present, scan_json NULL',      { scan: [{ scan_json: null, updated_at: FRESH() }] }],
  ['3 parses, no circuit_breaker key',   { scan: [{ scan_json: '{"regime":"x"}', updated_at: FRESH() }] }],
  ['4 circuit_breaker with no live_mode', { scan: scanRow({ equity: 9000 }) }],
  ['5 malformed scan_json',              { scan: [{ scan_json: '{not json', updated_at: FRESH() }] }],
  ['6 the SELECT itself throws',         { scan: () => { throw new Error('pool dead'); } }],
];

for (const [name, opts] of UNREAD) {
  test(`${name}: the mode is NOT READ and the payload says so`, async () => {
    const { status, body } = await get(opts);
    assert.strictEqual(status, 200);
    assert.strictEqual(body.mode, null, 'PAPER was asserted from a failed read');
    assert.strictEqual(body.source, 'sync');
    // The memory still travels, and is fresh because the snapshot is.
    assert.strictEqual(body.equity, 8200.5);
    assert.strictEqual(body.stale, false);
    assert.strictEqual(body.live_unavailable, false);
  });
}

test('the six unread payloads are identical to each other', async () => {
  const bodies = [];
  for (const [, opts] of UNREAD) bodies.push((await get(opts)).body);
  // `as_of.equity` is the snapshot's timestamp, minted per request by the
  // harness, so it is stripped; `provenance` is NOT — six unread modes must
  // name the same sources, or one of them has been read as something else.
  const strip = (b) => JSON.stringify({ ...b, open_positions: undefined, as_of: undefined });
  for (const b of bodies.slice(1)) assert.strictEqual(strip(b), strip(bodies[0]));
  for (const b of bodies) assert.deepStrictEqual(b.provenance,
    { equity: 'snapshot', open_positions: 'sync_rows', daily_pnl: 'absent' });
});

// ── the two READ cases, which must stay readings ───────────────────────────

test('7 a real reading of live_mode:false is PAPER, and is no longer confusable with unread', async () => {
  const { body } = await get({ scan: scanRow({ live_mode: false }) });
  assert.strictEqual(body.mode, 'PAPER');
  // THE DISTINCTION THE OLD PAYLOAD COULD NOT MAKE: unread is null, read is
  // a word. Before this, both were the string 'PAPER'.
  const unread = (await get({ scan: [] })).body;
  assert.notStrictEqual(body.mode, unread.mode);
});

test('8 a real reading of live_mode:true is LIVE', async () => {
  const { body } = await get({ scan: scanRow({ live_mode: true, equity: 8300, live_unavailable: false }) });
  assert.strictEqual(body.mode, 'LIVE');
  assert.strictEqual(body.equity, 8300);
});

test('a live_mode that is not a boolean is not a reading of it', async () => {
  // The contract is a Python bool from one producer. A string or number in
  // that slot is not evidence either way, and the safe direction is unknown.
  for (const junk of ['true', 1, 'PAPER', null]) {
    const { body } = await get({ scan: scanRow({ live_mode: junk }) });
    assert.strictEqual(body.mode, null, `live_mode=${JSON.stringify(junk)} read as a mode`);
  }
});

// ── stale describes the figure that was PUBLISHED ──────────────────────────

test('a fresh scan-cache equity is not called stale because the snapshot beside it is old', async () => {
  const { body } = await get({
    scan: scanRow({ live_mode: true, equity: 8300, live_unavailable: false }),
    snapAt: OLD,
  });
  assert.strictEqual(body.mode, 'LIVE');
  assert.strictEqual(body.equity, 8300, 'the seconds-old scan reading is what went out');
  assert.strictEqual(body.stale, false, 'a false caution about a measured number is still a false claim');
});

test('an old snapshot published as the memory IS stale, and says so', async () => {
  const { body } = await get({ scan: [], snapAt: OLD });
  assert.strictEqual(body.mode, null);
  assert.strictEqual(body.equity, 8200.5);
  assert.strictEqual(body.stale, true);
});

test('a live account whose balance the bot could not read says so, rather than PAPER or a number', async () => {
  const { body } = await get({
    scan: scanRow({ live_mode: true, live_unavailable: true }),
    snapAt: OLD,
  });
  assert.strictEqual(body.mode, 'LIVE');
  assert.strictEqual(body.equity, null);
  assert.strictEqual(body.live_unavailable, true);
});

test('an unread mode never takes the LIVE branch that writes through to the equity curve', async () => {
  let inserted = 0;
  await get({
    scan: [], snapAt: OLD, equity: '100.00',
    intercept: (sql) => (/INSERT INTO equity_snapshots/.test(sql) ? (inserted += 1, [{ affectedRows: 1 }]) : undefined),
  });
  assert.strictEqual(inserted, 0, 'a mode nobody read must not drive a write');
});

// ── the client prints the operator's unread mode as unknown ────────────────

function loadReadMode() {
  const start = SRC.indexOf('  function readMode(pf) {');
  assert.ok(start > 0, 'readMode is defined');
  const end = SRC.indexOf('\n  }\n', start) + 4;
  return vm.runInNewContext(SRC.slice(start, end) + '\nreadMode;');
}

test("the client renders the operator's exact unread payload as MODE ?", () => {
  const readMode = loadReadMode();
  // source:'sync' + stale:false is precisely the shape the six unread cases
  // publish, and it is the shape the per-user guard's stale escape hatch
  // deliberately does not catch. Only `mode: null` reaches unknown here.
  assert.strictEqual(readMode({ mode: null, source: 'sync', stale: false }), null);
  assert.strictEqual(readMode({ mode: null, source: 'sync', stale: true }), null);
  // And a real reading on the same path is still a reading.
  assert.strictEqual(readMode({ mode: 'PAPER', source: 'sync', stale: false }), 'PAPER');
  assert.strictEqual(readMode({ mode: 'LIVE', source: 'sync', stale: true }), 'LIVE');
});

test('the LIVE ACCOUNT badge is not shown for a mode nobody read', () => {
  // dashboard.js gates the badge on `pf.source === 'sync' && pf.mode === 'LIVE'`.
  // With mode null that is false -- honest, since nothing is claimed either
  // way -- and it must not have been rewritten to treat null as live.
  const at = SRC.indexOf("chip--live\">LIVE ACCOUNT");
  assert.ok(at > 0, 'the badge exists');
  const guard = SRC.slice(SRC.lastIndexOf('\n', at - 200), at);
  assert.match(guard, /pf\.mode === 'LIVE'/, 'the badge requires a read LIVE, not a non-PAPER');
});
