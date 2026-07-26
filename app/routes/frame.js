'use strict';
/**
 * Frame images — the Provable Call as a Farcaster frame / OG preview card,
 * rendered by the stdlib pixel engine (lib/pixel_card): no image deps.
 *
 * Honesty on a surface that cannot run the viewer's crypto:
 * - The card states SERVER RECOMPUTED — a feed image can't re-derive the
 *   hash in the viewer's browser, so it says exactly what was checked and
 *   points at the page that CAN ("open to re-derive it yourself").
 * - The card carries symbol + direction only — sealed public facts. No
 *   prices, no outcomes, no numbers that could read as a pitch in a feed.
 * - An unknown key answers an honest NOT-FOUND card (a broken image would
 *   say nothing; the truth is "no sealed call under this key").
 */

const express = require('express');
const crypto = require('crypto');
const { rateLimit, ipKey } = require('../lib/rate_limit');
const { Card, COLORS } = require('../lib/pixel_card');
const { KEY_RE } = require('../lib/frame_meta');

const router = express.Router();
router.use(rateLimit({ windowMs: 60000, max: 30, key: ipKey }));

const W = 800, H = 418; // 1.91:1 — the frame/OG aspect

// Seals are immutable, so cards cache hard; the map stays small and bounded.
const cache = new Map();
const CACHE_MAX = 200;

function baseCard(subtitle) {
  const c = new Card(W, H, COLORS.bg);
  c.fillRect(0, 0, W, 4, COLORS.gold);
  c.fillRect(0, H - 4, W, 4, COLORS.gold);
  c.center('RUNECLAW', 36, 5, COLORS.gold);
  c.center(subtitle, 92, 2, COLORS.muted);
  return c;
}

async function renderFor(key) {
  const { lookupCall } = require('./call');
  const r = await lookupCall(key);
  const c = baseCard('PROVABLE CALL');
  if (r.code !== 200) {
    c.center('NO SEALED CALL', 180, 3, COLORS.down);
    c.center('UNDER THIS KEY', 230, 3, COLORS.down);
    c.center('UNKNOWN IS NOT A VERDICT', 300, 2, COLORS.muted);
    return c.png();
  }
  const d = r.body;
  const dir = String(d.direction || '').toUpperCase();
  const line = `${String(d.symbol || '').toUpperCase()} ${dir}`.trim();
  c.center(line, 150, 4, dir === 'SHORT' ? COLORS.down : COLORS.up);
  c.center('SEAL ' + String(d.seal || '').slice(0, 32), 220, 2, COLORS.text);
  const recomputed = crypto.createHash('sha256')
    .update(String(d.seal_payload || ''), 'utf8').digest('hex');
  const match = recomputed === String(d.seal);
  c.center(match ? 'SERVER RECOMPUTED: MATCH' : 'SERVER RECOMPUTED: MISMATCH',
    266, 2, match ? COLORS.up : COLORS.down);
  c.center('SEALED AT DECISION TIME - BEFORE THE OUTCOME', 320, 2, COLORS.muted);
  c.center('OPEN TO RE-DERIVE THE HASH IN YOUR OWN BROWSER', 356, 2, COLORS.muted);
  return c.png();
}

router.get('/call/:key/image', async (req, res) => {
  try {
    const key = String(req.params.key || '');
    if (!KEY_RE.test(key)) return res.status(400).json({ error: 'Bad key' });
    let png = cache.get(key);
    if (!png) {
      png = await renderFor(key);
      if (cache.size >= CACHE_MAX) cache.delete(cache.keys().next().value);
      cache.set(key, png);
    }
    res.type('png').set('Cache-Control', 'public, max-age=300').send(png);
  } catch (err) {
    console.error('Frame image error:', err.stack || err.message);
    res.status(500).json({ error: 'Card unavailable' });
  }
});

// ── Public trader card — the leaderboard record as a feed image ──────────────
// §4 on a public surface, drawn strictly: SETTLED percent (from the closed
// balance alone — open positions and marks deliberately excluded, so the
// number is a fact, not a snapshot guess) plus counts. No amount is drawn,
// not even the virtual one. Trader records change, so this cache is a 60s
// TTL, unlike the immutable seal cards above.
const { buildTraderCard, HANDLE_RE } = require('../lib/arena_trader');
const traderCache = new Map();

router.get('/trader/:handle/image', async (req, res) => {
  try {
    const handle = String(req.params.handle || '').trim();
    if (!HANDLE_RE.test(handle)) return res.status(400).json({ error: 'Bad handle' });
    const hit = traderCache.get(handle);
    if (hit && Date.now() - hit.at < 60_000) {
      return res.type('png').set('Cache-Control', 'public, max-age=60').send(hit.png);
    }
    const { pool } = require('../db');
    const c = baseCard('PAPER TRADER - VIRTUAL STAKE');
    const [u] = await pool.execute('SELECT id FROM users WHERE leaderboard_handle = ?', [handle]);
    if (!u[0]) {
      c.center('NO SUCH TRADER', 200, 3, COLORS.down);
      c.center('HANDLES ARE OPT-IN', 270, 2, COLORS.muted);
      const png = c.png();
      return res.type('png').set('Cache-Control', 'public, max-age=60').send(png);
    }
    const [acct] = await pool.execute(
      'SELECT user_id, balance FROM arena_accounts WHERE user_id = ?', [u[0].id]);
    if (!acct[0]) {
      // A zero balance would draw as -100 PCT — a fabrication. Say the truth.
      c.center('NO ARENA ACCOUNT YET', 200, 3, COLORS.down);
      const png0 = c.png();
      return res.type('png').set('Cache-Control', 'public, max-age=60').send(png0);
    }
    const [trades] = await pool.execute(
      'SELECT id, symbol, direction, entry, exit_price, margin, leverage, pnl, reason, trade_key, seal, opened_at, closed_at FROM arena_trades WHERE user_id = ? ORDER BY id DESC LIMIT 30',
      [u[0].id]);
    const card = buildTraderCard({ handle, balance: acct[0].balance,
      positions: [], marks: {}, trades: trades || [] });
    c.center('#' + handle, 150, 3, COLORS.text);
    const pct = card.return_pct;
    c.center('SETTLED RETURN ' + (pct >= 0 ? '' : '-') + Math.abs(pct).toFixed(2) + ' PCT',
      206, 4, pct >= 0 ? COLORS.up : COLORS.down);
    c.center('CLOSES:' + card.closed_trades + '  SEALED:' + card.receipts.sealed
      + '  STREAK:' + card.streak_days, 276, 2, COLORS.text);
    c.center('PERCENT AND COUNTS ONLY - NEVER AN AMOUNT', 320, 2, COLORS.muted);
    c.center('OPEN THE RECORD - EVERY SEALED CLOSE VERIFIES', 356, 2, COLORS.muted);
    const png = c.png();
    if (traderCache.size >= CACHE_MAX) traderCache.delete(traderCache.keys().next().value);
    traderCache.set(handle, { at: Date.now(), png });
    res.type('png').set('Cache-Control', 'public, max-age=60').send(png);
  } catch (err) {
    console.error('Trader frame error:', err.stack || err.message);
    res.status(500).json({ error: 'Card unavailable' });
  }
});

module.exports = router;

