'use strict';
/**
 * GET /api/verify/:key — this server's live reading of one receipt.
 *
 * The same receipt GET /api/call/:key serves this viewer (redaction
 * included), checked three ways, each answered in its own word:
 *
 *   hash     'match' | 'mismatch' | 'sealed'
 *            sha256 of seal_payload against the seal; 'sealed' while the
 *            payload is not yet revealed (a live call, a round not open)
 *   path     'reaches_root' | 'misses_root' | 'none'
 *            the Merkle path from the seal to its day's root; 'none' while
 *            the day is open, the seal is outside the committed set, or the
 *            root could not be read
 *   quorum   the day's Anchor Quorum (lib/anchor_quorum.js), every chain
 *            checked live by this server's readers: 'multi_anchored' |
 *            'short' | 'unknown', or null with no path, or 'unread' when
 *            the anchors could not be read at all
 *
 * IT IS THIS SERVER'S WORD. A third party that does not want to trust it
 * takes the bundle (GET /api/call/:key/bundle) and checks it with a verifier
 * of its own (tools/rcverify.js shares no code with this server). The
 * answer says so beside the readings, and it never says "verified" on the
 * strength of any one of them: the three are reported apart.
 */
const crypto = require('crypto');
const express = require('express');
const { lookupCall } = require('./call');
const { signedIn, receiptFor, viewerCacheControl } = require('../lib/signal_access');
const { verifyProof } = require('../lib/sealroot');
const { rootForDay } = require('../lib/seal_roots');
const { dayQuorum } = require('../lib/day_quorum');
const { rateLimit, ipKey } = require('../lib/rate_limit');

const optionalAuth = (req, res, next) => require('../auth').optionalAuth(req, res, next);
const router = express.Router();

/** The three readings for one receipt (as served to its viewer), the quorum asked through `quorumOf`. */
async function readingsOf(receipt, quorumOf = async (day) => {
  const row = await rootForDay(day);
  return row ? dayQuorum(row) : null;
}) {
  const r = receipt || {};
  const payload = typeof r.seal_payload === 'string' ? r.seal_payload : null;
  const hash = payload === null ? 'sealed'
    : crypto.createHash('sha256').update(payload, 'utf8').digest('hex') === String(r.seal) ? 'match' : 'mismatch';
  const a = r.anchor && typeof r.anchor === 'object' && Array.isArray(r.anchor.proof) && r.anchor.root ? r.anchor : null;
  const path = !a ? 'none' : verifyProof(String(r.seal), a.proof, a.root) ? 'reaches_root' : 'misses_root';
  let quorum = null;
  if (a && path === 'reaches_root') {
    try {
      const q = await quorumOf(a.day);
      quorum = q ? { status: q.status, k: q.k, groups: q.groups, pending: q.pending, unreadable: q.unreadable,
        mismatched: q.mismatched, meaning: q.meaning } : { status: 'unread' };
    } catch (e) {
      quorum = { status: 'unread' };
    }
  }
  return { hash, path, quorum, day: a ? a.day : null, root: a ? a.root : null };
}

router.get('/:key', optionalAuth, rateLimit({ name: 'verify.key', windowMs: 60000, max: 30, key: ipKey }), async (req, res) => {
  try {
    const key = String(req.params.key || '');
    const r = await lookupCall(key, { userId: req.user && req.user.user_id ? req.user.user_id : null });
    if (r.code !== 200) return res.status(r.code).json(r.body);
    const receipt = receiptFor(signedIn(req), r.body);
    const readings = await readingsOf(receipt);
    viewerCacheControl(res, 30);
    res.json({
      key, seal: receipt.seal || null, kind: receipt.kind || null, ...readings,
      checked_at: new Date().toISOString(),
      by: 'this server: take the bundle and check it yourself to trust no one',
      bundle: `/api/call/${encodeURIComponent(key)}/bundle`,
    });
  } catch (err) {
    console.error('Verify API error:', err.name || 'Error');
    res.status(500).json({ error: 'Verify unavailable', exception: err.name || 'Error' });
  }
});

module.exports = router;
module.exports.readingsOf = readingsOf;
