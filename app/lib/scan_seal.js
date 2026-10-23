'use strict';
/**
 * Sealing a pre-signature scan — the write path, and the rules around it.
 *
 * WHAT THIS IS FOR
 *
 * `xray_transaction` decodes what a transaction actually does; `scan_transaction`
 * flags attack patterns in text. Both end their own descriptions with "nothing
 * is stored", which is a real privacy promise and also the reason no agent can
 * ever show what it was told before it acted. When an agent signs something
 * that drains a wallet, there is currently no artifact anywhere recording what
 * the checker said beforehand.
 *
 * Sealing the verdict at scan time, folding it into the day's Merkle root and
 * anchoring that on Base turns "we warned you" into something a third party can
 * check without trusting us.
 *
 * ONLY A KEYED CALLER SEALS, AND THAT IS NOT A PAYWALL
 *
 * Both tools sit in the PUBLIC, unauthenticated registry — 1 of 30. Sealing
 * every anonymous call would let anyone append unbounded leaves to a public
 * daily root: verification cost grows for everyone, and a root full of
 * stranger-supplied hashes says nothing about anyone.
 *
 * It is also the wrong artifact. The claim worth making is "THIS agent was
 * told this, before it signed" — an anonymous sealed verdict identifies nobody
 * and proves nothing about who was warned. So a scan seals when the caller
 * presented an Arena key, bounded by the 5-keys-per-user cap and the existing
 * rate limits, and attributed to that key's claimed agent when it has one.
 *
 * Anonymous callers get exactly what they got before, including the promise:
 * nothing stored.
 *
 * FAILING TO SEAL MUST NEVER FAIL THE SCAN
 *
 * The scan is the safety feature; the receipt is evidence about it. A database
 * that cannot be written must not stop an agent finding out that its calldata
 * says `setApprovalForAll`. So every failure here is swallowed and reported as
 * `sealed: false` with a reason — never raised, and never silently rendered as
 * though a receipt exists.
 */

const crypto = require('node:crypto');
const { pool } = require('../db');
const { sealScan, sealGuard, sealRwa, inputDigest } = require('./callseal');

/** Same shape as `newTradeKey` — short, unguessable, URL-safe. */
function newScanKey() {
  return 'sc_' + crypto.randomBytes(12).toString('base64url');
}

/**
 * Ids only, never prose.
 *
 * The decoder returns `{tid, en, params}` per action, where `en` is English
 * copy that will be reworded and translated. Hashing it would make a receipt
 * that stops verifying the day someone fixes a typo, so the seal commits to
 * the stable `tid` and the rendering stays free to change.
 */
function actionIds(actions) {
  return (Array.isArray(actions) ? actions : [])
    .map((a) => (a && a.tid) || (a && a.id) || '')
    .filter(Boolean);
}

function flagIds(flags) {
  return (Array.isArray(flags) ? flags : [])
    .map((f) => (typeof f === 'string' ? f : (f && f.id) || ''))
    .filter(Boolean);
}

/**
 * Seal one scan, if the caller is entitled to one.
 *
 * @param opts.tool           'xray_transaction' | 'scan_transaction'
 * @param opts.input          the exact bytes the caller sent — HASHED, never stored
 * @param opts.result         the tool's own return value
 * @param opts.deterministic  true only for a reproducible decode
 * @param opts.ctx            the MCP context: `{ userId, agentSlug }` or null
 * @returns `{ sealed: true, scan_key, seal, sealed_at }`
 *          or `{ sealed: false, reason }` — always one or the other, never a throw.
 */
async function sealIfKeyed(opts) {
  const { tool, input, result, deterministic, ctx } = opts || {};
  const userId = ctx && ctx.userId;
  if (!userId) {
    // Not an error and not a degraded state: this is the documented behaviour
    // for an anonymous caller, and the tool's "nothing is stored" promise.
    return { sealed: false, reason: 'anonymous' };
  }
  try {
    const digest = inputDigest(input);
    const scannedAt = new Date();
    const scanKey = newScanKey();
    const payload = {
      scan_key: scanKey,
      tool: String(tool || ''),
      deterministic: !!deterministic,
      input_sha256: digest.sha256,
      input_bytes: digest.bytes,
      actions: actionIds(result && result.actions),
      flags: flagIds(result && result.flags),
      // Three-valued. `undefined` means the tool does not answer the question
      // — the firewall scan has no notion of an undecodable input — and that
      // is NOT the same as answering "no, it was recognised".
      unknown: result && 'unknown' in result ? !!result.unknown : null,
      agent_slug: (ctx && ctx.agentSlug) || null,
      scanned_at: scannedAt,
    };
    const { seal, seal_payload } = sealScan(payload);
    await pool.execute(
      'INSERT INTO scan_seals (scan_key, user_id, agent_slug, tool, seal, seal_payload, sealed_at) '
      + 'VALUES (?, ?, ?, ?, ?, ?, ?)',
      [scanKey, userId, payload.agent_slug, payload.tool, seal, seal_payload, scannedAt]);
    return { sealed: true, scan_key: scanKey, seal, sealed_at: scannedAt.toISOString() };
  } catch (err) {
    // The scan already succeeded and its answer is what protects the caller.
    console.error('[scan_seal] could not seal:', err.stack || err.message);
    return { sealed: false, reason: 'seal_failed' };
  }
}

