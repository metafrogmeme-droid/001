'use strict';
/**
 * GET /api/call/:key — Provable Calls verify feed (public).
 *
 * Serves the sealed receipt for one signal: the seal (sha256 hex), the
 * canonical decision-time payload EXACTLY as sealed, when it was sealed,
 * the row's CURRENT decision-time values (so the client can surface any
 * drift — there should never be any), and the outcome. The client
 * re-derives sha256(seal_payload) with WebCrypto and compares — the server
 * is never trusted to say "verified".
 */

const express = require('express');
const { pool } = require('../db');
const { anchorFor } = require('../lib/seal_roots');

const router = express.Router();

// v3: the receipt's place in its day's Merkle root — null while the seal's
// UTC day is still open (never an early commitment), fail-open on errors.
async function anchorOf(seal, sealedAt) {
  try { return await anchorFor(seal, sealedAt); } catch (e) { return null; }
}

const KEY_RE = /^[A-Za-z0-9:_.\-]{4,128}$/;

const round2 = (n) => Math.round((Number(n) || 0) * 100) / 100;

// Arena receipt (Provable Calls v2): open position first, then closed trade.
// §4: this is a PUBLIC feed — percent return on margin only; margin and pnl
// (virtual dollars) never leave the row.
async function findArena(key) {
  const [open] = await pool.execute(
    'SELECT symbol, direction, entry, leverage, tp, sl, trade_key, seal, seal_payload, sealed_at, opened_at FROM arena_positions WHERE trade_key = ?', [key]);
  if (open[0] && open[0].seal) return { row: open[0], closed: false };
  const [done] = await pool.execute(
    'SELECT symbol, direction, entry, exit_price, margin, leverage, pnl, reason, trade_key, seal, seal_payload, sealed_at, opened_at, closed_at FROM arena_trades WHERE trade_key = ?', [key]);
  if (done[0] && done[0].seal) return { row: done[0], closed: true };
  return null;
}

router.get('/:key', async (req, res) => {
  try {
    const key = String(req.params.key || '');
    if (!KEY_RE.test(key)) return res.status(400).json({ error: 'Invalid call id' });
    if (key.startsWith('arena:')) {
      const found = await findArena(key);
      if (!found) {
        return res.status(404).json({ error: 'No sealed trade with that id — receipts exist for paper trades opened after Provable Calls v2 shipped.' });
      }
      const t = found.row;
      // Closed-trade rows carry no tp/sl columns — omit those keys entirely
      // so the client's drift check only compares what the live row still
      // holds (a null here would read as a false rewrite).
      const current = {
        trade_key: t.trade_key, symbol: t.symbol, direction: t.direction,
        entry: Number(t.entry) || 0,
        leverage: Number(t.leverage) || 0,
        opened_at: t.opened_at,
      };
      if (!found.closed) {
        current.tp = t.tp == null ? null : Number(t.tp);
        current.sl = t.sl == null ? null : Number(t.sl);
      }
      res.set('Cache-Control', 'public, max-age=15');
      return res.json({
        kind: 'arena_trade',
        seal: t.seal,
        seal_payload: t.seal_payload,
        sealed_at: t.sealed_at,
        anchor: await anchorOf(t.seal, t.sealed_at),
        current,
        outcome: found.closed
          ? { status: 'CLOSED', reason: t.reason,
              exit_price: Number(t.exit_price) || 0,
              pct: Number(t.margin) > 0 ? round2((Number(t.pnl) / Number(t.margin)) * 100) : 0,
              closed_at: t.closed_at }
          : { status: 'OPEN' },
      });
    }
    const [rows] = await pool.execute(
      'SELECT signal_key, symbol, direction, confidence, pattern, regime, entry_price, stop_loss, take_profit, status, pnl, created_at, resolved_at, seal, seal_payload, sealed_at FROM signals WHERE signal_key = ?',
      [key]);
    const s = rows[0];
    if (!s || !s.seal) {
      return res.status(404).json({ error: 'No sealed call with that id — receipts exist for calls made after Provable Calls shipped.' });
    }
    res.set('Cache-Control', 'public, max-age=15');
    res.json({
      kind: 'signal',
      seal: s.seal,
      seal_payload: s.seal_payload,
      sealed_at: s.sealed_at,
      anchor: await anchorOf(s.seal, s.sealed_at),
      current: {
        signal_key: s.signal_key, symbol: s.symbol, direction: s.direction,
        entry_price: Number(s.entry_price) || 0,
        stop_loss: Number(s.stop_loss) || 0,
        take_profit: Number(s.take_profit) || 0,
        confidence: Number(s.confidence) || 0,
        pattern: s.pattern || null, regime: s.regime || null,
        created_at: s.created_at,
      },
      outcome: { status: s.status, pnl: s.pnl == null ? null : Number(s.pnl), resolved_at: s.resolved_at },
    });
  } catch (err) {
    console.error('Call verify error:', err.stack || err.message);
    res.status(500).json({ error: 'Verify feed unavailable' });
  }
});

module.exports = router;
