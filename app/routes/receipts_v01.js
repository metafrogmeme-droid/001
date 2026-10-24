'use strict';
/**
 * GET /v1/receipts/:id?format=aat — one existing receipt, as an Agent Audit
 * Trail record beside its proof bundle.
 *
 * Off unless CLAW_GUARD_V01 is set. The receipt is the one GET /api/call/:id
 * would serve this viewer, redaction included, and the bundle is built the
 * same way as GET /api/call/:id/bundle. The audit record is hashes and
 * categories from that bundle (lib/aat_export.js). A payload that is still
 * sealed, or a time that cannot be read, is not exported: inventing the
 * record would put a version and a timestamp the receipt does not have.
 */

const express = require('express');
const { optionalAuth } = require('../auth');
const { rateLimit, ipKey } = require('../lib/rate_limit');
const { flag_on_for } = require('../lib/admin_preview');
const { exportAat, AAT_DRAFT } = require('../lib/aat_export');

const router = express.Router();
router.use(optionalAuth);

function formatOf(q) {
  const raw = q && q.format;
  const v = Array.isArray(raw) ? raw[0] : raw;
  return v == null ? '' : String(v);
}

router.get('/:id', rateLimit({ name: 'receipts_v01_aat', windowMs: 60000, max: 30, key: ipKey }), async (req, res) => {
  if (!(await flag_on_for(req, 'CLAW_GUARD_V01'))) return res.status(404).json({ error: 'Not found' });
  if (formatOf(req.query) !== 'aat') {
    return res.status(400).json({ error: 'format must be aat' });
  }
  try {
    const key = String(req.params.id || '');
    const { lookupCall } = require('./call');
    const { signedIn, receiptFor, viewerCacheControl } = require('../lib/signal_access');
    const r = await lookupCall(key, { userId: req.user && req.user.user_id ? req.user.user_id : null });
    if (r.code !== 200) return res.status(r.code).json(r.body);
    const receipt = receiptFor(signedIn(req), r.body);
    const day = receipt && receipt.anchor && receipt.anchor.day;
    let otsRow = null;
    let otsUnread = false;
    if (day) {
      try { otsRow = await require('../lib/root_ots').rowFor(day); } catch (e) { otsUnread = true; }
    }
    const bundle = require('../lib/proof_bundle').bundleOf(key, receipt, { otsRow, otsUnread });
    const exported = exportAat(bundle);
    if (!exported.ok) {
      return res.status(422).json({
        error: 'This receipt cannot be exported. Its payload or its time could not be read, so an audit record was not invented.',
        draft: AAT_DRAFT,
      });
    }
    viewerCacheControl(res, 60);
    res.status(200).json(exported.body);
  } catch (err) {
    console.error('[receipts_v01] export failed:', err && err.constructor ? err.constructor.name : 'Error');
    res.status(500).json({ error: 'The export could not run' });
  }
});

module.exports = router;
