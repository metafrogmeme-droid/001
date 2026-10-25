'use strict';
/**
 * The Pi Testnet app behind the owner's Cloudflare Worker.
 *
 * The Worker fetches www, so Host is www. The request is the Testnet app
 * only when X-Runeclaw-Pi-Proxy matches PI_TESTNET_PROXY_SECRET. A missing,
 * wrong or too-short secret leaves Mainnet behavior. X-Forwarded-Host is
 * not that signal. The secret is not written to a log.
 */
process.env.JWT_SECRET = process.env.JWT_SECRET || 'j'.repeat(64);

const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const http = require('node:http');
const path = require('node:path');
const { pathToFileURL } = require('node:url');
const express = require('express');
const { codeOnly } = require('./helpers/code_only');

const host = require('../lib/pi_host');
const pay = require('../lib/pi_payments');
const { setSession } = require('../lib/session_cookie');

const WWW = 'www.humanoid-traders.com';
const PI = 'pi.humanoid-traders.com';
const SECRET = 'correct-proxy-secret-for-the-pi-testnet-worker-ok';
const WRONG = 'correct-proxy-secret-for-the-pi-testnet-worker-no';
const SHORT = 'too-short-to-be-a-proxy-secret';
const PI_ORIGIN = `https://${PI}`;

const KEYS = {
  PI_VALIDATION_KEY: 'www-dummy',
  PI_TESTNET_HOST: PI,
  PI_TESTNET_VALIDATION_KEY: 'testnet-dummy',
  PI_TESTNET_PROXY_SECRET: SECRET,
  PI_NETWORK_API_KEY: 'main-api-dummy',
  PI_TESTNET_API_KEY: 'testnet-api-dummy',
};

function reqOf(hostname, headers) {
  return { hostname, headers: headers || {} };
}

test('the proxy secret selects the Testnet app, and a bad secret does not', () => {
  assert.strictEqual(host.proxyAuthenticates(reqOf(WWW, { 'x-runeclaw-pi-proxy': SECRET }), KEYS), true);
  assert.strictEqual(host.isTestnetHost(reqOf(WWW, { 'x-runeclaw-pi-proxy': SECRET }), KEYS), true);
  assert.strictEqual(host.isTestnetHost(reqOf(WWW, { 'x-runeclaw-pi-proxy': WRONG }), KEYS), false);
  assert.strictEqual(host.isTestnetHost(reqOf(WWW, {}), KEYS), false);
  assert.strictEqual(host.isTestnetHost(reqOf(WWW, { 'x-runeclaw-pi-proxy': SECRET }), { ...KEYS, PI_TESTNET_PROXY_SECRET: '' }), false);
  assert.strictEqual(host.isTestnetHost(reqOf(WWW, { 'x-runeclaw-pi-proxy': SHORT }), { ...KEYS, PI_TESTNET_PROXY_SECRET: SHORT }), false);
  assert.strictEqual(SHORT.length < host.MIN_PROXY_SECRET, true);
  // A repeated header is not one value. It must not match by accident.
  assert.strictEqual(host.proxyAuthenticates(reqOf(WWW, { 'x-runeclaw-pi-proxy': `${SECRET}, ${SECRET}` }), KEYS), false);
  assert.strictEqual(host.proxyAuthenticates(reqOf(WWW, { 'x-runeclaw-pi-proxy': [SECRET] }), KEYS), false);
  // The direct hostname still counts, with or without the header.
  assert.strictEqual(host.isTestnetHost(reqOf(PI, {}), KEYS), true);
  assert.strictEqual(host.isTestnetHost(reqOf('PI.HUMANOID-TRADERS.COM', {}), KEYS), true);
});

test('X-Forwarded-Host is not the proxy signal', () => {
  const spoofed = reqOf(WWW, { 'x-forwarded-host': PI, 'x-forwarded-proto': 'https' });
  assert.strictEqual(host.isTestnetHost(spoofed, KEYS), false);
  assert.strictEqual(host.isTestnetHost(reqOf(WWW, {
    'x-forwarded-host': PI,
    'x-runeclaw-pi-proxy': WRONG,
  }), KEYS), false);
  // The secret is enough on its own. A contradictory forwarded host is ignored.
  assert.strictEqual(host.isTestnetHost(reqOf(WWW, {
    'x-forwarded-host': 'evil.example',
    'x-runeclaw-pi-proxy': SECRET,
  }), KEYS), true);
});

