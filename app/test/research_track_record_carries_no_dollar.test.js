'use strict';
/**
 * The research dossier's "Agent track record here" section published the
 * operator's realized net P&L in DOLLARS, per coin, to anyone.
 *
 * Driven on 2026-09-26 with two operator PENDLE closes planted (+612.40 and
 * -140.15): a freshly registered stranger's GET /api/research/pendle, an
 * ANONYMOUS POST /mcp tools/call research_token and an ANONYMOUS POST
 * /api/tool/invoke research_token each answered
 *
 *     The agent has closed 2 trade(s) on PENDLE: 1W/1L, net +$472.25.
 *
 * under a source label reading "public track record data" -- and the public
 * track record (/track) is percent, ratio and count only. The same dossier
 * reaches the web chat's research intercept and Telegram's /research through
 * the bot's sync route, so one lib line was five doors.
 *
 * The record is a W/L count and the profit factor now (a ratio; none over no
 * loss), and this drives every door with the same planted closes and asserts
 * the operator's dollars cannot be read back off any of them.
 *
 * `public_no_dollars.test.js` could not see this and still cannot: it scans
 * ROUTE files for emitted money KEYS, and this was a dollar figure inside an
 * HTML string built in a LIB. That guard states the lib half as its own scope
 * limit; this file is the check made where the string is built.
 */
process.env.JWT_SECRET = 'j'.repeat(64);
process.env.BOT_SYNC_SECRET = 's'.repeat(48);
delete process.env.DATABASE_URL;
delete process.env.WEB_GATEWAY_SECRET;

const test = require('node:test');
const assert = require('node:assert/strict');
const http = require('node:http');
const express = require('express');
const authModule = require('../auth');
const { pool } = require('../db');
const research = require('../lib/research');

const OPERATOR = parseInt(process.env.BOT_USER_ID) || 1;
const TITLE = 'Agent track record here';
// Gross win 612.40 over gross loss 140.15.
const PF = (612.40 / 140.15).toFixed(2);   // "4.37"

let server, base;

function req(method, path, { token, body, botSecret } = {}) {
  return new Promise((resolve, reject) => {
    const payload = body ? JSON.stringify(body) : null;
    const r = http.request(`${base}${path}`, {
      method,
      headers: {
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
        ...(botSecret ? { 'X-Bot-Secret': botSecret } : {}),
        ...(payload ? { 'Content-Type': 'application/json' } : {}),
      },
    }, (res) => {
      let d = '';
      res.on('data', (c) => { d += c; });
      res.on('end', () => {
        let data;
        try { data = JSON.parse(d); } catch (e) { data = d; }
        resolve({ status: res.statusCode, data });
      });
    });
    r.on('error', reject);
    if (payload) r.write(payload);
    r.end();
  });
}

async function register(prefix) {
  const r = await req('POST', '/api/auth/register', {
    body: { email: `${prefix}${Date.now()}@example.com`, password: 'x'.repeat(12) } });
  assert.ok(r.data.token, JSON.stringify(r.data));
  return r.data.token;
}

async function close(pnl) {
  await pool.execute(
    `INSERT INTO trades (user_id, symbol, direction, entry_price, exit_price,
      size_usd, pnl, fees, status, pattern, opened_at, closed_at)
     VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'CLOSED', ?, ?, ?)`,
    [OPERATOR, 'PENDLE/USDT', 'LONG', 3.0, 3.3, 5000, pnl, 1, null,
     new Date(Date.now() - 86400000), new Date()]);
}

test.before(async () => {
  global.fetch = async (url) => { throw new Error(`network disabled in test: ${url}`); };
  research.setTickerFetcher(async () => ({
    PENDLEUSDT: { price: 3.5, change: 4, volume: 5e7 },
  }));
  require('../lib/token_safety').setPairSearcher(async () => null);

  const app = express();
  app.use(express.json());
  app.use('/api/auth', authModule.router);
  app.use('/api/research', require('../routes/research'));
  app.use('/mcp', require('../routes/mcp'));
  app.use('/api/bot/sync', require('../routes/sync'));
  app.use('/', require('../routes/tool8257'));
  await new Promise((res) => { server = app.listen(0, '127.0.0.1', res); });
  base = `http://127.0.0.1:${server.address().port}`;

  await register('op');           // the first account is the operator (id 1)
  await close(612.40);
  await close(-140.15);
});

test.after(() => { if (server) server.close(); });

/** The section's text as the reader gets it, whatever the envelope. */
function trackText(dossier) {
  const s = (dossier.sections || []).find((x) => x.title === TITLE);
  assert.ok(s, `no "${TITLE}" section in ${JSON.stringify(dossier).slice(0, 300)}`);
  return s;
}

