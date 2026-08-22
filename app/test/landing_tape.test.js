'use strict';
/**
 * Landing-page live tape strip — the Arena's REAL latest closes on the front
 * door, reusing the public /api/arena/tape feed (#740). §4: percent + opt-in
 * handles + counts only. Honesty: a quiet tape keeps the strip hidden —
 * liveliness is never faked to visitors.
 */
const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

const html = fs.readFileSync(
  path.join(__dirname, '..', 'public', 'index.html'), 'utf8');

test('the strip mounts hidden and is fed from the public tape API', () => {
  assert.match(html, /id="landingTape" hidden/);
  assert.match(html, /id="ltRows"/);
  assert.match(html, /id="ltPulse"/);
  assert.match(html, /\/api\/arena\/tape/);
});

test('a quiet tape keeps the strip hidden — no fake liveliness', () => {
  assert.match(html, /if \(!d \|\| !d\.rows \|\| !d\.rows\.length\) return;/);
  // Only revealed after real rows rendered.
  assert.match(html, /getElementById\('landingTape'\)\.hidden = false/);
});

/**
 * The live-tape IIFE, bounded at both ends by CODE.
 *
 * It used to be sliced from `// Live tape strip` to `/* strip is decoration` —
 * two comments. Either one being reworded silently moved a boundary, and the
 * §4 check below is a `!/vUSDT|balance|margin|pnl\b/` over whatever the window
 * happened to contain: shrink it and the check passes over nothing, grow it
 * and it fails on code that has no business being in scope. A negative
 * assertion over a comment-delimited span is the weakest shape in this repo,
 * and this file had two.
 */
function stripBlock() {
  const at = html.indexOf("fetch('/api/arena/tape'");
  assert.ok(at > 0, 'the landing tape no longer fetches the Arena tape');
  const end = html.indexOf('})();', at);
  assert.ok(end > at, 'the tape strip is no longer a self-contained IIFE');
  return html.slice(at, end);
}

test('§4: the strip shows percent, handle, counts — never dollar amounts', () => {
  const strip = stripBlock();
  assert.ok(strip.length > 200, 'strip script found');
  assert.match(strip, /toFixed\(2\) \+ '%/);       // percent rendering
  assert.match(strip, /t\.handle/);
  assert.match(strip, /traders/);                  // counts-only pulse line
  assert.ok(!/vUSDT|balance|margin|pnl\b/.test(strip), 'no dollar fields on the landing strip');
});

test('the strip caps at five rows and escapes user-controlled text', () => {
  assert.match(html, /d\.rows\.slice\(0, 5\)/);
  const strip = stripBlock();
  assert.match(strip, /esc\(t\.handle\)/);
  assert.match(strip, /esc\(t\.symbol\)/);
  assert.match(strip, /esc\(t\.reason\)/);
});
