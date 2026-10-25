'use strict';
/**
 * /marketplace and /market were 404s people still open.
 *
 * The host follows a 3xx itself (lib/navigate.js), so the alias answers a
 * page that navigates. /marketplace is the agent catalogue. /market is the
 * markets view on the dashboard. Neither is a redirect.
 */

const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const express = require('express');
const { navigate } = require('../lib/navigate');
const { navigationTarget } = require('./helpers/navigation');

const server = fs.readFileSync(path.join(__dirname, '..', 'server.js'), 'utf8');

test('the old addresses navigate, and the server does not redirect them', () => {
  assert.match(server, /app\.get\('\/marketplace', \(req, res\) => \{ navigate\(res, '\/agents'\); \}\)/);
  assert.match(server, /app\.get\('\/market', \(req, res\) => \{ navigate\(res, '\/dashboard#markets'\); \}\)/);
  assert.doesNotMatch(server, /res\.redirect\([^)]*\/marketplace/);
  assert.doesNotMatch(server, /res\.redirect\([^)]*\/market/);
});

test('a browser is sent to the catalogue and to the markets view', async () => {
  const app = express();
  app.get('/marketplace', (req, res) => navigate(res, '/agents'));
  app.get('/market', (req, res) => navigate(res, '/dashboard#markets'));
  const httpServer = await new Promise((r) => {
    const s = app.listen(0, '127.0.0.1', () => r(s));
  });
  const base = `http://127.0.0.1:${httpServer.address().port}`;
  try {
    assert.strictEqual(await navigationTarget(await fetch(base + '/marketplace', { redirect: 'manual' })), '/agents');
    assert.strictEqual(await navigationTarget(await fetch(base + '/market', { redirect: 'manual' })), '/dashboard#markets');
  } finally {
    httpServer.close();
  }
});
