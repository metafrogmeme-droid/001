'use strict';
/**
 * security.txt — an invitation to report, which is a promise like any other.
 *
 * Cloudflare's security scan flagged it missing. The file itself is trivial;
 * the two ways it goes wrong are not:
 *
 *   It 404s despite existing. `express.static` ignores dotfiles, so a file
 *   dropped into public/.well-known/ is never served. /.well-known/
 *   assetlinks.json is an explicit route for exactly this reason, and this
 *   follows it. Nothing about the file's presence in the repo would reveal the
 *   mistake — only a request does.
 *
 *   It expires. `Expires` is mandatory under RFC 9116, and a hardcoded date
 *   reliably goes stale. An expired security.txt reads as an abandoned
 *   project, which is worse than not publishing one; it is the single most
 *   common failure of this file in the wild. Rolling it forward per request
 *   means it cannot rot.
 *
 * And the contact has to reach somebody. Publishing an address nobody reads is
 * the same broken promise as a dead link, so an empty SECURITY_CONTACT serves
 * nothing at all rather than an invitation into a void.
 */
const test = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const path = require('path');

const server = fs.readFileSync(path.join(__dirname, '..', 'server.js'), 'utf8');

function routeBody() {
  const i = server.indexOf("app.get('/.well-known/security.txt'");
  assert.ok(i > 0, 'security.txt route not found');
  return server.slice(i, server.indexOf("app.get('/.well-known/assetlinks.json'", i));
}

test('it is a route, not a file express.static would refuse to serve', () => {
  assert.match(server, /app\.get\('\/\.well-known\/security\.txt'/);
  assert.ok(!fs.existsSync(path.join(__dirname, '..', 'public', '.well-known', 'security.txt')),
    'a file here would never be served — express.static ignores dotfiles');
});

test('it carries both mandatory RFC 9116 fields', () => {
  const body = routeBody();
  assert.match(body, /Contact: /);
  assert.match(body, /Expires: /);
});

test('the expiry is computed, never a date that can go stale', () => {
  const body = routeBody();
  assert.match(body, /Date\.now\(\) \+ \d+ \* 24 \* 3600 \* 1000/,
    'a literal date in this file is the most common way security.txt fails');
  assert.doesNotMatch(body, /Expires: 20\d\d-/, 'hardcoded expiry');
});

test('the computed expiry is in the future and RFC-shaped', () => {
  const expires = new Date(Date.now() + 90 * 24 * 3600 * 1000)
    .toISOString().replace(/\.\d{3}Z$/, 'Z');
  assert.ok(new Date(expires).getTime() > Date.now());
  assert.match(expires, /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$/);
});

test('an unset contact serves nothing rather than an address that bounces', () => {
  const body = routeBody();
  assert.match(body, /if \(!contact\) return res\.status\(404\)/,
    'an invitation nobody answers is a broken promise, not a courtesy');
});

test('the contact is overridable without a code change', () => {
  assert.match(routeBody(), /process\.env\.SECURITY_CONTACT/);
});

test('canonical uses the resolved public origin, not a hardcoded host', () => {
  const body = routeBody();
  assert.match(body, /public_origin/,
    'the canonical URL is consumed outside this server, like every other one');
});

test('it is served as plain text', () => {
  assert.match(routeBody(), /res\.type\('text\/plain'\)/,
    'scanners and humans both expect text/plain; HTML makes it unparseable');
});
