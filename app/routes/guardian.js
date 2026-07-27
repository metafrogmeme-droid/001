/**
 * RUNECLAW Guardian — read-only API.
 *
 * The safety, control, evidence, and recovery layer for autonomous crypto
 * capital. The first module exposed here is the Agent Flight Recorder: a
 * provenance-complete, tamper-evident record of every trading decision.
 *
 * The authoritative ledger is a SHA-256 hash-chained, Ed25519-attested
 * append-only log held bot-side (logs/audit_chain.jsonl). The engine runs the
 * cryptographic verify() (it holds the file and the exact canonical hashing) and
 * pushes recent joined DECISION↔OUTCOME records plus the verification result via
 * /api/bot/sync/flight. This route is a read-only mirror of that.
 *
 * DOLLAR FIGURES ARE AUTHENTICATED; THE RECORD IS NOT.
 *
 * public_flight.js says, in its own header: "The authed /api/guardian/flight
 * keeps the full (dollar-carrying) record." That sentence described a boundary
 * that did not exist. This router never applied authMiddleware — its two
 * siblings, guardian_readiness.js and guardian_review.js, both do — so an
 * anonymous GET returned records straight from `outcome_event_payload`,
 * including `pnl_usd`, `entry_price` and `exit_price`, plus any `*_usd` field
 * inside `idea`/`risk`. sanitizeRecord() existed, worked, and was applied only
 * on the public route; the endpoint documented as the authed one was the
 * unauthenticated way around it.
 *
 * The endpoint stays reachable anonymously, because it must: dashboard.js
 * fetches both /flight and /incidents with `auth: false`. What changes is WHAT
 * an anonymous caller gets — the same §4-safe, percent/ratio-only view the
 * public route serves. A logged-in caller still gets the dollars, which is what
 * public_flight.js said all along.
 */

const express = require('express');
const { optionalAuth } = require('../auth');
const { getLatestFlight } = require('./sync');
const { inspectWindow, sanitizeRecord } = require('../lib/flight');

const router = express.Router();
// Identifies, never refuses — see optionalAuth in auth.js.
router.use(optionalAuth);

/**
 * Redact for anonymous callers. One helper, used by every handler in this file,
 * so a new endpoint here cannot forget it in the way this whole router did.
 */
const publicSafe = (req, value) => (req.user ? value : sanitizeRecord(value));

/**
 * GET /api/guardian/flight?limit=50
 * Returns the recent flight records + the engine-verified chain status.
 */
router.get('/flight', async (req, res) => {
  try {
    const flight = await getLatestFlight();
    if (!flight || !Array.isArray(flight.records)) {
      return res.json({
        records: [], chain: null, guardian_status: (flight && flight.guardian_status) || null,
        window: null, updated_at: (flight && flight.updated_at) || null,
        note: 'No decisions recorded yet.',
      });
    }
    let limit = parseInt(req.query.limit, 10);
    if (!Number.isFinite(limit) || limit < 1) limit = 50;
    limit = Math.min(limit, 200);
    const records = flight.records.slice(0, limit);
    res.json({
      records: records.map((r) => publicSafe(req, r)),
      chain: flight.chain || {},
      policy: publicSafe(req, flight.policy || null),
      guardian_status: flight.guardian_status || null,
      // The window check runs over the UNREDACTED records: it verifies hash
      // chaining and sequence, which the scrubber does not touch, and deriving
      // it from the redacted copy would only make it weaker.
      window: inspectWindow(records),
      updated_at: flight.updated_at || null,
      disclosure: req.user ? undefined
        : 'Anonymous view — percent/ratio only, no dollar amounts. Sign in for the full record.',
    });
  } catch (err) {
    console.error('Guardian flight error:', err.stack || err.message);
    res.status(500).json({ error: 'Failed to read flight records' });
  }
});

/**
 * GET /api/guardian/flight/:decisionId
 * Returns one full decision record (with its outcome) by decision id.
 */
router.get('/flight/:decisionId', async (req, res) => {
  try {
    const flight = await getLatestFlight();
    const records = (flight && Array.isArray(flight.records)) ? flight.records : [];
    const rec = records.find((r) => r && r.decision_id === req.params.decisionId);
    if (!rec) return res.status(404).json({ error: 'Decision not found in recent window' });
    res.json({
      record: publicSafe(req, rec),
      chain: (flight && flight.chain) || {},
      disclosure: req.user ? undefined
        : 'Anonymous view — percent/ratio only, no dollar amounts. Sign in for the full record.',
    });
  } catch (err) {
    console.error('Guardian flight-by-id error:', err.stack || err.message);
    res.status(500).json({ error: 'Failed to read decision' });
  }
});

/**
 * GET /api/guardian/incidents?limit=40
 * The safety-engine incident ledger — blocks (firewall / risk-gate rejection /
 * auth-denied / self-critique halt / intent-policy deny) and recoveries (escape
 * plans), plus twin/sentinel flags — mirrored read-only from the sealed chain.
 * Falls back to deriving blocks from REJECTED flight records for older bots that
 * don't yet send an `incidents` array. Percent/flags only, no dollar amounts.
 */
router.get('/incidents', async (req, res) => {
  try {
    const flight = await getLatestFlight();
    let limit = parseInt(req.query.limit, 10);
    if (!Number.isFinite(limit) || limit < 1) limit = 40;
    limit = Math.min(limit, 100);

    let incidents = (flight && Array.isArray(flight.incidents)) ? flight.incidents : null;
    let derived = false;
    if (!incidents) {
      // Fallback: surface rejected decisions as block incidents.
      const recs = (flight && Array.isArray(flight.records)) ? flight.records : [];
      incidents = recs
        .filter((r) => r && String(r.outcome || '').startsWith('REJECTED'))
        .map((r) => ({
          id: (r.chain && r.chain.entry_hash) || r.decision_id || '',
          ts: r.timestamp || '',
          kind: 'block', category: 'Risk-gate rejection', severity: 'high',
          symbol: r.symbol || '',
          detail: (r.risk && (r.risk.reason
            || (Array.isArray(r.risk.checks_failed) && r.risk.checks_failed[0]))) || 'rejected',
          chain: r.chain || null,
        }));
      derived = true;
    }
    incidents = incidents.slice(0, limit);
    const tally = { block: 0, recovery: 0, flag: 0 };
    for (const i of incidents) { if (i && tally[i.kind] !== undefined) tally[i.kind]++; }

    res.json({
      read_only: true,
      // The header of this endpoint already promised "Percent/flags only, no
      // dollar amounts" and nothing enforced it. `flight.incidents` arrives
      // unscrubbed from the bot, and the derived fallback copies `risk.reason`
      // and `checks_failed[0]` verbatim — risk reasons routinely name a dollar
      // cap. The promise is now kept by the scrubber rather than by intent.
      incidents: incidents.map((i) => publicSafe(req, i)),
      counts: tally,
      derived,                          // true = fallback (rejections only)
      guardian_status: (flight && flight.guardian_status) || null,
      updated_at: (flight && flight.updated_at) || null,
      note: incidents.length ? undefined
        : 'No safety incidents recorded — the controls have had nothing to stop or recover.',
    });
  } catch (err) {
    console.error('Guardian incidents error:', err.stack || err.message);
    res.status(500).json({ error: 'Failed to read incidents' });
  }
});

module.exports = router;
