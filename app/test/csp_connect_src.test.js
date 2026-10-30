'use strict';
/**
 * Site-wide connect-src is an explicit list, and the list is what the pages
 * actually open.
 *
 * 'self' is every /api call and the event stream. blob: is the owl viewer's
 * GLTF image fetch. telegram.org is a script and an iframe, not a connection.
 * Pi's SDK is only on /pi, which replaces this header. The bot gateway is
 * called by the server, not by the browser.
 */

const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

const csp = require('../lib/csp');
const ROOT = path.join(__dirname, '..', '..');

test('the site-wide connect-src is self and blob, and nothing else', () => {
  assert.strictEqual(csp.connectSrc(), "'self' blob:");
  assert.ok(!csp.connectSrc().includes('*'));
  assert.ok(!/https?:/.test(csp.connectSrc()));
});

test('the Express policy uses that list', () => {
  const server = fs.readFileSync(path.join(__dirname, '..', 'server.js'), 'utf8');
  assert.match(server, /connect-src \$\{require\('\.\/lib\/csp'\)\.connectSrc\(\)\}/);
});

test('the nginx policy names connect-src and does not open a third-party origin', () => {
  const snippet = fs.readFileSync(
    path.join(ROOT, 'nginx', 'snippets', 'security-headers.conf'), 'utf8');
  const header = snippet.split('\n').filter((l) => l.includes('Content-Security-Policy'))
    .pop();
  assert.ok(header, 'nginx CSP header missing');
  const directive = header.split(';').map((d) => d.trim()).find((d) => d.startsWith('connect-src'));
  assert.ok(directive, 'nginx CSP has no explicit connect-src');
  assert.match(directive, /^connect-src 'self'/);
  assert.ok(!directive.includes('*'));
  assert.ok(!/https?:/.test(directive));
});
