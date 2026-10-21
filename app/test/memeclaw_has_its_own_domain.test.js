'use strict';
/**
 * Memeclaw on its own domain, served by this same server (lib/memeclaw_host.js).
 *
 * On a host named in MEMECLAW_HOSTS: `/` is the launchpad, Memeclaw's own
 * paths are answered, and nothing else of the website is (the presale bar,
 * the token page, the dashboard, sign-in). On any other host, and with the
 * setting unset, nothing changes.
 */
process.env.JWT_SECRET = process.env.JWT_SECRET || 'j'.repeat(64);
delete process.env.DATABASE_URL;

const test = require('node:test');
const assert = require('node:assert');
const http = require('node:http');
const express = require('express');

const host = require('../lib/memeclaw_host');
const pageRoute = require('../routes/memeclaw_page');

const MEME = 'memeclaw.test';
const MAIN = 'www.humanoid-traders.com';

/** The server's order, in miniature: the gate, then the website's own routes. */
async function serve(env) {
  const app = express();
  app.use(host.gate({ env }));
  app.use(express.json());
  app.get('/', (req, res) => res.send('the main site'));
  app.get('/token', (req, res) => res.send('the token page'));
  app.post('/api/auth/login', (req, res) => res.json({ ok: true }));
  app.get(/^\/js\/.+\.js$/, (req, res) => res.type('js').send('// ' + req.path));
  app.get(/^\/api\/call\/.+$/, (req, res) => res.json({ seal: 'x' }));
  // No route writes on a path the gate lets through today; this stands in
  // for one added later, which the gate must still refuse on Memeclaw's host.
  app.post(/^\/api\/call\/.+$/, (req, res) => res.json({ wrote: true }));
  app.post('/api/memeclaw/paper/players', (req, res) => res.json({ key: 'k' }));
  app.use('/memeclaw', pageRoute);
  const server = http.createServer(app);
  await new Promise((r) => server.listen(0, '127.0.0.1', r));
  return { server, port: server.address().port };
}

/** One request with a Host header (fetch will not set one). */
function ask(port, hostName, method, path) {
  return new Promise((resolve, reject) => {
    const req = http.request({ host: '127.0.0.1', port, method, path, headers: { host: hostName, 'content-type': 'application/json' } }, (res) => {
      let body = '';
      res.setEncoding('utf8');
      res.on('data', (c) => { body += c; });
      res.on('end', () => resolve({ status: res.statusCode, body }));
    });
    req.on('error', reject);
    req.end(method === 'GET' ? undefined : '{}');
  });
}

test('on Memeclaw\'s host, / is the launchpad and nothing else of the website answers', async () => {
  const { server, port } = await serve({ MEMECLAW_HOSTS: `${MEME}, www.${MEME}` });
  try {
    for (const h of [MEME, `www.${MEME}`, MEME.toUpperCase()]) {
      const home = await ask(port, h, 'GET', '/');
      assert.equal(home.status, 200, h);
      assert.match(home.body, /id="mc-root"/, `${h}: / is the launchpad`);
      assert.doesNotMatch(home.body, /the main site/);
    }
    for (const p of ['/token', '/dashboard', '/arena', '/api/trades/history', '/js/presale-bar.js', '/api/call/duel:' + 'a'.repeat(64)]) {
      const r = await ask(port, MEME, 'GET', p);
      assert.equal(r.status, 404, `${p} answered on Memeclaw's host`);
      assert.doesNotMatch(r.body, /token page/);
    }
    const signIn = await ask(port, MEME, 'POST', '/api/auth/login');
    assert.equal(signIn.status, 404, 'no write outside Memeclaw\'s API on its host');
    const write = await ask(port, MEME, 'POST', '/api/call/launch:' + 'a'.repeat(64));
    assert.equal(write.status, 404, 'not even on a path it reads');
    assert.doesNotMatch(write.body, /wrote/);
    // The other arm: Memeclaw's own paths answer there.
    assert.equal((await ask(port, MEME, 'GET', '/memeclaw')).status, 200);
    assert.equal((await ask(port, MEME, 'GET', '/js/memeclaw.js')).status, 200);
    assert.equal((await ask(port, MEME, 'GET', '/api/call/launch:' + 'a'.repeat(64))).status, 200, 'a launch receipt');
    assert.equal((await ask(port, MEME, 'POST', '/api/memeclaw/paper/players')).status, 200, 'practice mode writes through its API');
  } finally { server.close(); }
});

test('on the main host, and with the setting unset, nothing changes', async () => {
  // [setting, hosts that must see the main site]: with no usable host named,
  // even Memeclaw's own name is the main site.
  const cases = [[{ MEMECLAW_HOSTS: MEME }, [MAIN]], [{}, [MAIN, MEME]], [{ MEMECLAW_HOSTS: ' , ' }, [MAIN, MEME]]];
  for (const [env, hosts] of cases) {
    const { server, port } = await serve(env);
    try {
      for (const h of hosts) {
        assert.equal((await ask(port, h, 'GET', '/')).body, 'the main site', `${h} with ${JSON.stringify(env)}`);
        assert.equal((await ask(port, h, 'GET', '/token')).status, 200);
        assert.equal((await ask(port, h, 'POST', '/api/auth/login')).status, 200);
        assert.equal((await ask(port, h, 'POST', '/api/call/launch:' + 'a'.repeat(64))).status, 200);
        assert.equal((await ask(port, h, 'GET', '/memeclaw')).status, 200, 'and /memeclaw keeps serving on the main site');
      }
    } finally { server.close(); }
  }
});

test('everything the launchpad loads passes its own gate, at the versions the page asks for', () => {
  const pass = (p) => host.ALLOWED.some((re) => re.test(p));
  const wanted = [...pageRoute.HEAD_SCRIPTS, ...pageRoute.SCRIPTS, ...pageRoute.STYLES].map((s) => s.split('?')[0]);
  for (const p of [...wanted, '/api/memeclaw/config', '/privacy', '/terms', '/favicon.svg']) {
    assert.ok(pass(p), `${p} is loaded by the launchpad but refused on its host`);
  }
  for (const p of ['/', '/token', '/js/app.js', '/api/auth/me', '/memeclawx', '/api/memeclawx']) {
    assert.ok(!pass(p), `${p} would answer on Memeclaw's host`);
  }
  // The not-found page uses the launchpad's stylesheet at the launchpad's version.
  const html = host._notHere;
  for (const s of pageRoute.STYLES) assert.ok(html.includes(`href="${s}"`), `the not-found page asks for an old ${s}`);
});

test('a link preview names Memeclaw\'s domain when one is set, and only an https origin', () => {
  assert.equal(host.publicUrl({}), 'https://www.humanoid-traders.com/memeclaw');
  assert.equal(host.publicUrl({ MEMECLAW_PUBLIC_URL: 'https://memeclaw.test/' }), 'https://memeclaw.test');
  for (const bad of ['http://memeclaw.test', 'https://memeclaw.test/x', 'javascript:alert(1)', 'https://a"b']) {
    assert.equal(host.publicUrl({ MEMECLAW_PUBLIC_URL: bad }), 'https://www.humanoid-traders.com/memeclaw', bad);
  }
  assert.deepEqual([...host.hosts({ MEMECLAW_HOSTS: 'A.test, b.test,bad host,,' })], ['a.test', 'b.test']);
});
