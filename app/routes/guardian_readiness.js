/**
 * GET /api/guardian/readiness — the Guardian Readiness Score.
 *
 * A single read-only "is my agent safely constrained right now?" number,
 * composed from six signals RUNECLAW already produces: the authority envelope,
 * flight-recorder integrity, drawdown headroom, position concentration,
 * counterparty spread, and the live-exposure gate. Each signal is gathered
 * FAIL-SOFT — any that is unavailable arrives as null and is scored "not yet
 * observed" (never silently 100). Percentages / 0–100 sub-scores only; no dollar
 * amounts are emitted (§4). The reply always carries verdict:'heuristic'.
 */

'use strict';

const express = require('express');
const { authMiddleware } = require('../auth');
const { rateLimit, userKey } = require('../lib/rate_limit');
const { resolveBotIdentity } = require('../lib/identity');
const { getGateway, isConfigured } = require('../lib/gateway');
const { withDeadline } = require('../lib/deadline');
// Each axis is already documented as nullable — 'nulls for unobserved axes'.
// So a slow dependency should cost that ONE axis, not the whole score: the
// browser aborts this request at its own budget and reports a failure for a
// score the server was still assembling. Per-axis deadlines keep the answer
// inside that budget and simply leave the late axis null.
const AXIS_MS = 2000;
const { pool } = require('../db');
const { scoreReadiness } = require('../lib/guardian_readiness');
const { deriveStartEquity } = require('../lib/equity_basis');

const router = express.Router();
router.use(authMiddleware);
router.use(rateLimit({ windowMs: 60000, max: 20, key: userKey }));

