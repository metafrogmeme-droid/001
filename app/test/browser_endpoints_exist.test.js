'use strict';
/**
 * Every endpoint the browser fetches is an endpoint the server defines.
 *
 * Third boundary in one sweep, after the credential envelope and the sync
 * channel's field names. The client fetches `/api/…`; `server.js` mounts a
 * router that defines it. No test spans the two — route tests drive the router
 * directly and never construct a URL the way the page does, browser tests run
 * renderers on hand-written fixtures and never fetch at all. A renamed or
 * removed endpoint is a 404 at runtime and green in both suites.
 *
 * What the reader then sees depends on the panel, and the good outcome is an
 * error state. `panel_failure_honesty.test.js` enforces guard-or-omit on every
 * loader, so a 404 should paint "could not read" rather than an empty list —
 * but "the honest rendering of a mistake" is not a reason to keep making it.
 *
 * MEASURED FIRST: 74 client sources, 182 endpoints fetched, 264 route paths,
 * and every fetch resolves. A clean negative, guarded because nothing was
 * holding it there.
 *
 * THREE DRAFTS WERE WRONG BEFORE THIS ONE, and the canaries are what caught
 * each. They are load-bearing, not decoration:
 *
 *   - The first called 72 of 138 endpoints dead, `/api/auth/me` among them,
 *     because `server.js` mounts most routers through a variable
 *     (`app.use('/api/auth', authRouter)`) and the matcher only understood an
 *     inline `require()`.
 *   - The second still missed `/api/auth`, because that one variable comes
 *     from a DESTRUCTURED require: `const { router: authRouter } = …`.
 *
 * Those two are the failure CLAUDE.md names outright — a reachability checker
 * with a blind spot manufactures exactly the accusation it exists to prevent.
 * The third was worse, because it failed the other way:
 *
 *   - The third read only literals containing `/api`, and reported a clean
 *     result over 328 of 334 call sites. The six it could not see are written
 *     `apiCall('/auth/login')` — `index.html` defines `const API='/api'` and
 *     the helper prepends it — so the endpoints silently outside the check
 *     were register, login, forgot-password and wallet verification.
 *
 * A checker that finds nothing accuses the innocent and is caught in a day. A
 * checker that quietly covers 98% reports the same clean result forever. Both
 * canaries below therefore assert what the scan REACHES, not just what it
 * concludes, and the count floors fail rather than narrow.
 *
 * THE REVERSE DIRECTION IS DELIBERATELY NOT ASSERTED. Routes no browser fetches
 * came to 58 of 264, and they are overwhelmingly legitimate: the Python bot
 * pulls `/api/bot/sync/*`, MCP clients and external integrators hit
 * `/api/public/*`, and Farcaster fetches `/api/frame/*`. "No browser calls it"
 * is not "nothing calls it", and a guard that conflated the two would be an
 * accusation generator. Recorded so the measurement is not repeated.
 */

const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

const { codeOnly } = require('./helpers/code_only');

const APP = path.join(__dirname, '..');
const PUB = path.join(APP, 'public');

const read = (p) => codeOnly(fs.readFileSync(p, 'utf8'));

/** Every server-side JS file, comments stripped, keyed by path relative to app/. */
function serverSources() {
  const out = new Map();
  (function walk(dir) {
    for (const e of fs.readdirSync(dir, { withFileTypes: true })) {
      if (e.name === 'node_modules' || e.name === 'test' || e.name === 'public'
          || e.name.startsWith('.')) continue;
      const p = path.join(dir, e.name);
      if (e.isDirectory()) walk(p);
      else if (e.name.endsWith('.js')) out.set(path.relative(APP, p), read(p));
    }
  }(APP));
  return out;
}

