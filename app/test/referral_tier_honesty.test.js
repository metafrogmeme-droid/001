'use strict';
/**
 * A referral perk is a promise, and four of the five were backed by nothing.
 *
 * `REFERRAL_TIERS` (app/auth.js) shipped five milestones, and the comment
 * directly above it already said the quiet part: "the perks are aspirational
 * and mostly land with the token/billing layer... It does NOT grant fee credits
 * or change live limits". True, and invisible — the card rendered
 *
 *     Protocol revenue share when the token launches.
 *
 * in the same voice, at the same weight, beside the same gold chip as
 *
 *     Your invite link is live — share it to climb.
 *
 * One of those is true today. The other depends on a token whose own roadmap
 * opens "No token exists. No sale has run." That is this repo's signature
 * failure lifted from numbers to promises: THE CODE KNEW AND THE SURFACE DID
 * NOT SAY.
 *
 * Two were worse than aspirational. "Priority support" (1 invite) and "Early
 * access to new agents & features" (3 invites) are the PAID Pro and Elite
 * plans' own selling points, listed as such in dashboard.js's plan table, and
 * granted here by nothing at all: `referralTier` has exactly one caller in the
 * tree — the endpoint that prints it. Nothing reads a tier to grant anything.
 *
 * WHAT THIS FILE PINS
 *
 *   1. A tier cannot claim a perk without declaring whether it is in force,
 *      and a perk that is not in force must say what it waits on. That is a
 *      ratchet: adding an unbacked perk now requires writing down why.
 *   2. Nothing depending on the token may be marked live, and any perk that
 *      mentions the token must say it does not exist yet.
 *   3. A referral perk may not resell a paid plan's benefit.
 *   4. ABSENT IS NOT STARTER. The renderer opened with
 *      `r.data.tier || { name: 'Starter', perk: '' }` and `r.data.count || 0`,
 *      so a response carrying a link and no tier rendered as
 *      **Starter · 0 joined · 0% to Connector** — a bottom-of-the-ladder
 *      verdict manufactured from no data, shown to someone who may have
 *      invited twenty people.
 *
 * The renderer half is asserted against a PURE model rather than grepped, for
 * the #999 reason: the block was inline in six thousand lines of browser
 * script, where nothing could plant a response and read what the card says.
 */

// Requiring ../auth boots its config guards; same preamble as referrals.test.js.
process.env.JWT_SECRET = 'j'.repeat(64);
delete process.env.DATABASE_URL;

const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

const { codeOnly } = require('./helpers/code_only');
const { REFERRAL_TIERS, referralTier } = require('../auth');
const {
  referralTierState, NOT_IN_FORCE, PERK_LIVE, PERK_PLANNED,
} = require('../public/js/referral-tier-model.js');

const read = (...p) => fs.readFileSync(path.join(__dirname, '..', ...p), 'utf8');

// ── 1. the table declares what it can and cannot honour ───────────────────

test('every tier declares whether its perk is in force', () => {
  assert.ok(REFERRAL_TIERS.length >= 2, 'a ladder needs rungs');
  for (const t of REFERRAL_TIERS) {
    assert.ok(t.name, 'a tier needs a name');
    assert.ok(t.perk, `${t.name}: a tier with no perk is a rung with no reason`);
    assert.ok(t.state === 'live' || t.state === 'planned',
      `${t.name}: state must be 'live' or 'planned', got ${JSON.stringify(t.state)}`);
    assert.ok(Number.isInteger(t.at) && t.at >= 0, `${t.name}: at`);
  }
});

test('a perk that is not in force says what it waits on', () => {
  // The ratchet. Writing a new aspirational perk now costs one sentence
  // explaining why nobody has it — which is the sentence the card prints.
  for (const t of REFERRAL_TIERS.filter((x) => x.state === 'planned')) {
    assert.ok(typeof t.requires === 'string' && t.requires.trim().length > 10,
      `${t.name}: a planned perk must say what it depends on, so the card can`);
  }
});

