/**
 * The Agent Letter — read surface for the dashboard panel.
 *
 * GET /api/letter/latest       — last completed week's letter (lazy-generated)
 * GET /api/letter/archive      — recent week keys
 * GET /api/letter/:week        — a specific stored letter (e.g. 2026-W28)
 *
 * JWT-authed. Read-only; letters are composed exclusively from recorded data.
 *
 * TWO LETTERS, AND WHICH ONE IS DECIDED HERE. The stored letter is the
 * operator's: `lib/letter.js` composes it from `OPERATOR_USER_ID`'s rows, in
 * dollars. Every signed-in caller was served it, and registration is open, so
 * any free signup read the operator's net P&L -- the letter `routes/mcp.js`
 * deliberately does NOT serve for exactly that reason. Anyone but the operator
 * gets the public letter for the same week (`getPublicLetter`: the same
 * recorded week, percent and count only, the same shape), flagged `public`.
 */

const express = require('express');
const { authMiddleware } = require('../auth');
const { rateLimit, userKey } = require('../lib/rate_limit');
const letters = require('../lib/letter');
const { isOperator } = require('../lib/operator_view');

const router = express.Router();
router.use(authMiddleware);
router.use(rateLimit({ windowMs: 60000, max: 30, key: userKey }));

router.get('/latest', async (req, res) => {
  try {
    const week = letters.lastCompletedWeek();
    // Stored for every caller, as it always was: the archive lists stored
    // weeks, and the first read of a finished week is what writes it.
    const r = await letters.getLetter(week);
    if (!(await isOperator(req))) {
      const pub = await letters.getPublicLetter(week.key);
      return res.json({ generated_at: r.generated_at, letter: pub, public: true });
    }
    res.json({ generated_at: r.generated_at, letter: r.letter });
  } catch (err) {
    console.error('Letter latest error:', err.stack || err.message);
    res.status(500).json({ error: 'Letter unavailable' });
  }
});

router.get('/archive', async (req, res) => {
  try {
    res.json({ letters: await letters.listLetters(12) });
  } catch (err) {
    console.error('Letter archive error:', err.stack || err.message);
    res.status(500).json({ error: 'Archive unavailable' });
  }
});

router.get('/:week', async (req, res) => {
  try {
    const r = await letters.getLetterByKey(req.params.week);
    if (!r) return res.status(404).json({ error: 'No letter for that week' });
    // The same weeks for every caller -- the ones the archive lists -- and the
    // public letter of that week for anyone but the operator.
    if (!(await isOperator(req))) {
      const pub = await letters.getPublicLetter(req.params.week);
      if (!pub) return res.status(404).json({ error: 'No letter for that week' });
      return res.json({ generated_at: r.generated_at, letter: pub, public: true });
    }
    res.json(r);
  } catch (err) {
    console.error('Letter fetch error:', err.stack || err.message);
    res.status(500).json({ error: 'Letter unavailable' });
  }
});

module.exports = router;
