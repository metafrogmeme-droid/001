'use strict';
/**
 * Agent identity — a slug that belongs to someone, sealed when it is claimed.
 *
 * WHAT THIS FILE IS DEFENDING
 *
 * Three namespaces write to `arena_positions.agent_slug` / `arena_trades
 * .agent_slug` and none of them knew about each other: claimed agents, public
 * community strategies, and the engine's agent catalogue.
 * `/api/public/agent-record/:slug` selects `WHERE agent_slug = ?` with no
 * further qualification — so a slug collision does not merely confuse, it SUMS
 * two different agents' trades into one published record. Claim time is the
 * only place that ambiguity can still be prevented, which is why an unreadable
 * catalogue refuses the claim rather than allowing it: the question "is this
 * slug free" going unanswered is not the answer "yes".
 *
 * And the chain a stranger walks — payload → seal → day's root → Base block
 * time — stops for four unrelated reasons, one of which (a seal missing from a
 * committed leaf set) is the signature of a back-inserted row. Collapsing them
 * into one "not anchored" would hide the alarming case behind the routine one.
 */
process.env.JWT_SECRET = 'j'.repeat(64);
delete process.env.DATABASE_URL;

const test = require('node:test');
const assert = require('node:assert');
const crypto = require('node:crypto');

const { pool } = require('../db');
const agents = require('../lib/agents');
const { sealsForDay } = require('../lib/seal_roots');

const USER = 4242;
const OTHER = 9999;

test.beforeEach(() => {
  // The shim's tables are plain arrays; each test starts from an empty one.
  if (pool.agents) pool.agents.length = 0;
});

// ── the sealed payload ──────────────────────────────────────────────────────

test('the claim payload is byte-stable and carries NO owner', () => {
  const at = new Date('2026-08-26T09:00:00.000Z');
  const p = agents.canonicalClaim({ slug: 'alpha', display_name: 'Alpha', claimed_at: at });
  assert.equal(p, JSON.stringify({
    v: 1, kind: 'agent-claim', slug: 'alpha',
    display_name: 'Alpha', claimed_at: '2026-08-26T09:00:00.000Z',
  }));
  // Seals are the input to a PUBLIC Merkle root. Sealing the owner would
  // publish a user↔agent mapping through the receipt surface for nothing.
  assert.ok(!/user|owner|email/i.test(p), `the payload leaks an owner: ${p}`);
});

test('the seal is a plain sha256 anyone can re-derive', () => {
  const p = agents.canonicalClaim(
    { slug: 'beta', display_name: null, claimed_at: new Date(0) });
  assert.equal(agents.sealOf(p),
    crypto.createHash('sha256').update(p, 'utf8').digest('hex'));
  assert.match(agents.sealOf(p), /^[0-9a-f]{64}$/);
});

// ── slug validation ─────────────────────────────────────────────────────────

test('slug rules and reserved names', () => {
  assert.equal(agents.validateSlug('Good-Slug1').slug, 'good-slug1');
  for (const bad of ['-leading', 'has space', 'UPPER!', '', 'x'.repeat(65)]) {
    assert.equal(agents.validateSlug(bad).ok, false, `${bad!==''?bad:'(empty)'} should be refused`);
  }
  assert.equal(agents.validateSlug('runeclaw').code, 'reserved');
});

// ── the collision rules that stop two records merging ───────────────────────

test('a slug already claimed cannot be claimed again', async () => {
  assert.equal((await agents.claim(USER, 'twin')).ok, true);
  const second = await agents.claim(OTHER, 'twin');
  assert.equal(second.ok, false);
  assert.equal(second.code, 'claimed');
});

test('a slug naming a public community strategy is refused', async (t) => {
  t.mock.method(require('../lib/user_strategies'), 'getPublicBySlug',
    async () => ({ slug: 'momentum', rules: [] }));
  const r = await agents.claim(USER, 'momentum');
  assert.equal(r.ok, false);
  assert.equal(r.code, 'community_strategy');
  assert.match(r.error, /community strategy/);
});

