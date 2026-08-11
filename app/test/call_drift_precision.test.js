'use strict';
/**
 * The verification page cried wolf on its own flagship claim.
 *
 * A real receipt, from production, rendered this — directly beneath a green
 * "Seal verified in your browser":
 *
 *     ⚠️ The live record DIFFERS from what was sealed:
 *        opened_at: sealed 2026-08-10T14:40:06.293Z → now 2026-08-10T14:40:06.000Z
 *
 * Nothing had been tampered with. `arena_trades.opened_at` is a MySQL
 * TIMESTAMP with no fractional-seconds precision (app/db.js), so it stores
 * whole seconds; the seal commits the millisecond value from toISOString().
 * The round-trip drops up to 999ms and the drift check demanded equality to
 * within 1e-9, so it fired on essentially every arena trade.
 *
 * This is the worst failure mode this product has. The page exists to prove
 * integrity, and it was printing "verified" and "DIFFERS" side by side about
 * the same record. A reader cannot tell which to believe, and the one who
 * learns to scroll past the warning has been trained to miss the real one.
 * It is the "footnote nobody's row earns" problem with the stakes inverted:
 * here the noise is on the alarm itself.
 *
 * The fix compares timestamps at the precision the column actually holds. That
 * is not laxity — no check can distinguish values the storage cannot represent
 * — and a tamper of a second or more is still caught, which the last two tests
 * pin.
 */
const test = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const html = fs.readFileSync(
  path.join(__dirname, '..', 'public', 'call.html'), 'utf8');

function loadHelper() {
  const start = html.indexOf('var STORAGE_TIME_GRANULARITY_MS');
  assert.ok(start > 0, 'helper not found — did call.html move?');
  const end = html.indexOf('</script>', start);
  const ctx = { isFinite, Date, Number, String, Math };
  vm.createContext(ctx);
  vm.runInContext(html.slice(start, end), ctx);
  return ctx.sameAfterStorage;
}

const same = loadHelper();

test('THE BUG: a millisecond lost to the column is not tampering', () => {
  assert.strictEqual(
    same('opened_at', '2026-08-10T14:40:06.293Z', '2026-08-10T14:40:06.000Z'), true,
    'the exact receipt from production that reported false tampering');
});

test('every sub-second remainder survives the round trip', () => {
  for (const ms of ['001', '099', '293', '500', '750', '999']) {
    assert.strictEqual(
      same('opened_at', `2026-08-10T14:40:06.${ms}Z`, '2026-08-10T14:40:06.000Z'), true,
      `${ms}ms truncated to .000 must not read as tampering`);
  }
});

test('and so does a rounded one, in case the engine rounds instead of truncating', () => {
  // MySQL rounds fractional seconds into an fsp=0 column rather than
  // truncating, depending on version and mode. .999 then lands on the NEXT
  // second — a 1ms delta, not a 999ms one. Both directions must pass, because
  // which one happens is a server setting nobody here controls.
  assert.strictEqual(
    same('opened_at', '2026-08-10T14:40:06.999Z', '2026-08-10T14:40:07.000Z'), true);
});

test('a full second of drift IS still reported', () => {
  assert.strictEqual(
    same('opened_at', '2026-08-10T14:40:06.000Z', '2026-08-10T14:40:07.000Z'), false,
    'the check must still catch a real edit');
});

test('a large edit is reported', () => {
  assert.strictEqual(
    same('opened_at', '2026-08-10T14:40:06.293Z', '2026-08-11T09:00:00.000Z'), false);
});

test('a timestamp replaced with junk is reported, not waved through', () => {
  // The tempting shortcut is "unparseable → treat as equal". That hides the
  // one edit the check most needs to see.
  assert.strictEqual(same('opened_at', '2026-08-10T14:40:06.293Z', 'not-a-date'), false);
  assert.strictEqual(same('opened_at', '2026-08-10T14:40:06.293Z', ''), false);
  assert.strictEqual(same('opened_at', '2026-08-10T14:40:06.293Z', null), false);
});

test('two junk values that are byte-identical are equal', () => {
  assert.strictEqual(same('opened_at', 'not-a-date', 'not-a-date'), true);
});

test('non-timestamp fields keep exact comparison', () => {
  assert.strictEqual(same('entry', 226.63, 226.63), true);
  assert.strictEqual(same('entry', 226.63, 226.64), false,
    'a one-cent edit to the entry price is exactly what this page is for');
  assert.strictEqual(same('direction', 'LONG', 'SHORT'), false);
  assert.strictEqual(same('symbol', 'RTXSTOCKUSDT', 'RTXSTOCKUSDT'), true);
});

test('the tolerance is not silently applied to prices', () => {
  // A price is a DECIMAL and survives the round trip exactly. Extending the
  // timestamp tolerance to numbers would let a real edit through.
  assert.strictEqual(same('entry', 226.63, 227.0), false);
  assert.strictEqual(same('sl', 224.51832, 224.51833), false);
});

test('the arena drift loop uses the helper rather than its own compare', () => {
  const loop = html.slice(html.indexOf("['symbol', 'direction', 'entry', 'leverage'"));
  assert.match(loop.slice(0, 400), /sameAfterStorage\(k, p\[k\], ac\[k\]\)/,
    'a second comparison here would drift from the one that is tested');
});
