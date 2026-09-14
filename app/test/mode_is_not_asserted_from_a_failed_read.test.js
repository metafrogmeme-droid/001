'use strict';
/**
 * PAPER is a claim about which account the reader is trading, and three
 * surfaces printed it for a payload nobody could fetch.
 *
 * `routes/portfolio.js`'s `dbFallback` returns the last equity snapshot and
 * the open rows this database still holds — a memory, not a reading — and
 * every caller stamped `mode: 'PAPER'` on the way out. A LIVE user whose
 * gateway blipped had their dashboard relabelled PAPER, which is the one
 * label that says "none of this is real money".
 *
 * `stale: true` already travelled with it. `updateModeChip` read it and
 * printed "MODE ?"; the hub strip's Mode tile and mission control's Mode chip
 * did not, so the same payload said PAPER in two places and unknown in a
 * third. `readMode` is the one reading now, and the payload no longer asserts
 * the mode it could not read.
 *
 * The exception is a deployment with NO gateway configured: there is no bot
 * to have an account on, so PAPER there is the deployment's own state rather
 * than a guess about a reading that failed.
 */
process.env.JWT_SECRET = process.env.JWT_SECRET || 'j'.repeat(64);
process.env.BOT_USER_ID = '999';

const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const APP = path.join(__dirname, '..');
const SRC = fs.readFileSync(path.join(APP, 'public', 'js', 'dashboard.js'), 'utf8');

// ── the client's one reading ───────────────────────────────────────────────

function loadReadMode() {
  const start = SRC.indexOf('  function readMode(pf) {');
  assert.ok(start > 0, 'readMode is defined');
  const end = SRC.indexOf('\n  }\n', start) + 4;
  return vm.runInNewContext(SRC.slice(start, end) + '\nreadMode;');
}
const readMode = loadReadMode();

test('a reading that reached the bot is reported as it came', () => {
  assert.equal(readMode({ mode: 'LIVE', stale: false }), 'LIVE');
  assert.equal(readMode({ mode: 'MIXED', stale: false }), 'LIVE');
  assert.equal(readMode({ mode: 'PAPER', stale: false }), 'PAPER');
});

test('a stale payload has no mode to report', () => {
  assert.equal(readMode({ mode: 'PAPER', stale: true }), null);
  assert.equal(readMode({ mode: 'LIVE', stale: true }), null);
  assert.equal(readMode({ mode: null, stale: true }), null);
  assert.equal(readMode(null), null);
  assert.equal(readMode({}), null, 'an absent mode is unknown, never PAPER');
});

test('the operator sync path is a real reading even when it is stale', () => {
  // `source: 'sync'` is the operator's own feed: `stale` there means the last
  // push is old, not that nobody could be asked.
  assert.equal(readMode({ mode: 'LIVE', stale: true, source: 'sync' }), 'LIVE');
});

test('all three renderers read it — none rebuilds the claim', () => {
  const hub = SRC.slice(SRC.indexOf("tile('Mode',"), SRC.indexOf("tile('Mode',") + 200);
  assert.match(hub, /_mode === null \? \(pf \? 'MODE \?'/, 'the hub strip prints unknown as unknown');
  const mc = SRC.slice(SRC.indexOf("const mode = readMode(pf)"), SRC.indexOf("const mode = readMode(pf)") + 900);
  assert.match(mc, /mode \|\| 'MODE \?'/, 'mission control prints unknown as unknown');
  assert.ok(!/pf\.mode === 'LIVE' \|\| pf\.mode === 'MIXED'/.test(SRC.replace(/function readMode[\s\S]*?\n  \}\n/, '')),
    'no renderer re-derives LIVE from the raw payload');
});

// ── the payload ───────────────────────────────────────────────────────────
// The PER-USER path: BOT_USER_ID is somebody else, the caller is user 7 and
// the mode comes back through the gateway. One harness for every suite that
// drives this route — see helpers/portfolio_route.js.
const { get: getRoute } = require('./helpers/portfolio_route');
const get = ({ configured = true, status = 200, throws = false } = {}) =>
  getRoute({ operator: false, equity: '1234.50', gateway: { configured, status, throws, data: {} } });

test('a gateway that answered non-200 leaves the mode unknown', async () => {
  const { body } = await get({ status: 503 });
  assert.strictEqual(body.mode, null, 'PAPER was asserted from a failed read');
  assert.strictEqual(body.stale, true, 'and the payload says it is a memory');
  assert.strictEqual(body.equity, 1234.5, 'the last known equity still travels');
});

test('a gateway that threw leaves the mode unknown', async () => {
  const { body } = await get({ throws: true });
  assert.strictEqual(body.mode, null);
  assert.strictEqual(body.stale, true);
});

test('a deployment with no gateway at all IS paper, and says so', async () => {
  // RED HERRING: this is the one branch where PAPER is a fact about the
  // deployment rather than a guess about an account nobody could read.
  const { body } = await get({ configured: false });
  assert.strictEqual(body.mode, 'PAPER');
  assert.strictEqual(body.stale, false);
  assert.strictEqual(body.unconfigured, true);
});

test('and the client prints that one as PAPER', () => {
  assert.equal(readMode({ mode: 'PAPER', stale: false, unconfigured: true }), 'PAPER');
});
