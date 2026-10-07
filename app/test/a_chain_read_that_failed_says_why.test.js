'use strict';
/**
 * A chain read that failed says which read and why.
 *
 *  - #504 replaced the RPC error's message with its class, and ethers v6
 *    builds every provider error as a plain `Error` carrying a `code`. Every
 *    real failure read "rpc unreadable — Error": a 429, a bad URL and a
 *    blocked egress looked the same, the case holdings.js carries the detail
 *    for. The detail is now the class and the code, an enum token.
 *  - The DeFi composite caught each failed protocol read to `undefined` and
 *    filtered it out with the "no position" answers, so a wallet whose every
 *    RPC failed read "no Aave, Lido or Uniswap v3 positions found". The
 *    failed reads are named on the card and the panel.
 */
process.env.JWT_SECRET = 'j'.repeat(64);
delete process.env.DATABASE_URL;
delete process.env.WEB3_CHAINS;

const test = require('node:test');
const assert = require('node:assert/strict');
const { makeError } = require('ethers');

const wallet = require('../lib/wallet');
const defi = require('../lib/defi');

const ADDR = '0x' + 'ab'.repeat(20);
const word = (v) => BigInt(v).toString(16).padStart(64, '0');

class Provider {
  constructor({ down = null } = {}) { this.down = down; }
  async getBalance() { if (this.down) throw this.down(); return 0n; }
  // Six zero words: what a contract answers for an address with nothing
  // there (Aave's account data is six uints; a balance reads the first).
  async call() { if (this.down) throw this.down(); return '0x' + word(0).repeat(6); }
  async getNetwork() { return { chainId: 0n }; }
  async resolveName(n) { return n; }
}

test('an ethers failure names its code, never its message', () => {
  for (const code of ['NETWORK_ERROR', 'SERVER_ERROR', 'TIMEOUT', 'CALL_EXCEPTION', 'UNSUPPORTED_OPERATION']) {
    const e = makeError('request to https://rpc.example/secret-key failed', code);
    assert.equal(e.name, 'Error', 'ethers errors are plain Errors');
    assert.equal(wallet.errorReason(e), `Error ${code}`);
  }
  const sys = Object.assign(new Error('connect ECONNREFUSED 10.0.0.1:443'), { code: 'ECONNREFUSED' });
  assert.equal(wallet.errorReason(sys), 'Error ECONNREFUSED');
  // A code that is not an enum token is left out, and so is any message.
  assert.equal(wallet.errorReason(Object.assign(new TypeError('x'), { code: 'https://rpc/x' })), 'TypeError');
  assert.equal(wallet.errorReason(Object.assign(new Error('x'), { code: 429 })), 'Error');
  assert.equal(wallet.errorReason(null), 'Error');
});

test('a rate-limited chain reads "rpc unreadable — Error SERVER_ERROR"', async () => {
  process.env.WEB3_CHAINS = 'ethereum';
  try {
    wallet.setProviderFactory(() => new Provider({ down: () => makeError('429 Too Many Requests from https://x', 'SERVER_ERROR') }));
    wallet.setTickerFetcher(async () => ({}));
    const p = await wallet.getWalletPortfolio('0x' + 'ce'.repeat(20));
    const chain = p.chains.find((c) => c.chain === 'ethereum');
    assert.equal(chain.error, 'rpc unreadable');
    assert.equal(chain.error_detail, 'Error SERVER_ERROR');
    assert.doesNotMatch(JSON.stringify(p), /https:\/\/x|Too Many/);
  } finally {
    delete process.env.WEB3_CHAINS;
    wallet.setProviderFactory(null);
    wallet.setTickerFetcher(null);
  }
});

test('every DeFi read failed: the composite names each one, beside no positions', async () => {
  process.env.WEB3_CHAINS = 'ethereum,arbitrum';
  try {
    defi.setProviderFactory(() => new Provider({ down: () => makeError('down', 'NETWORK_ERROR') }));
    defi.setTickerFetcher(async () => ({}));
    const d = await defi.buildDefiPositions(ADDR);
    assert.deepEqual(d.aave, []);
    assert.equal(d.lido, null);
    // Every protocol on every chain is named, Uniswap's counts included.
    assert.deepEqual([...d.unread].sort(), [
      'Aave v3 · Arbitrum', 'Aave v3 · Ethereum', 'Lido',
      'Uniswap v3 · Arbitrum', 'Uniswap v3 · Ethereum']);
    assert.deepEqual(d.uniswap, []);
  } finally {
    delete process.env.WEB3_CHAINS;
    defi.setProviderFactory(null);
    defi.setTickerFetcher(null);
  }
});

