'use strict';
/**
 * The Arena key panel — the door onto the MCP surface.
 *
 * The endpoints shipped in #69 without any UI, which made a finished
 * capability reachable only by hand-rolling a POST. That is the same
 * "built but nobody can get to it" defect the Arena MCP work existed to
 * fix, one level up, so this closes it.
 *
 * What is worth pinning here is not that the panel renders — it is the three
 * places it could quietly lie:
 *
 *   · an unreadable key list rendered as an EMPTY one tells a user they have
 *     no keys, and the next thing they do is mint a duplicate for an agent
 *     that is still authenticating with the old one;
 *   · a key that has never been used is a different fact from one used long
 *     ago, so it must not borrow the creation date;
 *   · a failed clipboard write reported as success sends the user away
 *     believing they captured a key that is now unrecoverable.
 */

const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

const PUB = path.join(__dirname, '..', 'public');
const html = fs.readFileSync(path.join(PUB, 'arena.html'), 'utf8');
const i18n = fs.readFileSync(path.join(PUB, 'js', 'i18n.js'), 'utf8');

// ── it exists and appears for a signed-in user ────────────────────────────

test('the panel ships hidden and is revealed with the other signed-in panels', () => {
  assert.match(html, /id="keysPanel"[^>]*hidden/);
  assert.match(html, /\$\('keysPanel'\)\.hidden = false; loadKeys\(\);/,
    'revealed alongside posPanel/histPanel, not on its own timer');
});

test('mint, list and revoke are all wired', () => {
  assert.match(html, /RC\.fetchJSON\('\/api\/arena\/keys', \{ timeoutMs/, 'list');
  assert.match(html, /RC\.fetchJSON\('\/api\/arena\/keys', \{\s*method: 'POST'/, 'mint');
  assert.match(html, /\/api\/arena\/keys\/revoke/, 'revoke');
});

/**
 * The agent-keys panel, bounded at both ends by CODE.
 *
 * The start anchor was `// ── Agent keys` — a section heading. The end
 * (`function sigAgo`) was already real, so half this window was solid and half
 * moved whenever somebody retitled a comment. Both callers share it now, so
 * there is one boundary to be right about instead of two copies of one to be
 * wrong about together.
 */
function keyBlock() {
  const start = html.indexOf('function keyMsg(');
  const end = html.indexOf('function sigAgo');
  assert.ok(start > 0 && end > start, 'the agent-keys panel is gone from arena.html');
  return html.slice(start, end);
}

test('every fetch the panel makes is bounded', () => {
  // An unbounded fetch on a panel behind a login is a spinner that never ends.
  const block = keyBlock();
  // A fixed WINDOW after each call site, not a non-greedy match to the first
  // `)`. The first version did the latter and truncated every multi-line call
  // before its options object, reporting the panel unbounded when all three
  // calls carry timeoutMs. Same blind spot as any matcher that stops at the
  // first plausible delimiter.
  const sites = [...block.matchAll(/RC\.fetchJSON\(/g)].map((m) => m.index);
  assert.strictEqual(sites.length, 3, `expected the three key calls, found ${sites.length}`);
  for (const i of sites) {
    const window = block.slice(i, i + 260);
    assert.match(window, /timeoutMs/, window.slice(0, 70));
  }
});

// ── the three ways it could lie ───────────────────────────────────────────

test('an unreadable list renders nothing, never "no keys"', () => {
  const block = html.slice(html.indexOf('async function loadKeys'), html.indexOf('document.addEventListener'));
  // The failure branch must clear the host and say it could not read — it must
  // not fall through to the empty-state copy.
  assert.match(block, /if \(!r \|\| !r\.ok\) \{[\s\S]*?keys_unread[\s\S]*?return;/);
  const failBranch = block.slice(block.indexOf('if (!r || !r.ok)'), block.indexOf('keyMsg(\'\')'));
  assert.ok(!/keys_none/.test(failBranch),
    'a failed read must not render the "no keys yet" empty state');
});

test('a never-used key says so rather than borrowing the creation date', () => {
  const block = html.slice(html.indexOf('async function loadKeys'), html.indexOf('document.addEventListener'));
  assert.match(block, /k\.last_used_at\s*\?[\s\S]{0,200}?keys_never/,
    'null last_used_at must render "never used"');
  assert.ok(!/last_used_at \|\| k\.created_at/.test(block),
    'never-used must not fall back to created_at — they are different facts');
});

test('a failed copy says so instead of claiming success', () => {
  const block = html.slice(html.indexOf('var keyCopyBtn'), html.indexOf('function sigAgo'));
  assert.match(block, /keys_copyfail/);
  // Both arms of the clipboard promise are handled, and the no-clipboard
  // browser gets the failure message too rather than silence.
  assert.match(block, /writeText\(v\)\.then\(\s*function[\s\S]*?,\s*function[\s\S]*?keys_copyfail/);
  assert.match(block, /\} else \{[\s\S]*?keys_copyfail/);
});

// ── the plaintext key is handled once ─────────────────────────────────────

test('the page never stores the key', () => {
  const block = keyBlock();
  for (const sink of ['localStorage', 'sessionStorage', 'document.cookie']) {
    assert.ok(!block.includes(sink),
      `${sink} must never hold a key — the server keeps only a hash`);
  }
});

test('the one-time notice is not optional decoration', () => {
  // A user who assumes the key can be looked up later loses it silently, so
  // the panel says it outright rather than relying on the API note.
  assert.match(html, /id="keyOnce"[^>]*hidden/);
  assert.match(html, /data-i18n="arena\.keys_once"/);
  const line = i18n.split('\n').find((l) => l.includes("'arena.keys_once':"));
  assert.ok(line && /never shown again/i.test(line));
});

test('the panel states the blast radius of a stolen key', () => {
  const line = i18n.split('\n').find((l) => l.includes("'arena.keys_p':"));
  assert.ok(line, 'arena.keys_p exists');
  assert.match(line, /not a login/i);
  assert.match(line, /virtual money/i);
});

// ── translated, like everything else on this page ─────────────────────────

test('every new key string ships all fourteen locales on one line', () => {
  const keys = ['arena.p_keys', 'arena.s_keys', 'arena.keys_p', 'arena.ph_keylabel',
    'arena.b_mint', 'arena.b_revoke', 'arena.b_copy', 'arena.keys_once',
    'arena.keys_none', 'arena.keys_never', 'arena.keys_used', 'arena.keys_unnamed',
    'arena.keys_unread', 'arena.keys_revoked', 'arena.keys_revfail',
    'arena.keys_mintfail', 'arena.keys_copied', 'arena.keys_copyfail'];
  for (const key of keys) {
    const line = i18n.split('\n').find((l) => l.includes(`'${key}':`));
    assert.ok(line, `i18n defines ${key}`);
    for (const loc of ['en:', 'hi:', 'it:', 'es:', 'zh:', 'pt:', 'fr:', 'de:',
      'nl:', 'ja:', 'ko:', 'ru:', 'tr:', 'ar:']) {
      assert.ok(line.includes(loc), `${key} is missing ${loc}`);
    }
  }
});

test('no dollar figure reaches this panel', () => {
  const block = html.slice(html.indexOf('id="keysPanel"'), html.indexOf('<!-- Leaderboard'));
  assert.ok(!/\$\s?\d/.test(block));
});