test('a live perk is not also waiting on something', () => {
  for (const t of REFERRAL_TIERS.filter((x) => x.state === 'live')) {
    assert.strictEqual(t.requires, null,
      `${t.name}: "you have this" and "this depends on X" cannot both be true`);
  }
});

test('the ladder only goes up', () => {
  for (let i = 1; i < REFERRAL_TIERS.length; i++) {
    assert.ok(REFERRAL_TIERS[i].at > REFERRAL_TIERS[i - 1].at,
      'milestones out of order would make referralTier pick the wrong rung');
  }
  assert.strictEqual(REFERRAL_TIERS[0].at, 0, 'somebody with no invites has a tier');
});

// ── 2. nothing rides on a token that does not exist ───────────────────────

// `revenue`, not `revenue share`. The first draft used the latter and a
// mutation marking Legend live sailed through, because its perk reads "A share
// of protocol revenue" — the same promise with the two words the other way
// round. The grep tells you where you looked; the mutation tells you where you
// didn't.
const TOKEN_WORDS = /\$?RCLAW|\btoken\b|\brevenue\b|fee credit|\bairdrop\b/i;

test('no live perk depends on the token', () => {
  // docs/TOKEN_ROADMAP.md: "No token exists. No sale has run." A perk that
  // needs it cannot be something you already have.
  for (const t of REFERRAL_TIERS.filter((x) => x.state === 'live')) {
    assert.ok(!TOKEN_WORDS.test(t.perk),
      `${t.name} is marked live and its perk reads "${t.perk}" — the token is `
      + 'gated and unlaunched, so nothing that needs it is in force');
  }
});

test('a token-dependent perk says the token does not exist yet', () => {
  // Anchored to the tier's OWN `requires` string, not to a rendered blob:
  // asserting a short phrase is absent from a whole card is the assertion
  // that keeps misfiring in this repo.
  for (const t of REFERRAL_TIERS) {
    const mentions = TOKEN_WORDS.test(`${t.perk} ${t.requires || ''}`);
    if (!mentions) continue;
    const note = String(t.requires || '');
    assert.match(note, /does not exist|not launched|no token|unlaunched/i,
      `${t.name}: "${t.perk}" rides on the token, so its note must say the `
      + `token is not here yet. Got: ${JSON.stringify(note)}`);
  }
});

// ── 3. a referral perk may not resell the paid plan ───────────────────────

test('no tier offers a benefit the paid plan sells', () => {
  // dashboard.js's plan table lists these under Pro and Elite. Offering them
  // for one and three invites is either false (it is) or it undercuts the
  // thing being sold. Matched against the perk field's own text.
  const PLAN_BENEFITS = ['priority support', 'early access', 'higher live cap',
    'unlimited ai chat', 'premium model'];
  const clashes = [];
  for (const t of REFERRAL_TIERS) {
    const perk = String(t.perk).toLowerCase();
    for (const b of PLAN_BENEFITS) if (perk.includes(b)) clashes.push(`${t.name}: ${b}`);
  }
  assert.deepEqual(clashes, [],
    'these referral perks resell a paid plan benefit that referrals do not '
    + `grant:\n  ${clashes.join('\n  ')}`);
});

