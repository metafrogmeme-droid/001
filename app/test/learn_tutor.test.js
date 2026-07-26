'use strict';
/**
 * Study Room tutor — the contract under test:
 * - Market-advice questions are refused DETERMINISTICALLY before any model
 *   call (the mock gateway must see nothing).
 * - Lesson questions pass the filter — the refusal must not over-block the
 *   very questions the room exists for.
 * - The prompt that actually reaches the LLM channel carries the rules, the
 *   lesson texts and the question — grounding verified end-to-end.
 * - Every answer ships labeled ai: true with sources and the check-the-
 *   source note.
 */
process.env.JWT_SECRET = 'j'.repeat(64);
delete process.env.DATABASE_URL;

const test = require('node:test');
const assert = require('node:assert');
const http = require('node:http');
const express = require('express');
const jwt = require('jsonwebtoken');

let server, base, tok, mock, gatewaySeen = [];

function req(method, p, body, tokIn) {
  return new Promise((resolve, reject) => {
    const payload = body ? JSON.stringify(body) : null;
    const r = http.request(`${base}${p}`, {
      method,
      headers: {
        ...(payload ? { 'Content-Type': 'application/json' } : {}),
        ...(tokIn ? { Authorization: `Bearer ${tokIn}` } : {}),
      },
    }, (res) => {
      let d = '';
      res.on('data', (c) => d += c);
      res.on('end', () => resolve({ status: res.statusCode, data: d ? JSON.parse(d) : {} }));
    });
    r.on('error', reject);
    if (payload) r.write(payload);
    r.end();
  });
}

test.before(async () => {
  // Mock bot gateway FIRST — lib/gateway reads its env at require time.
  mock = http.createServer((rq, rs) => {
    let d = '';
    rq.on('data', (c) => d += c);
    rq.on('end', () => {
      gatewaySeen.push({ url: rq.url, body: d ? JSON.parse(d) : {} });
      rs.setHeader('Content-Type', 'application/json');
      rs.end(JSON.stringify({ reply: 'Grounded answer: the clamp is the margin itself (stops-and-risk).' }));
    });
  });
  await new Promise((res) => mock.listen(0, '127.0.0.1', res));
  process.env.BOT_GATEWAY_URL = `http://127.0.0.1:${mock.address().port}`;
  process.env.WEB_GATEWAY_SECRET = 's'.repeat(40);

  const { pool } = require('../db');
  await pool.execute('INSERT INTO users (email, password_hash) VALUES (?, ?)',
    ['tutor@test.io', 'x'.repeat(60)]);
  const [u] = await pool.execute('SELECT * FROM users WHERE email = ?', ['tutor@test.io']);
  tok = jwt.sign({ user_id: u[0].id, email: u[0].email }, process.env.JWT_SECRET);

  const app = express();
  app.use(express.json());
  app.use('/api/learn', require('../routes/learn'));
  await new Promise((res) => { server = app.listen(0, '127.0.0.1', res); });
  base = `http://127.0.0.1:${server.address().port}`;
});

test.after(() => { if (server) server.close(); if (mock) mock.close(); });

test('the advice filter: refuses market calls, passes lesson questions', () => {
  const { adviceAsked } = require('../lib/learn_tutor');
  for (const q of ['Should I buy ETH?', 'which coin should I buy', 'price prediction for BTC',
    'will btc pump tomorrow', 'best coin to buy now', 'worth it to long sol here?']) {
    assert.equal(adviceAsked(q), true, `must refuse: ${q}`);
  }
  for (const q of ['Should I use a trailing stop?', 'why does a stop beyond liquidation cap at margin?',
    'what is portfolio heat?', 'how do I size for 1% risk', 'when is bridging dust uneconomical?']) {
    assert.equal(adviceAsked(q), false, `must pass: ${q}`);
  }
});

test('refusal happens BEFORE the model: the gateway sees nothing', async () => {
  gatewaySeen = [];
  const r = await req('POST', '/api/learn/tutor', { question: 'should I buy ETH now?' }, tok);
  assert.equal(r.status, 200);
  assert.equal(r.data.refused, true);
  assert.equal(r.data.ai, false, 'a refusal is deterministic, not model output');
  assert.equal(gatewaySeen.length, 0, 'no token was spent refusing');
});

test('grounding, end-to-end: rules + lesson text + question reach the LLM channel', async () => {
  gatewaySeen = [];
  const r = await req('POST', '/api/learn/tutor',
    { question: 'why does a stop beyond liquidation cap at margin?' }, tok);
  assert.equal(r.status, 200);
  assert.match(r.data.answer, /Grounded answer/);
  assert.equal(r.data.ai, true);
  assert.equal(r.data.grounded, true);
  assert.ok(r.data.sources.includes('stops-and-risk'));
  assert.match(r.data.note, /check the source lesson/);
  assert.match(r.data.note, /never trading advice/);

  assert.equal(gatewaySeen.length, 1);
  const sent = gatewaySeen[0].body.text;
  assert.match(sent, /ONLY the lesson texts/, 'the rules ride in the prompt');
  assert.match(sent, /NEVER give trading advice/);
  assert.match(sent, /the lessons do not cover that yet/, 'uncovered → say so, never guess');
  assert.match(sent, /margin × leverage/, 'the actual lesson text is the grounding');
  assert.match(sent, /STUDENT QUESTION: why does a stop beyond liquidation cap at margin\?/);
});

test('a slug narrows the grounding to that lesson', () => {
  const { buildPrompt } = require('../lib/learn_tutor');
  const one = buildPrompt('what is dust?', 'bridges-gas-dust');
  assert.deepEqual(one.sources, ['bridges-gas-dust']);
  assert.match(one.prompt, /uneconomical to move/);
  assert.doesNotMatch(one.prompt, /margin × leverage/, 'other lessons ride as titles only');
  const all = buildPrompt('what is dust?', null);
  assert.ok(all.sources.length >= 3, 'no slug → the whole (small) shelf grounds the answer');
});

test('auth + validation: private, bounded, honest', async () => {
  assert.equal((await req('POST', '/api/learn/tutor', { question: 'x' })).status, 401);
  assert.equal((await req('POST', '/api/learn/tutor', { question: '' }, tok)).status, 400);
  assert.equal((await req('POST', '/api/learn/tutor', { question: 'q'.repeat(501) }, tok)).status, 400);
  const st = await req('GET', '/api/learn/tutor/status', null, tok);
  assert.deepEqual(st.data, { ready: true });
});

test('the page: tutor hidden until the channel answers, output labeled AI', () => {
  const fs = require('node:fs');
  const path = require('node:path');
  const page = fs.readFileSync(path.join(__dirname, '..', 'public', 'learn.html'), 'utf8');
  assert.match(page, /tutor\/status/, 'silence beats teasing a box that cannot reply');
  assert.match(page, /ln\.tutor_ai/, 'every answer wears the AI label');
  assert.match(page, /ln\.tutor_refuse/, 'the refusal is translated client-side by code');
  assert.match(page, /esc\(String\(r\.data\.answer\)\)/, 'model output is escaped, never trusted as html');
  const i18n = require('../public/js/i18n.js');
  for (const k of ['ln.tutor_h', 'ln.tutor_ph', 'ln.ask', 'ln.tutor_refuse', 'ln.tutor_ai', 'ln.tutor_fail']) {
    for (const l of i18n.LANGS) {
      assert.ok(String(i18n.STRINGS[k][l.code] || '').trim().length, `${k} missing ${l.code}`);
    }
  }
});
