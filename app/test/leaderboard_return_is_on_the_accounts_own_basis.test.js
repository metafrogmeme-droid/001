'use strict';
/**
 * The leaderboard's "return %" was the dollar P&L divided by a constant.
 *
 *     return_pct = Math.round((net_pnl / 10000) * 10000) / 100
 *
 * so `return_pct * 100` was the member's realized P&L in whole dollars, on a
 * board whose own panel says "never ... any dollar amount". Driven on
 * 2026-09-26: an opted-in member with closes of +412.37, -95.12 and +23.40
 * was shown to another signed-in account at 3.41%, and 3.41 x 100 is 341 --
 * the 340.65 the member had made, to the dollar. A ratio to a constant is a
 * dollar figure with the units moved.
 *
 * The return is measured on the account's OWN starting equity now, derived the
 * way the reputation route derives it (the latest equity snapshot minus the
 * realized net), and a member with no basis is LISTED by trades and win rate
 * with `return_pct: null` and no rank -- never divided by a constant, and
 * never ranked as a flat book because `null - x` reads null as 0.
 *
 * The route is driven against the in-memory database; the table renderer is
 * sliced out of dashboard.js (it is inline in a panel loader, so a VM run is
 * the drive available) and run with the real `signed` / `pnlClass` / `esc` /
 * `fmt` from app.js.
 */
process.env.JWT_SECRET = 'j'.repeat(64);
delete process.env.DATABASE_URL;

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const http = require('node:http');
const express = require('express');
const authModule = require('../auth');
const { pool } = require('../db');
const { codeOnly } = require('./helpers/code_only');
const { loaderBodies } = require('./helpers/loaders');

let server, base;