test('a slug naming a catalogue agent is refused', async (t) => {
  t.mock.method(require('../lib/user_strategies'), 'getPublicBySlug', async () => null);
  t.mock.method(require('../lib/agent_catalogue'), 'loadCatalogueChecked',
    async () => ({ readable: true, agents: [{ id: 'Scalper' }] }));
  const r = await agents.claim(USER, 'scalper');
  assert.equal(r.ok, false);
  assert.equal(r.code, 'catalogue_agent');
});

test('AN UNREADABLE CATALOGUE REFUSES THE CLAIM — absent is not "free"', async (t) => {
  t.mock.method(require('../lib/user_strategies'), 'getPublicBySlug', async () => null);
  t.mock.method(require('../lib/agent_catalogue'), 'loadCatalogueChecked',
    async () => ({ readable: false, agents: [] }));
  const r = await agents.claim(USER, 'unknowable');
  assert.equal(r.ok, false, 'a slug we could not check was allowed through');
  assert.equal(r.code, 'catalogue_unreadable');
  assert.match(r.error, /Nothing was claimed/);

  const rows = await pool.execute('SELECT slug FROM agents WHERE slug = ?', ['unknowable']);
  assert.equal(rows[0].length, 0, 'nothing may be written when the check could not run');
});

test('a THROWING catalogue is the same answer as an unreadable one', async (t) => {
  t.mock.method(require('../lib/user_strategies'), 'getPublicBySlug', async () => null);
  t.mock.method(require('../lib/agent_catalogue'), 'loadCatalogueChecked',
    async () => { throw new Error('ECONNREFUSED'); });
  const r = await agents.claim(USER, 'boom');
  assert.equal(r.ok, false);
  assert.equal(r.code, 'catalogue_unreadable');
});

test('a per-user cap bounds how many identities one account can mint', async () => {
  for (let i = 0; i < agents.MAX_AGENTS_PER_USER; i++) {
    assert.equal((await agents.claim(USER, `bot-${i}`)).ok, true, `claim ${i}`);
  }
  const over = await agents.claim(USER, 'one-too-many');
  assert.equal(over.ok, false);
  assert.equal(over.code, 'too_many');
});

// ── ownership ───────────────────────────────────────────────────────────────

test('ownedBy answers only for the owner, and fails CLOSED', async (t) => {
  await agents.claim(USER, 'mine');
  assert.equal(await agents.ownedBy(USER, 'mine'), true);
  assert.equal(await agents.ownedBy(OTHER, 'mine'), false);
  assert.equal(await agents.ownedBy(USER, 'not-a-thing'), false);

  // Every caller of this is deciding whether to ALLOW something, so an
  // unreadable database must land on the same side as "no".
  t.mock.method(pool, 'execute', async () => { throw new Error('db down'); });
  assert.equal(await agents.ownedBy(USER, 'mine'), false,
    'an unreadable database granted ownership');
});

test('the public view never exposes the owner', async () => {
  await agents.claim(USER, 'public-view', 'Public View');
  const a = await agents.bySlug('public-view');
  assert.equal(a.slug, 'public-view');
  assert.equal(a.display_name, 'Public View');
  assert.match(a.seal, /^[0-9a-f]{64}$/);
  assert.ok(!('user_id' in a), 'the public view carries the owner');
  // The seal on the record must be the hash of the payload on the record —
  // a stranger re-derives exactly this.
  assert.equal(agents.sealOf(a.seal_payload), a.seal);
  assert.equal(await agents.bySlug('nope'), null);
});

// ── the claim rides into the day's Merkle root ──────────────────────────────

