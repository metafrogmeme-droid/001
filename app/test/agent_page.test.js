'use strict';
/**
 * The public page for one agent — the artifact the whole credential layer
 * exists to produce, and therefore the most dangerous surface in it to build.
 *
 * CLAUDE.md's list of what has gone wrong in this repo is almost entirely
 * track records and leaderboards. Every shape on that list is reachable from
 * here: a return, a win count, a median, a green stripe, a claim about when
 * something happened. So the tests are not "does it render" — they are the
 * distinctions a naive renderer erases.
 *
 * WHAT IS BEING DEFENDED
 *
 * 1. COLOUR IS A CLAIM. The profit colour appears for exactly one chain state:
 *    `anchored`, where a Base transaction was actually read. `rooted` — the
 *    day's root is computed but nobody put it on-chain — is muted, because it
 *    still rests on our own clock, and it is the state most likely to be
 *    mistaken for the good one.
 *
 * 2. `not_in_root` IS A WARNING, NOT AN ABSENCE. A seal missing from a
 *    committed leaf set is the shape of a back-inserted row. Filing it with
 *    the routine "not anchored yet" states would hide the one case a reader
 *    should act on.
 *
 * 3. OMIT MUST NOT BECOME SILENCE. This is a composite view, so one dead
 *    source must not blank the others — but a block that could not be read has
 *    to SAY so where it would have been. Dropping it turns an outage into a
 *    page that quietly asserts the agent has no record.
 *
 * 4. `own: null` IS NOT A ZERO. "Never traded for itself" and "traded and
 *    scored nothing" are different facts.
 */

const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const { codeOnly } = require('./helpers/code_only');

const V = require('../public/js/agent-page.js');
const PAGE = fs.readFileSync(
  path.join(__dirname, '..', 'public', 'agent-profile.html'), 'utf8');

// ── colour is a claim ───────────────────────────────────────────────────────

test('an unreadable return is muted, never a win or a loss', () => {
  for (const v of [null, undefined, '', NaN, 'oops']) {
    assert.equal(V.pctClass(v), 'ap-flat', `${String(v)} was given a verdict colour`);
    assert.equal(V.pct(v), '—', `${String(v)} rendered as a number`);
  }
  // A real, measured break-even is NOT the same as unreadable, and 0 is falsy —
  // the exact trap CLAUDE.md names ("test `is None`, not falsiness").
  assert.equal(V.pctClass(0), 'ap-flat');
  assert.equal(V.pct(0), '0.0%');
  assert.equal(V.pctClass(2.5), 'ap-up');
  assert.equal(V.pctClass(-2.5), 'ap-down');
});

test('ONLY an anchored chain earns the profit colour', () => {
  const anchored = V.chainHtml({ status: 'anchored', anchor_tx: '0x' + 'a'.repeat(64), day: '2026-08-25' });
  assert.match(anchored, /class="ap-chain ap-up"/);
  assert.match(anchored, /basescan\.org\/tx\/0xaaa/);

  // Every other state, including the one that is nearly good.
  for (const s of ['rooted', 'day_open', 'no_root', 'unknown']) {
    const html = V.chainHtml({ status: s, day: '2026-08-25' });
    assert.ok(!/ap-chain ap-up/.test(html), `${s} was painted as anchored`);
  }
  // Anchored via an unrecognised status must not fall through to the good one.
  assert.ok(!/ap-chain ap-up/.test(V.chainHtml({ status: 'nonsense' })));
  assert.ok(!/ap-chain ap-up/.test(V.chainHtml(null)));
  assert.ok(!/ap-chain ap-up/.test(V.chainHtml({})));
});

test('a rooted-but-unanchored day says the date rests on our clock', () => {
  const html = V.chainHtml({ status: 'rooted', day: '2026-08-25', root: 'r'.repeat(64) });
  assert.match(html, /rests on our clock/);
  assert.ok(!/basescan/.test(html), 'an unanchored day linked a transaction');
});

test('a seal missing from a committed leaf set is a WARNING, not one more absence', () => {
  const html = V.chainHtml({ status: 'not_in_root', day: '2026-08-25', root: 'x' });
  assert.match(html, /class="ap-chain ap-down"/, 'the alarming case was muted like a routine one');
  assert.match(html, /unproven/);
  // It must not share its wording with the routine absences.
  for (const s of ['rooted', 'day_open', 'no_root']) {
    assert.notEqual(V.CHAIN_COPY[s].label, V.CHAIN_COPY.not_in_root.label);
    assert.notEqual(V.CHAIN_COPY[s].cls, V.CHAIN_COPY.not_in_root.cls);
  }
});

