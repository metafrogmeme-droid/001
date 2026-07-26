'use strict';
// A phone-link QR is scanned by a DIFFERENT device on a DIFFERENT network.
// The URL inside it therefore has to be the PUBLIC one.
//
// req.get('host') is not that. Behind a serverless/VPC front end it is whatever
// internal hop reached the app. In production this produced
//   http://page-…-vpc.fcapp.run/wallet-link?code=…
// The desktop rendered a perfectly normal-looking QR; the phone opened it and
// got "connection is not secure" against an internal hostname it could never
// reach. The flow was impossible to complete and nothing said why — the failure
// was invisible from the side generating it.
//
// A QR pointing nowhere is worse than no QR, so the endpoint now refuses to
// issue one rather than burn a single-use code against an unreachable origin.

const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

const auth = fs.readFileSync(path.join(__dirname, '..', 'auth.js'), 'utf8');
const dash = fs.readFileSync(path.join(__dirname, '..', 'public', 'js', 'dashboard.js'), 'utf8');

// Re-create the resolver against the source, so the test exercises the real
// regex and branching rather than a paraphrase of it.
function makeResolver(env) {
  const src = auth.slice(auth.indexOf('const _PRIVATE_HOST'), auth.indexOf('router.post(\'/wallet/link-code\''));
  const fn = new Function('process', `${src}; return publicOrigin;`);
  return fn({ env });
}
const reqWith = (headers, protocol = 'http') => ({
  get: (h) => headers[h.toLowerCase()],
  protocol,
});

test('an internal VPC host is refused, not silently encoded', () => {
  const resolve = makeResolver({});
  const r = resolve(reqWith({ host: 'page-pmvcg-mule-fuhmdocafd.ap-southeast-1-vpc.fcapp.run' }));
  assert.ok(r.error, 'the exact production host was accepted as public');
  assert.match(r.error, /PUBLIC_ORIGIN/, 'the error must name the fix');
});

test('other unreachable shapes are refused too', () => {
  const resolve = makeResolver({});
  for (const host of ['svc.internal', 'box.local', '10.0.0.4:8080', '192.168.1.9', '172.20.3.4']) {
    assert.ok(resolve(reqWith({ host })).error, `${host} was treated as publicly reachable`);
  }
});

test('PUBLIC_ORIGIN wins outright and loses a trailing slash', () => {
  const resolve = makeResolver({ PUBLIC_ORIGIN: 'https://pmvc58g2.mule.page/' });
  const r = resolve(reqWith({ host: 'page-x.vpc.fcapp.run' }));
  assert.equal(r.origin, 'https://pmvc58g2.mule.page');
  assert.ok(!r.error, 'an explicitly configured origin must never be second-guessed');
});

test('the edge proxy’s host beats the internal one', () => {
  const resolve = makeResolver({});
  const r = resolve(reqWith({
    host: 'page-x.vpc.fcapp.run',
    'x-forwarded-host': 'pmvc58g2.mule.page',
  }));
  assert.equal(r.origin, 'https://pmvc58g2.mule.page');
});

test('a public host is always https, never the internal http hop', () => {
  // This is the half trust-proxy did not fix: the internal hop really was http,
  // and a QR carrying http:// to a phone is flagged insecure by the browser.
  const resolve = makeResolver({});
  assert.equal(resolve(reqWith({ host: 'pmvc58g2.mule.page' }, 'http')).origin,
    'https://pmvc58g2.mule.page');
});

test('localhost still works for development', () => {
  const resolve = makeResolver({});
  assert.equal(resolve(reqWith({ host: 'localhost:3000' }, 'http')).origin, 'http://localhost:3000');
  assert.equal(resolve(reqWith({ host: '127.0.0.1:3000' }, 'http')).origin, 'http://127.0.0.1:3000');
});

test('no code is burned when the origin is unusable', () => {
  // A single-use code issued against an unreachable origin is a code the user
  // can never redeem — the resolve must happen BEFORE putCode.
  const body = auth.slice(auth.indexOf("router.post('/wallet/link-code'"), auth.indexOf('res.json({ code, url, svg'));
  assert.ok(body.indexOf('publicOrigin(req)') < body.indexOf('putCode'),
    'the origin is resolved after the code is stored — a failure would waste a code');
  assert.match(body, /if \(po\.error\) return res\.status\(503\)/);
});

test('the panel shows the URL the QR encodes', () => {
  // The whole failure was invisible from the desktop: the QR looked fine and
  // nothing said where it pointed. Printing the URL makes it self-diagnosing.
  assert.match(dash, /\$\{esc\(r\.data\.url \|\| ''\)\}/,
    'the QR still hides the address it encodes');
});