test('keys do not fall across the two apps', () => {
  const onProxy = reqOf(WWW, { 'x-runeclaw-pi-proxy': SECRET });
  const onWww = reqOf(WWW, {});
  assert.strictEqual(pay.apiKey(KEYS, onProxy), 'testnet-api-dummy');
  assert.strictEqual(pay.apiKey(KEYS, onWww), 'main-api-dummy');
  assert.strictEqual(pay.apiKey(KEYS, reqOf(WWW, { 'x-runeclaw-pi-proxy': WRONG })), 'main-api-dummy');
  assert.strictEqual(pay.apiKey({ ...KEYS, PI_TESTNET_PROXY_SECRET: SHORT }, reqOf(WWW, { 'x-runeclaw-pi-proxy': SHORT })), 'main-api-dummy');
  // The Testnet app does not read the Mainnet validation key, and www does
  // not read the Testnet one. apiKey's unset-Testnet-key fallback is the
  // existing payment rule and is not a swap of the validation keys.
  assert.strictEqual(pay.apiKey({ ...KEYS, PI_TESTNET_API_KEY: '' }, onProxy), 'main-api-dummy');
  assert.strictEqual(pay.apiKey({ ...KEYS, PI_NETWORK_API_KEY: '' }, onWww), null);
  assert.notStrictEqual(pay.apiKey({ ...KEYS, PI_NETWORK_API_KEY: '' }, onWww), 'testnet-api-dummy');
});

test('the Testnet origin is accepted only for an authenticated proxy request', () => {
  const piOrigin = { origin: PI_ORIGIN };
  assert.strictEqual(host.paymentOriginAllowed(reqOf(WWW, { ...piOrigin, 'x-runeclaw-pi-proxy': SECRET }), KEYS), true);
  assert.strictEqual(host.paymentOriginAllowed(reqOf(WWW, piOrigin), KEYS), false);
  assert.strictEqual(host.paymentOriginAllowed(reqOf(WWW, { ...piOrigin, 'x-runeclaw-pi-proxy': WRONG }), KEYS), false);
  assert.strictEqual(host.paymentOriginAllowed(reqOf(WWW, { ...piOrigin, 'x-runeclaw-pi-proxy': SHORT }), { ...KEYS, PI_TESTNET_PROXY_SECRET: SHORT }), false);
  // www talking to www is the Mainnet app, secret or not.
  assert.strictEqual(host.paymentOriginAllowed(reqOf(WWW, { origin: `https://${WWW}` }), KEYS), true);
  assert.strictEqual(host.paymentOriginAllowed(reqOf(WWW, { origin: `https://${WWW}`, 'x-runeclaw-pi-proxy': SECRET }), KEYS), true);
  // The Testnet host reached directly already matches its origin.
  assert.strictEqual(host.paymentOriginAllowed(reqOf(PI, piOrigin), KEYS), true);
  assert.strictEqual(host.paymentOriginAllowed(reqOf(WWW, { origin: 'https://evil.example', 'x-runeclaw-pi-proxy': SECRET }), KEYS), false);
  assert.strictEqual(host.paymentOriginAllowed(reqOf(WWW, {}), KEYS), true);
  assert.strictEqual(host.paymentOriginAllowed(reqOf('localhost', { origin: 'http://localhost:3000' }), KEYS), true);
  assert.strictEqual(host.paymentOriginAllowed(reqOf(WWW, { origin: 'http://www.humanoid-traders.com' }), KEYS), false);
});

function httpCall(port, method, p, headers, body) {
  return new Promise((resolve, reject) => {
    const req = http.request({
      host: '127.0.0.1', port, path: p, method, headers,
    }, (res) => {
      const chunks = [];
      res.on('data', (c) => chunks.push(c));
      res.on('end', () => resolve({
        status: res.statusCode,
        headers: res.headers,
        text: Buffer.concat(chunks).toString('utf8'),
      }));
    });
    req.on('error', reject);
    if (body) req.end(body);
    else req.end();
  });
}