test('a claim seal is picked up by the daily seal sweep', async () => {
  const r = await agents.claim(USER, 'rooted');
  assert.equal(r.ok, true);
  const day = new Date(r.agent.claimed_at).toISOString().slice(0, 10);
  const leaves = await sealsForDay(day);
  assert.ok(leaves.includes(r.agent.seal),
    'the claim was sealed but never reaches the root — the anchor would prove nothing about it');
});

// ── the chain walk: five outcomes, never collapsed into one ─────────────────
//
// This is the part a reader acts on. `anchorFor()` answers null for four
// unrelated reasons and one of them — a seal absent from a day's COMMITTED
// leaf set — is what a back-inserted row looks like. Each is driven here.

const { chainFor, SETTLED } = require('../lib/agent_chain');

const CLAIM = { seal: 'a'.repeat(64), claimed_at: '2026-08-20T10:00:00.000Z' };
const NOW = () => Date.parse('2026-08-26T10:00:00.000Z');

test('an anchored day reports the Base transaction', async () => {
  const c = await chainFor(CLAIM, {
    now: NOW,
    anchorFor: async () => ({ day: '2026-08-20', root: 'r', seal_count: 9,
      proof: [], anchor_tx: '0x' + 'b'.repeat(64), anchored_at: 'when' }),
  });
  assert.equal(c.status, 'anchored');
  assert.equal(c.anchor_tx, '0x' + 'b'.repeat(64));
  assert.match(c.note, /block time/);
  assert.equal(c.verify_url, '/api/roots/verify/2026-08-20');
});

test('a rooted but UNANCHORED day says the date still rests on our clock', async () => {
  const c = await chainFor(CLAIM, {
    now: NOW,
    anchorFor: async () => ({ day: '2026-08-20', root: 'r', seal_count: 9,
      proof: [], anchor_tx: null, anchored_at: null }),
  });
  assert.equal(c.status, 'rooted');
  assert.equal(c.anchor_tx, null);
  assert.match(c.note, /rests on our clock/,
    'an unanchored day must not read like an anchored one');
});

test('a claim made TODAY is not a missing anchor — the day is still open', async () => {
  const c = await chainFor(
    { seal: 'a'.repeat(64), claimed_at: '2026-08-26T09:00:00.000Z' },
    { now: NOW, anchorFor: async () => null, rootForDay: async () => null });
  assert.equal(c.status, 'day_open');
  assert.match(c.note, /COMPLETED UTC days/);
});

test('a completed day with no root yet says exactly that', async () => {
  const c = await chainFor(CLAIM, {
    now: NOW, anchorFor: async () => null, rootForDay: async () => null });
  assert.equal(c.status, 'no_root');
});

test('A SEAL MISSING FROM A COMMITTED LEAF SET IS THE ALARMING CASE', async () => {
  const c = await chainFor(CLAIM, {
    now: NOW,
    anchorFor: async () => null,
    rootForDay: async () => ({ root: 'committed-root', leaves: '["x"]' }),
  });
  assert.equal(c.status, 'not_in_root');
  assert.match(c.note, /NOT in that day's committed leaf set/);
  assert.match(c.note, /unproven/,
    'a back-inserted row must read as a warning, not as "not anchored yet"');
  // The four routine outcomes and this one must never share a status string.
  assert.ok(!['anchored', 'rooted', 'day_open', 'no_root'].includes(c.status));
});

test('an unreadable roots table answers UNKNOWN, and is never cached', async () => {
  const c = await chainFor(CLAIM, {
    now: NOW, anchorFor: async () => { throw new Error('db down'); } });
  assert.equal(c.status, 'unknown');
  assert.match(c.note, /nothing is claimed either way/);
  assert.ok(!SETTLED.has('unknown'),
    'caching UNKNOWN would freeze a transient failure into a minute of stated fact');
  for (const s of ['anchored', 'rooted', 'day_open', 'no_root', 'not_in_root']) {
    assert.ok(SETTLED.has(s), `${s} is a settled answer and should cache`);
  }
});