test('an unreadable chain is not a verdict either way', () => {
  const html = V.chainHtml({ status: 'unknown' });
  assert.match(html, /not a verdict/);
  assert.ok(!/ap-up|ap-down/.test(html), 'an unknown chain was given a verdict colour');
});

// ── omit must not become silence ────────────────────────────────────────────

test('a source that could not be read SAYS so, in its own place', () => {
  const html = V.errorHtml('Track record', 'boom');
  assert.match(html, /Could not be read/);
  assert.match(html, /not a statement about this agent/,
    'an outage must not read as a claim about the agent');
  assert.match(html, /ap-err/);
});

test('the page catches each source separately and renders both outcomes', () => {
  // The property: one dead endpoint cannot blank the other block. Pinned on
  // the page because the composition is the page's job, not the renderer's.
  assert.match(PAGE, /errorHtml\('Identity'/, 'a dead identity read renders nothing');
  assert.match(PAGE, /errorHtml\('Track record'/, 'a dead record read renders nothing');
  assert.match(PAGE, /\.catch\(function \(\) \{ return \{ ok: false \}; \}\)/,
    'a rejected fetch must resolve to a flag, not propagate and kill the page');
  // A 404 is a real answer and must not be painted as a fault.
  assert.match(PAGE, /r\.status === 404/);
});

// ── the two records stay two ────────────────────────────────────────────────

test('own: null says "never traded for itself", not a score of zero', () => {
  const html = V.ownHtml(null);
  assert.match(html, /never opened a position for itself/);
  assert.match(html, /not a score of zero/);
  assert.ok(!/ap-stats/.test(html), 'an absent record was rendered as a zeroed one');
});

test('an own record renders its own numbers and disclaims addition', () => {
  const html = V.ownHtml({ trades: 4, wins: 3, losses: 1, median_rom_pct: 12.5,
    best_rom_pct: 40, worst_rom_pct: -5, liquidations: 0 });
  assert.match(html, /12\.5%/);
  assert.match(html, /never added to it/,
    'the page must say the two records are not summed');
});

test('the copiers’ record states whose result it is', () => {
  const html = V.recordHtml({ trades: 12, copiers: 3, wins: 7, losses: 5,
    median_rom_pct: 2.2, best_rom_pct: 30, worst_rom_pct: -12, liquidations: 1 });
  assert.match(html, /3<\/b> member/);
  assert.match(html, /not how good the agent is/);
  assert.match(html, /sealed at OPEN/);
});

test('an empty record says nothing was measured — it does not print a zero score', () => {
  const html = V.statsHtml({ trades: 0, wins: 0, losses: 0, median_rom_pct: null,
    best_rom_pct: null, worst_rom_pct: null, liquidations: 0 });
  assert.match(html, /Nothing has been measured/);
  // The medians are null, so they must render as em dashes rather than 0.0%.
  assert.ok(!/0\.0%/.test(html), 'an unmeasured median printed as break-even');
});

test('a low sample says so', () => {
  assert.match(V.statsHtml({ trades: 3, low_sample: true }), /too few to mean much/);
  assert.ok(!/too few/.test(V.statsHtml({ trades: 40, low_sample: false })));
});

// ── the claim's limits are stated, not implied ──────────────────────────────

test('the identity block says what it does NOT prove', () => {
  const html = V.identityHtml({ slug: 'lonewolf', display_name: 'Lone Wolf',
    claimed_at: '2026-08-26T09:00:00.000Z', seal: 'a'.repeat(64),
    seal_payload: '{"v":1}', verify: { chain: { status: 'anchored', anchor_tx: '0x' + 'b'.repeat(64) } } });
  assert.match(html, /Does not prove/);
  assert.match(html, /who operates this agent/);
  // The bytes a stranger hashes must be ON the page, not merely described —
  // HTML-escaped, because `display_name` is inside the sealed payload and is
  // attacker-influenced. The browser decodes the entities back before anyone
  // copies from the <pre>, so the bytes that get hashed are the sealed ones.
  assert.match(html, /<pre class="ap-payload">\{&quot;v&quot;:1\}<\/pre>/);
  assert.match(html, /a{64}/);
});

test('the page never implies real money', () => {
  assert.match(V.footerHtml(), /Paper trading/);
  assert.match(V.footerHtml(), /Nothing on this page is a real-money result/);
  // §4: no dollar amount anywhere in the renderer's own output.
  const all = V.identityHtml({ slug: 's', verify: { chain: { status: 'rooted' } } })
    + V.recordHtml({ trades: 1, copiers: 1, median_rom_pct: 5 })
    + V.ownHtml({ trades: 1, median_rom_pct: 5 }) + V.footerHtml();
  assert.ok(!/\$|USDT\s*[\d.]|vUSDT/.test(all), 'a currency amount reached a public page');
});

test('escaping — a slug or name cannot inject markup', () => {
  const html = V.identityHtml({ slug: 'x', display_name: '<img src=x onerror=alert(1)>',
    seal_payload: '</pre><script>alert(1)</script>', verify: { chain: {} } });
  assert.ok(!/<img/.test(html));
  assert.ok(!/<script>alert/.test(html));
  assert.match(html, /&lt;img/);
});

// ── the route exists and does not collide ───────────────────────────────────

test('/a/:slug is served, and is NOT under the address-keyed /agent/', () => {
  const srv = fs.readFileSync(path.join(__dirname, '..', 'server.js'), 'utf8');
  assert.match(srv, /app\.get\('\/a\/:slug'/, 'the page has no route — it is unreachable');
  assert.match(srv, /agent-profile\.html/);
  // /agent/:address serves the ERC-8004 card. A slug routed there would be
  // handed the card page and die on its address regex.
  assert.match(srv, /app\.get\('\/agent\/:address'[\s\S]{0,200}agent-card\.html/);
});

// ── the index, and the route ordering that actually matters ─────────────────

test('/a is served, and its order against /a/:slug is irrelevant', async () => {
  const srv = fs.readFileSync(path.join(__dirname, '..', 'server.js'), 'utf8');
  assert.match(srv, /app\.get\('\/a',/, 'the claimed-agent index has no route');
  assert.match(srv, /agents-claimed\.html/);

  // Driven, not asserted from a comment. A previous version of this file's
  // sibling comment claimed bare /a had to be registered first; it does not,
  // because /a/:slug requires a second segment. The claim was copied from an
  // equally false one above /agents and only found by running it.
  const express = require('express');
  const app = express();
  app.get('/a/:slug', (q, r) => r.json({ hit: 'slug' }));
  app.get('/a', (q, r) => r.json({ hit: 'index' }));
  const s = app.listen(0, '127.0.0.1');
  await new Promise((ok) => s.once('listening', ok));
  const base = 'http://127.0.0.1:' + s.address().port;
  try {
    assert.equal((await (await fetch(base + '/a')).json()).hit, 'index',
      'the param route captured the bare index');
    assert.equal((await (await fetch(base + '/a/wolf')).json()).hit, 'slug');
  } finally { s.close(); }
});

test('THE ordering constraint that is real: /agents/compare before /agents/:slug', async () => {
  // Both are two-segment, so the param route WILL swallow the literal if it is
  // registered first — /agents/compare becomes a slug lookup that 404s. This
  // is the constraint the false comment next to it was drawing attention away
  // from, and nothing tested it.
  const srv = fs.readFileSync(path.join(__dirname, '..', 'server.js'), 'utf8');
  const compareAt = srv.indexOf("app.get('/agents/compare'");
  const slugAt = srv.indexOf("app.get('/agents/:slug'");
  assert.ok(compareAt > -1, '/agents/compare is not registered');
  if (slugAt > -1) {
    assert.ok(compareAt < slugAt,
      '/agents/compare moved below /agents/:slug — it is now a slug lookup that 404s');
  }

  // And the mechanism, demonstrated rather than described.
  const express = require('express');
  const app = express();
  app.get('/agents/:slug', (q, r) => r.json({ hit: 'slug' }));
  app.get('/agents/compare', (q, r) => r.json({ hit: 'compare' }));
  const s = app.listen(0, '127.0.0.1');
  await new Promise((ok) => s.once('listening', ok));
  try {
    const r = await (await fetch('http://127.0.0.1:' + s.address().port + '/agents/compare')).json();
    assert.equal(r.hit, 'slug',
      'the param route no longer shadows the literal — this test encodes why order matters');
  } finally { s.close(); }
});

test('the index endpoint refuses to render an unreadable table as empty', () => {
  // COMMENTS BLANKED FIRST. Without it this failed on the route's own comment
  // — which says "503, never `{agents: []}`" — the exact trap CLAUDE.md names:
  // a comment that quotes the string it forbids is indistinguishable from the
  // code doing it. Written, hit, and fixed with the repo's own helper rather
  // than by softening the assertion.
  const src = codeOnly(fs.readFileSync(
    path.join(__dirname, '..', 'routes', 'public_agent_identity.js'), 'utf8'));
  assert.match(src, /503/, 'an unreadable directory must not answer 200 with an empty list');
  assert.ok(!/agents: \[\]/.test(src), 'an empty list is manufactured somewhere');
  const page = fs.readFileSync(
    path.join(__dirname, '..', 'public', 'agents-claimed.html'), 'utf8');
  assert.match(page, /if \(!r\.ok\) throw/, 'the single-source page must GUARD, not omit');
  assert.match(page, /That is a count, not a failed load/);
  assert.match(page, /does not mean no agents exist/);
});
