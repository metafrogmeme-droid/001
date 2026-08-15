/**
 * Bot intelligence reports (read side) — web↔Telegram parity.
 *
 * GET /api/reports        — public sections: cross-venue funding scan, the
 *                           funding-arb PAPER tracker, and the live↔backtest
 *                           parity headline (already public on /track).
 * GET /api/reports/yield  — the yield radar. OPERATOR-SENSITIVE (contains
 *                           real account idle balances), so it requires a
 *                           logged-in user whose plan is 'admin' — and plan
 *                           is re-read fresh from the DB, not trusted from
 *                           the JWT (tiers can change after token issue).
 *
 * Data is pushed hourly by the bot (POST /api/bot/sync/reports); this router
 * never invents numbers — a missing payload/section renders as empty state.
 */

const express = require('express');
const { pool } = require('../db');
const { authMiddleware } = require('../auth');
const { getLatestReports, readReports } = require('./sync');

const router = express.Router();

router.get('/', async (req, res) => {
  try {
    // readReports, not getLatestReports: the swallowing variant cannot
    // distinguish "none pushed yet" from "could not read", and this route
    // renders the first as a real empty state.
    const r = await readReports();
    if (!r) return res.json({ reports: null });
    res.json({
      reports: {
        generated_at: r.generated_at || null,
        received_at: r.received_at || null,
        funding: r.funding || null,
        arb: r.arb || null,
        parity: r.parity || null,
        has_yield: !!r.yield, // presence flag only — content stays admin-gated
      },
    });
  } catch (err) {
    console.error('Reports read error:', err.stack || err.message);
    // M13's defect in a second file: `{reports: null}` here is byte-identical
    // to the genuine "no reports pushed yet" branch above, so a failed read
    // rendered as an hourly scan that found nothing. getReports() in
    // dashboard.js already mustRead()s this and its comment says "an
    // unreachable report feed is not an empty one" — a 200 was the one answer
    // that could defeat it.
    res.status(503).json({ error: 'reports_unavailable' });
  }
});

router.get('/yield', authMiddleware, async (req, res) => {
  try {
    const [u] = await pool.execute(
      'SELECT plan FROM users WHERE id = ?', [req.user.user_id]);
    if (!u[0] || String(u[0].plan) !== 'admin') {
      return res.status(403).json({ error: 'admin_required' });
    }
    const r = await getLatestReports();
    res.json({
      yield: (r && r.yield) || null,
      generated_at: (r && r.generated_at) || null,
    });
  } catch (err) {
    console.error('Yield report read error:', err.stack || err.message);
    res.status(500).json({ error: 'Failed to read yield report' });
  }
});

module.exports = router;