function req(method, p, { token, body } = {}) {
  return new Promise((resolve, reject) => {
    const payload = body ? JSON.stringify(body) : null;
    const r = http.request(`${base}${p}`, {
      method,
      headers: {
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
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

let n = 0;
async function member(handle, { closes = [], equity = null } = {}) {
  const r = await req('POST', '/api/auth/register',
    { body: { email: `ownbasis${++n}_${Date.now()}@test.io`, password: 'x'.repeat(12) } });
  const { token, user_id: id } = r.data;
  assert.ok(token, JSON.stringify(r.data));
  if (handle) {
    const o = await req('POST', '/api/leaderboard/opt-in', { token, body: { handle } });
    assert.equal(o.status, 200, JSON.stringify(o.data));
  }
  for (const pnl of closes) {
    await pool.execute(
      "INSERT INTO trades (user_id, symbol, direction, entry_price, exit_price, size_usd, pnl, fees, status, pattern, opened_at, closed_at) VALUES (?,?,?,?,?,?,?,?,'CLOSED',?,?,?)",
      [id, 'BTC/USDT', 'LONG', 60000, 61000, 2000, pnl, 1.2, 'x', new Date(), new Date()]);
  }
  if (equity !== null) {
    await pool.execute(
      'INSERT INTO equity_snapshots (user_id, equity, snapshot_at) VALUES (?, ?, ?)',
      [id, equity, new Date()]);
  }
  return { token, id };
}

test.before(async () => {
  const app = express();
  app.use(express.json());
  app.use('/api/auth', authModule.router);
  app.use('/api/leaderboard', require('../routes/leaderboard'));
  await new Promise((res) => { server = app.listen(0, '127.0.0.1', res); });
  base = `http://127.0.0.1:${server.address().port}`;
});

test.after(() => { if (server) server.close(); });

const board = async (token) => (await req('GET', '/api/leaderboard', { token })).data;
// Registration is IP rate-limited, so one signed-in reader serves every case.
let viewer = null;
const reader = async () => { viewer = viewer || await member(null); return viewer; };
const row = (b, handle) => b.rows.find((r) => r.handle === handle);

test('no equity reading: listed by trades and win rate, no return, no rank', async () => {
  await member('desk_nobasis', { closes: [412.37, -95.12, 23.4] });
  const b = await board((await reader()).token);
  const r = row(b, 'desk_nobasis');
  assert.ok(r, 'a member with a real record must stay listed');
  assert.equal(r.return_pct, null,
    'a return over no basis was published -- it used to be net / 10000, which '
    + 'is the dollar P&L x 100');
  assert.equal(r.rank, null, 'a member with no return has no rank by return');
  assert.equal(r.trades, 3);
  assert.equal(r.win_rate, 66.7);
});

test('an equity reading gives a return on that account, not on a constant', async () => {
  // Start = 2340.65 - 340.65 = 2000, so the return is 340.65 / 2000 = 17.03%.
  await member('desk_basis', { closes: [412.37, -95.12, 23.4], equity: 2340.65 });
  const r = row(await board((await reader()).token), 'desk_basis');
  assert.equal(r.return_pct, 17.03);
  assert.ok(Math.abs(r.return_pct * 100 - 340.65) > 1,
    'return_pct x 100 still reads back as the dollar P&L');
  assert.equal(typeof r.rank, 'number');
});

test('a snapshot at or below the realized net is no basis, not a floor of 1', async () => {
  // A withdrawal leaves the latest equity below what was realized. The basis
  // clamps to 1 there, and dividing by 1 publishes the net P&L x 100.
  await member('desk_withdrawn', { closes: [412.37], equity: 100 });
  const r = row(await board((await reader()).token), 'desk_withdrawn');
  assert.equal(r.return_pct, null, `a clamped basis was divided by: ${JSON.stringify(r)}`);
  assert.equal(r.rank, null);
});

test('an unranked member is listed after the ranked ones and does not count as ranked', async () => {
  const flat = await member('zz_flat', { closes: [50, -50], equity: 1000 });   // 0.00%, a reading
  const big = await member('aa_nobasis', { closes: [5000, 10] });              // no snapshot
  const b = await board(big.token);
  const handles = b.rows.map((r) => r.handle);
  const iFlat = handles.indexOf('zz_flat');
  const iBig = handles.indexOf('aa_nobasis');
  assert.ok(iFlat >= 0 && iBig >= 0, JSON.stringify(handles));
  assert.ok(iFlat < iBig,
    'a member with no basis was sorted as a return -- `null - x` reads null as 0');
  const ranked = b.rows.filter((r) => r.rank !== null);
  assert.deepEqual(ranked.map((r) => r.rank), ranked.map((_, i) => i + 1),
    'ranks are 1..N over the ranked members only');
  assert.ok(b.rows.slice(ranked.length).every((r) => r.rank === null && r.return_pct === null),
    'every row after the ranked ones is an unranked one');
  assert.equal(b.ranked_total, ranked.length);
  assert.equal(b.my_rank, null);
  assert.equal(b.my_unranked, true, 'the caller is told they are listed and not ranked');
  assert.ok(b.unranked_total >= 1);
  const flatView = await board(flat.token);
  assert.equal(flatView.my_unranked, false);
  assert.equal(typeof flatView.my_rank, 'number');
  assert.equal(row(flatView, 'zz_flat').return_pct, 0, 'a measured flat book is 0.00%, ranked');
});

test('a member who has closed nothing is neither ranked nor listed', async () => {
  const fresh = await member('newbie_ob', {});
  const b = await board(fresh.token);
  assert.equal(b.my_rank, null);
  assert.equal(b.my_unranked, false, '"close a trade to get ranked" is the true sentence here');
  assert.equal(row(b, 'newbie_ob'), undefined);
});

// ── The panel ────────────────────────────────────────────────────────────────

const DASH = codeOnly(fs.readFileSync(path.join(__dirname, '..', 'public', 'js', 'dashboard.js'), 'utf8'));
const APPJS = codeOnly(fs.readFileSync(path.join(__dirname, '..', 'public', 'js', 'app.js'), 'utf8'));

/** A function declaration's full text, by brace-matching from its name. */
function fnText(src, name) {
  const i = src.indexOf(`function ${name}(`);
  assert.ok(i >= 0, `function ${name} not found`);
  let depth = 0, j = src.indexOf('{', i);
  for (; j < src.length; j++) {
    if (src[j] === '{') depth++;
    else if (src[j] === '}') { depth--; if (depth === 0) break; }
  }
  return src.slice(i, j + 1);
}

/** The inline loader passed to renderPanel(C(<id>), ...), as a callable. */
function loader(id) {
  const calls = loaderBodies(DASH).filter((l) => l.target === `C('${id}')`);
  assert.equal(calls.length, 1, `expected one renderPanel(C('${id}')) call`);
  const body = calls[0].body;
  const start = body.indexOf('async () => {');
  let depth = 0, j = body.indexOf('{', start);
  for (; j < body.length; j++) {
    if (body[j] === '{') depth++;
    else if (body[j] === '}') { depth--; if (depth === 0) break; }
  }
  const fnSrc = body.slice(start, j + 1);
  const helpers = ['esc', 'fmt', 'signed', 'pnlClass'].map((h) => fnText(APPJS, h)).join('\n');
  return (data) => vm.runInNewContext(
    `${helpers}\nconst T = (k, en) => en; const load = async () => data;\n(${fnSrc})()`,
    { data });
}

test('the table prints a dash for an unranked row, never "--%" or a rank', async () => {
  const html = await loader('lbtable')({ rows: [
    { rank: 1, handle: 'ranked_one', return_pct: 4.2, trades: 3, win_rate: 66.7, is_me: false },
    { rank: null, handle: 'no_basis', return_pct: null, trades: 5, win_rate: 60, is_me: false },
  ] });
  const tr = html.split('<tr').find((x) => x.includes('no_basis'));
  assert.ok(tr, html);
  assert.ok(!/--%|null|undefined|NaN/.test(tr), `an absent return rendered as a figure: ${tr}`);
  assert.ok(!/class="r num (pos|neg)"/.test(tr), 'colour is a claim: an absent return is muted');
  assert.equal((tr.match(/>—</g) || []).length, 2, `rank and return both read as a dash: ${tr}`);
  const ok = html.split('<tr').find((x) => x.includes('ranked_one'));
  assert.match(ok, />\+4\.20%</);
  assert.match(ok, />1</);
  assert.ok(!/standard paper stake/.test(html),
    'the caption still says returns are on a standard stake');
  assert.match(html, /each account's own starting equity/);
});

test('the join panel names the listed-but-unranked state', async () => {
  const run = loader('lbjoin');
  const unranked = await run({ opted_in: true, handle: 'h1', my_rank: null,
    my_unranked: true, ranked_total: 4 });
  assert.match(unranked, /listed by trades and win rate but not ranked/);
  assert.ok(!/Close a trade to get ranked/.test(unranked),
    'told to close a trade to get ranked, when they have closed trades');
  const none = await run({ opted_in: true, handle: 'h1', my_rank: null,
    my_unranked: false, ranked_total: 4 });
  assert.match(none, /Close a trade to get ranked/);
  const ranked = await run({ opted_in: true, handle: 'h1', my_rank: 2,
    my_unranked: false, ranked_total: 4 });
  assert.match(ranked, /#2/);
});
