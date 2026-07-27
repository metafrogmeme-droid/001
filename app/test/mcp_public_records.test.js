'use strict';
/**
 * Three new published-data MCP tools, added BEFORE ERC-8257 registration
 * while manifest-hash changes are still free:
 * - get_arena_trader — the §4-safe public trader card by opt-in handle,
 *   through fetchTraderCard (one source of truth with the website);
 * - get_paper_leaderboard — the arena board through computeLeaderboard
 *   (same computation the JSON route and the frame card use);
 * - get_rune_stats — the collection state with unknown-never-zero honesty.
 * Pins: §4 shape on the wire (no balance/user_id keys), honest not-found
 * and not-deployed answers, and the Guardian caller-input family untouched.
 */
process.env.JWT_SECRET = process.env.JWT_SECRET || 'j'.repeat(64);
delete process.env.DATABASE_URL;
delete process.env.NFT_CONTRACT_ADDRESS;

const test = require('node:test');
const assert = require('node:assert');
const http = require('http');
const express = require('express');
const { pool } = require('../db');
const { TOOLS } = require('../routes/mcp');

let server, base, uid;

function rpc(method, params) {
  return new Promise((resolve, reject) => {
    const body = JSON.stringify({ jsonrpc: '2.0', id: 1, method, params });
    const r = http.request(`${base}/mcp`, { method: 'POST',
      headers: { 'Content-Type': 'application/json', Accept: 'application/json' } }, (res) => {
      const chunks = [];
      res.on('data', (c) => chunks.push(c));
      res.on('end', () => resolve(JSON.parse(Buffer.concat(chunks).toString('utf8'))));
    });
    r.on('error', reject);
    r.end(body);
  });
}

async function call(name, args) {
  const r = await rpc('tools/call', { name, arguments: args || {} });
  return JSON.parse(r.result.content[0].text);
}

test.before(async () => {
  const [u] = await pool.execute(
    'INSERT INTO users (email, password_hash, created_at) VALUES (?, ?, NOW())',
    ['mcp-records@test.dev', 'x'.repeat(60)]);
  uid = u.insertId;
  await pool.execute('UPDATE users SET leaderboard_handle = ? WHERE id = ?', ['mcprecord', uid]);
  await pool.execute(
    'INSERT INTO arena_accounts (user_id, balance, created_at) VALUES (?, ?, NOW()) ON DUPLICATE KEY UPDATE balance = VALUES(balance)',
    [uid, 10400]);
  await pool.execute(
    `INSERT INTO arena_trades (user_id, symbol, direction, entry, exit_price, margin, leverage,
       pnl, reason, opened_at, closed_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NOW(), NOW())`,
    [uid, 'BTCUSDT', 'LONG', 60000, 61000, 100, 3, 5, 'tp']);
  const app = express();
  app.use('/mcp', require('../routes/mcp'));
  await new Promise((res) => { server = app.listen(0, '127.0.0.1', res); });
  base = `http://127.0.0.1:${server.address().port}`;
});
test.after(() => { if (server) server.close(); });

test('the three tools are published-data, and the Guardian family is untouched', () => {
  for (const t of ['get_arena_trader', 'get_paper_leaderboard', 'get_rune_stats']) {
    assert.ok(TOOLS[t], `${t} exists`);
    assert.ok(!TOOLS[t].computesOnInput, `${t} serves published data`);
  }
  const guardians = Object.keys(TOOLS).filter((n) => TOOLS[n].computesOnInput).sort();
  assert.deepEqual(guardians, ['compile_intent', 'plan_escape', 'scan_transaction',
    'stress_portfolio', 'xray_transaction']);
});

test('get_arena_trader serves the §4-safe card, honestly', async () => {
  const bad = await call('get_arena_trader', { handle: '!!' });
  assert.strictEqual(bad.found, false);
  const missing = await call('get_arena_trader', { handle: 'nosuchhandle' });
  assert.strictEqual(missing.found, false);
  assert.match(missing.note, /opt-in/, 'absence is explained, not judged');
  const hit = await call('get_arena_trader', { handle: 'mcprecord' });
  assert.strictEqual(hit.found, true);
  assert.strictEqual(hit.handle, 'mcprecord');
  assert.strictEqual(hit.closed_trades, 1);
  assert.strictEqual(hit.virtual, true);
  // §4 on the wire: percent and counts only
  const flat = JSON.stringify(hit);
  assert.ok(!flat.includes('"balance"'), 'no balance rides the wire');
  assert.ok(!flat.includes('"user_id"'), 'no user id rides the wire');
  assert.ok(!/"pnl"/.test(flat), 'no raw pnl amount rides the wire');
});

test('get_paper_leaderboard is the same computation the site serves', async () => {
  const board = await call('get_paper_leaderboard', {});
  assert.ok(Array.isArray(board.rows));
  assert.strictEqual(board.virtual, true);
  const row = board.rows.find((r) => r.handle === 'mcprecord');
  assert.ok(row, 'the seeded trader ranks');
  assert.ok(!('balance' in row), 'rows carry no balance');
  // one source of truth, pinned in source
  const src = require('fs').readFileSync(
    require('path').join(__dirname, '..', 'routes', 'mcp.js'), 'utf8');
  assert.match(src, /computeLeaderboard/, 'the tool reuses the shared computation');
  assert.match(src, /fetchTraderCard/, 'the trader tool reuses the shared fetch');
});

test('get_rune_stats answers not-deployed as a state, never as zero', async () => {
  const stats = await call('get_rune_stats', {});
  assert.strictEqual(stats.deployed, false);
  assert.ok(!('minted_count' in stats) || stats.minted_count !== 0,
    'no fabricated zero for an undeployed collection');
});
