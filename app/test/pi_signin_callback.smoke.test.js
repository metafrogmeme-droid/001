'use strict';
/**
 * Pi Sign-In's callback page in a real browser (routes/pi_signin_page.js).
 *
 * Pi sends the person back to /auth/pi/callback with the access token in the
 * URL fragment. Only a script can read a fragment, so the page's one script
 * has to do four things in order, which no server-side test can watch:
 *   1. take the token and state out of the fragment;
 *   2. take them out of the address bar and the history entry;
 *   3. post them to /api/auth/oauth/pi/token;
 *   4. land on the home page with the session payload, or with one of our
 *      sentences when Pi said no or could not be reached.
 *
 * The server here is a stand-in that records what the page posted and
 * answers what each case needs. The real route is pi_signin_on_the_website.test.js's.
 */
const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const express = require('express');

const callbackPage = require('../routes/pi_signin_page');
const PUBLIC = path.join(__dirname, '..', 'public');

function findChromium() {
  const cands = [process.env.RC_SMOKE_CHROMIUM].filter(Boolean);
  const root = process.env.PLAYWRIGHT_BROWSERS_PATH || '/opt/pw-browsers';
  try { for (const d of fs.readdirSync(root)) if (/^chromium-\d+$/.test(d)) cands.push(path.join(root, d, 'chrome-linux', 'chrome')); } catch (e) { /* none */ }
  return cands.find((p) => { try { return fs.statSync(p).isFile(); } catch (e) { return false; } }) || null;
}
let pw = null; try { pw = require('playwright-core'); } catch (e) { pw = null; }
const CHROMIUM = findChromium();
const SKIP = !pw ? 'playwright-core is not installed' : !CHROMIUM ? 'no Chromium binary found' : null;

/** The page and its script, as server.js serves them; the token route is a recorder. */
async function serve(answer) {
  const posted = [];
  const app = express();
  app.use(express.json());
  app.use('/js', express.static(path.join(PUBLIC, 'js')));
  app.get('/auth/pi/callback', callbackPage);
  app.post('/api/auth/oauth/pi/token', (req, res) => {
    posted.push(req.body);
    const [status, body] = answer;
    res.status(status).json(body);
  });
  app.get('/', (req, res) => res.type('html').send('<!doctype html><title>home</title><p>home</p>'));
  const server = await new Promise((r) => { const s = app.listen(0, '127.0.0.1', () => r(s)); });
  return { server, posted, base: `http://127.0.0.1:${server.address().port}` };
}

async function visit(answer, fragment) {
  const { server, posted, base } = await serve(answer);
  const browser = await pw.chromium.launch({ executablePath: CHROMIUM });
  try {
    const page = await browser.newPage();
    const seen = [];
    page.on('framenavigated', (f) => { if (f === page.mainFrame()) seen.push(f.url()); });
    await page.goto(`${base}/auth/pi/callback#${fragment}`);
    await page.waitForURL((u) => new URL(u).pathname === '/', { timeout: 5000 });
    return { posted, final: page.url(), seen, base };
  } finally {
    await browser.close();
    server.close();
  }
}

const TOKEN = 'piAccessToken_fromPiItself_1234567890';

test('a token from Pi is posted with its state, then the home page gets the session', SKIP ? { skip: SKIP } : {}, async () => {
  const r = await visit([200, { oauth: 'eyJ0b2tlbiI6InQifQ==' }],
    `access_token=${TOKEN}&token_type=Bearer&expires_in=3600&state=st-abc`);
  assert.deepStrictEqual(r.posted, [{ access_token: TOKEN, state: 'st-abc' }]);
  assert.strictEqual(r.final, `${r.base}/#oauth=eyJ0b2tlbiI6InQifQ==`);
  assert.ok(!r.final.includes(TOKEN));
});

test('Pi\'s cancel and a refused, unreachable or spent sign-in each land with our own sentence', SKIP ? { skip: SKIP } : {}, async () => {
  const cases = [
    [[200, {}], 'error=access_denied&state=s', 'Pi sign-in was cancelled.', 0],
    [[200, {}], 'error=%3Cimg%20src%3Dx%3E&state=s', 'Pi could not complete the sign-in. Please try again.', 0],
    [[200, {}], 'state=s', 'Pi did not return a sign-in. Please try again.', 0],
    [[401, { error: 'pi_rejected' }], `access_token=${TOKEN}&state=s`, 'Pi did not confirm the sign-in.', 1],
    [[503, { error: 'pi_unavailable' }], `access_token=${TOKEN}&state=s`,
      'Pi could not be reached to confirm the sign-in. This is on our side; please try again.', 1],
    [[400, { error: 'invalid or expired login attempt' }], `access_token=${TOKEN}&state=s`,
      'This sign-in link expired or was already used. Please try again.', 1],
  ];
  for (const [answer, fragment, sentence, posts] of cases) {
    const r = await visit(answer, fragment);
    assert.strictEqual(r.posted.length, posts, fragment);
    assert.strictEqual(decodeURIComponent(new URL(r.final).hash.replace(/^#oauth_error=/, '')), sentence, fragment);
    assert.ok(!r.final.includes(TOKEN), 'the token is never carried to the home page');
  }
});

test('the token is out of the address bar before the page posts it', SKIP ? { skip: SKIP } : {}, async () => {
  const { server, base } = await serve([200, {}]);
  const browser = await pw.chromium.launch({ executablePath: CHROMIUM });
  try {
    const page = await browser.newPage();
    let hrefAtPost = null;
    await page.route('**/api/auth/oauth/pi/token', async (route) => {
      hrefAtPost = await page.evaluate(() => location.href).catch(() => null);
      await route.fulfill({ status: 200, contentType: 'application/json', body: '{"oauth":"cA=="}' });
    });
    await page.goto(`${base}/auth/pi/callback#access_token=${TOKEN}&state=s1`);
    await page.waitForURL((u) => new URL(u).pathname === '/', { timeout: 5000 });
    assert.strictEqual(hrefAtPost, `${base}/auth/pi/callback`);
  } finally {
    await browser.close();
    server.close();
  }
});
