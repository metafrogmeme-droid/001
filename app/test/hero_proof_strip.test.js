'use strict';
/**
 * The hero's one factual claim, and the ways it could become a lie.
 *
 * The proof strip publishes a real Merkle root over a real day's sealed calls.
 * That makes it the most load-bearing block on the site — and the most
 * dangerous, because a "verify us" headline sitting above a fabricated or
 * placeholder hash produces the exact opposite of trust: a reader copies it,
 * finds it doesn't match, and concludes the whole page is theatre.
 *
 * So every failure path here has to render NOTHING. Hidden asserts nothing;
 * an empty `<code>` under a label saying "Latest sealed day" asserts that
 * there is one. That is the CLAUDE.md rule at its sharpest — a failed read is
 * not a zero, and here it is not an empty string either.
 *
 * The FIRST draft of this copy asked for today's root
 * (`/roots/2026-08-11 | sha256sum`). `seal_roots.rootForDay` refuses any day
 * `>= today` — "the day is still open" — so it could never have resolved, and
 * piping a 404 body to sha256sum still prints a hash. That is why the strip
 * renders the day the SERVER reports rather than one the page computes.
 */
const test = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const html = fs.readFileSync(
  path.join(__dirname, '..', 'public', 'index.html'), 'utf8');

const START = "(function () {\n  var box = document.getElementById('proofStrip');";
function source() {
  const i = html.indexOf(START);
  assert.ok(i > 0, 'proof-strip IIFE not found — did the block move?');
  const end = html.indexOf('</script>', i);
  return html.slice(i, end);
}

/** Run the strip against a given /api/roots response. */
async function run(payload, { ok = true, reject = false } = {}) {
  const els = {
    proofStrip: { hidden: true },
    psRoot: { textContent: '' },
    psDay: { textContent: '' },
    psCount: { textContent: '' },
    psCmd: { textContent: '' },
    psCopy: { textContent: 'copy', addEventListener() {} },
  };
  const ctx = {
    document: { getElementById: (id) => els[id] || null },
    location: { origin: 'https://www.humanoid-traders.com' },
    navigator: {},
    window: {},
    setTimeout,
    isFinite,
    fetch: () => (reject
      ? Promise.reject(new Error('network down'))
      : Promise.resolve({ ok, json: () => Promise.resolve(payload) })),
  };
  vm.createContext(ctx);
  vm.runInContext(source(), ctx);
  for (let i = 0; i < 8; i++) await Promise.resolve();
  return els;
}

const GOOD = {
  roots: [{ day: '2026-08-10', root: 'a'.repeat(64), seal_count: 352 }],
};

test('a real root renders and reveals the strip', async () => {
  const els = await run(GOOD);
  assert.strictEqual(els.proofStrip.hidden, false);
  assert.strictEqual(els.psRoot.textContent, 'a'.repeat(64));
  assert.strictEqual(els.psDay.textContent, '2026-08-10');
});

test('the command names the day the SERVER reported', async () => {
  const els = await run(GOOD);
  assert.match(els.psCmd.textContent, /\/api\/roots > runeclaw-roots-2026-08-10\.json/);
  assert.doesNotMatch(els.psCmd.textContent, /sha256sum/,
    'hashing the feed body is not the verification and never was');
});

test('a dead feed leaves the strip hidden and prints no root', async () => {
  const els = await run(null, { reject: true });
  assert.strictEqual(els.proofStrip.hidden, true);
  assert.strictEqual(els.psRoot.textContent, '');
});

test('a non-200 leaves it hidden', async () => {
  const els = await run(GOOD, { ok: false });
  assert.strictEqual(els.proofStrip.hidden, true);
});

test('an empty roots feed shows nothing rather than an empty hash', async () => {
  const els = await run({ roots: [] });
  assert.strictEqual(els.proofStrip.hidden, true);
  assert.strictEqual(els.psRoot.textContent, '');
});

test('a malformed root is refused, not displayed', async () => {
  for (const root of ['', 'not-a-hash', 'abc', 'A'.repeat(64), 'a'.repeat(63)]) {
    const els = await run({ roots: [{ day: '2026-08-10', root, seal_count: 3 }] });
    assert.strictEqual(els.proofStrip.hidden, true,
      `rendered a root that is not a 64-hex digest: ${JSON.stringify(root)}`);
  }
});

test('a malformed day is refused too', async () => {
  const els = await run({ roots: [{ day: 'yesterday', root: 'a'.repeat(64) }] });
  assert.strictEqual(els.proofStrip.hidden, true);
});

test('a missing seal count is omitted, never printed as zero', async () => {
  const els = await run({ roots: [{ day: '2026-08-10', root: 'b'.repeat(64) }] });
  assert.strictEqual(els.proofStrip.hidden, false, 'the root itself is still good');
  assert.strictEqual(els.psCount.textContent, '',
    '"0 calls sealed" is a measurement nobody made');
});

test('a real zero count IS shown', async () => {
  const els = await run({ roots: [{ day: '2026-08-10', root: 'c'.repeat(64), seal_count: 0 }] });
  assert.match(els.psCount.textContent, /\b0\b/,
    'a day that genuinely sealed nothing is a reading, not an absence');
});

test('the markup ships hidden so nothing flashes before the fetch resolves', () => {
  assert.match(html, /<div class="proof-strip" id="proofStrip" hidden>/);
});

test('the strip points at the page that verifies a single call', () => {
  assert.match(html, /href="\/provable"/);
});
