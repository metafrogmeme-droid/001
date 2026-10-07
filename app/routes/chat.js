/**
 * Web chat — the RUNECLAW chatbot on the website (JWT-authed, ALL users).
 *
 * Proxies to the bot process's user gateway (POST /gateway/chat), which runs
 * the SAME pipeline as Telegram free-text: intent router -> skill dispatch ->
 * LLM chat fallback, with shared conversation memory and per-role LLM tiers.
 *
 * Identity: resolved server-side (lib/identity.js) — the linked telegram_id,
 * or "web:<user_id>" for web-only accounts (paper-only, auto-provisioned by
 * the bot). The browser can never chat as someone else.
 */

const express = require('express');
const { authMiddleware } = require('../auth');
const { rateLimit, userKey } = require('../lib/rate_limit');
const { resolveBotIdentity } = require('../lib/identity');
const gateway = require('../lib/gateway');
const { loadProfile } = require('./profile');

const router = express.Router();
router.use(authMiddleware);

const chatLimit = rateLimit({ windowMs: 60000, max: 15, key: userKey });

const MAX_TEXT_LEN = 2000;
// LLM replies can take a while — give chat a longer budget than the default.
const CHAT_TIMEOUT_MS = 45000;
// Recording a local answer into the bot's memory is fire-and-forget; it
// must never hold the reply, so it gets a short budget of its own.
const RECORD_TIMEOUT_MS = 5000;
// A streamed turn may read a tool between two model calls, so its absolute
// deadline sits above the plain chat budget. Still a deadline: a stream
// that trickles is the one shape an inactivity timeout can never end.
const STREAM_TIMEOUT_MS = 75000;

/**
 * The local text intercepts, IN ORDER. Each is `(userId, text, ident) ->
 * reply | null`, where `ident()` lazily resolves the bot identity for a
 * row that needs it. The first non-null reply is the answer. The table is
 * empty.
 *
 * Why a table and not a column of `if`s: the order IS the routing, and a
 * table can be read by a test, which fourteen consecutive early returns
 * could not. None of the fourteen had one. The table is empty: idle yield
 * was the last row, and it left for the shared idleyield door.
 *
 * THE THIRD COLUMN IS WHAT THE CAPABILITY CARD SAYS. `capability_answer`
 * takes an `extras` list for exactly this — "capabilities a surface knows
 * about and this module cannot see", in its own words — and nothing had ever
 * filled it: the intercept table is here, the card is Python, and the chat
 * payload carried telegram_id/name/text/profile/lang and nothing else. A
 * socket with no cable. So the card built to stop the bot OVERSTATING what it
 * can do was understating it by every row below, on the one surface those
 * rows exist for. The sentence lives beside the handler it describes, for the
 * reason `SKILL_SAYS` is a column on the permission table rather than a map
 * in a renderer.
 *
 * WHAT A HIT DOES NOW. Answer here, AND record the exchange into the bot's
 * shared conversation memory (POST /gateway/chat/record). These used to
 * answer and vanish: "what's my net worth?" was answered by the web, and
 * "and how does that compare to last week?" reached a model that had never
 * seen the first question. The store is what both surfaces read history
 * from, so an answer it never hears about is an answer the next turn cannot
 * build on.
 */
