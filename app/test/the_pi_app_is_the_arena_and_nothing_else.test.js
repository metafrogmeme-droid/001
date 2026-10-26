'use strict';
/**
 * The Pi Browser app (GET /pi) is the paper arena and nothing else.
 *
 * Three promises, each held from what the server actually sends:
 *
 *   No token, no presale. The operator's ruling (2026-10-16): presale
 *   information does not go into Pi. Almost every page of the site carries the
 *   presale bar (presale.js), so this page is built in its router from the Mini
 *   App's parts, and nothing it loads may say presale, $RCLAW, Smithii or link
 *   to /token.
 *
 *   Pi's policy, explicit and narrow. The SDK from sdk.minepi.com, loaded in
 *   the head; connect-src 'self' and https://socialchain.app (the SDK's own
 *   authentication call); never a wildcard; frame-ancestors limited to Pi's
 *   hosts. The Platform check runs on our server, so api.minepi.com is NOT in
 *   this page's connect-src. The site's own policy, which other tests pin, is
 *   not touched.
 *
 *   App Studio's validation file is a static file. It must answer at
 *   /pi/pi-app-validation.txt with exactly the token and nothing else, served
 *   by express.static ahead of the /pi router, with no route of its own.
 */
const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const express = require('express');
const { codeOnly } = require('./helpers/code_only');

const APP = path.join(__dirname, '..');
const PUBLIC = path.join(APP, 'public');
const PUBLIC_JS = path.join(PUBLIC, 'js');
const VALIDATION_TOKEN = 'piappstudio-UTmzZbZm68Hqo7a0yTvian5zxjEApYdY';

/** One request. `host` is the Host header; fetch will not set one. */
function httpGet(port, p, headers) {
  return new Promise((resolve, reject) => {
    const req = http.request({
      host: '127.0.0.1', port, path: p, method: 'GET', headers,
    }, (res) => {
      const chunks = [];
      res.on('data', (c) => chunks.push(c));
      res.on('end', () => resolve({
        status: res.statusCode,
        headers: {
          get(name) {
            const v = res.headers[String(name).toLowerCase()];
            if (v == null) return null;
            return Array.isArray(v) ? v.join(', ') : String(v);
          },
        },
        text: Buffer.concat(chunks).toString('utf8'),
      }));
    });
    req.on('error', reject);
    req.end();
  });
}

/**
 * The router behind express.static, in the order server.js mounts them.
 * `opts.host` sets Host. `opts.trustProxy` is Express's trust proxy setting,
 * off unless a test turns it on. `opts.headers` are extra request headers.
 */
async function get(p, env = {}, opts = {}) {
  const saved = {};
  for (const [k, v] of Object.entries(env)) { saved[k] = process.env[k]; process.env[k] = v; }
  const mod = require('../routes/piapp');
  const app = express();
  if (opts.trustProxy != null) app.set('trust proxy', opts.trustProxy);
  app.use(express.static(PUBLIC));
  app.use('/pi', mod);
  app.get('/validation-key.txt', mod.validationKey);
  const server = http.createServer(app);
  await new Promise((r) => server.listen(0, '127.0.0.1', r));
  try {
    if (opts.host || opts.headers || opts.trustProxy != null) {
      const headers = { ...(opts.headers || {}) };
      if (opts.host) headers.host = opts.host;
      return await httpGet(server.address().port, p, headers);
    }
    const r = await fetch(`http://127.0.0.1:${server.address().port}${p}`, { redirect: 'manual' });
    return { status: r.status, headers: r.headers, text: await r.text() };
  } finally {
    server.close();
    for (const [k, v] of Object.entries(saved)) {
      if (v === undefined) delete process.env[k]; else process.env[k] = v;
    }
  }
}

const scripts = (html) => [...html.matchAll(/<script\s+src="([^"]+)"/g)].map((m) => m[1]);
const directive = (csp, name) => (csp.split(';').map((d) => d.trim()).find((d) => d.startsWith(name + ' ')) || '');
const PRESALE = /presale|\$?RCLAW|smithii|\/token\b/i;