/**
 * Seal one Claw Guard verdict (v10, `lib/callseal.js` canonicalGuardPayload)
 * for a signed-in caller of routes/guard.js.
 *
 * The same rules as `sealIfKeyed`, for the same reasons: an anonymous caller
 * gets the verdict and nothing is stored; a caller who is identified gets a
 * receipt, bounded by the route's rate limit; and failing to seal never
 * fails the check. The row goes in `scan_seals` beside the v5 scans, so it is
 * a leaf of the same daily root and opens at the same /call/<key>.
 *
 * @param opts.request  'tx' | 'typed_data' | 'authorization'
 * @param opts.input    the exact text the caller sent: HASHED, never stored
 * @param opts.verdict  guard-model.js `verdict()` result
 * @param opts.decoded  the X-ray result the verdict read (for `unknown`)
 * @param opts.simulation  the simulation reading, or null when none was asked for
 * @param opts.userId   the signed-in caller, or null
 */
async function sealGuardIfSignedIn(opts) {
  const { request, input, verdict, decoded, simulation, userId } = opts || {};
  if (!userId) return { sealed: false, reason: 'anonymous' };
  try {
    const digest = inputDigest(input);
    const scannedAt = new Date();
    const scanKey = newScanKey();
    const v = verdict || {};
    const { seal, seal_payload } = sealGuard({
      scan_key: scanKey,
      request,
      input_sha256: digest.sha256,
      input_bytes: digest.bytes,
      level: v.level,
      reasons: v.reasons,
      actions: actionIds(v.actions),
      unknown: decoded && 'unknown' in decoded ? !!decoded.unknown : null,
      simulation: simulation == null ? null : (simulation.ok
        ? { ok: true, chain_id: simulation.chain_id, block: simulation.block, status: simulation.status,
            changes: Array.isArray(simulation.changes) ? simulation.changes.length : 0 }
        : { ok: false, chain_id: simulation.chain_id, reason: simulation.reason }),
      scanned_at: scannedAt,
    });
    await pool.execute(
      'INSERT INTO scan_seals (scan_key, user_id, agent_slug, tool, seal, seal_payload, sealed_at) '
      + 'VALUES (?, ?, ?, ?, ?, ?, ?)',
      [scanKey, userId, null, 'claw_guard', seal, seal_payload, scannedAt]);
    return { sealed: true, scan_key: scanKey, seal, sealed_at: scannedAt.toISOString() };
  } catch (err) {
    console.error('[scan_seal] could not seal a guard verdict:', err.constructor.name);
    return { sealed: false, reason: 'seal_failed' };
  }
}

/**
 * Seal one RWA pre-check (v11) for a signed-in caller.
 *
 * Same rules as a Claw Guard verdict: anonymous stores nothing, a seal
 * failure is reported and does not fail the check, and the row is a leaf of
 * the same daily root. The payload carries flag ids, not addresses.
 */
async function sealRwaIfSignedIn(opts) {
  const { input, projection, userId } = opts || {};
  if (!userId) return { sealed: false, reason: 'anonymous' };
  try {
    const digest = inputDigest(input);
    const scannedAt = new Date();
    const scanKey = newScanKey();
    const p = projection || {};
    const { seal, seal_payload } = sealRwa({
      scan_key: scanKey,
      input_sha256: digest.sha256,
      input_bytes: digest.bytes,
      chain_id: p.chain_id,
      block: p.block,
      flags: p.flags,
      unreadable: p.unreadable,
      scanned_at: scannedAt,
    });
    await pool.execute(
      'INSERT INTO scan_seals (scan_key, user_id, agent_slug, tool, seal, seal_payload, sealed_at) '
      + 'VALUES (?, ?, ?, ?, ?, ?, ?)',
      [scanKey, userId, null, 'rwa_precheck', seal, seal_payload, scannedAt]);
    return { sealed: true, scan_key: scanKey, seal, sealed_at: scannedAt.toISOString() };
  } catch (err) {
    console.error('[scan_seal] could not seal an rwa pre-check:', err && err.constructor ? err.constructor.name : 'Error');
    return { sealed: false, reason: 'seal_failed' };
  }
}

/** One receipt by key, or null. Never exposes `user_id`. */
async function byKey(scanKey) {
  const k = String(scanKey || '');
  if (!k) return null;
  const [rows] = await pool.execute(
    'SELECT scan_key, agent_slug, tool, seal, seal_payload, sealed_at FROM scan_seals WHERE scan_key = ?',
    [k]);
  const r = rows && rows[0];
  if (!r) return null;
  return {
    scan_key: r.scan_key,
    agent_slug: r.agent_slug || null,
    tool: r.tool,
    seal: r.seal,
    seal_payload: r.seal_payload,
    sealed_at: r.sealed_at ? new Date(r.sealed_at).toISOString() : null,
  };
}

module.exports = {
  sealIfKeyed, sealGuardIfSignedIn, sealRwaIfSignedIn, byKey, newScanKey, actionIds, flagIds,
};