async function withPiApp(env, fn) {
  const saved = {};
  for (const [k, v] of Object.entries(env)) {
    saved[k] = process.env[k];
    if (v === undefined) delete process.env[k];
    else process.env[k] = v;
  }
  const mod = require('../routes/piapp');
  const app = express();
  app.use(express.static(path.join(__dirname, '..', 'public')));
  app.use('/pi', mod);
  app.get('/validation-key.txt', mod.validationKey);
  const server = http.createServer(app);
  await new Promise((r) => server.listen(0, '127.0.0.1', r));
  try {
    return await fn(server.address().port);
  } finally {
    server.close();
    for (const [k, v] of Object.entries(saved)) {
      if (v === undefined) delete process.env[k];
      else process.env[k] = v;
    }
  }
}

function captureConsole(fn) {
  const logs = [];
  const names = ['log', 'info', 'warn', 'error', 'debug'];
  const prev = {};
  for (const name of names) {
    prev[name] = console[name];
    console[name] = (...args) => {
      logs.push(args.map((a) => (typeof a === 'string' ? a : String(a))).join(' '));
    };
  }
  const done = () => {
    for (const name of names) console[name] = prev[name];
    return logs.join('\n');
  };
  try {
    const out = fn();
    if (out && typeof out.then === 'function') {
      return out.then((v) => ({ value: v, logs: done() }), (e) => { done(); throw e; });
    }
    return { value: out, logs: done() };
  } catch (e) {
    done();
    throw e;
  }
}

test('the validation file follows the proxy secret and not a spoofed host', async () => {
  await withPiApp(KEYS, async (port) => {
    const headers = (extra) => ({ host: WWW, ...extra });
    const hit = (extra) => httpCall(port, 'GET', '/validation-key.txt', headers(extra));
    const logged = await captureConsole(() => Promise.all([
      hit({ 'x-runeclaw-pi-proxy': SECRET }),
      hit({ 'x-runeclaw-pi-proxy': WRONG }),
      hit({}),
      hit({ 'x-runeclaw-pi-proxy': SHORT }),
      hit({ 'x-forwarded-host': PI }),
      hit({ 'x-forwarded-host': PI, 'x-runeclaw-pi-proxy': WRONG }),
    ]));
    const [ok, wrong, missing, short, forwarded, forwardedWrong] = logged.value;
    assert.strictEqual(ok.status, 200);
    assert.strictEqual(ok.text, 'testnet-dummy');
    assert.match(ok.headers['cache-control'], /no-store/);
    assert.match(ok.headers.vary, /\bHost\b/);
    assert.match(ok.headers.vary, /X-Runeclaw-Pi-Proxy/);
    assert.ok(!ok.headers.vary.includes(SECRET));
    assert.strictEqual(wrong.text, 'www-dummy');
    assert.strictEqual(missing.text, 'www-dummy');
    assert.strictEqual(short.text, 'www-dummy');
    assert.strictEqual(forwarded.text, 'www-dummy');
    assert.strictEqual(forwardedWrong.text, 'www-dummy');
    assert.ok(!logged.logs.includes(SECRET), 'the proxy secret was written to a log');
    assert.ok(!logged.logs.includes(WRONG), 'a presented proxy header was written to a log');
    assert.ok(!ok.text.includes(SECRET));
  });

  await withPiApp({ ...KEYS, PI_TESTNET_PROXY_SECRET: SHORT }, async (port) => {
    const r = await httpCall(port, 'GET', '/pi/validation-key.txt', {
      host: WWW, 'x-runeclaw-pi-proxy': SHORT,
    });
    assert.strictEqual(r.text, 'www-dummy');
  });

  // An unset Testnet validation key is a 404, not the Mainnet key, including
  // through the proxy.
  await withPiApp({ ...KEYS, PI_TESTNET_VALIDATION_KEY: '' }, async (port) => {
    const r = await httpCall(port, 'GET', '/validation-key.txt', {
      host: WWW, 'x-runeclaw-pi-proxy': SECRET,
    });
    assert.strictEqual(r.status, 404);
    assert.doesNotMatch(r.text, /www-dummy/);
  });
});

