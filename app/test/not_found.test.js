'use strict';
/**
 * The terminal 404: what it answers, and — the part that matters — WHERE it is
 * registered.
 *
 * A catch-all `app.use` swallows everything below it. Placed above the routes
 * it returns 404 for the entire site: every page, every endpoint, from one
 * line, and the diff looks identical either way. That is the failure this file
 * exists for; the content assertions are the easy half.
 */

const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

const nf = require('../lib/not_found');
const SERVER = fs.readFileSync(path.join(__dirname, '..', 'server.js'), 'utf8');

/** Whole-line `//` comments dropped: the comment block introducing the handler
 *  names routes and `app.use`, and would otherwise count as registrations. */
const codeOnly = (js) => js.split('\n')
  .filter((ln) => !ln.trimStart().startsWith('//'))
  .join('\n');

// ── what it answers ───────────────────────────────────────────────────

test('an unmatched API path gets JSON, not somebody else\'s HTML', () => {
  const out = nf.notFound('/api/market/overview', 'text/html,*/*');
  assert.equal(out.type, 'json');
  assert.equal(out.status, 404);
  assert.deepEqual(out.body, { error: 'not_found' });
});

test('an unmatched page gets HTML', () => {
  const out = nf.notFound('/definitely-not-a-page', 'text/html,*/*');
  assert.equal(out.type, 'html');
  assert.match(out.body, /RUNECLAW/);
  assert.match(out.body, /noindex/);
});

test('an explicit JSON Accept is honoured outside /api', () => {
  assert.equal(nf.notFound('/whatever', 'application/json').type, 'json');
});

test('*/* is not a request for JSON', () => {
  // curl's default, and what a browser sends after its html preference.
  // Treating it as JSON would hand `curl https://host/typo` a JSON body
  // where a person expects the page.
  assert.equal(nf.notFound('/typo', '*/*').type, 'html');
  assert.equal(nf.notFound('/typo', '').type, 'html');
  assert.equal(nf.notFound('/typo', undefined).type, 'html');
});

// ── what it must never do ─────────────────────────────────────────────

test('the requested path is never echoed into the response', () => {
  const nasty = '/<img src=x onerror=alert(1)>';
  const out = nf.notFound(nasty, 'text/html');
  assert.ok(!out.body.includes('onerror'), 'the path reached the HTML body');
  assert.ok(!out.body.includes('<img'), 'the path reached the HTML body');
  // The body is a constant, so this holds for every input rather than for the
  // ones a test happened to try.
  assert.equal(out.body, nf.HTML_BODY);
});

test('the 404 page carries no inline script', () => {
  // script-src is hash-based with no 'unsafe-inline' (lib/csp.js), so an
  // inline block would silently not execute — a page whose script quietly
  // does not run is worse than one that never had any.
  assert.ok(!/<script/i.test(nf.HTML_BODY));
});

// ── where it is registered ────────────────────────────────────────────

test('the catch-all is registered AFTER every route', () => {
  const code = codeOnly(SERVER);
  const marker = "app.use(require('./lib/not_found').handler)";
  const at = code.indexOf(marker);
  assert.ok(at > 0, 'the catch-all is not registered at all');

  const after = code.slice(at + marker.length);
  const strays = [...after.matchAll(/app\.(get|post|put|delete|use)\(\s*['"]/g)]
    .map((m) => m[0]);
  assert.deepEqual(strays, [], (
    'these routes are registered BELOW the catch-all 404 and can never be '
    + 'reached:\n  ' + strays.join('\n  ')
    + '\nMove them above app.use(require(\'./lib/not_found\').handler).'));
});

test('the error handler still comes last', () => {
  const code = codeOnly(SERVER);
  const nfAt = code.indexOf("app.use(require('./lib/not_found').handler)");
  const errAt = code.indexOf('app.use((err, req, res, next)');
  assert.ok(errAt > nfAt, (
    'the 4-arity error handler must stay below the 404 handler — Express '
    + 'selects it by arity, and a 404 registered after it would still run, '
    + 'but keeping the documented order is what makes the file readable'));
});

test('the placement check reads code, not comments', () => {
  // The comment above the handler names app.use and several routes. If this
  // scan read raw text it would find "routes" below the marker and fail, or
  // worse, find the marker inside prose and pass with no handler registered.
  const fake = codeOnly("// app.get('/ghost', h)\napp.use(require('./lib/not_found').handler)\n");
  assert.ok(!fake.includes("app.get('/ghost'"));
});

// ── the express-facing half ───────────────────────────────────────────
//
// Everything above drives `notFound`, which is pure. `handler` is what
// server.js actually registers, and nothing here touched it — so it could be
// broken in any way and this file would stay green. That is the exact defect
// this repo keeps re-finding (code present, never exercised), reproduced in
// the tests written to prevent it.

function fakeRes() {
  const r = { code: null, sent: null, kind: null, headers: {} };
  r.status = (c) => { r.code = c; return r; };
  r.json = (b) => { r.kind = 'json'; r.sent = b; return r; };
  r.type = (t) => { r.headers['content-type'] = t; return r; };
  r.send = (b) => { r.kind = 'send'; r.sent = b; return r; };
  return r;
}
const fakeReq = (p, accept) => ({ path: p, get: (h) =>
  (String(h).toLowerCase() === 'accept' ? accept : undefined) });

test('handler answers an API path with a JSON body and a 404', () => {
  const res = fakeRes();
  nf.handler(fakeReq('/api/nope', 'text/html,*/*'), res);
  assert.equal(res.code, 404);
  assert.equal(res.kind, 'json');
  assert.deepEqual(res.sent, { error: 'not_found' });
});

test('handler answers a page path with HTML and a 404', () => {
  const res = fakeRes();
  nf.handler(fakeReq('/nope', 'text/html,*/*'), res);
  assert.equal(res.code, 404);
  assert.equal(res.kind, 'send');
  assert.equal(res.headers['content-type'], 'html');
  assert.match(res.sent, /RUNECLAW/);
});

test('handler survives a request with no accept header at all', () => {
  // `req.get` is Express's; a bare object without it must not throw, because
  // throwing here turns a 404 into a 500 through the error handler below it.
  const res = fakeRes();
  nf.handler({ path: '/nope' }, res);
  assert.equal(res.code, 404);
  assert.equal(res.kind, 'send');
});
