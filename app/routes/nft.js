'use strict';
/**
 * NFT surface. Read-only OpenSea data (public radar + wallet mirror by
 * address — no marketplace machinery), plus the Rune of Entry:
 *  - GET /stats      (public)  deployed? how many minted (a count only)
 *  - GET /mint-plan  (authed)  EIP-712 voucher + calldata for the linked
 *                              wallet. The server never signs or sends a
 *                              transaction — see lib/nft.js.
 */

const express = require('express');
const { rateLimit, ipKey } = require('../lib/rate_limit');
const { authMiddleware } = require('../auth');
const { pool } = require('../db');
const opensea = require('../lib/opensea');
const nft = require('../lib/nft');

const router = express.Router();
router.use(rateLimit({ windowMs: 60_000, max: 30, key: ipKey, message: 'rate_limited' }));

let statsCache = null;
router.get('/stats', async (req, res) => {
  try {
    if (statsCache && Date.now() - statsCache.at < 60000) return res.json(statsCache.body);
    const body = await nft.readStats();
    // An unreadable count is never cached — the next caller gets a fresh try.
    if (!body.deployed || body.minted_count != null) statsCache = { at: Date.now(), body };
    res.json(body);
  } catch (err) {
    console.error('NFT stats error:', err.stack || err.message);
    res.status(500).json({ error: 'Stats unavailable' });
  }
});

router.get('/mint-plan', authMiddleware, async (req, res) => {
  try {
    const [rows] = await pool.execute(
      'SELECT wallet_address FROM users WHERE id = ?', [req.user.user_id]);
    const addr = rows && rows[0] && rows[0].wallet_address;
    res.json(await nft.buildMintPlan(addr));
  } catch (err) {
    console.error('NFT mint-plan error:', err.stack || err.message);
    res.status(500).json({ error: 'Mint plan unavailable' });
  }
});

router.get('/radar', async (req, res) => {
  res.json(await opensea.getNftRadar());
});

router.get('/wallet/:address', async (req, res) => {
  const chain = /^[a-z0-9_-]{1,20}$/.test(String(req.query.chain || ''))
    ? String(req.query.chain) : 'ethereum';
  res.json(await opensea.getWalletNfts(req.params.address, chain));
});

module.exports = router;