/** Every full `/api/...` path the server answers on. */
function routePaths() {
  const files = serverSources();
  const server = files.get('server.js');
  assert.ok(server, 'server.js is gone');

  // Resolve the router variables server.js mounts by name.
  const varToFile = new Map();
  const norm = (f) => (f.endsWith('.js') ? f : `${f}.js`);
  for (const m of server.matchAll(
    /(?:const|let|var)\s+(\w+)\s*=\s*require\(\s*['"]\.\/([\w/.-]+)['"]\s*\)/g)) {
    varToFile.set(m[1], norm(m[2]));
  }
  for (const m of server.matchAll(
    /(?:const|let|var)\s*\{([^}]*)\}\s*=\s*require\(\s*['"]\.\/([\w/.-]+)['"]\s*\)/g)) {
    for (const part of m[1].split(',')) {
      const name = (part.includes(':') ? part.split(':')[1] : part).trim();
      if (name) varToFile.set(name, norm(m[2]));
    }
  }

  const paths = new Set();
  // Endpoints declared straight on the app.
  for (const [, src] of files) {
    for (const m of src.matchAll(
      /app\.(?:get|post|put|delete|patch|all)\(\s*['"](\/api[\w/:.-]*)['"]/g)) {
      paths.add(m[1]);
    }
  }
  // app.use('/api/x', router) — variable or inline require — expanded.
  for (const m of server.matchAll(
    /app\.use\(\s*['"](\/api[\w/-]*)['"]\s*,\s*(?:require\(\s*['"]\.\/([\w/.-]+)['"]\s*\)|(\w+))/g)) {
    const file = m[2] ? norm(m[2]) : varToFile.get(m[3]);
    const src = file && files.get(file);
    if (!src) continue;
    for (const r of src.matchAll(
      /router\.(?:get|post|put|delete|patch|all)\(\s*['"]([^'"]*)['"]/g)) {
      paths.add((m[1] + r[1]).replace(/\/+$/, '') || '/');
    }
  }
  return paths;
}

/** Every `/api/...` literal the client side names, as path -> Set(source). */
function fetchedEndpoints() {
  const sources = [];
  const jsDir = path.join(PUB, 'js');
  for (const f of fs.readdirSync(jsDir)) {
    if (f.endsWith('.js')) sources.push([`js/${f}`, path.join(jsDir, f)]);
  }
  // Pages carry inline <script> too, and 43 endpoints are named ONLY there —
  // the whole arena, allowances and agent-card surfaces. A scan of public/js
  // alone misses every one of them and still reports a clean result.
  (function walk(dir) {
    for (const e of fs.readdirSync(dir, { withFileTypes: true })) {
      if (e.name.startsWith('.')) continue;
      const p = path.join(dir, e.name);
      if (e.isDirectory()) { if (e.name !== 'js') walk(p); }
      else if (e.name.endsWith('.html')) sources.push([path.relative(PUB, p), p]);
    }
  }(PUB));

  const out = new Map();
  const add = (p, name) => {
    const k = p.replace(/\/+$/, '');
    if (!out.has(k)) out.set(k, new Set());
    out.get(k).add(name);
  };
  let viaHelper = 0;
  for (const [name, full] of sources) {
    const src = read(full);
    for (const m of src.matchAll(/['"`](\/api\/[\w/-]+)/g)) {
      // A template literal continues into `${…}`; what is captured is the
      // static prefix, which is matched as a prefix below.
      add(m[1], name);
    }
    // NOT EVERY CALL NAMES /api. index.html defines `const API='/api'` and
    // `apiCall(path)` fetches `API + path`, so its six call sites are written
    // `/auth/login`, `/auth/register`, `/auth/wallet/verify` and so on. The
    // first version of this file could not see any of them — a scan that
    // covers 328 of 334 sites and reports a clean result is the exact defect
    // this repo spends its guards preventing, and the six it missed are the
    // authentication path.
    for (const m of src.matchAll(/\bapiCall\(\s*['"`](\/[\w/-]+)/g)) {
      viaHelper += 1;
      add(`/api${m[1]}`, name);
    }
  }
  return { endpoints: out, sourceCount: sources.length, viaHelper };
}

function resolver(paths) {
  return (fetched) => {
    for (const r of paths) {
      if (r === fetched) return true;
      const rx = new RegExp(`^${r.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
        .replace(/:[^/\\]+/g, '[^/]+')}$`);
      if (rx.test(fetched)) return true;
      // The page built a longer URL from this prefix plus an id; a route one
      // segment deeper covers it.
      if (r.startsWith(`${fetched}/`)) return true;
    }
    return false;
  };
}

test('the route table resolves endpoints mounted every way server.js mounts them', () => {
  const matches = resolver(routePaths());
  // Load-bearing. Two drafts of this file failed here rather than in the
  // verdict, and a checker that silently sees nothing reports perfect health.
  const canaries = [
    ['/api/auth/me', 'mounted via a DESTRUCTURED require — const { router: authRouter }'],
    ['/api/controls/status', 'mounted via a plain router variable'],
    ['/api/version', 'declared directly on the app, not through a router'],
  ];
  for (const [p, how] of canaries) {
    assert.ok(matches(p), `${p} is a real endpoint and was not seen (${how})`);
  }
});

test('the client scan reaches both bundles and inline page scripts', () => {
  const { endpoints, sourceCount, viaHelper } = fetchedEndpoints();
  assert.ok(sourceCount >= 50, `only ${sourceCount} client sources scanned`);
  assert.ok(endpoints.size >= 120, `only ${endpoints.size} endpoints found`);
  assert.ok(endpoints.has('/api/insight'),
    '/api/insight is fetched from a bundle under public/js');
  // Measured, not assumed: 43 endpoints are named ONLY by an inline page
  // script and by nothing under public/js. Dropping the HTML sweep loses all
  // of them, so one of them is the canary.
  assert.ok(endpoints.has('/api/arena/keys'),
    '/api/arena/keys is named only by an inline script in arena.html — if it '
    + 'is missing, this scan is back to reading public/js only, and 43 '
    + 'endpoints go unchecked while it still reports a clean result');
});

test('calls that go through the apiCall helper are resolved, not skipped', () => {
  // The helper writes its paths WITHOUT the /api prefix. Missing it is not a
  // narrower check, it is a check that reports health over code it never read.
  const { endpoints, viaHelper } = fetchedEndpoints();
  assert.ok(viaHelper >= 5,
    `only ${viaHelper} apiCall sites resolved — the helper was renamed or `
    + 'rewritten and its endpoints are now invisible to this file');
  for (const p of ['/api/auth/login', '/api/auth/register']) {
    assert.ok(endpoints.has(p), `${p} is reached only through apiCall()`);
  }
  // The prefix is derived from the page, not assumed: if `API` stops being
  // '/api', every path built above is wrong by exactly that string.
  const index = read(path.join(PUB, 'index.html'));
  assert.match(index, /const API\s*=\s*['"]\/api['"]/,
    "index.html no longer defines API as '/api', so apiCall builds a different "
    + 'URL than this test reconstructs');
});

test('a fetch of an endpoint that does not exist is actually detected', () => {
  // Anti-vacuity: every assertion here is satisfied by an empty route table
  // paired with an empty fetch list.
  const matches = resolver(routePaths());
  assert.ok(!matches('/api/definitely/not/a/route'));
});

test('no page or bundle fetches an endpoint the server does not define', () => {
  const matches = resolver(routePaths());
  const { endpoints } = fetchedEndpoints();
  const missing = [...endpoints.keys()].filter((p) => !matches(p)).sort()
    .map((p) => `${p}  <- ${[...endpoints.get(p)].join(', ')}`);
  assert.deepStrictEqual(missing, [],
    'These are fetched by the client and match no route. Every call is a 404 '
    + 'at runtime, and no test in either suite makes the request:\n  '
    + missing.join('\n  '));
});