test('the head loads api-retry, then Pi\'s SDK, before anything else', async () => {
  const r = await get('/pi/');
  assert.strictEqual(r.status, 200);
  const head = r.text.slice(0, r.text.indexOf('</head>'));
  const inHead = scripts(head);
  assert.match(inHead[0], /^\/js\/api-retry\.js\?v=\d+$/);
  assert.strictEqual(inHead[1], 'https://sdk.minepi.com/pi-sdk.js');
  assert.match(head, /<script src="https:\/\/sdk\.minepi\.com\/pi-sdk\.js"><\/script>/,
    'the SDK must load in the head, not deferred, so window.Pi exists before the arena runs');
  const s = scripts(r.text);
  assert.match(s[s.length - 1], /^\/js\/pi-arena\.js\?v=\d+$/);
  assert.ok(s.some((x) => /\/js\/arena-session\.js\?v=\d+$/.test(x)), 'the shared session is not loaded');
});

test('/pi itself reaches the page', async () => {
  // public/pi/ exists for the validation file, so express.static redirects the
  // bare path to /pi/ before the router; the router answers there.
  const bare = await get('/pi');
  assert.ok(bare.status === 200 || (bare.status === 301 && /\/pi\/$/.test(bare.headers.get('location'))),
    `GET /pi answered ${bare.status}`);
  assert.strictEqual((await get('/pi/')).status, 200);
});

test('Pi\'s default document /pi/index.html is the Arena, and a stray path is not', async () => {
  // Pi Browser and the sandbox load frontend_url plus a path. For this app
  // that document is /pi/index.html (public/pi/ has no index file, so static
  // does not answer it). It must be the Arena, with the Pi policy, including
  // when the sandbox appends ?sandbox=true. A path Pi does not ask for stays
  // unanswered: this is not a catch-all.
  for (const p of ['/pi/index.html', '/pi/index.html?sandbox=true']) {
    const r = await get(p);
    assert.strictEqual(r.status, 200, p);
    assert.strictEqual(r.headers.get('location'), null, `${p} must not redirect`);
    assert.match(r.text, /<title>RUNECLAW Arena<\/title>/);
    assert.strictEqual(r.headers.get('x-frame-options'), null);
    assert.match(r.headers.get('cache-control'), /no-store/);
    assert.match(r.headers.get('content-security-policy'), /frame-ancestors /);
    assert.match(r.text, /src="https:\/\/sdk\.minepi\.com\/pi-sdk\.js"/);
  }
  const stray = await get('/pi/not-a-page');
  assert.notStrictEqual(stray.status, 200);
  assert.doesNotMatch(stray.text, /<title>RUNECLAW Arena<\/title>/);
});

