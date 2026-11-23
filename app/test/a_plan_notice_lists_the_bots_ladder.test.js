'use strict';
/**
 * The plan notice lists what the plan includes as the bot's plan ladder says
 * it at send time, the same answer the dashboard's plan card draws, and says
 * so honestly when it could not read it or when the ladder is not enforced.
 * It picks the downgrade wording only when it knows the plan went down.
 *
 * Driven through the real routes/sync.js on the in-memory database, with the
 * bot gateway stubbed at its module (as a_plan_card_is_the_bots_tier.test.js
 * does). Its own file because the stub replaces the gateway for every module
 * that loads it, and an_account_change_reaches_its_owner.test.js needs the
 * gateway absent for its deletion case.
 */
process.env.JWT_SECRET = process.env.JWT_SECRET || 'j'.repeat(64);
process.env.BOT_SYNC_SECRET = 's'.repeat(48);
process.env.APP_BASE_URL = 'https://app.test';
delete process.env.DATABASE_URL;

const test = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');
const express = require('express');

// The bot's answer, as its gateway sends it; `asked` records each call.
const asked = [];
let gatewayAnswer = null;
let gatewayConfigured = true;
const gatewayPath = require.resolve(path.join(__dirname, '..', 'lib', 'gateway'));
require.cache[gatewayPath] = {
  id: gatewayPath, filename: gatewayPath, loaded: true,
  exports: {
    isConfigured: () => gatewayConfigured,
    getGateway: async (p, timeout) => {
      asked.push({ p, timeout });
      if (gatewayAnswer instanceof Error) throw gatewayAnswer;
      return gatewayAnswer;
    },
    postGateway: async () => ({ status: 200, data: {} }),
    relay: (res, r) => res.status(r.status).json(r.data),
  },
};

const mailer = require('../lib/mailer');
const sent = [];
mailer.sendMail = async (msg) => { sent.push(msg); return { skipped: false }; };
mailer.isConfigured = () => true;

const { pool } = require('../db');
const prefs = require('../lib/email_prefs');
const planFeatures = require('../lib/plan_features');

let server, base;
test.before(async () => {
  const app = express();
  app.use(express.json());
  app.use('/api/bot/sync', require('../routes/sync'));
  await new Promise((res) => { server = app.listen(0, '127.0.0.1', res); });
  base = `http://127.0.0.1:${server.address().port}`;
});
test.after(() => server && server.close());

const SECRET = process.env.BOT_SYNC_SECRET;

async function notice(body) {
  const r = await fetch(`${base}/api/bot/sync/tier-notice`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-Bot-Secret': SECRET },
    body: JSON.stringify(body),
  });
  return { status: r.status, json: await r.json() };
}

let n = 0;
async function stored() {
  n += 1;
  const email = `ladder${n}@example.com`;
  const [r] = await pool.execute('INSERT INTO users (email, password_hash) VALUES (?, ?)', [email, 'x']);
  return { id: r.insertId, email };
}

// The ladder's shape, as bot/token/tier_gate.py plan_ladder returns it.
function ladder({ enforced }) {
  return {
    status: 200,
    data: {
      reading: { tier: 'pro' },
      ladder: {
        enforced,
        stars: false,
        stake: false,
        tiers: [
          { tier: 'basic', name: 'Basic', unlocks: [], chat_per_day: 20, premium_analyze: false },
          { tier: 'pro', name: 'Pro', unlocks: [
            { feature: 'deepscan', commands: ['deepscan'] },
            { feature: 'patterns', commands: ['patterns'] },
          ], chat_per_day: 50, premium_analyze: true },
          { tier: 'elite', name: 'Elite', unlocks: [
            { feature: 'run_backtest', commands: ['backtest'] },
          ], chat_per_day: null, premium_analyze: true },
        ],
      },
    },
  };
}

const features = (text) => [...text.matchAll(/^- (.*)$/gm)].map((m) => m[1]);

test('an enforced ladder: the tier\'s own lines, as the plan card prints them', async () => {
  const m = await stored();
  gatewayConfigured = true;
  gatewayAnswer = ladder({ enforced: true });
  asked.length = 0;
  const from = sent.length;
  const r = await notice({ member: `web:${m.id}`, tier: 'pro' });
  assert.deepEqual(r.json, { sent: true });
  assert.deepEqual(asked.map((a) => a.p), [`/tier?telegram_id=${encodeURIComponent(`web:${m.id}`)}`]);
  assert.equal(asked[0].timeout, planFeatures.TIMEOUT_MS);
  const mail = sent.slice(from)[0];
  assert.deepEqual(features(mail.text), [
    'Everything in Basic',
    'In the Telegram bot: /deepscan /patterns',
    'AI chat: 50 questions a day',
    'The operator-funded model on /analyze',
  ]);
  assert.ok(mail.html.includes('<li style="margin:0 0 8px;">In the Telegram bot: /deepscan /patterns</li>'));
});

