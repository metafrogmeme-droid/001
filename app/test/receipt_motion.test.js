'use strict';
/**
 * Receipt-page motion — the shareable inbound pages (/call/<key> and
 * /trader/<handle>) get their moments: each verdict on the call page stamps
 * in as it lands (initial check, browser-computed verdict, Merkle-anchor
 * replay), the trader card's form bars grow out of the midline in the order
 * the closes happened, and badges rise staggered. Pins:
 * - every animation is decoration on top of already-honest content — nothing
 *   about the numbers or verdicts changes;
 * - reduced motion switches every one of them off at the CSS level;
 * - the bar delay rides the same index as the bar itself, so the drawing
 *   order IS the chronological order.
 */
const test = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const path = require('path');

const pub = (...p) => fs.readFileSync(path.join(__dirname, '..', 'public', ...p), 'utf8');
const call = pub('call.html');
const trader = pub('trader.html');

test('the call verdicts stamp in, and reduced motion turns it off', () => {
  assert.match(call, /\.verdict \{ animation: stamp/);
  assert.match(call, /@keyframes stamp \{ from \{ opacity: 0; transform: scale\(1\.0\d+\); \} \}/);
  assert.match(call,
    /@media \(prefers-reduced-motion: reduce\) \{ \.verdict \{ animation: none; \} \}/);
  // `backwards` fill: a verdict inserted later starts hidden, never flashes
  assert.match(call, /animation: stamp [\d.]+s [^;]+ backwards;/);
});

test('trader form bars grow chronologically, badges rise staggered', () => {
  assert.match(trader, /\.spark-wrap svg rect \{ transform-box: fill-box; transform-origin: center; animation: barIn/);
  assert.match(trader, /@keyframes barIn \{ from \{ transform: scaleY\(0\); \} \}/);
  // the delay is computed from the same loop index that places the bar
  assert.match(trader, /animation-delay:' \+ \(i \* 0\.035\)\.toFixed\(3\)/);
  // badges stagger with a bounded start offset
  assert.match(trader, /badge-chip rise" style="animation-delay:' \+ \(0\.10 \+ i \* 0\.05\)/);
});

test('reduced motion silences the trader animations too', () => {
  const media = trader.slice(trader.indexOf('@media (prefers-reduced-motion: reduce)'));
  const block = media.slice(0, media.indexOf('}\n') + 2);
  assert.match(trader, /\.rise \{ opacity: 1; transform: none; animation: none; \}/);
  assert.match(trader, /reduce\) \{[\s\S]{0,200}\.spark-wrap svg rect \{ animation: none; \}/);
  assert.ok(block.length > 0);
});

test('motion never touched the honesty scans', () => {
  // §4: the drawn card remains percent/count-only — the animation edits did
  // not introduce any amount into either page's public template.
  for (const [name, src] of [['call', call], ['trader', trader]]) {
    assert.ok(!/\$\s?\d/.test(src), `${name}: no $-amount`);
  }
});