router.get('/', async (req, res) => {
  const uid = req.user.user_id;
  let ident;
  try { ident = await resolveBotIdentity(req); }
  catch (e) { ident = { id: `web:${uid}` }; }

  // Each block is independent and fail-soft: a hiccup leaves that axis null,
  // which the pure scorer reports as "not yet observed" rather than a pass.

  // 1. Authority envelope (bot-side, the same status the authority panel shows).
  let envelope = null;
  try {
    if (isConfigured()) {
      const r = await withDeadline(getGateway(
        `/authority/status?telegram_id=${encodeURIComponent(ident.id)}`, AXIS_MS), AXIS_MS);
      if (r && r.status === 200 && r.data) {
        envelope = { mode: r.data.mode || 'off', bound: !!r.data.bound };
      }
    }
  } catch (_) { /* axis stays null */ }

  // 2. Flight-recorder chain integrity.
  let recorderOk = null;
  try {
    const { getLatestFlight } = require('./sync');
    const flight = await getLatestFlight();
    // `chain.ok` IS THREE-VALUED AT THE SOURCE, and `!== false` collapsed it
    // onto the pass. engine.guardian_status() sets `chain: {ok: None}` and only
    // promotes it to a bool when `audit_chain.verify()` actually ran — so an
    // unverifiable chain (verify raised, or the console never reached it)
    // arrived as null, `null !== false` is true, and this axis scored **100,
    // "Decision→outcome chain is intact"** on provenance nobody had checked.
    // The Telegram card has told these three apart for a while (✅ verified /
    // ⚠️ UNVERIFIED / · unchecked); this is the same read, and it had two.
    const ok = flight && flight.chain ? flight.chain.ok : undefined;
    if (ok === true || ok === false) recorderOk = ok;
  } catch (_) { /* null */ }

  // 3. Drawdown headroom (max drawdown % of peak, from the user's closed trades).
  let drawdownPct = null;
  let drawdownBasis = null;
  try {
    const [rows] = await pool.execute(
      `SELECT symbol, direction, pnl, size_usd, closed_at
         FROM trades WHERE user_id = ? AND status = 'CLOSED'
         ORDER BY closed_at DESC LIMIT 2000`, [uid]);
    if (rows.length) {
      const { computePerformance } = require('../lib/trade_performance');
      const [snap] = await pool.execute(
        'SELECT equity FROM equity_snapshots WHERE user_id = ? ORDER BY snapshot_at DESC LIMIT 1',
        [uid]);
      // The basis was summed over ALL rows while computePerformance scores
      // only the priced ones — see deriveStartEquity. The drawdown this
      // produces feeds a READINESS SCORE, so the coverage travels with it:
      // a score resting on an estimated basis is not the same claim as one
      // resting on a measured one, and the reader gets to see which.
      const basis = deriveStartEquity(rows, snap.length ? snap[0].equity : null);
      drawdownBasis = basis;
      const perf = computePerformance(rows, { startEquity: basis.start_equity });
      if (perf && perf.drawdown && isFinite(perf.drawdown.max_pct)) {
        drawdownPct = perf.drawdown.max_pct;
      }
    }
  } catch (_) { /* null */ }

  // 4. Position concentration (top holding share of gross; needs >=2 holdings).
  let concentrationPct = null;
  try {
    const { buildExposure } = require('../lib/exposure');
    const exp = await withDeadline(buildExposure(uid), AXIS_MS);
    if (exp && Array.isArray(exp.assets) && exp.assets.length >= 2
        && exp.gross_total_usd > 0) {
      concentrationPct = exp.assets[0].gross_usd / exp.gross_total_usd;
    }
  } catch (_) { /* null */ }

  // 5. Counterparty spread (custodial/self-custody + issuer concentration tier).
  let counterpartyTier = null;
  try {
    const { buildHoldings } = require('../lib/holdings');
    const { computeCounterparty } = require('../lib/counterparty');
    const holdings = await withDeadline(buildHoldings(ident, uid), AXIS_MS);
    const cp = computeCounterparty(holdings);
    if (cp && !cp.unrated && cp.concentration) counterpartyTier = cp.concentration;
  } catch (_) { /* null */ }

  // 6. Live-exposure gate (paper vs operator-gated live vs de-risked).
  let liveState = null;
  try {
    const [cr] = await pool.execute(
      'SELECT live_enabled, paused, allowlisted FROM user_controls WHERE user_id = ?',
      [uid]);
    if (cr.length) {
      const c = cr[0];
      liveState = {
        live_enabled: !!c.live_enabled,
        allowlisted: !!c.allowlisted,
        paused: !!c.paused,
        source: 'controls',
      };
    } else {
      // THE ABSENT ROW WAS SCORED 100, "Paper only — no live capital exposed."
      //
      // `user_controls` is a MIRROR, and routes/webtrade.js says what of:
      // "written only for web-originated control changes, so it is empty for a
      // user who enabled live in Telegram AND for every web-only live user".
      // So the rows this branch fires on are, precisely, the accounts most
      // likely to be live — and it handed them full marks on the
      // highest-consequence axis this score has, off a row that does not exist.
      // Absent is never a measurement.
      //
      // Ask the same authority the confirm path asks (`_trade_mode`, via
      // /trade/live_mode). An answer is a real reading in either direction; no
      // answer leaves the axis null, which scoreReadiness renormalises around
      // and reports as "not yet observed".
      if (isConfigured()) {
        const lm = await withDeadline(getGateway(
          `/trade/live_mode?telegram_id=${encodeURIComponent(ident.id)}`, AXIS_MS), AXIS_MS);
        if (lm && lm.status === 200 && lm.data && typeof lm.data.live_allowed === 'boolean') {
          liveState = {
            // The bot's gate is the operator gate: it says live_allowed only
            // once the allowlist (or the fail-closed web-live gate) has let
            // this identity through, so `allowlisted` tracks it rather than
            // being asserted separately.
            live_enabled: lm.data.live_allowed,
            allowlisted: lm.data.live_allowed,
            paused: false,
            source: 'gateway',
          };
        }
      }
    }
  } catch (_) { /* null */ }

  try {
    const out = scoreReadiness({
      envelope, recorderOk, drawdownPct, concentrationPct, counterpartyTier, liveState,
    });
    out.drawdown_basis = drawdownBasis;
    out.generated_at = new Date().toISOString();
    return res.json(out);
  } catch (err) {
    console.error('Guardian readiness compose error:', err.stack || err.message);
    return res.status(502).json({ error: 'Readiness score unavailable' });
  }
});

module.exports = router;
