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

async function renderFor(key, rechecked) {
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
  if (rechecked) {
    // The re-verify tap really ran: the recompute above happened on THIS
    // request, and the stamp carries the SERVER's clock — never a client
    // string (nothing from the POST body reaches the drawing).
    const at = new Date().toISOString().slice(0, 16).replace('T', ' ');
    c.center('RECHECKED ' + at + ' UTC - TAP RE-VERIFY ANYTIME', 302, 2, COLORS.gold);
    c.center('OPEN TO RE-DERIVE THE HASH IN YOUR OWN BROWSER', 356, 2, COLORS.muted);
    return c.png();
  }
  c.center('SEALED AT DECISION TIME - BEFORE THE OUTCOME', 320, 2, COLORS.muted);
  c.center('OPEN TO RE-DERIVE THE HASH IN YOUR OWN BROWSER', 356, 2, COLORS.muted);
  return c.png();
}

router.get('/call/:key/image', async (req, res) => {
  try {
    const key = String(req.params.key || '');
    if (!KEY_RE.test(key)) return res.status(400).json({ error: 'Bad key' });
    if (req.query.rechecked != null) {
      // Recheck variants carry a clock line, so they cache by the minute —
      // bounded in the same map under a bucketed key, and briefly at the edge.
      const bucket = key + '|R' + new Date().toISOString().slice(0, 16);
      let rp = cache.get(bucket);
      if (!rp) {
        rp = await renderFor(key, true);
        if (cache.size >= CACHE_MAX) cache.delete(cache.keys().next().value);
        cache.set(bucket, rp);
      }
      return res.type('png').set('Cache-Control', 'public, max-age=60').send(rp);
    }
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

// ── Frame POST interactions — the feed taps back ─────────────────────────────
// A Farcaster client POSTs a signed packet when a viewer taps a post-action
// button. We deliberately ignore the packet: these interactions grant
// nothing and read nothing private, so no identity is needed — the response
// is the same public card, re-verified on THIS request. The signed-packet
// fields are untrusted input and none of them reach the drawing or the URLs
// (both are rebuilt server-side from the validated path param alone).
const { callVerifyFrame, traderRefreshFrame } = require('../lib/frame_meta');
const publicOrigin = require('../lib/public_origin');

router.post('/call/:key/verify', (req, res) => {
  const key = String(req.params.key || '');
  const origin = publicOrigin.configured();
  if (!origin || !KEY_RE.test(key)) return res.status(400).json({ error: 'Bad key' });
  const html = callVerifyFrame(key, origin, Date.now().toString(36));
  if (!html) return res.status(400).json({ error: 'Bad key' });
  res.type('html').set('Cache-Control', 'no-store').send(html);
});

// ── Leaderboard card — the whole board as a feed image ───────────────────────
// One source of truth: the SAME computeLeaderboard the JSON route serves,
// so the card can never disagree with the API. §4 drawn strictly: handles,
// percent and counts only. An empty board answers an honest card.
const boardCache = { at: 0, png: null };

async function renderBoard() {
  const { computeLeaderboard } = require('./arena');
  const board = await computeLeaderboard();
  const c = baseCard('PAPER ARENA - VIRTUAL STAKE');
  if (!board.rows.length) {
    c.center('NO RANKED TRADERS YET', 190, 3, COLORS.muted);
    c.center('HANDLES ARE OPT-IN', 250, 2, COLORS.muted);
    c.center('PERCENT AND COUNTS ONLY - NEVER AN AMOUNT', 330, 2, COLORS.muted);
    return c.png();
  }
  c.center('LEADERBOARD - TOP ' + Math.min(5, board.rows.length)
    + ' OF ' + board.ranked_total, 132, 2, COLORS.text);
  board.rows.slice(0, 5).forEach((r, i) => {
    const pct = Number(r.return_pct) || 0;
    const line = '#' + r.rank + ' ' + String(r.handle).toUpperCase()
      + ' ' + (pct >= 0 ? '+' : '-') + Math.abs(pct).toFixed(2) + ' PCT'
      + '  SEALED:' + r.sealed + '/' + r.closes;
    c.center(line, 168 + i * 30, 2, pct >= 0 ? COLORS.up : COLORS.down);
  });
  c.center('PERCENT AND COUNTS ONLY - NEVER AN AMOUNT', 330, 2, COLORS.muted);
  c.center('EVERY SEALED CLOSE VERIFIES IN YOUR BROWSER', 360, 2, COLORS.muted);
  return c.png();
}

router.get('/leaderboard/image', async (req, res) => {
  try {
    if (!boardCache.png || Date.now() - boardCache.at > 60_000) {
      boardCache.png = await renderBoard();
      boardCache.at = Date.now();
    }
    res.type('png').set('Cache-Control', 'public, max-age=60').send(boardCache.png);
  } catch (err) {
    console.error('Board frame error:', err.stack || err.message);
    res.status(500).json({ error: 'Card unavailable' });
  }
});

router.post('/leaderboard/refresh', (req, res) => {
  const origin = publicOrigin.configured();
  if (!origin) return res.status(400).json({ error: 'No public origin' });
  const { boardRefreshFrame } = require('../lib/frame_meta');
  res.type('html').set('Cache-Control', 'no-store')
    .send(boardRefreshFrame(origin, Date.now().toString(36)));
});

router.post('/trader/:handle/refresh', (req, res) => {
  const handle = String(req.params.handle || '').trim();
  const origin = publicOrigin.configured();
  if (!origin || !HANDLE_RE.test(handle)) return res.status(400).json({ error: 'Bad handle' });
  const html = traderRefreshFrame(handle, origin, Date.now().toString(36));
  if (!html) return res.status(400).json({ error: 'Bad handle' });
  res.type('html').set('Cache-Control', 'no-store').send(html);
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

/**
 * Daily Duel share card.
 *
 * The thing a player wants to post is "I beat the Claw 3 times this week", and
 * this draws exactly that — a handle, a hit rate, a call count and a beat
 * count. §4 costs nothing here: a duel has no stake, so there is no amount to
 * leave out in the first place.
 *
 * A player whose record is too short to read gets a card that SAYS so. The
 * alternative — drawing 0% because the number was null — would publish a
 * confident failure about somebody who has simply not played enough yet, and
 * publish it as an image, which is the one form nobody can check.
 */
const duelCache = new Map();

router.get('/duel/:handle/image', async (req, res) => {
  try {
    const handle = String(req.params.handle || '').trim();
    if (!HANDLE_RE.test(handle)) return res.status(400).json({ error: 'Bad handle' });
    const hit = duelCache.get(handle);
    if (hit && Date.now() - hit.at < 60_000) {
      return res.type('png').set('Cache-Control', 'public, max-age=60').send(hit.png);
    }

    const { pool } = require('../db');
    const duel = require('../lib/duel');
    const { playerStanding, MIN_SCORED } = require('../lib/duel_squads');
    const c = baseCard('DAILY DUEL - NO STAKE, NO WAGER');

    const [u] = await pool.execute(
      'SELECT id, leaderboard_handle FROM users WHERE leaderboard_handle = ?', [handle]);
    if (!u[0]) {
      c.center('NO SUCH PLAYER', 200, 3, COLORS.down);
      c.center('HANDLES ARE OPT-IN', 270, 2, COLORS.muted);
      const miss = c.png();
      return res.type('png').set('Cache-Control', 'public, max-age=60').send(miss);
    }

    const season = new Date().toISOString().slice(0, 7);
    const [rounds] = await pool.execute(
      'SELECT id, day, idx, symbol, agent_direction, signal_key FROM duel_rounds'
      + ' WHERE day >= ? ORDER BY day, idx', [season + '-01']);
    const [picks] = await pool.execute(
      'SELECT id, round_id, pick, entry_price, resolves_at, settle_price, settle_state,'
      + ' seal, created_at FROM duel_picks WHERE user_id = ?', [u[0].id]);
    const entries = duel.scoreEntries(
      (rounds || []).filter((r) => String(r.day).slice(0, 7) === season), picks || []);
    const st = playerStanding(handle, entries);

    c.center('#' + handle, 150, 3, COLORS.text);
    if (st.accuracy_pct == null) {
      // Not yet readable — said plainly, and in the neutral colour, because a
      // green or red stripe here would be a verdict on a record that does not
      // exist yet.
      c.center('WARMING UP', 210, 4, COLORS.muted);
      c.center(MIN_SCORED + ' SETTLED CALLS TO RANK', 280, 2, COLORS.muted);
    } else {
      c.center(String(st.accuracy_pct) + ' PCT', 205, 6,
        st.accuracy_pct >= 50 ? COLORS.up : COLORS.down);
      c.center('OF ' + st.calls + ' CALLS', 285, 2, COLORS.muted);
      if (st.beat_agent > 0) {
        c.center('BEAT THE CLAW ' + st.beat_agent + 'X', 325, 2, COLORS.gold);
      }
    }

    const png = c.png();
    if (duelCache.size > CACHE_MAX) duelCache.clear();
    duelCache.set(handle, { at: Date.now(), png });
    res.type('png').set('Cache-Control', 'public, max-age=60').send(png);
  } catch (err) {
    console.error('Duel card error:', err.stack || err.message);
    res.status(503).json({ error: 'Card unavailable' });
  }
});

module.exports = router;

