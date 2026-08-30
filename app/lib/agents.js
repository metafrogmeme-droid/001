'use strict';
/**
 * Agent identity — a slug that belongs to someone, sealed when it is claimed.
 *
 * WHY THIS EXISTS
 *
 * An autonomous agent can already paper-trade over MCP with an Arena key: the
 * trade is sealed at open, rides into the day's Merkle root, and ranks on the
 * public leaderboard. What it cannot do is say "this is MY record, check it" —
 * `openForUser` never writes `agent_slug`, so the per-agent record surface
 * (`/api/public/agent-record/:slug`) can never see an autonomous agent's own
 * trades. Its identity is its user account.
 *
 * That is the missing half of a credential. `verify.py` states the general
 * form of the problem for statements: a record that verifies internally still
 * says only "somebody with some keypair computed these numbers". A track
 * record nobody can attribute is not a track record.
 *
 * WHAT A CLAIM IS, AND WHAT IT IS NOT
 *
 * A claim binds a slug to a user and records WHEN. It is sealed with the same
 * primitive every other sealed surface uses, so the claim ends up in that
 * day's root and is anchored on Base with everything else — which makes
 * "this agent existed on this date" independently checkable rather than a
 * reading of our own clock.
 *
 * It does NOT prove who operates the agent to a third party. That needs a
 * signature over the claim from a key the agent controls, and it is
 * deliberately not here: shipping a `pubkey` column that nothing verifies
 * would put an attribution claim on a public surface with nothing behind it.
 * The seal is honest about being a timestamp and an ownership record, and
 * says nothing it cannot support.
 *
 * THE SEALED PAYLOAD CARRIES NO USER ID
 *
 * Ownership is an authorization fact — it decides whose keys may trade as this
 * agent — not a public one, and seals are the input to a PUBLIC root. Sealing
 * `owner_user_id` would publish a user↔agent mapping through the receipt
 * surface for no gain. Hashing it instead would be theatre: user ids are small
 * integers and the whole preimage space is walkable in a second. So the seal
 * commits to the public facts only, and ownership is enforced at write time
 * against the column.
 */

const crypto = require('node:crypto');
const { pool } = require('../db');

/** Same shape the arena attribution path and the record route already accept. */
const SLUG_RE = /^[a-z0-9][a-z0-9-]{0,63}$/;

/** A bound, so a loop cannot mint agent identities forever. */
const MAX_AGENTS_PER_USER = 10;

/**
 * Reserved because a collision would MERGE two different records.
 *
 * `/api/public/agent-record/:slug` selects `WHERE agent_slug = ?` with no
 * further qualification, and the member-copy path in routes/arena.js writes
 * catalogue and community-strategy slugs into that same column. So an
 * autonomous agent claiming a slug that already names a community strategy
 * would not merely be confusing — the two sets of trades would be summed into
 * one published record. Refused at claim time, which is the only place the
 * ambiguity can still be prevented.
 */
const RESERVED = new Set([
  'runeclaw', 'admin', 'api', 'system', 'engine', 'official', 'support',
]);

/**
 * Canonical claim payload. Key insertion order IS the contract (v:1), exactly
 * as in lib/callseal.js: a client hashes the served string verbatim, so there
 * is no re-canonicalization step to drift.
 */
function canonicalClaim(c) {
  return JSON.stringify({
    v: 1,
    kind: 'agent-claim',
    slug: String(c.slug),
    display_name: c.display_name ? String(c.display_name) : null,
    claimed_at: new Date(c.claimed_at).toISOString(),
  });
}

function sealOf(payload) {
  return crypto.createHash('sha256').update(payload, 'utf8').digest('hex');
}

/** `{ ok }` or `{ ok: false, error, code }` — never a thrown string. */
function validateSlug(slug) {
  const s = String(slug || '').toLowerCase();
  if (!SLUG_RE.test(s)) {
    return { ok: false, code: 'bad_slug',
      error: 'A slug is 1-64 characters: lowercase letters, digits and hyphens, starting with a letter or digit.' };
  }
  if (RESERVED.has(s)) {
    return { ok: false, code: 'reserved', error: `"${s}" is reserved.` };
  }
  return { ok: true, slug: s };
}

/**
 * Is this slug already spoken for by something that writes to `agent_slug`?
 *
 * Three namespaces share that column and none of them knew about each other:
 * claimed agents (here), public community strategies, and the engine's agent
 * catalogue. Checked in that order, and an UNREADABLE catalogue refuses the
 * claim rather than allowing it — the whole point is preventing a merge, and
 * "I could not check" is not "it is free".
 *
 * @returns null when free, else a short reason code.
 */
async function slugTaken(slug) {
  const [rows] = await pool.execute('SELECT slug FROM agents WHERE slug = ?', [slug]);
  if (rows && rows.length) return 'claimed';

  try {
    const cs = await require('./user_strategies').getPublicBySlug(slug);
    if (cs) return 'community_strategy';
  } catch (e) {
    return 'community_unreadable';
  }

  try {
    const cat = await require('./agent_catalogue').loadCatalogueChecked();
    if (!cat.readable) return 'catalogue_unreadable';
    if ((cat.agents || []).some((a) => String(a.id).toLowerCase() === slug)) {
      return 'catalogue_agent';
    }
  } catch (e) {
    return 'catalogue_unreadable';
  }
  return null;
}

const TAKEN_REASON = {
  claimed: 'That slug is already claimed.',
  community_strategy: 'That slug already names a public community strategy.',
  catalogue_agent: 'That slug already names a RUNECLAW catalogue agent.',
  community_unreadable:
    'The community strategy catalogue could not be read, so we cannot tell whether that slug is free. Nothing was claimed — try again shortly.',
  catalogue_unreadable:
    'The agent catalogue could not be read, so we cannot tell whether that slug is free. Nothing was claimed — try again shortly.',
};

