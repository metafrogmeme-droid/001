'use strict';
// A signal's outcome reaches the panels, and each word says what it claims.
//
// Both producers of the public signal stream pushed `status: NEW` and nothing
// ever pushed a second row, so the stats panel's promise that "outcomes appear
// once signals hit target or stop" described a path that did not exist. The
// bot now walks hourly candles from each signal's publication
// (bot/core/signal_outcomes.py) and re-sends the row with one of eight words.
// Five are final and only two carry an R; a panel that knew only WIN / LOSS
// kept offering a Trade button on a call whose window had closed, and a daily
// count of "resolved" beside "wins" would have read every unfilled call as a
// loss.
process.env.BOT_SYNC_SECRET = process.env.BOT_SYNC_SECRET || 's'.repeat(48);
process.env.JWT_SECRET = process.env.JWT_SECRET || 'j'.repeat(64);

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const express = require('express');

const SS = require('../public/js/signal-status-model.js');
const { pool } = require('../db');
const { dollarKeys } = require('../lib/public_signal');

const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({
  '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

// ── the model ───────────────────────────────────────────────────────────────

test('every word the walk writes is known, and exactly the final ones are final', () => {
  const words = ['NEW', 'OPEN', 'TARGET', 'STOP', 'AMBIGUOUS', 'EXPIRED', 'NO_EXIT', 'UNSCORED'];
  for (const w of words) {
    const st = SS.status({ status: w });
    assert.equal(st.known, true, w);
    assert.equal(st.final, SS.TERMINAL.includes(w), w);
  }
  assert.deepEqual([...SS.PENDING, ...SS.TERMINAL].sort(), [...words].sort());
});

test('the Python walk and the page agree on the words', () => {
  const src = fs.readFileSync(path.join(__dirname, '..', '..', 'bot', 'core', 'signal_outcomes.py'), 'utf8');
  const pending = /^PENDING = \(([^)]*)\)/m.exec(src)[1];
  const terminal = /^TERMINAL = \(([^)]*)\)/m.exec(src)[1];
  const names = (s) => s.split(',').map((x) => x.trim()).filter(Boolean);
  const lookup = {};
  for (const line of src.split('\n')) {
    const m = /^([A-Z_, ]+) = ("[A-Z_]+"(?:, "[A-Z_]+")*)$/.exec(line.trim());
    if (!m) continue;
    const ks = m[1].split(',').map((x) => x.trim());
    const vs = m[2].split(',').map((x) => x.trim().replace(/"/g, ''));
    ks.forEach((k, i) => { lookup[k] = vs[i]; });
  }
  assert.deepEqual(names(pending).map((n) => lookup[n]), SS.PENDING);
  assert.deepEqual(names(terminal).map((n) => lookup[n]).sort(), [...SS.TERMINAL].sort());
});

test('only the two words that carry an R get a colour', () => {
  for (const w of SS.PENDING.concat(SS.TERMINAL)) {
    const cls = SS.status({ status: w }).cls;
    if (w === 'TARGET') assert.equal(cls, 'chip--up');
    else if (w === 'STOP') assert.equal(cls, 'chip--down');
    else assert.equal(cls, '', w);
  }
});

test('an absent word is NEW, and a word the page does not know is final', () => {
  assert.equal(SS.status({}).word, 'NEW');
  assert.equal(SS.status({ status: '  open ' }).word, 'OPEN');
  const odd = SS.status({ status: 'LIQUIDATED' });
  assert.equal(odd.known, false);
  assert.equal(odd.final, true);
  assert.equal(odd.label, 'LIQUIDATED');
});

test('only a pending call with no outcome may be acted on', () => {
  assert.equal(SS.actionable({ status: 'NEW' }), true);
  assert.equal(SS.actionable({ status: 'OPEN' }), true);
  assert.equal(SS.actionable({}), true);
  for (const w of SS.TERMINAL) assert.equal(SS.actionable({ status: w }), false, w);
  assert.equal(SS.actionable({ status: 'LIQUIDATED' }), false);
  // An older server derives the outcome from the R and sends no word.
  assert.equal(SS.actionable({ status: 'NEW', outcome: 'WIN' }), false);
  assert.equal(SS.actionable({ status: 'NEW', outcome: 'FLAT' }), false);
  assert.equal(SS.actionable(null), false);
});

test('the mean R is a ratio with a sign, muted when flat or unread', () => {
  assert.equal(SS.avgR(0.9), '+0.90R');
  assert.equal(SS.avgR(-0.25), '−0.25R');
  assert.equal(SS.avgR(0), '0.00R');
  for (const v of [null, undefined, '', 'x', true, NaN]) assert.equal(SS.avgR(v), '—');
  assert.equal(SS.avgCls(0.1), 'pos');
  assert.equal(SS.avgCls(-0.1), 'neg');
  assert.equal(SS.avgCls(0), '');
  assert.equal(SS.avgCls(null), '');
});

test('the other outcomes are counted when they bite, and an unread count says so', () => {
  assert.equal(SS.otherLine({ NEW: 2, EXPIRED: 4, TARGET: 9, other: 0 }),
    '2 waiting for the entry · 4 not filled');
  assert.equal(SS.otherLine({ TARGET: 3 }), '');
  assert.equal(SS.otherLine({ other: 2 }), '2 in a word this page does not know');
  assert.equal(SS.otherLine(null), 'The other outcomes could not be counted.');
});

test('stats: nothing at all is the empty state, and tracking-only says so', () => {
  assert.equal(SS.statsHtml({ resolved: 0, by_status: { NEW: 0 } }, esc), null);
  assert.equal(SS.statsHtml(null, esc), null);
  const html = SS.statsHtml({ resolved: 0, by_status: { NEW: 3, EXPIRED: 1 } }, esc);
  assert.match(html, /None has reached its target or stop yet · 3 waiting for the entry · 1 not filled/);
  assert.match(html, /hourly candles/);
});

test('stats: a record prints the win rate, the gross mean R and what it is over', () => {
  const html = SS.statsHtml({ resolved: 3, wins: 2, losses: 1, flat: 0, win_rate: 66.7,
    avg_r: 0.9, by_status: { EXPIRED: 4, AMBIGUOUS: 1 } }, esc);
  assert.match(html, /66\.7%/);
  assert.match(html, /Avg R \(gross\)/);
  assert.match(html, /class="v pos">\+0\.90R/);
  assert.match(html, /2 \/ 1/);
  assert.match(html, /4 not filled · 1 ambiguous bar/);
  assert.match(html, /Gross of fees/);
  assert.deepEqual(dollarKeys({ html }), []);
  assert.ok(!/\$\s?\d/.test(html), 'a dollar figure on a public panel');
});

test('stats: an older server with no counts prints no line about them', () => {
  const html = SS.statsHtml({ resolved: 1, wins: 1, losses: 0, flat: 0, win_rate: 100 }, esc);
  assert.ok(!/could not be counted/.test(html));
  assert.match(html, /Avg R \(gross\)<\/div><div class="v ">—/);
});

test('stats: an escaper is required', () => {
  assert.throws(() => SS.statsHtml({ resolved: 1 }), /escaper/);
});

// ── the route ───────────────────────────────────────────────────────────────

let server, base;
test.before(async () => {
  const app = express();
  app.use(express.json());
  app.use('/api/bot/sync', require('../routes/sync'));
  app.use('/api/signals', require('../routes/signals'));
  await new Promise((res) => { server = app.listen(0, '127.0.0.1', res); });
  base = `http://127.0.0.1:${server.address().port}`;
});
test.after(() => { if (server) server.close(); });

function call(method, p, body) {
  return new Promise((resolve, reject) => {
    const payload = body ? JSON.stringify(body) : null;
    const r = http.request(base + p, { method, headers: {
      'Content-Type': 'application/json', 'X-Bot-Secret': process.env.BOT_SYNC_SECRET } }, (res) => {
      let d = ''; res.on('data', (c) => { d += c; });
      res.on('end', () => resolve({ status: res.statusCode, data: d ? JSON.parse(d) : {} }));
    });
    r.on('error', reject); if (payload) r.write(payload); r.end();
  });
}

const row = (k, extra = {}) => ({
  signal_key: k, symbol: 'BTC/USDT', direction: 'LONG', confidence: 0.7, score: 0.7,
  pattern: 'x', regime: 'TREND_UP', entry_price: 100, stop_loss: 95, take_profit: 110,
  rr: 2, thesis: 't', status: 'NEW', pnl: null,
  created_at: new Date().toISOString(), resolved_at: '', ...extra,
});

test('a re-sent outcome reaches the stats as counts and a mean R, never a dollar', async () => {
  pool.signals.length = 0;
  await call('POST', '/api/bot/sync/signals', { signals: [
    row('o1'), row('o2'), row('o3'), row('o4'), row('o5')] });
  const now = new Date().toISOString();
  // The bot's re-send: the SAME row with its outcome fields set.
  const r = await call('POST', '/api/bot/sync/signals', { signals: [
    row('o1', { status: 'TARGET', pnl: 2.0, resolved_at: now }),
    row('o2', { status: 'STOP', pnl: -1.0, resolved_at: now }),
    row('o3', { status: 'EXPIRED', pnl: null, resolved_at: now }),
    row('o4', { status: 'OPEN' })] });
  assert.equal(r.status, 200);
  const s = (await call('GET', '/api/signals/stats')).data;
  assert.equal(s.resolved, 2);
  assert.equal(s.wins, 1);
  assert.equal(s.losses, 1);
  assert.equal(s.win_rate, 50);
  assert.equal(s.avg_r, 0.5);
  assert.equal(s.r_basis, 'gross');
  assert.deepEqual(s.by_status, { other: 0, NEW: 1, OPEN: 1, TARGET: 1, STOP: 1,
    AMBIGUOUS: 0, EXPIRED: 1, NO_EXIT: 0, UNSCORED: 0 });
  assert.deepEqual(dollarKeys(s), []);
});

test('the re-send never touches the seal', async () => {
  pool.signals.length = 0;
  await call('POST', '/api/bot/sync/signals', { signals: [row('z1')] });
  const seal = pool.signals.find((x) => x.signal_key === 'z1').seal;
  await call('POST', '/api/bot/sync/signals', { signals: [
    row('z1', { status: 'TARGET', pnl: 2, resolved_at: new Date().toISOString(), confidence: 0.1 })] });
  const stored = pool.signals.find((x) => x.signal_key === 'z1');
  assert.equal(stored.seal, seal);
  assert.equal(stored.status, 'TARGET');
  assert.equal(Number(stored.pnl), 2);
});

test('nothing resolved is a null mean, and a word nobody knows is counted as other', async () => {
  pool.signals.length = 0;
  await call('POST', '/api/bot/sync/signals', { signals: [row('n1', { status: 'LIQUIDATED' })] });
  const s = (await call('GET', '/api/signals/stats')).data;
  assert.equal(s.resolved, 0);
  assert.equal(s.avg_r, null);
  assert.equal(s.by_status.other, 1);
});

test('counts that could not be read are null, not zeros, and the record still reads', async () => {
  const real = pool.execute.bind(pool);
  pool.execute = async (sql, params) => {
    if (/GROUP BY status/.test(sql)) throw new Error('ER_LOCK_WAIT_TIMEOUT');
    return real(sql, params);
  };
  try {
    const r = await call('GET', '/api/signals/stats');
    assert.equal(r.status, 200);
    assert.equal(r.data.by_status, null);
  } finally {
    pool.execute = real;
  }
});

// ── the daily count ─────────────────────────────────────────────────────────

test('the day counts calls that reached a level, not every call that ended', async () => {
  pool.signals.length = 0;
  const now = new Date().toISOString();
  await call('POST', '/api/bot/sync/signals', { signals: [
    row('d1', { status: 'TARGET', pnl: 1.5, resolved_at: now }),
    row('d2', { status: 'EXPIRED', pnl: null, resolved_at: now }),
    row('d3', { status: 'AMBIGUOUS', pnl: null, resolved_at: now }),
    row('d4', { status: 'STOP', pnl: -1, resolved_at: now })] });
  const { fetchToday } = require('../lib/daily_rune');
  const parts = await fetchToday();
  const sig = parts.signals || (parts.parts && parts.parts.signals);
  assert.ok(sig, 'the day carried no signal block');
  assert.equal(sig.resolved_today, 2);
  assert.equal(sig.wins_today, 1);
});

// ── the dashboard reads the model ───────────────────────────────────────────

const { codeOnly } = require('./helpers/code_only');
const DASH = fs.readFileSync(path.join(__dirname, '..', 'public', 'js', 'dashboard.js'), 'utf8');

test('the stream offers Trade and Paper only for an actionable call', () => {
  const src = codeOnly(DASH);
  const a = src.indexOf("renderPanel(C('stream')");
  const b = src.indexOf("text: 'No signals yet.", a);
  assert.ok(a > 0 && b > a, 'the stream panel moved');
  const block = src.slice(a, b);
  assert.match(block, /const live = SS \? SS\.actionable\(s\) : false;/);
  assert.match(block, /const canTrade = live && s\.entry_price/);
  assert.match(block, /const arenaBtn = live && s\.signal_key/);
  assert.ok(!/s\.outcome == null/.test(block), 'a button decided by the outcome label alone');
});

test('the stats panel is the model\'s, and a missing model is a failed read', () => {
  const src = codeOnly(DASH);
  const a = src.indexOf("renderPanel(C('sstats')");
  const block = src.slice(a, src.indexOf('async function drawStream', a));
  assert.match(block, /return SS\.statsHtml\(r\.data, esc\);/);
  assert.match(block, /throw new Error\('signal-status-model\.js did not load'\)/);
});

test('the page loads the model before the dashboard', () => {
  const html = fs.readFileSync(path.join(__dirname, '..', 'public', 'dashboard.html'), 'utf8');
  const m = html.indexOf('/js/signal-status-model.js?v=');
  const d = html.indexOf('/js/dashboard.js?v=');
  assert.ok(m > 0 && d > m);
});
