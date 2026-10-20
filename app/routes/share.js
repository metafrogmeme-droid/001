/**
 * Share cards — server-rendered PNG for the closed-trade share flow.
 *
 * GET /api/share/card?symbol=&direction=&pnl_pct=[&opened_at=&closed_at=&book=]
 *
 * The card is a pure function of three public inputs (symbol, direction,
 * PnL percent) rendered by the bot's Pillow renderer and relayed here as
 * binary. It carries NO dollar figure, size, or account data by design —
 * the share flow exists so a win can be shared without leaking account
 * size. JWT-authed (render work is CPU-bound; anonymous callers can't
 * hammer it) but deliberately identity-free: no telegram lookup, no per-
 * user data on this path.
 */

const express = require('express');
const { authMiddleware } = require('../auth');
const { rateLimit, userKey } = require('../lib/rate_limit');
const { getGatewayBinary, isConfigured } = require('../lib/gateway');

const router = express.Router();
router.use(authMiddleware);
router.use(rateLimit({ name: 'share', windowMs: 60000, max: 30, key: userKey }));

const SYMBOL_RE = /^[A-Z0-9]{1,15}$/;

/**
 * kind=plan: the dashboard's decision picture, a PLAN card (symbol, direction,
 * the price move to the take-profit, an optional leverage and instant). The
 * bot draws it as a plan, never in a result's voice. The query, or a 400.
 */
function planQuery(req, symbol, direction) {
  const target = Number(req.query.target_pct);
  if (req.query.target_pct === undefined || req.query.target_pct === '' || !Number.isFinite(target)) {
    return { error: 'Invalid target_pct' };
  }
  const qs = new URLSearchParams({ kind: 'plan', symbol, direction, target_pct: target.toFixed(2) });
  if (req.query.leverage !== undefined && req.query.leverage !== '') {
    const lev = Number(req.query.leverage);
    if (!Number.isFinite(lev) || lev <= 0 || lev > 200) return { error: 'Invalid leverage' };
    qs.set('leverage', String(lev));
  }
  if (req.query.at !== undefined && req.query.at !== '') {
    const ms = Date.parse(String(req.query.at));
    if (!Number.isFinite(ms)) return { error: 'Invalid at' };
    qs.set('at', new Date(ms).toISOString());
  }
  return { qs };
}

router.get('/card', async (req, res) => {
  if (!isConfigured()) return res.status(503).json({ error: 'Not configured' });
  const symbol = String(req.query.symbol || '').toUpperCase().trim();
  const direction = String(req.query.direction || '').toUpperCase().trim();
  if (!SYMBOL_RE.test(symbol)) return res.status(400).json({ error: 'Invalid symbol' });
  if (direction !== 'LONG' && direction !== 'SHORT') {
    return res.status(400).json({ error: 'Invalid direction' });
  }
  if (String(req.query.kind || '').toLowerCase() === 'plan') {
    const plan = planQuery(req, symbol, direction);
    if (plan.error) return res.status(400).json({ error: plan.error });
    return relayCard(res, plan.qs);
  }
  const pnl_pct = Number(req.query.pnl_pct);
  if (!Number.isFinite(pnl_pct)) return res.status(400).json({ error: 'Invalid pnl_pct' });
  {
    const qs = new URLSearchParams({ symbol, direction, pnl_pct: pnl_pct.toFixed(2) });
    // Optional: when the trade was opened and closed, and which book, so the
    // card can say so. A time is relayed as an ISO instant; junk is a 400,
    // never a card with a made-up date.
    for (const key of ['opened_at', 'closed_at']) {
      if (req.query[key] === undefined || req.query[key] === '') continue;
      const ms = Date.parse(String(req.query[key]));
      if (!Number.isFinite(ms)) return res.status(400).json({ error: `Invalid ${key}` });
      qs.set(key, new Date(ms).toISOString());
    }
    if (req.query.book !== undefined && req.query.book !== '') {
      const book = String(req.query.book).toLowerCase();
      if (book !== 'paper' && book !== 'live') return res.status(400).json({ error: 'Invalid book' });
      qs.set('book', book);
    }
    return relayCard(res, qs);
  }
});

/** Ask the bot for the PNG and relay it; anything but a PNG is a 502. */
async function relayCard(res, qs) {
  try {
    const r = await getGatewayBinary(`/share-card?${qs.toString()}`, 15000);
    if (r.status !== 200 || !/image\/png/.test(r.contentType)) {
      return res.status(502).json({ error: 'Card unavailable' });
    }
    return res.type('png').set('Cache-Control', 'private, max-age=300').send(r.body);
  } catch (err) {
    console.error('Share card error:', err.stack || err.message);
    return res.status(502).json({ error: 'Card unavailable' });
  }
}

module.exports = router;
