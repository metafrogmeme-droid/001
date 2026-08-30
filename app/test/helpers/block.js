'use strict';
/**
 * Slice a named region out of a source file — and REFUSE to hand back nothing.
 *
 * Two guard tests sliced with markers in the wrong order:
 *
 *   dashboard_social.test.js   slice(266855, 164307)  -> ""
 *   landing_engagement.test.js slice(81368,  16663)   -> ""
 *
 * `String.prototype.slice` returns "" when start > end. It does not complain.
 * So both blocks were the empty string, and every `!block.includes('net_pnl_usd')`
 * assertion built on them passed vacuously — for months, on the guards for the
 * no-dollars-on-public rule. The surfaces happened to stay clean; the guards
 * had stopped watching.
 *
 * That is the same family as the `[^,]+` regex that could not cross a comma and
 * the literal-string match any added argument defeated: an assertion that cannot
 * see its own subject, reporting success. This helper makes the failure loud
 * instead of silent, so the next reversed pair is a red test rather than a
 * quiet one:
 *
 *   * a marker that is not present            -> fail, naming it
 *   * markers found in either order           -> slice correctly, ordered
 *   * a block that comes out empty or trivial -> fail
 *
 * Order-insensitivity is deliberate. Requiring the caller to know which marker
 * comes first in a 260k-character bundle is exactly the knowledge that went
 * stale here.
 */

const assert = require('node:assert');

/**
 * @param {string} src     the haystack
 * @param {string} markerA one edge of the region
 * @param {string} markerB the other edge
 * @param {{pad?: number, min?: number, label?: string}} [opts]
 *        pad    extra characters to include past the later marker
 *        before extra characters to include BEFORE the earlier marker — for a
 *               region whose distinctive, unique anchor sits in the MIDDLE of
 *               what is being guarded rather than at its edge
 *        min   smallest block that counts as real (default 20)
 *        label what to call this region when a message is printed
 */
function blockBetween(src, markerA, markerB, opts = {}) {
  const { pad = 0, before = 0, min = 20,
    label = `${markerA} .. ${markerB}` } = opts;
  assert.equal(typeof src, 'string', `blockBetween(${label}): source is not a string`);

  const a = src.indexOf(markerA);
  const b = src.indexOf(markerB);
  assert.notEqual(a, -1, `blockBetween(${label}): marker ${JSON.stringify(markerA)} `
    + 'is not in the source — it moved or was renamed, and the region this '
    + 'test guards no longer exists where it looks');
  assert.notEqual(b, -1, `blockBetween(${label}): marker ${JSON.stringify(markerB)} `
    + 'is not in the source');

  const start = Math.max(0, Math.min(a, b) - before);
  const end = Math.max(a, b) + pad;
  const block = src.slice(start, end);
  assert.ok(block.length >= min,
    `blockBetween(${label}): the region is ${block.length} characters — too `
    + 'small to assert anything against. Every "must not contain" check on it '
    + 'would pass because there is nothing there.');
  return block;
}

module.exports = { blockBetween };
