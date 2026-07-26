'use strict';
/**
 * GET /api/roots — Provable Calls v3, the daily seal roots feed (public).
 *
 * One Merkle root per completed UTC day, covering EVERY seal minted that day
 * (engine calls + arena paper trades). Mirror this feed anywhere — a tweet,
 * a git repo, a chain memo — and you hold an independent timestamp: no call
 * can be back-inserted into a mirrored day without changing its root.
 * Days with zero seals are omitted, never invented.
 */

const express = require('express');
const { listRoots, rootForDay } = require('../lib/seal_roots');
const { buildAnchorPlan, verifyAnchor } = require('../lib/root_anchor');
const { authMiddleware } = require('../auth');
const { rateLimit, userKey } = require('../lib/rate_limit');
const { pool } = require('../db');

const router = express.Router();

let cache = { at: 0, body: null };

router.get('/', async (req, res) => {
  try {
    if (!cache.body || Date.now() - cache.at > 60000) {
      const roots = await listRoots(30);
      cache = { at: Date.now(), body: {
        construction: 'leaves = day seals (64-hex) deduped + sorted asc; parent = sha256(utf8(leftHex+rightHex)); odd node promoted; single leaf = root',
        roots,
      } };
    }
    res.set('Cache-Control', 'public, max-age=60');
    res.json(cache.body);
  } catch (err) {
    console.error('Seal roots error:', err.stack || err.message);
    res.status(500).json({ error: 'Roots feed unavailable' });
  }
});

// GET /api/roots/anchor-plan/:day — the exact zero-value Base transaction
// that anchors a completed day's root. DRY RUN: the server never signs.
router.get('/anchor-plan/:day', async (req, res) => {
  try {
    const row = await rootForDay(String(req.params.day || ''));
    if (!row) return res.status(404).json({ error: 'No root for that day (still open, empty, or malformed)' });
    if (row.anchor_tx) {
      return res.json({ already_anchored: true, day: row.day, root: row.root, anchor_tx: row.anchor_tx });
    }
    res.json(buildAnchorPlan(row.day, row.root));
  } catch (err) {
    console.error('Anchor plan error:', err.stack || err.message);
    res.status(500).json({ error: 'Anchor plan unavailable' });
  }
});

// GET /api/roots/verify/:day — re-verify a recorded anchor against Base,
// LIVE. Public and cached: this is the self-audit surface — anyone can ask
// the server to re-check its own claim, and the server reports exactly what
// the chain said, unknown included.
const _verifyCache = new Map();          // day -> { at, body }
router.get('/verify/:day', async (req, res) => {
  try {
    const day = String(req.params.day || '');
    const row = await rootForDay(day);
    if (!row) return res.status(404).json({ error: 'No root for that day' });
    if (!row.anchor_tx) {
      return res.json({ day: row.day, root: row.root, status: 'unanchored' });
    }
    const hit = _verifyCache.get(day);
    if (hit && Date.now() - hit.at < 5 * 60000) {
      res.set('Cache-Control', 'public, max-age=60');
      return res.json(hit.body);
    }
    const v = await verifyAnchor(row.anchor_tx, row.day, row.root);
    const body = { day: row.day, root: row.root, anchor_tx: row.anchor_tx,
      status: v.status, block_time: v.block_time || null,
      from: v.from || null, reason: v.reason || null };
    // Cache verified results; an unknown answer is retried on the next ask.
    if (v.status === 'verified') _verifyCache.set(day, { at: Date.now(), body });
    res.set('Cache-Control', 'public, max-age=60');
    res.json(body);
  } catch (err) {
    console.error('Anchor verify error:', err.stack || err.message);
    res.status(500).json({ error: 'Anchor verify unavailable' });
  }
});

// POST /api/roots/anchor { day, tx_hash } — record an anchor AFTER verifying
// it against Base. The chain is the gatekeeper: a hash whose calldata does
// not equal the tagged root payload is refused, whoever submits it. On an
// unreadable chain the answer is "try again", never a guess.
router.post('/anchor', authMiddleware, rateLimit({ windowMs: 60000, max: 6, key: userKey }), async (req, res) => {
  try {
    const day = String((req.body || {}).day || '');
    const txHash = String((req.body || {}).tx_hash || '').toLowerCase();
    const row = await rootForDay(day);
    if (!row) return res.status(404).json({ error: 'No root for that day' });
    if (row.anchor_tx) {
      return res.status(409).json({ error: 'Already anchored', anchor_tx: row.anchor_tx });
    }
    const v = await verifyAnchor(txHash, row.day, row.root);
    if (v.status === 'unknown') {
      return res.status(503).json({ error: 'Could not read Base to verify — try again shortly', reason: v.reason });
    }
    if (v.status !== 'verified') {
      return res.status(400).json({ error: 'That transaction does not anchor this root', reason: v.reason });
    }
    // First anchor wins — immutable like the root itself.
    await pool.execute(
      'UPDATE seal_roots SET anchor_tx = ?, anchored_at = ? WHERE day = ? AND anchor_tx IS NULL',
      [txHash, new Date(v.block_time), day]);
    cache = { at: 0, body: null };            // feed shows the anchor next read
    res.json({ ok: true, day, anchor_tx: txHash, block_time: v.block_time, from: v.from });
  } catch (err) {
    console.error('Anchor submit error:', err.stack || err.message);
    res.status(500).json({ error: 'Anchor submit failed' });
  }
});

module.exports = router;