test('nothing the page loads carries the presale or the token', async () => {
  const r = await get('/pi/');
  assert.doesNotMatch(r.text, PRESALE, 'the page itself names the sale');
  const s = scripts(r.text);
  assert.ok(!s.some((x) => /presale|i18n-core|\/app\.js|nav/.test(x)), `a site-wide script rode in: ${s}`);
  for (const src of s.filter((x) => x.startsWith('/js/'))) {
    const file = path.join(PUBLIC_JS, src.replace(/^\/js\//, '').replace(/\?.*$/, ''));
    assert.doesNotMatch(codeOnly(fs.readFileSync(file, 'utf8')), PRESALE, `${src} names the sale or the token`);
  }
});

test('the only pages the app links to are the two policies, which carry no presale', async () => {
  // Every href any script on the page can write. The policies are served by
  // routes/legal.js from the site's build, with no script, no presale bar and
  // no link beyond each other (the_policies_are_served_on_www.test.js holds
  // that); the Pi Developer Portal asks for both, and a reviewer will open them.
  //
  // Named exceptions, each a link a shared module can write that the Pi app
  // never reaches, with the reason; a listed one that is gone fails too.
  const UNREACHED = {
    // The locked-call card links to the home page; pi-signals.js draws locked
    // calls itself, and the smoke test drives one (the_pi_app_signs_in_with_pi).
    'embed-row.js': ['/'],
  };
  const r = await get('/pi/');
  const targets = new Set();
  const seen = {};
  for (const src of scripts(r.text).filter((x) => x.startsWith('/js/'))) {
    const name = src.replace(/^\/js\//, '').replace(/\?.*$/, '');
    for (const m of codeOnly(fs.readFileSync(path.join(PUBLIC_JS, name), 'utf8')).matchAll(/href="(\/[^"' +]*)"/g)) {
      if ((UNREACHED[name] || []).includes(m[1])) { (seen[name] = seen[name] || new Set()).add(m[1]); continue; }
      targets.add(m[1]);
    }
  }
  for (const [name, list] of Object.entries(UNREACHED)) {
    for (const t of list) assert.ok(seen[name] && seen[name].has(t), `${name} no longer writes ${t}; drop the exception`);
  }
  for (const t of targets) {
    assert.ok(t === '/privacy' || t === '/terms' || t.startsWith('/call/'), `the Pi app can link to ${t}`);
  }
  // /call/ is the website's receipt link, which duel.js draws only when asked
  // for links; the Pi block asks for the in-app check instead.
  const duelBlock = codeOnly(fs.readFileSync(path.join(PUBLIC_JS, 'pi-duel.js'), 'utf8'));
  assert.match(duelBlock, /\{ verify: !!window\.RCPiReceipt[,}\s]/, 'the Pi Duel stopped asking for the in-app receipt check');
  assert.doesNotMatch(duelBlock, /links:\s*true/, 'the Pi Duel asks for links to the website');
  assert.ok(targets.has('/privacy') && targets.has('/terms'), 'the policies are not reachable from the app');
});

test('the policy is Pi\'s: explicit sources, no wildcard, no inline script', async () => {
  const csp = (await get('/pi/')).headers.get('content-security-policy');
  assert.strictEqual(directive(csp, 'default-src'), "default-src 'none'");
  assert.strictEqual(directive(csp, 'script-src'), "script-src 'self' https://sdk.minepi.com");
  assert.strictEqual(directive(csp, 'connect-src'), "connect-src 'self' https://socialchain.app");
  assert.strictEqual(directive(csp, 'style-src'), "style-src 'self' 'unsafe-inline'",
    'the SDK injects inline styles; scripts stay strict');
  assert.doesNotMatch(csp, /(^|\s)\*(\s|;|$)/, 'a bare wildcard source');
  assert.doesNotMatch(directive(csp, 'connect-src'), /piappengine|api\.minepi\.com/,
    'the Platform check is server-side; its origin does not belong in the browser policy');
  assert.doesNotMatch(csp, /unsafe-eval/);
});

test('framable by Pi\'s hosts only, uncached, and cookie-free', async () => {
  const r = await get('/pi/');
  assert.strictEqual(r.headers.get('x-frame-options'), null);
  assert.match(r.headers.get('cache-control'), /no-store/);
  assert.strictEqual(r.headers.get('set-cookie'), null);
  assert.strictEqual(directive(r.headers.get('content-security-policy'), 'frame-ancestors'),
    "frame-ancestors 'self' https://*.pinet.com https://*.minepi.com "
    + 'https://appstudio-u7cm9zhmha0ruwv8.piappengine.com');
  const narrowed = await get('/pi/', { PI_FRAME_ANCESTORS: 'https://app.minepi.com' });
  assert.strictEqual(directive(narrowed.headers.get('content-security-policy'), 'frame-ancestors'),
    'frame-ancestors https://app.minepi.com');
});

test('App Studio may frame the page by its exact origin, and sign-in asks the Platform', async () => {
  // App Studio's "Checking Pi Auth" previews the page inside App Studio, and
  // frame-ancestors is checked against every ancestor: without this origin the
  // preview showed net::ERR_BLOCKED_BY_RESPONSE (2026-10-17). Sign-in does
  // not talk to that host: the verifier is the Platform API.
  const { APP_STUDIO } = require('../routes/piapp');
  const { API_BASE } = require('../lib/pi_auth');
  assert.strictEqual(new URL(API_BASE).host, 'api.minepi.com');
  assert.notStrictEqual(new URL(API_BASE).host, new URL(APP_STUDIO).host);
  const fa = directive((await get('/pi/')).headers.get('content-security-policy'), 'frame-ancestors');
  assert.ok(fa.split(/\s+/).includes(APP_STUDIO), fa);
  assert.doesNotMatch(fa, /\*\.piappengine\.com/, 'every app App Studio hosts could frame the page');
  assert.doesNotMatch(directive((await get('/pi/')).headers.get('content-security-policy'), 'connect-src'),
    /api\.minepi\.com/, 'the Platform check is server-side');
});

test('Pi.init is awaited, gets only its version, and no sandbox flag is set', () => {
  const src = codeOnly(fs.readFileSync(path.join(PUBLIC_JS, 'pi-arena.js'), 'utf8'));
  assert.match(src, /Promise\.resolve\(Pi\.init\(\{ version: '2\.0' \}\)\)/);
  assert.doesNotMatch(src, /sandbox/i);
});

test('the router defines GET only', () => {
  const src = codeOnly(fs.readFileSync(path.join(APP, 'routes', 'piapp.js'), 'utf8'));
  assert.doesNotMatch(src, /router\.(post|put|patch|delete)\(/);
  assert.match(src, /router\.use\(rateLimit\(\{[^}]*name:\s*'piapp'/);
});

test('the client sends the access token, never a uid of its own', () => {
  const src = codeOnly(fs.readFileSync(path.join(PUBLIC_JS, 'pi-arena.js'), 'utf8'));
  assert.match(src, /body:\s*\{\s*accessToken:\s*auth\.accessToken\s*\}/);
  assert.doesNotMatch(src, /auth\.user|uid\s*:|username\s*:/, 'the page sends who it says the user is');
  assert.doesNotMatch(src, /localStorage|sessionStorage|document\.cookie/);
});

test('the one payment is a tip whose terms come from the server, settled by id', () => {
  const src = codeOnly(fs.readFileSync(path.join(PUBLIC_JS, 'pi-arena.js'), 'utf8'));
  const calls = [...src.matchAll(/Pi\.createPayment\(/g)];
  assert.strictEqual(calls.length, 1, 'one payment, the tip');
  assert.match(src, /Pi\.createPayment\(\{ amount: order\.amount, memo: order\.memo, metadata: order\.metadata \}/,
    'amount, memo and metadata must be the order the server issued, not the page\'s');
  assert.match(src, /Pi\.authenticate\(\['username', 'payments'\]/);
  // Approval, completion and an interrupted payment are handed to the server
  // as Pi's ids; the payment itself is read back from Pi there.
  assert.match(src, /'\/api\/pi\/payments\/approve', \{ method: 'POST', body: \{ paymentId: paymentId \} \}/);
  assert.match(src, /body: \{ paymentId: paymentId, txid: txid \}/);
  assert.match(src, /'\/api\/pi\/payments\/incomplete', \{ method: 'POST', body: \{ paymentId: id \} \}/);
});

// ── App Studio's validation file ──────────────────────────────────────────

test('the validation file is exactly the token, at /pi/pi-app-validation.txt', async () => {
  const onDisk = fs.readFileSync(path.join(PUBLIC, 'pi', 'pi-app-validation.txt'), 'utf8');
  assert.strictEqual(onDisk, VALIDATION_TOKEN, 'the file carries more than the token');
  const r = await get('/pi/pi-app-validation.txt');
  assert.strictEqual(r.status, 200);
  assert.strictEqual(r.text, VALIDATION_TOKEN);
  assert.match(r.headers.get('content-type'), /^text\/plain/);
});

test('the validation file is static: no route, handler or redirect names it', () => {
  const sources = [path.join(APP, 'server.js'),
    ...fs.readdirSync(path.join(APP, 'routes')).map((f) => path.join(APP, 'routes', f))];
  const naming = sources.filter((f) => /pi-app-validation/.test(codeOnly(fs.readFileSync(f, 'utf8'))));
  assert.deepStrictEqual(naming, []);
  const server = codeOnly(fs.readFileSync(path.join(APP, 'server.js'), 'utf8'));
  assert.ok(server.indexOf('express.static(') < server.indexOf("app.use('/pi', "),
    'the /pi router must not be mounted ahead of the static files');
});

test('the domain key is served only when set, and never as an empty 200', async () => {
  // The Developer Portal's "Verify App Domain" step asks for the key beside
  // the App URL, /pi/validation-key.txt (2026-10-17); the root path stays.
  for (const p of ['/validation-key.txt', '/pi/validation-key.txt']) {
    const unset = await get(p, { PI_VALIDATION_KEY: '', PI_TESTNET_HOST: '', PI_TESTNET_VALIDATION_KEY: '' });
    assert.strictEqual(unset.status, 404, p);
    assert.notStrictEqual(unset.text.trim(), '', `${p}: an unset key answered an empty body`);
    const set = await get(p, { PI_VALIDATION_KEY: '  abc123key  ', PI_TESTNET_HOST: '', PI_TESTNET_VALIDATION_KEY: '' });
    assert.strictEqual(set.status, 200, p);
    assert.strictEqual(set.text, 'abc123key', p);
    assert.match(set.headers.get('content-type'), /^text\/plain/, p);
  }
});

const WWW = 'www.humanoid-traders.com';
const PI_HOST = 'pi.humanoid-traders.com';
const BOTH_KEYS = {
  PI_VALIDATION_KEY: 'www-dummy',
  PI_TESTNET_HOST: PI_HOST,
  PI_TESTNET_VALIDATION_KEY: '  testnet-dummy  ',
};

test('the testnet host is served its own key, and www keeps the mainnet key', async () => {
  for (const p of ['/validation-key.txt', '/pi/validation-key.txt']) {
    const testnet = await get(p, BOTH_KEYS, { host: 'PI.HUMANOID-TRADERS.COM:443' });
    assert.strictEqual(testnet.status, 200, p);
    assert.strictEqual(testnet.text, 'testnet-dummy', p);
    assert.match(testnet.headers.get('content-type'), /^text\/plain/, p);
    assert.match(testnet.headers.get('cache-control'), /no-store/, p);
    assert.match(testnet.headers.get('vary'), /\bHost\b/, p);
    assert.strictEqual(testnet.headers.get('location'), null, p);

    const www = await get(p, BOTH_KEYS, { host: WWW });
    assert.strictEqual(www.status, 200, p);
    assert.strictEqual(www.text, 'www-dummy', p);
    assert.strictEqual(www.headers.get('location'), null, p);

    const neighbour = await get(p, BOTH_KEYS, { host: 'not-pi.humanoid-traders.com' });
    assert.strictEqual(neighbour.text, 'www-dummy', `${p} treated a neighbour as the testnet host`);
  }
});

test('an unset key is a 404, including the testnet key on the testnet host', async () => {
  for (const p of ['/validation-key.txt', '/pi/validation-key.txt']) {
    const neither = await get(p, {
      PI_VALIDATION_KEY: '', PI_TESTNET_HOST: '', PI_TESTNET_VALIDATION_KEY: '',
    }, { host: PI_HOST });
    assert.strictEqual(neither.status, 404, p);
    assert.notStrictEqual(neither.text.trim(), '', p);

    // The testnet host does not fall back to the mainnet key.
    const testnetUnset = await get(p, {
      PI_VALIDATION_KEY: 'www-dummy', PI_TESTNET_HOST: ` ${PI_HOST}:8443 `, PI_TESTNET_VALIDATION_KEY: ' ',
    }, { host: PI_HOST });
    assert.strictEqual(testnetUnset.status, 404, p);
    assert.doesNotMatch(testnetUnset.text, /www-dummy/, p);
    assert.match(testnetUnset.headers.get('cache-control'), /no-store/, p);

    // The new settings unset: today's one key, on every host.
    const legacy = await get(p, {
      PI_VALIDATION_KEY: 'www-dummy', PI_TESTNET_HOST: '', PI_TESTNET_VALIDATION_KEY: 'testnet-dummy',
    }, { host: PI_HOST });
    assert.strictEqual(legacy.status, 200, p);
    assert.strictEqual(legacy.text, 'www-dummy', p);
  }
});

test('the testnet host is not redirected, and a forwarded host is not trusted unless the proxy is', async () => {
  for (const p of ['/validation-key.txt', '/pi/validation-key.txt', '/pi', '/pi/', '/pi/index.html']) {
    const r = await get(p, BOTH_KEYS, { host: PI_HOST });
    const loc = r.headers.get('location');
    assert.ok(r.status < 400, `${p} answered ${r.status}`);
    if (loc) {
      assert.doesNotMatch(loc, /www\.humanoid-traders\.com/i, `${p} location ${loc}`);
      if (/^https?:\/\//i.test(loc)) {
        assert.strictEqual(new URL(loc).hostname, PI_HOST, `${p} left the host: ${loc}`);
      }
    }
  }
  const page = await get('/pi/', BOTH_KEYS, { host: PI_HOST });
  assert.strictEqual(page.status, 200);
  assert.doesNotMatch(page.text, /http-equiv="refresh"/i);

  // trust proxy is off: X-Forwarded-Host cannot pick the testnet key.
  const spoofed = await get('/validation-key.txt', BOTH_KEYS, {
    host: WWW,
    headers: { 'x-forwarded-host': PI_HOST },
  });
  assert.strictEqual(spoofed.status, 200);
  assert.strictEqual(spoofed.text, 'www-dummy');

  // The same header counts when this app already trusts the connecting hop.
  const trusted = await get('/pi/validation-key.txt', BOTH_KEYS, {
    host: 'internal.local',
    headers: { 'x-forwarded-host': PI_HOST },
    trustProxy: true,
  });
  assert.strictEqual(trusted.status, 200);
  assert.strictEqual(trusted.text, 'testnet-dummy');
});

test('the page carries a description, a link-preview image and an icon, and the files it names exist', async () => {
  const r = await get('/pi/');
  const head = r.text.split('</head>')[0];
  assert.match(head, /<meta name="description" content="[^"]{40,}">/);
  assert.match(head, /<meta property="og:image" content="https:\/\/www\.humanoid-traders\.com\/pi_og_1200x630\.jpg">/);
  assert.match(head, /<meta property="og:image:width" content="1200">[\s\S]*<meta property="og:image:height" content="630">/);
  assert.match(head, /<link rel="icon" type="image\/png" sizes="200x200" href="\/pi_icon_200\.png">/);
  assert.match(head, /<link rel="apple-touch-icon" sizes="180x180" href="\/pi_touch_180\.png">/);
  assert.doesNotMatch(head, PRESALE, 'the preview names the sale');
  assert.doesNotMatch(head, /<a\s/, 'a door in the head');
  for (const f of ['pi_og_1200x630.jpg', 'pi_icon_200.png', 'pi_touch_180.png']) {
    assert.ok(fs.existsSync(path.join(PUBLIC, f)), `${f} is not in public/`);
  }
  // The JPEG's own frame header says what the tags claim.
  const jpg = fs.readFileSync(path.join(PUBLIC, 'pi_og_1200x630.jpg'));
  let i = 2, size = null;
  while (i < jpg.length - 9) {
    if (jpg[i] !== 0xFF) { i++; continue; }
    const marker = jpg[i + 1];
    if (marker === 0xC0 || marker === 0xC2) { size = { h: jpg.readUInt16BE(i + 5), w: jpg.readUInt16BE(i + 7) }; break; }
    if (marker === 0xD8 || marker === 0x01 || (marker >= 0xD0 && marker <= 0xD7)) { i += 2; continue; }
    i += 2 + jpg.readUInt16BE(i + 2);
  }
  assert.deepEqual(size, { w: 1200, h: 630 });
  const png = fs.readFileSync(path.join(PUBLIC, 'pi_icon_200.png'));
  assert.deepEqual([png.readUInt32BE(16), png.readUInt32BE(20)], [200, 200]);
});
