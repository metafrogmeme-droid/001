'use strict';
/**
 * Stress Lab shockwave — when the book changes, the drawdown bars sweep
 * across the scenario grid in card order (the shock propagating through the
 * portfolio) and any liquidation chip pulses once. The contract:
 * - the wave fires only when the RESULTS changed shape (a results key with
 *   the same no-restrobe rule as the escape cascade — retyping a symbol
 *   must not strobe the grid);
 * - the stagger delay rides the same card index that places the card, so
 *   the sweep order IS the scenario order;
 * - reduced motion switches both animations off;
 * - decoration only: the key is built FROM the results, it never feeds back
 *   into them.
 */
const test = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const path = require('path');

const stress = fs.readFileSync(path.join(__dirname, '..', 'public', 'stress.html'), 'utf8');

test('the wave is guarded by a results key, not fired on every render', () => {
  assert.match(stress, /var lastGridKey;/);
  assert.match(stress, /x\.scenario\.id \+ '\|' \+ x\.result\.drawdownPct\.toFixed\(2\) \+ '\|' \+ x\.result\.liquidatedCount/,
    'the key is built from the results themselves');
  assert.match(stress, /classList\.toggle\('wave', gridKey !== lastGridKey\)/);
  // the toggle happens BEFORE the innerHTML rebuild, so fresh elements are
  // born into the right animation state
  const toggleAt = stress.indexOf("classList.toggle('wave'");
  const rebuildAt = stress.indexOf("$('grid').innerHTML = runs.map");
  assert.ok(toggleAt > 0 && rebuildAt > 0 && toggleAt < rebuildAt,
    'wave class settles before the grid rebuilds');
});

test('the sweep order is the scenario order', () => {
  assert.match(stress, /style="--d:' \+ \(i \* 0\.07\)\.toFixed\(2\) \+ 's"/,
    'the delay rides the same index that places the card');
  assert.match(stress, /#grid\.wave \.sc \.bar i \{ animation: shock [^}]*animation-delay: var\(--d, 0s\);/);
  assert.match(stress, /@keyframes shock \{ from \{ width: 0; \} \}/);
});

test('liquidation chips pulse, and reduced motion silences everything', () => {
  assert.match(stress, /#grid\.wave \.liqchip\.on \{ animation: alarm/);
  assert.match(stress, /@keyframes alarm \{ 0% \{ box-shadow: 0 0 0 0 rgba\(224,82,82,\.5\); \}/);
  assert.match(stress,
    /@media \(prefers-reduced-motion: reduce\) \{\s*#grid\.wave \.sc \.bar i, #grid\.wave \.liqchip\.on \{ animation: none; \}\s*\}/);
});
