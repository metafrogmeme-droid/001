'use strict';
/**
 * GET /api/roots — Provable Calls v3, the daily seal roots feed (public).
 *
 * One Merkle root per completed UTC day, covering EVERY seal minted that day
 * (engine calls, arena paper trades, agent claims, scan seals, the Daily
 * Duel's rounds, calls and settlements). Mirror this feed anywhere — a tweet,
 * a git repo, a chain memo — and you hold an independent timestamp: no call
 * can be back-inserted into a mirrored day without changing its root.
 * Days with zero seals are omitted, never invented.
 *
 * ANCHORS, ON MORE THAN ONE CHAIN (2026-10-19). A completed day's root may be
 * anchored on Base and on the DogeOS testnet, each by anyone's own wallet:
 * the server hands out the exact zero-value transaction (dry run), never
 * signs, and records a hash only after the chain itself confirmed the
 * calldata. `?chain=` names the chain on the three anchor routes; the
 * registry (`/chains`) is lib/root_anchor.js's one reading of each chain's
 * id, RPCs and explorer, so a page or an app asks here rather than carrying
 * literals of its own.
 */
const express = require('express');
const { listRoots, rootForDay, anchorsFor, recordAnchor } = require('../lib/seal_roots');
const { buildAnchorPlan, verifyAnchor, publicChains, chainInfo, DEFAULT_CHAIN } = require('../lib/root_anchor');
const { authMiddleware } = require('../auth');
const { rateLimit, userKey } = require('../lib/rate_limit');

const router = express.Router();

let cache = { at: 0, body: null };

/** The chain a request names (`?chain=` or body.chain), Base by default; null when unknown. */
function chainOf(req) {
  const raw = (req.query && req.query.chain) || (req.body && req.body.chain) || DEFAULT_CHAIN;
  return chainInfo(String(raw));
}