test('nothing in the tree grants anything off a referral tier', () => {
  // The claim behind the whole file: the tier is DISPLAY. If a future change
  // makes it gate something, this test should fail and be rewritten — because
  // at that point the perks are real and the honesty rules change shape.
  const roots = ['auth.js', 'server.js', 'db.js'];
  const dirs = ['routes', 'lib'];
  const files = roots.map((f) => path.join(__dirname, '..', f));
  for (const d of dirs) {
    const dir = path.join(__dirname, '..', d);
    for (const f of fs.readdirSync(dir).filter((x) => x.endsWith('.js'))) {
      files.push(path.join(dir, f));
    }
  }
  const callers = files.filter((f) => /\breferralTier\s*\(/.test(codeOnly(fs.readFileSync(f, 'utf8'))));
  assert.deepEqual(callers.map((f) => path.basename(f)), ['auth.js'],
    'referralTier gained a caller — if a tier now grants something, the '
    + '"planned" perks may be describable as live, and this file needs revisiting');
});

// ── 4. referralTier itself ────────────────────────────────────────────────

test('the tier matches the count', () => {
  assert.strictEqual(referralTier(0).tier.name, REFERRAL_TIERS[0].name);
  assert.strictEqual(referralTier(1).tier.name, REFERRAL_TIERS[1].name);
  assert.strictEqual(referralTier(2).tier.name, REFERRAL_TIERS[1].name,
    'between rungs you keep the one you reached');
  const last = REFERRAL_TIERS[REFERRAL_TIERS.length - 1];
  assert.strictEqual(referralTier(last.at).tier.name, last.name);
  assert.strictEqual(referralTier(last.at + 500).next, null, 'top rung has no next');
});

test('the tier carries the perk state through to the caller', () => {
  const t = referralTier(0).tier;
  assert.ok(t.state === 'live' || t.state === 'planned');
  assert.ok('requires' in t, 'the note must survive the trip to the browser');
});

test('an unreadable count is not tier zero', () => {
  // Was `Math.max(0, Number(count) || 0)`, which answered Starter for null,
  // undefined, NaN, '' and 'abc' — five different failures rendered as the
  // one verdict that is also a real, common, true state.
  for (const bad of [null, undefined, NaN, '', 'abc', -1, 1.5, {}, [], true]) {
    assert.strictEqual(referralTier(bad), null,
      `referralTier(${JSON.stringify(bad)}) must not invent a tier`);
  }
});

// ── 5. the card omits rather than invents ─────────────────────────────────

const LIVE_TIER = { name: 'Connector', index: 1, state: 'live', requires: null,
  perk: 'Your recruits ride with you.' };
const PLANNED_TIER = { name: 'Legend', index: 4, state: 'planned',
  perk: 'A share of protocol revenue.',
  requires: 'Depends on the $RCLAW token, which does not exist yet.' };

test('a body with no tier renders no tier block', () => {
  // THE MUTATION THAT SHIPPED: `r.data.tier || { name: 'Starter' }`.
  assert.strictEqual(referralTierState({ code: 'abc', count: 12 }), null);
  assert.strictEqual(referralTierState({ code: 'abc', count: 12, tier: null }), null);
  assert.strictEqual(referralTierState({ code: 'abc', count: 12, tier: {} }), null);
});

test('an unreadable count renders no tier block', () => {
  for (const bad of [undefined, null, NaN, '3', 3.5, -1, true, {}]) {
    assert.strictEqual(referralTierState({ tier: LIVE_TIER, count: bad }), null,
      `count ${JSON.stringify(bad)} must not print as a number of friends`);
  }
});

test('zero is a real, measured zero', () => {
  // `is None`, not falsiness — the oldest rule in CLAUDE.md. Nobody having
  // taken your link yet is a fact about the world, not a failed read.
  const s = referralTierState({ tier: LIVE_TIER, count: 0,
    next: { name: 'Advocate', at: 3, remaining: 3 } });
  assert.ok(s, 'a genuine zero still gets a card');
  assert.strictEqual(s.count, 0);
  assert.strictEqual(s.next.pct, 0);
});

test('nothing renders as Starter unless the server said Starter', () => {
  for (const body of [null, undefined, {}, { count: 0 }, { tier: LIVE_TIER }]) {
    const s = referralTierState(body);
    if (s === null) continue;
    assert.notStrictEqual(s.name, 'Starter');
  }
});

// ── 6. colour is a claim ──────────────────────────────────────────────────

test('a live perk carries no caveat and the live class', () => {
  const s = referralTierState({ tier: LIVE_TIER, count: 4 });
  assert.strictEqual(s.perk.inForce, true);
  assert.strictEqual(s.perk.note, null);
  assert.strictEqual(s.perk.cls, PERK_LIVE);
});

test('a planned perk is marked, in words as well as in colour', () => {
  const s = referralTierState({ tier: PLANNED_TIER, count: 30 });
  assert.strictEqual(s.perk.inForce, false);
  assert.strictEqual(s.perk.cls, PERK_PLANNED);
  assert.match(s.perk.note, /does not exist/,
    'the note is the honest half; a class name is not readable aloud');
});

test('an unknown state is treated as not-in-force', () => {
  // Fail toward NOT claiming. A tier from an older server, or one whose state
  // a future edit forgets, must not inherit "you have this".
  for (const state of [undefined, null, '', 'LIVE', 'active', 'yes', true, 1]) {
    const s = referralTierState({ tier: { ...PLANNED_TIER, state }, count: 30 });
    assert.strictEqual(s.perk.inForce, false, `state ${JSON.stringify(state)}`);
    assert.ok(s.perk.note, 'and it says so');
  }
});

test('a planned perk with no note still says it is not in force', () => {
  const s = referralTierState({
    tier: { ...PLANNED_TIER, requires: null }, count: 30 });
  assert.strictEqual(s.perk.note, NOT_IN_FORCE,
    'colour alone does not survive greyscale, a screen reader, or a page '
    + 'whose stylesheet failed to load');
});

// ── 7. the progress bar cannot lie ────────────────────────────────────────

test('progress is a real fraction of a real milestone', () => {
  const s = referralTierState({ tier: LIVE_TIER, count: 1,
    next: { name: 'Advocate', at: 4, remaining: 3 } });
  assert.strictEqual(s.next.pct, 25);
  assert.strictEqual(s.next.remaining, 3);
  assert.strictEqual(s.topTier, false);
});

test('a milestone at zero draws no bar', () => {
  // `count / 0` is Infinity, and Math.round(Infinity * 100) is Infinity —
  // which interpolates into a style attribute as `width:Infinity%`.
  for (const at of [0, -3, NaN, undefined, null, 'ten']) {
    const s = referralTierState({ tier: LIVE_TIER, count: 2,
      next: { name: 'Advocate', at } });
    assert.strictEqual(s.next, null, `at=${JSON.stringify(at)}`);
    assert.strictEqual(s.topTier, true);
  }
});

test('overshooting a milestone caps at full, never past it', () => {
  const s = referralTierState({ tier: LIVE_TIER, count: 99,
    next: { name: 'Advocate', at: 3 } });
  assert.strictEqual(s.next.pct, 100);
  assert.strictEqual(s.next.remaining, 0, 'never a negative countdown');
});

test('no next milestone means top of the ladder, not zero progress', () => {
  const s = referralTierState({ tier: LIVE_TIER, count: 40, next: null });
  assert.strictEqual(s.next, null);
  assert.strictEqual(s.topTier, true);
});

// ── 8. reachability: the seam exists AND the card uses it ─────────────────

test('the dashboard renders the tier through the model', () => {
  // #999: a card was built inline, source-scanned, shipped, and rendered zero
  // times. Present and never reached are indistinguishable from a green suite,
  // so both halves are asserted — the page loads it, the script calls it.
  const js = codeOnly(read('public', 'js', 'dashboard.js'));
  assert.match(js, /referralTierState\s*\(/,
    'the invite panel must go through the model, not re-decide inline');
  const html = read('public', 'dashboard.html');
  assert.match(html, /referral-tier-model\.js\?v=\d+/,
    'a model the page never loads is a ReferenceError at render time');
});

test('the dashboard no longer manufactures a Starter tier', () => {
  const js = codeOnly(read('public', 'js', 'dashboard.js'));
  assert.ok(!/\|\|\s*\{\s*name:\s*'Starter'/.test(js),
    "the `|| { name: 'Starter' }` fallback printed a bottom-rung verdict "
    + 'from an absent one');
});
