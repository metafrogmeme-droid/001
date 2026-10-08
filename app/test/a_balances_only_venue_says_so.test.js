/**
 * A venue this bot places no order on says so wherever it is listed.
 *
 * Asked 8 October: link Bybit EU for balances. The operator's Bybit key had
 * answered 10003 on bybit.com because it was a Bybit EU key, and Bybit EU
 * offers spot only, no perpetual futures. So Bybit EU joins as a venue that is
 * linked and read and never traded, as OKX, Gate, KuCoin and Paradex already
 * were.
 *
 * Every connected venue was listed under "Venues that trade" with nothing
 * beside it, balances-only ones included. `orders` on each venue in
 * app/lib/venues.js now says whether an order can route there, the status
 * route carries it per row, and the picker says "linked for balances only".
 *
 * The web list and the bot's are two lists in two languages, so they are held
 * equal here: the venue ids against `_VENUE_FIELDS`
 * (bot/core/exchange_credentials.py), and the venues that take orders against
 * `PER_USER_EXECUTION_VENUES` (bot/core/venues.py).
 *
 * Run: npm test  (node --test test/)
 */
'use strict';

process.env.JWT_SECRET = 'j'.repeat(64);
process.env.BOT_SYNC_SECRET = 's'.repeat(48);
process.env.WEB_CREDS_KEY = Buffer.alloc(32, 7).toString('base64');

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const http = require('node:http');
const path = require('node:path');
const jwt = require('jsonwebtoken');

const { VENUES, isVenue, venueTakesOrders } = require('../lib/venues');
const Picker = require('../public/js/venue-picker-model.js');
const { codeOnly } = require('./helpers/code_only');

const ROOT = path.join(__dirname, '..', '..');
const py = (rel) => fs.readFileSync(path.join(ROOT, rel), 'utf8');

/** The keys of `_VENUE_FIELDS` in the bot's credential store. */
function botVenueIds() {
  const src = py('bot/core/exchange_credentials.py');
  const start = src.indexOf('_VENUE_FIELDS: dict[str, tuple[str, ...]] = {');
  assert.ok(start > 0, '_VENUE_FIELDS is gone from exchange_credentials.py');
  const body = src.slice(start, src.indexOf('\n}', start));
  return [...body.matchAll(/^\s+"([a-z0-9]+)":\s*\(/gm)].map((m) => m[1]);
}

/** The bot's `PER_USER_EXECUTION_VENUES`. */
function botOrderVenues() {
  const m = py('bot/core/venues.py').match(/^PER_USER_EXECUTION_VENUES = frozenset\(\{([^}]*)\}\)/m);
  assert.ok(m, 'PER_USER_EXECUTION_VENUES is gone from venues.py');
  return [...m[1].matchAll(/"([a-z0-9]+)"/g)].map((x) => x[1]);
}

test('the website and the bot list the same venues', () => {
  const bot = botVenueIds();
  assert.ok(bot.length >= 9 && bot.includes('bybiteu'), bot.join(','));
  assert.deepEqual(VENUES.map((v) => v.id).sort(), [...bot].sort());
});

test('the website and the bot agree on which venues take an order', () => {
  const bot = botOrderVenues();
  assert.ok(bot.length >= 2, `parsed ${bot.length} venues — the parser read nothing`);
  assert.deepEqual(VENUES.filter((v) => v.orders === true).map((v) => v.id).sort(), [...bot].sort());
  // Every venue says it, one way or the other: a missing `orders` is not false.
  for (const v of VENUES) assert.equal(typeof v.orders, 'boolean', v.id);
});

test('Bybit EU is connectable, spot, balances only, and says so in its help', () => {
  assert.ok(isVenue('bybiteu'));
  const v = VENUES.find((x) => x.id === 'bybiteu');
  assert.equal(v.label, 'Bybit EU');
  assert.equal(v.market, 'spot');
  assert.deepEqual(v.fields.map((f) => f.key), ['api_key', 'api_secret']);
  assert.match(v.help, /^Balances only\./);
  assert.match(v.help, /no order is placed there/);
  assert.match(v.help, /withdrawals disabled/i);
});

test('venueTakesOrders: true, false, and null for a venue nobody listed', () => {
  assert.equal(venueTakesOrders('bitget'), true);
  assert.equal(venueTakesOrders('bybit'), true);
  assert.equal(venueTakesOrders('bybiteu'), false);
  assert.equal(venueTakesOrders('okx'), false);
  assert.equal(venueTakesOrders('ftx'), null, 'unknown is not "balances only"');
});

// ── the picker ──────────────────────────────────────────────────────────────

