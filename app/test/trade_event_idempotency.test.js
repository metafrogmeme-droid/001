'use strict';
// The bot could not retry a trade sync, and that was correct until now.
//
// POST /api/bot/sync/trade-event inserts unconditionally:
//
//     INSERT INTO trades (...)                    -- on 'open'
//     DELETE ... WHERE status='OPEN' LIMIT 1      -- on 'close'
//     INSERT INTO trades (... status='CLOSED')
//
// so a retry of a delivery whose RESPONSE was merely lost appends a second
// trade: a phantom position on 'open', and on 'close' it consumes another
// open row and manufactures a closed trade beside it — corrupting the P&L
// history with a trade that never happened.
//
//     LOSING AN EVENT IS BAD. INVENTING ONE IS WORSE.
//
// The bot now mints one event_id per logical event and replays it on every
// retry; this guard is what makes that safe. Two properties matter and both
// are asserted below:
//
//   1. a replayed id writes NOTHING and still answers ok — a correct client
//      told "failed" would retry forever over work already done;
//   2. the guard is bounded, so a long-running container cannot grow a set
//      of every trade id it has ever seen.
//
// Scope is deliberately narrow and is documented as such in the route: this
// covers the retry window (seconds), per-container, in memory. It is not a
// durable idempotency ledger, and the code does not claim to be one.

const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

const src = fs.readFileSync(
  path.join(__dirname, '..', 'routes', 'sync.js'), 'utf8');

// Lift the pure guard out of the route module (which needs a DB pool) the
// same way the rest of this suite tests route internals.
function loadGuard() {
  const start = src.indexOf('const SEEN_EVENTS');
  const end = src.indexOf("router.post('/trade-event'");
  assert.ok(start > 0 && end > start, 'dedupe guard must exist above the route');
  const mod = { exports: {} };
  // eslint-disable-next-line no-new-func
  new Function('module', `${src.slice(start, end)}\nmodule.exports = { _seenEvent, SEEN_EVENTS, SEEN_EVENT_MAX };`)(mod);
  return mod.exports;
}

test('a replayed event id is recognised', () => {
  const { _seenEvent } = loadGuard();
  assert.strictEqual(_seenEvent('abc123'), false, 'first delivery is new');
  assert.strictEqual(_seenEvent('abc123'), true, 'the retry is a replay');
});

test('distinct events are not conflated', () => {
  const { _seenEvent } = loadGuard();
  assert.strictEqual(_seenEvent('one'), false);
  assert.strictEqual(_seenEvent('two'), false,
    'two genuine trades must both be recorded');
});

test('a missing id is never treated as a replay', () => {
  const { _seenEvent } = loadGuard();
  // An older bot, or any caller that has not opted in, sends no id. Treating
  // absent as "already seen" would silently DROP real trades — trading a
  // duplicate-write bug for a data-loss one.
  for (const empty of [undefined, null, '', 0]) {
    assert.strictEqual(_seenEvent(empty), false, `empty id: ${String(empty)}`);
  }
});

test('the guard is bounded', () => {
  const { _seenEvent, SEEN_EVENTS, SEEN_EVENT_MAX } = loadGuard();
  for (let i = 0; i < SEEN_EVENT_MAX + 500; i++) _seenEvent(`id-${i}`);
  assert.ok(SEEN_EVENTS.size <= SEEN_EVENT_MAX,
    `set grew to ${SEEN_EVENTS.size}; a long-running container must not `
    + 'accumulate every id it has ever seen');
});

test('eviction is oldest-first, so a fresh retry still dedupes', () => {
  const { _seenEvent, SEEN_EVENTS, SEEN_EVENT_MAX } = loadGuard();
  _seenEvent('oldest');
  for (let i = 0; i < SEEN_EVENT_MAX + 100; i++) _seenEvent(`filler-${i}`);
  assert.ok(!SEEN_EVENTS.has('oldest'), 'the oldest entry goes first');
  const newest = `filler-${SEEN_EVENT_MAX + 99}`;
  assert.ok(SEEN_EVENTS.has(newest),
    'the most recent ids must survive — those are the ones a retry replays');
});

test('a duplicate answers ok, not an error', () => {
  // A correct client told "failed" retries. Over work already done, that is
  // an infinite loop the server caused.
  const i = src.indexOf('_seenEvent(event_id)');
  assert.ok(i > 0, 'the route must consult the guard');
  const window = src.slice(i, i + 200);
  assert.match(window, /ok:\s*true/);
  assert.match(window, /duplicate:\s*true/,
    'the response should still be honest that nothing was written');
});

test('the guard runs before any write', () => {
  const routeStart = src.indexOf("router.post('/trade-event'");
  const guard = src.indexOf('_seenEvent(event_id)', routeStart);
  const insertInRoute = src.indexOf('INSERT INTO trades', routeStart);
  const deleteInRoute = src.indexOf('DELETE FROM trades', routeStart);
  assert.ok(guard > routeStart, 'the guard must be inside the route');
  assert.ok(guard < insertInRoute,
    'a dedupe check after the INSERT prevents nothing');
  assert.ok(guard < deleteInRoute,
    'the close branch DELETEs an OPEN row before inserting; a replay that '
    + 'reaches it consumes a position that is still live');
});

test('the route documents that the scope is the retry window', () => {
  // The claim being made must match what the code does. This is in-process
  // and bounded; presenting it as durable idempotency would be the defect
  // one level up.
  const i = src.indexOf('const SEEN_EVENTS');
  const preamble = src.slice(Math.max(0, i - 1400), i);
  assert.match(preamble, /retry window/i);
  assert.match(preamble, /not a durable idempotency ledger/i);
});