router.get('/', async (req, res) => {
  try {
    if (!cache.body || Date.now() - cache.at > 60000) {
      const roots = await listRoots(30);
      cache = { at: Date.now(), body: {
        construction: 'leaves = day seals (64-hex) deduped + sorted asc; parent = sha256(utf8(leftHex+rightHex)); odd node promoted; single leaf = root',
        chains: publicChains(),
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

// GET /api/roots/chains — the chains a root may be anchored on: id, name,
// RPCs, explorer, native currency. What a wallet is asked to add, read from
// the registry the verifier uses, so the two cannot name different chains.
router.get('/chains', (req, res) => {
  res.set('Cache-Control', 'public, max-age=300');
  res.json({ default: DEFAULT_CHAIN, chains: publicChains() });
});

// GET /api/roots/anchor-plan/:day?chain= — the exact zero-value transaction
// that anchors a completed day's root on that chain. DRY RUN: the server
// never signs.
router.get('/anchor-plan/:day', async (req, res) => {
  try {
    const chain = chainOf(req);
    if (!chain) return res.status(400).json({ error: 'Unknown chain' });
    const row = await rootForDay(String(req.params.day || ''));
    if (!row) return res.status(404).json({ error: 'No root for that day (still open, empty, or malformed)' });
    const have = (await anchorsFor(row.day, row)).find((a) => a.chain === chain.key);
    if (have) {
      return res.json({ already_anchored: true, day: row.day, root: row.root, chain: chain.key, anchor_tx: have.tx });
    }
    res.json(buildAnchorPlan(row.day, row.root, chain.key));
  } catch (err) {
    console.error('Anchor plan error:', err.stack || err.message);
    res.status(500).json({ error: 'Anchor plan unavailable' });
  }
});

// GET /api/roots/verify/:day?chain= — re-verify a recorded anchor against the
// chain, LIVE. Public and cached: this is the self-audit surface — anyone can
// ask the server to re-check its own claim, and the server reports exactly
// what the chain said, unknown included.
const _verifyCache = new Map();          // `${day}|${chain}` -> { at, body }
router.get('/verify/:day', async (req, res) => {
  try {
    const chain = chainOf(req);
    if (!chain) return res.status(400).json({ error: 'Unknown chain' });
    const day = String(req.params.day || '');
    const row = await rootForDay(day);
    if (!row) return res.status(404).json({ error: 'No root for that day' });
    const have = (await anchorsFor(row.day, row)).find((a) => a.chain === chain.key);
    if (!have) {
      return res.json({ day: row.day, root: row.root, chain: chain.key, chain_name: chain.name, status: 'unanchored' });
    }
    const key = `${day}|${chain.key}`;
    const hit = _verifyCache.get(key);
    if (hit && Date.now() - hit.at < 5 * 60000) {
      res.set('Cache-Control', 'public, max-age=60');
      return res.json(hit.body);
    }
    const v = await verifyAnchor(have.tx, row.day, row.root, undefined, chain.key);
    const body = { day: row.day, root: row.root, chain: chain.key, chain_name: chain.name,
      anchor_tx: have.tx, explorer: chain.explorer_tx + have.tx,
      status: v.status, block_time: v.block_time || null,
      from: v.from || null, reason: v.reason || null,
      // On a chain with a reorg depth: whether its `finalized` block has
      // passed the anchor (null when that could not be read).
      ...(v.final !== undefined ? { final: v.final } : {}) };
    // Cache verified results; an unknown answer is retried on the next ask.
    if (v.status === 'verified') _verifyCache.set(key, { at: Date.now(), body });
    res.set('Cache-Control', 'public, max-age=60');
    res.json(body);
  } catch (err) {
    console.error('Anchor verify error:', err.stack || err.message);
    res.status(500).json({ error: 'Anchor verify unavailable' });
  }
});

// POST /api/roots/anchor { day, tx_hash, chain } — record an anchor AFTER
// verifying it against the chain. The chain is the gatekeeper: a hash whose
// calldata does not equal the tagged root payload is refused, whoever
// submits it. On an unreadable chain the answer is "try again", never a
// guess. First anchor per chain wins — immutable like the root itself.
router.post('/anchor', authMiddleware, rateLimit({ name: 'roots.anchor', windowMs: 60000, max: 6, key: userKey }), async (req, res) => {
  try {
    const chain = chainOf(req);
    if (!chain) return res.status(400).json({ error: 'Unknown chain' });
    const day = String((req.body || {}).day || '');
    const txHash = String((req.body || {}).tx_hash || '').toLowerCase();
    const row = await rootForDay(day);
    if (!row) return res.status(404).json({ error: 'No root for that day' });
    const have = (await anchorsFor(row.day, row)).find((a) => a.chain === chain.key);
    if (have) {
      return res.status(409).json({ error: 'Already anchored', chain: chain.key, anchor_tx: have.tx });
    }
    const v = await verifyAnchor(txHash, row.day, row.root, undefined, chain.key);
    if (v.status === 'unknown') {
      return res.status(503).json({ error: `Could not read ${chain.name} to verify — try again shortly`, reason: v.reason });
    }
    if (v.status !== 'verified') {
      return res.status(400).json({ error: 'That transaction does not anchor this root', reason: v.reason });
    }
    const recorded = await recordAnchor(day, chain.key, txHash, v);
    if (!recorded) {
      // Somebody's anchor landed between the check above and this write.
      const now = (await anchorsFor(row.day, row)).find((a) => a.chain === chain.key);
      return res.status(409).json({ error: 'Already anchored', chain: chain.key, anchor_tx: now ? now.tx : null });
    }
    cache = { at: 0, body: null };            // feed shows the anchor next read
    res.json({ ok: true, day, chain: chain.key, chain_name: chain.name, anchor_tx: txHash,
      explorer: chain.explorer_tx + txHash, block_time: v.block_time, from: v.from });
  } catch (err) {
    console.error('Anchor submit error:', err.stack || err.message);
    res.status(500).json({ error: 'Anchor submit failed' });
  }
});

module.exports = router;
