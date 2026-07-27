'use strict';
/**
 * Per-user BOT strategy — the web mirror of Telegram's /mystrategy, proxied
 * to the bot gateway with the caller's identity resolved SERVER-side
 * (lib/identity: linked telegram_id, or "web:<user_id>"). JWT-authed.
 *
 * The selection is a TIGHTEN-ONLY veto on trades the user confirms: the
 * bot-side gate can refuse an idea that breaks the chosen preset's rules,
 * it never places trades, and it never touches the operator's global
 * stance. This route stores and reads a preference — it moves nothing.
 */

const express = require('express');
const { authMiddleware } = require('../auth');
const { rateLimit, userKey } = require('../lib/rate_limit');
const gateway = require('../lib/gateway');
const { resolveBotIdentity } = require('../lib/identity');

const router = express.Router();
router.use(authMiddleware);
router.use(rateLimit({ windowMs: 60000, max: 30, key: userKey }));

router.get('/', async (req, res) => {
  try {
    if (!gateway.isConfigured()) return res.status(503).json({ error: 'Not configured' });
    const ident = await resolveBotIdentity(req);
    const r = await gateway.getGateway(
      `/user/strategy?telegram_id=${encodeURIComponent(ident.id)}`, 10000);
    return gateway.relay(res, r);
  } catch (err) {
    console.error('Bot strategy read error:', err.stack || err.message);
    res.status(502).json({ error: 'Bot strategy unavailable' });
  }
});

router.put('/', async (req, res) => {
  try {
    if (!gateway.isConfigured()) return res.status(503).json({ error: 'Not configured' });
    const strategy = String((req.body || {}).strategy || '').slice(0, 64);
    const ident = await resolveBotIdentity(req);
    const r = await gateway.postGateway('/user/strategy', {
      telegram_id: ident.id, strategy });
    return gateway.relay(res, r);
  } catch (err) {
    console.error('Bot strategy set error:', err.stack || err.message);
    res.status(502).json({ error: 'Bot strategy unavailable' });
  }
});

router.delete('/', async (req, res) => {
  try {
    if (!gateway.isConfigured()) return res.status(503).json({ error: 'Not configured' });
    const ident = await resolveBotIdentity(req);
    // Revocable is the whole point — clearing always routes, never caches.
    const r = await gateway.postGateway('/user/strategy', {
      telegram_id: ident.id, strategy: 'off' });
    return gateway.relay(res, r);
  } catch (err) {
    console.error('Bot strategy clear error:', err.stack || err.message);
    res.status(502).json({ error: 'Bot strategy unavailable' });
  }
});

module.exports = router;
