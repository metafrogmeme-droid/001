'use strict';
/**
 * The website's price-alert intercept reaches the bot's sync route as a card
 * whose ARGUMENT is the sentence, and a tripped alert reaches a linked
 * Telegram account through a trips queue the bot polls and acks.
 *
 * Three things pinned here: the route answers the intercept's own card for
 * the same words (byte for byte) in Telegram's delivery sentence, `unlinked`
 * for a caller nobody can map, and the help card — never nothing — for words
 * that are no alert; a trip writes ONE row beside the push, listed only for
 * a person with a Telegram id, oldest first; and an ack stamps a row once,
 * sent or failed-with-reason, so a second listing never carries it.
 */
process.env.JWT_SECRET = 'j'.repeat(64);
process.env.BOT_SYNC_SECRET = 's'.repeat(48);
delete process.env.DATABASE_URL;

const test = require('node:test');
const assert = require('node:assert/strict');
const http = require('node:http');
const express = require('express');
const authModule = require('../auth');
const { pool } = require('../db');
const alerts = require('../lib/alerts');

const SECRET = process.env.BOT_SYNC_SECRET;
const TICKERS = { BTCUSDT: { price: 98_000, change: -2.5 }, SOLUSDT: { price: 150, change: 6.2 } };
let server, base;

function req(method, path, { botSecret, body } = {}) {
  return new Promise((resolve, reject) => {
    const payload = body ? JSON.stringify(body) : null;
    const r = http.request(`${base}${path}`, {
      method,
      headers: {
        ...(botSecret ? { 'X-Bot-Secret': botSecret } : {}),
        ...(payload ? { 'Content-Type': 'application/json' } : {}),
      },
    }, (res) => {
      let d = '';
      res.on('data', c => d += c);
      res.on('end', () => resolve({ status: res.statusCode, data: d ? JSON.parse(d) : {} }));
    });
    r.on('error', reject);
    if (payload) r.write(payload);
    r.end();
  });
}
function register(email) {
  return new Promise((resolve, reject) => {
    const body = JSON.stringify({ email, password: 'x'.repeat(12) });
    const rq = http.request(`${base}/api/auth/register`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
    }, (res) => {
      let d = '';
      res.on('data', c => d += c);
      res.on('end', () => resolve(JSON.parse(d)));
    });
    rq.on('error', reject);
    rq.write(body);
    rq.end();
  });
}
const card = (q) => req('GET', '/api/bot/sync/card/alerts' + q, { botSecret: SECRET });
const pending = () => req('GET', '/api/bot/sync/alerts/pending', { botSecret: SECRET });
const ack = (acks) => req('POST', '/api/bot/sync/alerts/ack', { botSecret: SECRET, body: { acks } });

let linked, unlinkedUser;   // users.id of a Telegram-linked account and of one with no Telegram id
const TG = '770001';

test.before(async () => {
  alerts.setTickerFetcher(async () => TICKERS);
  const app = express();
  app.use(express.json());
  app.use('/api/auth', authModule.router);
  app.use('/api/bot/sync', require('../routes/sync'));
  await new Promise((res) => { server = app.listen(0, '127.0.0.1', res); });
  base = `http://127.0.0.1:${server.address().port}`;
  await register('linked@example.com');
  await register('web-only@example.com');
  const [rows] = await pool.execute('SELECT id FROM users WHERE email = ?', ['linked@example.com']);
  linked = rows[0].id;
  const [rows2] = await pool.execute('SELECT id FROM users WHERE email = ?', ['web-only@example.com']);
  unlinkedUser = rows2[0].id;
  await pool.execute('UPDATE users SET telegram_id = ? WHERE id = ?', [TG, linked]);
});
test.after(() => { if (server) server.close(); alerts.setTickerFetcher(null); });

test('the route is the bot-secret channel', async () => {
  const r = await req('GET', '/api/bot/sync/card/alerts?telegram_id=' + TG + '&text=my%20alerts');
  assert.equal(r.status, 403);
});