test('a proxied Testnet page names its own origin and still loads the Pi SDK', async () => {
  const arena = codeOnly(fs.readFileSync(path.join(__dirname, '..', 'public', 'js', 'pi-arena.js'), 'utf8'));
  assert.match(arena, /Pi\.init\(\{\s*version:\s*'2\.0'\s*\}\)/);
  assert.doesNotMatch(arena, /sandbox:\s*true/);

  await withPiApp(KEYS, async (port) => {
    const proxied = await httpCall(port, 'GET', '/pi/', {
      host: WWW, 'x-runeclaw-pi-proxy': SECRET,
    });
    assert.strictEqual(proxied.status, 200);
    const head = proxied.text.split('</head>')[0];
    assert.match(head, new RegExp(`<link rel="canonical" href="${PI_ORIGIN}/pi">`));
    assert.match(head, new RegExp(`<meta property="og:url" content="${PI_ORIGIN}/pi">`));
    assert.match(head, new RegExp(`<meta property="og:image" content="${PI_ORIGIN}/pi_og_1200x630.jpg">`));
    assert.match(head, /<script src="https:\/\/sdk\.minepi\.com\/pi-sdk\.js"><\/script>/);
    assert.doesNotMatch(head, /sandbox=true/);
    const csp = proxied.headers['content-security-policy'];
    assert.match(csp, /connect-src 'self' https:\/\/socialchain\.app/);
    assert.match(csp, /script-src 'self' https:\/\/sdk\.minepi\.com/);
    assert.doesNotMatch(csp, /www\.humanoid-traders\.com/);

    const www = await httpCall(port, 'GET', '/pi/', { host: WWW });
    const wwwHead = www.text.split('</head>')[0];
    assert.match(wwwHead, /<link rel="canonical" href="https:\/\/www\.humanoid-traders\.com\/pi">/);
    assert.match(wwwHead, /<meta property="og:image" content="https:\/\/www\.humanoid-traders\.com\/pi_og_1200x630\.jpg">/);
    assert.doesNotMatch(wwwHead, new RegExp(PI_ORIGIN));
  });
});

test('payment approve and complete accept the Testnet origin only through the proxy', async () => {
  delete process.env.DATABASE_URL;
  const router = require('../routes/pi_payments');
  const prev = router._deps;
  router._deps = {
    env: KEYS,
    apiKey: 'injected',
    fetch: async () => { throw new Error('upstream was called'); },
  };
  const app = express();
  app.use(express.json());
  app.use('/api/pi/payments', router);
  const server = http.createServer(app);
  await new Promise((r) => server.listen(0, '127.0.0.1', r));
  const port = server.address().port;
  try {
    async function post(p, headers) {
      return httpCall(port, 'POST', p, {
        host: WWW,
        'content-type': 'application/json',
        ...headers,
      }, '{}');
    }
    for (const p of ['/api/pi/payments/approve', '/api/pi/payments/complete', '/api/pi/payments/incomplete']) {
      const refused = await post(p, { origin: PI_ORIGIN });
      assert.strictEqual(refused.status, 403, p);
      assert.strictEqual(JSON.parse(refused.text).error, 'origin_not_allowed');
      assert.ok(!refused.text.includes(SECRET), p);
      const allowed = await post(p, { origin: PI_ORIGIN, 'x-runeclaw-pi-proxy': SECRET });
      assert.strictEqual(allowed.status, 401, p);
      const mainnet = await post(p, { origin: `https://${WWW}` });
      assert.strictEqual(mainnet.status, 401, p);
      const stranger = await post(p, { origin: 'https://evil.example', 'x-runeclaw-pi-proxy': SECRET });
      assert.strictEqual(stranger.status, 403, p);
    }
  } finally {
    server.close();
    router._deps = prev;
  }
});

