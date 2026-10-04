/**
 * One signal's thesis and its counter-case.
 *
 * Public, like the stream. The stored column is the model's reasoning with
 * the provenance tag the bot stamps in front of it, and the counter-case is
 * the "Against:" the thesis prompt asks the model to write last. Both are
 * read through ThesisModel, the same split the cards use
 * (bot/formatters/thesis_text.py). This route does not invent either sentence.
 *
 * The id is a query parameter. A slash in a path segment does not survive a
 * hop, and a signal id is allowed to carry one.
 */

'use strict';

const express = require('express');
const { pool } = require('../db');
const { storedThesis } = require('../public/js/thesis-model');

const router = express.Router();

// The column is VARCHAR(128). A longer id cannot be that row, and trimming
// it to 128 would show a different signal.
const KEY_MAX = 128;

function requestedKey(query) {
  if (!query || typeof query !== 'object' || !Object.prototype.hasOwnProperty.call(query, 'key')) {
    return { status: 400, error: 'signal_key_required' };
  }
  const raw = query.key;
  if (Array.isArray(raw) || raw == null) return { status: 400, error: 'signal_key_required' };
  const key = String(raw).trim();
  if (!key) return { status: 400, error: 'signal_key_required' };
  if (key.length > KEY_MAX) return { status: 404, error: 'signal_not_found' };
  return { key };
}

// GET /api/signal?key=
router.get('/', async (req, res) => {
  const asked = requestedKey(req.query);
  if (!asked.key) return res.status(asked.status).json({ error: asked.error });
  try {
    const [rows] = await pool.execute(
      'SELECT signal_key, symbol, direction, thesis FROM signals WHERE signal_key = ?',
      [asked.key]
    );
    const row = rows && rows[0];
    if (!row) return res.status(404).json({ error: 'signal_not_found' });
    const read = storedThesis(row.thesis);
    // Allowlist. Prices, R, the seal and the outcome are other sentences.
    // A signal's pnl is not on this payload.
    res.json({
      signal_key: row.signal_key,
      symbol: row.symbol == null ? null : String(row.symbol),
      direction: row.direction == null ? null : String(row.direction),
      thesis: read.thesis,
      counter_case: read.counter_case,
    });
  } catch (err) {
    // The class, never the message: a driver detail is not a sentence for
    // the page, and the page does not read this log.
    console.error('Signal page error:', err && err.name ? err.name : 'Error');
    res.status(503).json({ error: 'signal_unavailable' });
  }
});

module.exports = router;
module.exports.requestedKey = requestedKey;
