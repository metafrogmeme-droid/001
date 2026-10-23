'use strict';

/**
 * POST /api/botcheck/submit.
 *
 * Anonymous: the page form stores no account, and a queued name is not
 * published. Three bounds sit on the write. Five requests a minute per
 * client IP. A 1kb JSON parser mounted in server.js ahead of the global
 * parser, answered here by rejectOversized. A numeric id is refused in
 * lib/botcheck and not stored.
 *
 * 404 unless BOTCHECK_ENABLED is 1, true, yes, or on. The body fields read
 * are query and note. Anything else on the body is ignored.
 */

const express = require('express');

const { rateLimit, ipKey } = require('../lib/rate_limit');
const check = require('../lib/botcheck');

const router = express.Router();

function rejectOversized(err, req, res, next) {
  const status = err && (err.status || err.statusCode);
  if (status === 413) {
    return res.set('Cache-Control', 'no-store').status(413).json({
      ok: false,
      error: 'That submission is too long.',
    });
  }
  return next(err);
}

router.use((req, res, next) => {
  if (!check.enabled()) {
    return res.set('Cache-Control', 'no-store').status(404).json({ error: 'not_found' });
  }
  return next();
});

router.post('/', rateLimit({ name: 'botcheck.submit', windowMs: 60000, max: 5, key: ipKey }), (req, res) => {
  try {
    const body = req.body && typeof req.body === 'object' ? req.body : {};
    const query = typeof body.query === 'string' ? body.query : '';
    const note = typeof body.note === 'string' ? body.note : '';
    const result = check.enqueue(query, { note: note, via: 'web' });
    return res.set('Cache-Control', 'no-store').status(result.ok ? 200 : 400).json(result);
  } catch (err) {
    console.error('[botcheck]', err && err.name ? err.name : 'Error');
    return res.set('Cache-Control', 'no-store').status(500).json({
      ok: false,
      error: 'The check could not be read.',
    });
  }
});

router.rejectOversized = rejectOversized;
module.exports = router;
