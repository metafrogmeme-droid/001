'use strict';

/**
 * /botcheck and GET /api/botcheck.
 *
 * Both answer 404 unless BOTCHECK_ENABLED is 1, true, yes, or on. This module
 * is the read: the curated catalog, nothing written. The review-queue write
 * is routes/botcheck_submit.js.
 *
 * The page shell lives in content/, not public/, so the static handler cannot
 * serve it while the flag is off. The asset strings below are the ones the
 * shell loads; a shell that lost one is not sent.
 */

const fs = require('fs');
const path = require('path');
const express = require('express');

const { rateLimit, ipKey } = require('../lib/rate_limit');
const nf = require('../lib/not_found');
const check = require('../lib/botcheck');

const PAGE_FILE = path.join(__dirname, '..', 'content', 'botcheck.html');
const PAGE_ASSETS = ['/styles.css?v=53', '/js/botcheck.js?v=1'];

const router = express.Router();

function unavailable(req, res) {
  const out = nf.notFound(req.path || '/botcheck', req.get && req.get('accept'));
  res.set('Cache-Control', 'no-store').status(out.status);
  if (out.type === 'json') return res.json(out.body);
  return res.type('html').send(out.body);
}

function fail(res, status) {
  return res.set('Cache-Control', 'no-store').status(status).json({
    ok: false,
    error: 'The check could not be read.',
  });
}

function page(req, res) {
  if (!check.enabled()) return unavailable(req, res);
  let html;
  try {
    html = fs.readFileSync(PAGE_FILE, 'utf8');
  } catch (err) {
    console.error('[botcheck]', err && err.name ? err.name : 'Error');
    return res.status(500).set('Cache-Control', 'no-store').type('html').send('The check could not be read.');
  }
  for (const asset of PAGE_ASSETS) {
    if (!html.includes(asset)) {
      return res.status(500).set('Cache-Control', 'no-store').type('html').send('The check could not be read.');
    }
  }
  return res.set('Cache-Control', 'no-cache').type('html').send(html);
}

router.use((req, res, next) => {
  if (!check.enabled()) return res.set('Cache-Control', 'no-store').status(404).json({ error: 'not_found' });
  return next();
});

router.get('/', rateLimit({ name: 'botcheck.read', windowMs: 60000, max: 30, key: ipKey }), (req, res) => {
  try {
    const raw = req.query ? req.query.q : undefined;
    if (raw != null && typeof raw !== 'string') return fail(res, 400);
    const body = raw ? check.lookup(raw) : check.publicDocument();
    return res.set('Cache-Control', 'no-store').json(body);
  } catch (err) {
    console.error('[botcheck]', err && err.name ? err.name : 'Error');
    return fail(res, 500);
  }
});

router.page = page;
module.exports = router;
