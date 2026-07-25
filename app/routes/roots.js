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
const { listRoots } = require('../lib/seal_roots');

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
    console.error('Seal roots error:', err.message);
    res.status(500).json({ error: 'Roots feed unavailable' });
  }
});

module.exports = router;
