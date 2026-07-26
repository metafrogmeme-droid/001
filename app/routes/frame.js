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

function baseCard() {
  const c = new Card(W, H, COLORS.bg);
  c.fillRect(0, 0, W, 4, COLORS.gold);
  c.fillRect(0, H - 4, W, 4, COLORS.gold);
  c.center('RUNECLAW', 36, 5, COLORS.gold);
  c.center('PROVABLE CALL', 92, 2, COLORS.muted);
  return c;
}

async function renderFor(key) {
  const { lookupCall } = require('./call');
  const r = await lookupCall(key);
  const c = baseCard();
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

module.exports = router;
