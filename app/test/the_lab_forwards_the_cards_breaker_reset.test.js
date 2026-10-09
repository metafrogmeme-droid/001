'use strict';
/**
 * "Reproduce in Lab" re-runs a card with the breaker reset the card was
 * measured with.
 *
 * The cards are recorded with a tripped breaker reset after 24 bars, as an
 * operator would (`scripts/gen_agent_scorecards.py::CARD_BREAKER_RESET_BARS`).
 * The Lab's request had no field for it, so a reproduce ran the runner's
 * default of 0: Full Scan halts at its first trip and re-runs as a fraction of
 * the trades its card shows, under the card's name.
 *
 * Driven: the real `labBody` builds the request from a card, the real route
 * relays it to a mock bridge, and the test reads what the bridge received.
 */
process.env.JWT_SECRET = process.env.JWT_SECRET || 'j'.repeat(64);

const test = require('node:test');
const assert = require('node:assert/strict');
const http = require('node:http');
const jwt = require('jsonwebtoken');

const { pool } = require('../db');
const { labBody } = require('../public/js/agent-scorecard.js');

const seen = [];
let bridge, server, base, token;

function post(p, body) {
  return new Promise((resolve, reject) => {
    const payload = JSON.stringify(body);
    const r = http.request(`${base}${p}`, {
      method: 'POST',
      headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
    }, (res) => {
      let d = '';
      res.on('data', (c) => { d += c; });
      res.on('end', () => resolve({ status: res.statusCode, data: d ? JSON.parse(d) : {} }));
    });
    r.on('error', reject);
    r.write(payload);
    r.end();
  });
}

test.before(async () => {
  bridge = http.createServer((req, res) => {
    let d = '';
    req.on('data', (c) => { d += c; });
    req.on('end', () => {
      seen.push({ url: req.url, body: d ? JSON.parse(d) : null });
      res.setHeader('Content-Type', 'application/json');
      res.end(JSON.stringify({ job_id: 'abc123', status: 'running' }));
    });
  });
  await new Promise((res) => bridge.listen(0, '127.0.0.1', res));
  process.env.BOT_API_URL = `http://127.0.0.1:${bridge.address().port}`;

  await pool.execute('INSERT INTO users (email, password_hash, name) VALUES (?, ?, ?)',
    ['lab-breaker@test.io', 'x', 'L']);
  const [rows] = await pool.execute('SELECT id FROM users WHERE email = ?', ['lab-breaker@test.io']);
  token = jwt.sign({ user_id: rows[0].id, email: 'lab-breaker@test.io' }, process.env.JWT_SECRET);

  const express = require('express');
  const app = express();
  app.use(express.json());
  app.use('/api/lab', require('../routes/lab'));
  await new Promise((res) => { server = app.listen(0, '127.0.0.1', res); });
  base = `http://127.0.0.1:${server.address().port}`;
});

test.after(() => {
  if (server) server.close();
  if (bridge) bridge.close();
});

async function relayed(body) {
  seen.length = 0;
  const r = await post('/api/lab/run', body);
  assert.equal(r.status, 200, JSON.stringify(r.data));
  assert.equal(seen.length, 1);
  assert.equal(seen[0].url, '/lab/run');
  return seen[0].body;
}

const CARD = {
  dataset: 'majors_1h', symbols: ['BTC/USDT:USDT'], bars: 1500, gates: {},
  breaker: { reset_bars: 24, trips: 3 },
};

test('a card with a recorded reset reaches the bridge with it', async () => {
  const sent = await relayed(labBody('Full Scan', CARD).body);
  assert.equal(sent.breaker_reset_bars, 24);
});

test('a card from before the block sends none, so the Lab runs 0 as that card did', async () => {
  const { breaker, ...old } = CARD;
  const sent = await relayed(labBody('Full Scan', old).body);
  assert.equal(Object.prototype.hasOwnProperty.call(sent, 'breaker_reset_bars'), false);
});

test('a reset typed as text is read as its number', async () => {
  const sent = await relayed({ dataset: 'majors_1h', breaker_reset_bars: '24' });
  assert.equal(sent.breaker_reset_bars, 24);
});

test('a value that is not a number is forwarded as written for the bot to refuse, never as no reset', async () => {
  const sent = await relayed({ dataset: 'majors_1h', breaker_reset_bars: 'abc' });
  // NaN would have been written as null, and null is a run at 0.
  assert.equal(sent.breaker_reset_bars, 'abc');
});