test('the picker marks a balances-only venue, and only that one', () => {
  const status = { venues: ['bitget'], venues_pending: null, venues_mode: 'off' };
  const s = Picker.pickerState(status, [
    { venue: 'bitget', connected: true, orders: true },
    { venue: 'bybiteu', connected: true, orders: false },
  ]);
  const by = Object.fromEntries(s.rows.map((r) => [r.venue, r]));
  assert.equal(by.bybiteu.balancesOnly, true);
  assert.equal(by.bitget.balancesOnly, false);
  // A row that does not carry the field (an older server) says nothing.
  const old = Picker.pickerState(status, [{ venue: 'okx', connected: true }]);
  assert.equal(old.rows.find((r) => r.venue === 'okx').balancesOnly, false);
  // A selected venue that is no longer connected keeps the mark it was sent.
  const gone = Picker.pickerState({ venues: ['bybiteu'], venues_pending: null, venues_mode: 'off' },
    [{ venue: 'bybiteu', connected: false, orders: false }]);
  assert.deepEqual(gone.rows.map((r) => [r.venue, r.disconnected, r.balancesOnly]),
    [['bybiteu', true, true]]);
});

test('the picker prints the balances-only note beside the venue', () => {
  const dash = codeOnly(fs.readFileSync(path.join(__dirname, '..', 'public', 'js', 'dashboard.js'), 'utf8'));
  const fn = dash.slice(dash.indexOf('function venuePickerHtml('), dash.indexOf("renderPanel(C('actl')"));
  assert.ok(fn.length > 100, 'venuePickerHtml was not found');
  assert.match(fn, /r\.balancesOnly\s*\?/);
  assert.match(fn, /T\('venue\.balances_only'/);
});

// ── driven: the real status route on the in-memory store ────────────────────

let server, base, token, uid;

function request(method, p, { token: tk, body } = {}) {
  return new Promise((resolve, reject) => {
    const payload = body ? JSON.stringify(body) : null;
    const r = http.request(`${base}${p}`, {
      method,
      headers: {
        ...(tk ? { Authorization: `Bearer ${tk}` } : {}),
        ...(payload ? { 'Content-Type': 'application/json' } : {}),
      },
    }, (res) => {
      let d = '';
      res.on('data', (c) => { d += c; });
      res.on('end', () => resolve({ status: res.statusCode, data: d ? JSON.parse(d) : {} }));
    });
    r.on('error', reject);
    if (payload) r.write(payload);
    r.end();
  });
}

test('the status route says, per venue, whether an order can route there', async (t) => {
  const { pool } = require('../db');
  await pool.execute('INSERT INTO users (email, password_hash, name) VALUES (?, ?, ?)',
    ['eu@test.io', 'x', 'E']);
  const [rows] = await pool.execute('SELECT id FROM users WHERE email = ?', ['eu@test.io']);
  uid = rows[0].id;
  await pool.execute('UPDATE users SET telegram_id = ? WHERE id = ?', ['9004', uid]);
  const [u] = await pool.execute('SELECT * FROM users WHERE id = ?', [uid]);
  u[0].telegram_linked = true; // MemoryDB: set the flag directly
  token = jwt.sign({ user_id: uid, email: 'eu@test.io' }, process.env.JWT_SECRET);
  const express = require('express');
  const app = express();
  app.use(express.json());
  app.use('/api/credentials', require('../routes/credentials'));
  await new Promise((res) => { server = app.listen(0, '127.0.0.1', res); });
  t.after(() => server.close());
  base = `http://127.0.0.1:${server.address().port}`;

  // The form takes a Bybit EU key: the route validates against venues.js.
  const sub = await request('POST', '/api/credentials', {
    token, body: { venue: 'bybiteu', api_key: 'k'.repeat(18), api_secret: 's'.repeat(36) },
  });
  assert.equal(sub.status, 200, JSON.stringify(sub.data));

  for (const v of ['bitget', 'bybiteu']) {
    await pool.execute(
      `INSERT INTO exchange_venue_status (user_id, exchange, connected, last_error)
       VALUES (?, ?, ?, NULL) ON DUPLICATE KEY UPDATE connected = VALUES(connected)`, [uid, v, true]);
  }
  const s = await request('GET', '/api/credentials/status', { token });
  assert.equal(s.status, 200);
  const by = Object.fromEntries(s.data.venues.map((r) => [r.venue, r]));
  assert.equal(by.bitget.orders, true);
  assert.equal(by.bybiteu.orders, false);
  assert.equal(s.data.pending_venue, 'bybiteu');
});
