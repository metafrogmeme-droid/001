'use strict';
/**
 * A void opened between the nav rail and the content, and grew with the screen.
 *
 * Reported from a wide display on 2026-08-18 with a screenshot of /dashboard
 * whose panels sat marooned in the right half of the window, a band of empty
 * background separating them from the rail.
 *
 * `.shell` is `display: flex`. Its two children are `.rail` (fixed width, shown
 * from 1024px up) and `.content`, which carries
 *
 *     flex: 1;  max-width: var(--maxw);  margin: 0 auto;
 *
 * Auto margins absorb the leftover space in a flex line and split it evenly.
 * Below the rail breakpoint that is exactly right — `.content` is the only
 * child, and centring a 1200px column in a 1600px window is the intent. With
 * the rail present it is wrong in a way that is invisible in the CSS and
 * obvious on the screen: the leftover is measured AFTER the rail is subtracted,
 * so half of it is deposited on the content's INSIDE edge, between the
 * navigation and the thing it navigates.
 *
 * Measured in Chromium at 2000px before the fix:
 *
 *     rail 0–208 · GAP 208–504 · content 504–1704 · gap 1704–2000
 *
 * The inner gap takes half of every additional pixel of window width, so it is
 * absent on a laptop, mild at 1920 and severe at 3440 — which is why it shipped.
 * The fix drops the auto LEFT margin inside the rail's own media query, so the
 * trailing space collects on the outside edge where an eye reads it as margin.
 *
 * WHY THIS IS A SOURCE CHECK. The property that matters is a computed layout,
 * and this suite has no browser. What can be pinned without one is the
 * CONDITIONAL: an auto left margin is only a defect while the rail occupies the
 * same flex line, so the test reads both facts out of the same media block and
 * asserts they cannot both be true. A future breakpoint change moves them
 * together or fails here.
 */

const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

const CSS = fs.readFileSync(path.join(__dirname, '..', 'public', 'styles.css'), 'utf8');

/** The body of the first `@media (min-width: <px>)` block, brace-matched. */
function mediaBlock(minWidth) {
  const head = CSS.indexOf(`@media (min-width: ${minWidth})`);
  if (head < 0) return null;
  let i = CSS.indexOf('{', head);
  if (i < 0) return null;
  let depth = 0;
  const start = i + 1;
  for (; i < CSS.length; i++) {
    if (CSS[i] === '{') depth++;
    else if (CSS[i] === '}' && --depth === 0) return CSS.slice(start, i);
  }
  return null;
}

/** Declarations of a selector inside a block, as one string. */
function ruleFor(block, selector) {
  const re = new RegExp(`(^|[},\\s])${selector.replace('.', '\\.')}\\s*\\{([^}]*)\\}`, 'g');
  let out = '';
  for (const m of block.matchAll(re)) out += m[2] + ';';
  return out;
}

test('the rail and an auto left margin never share a flex line', () => {
  // THE WHOLE DEFECT, as a single incompatibility. Both halves are read out of
  // the same media block so neither can move without the other.
  const block = mediaBlock('1024px');
  assert.ok(block, 'the rail breakpoint is gone — re-derive this test before trusting it');

  const rail = ruleFor(block, '.rail');
  assert.match(rail, /display:\s*flex/,
    'the rail no longer becomes a flex sibling at this breakpoint; if it is laid '
    + 'out some other way now, the margin rule below needs re-deciding, not keeping');

  const content = ruleFor(block, '.content');
  assert.match(content, /margin-left:\s*0/,
    '`.content` centres itself with an auto left margin while the rail is on the '
    + 'same flex line — that reopens the empty band between the navigation and '
    + 'the content, widening by half of every extra pixel of window');
  assert.ok(!/margin:\s*0\s+auto/.test(content),
    'a shorthand `margin: 0 auto` in the rail breakpoint puts the auto left '
    + 'margin straight back');
});

test('the base rule still centres when there is no rail', () => {
  // THE CONTROL. Deleting the centring outright would pass the test above and
  // strand a 1200px column against the left edge of every phone and tablet,
  // where `.content` is the only child and centring is correct.
  const base = CSS.slice(0, CSS.indexOf('@media (min-width: 1024px)'));
  const content = ruleFor(base, '.content');
  assert.match(content, /margin:\s*0\s+auto/,
    'the unbreakpointed `.content` no longer centres — below 1024px the rail is '
    + 'display:none and the content is the only flex child, so centring is what '
    + 'is wanted there');
  assert.match(content, /max-width:\s*var\(--maxw\)/,
    'the reading-width cap is gone; without it the centring question does not '
    + 'arise and neither does this guard');
});

test('only the dashboard puts a max-width column beside a fixed sibling', () => {
  // Asked because the same shape elsewhere would have the same defect: the
  // landing page's `.hero`, `.section` and footer all use
  // `max-width: var(--maxw); margin: 0 auto`, and all of them are in normal
  // block flow with no sibling to be pushed away from. `.shell` is the only
  // flex row with a fixed-width child, and it exists on one page.
  const pub = path.join(__dirname, '..', 'public');
  const shells = fs.readdirSync(pub)
    .filter((f) => f.endsWith('.html'))
    .filter((f) => /class="shell"/.test(fs.readFileSync(path.join(pub, f), 'utf8')));
  assert.deepStrictEqual(shells, ['dashboard.html'],
    'another page adopted the rail shell — check its content column for the same '
    + 'inner gap before adding it here');
});
