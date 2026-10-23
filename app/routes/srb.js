'use strict';
/**
 * The shadow restitution record.
 *
 * Operator only. There is no public dashboard. A caller who is not the
 * operator gets 404, the same answer as a path this server does not have.
 * The admin view carries the bond label. The public block does not.
 */

const { spawnSync } = require('node:child_process');
const path = require('node:path');

const express = require('express');

const { optionalAuth } = require('../auth');
const { PREVIEW, access } = require('../lib/srb_flag');

const ROOT = path.join(__dirname, '..', '..');
const router = express.Router();

function loadReport() {
  const run = spawnSync('python3', ['-m', 'bot.srb.report'], {
    cwd: ROOT,
    encoding: 'utf8',
    timeout: 15000,
  });
  if (!run || run.status !== 0 || !run.stdout) {
    const err = new Error('SrbUnreadable');
    err.code = 'SrbUnreadable';
    throw err;
  }
  const parsed = JSON.parse(run.stdout);
  if (!parsed || !parsed.public) {
    const err = new Error('SrbUnreadable');
    err.code = 'SrbUnreadable';
    throw err;
  }
  return parsed;
}

router.get('/signals', optionalAuth, (req, res) => {
  access(req, res).then((decision) => {
    if (!decision.ok) return res.status(404).type('text/plain').send('Not found');
    let report;
    try {
      report = loadReport();
    } catch (err) {
      return res.status(503).json({ error: 'SrbUnreadable' });
    }
    const body = {
      preview: decision.preview,
      banner: decision.preview ? PREVIEW : null,
      public: report.public,
    };
    if (decision.admin) body.admin = report.admin;
    return res.json(body);
  }).catch(() => {
    if (!res.headersSent) res.status(404).type('text/plain').send('Not found');
  });
});

module.exports = router;