test('arming: the route answers the intercept\'s own card for the same words, in Telegram\'s delivery sentence', async () => {
  const r = await card(`?telegram_id=${TG}&text=${encodeURIComponent('tell me when BTC drops below 100k')}`);
  assert.equal(r.status, 200);
  assert.equal(r.data.intent, 'alerts');
  assert.match(r.data.reply_html, /Alert armed: <b>BTC price below \$100,000<\/b>/);
  assert.match(r.data.reply_html, /message you here the moment it trips/);
  assert.doesNotMatch(r.data.reply_html, /push notification|Enable push notifications/);
  // The same words on the web: the same condition, the web's own delivery
  // sentence and its push hint — one renderer, one channel word.
  const web = await alerts.maybeHandleAlertChat(linked, 'tell me when SOL rises above 200');
  assert.match(web.reply_html, /send you a push notification/);
  assert.match(web.reply_html, /Enable push notifications/);
  const tg = await alerts.maybeHandleAlertChat(linked, 'tell me when SOL rises above 250', { channel: 'telegram' });
  assert.match(tg.reply_html, /message you here the moment it trips/);
  assert.doesNotMatch(tg.reply_html, /Enable push notifications/);
});

test('listing: no words is the list, byte for byte the intercept\'s, with the web app named as the place to manage them', async () => {
  const r = await card(`?telegram_id=${TG}&text=${encodeURIComponent('my alerts')}`);
  assert.equal(r.status, 200);
  const web = await alerts.maybeHandleAlertChat(linked, 'my alerts', { channel: 'telegram' });
  assert.deepEqual(r.data, { reply_html: web.reply_html, intent: 'alerts' });
  assert.match(r.data.reply_html, /Your alerts/);
  assert.match(r.data.reply_html, /web app's Live Feed view/);
});

test('words that are no alert get the help card, never nothing; an unreadable condition gets the same card', async () => {
  const help = alerts.alertHelpCard();
  for (const text of ['hello there', 'tell me when the scan finishes']) {
    const r = await card(`?telegram_id=${TG}&text=${encodeURIComponent(text)}`);
    assert.equal(r.status, 200, text);
    assert.deepEqual(r.data, { reply_html: help.reply_html, intent: 'alerts' }, text);
  }
  assert.match(help.reply_html, /didn't catch the condition/);
});

test('a caller nobody can map is unlinked, and nothing is armed for them', async () => {
  const before = (await pool.execute('SELECT * FROM user_alerts WHERE active = 1'))[0].length;
  const r = await card(`?telegram_id=999999&text=${encodeURIComponent('tell me when BTC drops below 90k')}`);
  assert.equal(r.status, 200);
  assert.deepEqual(r.data, { reply_html: null, intent: 'alerts', unlinked: true });
  const after = (await pool.execute('SELECT * FROM user_alerts WHERE active = 1'))[0].length;
  assert.equal(after, before, 'no row was written for an unmapped caller');
});

test('the sentence is bounded, not rejected', async () => {
  const r = await card(`?telegram_id=${TG}&text=${encodeURIComponent('x'.repeat(1000))}`);
  assert.equal(r.status, 200);
  assert.deepEqual(r.data, { reply_html: alerts.alertHelpCard().reply_html, intent: 'alerts' });
});

test('a trip writes one row beside the push, and only a linked person\'s trips are pending', async () => {
  // Arm one for the linked account and one for the web-only account, then
  // move the market through both. Earlier tests armed alerts of their own
  // (the route test's "below 100k" for the linked account trips too), so the
  // expectations are read off the pushes rather than typed.
  const a = await alerts.createAlert(linked, { base: 'BTC', metric: 'price', op: '<', threshold: 97_000, mode: 'once' });
  const b = await alerts.createAlert(unlinkedUser, { base: 'BTC', metric: 'price', op: '<', threshold: 97_000, mode: 'once' });
  assert.ok(a.ok && b.ok);
  alerts.setTickerFetcher(async () => ({ BTCUSDT: { price: 96_000, change: -4 }, SOLUSDT: TICKERS.SOLUSDT }));
  const pushes = [];
  const tripped = await alerts.runOnce(async (payload, userIds) => { pushes.push({ payload, userIds }); });
  assert.ok(tripped >= 2, `both new alerts trip (${tripped})`);
  assert.equal(pushes.length, tripped, 'the push still goes out for every trip');
  const linkedPushes = pushes.filter(p => p.userIds[0] === linked);
  const webOnlyPushes = pushes.filter(p => p.userIds[0] === unlinkedUser);
  assert.ok(linkedPushes.length >= 1 && webOnlyPushes.length === 1);
  const r = await pending();
  assert.equal(r.status, 200);
  assert.equal(r.data.trips.length, linkedPushes.length, 'the web-only account has nowhere for a delivery to go');
  for (const t of r.data.trips) {
    assert.equal(t.telegram_id, TG);
    const push = linkedPushes.find(p => p.payload.title === t.title && p.payload.body === t.body);
    assert.ok(push, `the trip carries the push's own title and body: ${t.body}`);
  }
  assert.ok(r.data.trips.some(t => /BTC price below \$97,000 — BTC is now \$96,000/.test(t.body)));
  // Oldest first: ids ascend.
  const ids = r.data.trips.map(t => t.id);
  assert.deepEqual(ids, [...ids].sort((x, y) => x - y));
});

test('an ack stamps a row once — sent, or failed with its reason — and a second listing never carries it', async () => {
  const r0 = await pending();
  assert.ok(r0.data.trips.length >= 1);
  const [first, ...rest] = r0.data.trips;
  let r = await ack([{ id: first.id, ok: false, error: 'Forbidden' }]);
  assert.equal(r.status, 200);
  assert.deepEqual(r.data, { ok: true, acked: 1 });
  const r1 = await pending();
  assert.equal(r1.data.trips.length, rest.length, 'a failed send is stamped too, so it does not retry forever');
  assert.ok(!r1.data.trips.some(t => t.id === first.id));
  r = await ack([{ id: first.id, ok: true }]);
  assert.deepEqual(r.data, { ok: true, acked: 0 }, 'a stamped row is not stamped twice');
  r = await ack(rest.map(t => ({ id: t.id, ok: true })));
  assert.deepEqual(r.data, { ok: true, acked: rest.length });
  assert.equal((await pending()).data.trips.length, 0);
  r = await ack([{ id: 'junk' }, { id: -1, ok: true }, 'x']);
  assert.deepEqual(r.data, { ok: true, acked: 0 });
  r = await req('POST', '/api/bot/sync/alerts/ack', { botSecret: SECRET, body: { acks: 'junk' } });
  assert.deepEqual(r.data, { ok: true, acked: 0 });
});

test('the delivery sentence is the channel\'s, and the web sentences are the original ones byte for byte', () => {
  const once = { mode: 'once', metric: 'price', cooldown_min: 60 };
  const rec = { mode: 'recurring', metric: 'price', cooldown_min: 30 };
  const sig = { mode: 'recurring', metric: 'signal', cooldown_min: 0 };
  assert.equal(alerts.deliverySentence(once, 'web'),
    'I\'ll send you a push notification the moment it trips — one-shot, then it disarms.');
  assert.equal(alerts.deliverySentence(rec, 'web'), 'I\'ll push you each time it trips (at most once per 30 min).');
  assert.equal(alerts.deliverySentence(sig, 'web'), 'I\'ll push you every matching signal.');
  assert.equal(alerts.deliverySentence(once, 'telegram'),
    'I\'ll message you here the moment it trips — one-shot, then it disarms.');
  assert.equal(alerts.deliverySentence(rec, 'telegram'), 'I\'ll message you here each time it trips (at most once per 30 min).');
  assert.equal(alerts.deliverySentence(sig, 'telegram'), 'I\'ll message you here on every matching signal.');
});
