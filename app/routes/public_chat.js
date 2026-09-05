/**
 * Public web chat — the RUNECLAW assistant for ANONYMOUS visitors (no auth).
 *
 * This is the read-only, account-free counterpart to routes/chat.js. It proxies
 * to the bot gateway's POST /gateway/chat/public, which runs ONLY the LLM chat
 * with a static market-only system prompt: no user is provisioned, no trade is
 * ever proposed, and no account/portfolio/order skill is dispatched. So an
 * anonymous caller can never reach account data or place a trade through here,
 * regardless of what they send — the boundary is enforced server-side on the bot.
 *
 * Abuse controls: per-IP rate limit (unauthenticated traffic), a text-length
 * cap, and the bot's own daily LLM-budget guard inside _llm_chat.
 */

const express = require('express');
const { rateLimit, ipKey } = require('../lib/rate_limit');
const gateway = require('../lib/gateway');

const router = express.Router();

// Anonymous → bucket by client IP. Tighter than the authed chat limit (15/min):
// there is no account behind these requests, so keep the ceiling low.
const publicChatLimit = rateLimit({
  windowMs: 60000,
  max: 6,
  key: ipKey,
  message: 'Too many messages — give it a few seconds.',
});

const MAX_TEXT_LEN = 2000;
// LLM replies can take a while — give chat a longer budget than the default.
const CHAT_TIMEOUT_MS = 45000;

// The same turn on either wire shape — see routes/chat.js for why the two
// share one function rather than two copies of the validation.
async function publicTurn(req, res, { stream = false } = {}) {
  try {
    if (!gateway.isConfigured()) {
      return res.status(503).json({ error: 'Chat not configured' });
    }
    const text = typeof (req.body || {}).text === 'string' ? req.body.text.trim() : '';
    if (!text) return res.status(400).json({ error: 'text required' });
    if (text.length > MAX_TEXT_LEN) return res.status(400).json({ error: 'Message too long' });
    // An anonymous visitor has no stored profile, so the bot can only answer in
    // their language if the site sends the current UI locale. Forward it (short,
    // charset-restricted — the gateway caps to 12 chars too). Still no identity.
    const rawLang = typeof (req.body || {}).lang === 'string' ? req.body.lang.trim() : '';
    const lang = /^[a-zA-Z-]{2,12}$/.test(rawLang) ? rawLang : '';
    const payload = lang ? { text, lang } : { text };
    if (stream) return gateway.postGatewayStream('/chat/public/stream', payload, res, CHAT_TIMEOUT_MS + 15000);
    const r = await gateway.postGateway('/chat/public', payload, CHAT_TIMEOUT_MS);
    return gateway.relay(res, r);
  } catch (err) {
    console.error('Public chat proxy error:', err.stack || err.message);
    if (res.headersSent) { try { res.end(); } catch (e) { /* gone */ } return; }
    return res.status(502).json({ error: 'Chat unavailable' });
  }
}

// POST /api/public/chat  body: { text }
router.post('/', publicChatLimit, (req, res) => publicTurn(req, res, { stream: false }));
// POST /api/public/chat/stream — the same turn as text/event-stream.
router.post('/stream', publicChatLimit, (req, res) => publicTurn(req, res, { stream: true }));

module.exports = router;