test('a ladder that is not enforced lists no commands as unlocked and says it is not enforced', async () => {
  const m = await stored();
  gatewayAnswer = ladder({ enforced: false });
  for (const [tier, want] of [
    ['pro', ['Everything in Basic', 'AI chat: 50 questions a day', 'The operator-funded model on /analyze']],
    ['basic', ['AI chat: 20 questions a day']],
    ['elite', ['Everything in Pro', 'AI chat: no daily cap', 'The operator-funded model on /analyze']],
  ]) {
    const from = sent.length;
    const r = await notice({ member: `web:${m.id}`, tier });
    assert.deepEqual(r.json, { sent: true }, tier);
    const text = sent.slice(from)[0].text;
    assert.deepEqual(features(text), [...want, 'Tiers are not enforced yet: every member can use every command.'], tier);
    assert.doesNotMatch(text, /In the Telegram bot|Every command not listed/, tier);
  }
});

test('a ladder that could not be read is said, not listed as empty', async () => {
  const m = await stored();
  const cases = [
    ['no gateway', () => { gatewayConfigured = false; gatewayAnswer = ladder({ enforced: true }); }],
    ['a failed call', () => { gatewayConfigured = true; gatewayAnswer = new Error('Gateway timeout'); }],
    ['a 502', () => { gatewayAnswer = { status: 502, data: { error: 'Bot gateway error' } }; }],
    ['an answer with no ladder', () => { gatewayAnswer = { status: 200, data: { reading: {} } }; }],
    ['a ladder without the tier', () => {
      const a = ladder({ enforced: true });
      a.data.ladder.tiers = a.data.ladder.tiers.filter((t) => t.tier !== 'elite');
      gatewayAnswer = a;
    }],
  ];
  for (const [what, arrange] of cases) {
    arrange();
    const from = sent.length;
    const r = await notice({ member: `web:${m.id}`, tier: 'elite' });
    assert.deepEqual(r.json, { sent: true }, what);
    assert.deepEqual(features(sent.slice(from)[0].text), ['Your dashboard shows what it includes.'], what);
  }
  gatewayConfigured = true;
});

test('the downgrade wording only when the plan is known to have gone down', async () => {
  const m = await stored();
  gatewayAnswer = ladder({ enforced: true });
  const UP = 'Here’s what it includes:';
  const DOWN = 'Here’s what it includes now:';
  for (const [body, want] of [
    [{ tier: 'pro', from: 'elite' }, DOWN],
    [{ tier: 'basic', from: 'pro' }, DOWN],
    [{ tier: 'basic', from: 'ELITE' }, DOWN],
    [{ tier: 'pro', from: 'basic' }, UP],
    [{ tier: 'pro' }, UP],
    [{ tier: 'pro', from: 'pro' }, UP],
    [{ tier: 'pro', from: 'admin' }, UP],
    [{ tier: 'pro', from: 42 }, UP],
  ]) {
    const from = sent.length;
    const r = await notice({ member: `web:${m.id}`, ...body });
    assert.deepEqual(r.json, { sent: true }, JSON.stringify(body));
    const mail = sent.slice(from)[0];
    assert.equal(mail.subject, `Your RUNECLAW plan is now ${body.tier === 'pro' ? 'Pro' : 'Basic'}`);
    assert.ok(mail.text.includes(`\n${want}\n`), `${JSON.stringify(body)}: ${mail.text}`);
    assert.doesNotMatch(mail.text, /upgraded|downgraded/i);
  }
});

test('a member who opted out costs no read of the ladder', async () => {
  const m = await stored();
  await prefs.setOptOut(m.id, true);
  gatewayAnswer = ladder({ enforced: true });
  asked.length = 0;
  const from = sent.length;
  const r = await notice({ member: `web:${m.id}`, tier: 'pro' });
  assert.deepEqual(r.json, { sent: false, reason: 'opted_out' });
  assert.deepEqual(asked, [], 'the bot was asked for a ladder nobody would read');
  assert.equal(sent.length, from);
});

test('the whole-book mirror still mails nobody and asks the bot nothing', async () => {
  const m = await stored();
  asked.length = 0;
  const from = sent.length;
  const r = await fetch(`${base}/api/bot/sync/tiers`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-Bot-Secret': SECRET },
    body: JSON.stringify({ tiers: [{ telegram_id: `web:${m.id}`, tier: 'elite' }] }),
  });
  assert.equal(r.status, 200);
  assert.equal((await r.json()).updated, 1);
  assert.equal(sent.length, from);
  assert.deepEqual(asked, []);
});