function assertNoOperatorDollars(where, s) {
  const text = `${s.html} ${s.source}`;
  assert.ok(!/\$/.test(text), `${where}: a dollar sign on the track record: ${text}`);
  for (const figure of ['472', '612', '140']) {
    assert.ok(!text.includes(figure),
      `${where}: the operator's P&L (${figure}) is readable off the card: ${text}`);
  }
  assert.match(s.html, /closed <b>2<\/b> trade/, `${where}: the count is part of the record`);
  assert.match(s.html, /1W\/1L/, `${where}: the win/loss count is part of the record`);
  assert.ok(s.html.includes(`profit factor <b>${PF}</b>`),
    `${where}: the ratio must survive -- it carries the signal the dollar figure did: ${s.html}`);
  assert.ok(!/public track record data/.test(s.source),
    `${where}: the source label claimed the public record while printing dollars`);
  assert.match(s.source, /no dollar figures/, `${where}: the label says what is shown`);
}

test('a signed-in stranger reads a count and a ratio, never the operator dollars', async () => {
  const token = await register('stranger');
  const r = await req('GET', '/api/research/pendle', { token });
  assert.equal(r.status, 200);
  assertNoOperatorDollars('GET /api/research', trackText(r.data));
});

test('the anonymous MCP tool call carries no dollar figure', async () => {
  const r = await req('POST', '/mcp', { body: { jsonrpc: '2.0', id: 1, method: 'tools/call',
    params: { name: 'research_token', arguments: { symbol: 'PENDLE' } } } });
  assert.equal(r.status, 200);
  const text = r.data.result.content.map((c) => c.text).join('');
  const d = JSON.parse(text);
  assert.equal(d.listed, true);
  assertNoOperatorDollars('POST /mcp research_token', trackText(d));
});

test('the anonymous tool-invoke door carries no dollar figure', async () => {
  const r = await req('POST', '/api/tool/invoke',
    { body: { tool: 'research_token', args: { symbol: 'PENDLE' } } });
  assert.equal(r.status, 200);
  assertNoOperatorDollars('POST /api/tool/invoke', trackText(r.data.result));
});

test('the bot sync route (Telegram /research) carries no dollar figure', async () => {
  const r = await req('GET', '/api/bot/sync/research/PENDLE',
    { botSecret: process.env.BOT_SYNC_SECRET });
  assert.equal(r.status, 200);
  assertNoOperatorDollars('GET /api/bot/sync/research', trackText(r.data));
});

test('the web chat intercept carries no dollar figure', async () => {
  const out = await research.maybeHandleResearchChat(null, 'research PENDLE');
  assert.ok(out && out.reply_html, 'the intercept did not answer');
  const html = out.reply_html;
  const i = html.indexOf(TITLE);
  assert.ok(i >= 0, html);
  const part = html.slice(i, html.indexOf('<br><br>', i));
  assert.ok(!/\$/.test(part), `a dollar sign on the chat card's track record: ${part}`);
  assert.ok(!/472|612|140/.test(part), `the operator's P&L is readable: ${part}`);
  assert.ok(part.includes(`profit factor <b>${PF}</b>`), part);
});

test('a record with no losing close has no profit factor, and says so', async () => {
  research.setTickerFetcher(async () => ({ QQQXUSDT: { price: 2, change: 1, volume: 1e6 } }));
  try {
    await pool.execute(
      `INSERT INTO trades (user_id, symbol, direction, entry_price, exit_price,
        size_usd, pnl, fees, status, pattern, opened_at, closed_at)
       VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'CLOSED', ?, ?, ?)`,
      [OPERATOR, 'QQQX/USDT', 'LONG', 1, 1.1, 100, 25, 1, null,
       new Date(Date.now() - 86400000), new Date()]);
    const s = trackText(await research.buildDossier('QQQX'));
    assert.match(s.html, /1W\/0L/);
    assert.match(s.html, /no losing close on record, so no profit factor/);
    assert.ok(!/profit factor <b>/.test(s.html), 'a ratio over no loss is not a number');
    assert.ok(!/\$|25/.test(s.html), s.html);
  } finally {
    research.setTickerFetcher(async () => ({
      PENDLEUSDT: { price: 3.5, change: 4, volume: 5e7 },
    }));
  }
});

test('a measured flat close is counted as flat, not as a win or a loss', async () => {
  research.setTickerFetcher(async () => ({ FLATXUSDT: { price: 2, change: 1, volume: 1e6 } }));
  try {
    for (const pnl of [30, -10, 0]) {
      await pool.execute(
        `INSERT INTO trades (user_id, symbol, direction, entry_price, exit_price,
          size_usd, pnl, fees, status, pattern, opened_at, closed_at)
         VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'CLOSED', ?, ?, ?)`,
        [OPERATOR, 'FLATX/USDT', 'LONG', 1, 1.1, 100, pnl, 1, null,
         new Date(Date.now() - 86400000), new Date()]);
    }
    const s = trackText(await research.buildDossier('FLATX'));
    assert.match(s.html, /1W\/1L \(1 flat\)/, s.html);
    assert.match(s.html, /profit factor <b>3\.00<\/b>/, s.html);
  } finally {
    research.setTickerFetcher(async () => ({
      PENDLEUSDT: { price: 3.5, change: 4, volume: 5e7 },
    }));
  }
});