test('every DeFi read answered with nothing: no position, and nothing unread', async () => {
  process.env.WEB3_CHAINS = 'ethereum,arbitrum';
  try {
    defi.setProviderFactory(() => new Provider());
    defi.setTickerFetcher(async () => ({}));
    const d = await defi.buildDefiPositions(ADDR);
    assert.deepEqual(d.unread, []);
    assert.deepEqual(d.aave.filter((a) => a.collateral_usd > 0), []);
    assert.equal(d.lido, null);
  } finally {
    delete process.env.WEB3_CHAINS;
    defi.setProviderFactory(null);
    defi.setTickerFetcher(null);
  }
});

test('the chat card for a wallet whose every DeFi read failed names them, not "no positions"', async () => {
  process.env.WEB3_CHAINS = 'ethereum';
  const { pool } = require('../db');
  const addr = '0x' + 'cd'.repeat(20);
  try {
    await pool.execute('INSERT INTO users (email, password_hash) VALUES (?, ?)', ['defidown@example.com', 'x']);
    const [rows] = await pool.execute('SELECT * FROM users WHERE email = ?', ['defidown@example.com']);
    await pool.execute('UPDATE users SET wallet_address = ? WHERE id = ?', [addr, rows[0].id]);
    defi.setProviderFactory(() => new Provider({ down: () => makeError('down', 'NETWORK_ERROR') }));
    defi.setTickerFetcher(async () => ({}));
    const card = await defi.defiChatCard(rows[0].id);
    assert.match(card.reply_html, /Could not read .*Aave v3 · Ethereum.*\(RPC\)\. That is a failed read, not "no position"\./);
    assert.doesNotMatch(card.reply_html, /no Aave, Lido or Uniswap v3 positions found/);
  } finally {
    delete process.env.WEB3_CHAINS;
    defi.setProviderFactory(null);
    defi.setTickerFetcher(null);
  }
});

// ── the dashboard panel's renderer, run ─────────────────────────────────────
function panel() {
  const fs = require('node:fs');
  const path = require('node:path');
  const vm = require('node:vm');
  const raw = fs.readFileSync(path.join(__dirname, '..', 'public', 'js', 'dashboard.js'), 'utf8');
  const a = raw.indexOf('// ── the DeFi panel: renderer start ─');
  const b = raw.indexOf('// ── the DeFi panel: renderer end ─');
  assert.ok(a > 0 && b > a, 'the DeFi panel renderer lost its markers');
  const ctx = {
    esc: (v) => String(v == null ? '' : v).replace(/&/g, '&amp;').replace(/</g, '&lt;'),
    fmt: (v, n) => Number(v).toFixed(n),
    hfMeter: () => '',
    Number, Array, out: null,
  };
  vm.runInNewContext(raw.slice(a, b) + '\nout = defiPanelHtml;', ctx, { timeout: 5000 });
  return ctx.out;
}

test('the dashboard DeFi panel names failed reads, alone or beside what it read', () => {
  const defiPanelHtml = panel();
  const NOTE = 'Read straight from protocol contracts.';
  // Nothing read and nothing failed: null, which the panel paints as its
  // "no positions found" empty state.
  assert.equal(defiPanelHtml({ aave: [], lido: null, uniswap: [], unread: [], note: NOTE }), null);
  // Nothing read and reads failed: the failure, not the empty state.
  const down = defiPanelHtml({ aave: [], lido: null, uniswap: [], unread: ['Aave v3 · Base', 'Lido'], note: NOTE });
  assert.match(down, /Could not read Aave v3 · Base, Lido \(RPC\)\. That is a failed read, not "no position"\./);
  // A position read on one chain and a failure on another: both.
  const part = defiPanelHtml({
    aave: [{ label: 'Ethereum', collateral_usd: 1000, debt_usd: 0, health_factor: null }],
    lido: null, uniswap: [], unread: ['Uniswap v3 · Arbitrum'], warnings: [], note: NOTE });
  assert.match(part, /Aave v3 · Ethereum/);
  assert.match(part, /Could not read Uniswap v3 · Arbitrum \(RPC\)/);
  assert.match(part, /Read straight from protocol contracts\./);
  // An escaped label: the unread names come from chain config, still escaped.
  assert.match(defiPanelHtml({ aave: [], uniswap: [], unread: ['<b>x</b>'], note: NOTE }), /&lt;b>x&lt;\/b>/);
});

test('the dashboard DeFi panel is painted by that renderer', () => {
  const fs = require('node:fs');
  const path = require('node:path');
  const { codeOnly } = require('./helpers/code_only');
  const dash = codeOnly(fs.readFileSync(path.join(__dirname, '..', 'public', 'js', 'dashboard.js'), 'utf8'));
  const start = dash.indexOf("renderPanel(C('defi')");
  assert.ok(start > 0 && dash.indexOf("renderPanel(C('defi')", start + 1) < 0);
  const end = dash.indexOf('No Aave, Lido or Uniswap v3 positions found', start);
  assert.match(dash.slice(start, end), /return defiPanelHtml\(d\);/);
});
