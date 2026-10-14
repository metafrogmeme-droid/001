/**
 * GET /api/tier — the signed-in member's tier and what each tier includes,
 * as the bot reads them (bot gateway /gateway/tier, the same two readings the
 * Telegram /tier card renders).
 *
 * The bot is the one tier authority. The plan card used to describe the tiers
 * from a table written into dashboard.js, which promised things no tier
 * grants ("Higher live caps", "Live trading eligibility", "Early access");
 * it renders this answer instead. Identity is resolved server-side exactly as
 * chat's is (lib/identity.js): the linked telegram_id, or "web:<user_id>".
 *
 * A failed read is a failed read: 503 or 502, and the card says it could not
 * read the tier. Never a default plan in its place.
 */

const express = require('express');
const { authMiddleware } = require('../auth');
const { resolveBotIdentity } = require('../lib/identity');
const gateway = require('../lib/gateway');

const router = express.Router();
router.use(authMiddleware);

router.get('/', async (req, res) => {
  // Per member, so never stored by a shared cache.
  res.set('Cache-Control', 'private, no-store');
  if (!gateway.isConfigured()) {
    return res.status(503).json({ error: 'Bot gateway not configured' });
  }
  try {
    const ident = await resolveBotIdentity(req);
    const r = await gateway.getGateway(`/tier?telegram_id=${encodeURIComponent(ident.id)}`);
    return gateway.relay(res, r);
  } catch (e) {
    return res.status(502).json({ error: 'Bot gateway unavailable' });
  }
});

module.exports = router;
