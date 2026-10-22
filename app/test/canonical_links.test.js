'use strict';
/**
 * SEO hygiene: every indexable public page declares a <link rel="canonical">.
 * The site is reachable on more than one host, so without a canonical tag the
 * duplicate URLs dilute ranking. The canonical must point at the same absolute
 * URL the page's og:url already commits to (when it has one), and match the
 * path the sitemap lists — so the three signals never disagree.
 */
const test = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const path = require('path');
const { STATIC_PATHS } = require('../lib/sitemap');

const PUB = path.join(__dirname, '..', 'public');
// Map each sitemap static path to the file that serves it.
const FILE_FOR = {
  '/': 'index.html', '/agents': 'agents.html', '/leaderboard': 'leaderboard.html',
  '/proof': 'proof.html', '/track': 'track.html', '/letter': 'letter.html',
  '/token': 'token.html',
  '/docs': 'docs.html',
  '/developers': 'developers.html', '/status': 'status.html', '/guardian': 'guardian.html',
  '/intent': 'intent.html', '/firewall': 'firewall.html', '/escape': 'escape.html',
  '/sentinel': 'sentinel.html', '/stress': 'stress.html', '/flight': 'flight.html',
  '/strengthmap': 'strengthmap.html', '/arena': 'arena.html', '/duel': 'duel.html',
  '/learn': 'learn.html', '/command': 'command.html', '/rune': 'rune.html',
  '/gas': 'gas.html', '/approvals': 'approvals.html', '/provable': 'provable.html',
  '/roots': 'roots.html', '/scanner': 'scanner.html',
};
const read = (f) => fs.readFileSync(path.join(PUB, f), 'utf8');
const canonOf = (h) => (h.match(/<link rel="canonical" href="([^"]+)"/) || [])[1];
const ogUrlOf = (h) => (h.match(/property="og:url" content="([^"]+)"/) || [])[1];

test('every sitemap static page maps to a served file', () => {
  for (const s of STATIC_PATHS) {
    assert.ok(FILE_FOR[s.path], 'no file mapped for sitemap path ' + s.path);
  }
});

test('every indexable page declares exactly one canonical link', () => {
  for (const [p, file] of Object.entries(FILE_FOR)) {
    const html = read(file);
    const n = (html.match(/rel="canonical"/g) || []).length;
    assert.equal(n, 1, `${file} should have exactly one canonical (has ${n})`);
  }
});

test('the canonical path matches the sitemap path and the og:url', () => {
  for (const [p, file] of Object.entries(FILE_FOR)) {
    const html = read(file);
    const canon = canonOf(html);
    assert.ok(canon, `${file} has a canonical href`);
    assert.ok(canon.endsWith(p === '/' ? '/' : p), `${file} canonical ends with sitemap path ${p} (got ${canon})`);
    const og = ogUrlOf(html);
    if (og) assert.equal(canon, og, `${file} canonical must equal og:url`);
  }
});

test('canonical URLs are absolute https, not relative', () => {
  for (const file of Object.values(FILE_FOR)) {
    assert.match(canonOf(read(file)), /^https:\/\//, `${file} canonical is absolute https`);
  }
});

test('the Study Room answers at the address people guess for it', async () => {
  // The nav calls /learn "Study Room"; a review typed /study and got a 404.
  // Driven: the alias answers a page that navigates to /learn, not a 3xx,
  // because the live host follows a redirect itself (lib/navigate.js).
  const express = require('express');
  const { navigationTarget } = require('./helpers/navigation');
  const { navigate } = require('../lib/navigate');
  const app = express();
  app.get('/study', (req, res) => { navigate(res, '/learn'); });
  const server = await new Promise((r) => { const s = app.listen(0, '127.0.0.1', () => r(s)); });
  try {
    const r = await fetch(`http://127.0.0.1:${server.address().port}/study`, { redirect: 'manual' });
    assert.strictEqual(r.status, 200);
    assert.strictEqual(await navigationTarget(r), '/learn');
  } finally {
    server.close();
  }
  const { codeOnly } = require('./helpers/code_only');
  const src = codeOnly(fs.readFileSync(path.join(__dirname, '..', 'server.js'), 'utf8'));
  assert.match(src, /app\.get\('\/study', \(req, res\) => \{ navigate\(res, '\/learn'\); \}\);/,
    'the server route is the one driven above');
  assert.ok(fs.existsSync(path.join(PUB, 'learn.html')), 'the alias lands on a page that exists');
});