const INTERCEPTS = [
  // Price alerts left this table. Both doors route "tell me when…" to the
  // shared price_alert seam, which arms the same engine. The engine and
  // /api/alerts still run here, so an alert that is already armed is still
  // evaluated while the bot process is down.
  // The what-if replay left this table. Both doors route "replay every
  // signal" to the shared replay seam, which fetches this process's own
  // card. The portfolio panel and /api/replay still run here, so the replay
  // is still readable while the bot process is down.
  // The weekly letter left this table. Both doors route "this week's letter"
  // to the shared letter seam, which fetches this process's own card. The
  // press and /api/letter still run here, so the panel still has the letter
  // while the bot process is down.
  // The tokenized-asset radar left this table. Both doors route "rwa
  // radar" to the shared rwa seam, which fetches this process's own card.
  // The Markets panel and /api/market/rwa still run here, so the radar is
  // still readable while the bot process is down.
  // The airdrop radar left this table. Both doors route "airdrop radar"
  // to the shared airdrops seam, which fetches this process's own card
  // for the caller the turn names. The Markets panel and /api/airdrops still
  // run here, so the radar is still readable while the bot process is down.
  // The venue router left this table. Both doors route "best venue for
  // BTC" to the shared venue_router seam, which fetches this process's
  // own card. The asset the sentence names narrows it; an unnamed asset
  // is the top five. The Markets panel and /api/market/venue-router still
  // run here, so the read is still available while the bot process is down.
  // The meme radar left this table. Both doors route "meme radar" to the
  // shared meme_radar seam, which fetches this process's own card. The
  // feed is public DEXScreener data. The Markets panel and /api/market/meme
  // still run here, so the radar is still readable while the bot process
  // is down.
  // The NFT radar left this table. Both doors route "nft radar" to the
  // shared nft seam, which fetches this process's own card. OpenSea
  // collection stats are public. /api/nft/radar still runs here, so the
  // radar is still readable while the bot process is down.
  // The spot market left this table. Both doors route "spot market" to
  // the shared spot seam, which fetches this process's own card. Venue
  // tickers are public. Nothing here places a spot order. /api/spot/market
  // and /api/spot/basis still run here, so the read is still available
  // while the bot process is down.

  // The wallet mirror left this table. Both doors route "my wallet" to the
  // shared wallet seam, which fetches this process's own card for the caller
  // the turn names. The panel and /api/wallet/portfolio still run here, so
  // the mirror is still readable while the bot process is down.
  // DeFi positions left this table. Both doors route "my defi positions"
  // to the shared defi seam, which fetches this process's own card for
  // the caller the turn names. The read is that caller's linked wallet.
  // Nothing here repays, withdraws, or manages a position. /api/defi still
  // runs here, so the positions are still readable while the bot process
  // is down.
  // Cross-venue exposure left this table. Both doors route "my exposure"
  // to the shared exposure seam, which fetches this process's own card for
  // the caller the turn names. The read nets that caller's perp positions
  // against their on-chain spot. Nothing here resizes, hedges, or closes a
  // position. "what's my drawdown" stays the risk engine. /api/exposure
  // still runs here, so the read is still available while the bot process
  // is down.
  // The research dossier left this table. Both doors route "research
  // PENDLE" to the shared research seam, which fetches this process's own
  // card for the one symbol the sentence names. Nothing here places,
  // confirms, sizes, or closes a trade. "deep dive on SOL" stays the
  // chart; "research the docs" and "research report" stay the model.
  // /api/research/:symbol still runs here, so the dossier is still
  // readable while the bot process is down.
  // Net worth left this table. Both doors route "my net worth" to the
  // shared networth seam, which fetches this process's own card for the
  // caller the turn names. The read is that caller's exchange plus their
  // on-chain wallet; paper is labelled simulated and never added in.
  // Nothing here places, confirms, sizes, or closes. "what's my drawdown",
  // "am I over my exposure", "check my risk" and "what's my max exposure"
  // stay the risk engine. Dollars stay on this private card. /api/networth
  // still runs here, and half of it reads without the bot: with the bot
  // process down the on-chain wallet still reads and the exchange half,
  // which comes through the bot gateway, says it could not be read.
  // Idle yield left this table. Both doors route "my idle usdc" to the
  // shared idleyield seam, which fetches this process's own card for the
  // caller the turn names. The read is that caller's linked wallet, never
  // the operator's exchange book — Telegram's /idleyield stays that admin
  // scan. Nothing here places, confirms, sizes, or stakes. "stake my usdc"
  // stays the stake door. Dollars stay on this private card. /api/idleyield
  // still runs here, but its only optimizer is the bot gateway
  // (`postGateway('/idleyield')` in app/lib/idle_yield.js): with the bot
  // process down the panel says the scanner is unavailable, not a rate.
];

/**
 * What this client answers for itself, in words a person reads.
 *
 * Sent with every turn so the bot's capability card can name them. It is the
 * table's OWN third column, so a row added above is carried without anybody
 * editing here — and a row with no sentence is left out rather than named by
 * its handler's variable name, which is not English.
 */
function interceptSays() {
  return INTERCEPTS.map(([, , says]) => says).filter((s) => typeof s === 'string' && s.trim());
}

/**
 * Tell the bot's conversation memory about an answer the web gave itself.
 * Fire-and-forget: the reply has already been decided, and a memory write
 * that could delay or fail it would be a worse trade than a memory gap.
 * A gateway that is not configured simply has no memory to tell.
 */
function rememberIntercept(ident, text, reply, intent) {
  if (!gateway.isConfigured()) return;
  const html = reply && typeof reply.reply_html === 'string' ? reply.reply_html : '';
  if (!html) return;
  Promise.resolve()
    .then(() => ident())
    .then((who) => gateway.postGateway('/chat/record',
      { telegram_id: who.id, text, reply: html, intent }, RECORD_TIMEOUT_MS))
    .then((r) => {
      if (!(r && r.status >= 200 && r.status < 300)) {
        console.warn(`[chat] memory record for '${intent}' refused (${r && r.status})`);
      }
    })
    .catch((e) => console.warn('[chat] memory record failed:', e && e.message));
}

/**
 * One chat turn, on either wire shape.
 *
 * `stream: false` answers with JSON exactly as it always has. `stream: true`
 * answers as text/event-stream: `delta` frames as the model produces text,
 * `tool` frames while it reads something, and ONE `final` frame carrying the
 * same JSON the non-streaming route would have returned — so the browser
 * renders an intercept hit, a refusal and a model answer through the same
 * reader, and the fallback to the JSON route is a matter of which URL.
 * Everything before the gateway call (validation, images, intercepts) is
 * shared: a streaming route with its own copy of the intercept table is how
 * one of the two copies stops being consulted.
 */
