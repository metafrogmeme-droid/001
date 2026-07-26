'use strict';
/**
 * Trade Learn Center — the daily trade diary (study room, stage 1).
 *
 * PRIVATE per-user surface, all routes authed. The diary is the reflective
 * layer the platform records can't provide: the trader's own words, one
 * entry per UTC day, with that day's closed Arena trades attached AT READ
 * TIME (derived live, never copied — a stored duplicate could drift from
 * the sealed record).
 *
 * Honesty mechanics:
 * - Editing an existing day is allowed but never silent: edited_at is set
 *   and the UI wears the same "✎ edited" marker the Arena exits wear.
 * - Future days are refused — a diary describes days that happened.
 * - The streak is a count of consecutive journaled days; it never invents
 *   grace beyond "today not written yet".
 */

const express = require('express');
const { authMiddleware } = require('../auth');
const { pool } = require('../db');

const router = express.Router();
router.use(authMiddleware);

const DAY_RE = /^\d{4}-\d{2}-\d{2}$/;
const MAX_BODY = 4000;

function todayUtc() { return new Date().toISOString().slice(0, 10); }

function dayWindow(day) {
  const lo = new Date(day + 'T00:00:00.000Z');
  return { lo, hi: new Date(lo.getTime() + 86_400_000) };
}

/** Consecutive journaled days ending today or yesterday (today may simply
 *  not be written yet — that does not break a streak; a missed day does). */
function streakOf(daysDesc) {
  const have = new Set(daysDesc);
  let cursor = todayUtc();
  if (!have.has(cursor)) {
    cursor = new Date(Date.parse(cursor) - 86_400_000).toISOString().slice(0, 10);
  }
  let n = 0;
  while (have.has(cursor)) {
    n++;
    cursor = new Date(Date.parse(cursor) - 86_400_000).toISOString().slice(0, 10);
  }
  return n;
}

async function tradesOfDay(userId, day) {
  const { lo, hi } = dayWindow(day);
  const [rows] = await pool.execute(
    `SELECT symbol, direction, pnl, reason, trade_key, closed_at FROM arena_trades
     WHERE user_id = ? AND closed_at >= ? AND closed_at < ?`, [userId, lo, hi]);
  return (rows || []).map((t) => ({
    symbol: t.symbol, direction: t.direction,
    pnl: Math.round(Number(t.pnl) * 100) / 100,
    reason: t.reason, trade_key: t.trade_key || null,
  }));
}

// The diary list + streak. Entries come back day-desc, capped at 60.
router.get('/diary', async (req, res) => {
  try {
    const [rows] = await pool.execute(
      `SELECT day, body, created_at, edited_at FROM learn_diary
       WHERE user_id = ? ORDER BY day DESC LIMIT 60`, [req.user.user_id]);
    const entries = (rows || []).map((e) => ({
      day: e.day, body: e.body,
      edited: e.edited_at != null,
    }));
    res.json({
      entries,
      streak: streakOf(entries.map((e) => e.day)),
      total: entries.length,
      private: true,
    });
  } catch (err) {
    console.error('Diary list error:', err.stack || err.message);
    res.status(500).json({ error: 'Diary unavailable' });
  }
});

// One day: the entry (or null, honestly) plus that day's closed trades.
router.get('/diary/:day', async (req, res) => {
  try {
    const day = String(req.params.day || '');
    if (!DAY_RE.test(day)) return res.status(400).json({ error: 'Bad day (YYYY-MM-DD)' });
    const [rows] = await pool.execute(
      `SELECT day, body, created_at, edited_at FROM learn_diary
       WHERE user_id = ? AND day = ?`, [req.user.user_id, day]);
    const e = rows && rows[0];
    res.json({
      day,
      entry: e ? { body: e.body, edited: e.edited_at != null } : null,
      trades: await tradesOfDay(req.user.user_id, day),
      private: true,
    });
  } catch (err) {
    console.error('Diary read error:', err.stack || err.message);
    res.status(500).json({ error: 'Diary unavailable' });
  }
});

// Write a day. Insert once; later writes UPDATE and wear edited_at forever.
router.put('/diary/:day', async (req, res) => {
  try {
    const day = String(req.params.day || '');
    if (!DAY_RE.test(day)) return res.status(400).json({ error: 'Bad day (YYYY-MM-DD)' });
    if (day > todayUtc()) {
      return res.status(400).json({ error: 'A diary describes days that happened — that day has not.' });
    }
    const body = String((req.body && req.body.body) || '').trim();
    if (!body) return res.status(400).json({ error: 'Write something first' });
    if (body.length > MAX_BODY) {
      return res.status(400).json({ error: `Too long (max ${MAX_BODY} characters)` });
    }
    const [rows] = await pool.execute(
      'SELECT day FROM learn_diary WHERE user_id = ? AND day = ?', [req.user.user_id, day]);
    const exists = rows && rows.length > 0;
    // Upsert, not select-then-insert: two concurrent first saves must not
    // duplicate-key. The duplicate path IS a second write, so it earns the
    // edited_at mark exactly like a sequential edit would.
    await pool.execute(
      `INSERT INTO learn_diary (user_id, day, body, created_at) VALUES (?, ?, ?, ?)
       ON DUPLICATE KEY UPDATE body = VALUES(body), edited_at = VALUES(created_at)`,
      [req.user.user_id, day, body, new Date()]);
    res.json({ saved: true, day, edited: exists });
  } catch (err) {
    console.error('Diary write error:', err.stack || err.message);
    res.status(500).json({ error: 'Could not save — nothing was changed' });
  }
});

module.exports = router;
