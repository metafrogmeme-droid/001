'use strict';
/**
 * The plan card shows the tier the bot holds and what each tier buys there,
 * and the site's copy of the tier reaches every member the bot can grant.
 *
 * The card carried its own table: Pro "Live trading eligibility" and
 * "premium models on your scans", Elite "Higher live caps" and "Early access
 * to new agent features". No tier granted any of them, and live limits are
 * never tier-gated. It now draws the bot's answer (GET /api/tier, the bot
 * gateway's /tier). And the mirror that writes users.plan matched rows on
 * telegram_id only, so a website-only member (the bot's "web:<id>") could
 * not hold a tier at all, and it cut its input at 500 rows while answering
 * ok, so the member past the cut never got theirs.
 *
 * Driven through the real routes/sync.js and routes/tier.js on the
 * in-memory DB, with the gateway stubbed at its module.
 */
process.env.JWT_SECRET = process.env.JWT_SECRET || 'j'.repeat(64);
process.env.BOT_SYNC_SECRET = 's'.repeat(48);
delete process.env.DATABASE_URL;

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const http = require('node:http');
const path = require('node:path');
const express = require('express');
const jwt = require('jsonwebtoken');
const { codeOnly } = require('./helpers/code_only');

// The bot's answer, as its gateway sends it; `asked` records who was asked.
const asked = [];
let gatewayAnswer = null;
const gatewayPath = require.resolve(path.join(__dirname, '..', 'lib', 'gateway'));
require.cache[gatewayPath] = {
  id: gatewayPath, filename: gatewayPath, loaded: true,
  exports: {
    isConfigured: () => true,
    getGateway: async (p) => { asked.push(p); return gatewayAnswer; },
    postGateway: async () => ({ status: 200, data: {} }),
    relay: (res, r) => {
      if (r.status >= 200 && r.status < 300) return res.json(r.data);
      if (r.status >= 400 && r.status < 500) return res.status(r.status).json(r.data);
      return res.status(502).json({ error: 'Bot gateway error' });
    },
  },
};

const authModule = require('../auth');
const { pool } = require('../db');
const PlanCard = require('../public/js/plan-card-model.js');

const SECRET = process.env.BOT_SYNC_SECRET;
let server, base;

function req(method, p, { botSecret, body, token } = {}) {
  return new Promise((resolve, reject) => {
    const payload = body ? JSON.stringify(body) : null;
    const r = http.request(`${base}${p}`, {
      method,
      headers: {
        ...(botSecret ? { 'X-Bot-Secret': botSecret } : {}),
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
        ...(payload ? { 'Content-Type': 'application/json' } : {}),
      },
    }, (res) => {
      let d = '';
      res.on('data', (c) => { d += c; });
      res.on('end', () => resolve({ status: res.statusCode, headers: res.headers,
                                    data: d ? JSON.parse(d) : {} }));
    });
    r.on('error', reject);
    if (payload) r.write(payload);
    r.end();
  });
}

async function register(email) {
  const r = await req('POST', '/api/auth/register', { body: { email, password: 'x'.repeat(12) } });
  const uid = Number(r.data.user_id || r.data.id || (r.data.user && r.data.user.id));
  assert.ok(uid > 0, JSON.stringify(r.data));
  return uid;
}
async function link(uid, chat) {
  await pool.execute(
    'UPDATE users SET link_token = NULL, link_token_expires = NULL, telegram_linked = TRUE, telegram_id = ? WHERE id = ?',
    [chat, uid]);
}
async function planOf(uid) {
  const [rows] = await pool.execute('SELECT * FROM users WHERE id = ?', [uid]);
  return rows[0].plan;
}
const tokenFor = (uid, email) => jwt.sign({ user_id: uid, email }, process.env.JWT_SECRET, { expiresIn: '1h' });

test.before(async () => {
  const app = express();
  app.use(express.json());
  app.use('/api/auth', authModule.router);
  app.use('/api/bot/sync', require('../routes/sync'));
  app.use('/api/tier', require('../routes/tier'));
  await new Promise((res) => { server = app.listen(0, '127.0.0.1', res); });
  base = `http://127.0.0.1:${server.address().port}`;
});
test.after(() => server && server.close());