test('the session cookie is not pinned to www', () => {
  const headers = {};
  const res = {
    getHeader(name) { return headers[name]; },
    setHeader(name, value) { headers[name] = value; },
  };
  setSession({ secure: true, headers: {} }, res, 'tok');
  const all = [].concat(headers['Set-Cookie']).join('\n');
  assert.doesNotMatch(all, /domain=/i);
  assert.doesNotMatch(all, /www\.humanoid-traders\.com/i);
  assert.match(all, /Path=\//);
  assert.match(all, /SameSite=Lax/);
});

test('the host module and the worker do not log', () => {
  const src = codeOnly(fs.readFileSync(path.join(__dirname, '..', 'lib', 'pi_host.js'), 'utf8'));
  assert.doesNotMatch(src, /console\./);
  const worker = codeOnly(fs.readFileSync(path.join(__dirname, '..', '..', 'scripts', 'cloudflare', 'pi-testnet-worker.js'), 'utf8'));
  assert.doesNotMatch(worker, /console\./);
});

test('the worker proxies the Testnet app and rewrites www back to pi', async () => {
  const worker = await import(pathToFileURL(path.join(
    __dirname, '..', '..', 'scripts', 'cloudflare', 'pi-testnet-worker.js')).href);
  assert.strictEqual(worker.shouldProxy('/pi'), true);
  assert.strictEqual(worker.shouldProxy('/pi/validation-key.txt'), true);
  assert.strictEqual(worker.shouldProxy('/validation-key.txt'), true);
  assert.strictEqual(worker.shouldProxy('/api/pi/payments/approve'), true);
  assert.strictEqual(worker.shouldProxy('/api/pi/payments/complete'), true);
  assert.strictEqual(worker.shouldProxy('/api/arena/account'), true);
  assert.strictEqual(worker.shouldProxy('/js/pi-arena.js'), true);
  assert.strictEqual(worker.shouldProxy('/embed.css'), true);
  assert.strictEqual(worker.shouldProxy('/api/pioneer'), false);
  assert.strictEqual(worker.shouldProxy('/'), false);
  assert.strictEqual(worker.shouldProxy('/token'), false);

  const seen = [];
  const fetchImpl = async (url, init) => {
    seen.push({ url: String(url), init });
    return new Response('proxied', {
      status: 302,
      headers: {
        location: 'https://www.humanoid-traders.com/pi/?lang=en',
        'set-cookie': 'rc_jwt=abc; Path=/; Domain=www.humanoid-traders.com; Secure; HttpOnly',
      },
    });
  };
  const res = await worker.handle(new Request(`https://${PI}/pi/validation-key.txt?x=1`, {
    method: 'POST',
    headers: {
      origin: PI_ORIGIN,
      'content-type': 'application/json',
      'x-runeclaw-pi-proxy': 'sent-by-the-browser',
      'x-forwarded-host': 'evil.example',
      cookie: 'rc_auth=1',
    },
    body: '{"amount":1}',
    duplex: 'half',
  }), { PI_PROXY_SECRET: SECRET }, fetchImpl);

  assert.strictEqual(seen.length, 1);
  assert.strictEqual(seen[0].url, `https://${WWW}/pi/validation-key.txt?x=1`);
  assert.strictEqual(seen[0].init.method, 'POST');
  assert.strictEqual(await new Response(seen[0].init.body).text(), '{"amount":1}');
  const sent = seen[0].init.headers;
  assert.strictEqual(sent.get('x-runeclaw-pi-proxy'), SECRET);
  assert.notStrictEqual(sent.get('x-runeclaw-pi-proxy'), 'sent-by-the-browser');
  assert.strictEqual(sent.get('x-forwarded-host'), null);
  assert.strictEqual(sent.get('origin'), PI_ORIGIN);
  assert.strictEqual(sent.get('cookie'), 'rc_auth=1');
  assert.strictEqual(sent.get('content-type'), 'application/json');
  assert.strictEqual(res.status, 302);
  assert.strictEqual(res.headers.get('location'), `https://${PI}/pi/?lang=en`);
  const cookie = res.headers.getSetCookie()[0];
  assert.match(cookie, /rc_jwt=abc/);
  assert.doesNotMatch(cookie, /domain=/i);
  assert.doesNotMatch(cookie, /www\.humanoid-traders\.com/i);
  assert.match(cookie, /Secure/);

  seen.length = 0;
  const elsewhere = await worker.handle(new Request(`https://${PI}/token`), { PI_PROXY_SECRET: SECRET }, fetchImpl);
  assert.strictEqual(seen.length, 0);
  assert.strictEqual(elsewhere.status, 302);
  assert.strictEqual(elsewhere.headers.get('location'), `https://${WWW}/token`);

  const posted = await worker.handle(new Request(`https://${PI}/token`, { method: 'POST' }), { PI_PROXY_SECRET: SECRET }, fetchImpl);
  assert.strictEqual(posted.status, 404);
  assert.strictEqual(seen.length, 0);

  const closed = await worker.handle(new Request(`https://${PI}/pi/`), { PI_PROXY_SECRET: SHORT }, fetchImpl);
  assert.strictEqual(closed.status, 503);
  assert.strictEqual(seen.length, 0);
  const unset = await worker.handle(new Request(`https://${PI}/validation-key.txt`), {}, fetchImpl);
  assert.strictEqual(unset.status, 503);

  const otherHost = await worker.handle(new Request('https://workers.example/pi'), { PI_PROXY_SECRET: SECRET }, fetchImpl);
  assert.strictEqual(otherHost.status, 404);
  assert.strictEqual(seen.length, 0);
});
