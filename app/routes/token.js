'use strict';
/**
 * GET /api/token — the $RCLAW record the /token page paints.
 *
 * Public by construction: a mint address, a token program, a supply, two
 * revoked authorities and a presale block in which nothing is announced.
 * No account is in scope, nothing is priced, and the one file it reads is
 * in the repository. An unreadable record is a 503 that names the exception
 * class — never a 200 with an empty token, which a page would paint as a
 * token with no address.
 */

const express = require('express');
const rclaw = require('../lib/rclaw_token');

const router = express.Router();

router.get('/', (req, res) => {
  let record;
  try {
    record = rclaw.readRecord();
  } catch (err) {
    // The class, never the message: a path or a parser detail is not a
    // sentence for the page.
    const name = err && err.name ? String(err.name) : 'Error';
    console.error('Token record error:', name);
    return res.status(503).json({ error: 'token_record_unreadable', exception: name });
  }
  res.setHeader('Cache-Control', 'no-cache');
  res.json({ token: rclaw.publicRecord(record) });
});

/**
 * GET /api/token/pool — the $RCLAW DEX pool as CoinGecko reports it
 * (lib/token_pool.js). Public: market facts about a public mint. Three
 * answers: a pool (200, listed true), none listed (200, listed false, a fact
 * CoinGecko reported), could not be read (503, listed null, with the reason)
 * — the page paints each with its own sentence.
 */
router.get('/pool', async (req, res) => {
  try {
    const { tokenPool } = require('../lib/token_pool');
    const out = await tokenPool();
    res.setHeader('Cache-Control', 'no-cache');
    if (out.listed === null) return res.status(503).json({ error: 'pool_unreadable', ...out });
    res.json(out);
  } catch (err) {
    const name = err && err.name ? String(err.name) : 'Error';
    console.error('Token pool error:', name);
    res.status(503).json({ error: 'pool_unreadable', listed: null, reason: 'unreachable', detail: name });
  }
});

module.exports = router;