const LADDER = {
  enforced: true, stake: false, stake_min: null,
  tiers: [
    { tier: 'basic', name: 'Basic', unlocks: [], chat_per_day: 5, premium_analyze: false },
    { tier: 'pro', name: 'Pro', unlocks: [{ feature: 'deepscan', commands: ['deepscan'] }],
      chat_per_day: null, premium_analyze: false },
    { tier: 'elite', name: 'Elite', unlocks: [{ feature: 'run_backtest', commands: ['backtest'] }],
      chat_per_day: null, premium_analyze: true },
  ],
};
const esc = (s) => String(s == null ? '' : s).replace(/[&<>"']/g, (c) => `&#${c.charCodeAt(0)};`);

// ── the mirror ─────────────────────────────────────────────────────────────

test('a website-only member\'s tier reaches their row, and a linked account keeps its own', async () => {
  const web = await register('webonly@example.com');
  const linked = await register('linked@example.com');
  await link(linked, '777001');
  // The linked account's current grant goes FIRST and its old web key
  // after it, so a route that let the web key through would leave it at
  // the old grant rather than be rescued by row order.
  const r = await req('POST', '/api/bot/sync/tiers', { botSecret: SECRET, body: { tiers: [
    { telegram_id: `web:${web}`, tier: 'pro' },
    { telegram_id: '777001', tier: 'elite' },
    { telegram_id: `web:${linked}`, tier: 'basic' },   // the old web key of a linked account
  ] } });
  assert.equal(r.status, 200);
  assert.equal(await planOf(web), 'pro', 'a web-only member could not hold a tier');
  assert.equal(await planOf(linked), 'elite', 'an old web-key grant overwrote the linked one');
});

test('a web key never writes the operator tier', async () => {
  const web = await register('notop@example.com');
  const r = await req('POST', '/api/bot/sync/tiers', { botSecret: SECRET, body: { tiers: [
    { telegram_id: `web:${web}`, tier: 'admin' }] } });
  assert.equal(r.status, 200);
  assert.notEqual(await planOf(web), 'admin', 'a sign-up became the operator');
  assert.equal(r.data.skipped, 1);
});

test('a list longer than a page is refused whole, not cut and answered ok', async () => {
  const tiers = Array.from({ length: 501 }, (_, i) => ({ telegram_id: String(900000 + i), tier: 'pro' }));
  const over = await req('POST', '/api/bot/sync/tiers', { botSecret: SECRET, body: { tiers } });
  assert.equal(over.status, 413);
  const page = await req('POST', '/api/bot/sync/tiers', { botSecret: SECRET, body: { tiers: tiers.slice(0, 500) } });
  assert.equal(page.status, 200);
  assert.equal(page.data.received, 500);
});

// ── the door ───────────────────────────────────────────────────────────────

test('/api/tier asks the bot about the caller, by the identity the bot keys them on', async () => {
  const web = await register('asker@example.com');
  gatewayAnswer = { status: 200, data: { reading: { tier: 'pro' }, ladder: LADDER } };
  asked.length = 0;
  const r = await req('GET', '/api/tier', { token: tokenFor(web, 'asker@example.com') });
  assert.equal(r.status, 200);
  assert.deepEqual(asked, [`/tier?telegram_id=${encodeURIComponent(`web:${web}`)}`]);
  assert.match(String(r.headers['cache-control']), /private/);
  const linked = await register('asker2@example.com');
  await link(linked, '777002');
  asked.length = 0;
  await req('GET', '/api/tier', { token: tokenFor(linked, 'asker2@example.com') });
  assert.deepEqual(asked, ['/tier?telegram_id=777002']);
  const anon = await req('GET', '/api/tier');
  assert.equal(anon.status, 401);
});

test('a bot that did not answer is a failed read, not a Basic plan', async () => {
  const web = await register('down@example.com');
  gatewayAnswer = { status: 503, data: { error: 'down' } };
  const r = await req('GET', '/api/tier', { token: tokenFor(web, 'down@example.com') });
  assert.equal(r.status, 502);
  assert.throws(() => PlanCard.cardHtml(r.data, esc), /not readable/);
});

// ── the card ───────────────────────────────────────────────────────────────

test('the card draws the bot\'s ladder, marks the member\'s tier, and lists no live benefit', () => {
  const html = PlanCard.cardHtml({ reading: { tier: 'elite', granted: 'elite', granted_known: true },
    ladder: LADDER }, esc);
  assert.match(html, /\/deepscan/);
  assert.match(html, /\/backtest/);
  assert.match(html, /AI chat: 5 questions a day/);
  assert.equal((html.match(/The operator-funded model on \/analyze/g) || []).length, 1,
    'the operator-funded model was listed on a tier the bot did not route to it');
  assert.equal((html.match(/your plan/g) || []).length, 1);
  assert.ok(html.indexOf('your plan') > html.indexOf('Elite'), 'the chip is on the wrong tile');
  for (const claim of [/Higher live caps/i, /Live trading eligibility/i, /Early access/i,
    /Priority support/i, /premium models on your scans/i]) {
    assert.doesNotMatch(html, claim);
  }
  assert.doesNotMatch(html, /not enforced/, 'an enforced ladder said it was not');
});

test('an unenforced ladder says so, and an unread tier is not drawn as a plan', () => {
  const open = PlanCard.cardHtml({ reading: { tier: 'basic', granted: 'basic', granted_known: true },
    ladder: { ...LADDER, enforced: false } }, esc);
  assert.match(open, /Tiers are not enforced yet/);
  const unread = PlanCard.cardHtml({ reading: { tier: null }, ladder: LADDER }, esc);
  assert.match(unread, /could not be read/);
  assert.doesNotMatch(unread, /your plan/, 'an unread tier was marked as a plan');
  const odd = PlanCard.cardHtml({ reading: { tier: 'basic', granted: 'premium', granted_known: false },
    ladder: LADDER }, esc);
  assert.match(odd, /<code>premium<\/code> is not one the bot knows/);
});

test('the dashboard\'s plan panel renders the bot\'s answer and carries no table of its own', () => {
  const src = codeOnly(fs.readFileSync(path.join(__dirname, '..', 'public', 'js', 'dashboard.js'), 'utf8'));
  const start = src.indexOf("renderPanel(C('aplan')");
  const block = src.slice(start, src.indexOf('}, { empty:', start));
  assert.ok(start > 0, 'the plan panel was not found');
  assert.match(block, /fetchJSON\('\/api\/tier'/);
  assert.match(block, /PlanCardModel\.cardHtml\(/);
  assert.doesNotMatch(block, /PLANS|Higher live caps|Live trading eligibility/);
  const page = fs.readFileSync(path.join(__dirname, '..', 'public', 'dashboard.html'), 'utf8');
  assert.ok(page.indexOf('plan-card-model.js') < page.indexOf('/js/dashboard.js'),
    'the model must load before the dashboard that calls it');
});
