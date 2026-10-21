'use strict';
/**
 * GET /api/public/record[?week=YYYY-Www] — the weekly public record, for anyone.
 *
 * Public by construction: the committed weeks under `benchmark/record/`
 * (the website's copy, app/content/record/), each projected through the
 * allowlist in app/lib/weekly_record.js: percent, ratio and count only. No
 * account is in scope and nothing is read from the bot; a week is what was
 * frozen in git that week. An unreadable week is a 503 that names the
 * exception class, never a 200 with no record, which the page would paint
 * as a record never kept.
 */

const express = require('express');
const { rateLimit, ipKey } = require('../lib/rate_limit');
const weekly = require('../lib/weekly_record');

const router = express.Router();
router.use(rateLimit({ name: 'public_record', windowMs: 60000, max: 30, key: ipKey }));

router.get('/', (req, res) => {
  const week = typeof req.query.week === 'string' ? req.query.week : '';
  if (week && !weekly.WEEK.test(week)) return res.status(400).json({ error: 'bad_week' });
  let body;
  try {
    body = weekly.publicRecord({ week: week || undefined });
  } catch (err) {
    // The class, never the message: a path or a parser detail is not a
    // sentence for the page.
    const name = err && err.name ? String(err.name) : 'Error';
    console.error('Weekly record error:', name);
    return res.status(503).json({ error: 'record_unreadable', exception: name });
  }
  if (body.unknown_week) return res.status(404).json({ error: 'unknown_week', weeks: body.weeks });
  res.setHeader('Cache-Control', 'no-cache');
  res.json(body);
});

module.exports = router;
