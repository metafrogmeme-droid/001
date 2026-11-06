/**
 * The public homepage is the Express app, not website/index.html.
 *
 * www.humanoid-traders.com is served by app.get('/') from
 * app/public/index.html (app/server.js). A section that exists only in the
 * marketing build under website/ is invisible after a republish of app/.
 * This asks the running server for GET /, the same path Mule Pages serves.
 */
'use strict';

const test = require('node:test');
const assert = require('node:assert');
const http = require('node:http');
const fs = require('node:fs');
const path = require('node:path');
const { spawn } = require('node:child_process');

const APP = path.join(__dirname, '..');
const PORT = 18771;

let child;
let base;

function get(pathname) {
  return new Promise((resolve, reject) => {
    const req = http.get(`${base}${pathname}`, (res) => {
      const chunks = [];
      res.on('data', (c) => chunks.push(c));
      res.on('end', () => resolve({
        status: res.statusCode,
        type: res.headers['content-type'] || '',
        body: Buffer.concat(chunks).toString('utf8'),
      }));
    });
    req.on('error', reject);
  });
}

test.before(async () => {
  const env = { ...process.env, PORT: String(PORT), NODE_ENV: 'test' };
  delete env.DATABASE_URL;
  env.BOT_SYNC_SECRET = 'b'.repeat(48);
  env.JWT_SECRET = 'j'.repeat(64);
  child = spawn(process.execPath, ['server.js'], {
    cwd: APP,
    env,
    stdio: ['ignore', 'pipe', 'pipe'],
  });
  let log = '';
  await new Promise((resolve, reject) => {
    const timer = setTimeout(() => {
      reject(new Error(`server did not listen:\n${log.slice(-2000)}`));
    }, 20000);
    const onData = (buf) => {
      log += buf.toString();
      if (log.includes(`running on port ${PORT}`)) {
        clearTimeout(timer);
        resolve();
      }
    };
    child.stdout.on('data', onData);
    child.stderr.on('data', onData);
    child.on('exit', (code) => {
      clearTimeout(timer);
      reject(new Error(`server exited ${code}:\n${log.slice(-2000)}`));
    });
  });
  base = `http://127.0.0.1:${PORT}`;
});

test.after(() => {
  if (child && !child.killed) child.kill('SIGTERM');
});

test('GET / is the Express homepage and it shows the two BOT Chain origins', async () => {
  const res = await get('/');
  assert.equal(res.status, 200);
  assert.match(res.type, /text\/html/);
  const html = res.body;
  const hrefs = [...html.matchAll(/href="([^"]*)"/g)].map((m) => m[1]);
  assert.ok(hrefs.includes('https://botchain.ai'), 'https://botchain.ai is not an href on GET /');
  assert.ok(hrefs.includes('https://scan.botchain.ai'), 'https://scan.botchain.ai is not an href on GET /');
  assert.ok(html.includes('>https://botchain.ai<'), 'the botchain.ai link text is not the URL');
  assert.ok(html.includes('>https://scan.botchain.ai<'), 'the scan link text is not the URL');
  assert.ok(html.includes('0x2554160560747dF51873171FBD838bbDEEdf9093'));
  assert.ok(html.includes('0xd229cab8bE1a674802DA9Fa847CFEdD7eAb7f11F'));
  assert.ok(html.includes('0xc40e34faa530d2318c6658681471fb64cb2a5a6c4382fbe082066a5d16ca9640'));
  assert.match(html, /not proof that what the file says is true/);
  assert.match(html, /not a trading result/);
  assert.match(html, /not a finished product flow/);
  assert.match(html, /no Anchored event/);
  assert.doesNotMatch(html, /bohr-seal-verify|vercel\.app/i);
  const at = (needle) => {
    const i = html.indexOf(needle);
    assert.ok(i >= 0, `missing ${needle}`);
    return i;
  };
  assert.ok(at('id="doorsTease"') < at('id="botchain"'),
    'BOT Chain leads the Express homepage');
  assert.ok(at('data-i18n="nav.grp_trade"') < at('id="botchain"'));
  assert.ok(at('<h1') < at('id="botchain"'));
});

test('the route that serves / is still public/index.html', () => {
  const server = fs.readFileSync(path.join(APP, 'server.js'), 'utf8');
  assert.match(server,
    /app\.get\('\/', \(req, res\) => \{ res\.setHeader\('Cache-Control', 'no-cache'\); res\.sendFile\(path\.join\(__dirname, 'public', 'index\.html'\)\); \}\)/);
});

test('every locale keeps the two origin hrefs inside the translated section', () => {
  const i18n = require('../public/js/i18n.js');
  const codes = i18n.LANGS.map((l) => l.code);
  const body = i18n.STRINGS['bc.body'];
  assert.ok(body, 'bc.body is missing');
  for (const code of codes) {
    const html = body[code];
    assert.ok(html, `bc.body is missing ${code}`);
    assert.ok(html.includes('href="https://botchain.ai"'), `${code} dropped https://botchain.ai`);
    assert.ok(html.includes('href="https://scan.botchain.ai"'), `${code} dropped https://scan.botchain.ai`);
    assert.ok(!/bohr-seal-verify|vercel\.app/i.test(html), `${code} names the unpublished checker`);
    assert.match(html, /677/);
  }
});
