/**
 * Outcome-Based Agent Reputation — web surface.
 *
 * Serves a verifiable, confidence-adjusted reputation score computed only from
 * the user's OWN realized closed trades. Self-contained on the DB layer.
 * ADVISORY ONLY — a heuristic score, never a verdict (§4). Dollar-free (all
 * ratios), so it reads honestly without inventing a starting balance.
 */

const express = require('express');
const { pool } = require('../db');
const { authMiddleware } = require('../auth');
const { rateLimit, userKey } = require('../lib/rate_limit');
const { deriveStartEquity } = require('../lib/equity_basis');
const { computeReputation } = require('../lib/reputation');

const router = express.Router();
router.use(authMiddleware);
router.use(rateLimit({ windowMs: 60000, max: 30, key: userKey }));

// GET /api/reputation — the user's outcome-based reputation readout.
router.get('/', async (req, res) => {
  try {
    const uid = req.user.user_id;
    const [rows] = await pool.execute(
      `SELECT symbol, direction, pnl, size_usd, fees, opened_at, closed_at
         FROM trades WHERE user_id = ? AND status = 'CLOSED' AND closed_at IS NOT NULL
         ORDER BY closed_at ASC`,
      [uid]
    );
    // Seed the drawdown-% denominator from the latest equity snapshot when we
    // have one (matches /api/trades/breakdown), else a neutral default.
    const [snap] = await pool.execute(
      'SELECT equity FROM equity_snapshots WHERE user_id = ? ORDER BY snapshot_at DESC LIMIT 1',
      [uid]
    );
    // The basis and the metrics must agree about which trades exist:
    // computeReputation filters to rows with a readable pnl, so summing the
    // unreadable ones as zero here left every percentage biased by exactly
    // the pnl nobody could read. Unfixable, so declared — see deriveStartEquity.
    const { start_equity, ...coverage } = deriveStartEquity(
      rows, snap.length > 0 ? snap[0].equity : null);
    // `start_equity` is deliberately NOT forwarded. reputation.js opens by
    // saying it is "leverage-agnostic and dollar-free (every metric is a
    // ratio) ... stays shareable without exposing amounts", and this route is
    // where a dollar figure would get bolted onto it. The COVERAGE is the part
    // a reader needs — how many trades the basis could and could not see — and
    // it is a count, not an amount.
    res.json({ ...computeReputation(rows, { startEquity: start_equity }), coverage });
  } catch (err) {
    console.error('Reputation error:', err.stack || err.message);
    res.status(500).json({ error: 'Failed to compute reputation' });
  }
});

module.exports = router;