async function chatTurn(req, res, { stream = false } = {}) {
  const answer = (status, body) => (stream ? gateway.sseFinal(res, status, body)
    : res.status(status).json(body));
  try {
    const text = typeof (req.body || {}).text === 'string' ? req.body.text.trim() : '';
    // WEB-VISION: optional image attachments. Validate shape + size here; the
    // gateway/_llm_chat admin-gates who can actually use them. Cap 3 images,
    // ~5MB base64 each (the 7mb body parser bounds the total).
    let images;
    const rawImgs = (req.body || {}).images;
    if (Array.isArray(rawImgs) && rawImgs.length) {
      images = [];
      for (const it of rawImgs.slice(0, 3)) {
        const data = it && typeof it.data === 'string' ? it.data : '';
        const mt = it && typeof it.media_type === 'string' ? it.media_type : 'image/png';
        if (data && data.length <= 5_000_000 && /^image\/(png|jpe?g|webp|gif)$/.test(mt)) {
          images.push({ media_type: mt, data });
        }
      }
      if (!images.length) images = undefined;
    }
    if (!text && !images) return res.status(400).json({ error: 'text or image required' });
    if (text.length > MAX_TEXT_LEN) return res.status(400).json({ error: 'Message too long' });
    // An image message skips the local text intercepts and goes straight
    // to the bot's vision-capable chat path.
    if (images) {
      const ident = await resolveBotIdentity(req);
      const payload = {
        telegram_id: ident.id, name: String(ident.email || '').split('@')[0], text, images,
      };
      if (stream) return gateway.postGatewayStream('/chat/stream', payload, res, STREAM_TIMEOUT_MS);
      const r = await gateway.postGateway('/chat', payload, CHAT_TIMEOUT_MS);
      return gateway.relay(res, r);
    }
    // The local intercepts, in table order; the identity is resolved at most
    // once and only when something actually needs it.
    let identP = null;
    const identLazy = () => (identP || (identP = resolveBotIdentity(req)));
    for (const [name, fn] of INTERCEPTS) {
      const reply = await fn(req.user.user_id, text, identLazy);
      if (reply) {
        rememberIntercept(identLazy, text, reply, name);
        return answer(200, reply);
      }
    }
    if (!gateway.isConfigured()) {
      return res.status(503).json({ error: 'Chat not configured' });
    }
    const ident = await identLazy();
    const name = String(ident.email || '').split('@')[0];
    // The user's saved agent profile rides along so the bot's chat prompt
    // knows who it's talking to (risk preference, watchlist). Best-effort —
    // a profile read hiccup must never block chat.
    let profile = null;
    let lang = '';
    try {
      const p = await loadProfile(req.user.user_id);
      if (p.risk_pref || (p.watchlist || []).length) {
        profile = { risk_pref: p.risk_pref, watchlist: p.watchlist };
      }
      // Preferred chat language (the bot LLM replies in it). Best-effort.
      if (p.prefs && typeof p.prefs.lang === 'string') lang = p.prefs.lang;
    } catch (e) { /* chat works without a profile */ }
    const payload = {
      telegram_id: ident.id, name, text,
      ...(profile ? { profile } : {}),
      ...(lang ? { lang } : {}),
      // The cable for `capability_answer(extras=...)`. Sent on every turn
      // rather than only on a capability ask, because this route does not
      // classify the message — the bot does — and a field the bot only
      // sometimes receives is a field that is sometimes missing for reasons
      // nobody can reconstruct.
      client_capabilities: interceptSays(),
    };
    if (stream) return gateway.postGatewayStream('/chat/stream', payload, res, STREAM_TIMEOUT_MS);
    const r = await gateway.postGateway('/chat', payload, CHAT_TIMEOUT_MS);
    return gateway.relay(res, r);
  } catch (err) {
    console.error('Chat proxy error:', err.stack || err.message);
    if (res.headersSent) { try { res.end(); } catch (e) { /* gone */ } return; }
    return res.status(502).json({ error: 'Chat unavailable' });
  }
}

// POST /api/chat  body: { text }
router.post('/', chatLimit, (req, res) => chatTurn(req, res, { stream: false }));
// POST /api/chat/stream  body: { text } — the same turn as text/event-stream.
router.post('/stream', chatLimit, (req, res) => chatTurn(req, res, { stream: true }));

// GET /api/chat/history?limit=30
router.get('/history', async (req, res) => {
  try {
    if (!gateway.isConfigured()) {
      return res.status(503).json({ error: 'Chat not configured' });
    }
    const ident = await resolveBotIdentity(req);
    const limit = Math.min(parseInt(req.query.limit) || 30, 100);
    const r = await gateway.getGateway(
      `/chat/history?telegram_id=${encodeURIComponent(ident.id)}&limit=${limit}`);
    return gateway.relay(res, r);
  } catch (err) {
    console.error('Chat history proxy error:', err.stack || err.message);
    return res.status(502).json({ error: 'Chat unavailable' });
  }
});

module.exports = router;
// The routing table, readable by tests — see the note on INTERCEPTS.
module.exports.INTERCEPTS = INTERCEPTS;
module.exports.interceptSays = interceptSays;
module.exports.rememberIntercept = rememberIntercept;
