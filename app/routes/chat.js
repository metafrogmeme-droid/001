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
const { maybeHandleAlertChat } = require('../lib/alerts');
const { maybeHandleReplayChat } = require('../lib/replay');
const { maybeHandleLetterChat } = require('../lib/letter');
const { maybeHandleRwaChat } = require('../lib/rwa');
const { maybeHandleWalletChat } = require('../lib/wallet');
const { maybeHandleDefiChat } = require('../lib/defi');
const { maybeHandleNetWorthChat } = require('../lib/networth');
const { maybeHandleIdleYieldChat } = require('../lib/idle_yield');
const { maybeHandleExposureChat } = require('../lib/exposure');
const { maybeHandleResearchChat } = require('../lib/research');

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
 * reply | null`, where `ident()` lazily resolves the bot identity for the
 * two that need it. The first non-null reply is the answer.
 *
 * Why a table and not a column of `if`s: the order IS the routing — "idle"
 * is answered by the yield intercept only because nothing above it claimed
 * the sentence — and a table can be read by a test, which fourteen
 * consecutive early returns could not. None of the fourteen had one.
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
  // "tell me when BTC drops below 100k" — alerts live in the WEB app (the
  // push channel is here), so handle them before the bot proxy. Evaluated
  // against public tickers; works even while the bot process is down.
  ['alerts', (uid, text) => maybeHandleAlertChat(uid, text)],
  // "what if I'd taken every signal with $1k?" — replayed from the web's
  // own recorded trade history, no bot round-trip needed.
  ['replay', (uid, text) => maybeHandleReplayChat(uid, text)],
  // "show me this week's letter" — the weekly fund-style letter, composed
  // from recorded data in the web DB.
  ['letter', (uid, text) => maybeHandleLetterChat(uid, text)],
  // "rwa radar" — read-only tokenized-asset sector snapshot from live tickers.
  ['rwa', (uid, text) => maybeHandleRwaChat(uid, text)],
  // "airdrops" / "testnets" — curated guided-only radar; the reply itself
  // restates the anti-sybil line so chat can never be read as offering
  // automated farming.
  ['airdrops', (uid, text) => require('../lib/airdrops').maybeHandleAirdropChat(uid, text)],
  // "best venue for BTC" — funding-cost venue read; recommendations only.
  ['venues', (uid, text) => require('../lib/venue_router').maybeHandleVenueRouterChat(uid, text)],
  // "meme radar" / "dexscreener" — read-only on-chain meme/AI-token snapshot
  // with an explicit safety read. Never trades or launches.
  ['meme', (uid, text) => require('../lib/meme').maybeHandleMemeChat(uid, text)],
  // "nft radar" / "opensea" — read-only collection floor/volume snapshot.
  // Never lists, bids, mints or trades.
  ['nft', (uid, text) => require('../lib/opensea').maybeHandleNftChat(uid, text)],
  // "spot market" — read-only spot pairs + spot/perp basis. Never orders.
  ['spot', (uid, text) => require('../lib/spot').maybeHandleSpotChat(uid, text)],
  // "my wallet" — read-only mirror of the caller's SIWE-linked wallet.
  ['wallet', (uid, text) => maybeHandleWalletChat(uid, text)],
  // "my defi positions" / "health factor" — Aave/Lido/Uniswap read straight
  // from protocol contracts, with liquidation-risk warnings.
  ['defi', (uid, text) => maybeHandleDefiChat(uid, text)],
  // "what's my total exposure?" — perp positions netted against wallet spot.
  ['exposure', (uid, text) => maybeHandleExposureChat(uid, text)],
  // "research PENDLE" — evidence dossier from trusted local + live sources.
  ['research', (uid, text) => maybeHandleResearchChat(uid, text)],
  // "net worth" — everything the user holds, everywhere, read-only. Needs
  // the resolved bot identity, so its own cheap pattern decides first and
  // the DB lookup only happens on a match.
  ['networth', async (uid, text, ident) => (
    /net ?worth|total (balance|holdings|equity)|balance across|everything i (own|hold)/i.test(text)
      ? maybeHandleNetWorthChat(await ident(), uid, text) : null)],
  // "idle" / "best rate" / "earn more" — idle-asset yield optimizer.
  ['idleyield', async (uid, text, ident) => (
    /\bidle|earn more|best (rate|yield|apy)|put .* to work|stake my|where can i earn\b/i.test(text)
      ? maybeHandleIdleYieldChat(await ident(), uid, text) : null)],
];

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
    // An image message skips the local text-intercepts (alerts/replay/etc.) and
    // goes straight to the bot's vision-capable chat path.
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
module.exports.rememberIntercept = rememberIntercept;