/**
 * Claim a slug. Returns `{ ok: true, agent }` or `{ ok: false, error, code }`.
 *
 * Nothing is written unless every check passes, and the UNIQUE index on `slug`
 * is the real arbiter: two concurrent claims both pass `slugTaken` and one of
 * them loses on insert, which is reported as taken rather than as a 500.
 */
async function claim(userId, slug, displayName = '') {
  const v = validateSlug(slug);
  if (!v.ok) return { ok: false, error: v.error, code: v.code };

  const [own] = await pool.execute(
    'SELECT COUNT(*) AS n FROM agents WHERE user_id = ?', [userId]);
  const n = Number(own && own[0] && own[0].n) || 0;
  if (n >= MAX_AGENTS_PER_USER) {
    return { ok: false, code: 'too_many',
      error: `At most ${MAX_AGENTS_PER_USER} agents — release one first.` };
  }

  const taken = await slugTaken(v.slug);
  if (taken) return { ok: false, code: taken, error: TAKEN_REASON[taken] };

  const name = String(displayName || '').replace(/[^\w .\-]/g, '').slice(0, 80);
  const claimedAt = new Date();
  const payload = canonicalClaim(
    { slug: v.slug, display_name: name || null, claimed_at: claimedAt });
  const seal = sealOf(payload);

  try {
    await pool.execute(
      'INSERT INTO agents (slug, user_id, display_name, seal, seal_payload, sealed_at, created_at) '
      + 'VALUES (?, ?, ?, ?, ?, ?, ?)',
      [v.slug, userId, name || null, seal, payload, claimedAt, claimedAt]);
  } catch (err) {
    if (err && err.code === 'ER_DUP_ENTRY') {
      return { ok: false, code: 'claimed', error: TAKEN_REASON.claimed };
    }
    throw err;
  }
  return { ok: true, agent: {
    slug: v.slug, display_name: name || null, seal, seal_payload: payload,
    claimed_at: claimedAt.toISOString(),
  } };
}

/** The public view of one agent, or null. Never exposes the owner. */
async function bySlug(slug) {
  const v = validateSlug(slug);
  if (!v.ok) return null;
  const [rows] = await pool.execute(
    'SELECT slug, display_name, seal, seal_payload, sealed_at FROM agents WHERE slug = ?',
    [v.slug]);
  const r = rows && rows[0];
  if (!r) return null;
  return {
    slug: r.slug,
    display_name: r.display_name || null,
    seal: r.seal,
    seal_payload: r.seal_payload,
    claimed_at: r.sealed_at ? new Date(r.sealed_at).toISOString() : null,
  };
}

/**
 * Every claimed agent, newest first — the public index.
 *
 * It exists because `/a/:slug` shipped with nothing linking to it. A page
 * reachable only by already knowing its URL is the shape CLAUDE.md names: code
 * that is present and code that is reached are different things, and from the
 * outside they are indistinguishable.
 *
 * It cannot be linked from `/agents/:slug` instead, and the reason is
 * structural rather than an oversight: `slugTaken` REFUSES a claim on any slug
 * that already names a community strategy or a catalogue agent, so the two
 * namespaces are disjoint by construction. No marketplace page can ever have a
 * `/a/` counterpart.
 *
 * Never selects the owner column — same rule as `bySlug`.
 */
async function listClaimed(limit = 100) {
  const n = Math.max(1, Math.min(500, Number(limit) || 100));
  // Interpolated, not bound: mysql2's execute() sends JS numbers as DOUBLE and
  // MySQL rejects a DOUBLE as a prepared LIMIT argument. Clamped to an integer
  // immediately above, so there is nothing here a caller can influence.
  const [rows] = await pool.execute(
    'SELECT slug, display_name, seal, sealed_at FROM agents ORDER BY id DESC LIMIT ' + n);
  return (rows || []).map((r) => ({
    slug: r.slug,
    display_name: r.display_name || null,
    seal: r.seal,
    claimed_at: r.sealed_at ? new Date(r.sealed_at).toISOString() : null,
  }));
}

/** Agents this user owns. */
async function forUser(userId) {
  const [rows] = await pool.execute(
    'SELECT slug, display_name, seal, sealed_at FROM agents WHERE user_id = ? ORDER BY id DESC',
    [userId]);
  return (rows || []).map((r) => ({
    slug: r.slug,
    display_name: r.display_name || null,
    seal: r.seal,
    claimed_at: r.sealed_at ? new Date(r.sealed_at).toISOString() : null,
  }));
}

/**
 * Does this user own this slug? The authorization question, asked directly.
 *
 * Returns false on an unreadable database rather than throwing: every caller
 * is deciding whether to ALLOW something, so "I could not check" must land on
 * the same side as "no".
 */
async function ownedBy(userId, slug) {
  const v = validateSlug(slug);
  if (!v.ok) return false;
  try {
    const [rows] = await pool.execute(
      'SELECT id FROM agents WHERE slug = ? AND user_id = ?', [v.slug, userId]);
    return !!(rows && rows.length);
  } catch (err) {
    console.error('[agents] ownedBy failed:', err.stack || err.message);
    return false;
  }
}

module.exports = {
  SLUG_RE, MAX_AGENTS_PER_USER, RESERVED,
  canonicalClaim, sealOf, validateSlug, slugTaken,
  claim, bySlug, listClaimed, forUser, ownedBy,
};
