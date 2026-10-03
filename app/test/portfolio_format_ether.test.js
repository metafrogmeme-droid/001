'use strict';
/**
 * Portfolio on-chain rows called `ethers.formatEther` on the namespace this
 * process required. ethers v6 exports that function on the module; a build
 * that does not (the live TypeError) made every chain "rpc unreadable" and
 * printed the exception message on the card. A readable balance must still
 * show. An RPC that throws stays unreadable, stays out of the total, and the
 * card names the exception class, never the message.
 */
process.env.JWT_SECRET = 'j'.repeat(64);
delete process.env.DATABASE_URL;
delete process.env.WEB_GATEWAY_SECRET;
delete process.env.BOT_GATEWAY_URL;
process.env.WEB3_CHAINS = 'ethereum,base';

const test = require('node:test');
const assert = require('node:assert');
const { pool } = require('../db');
const wallet = require('../lib/wallet');
const { buildHoldings } = require('../lib/holdings');

const LEAK = 'ethers.formatEther is not a function';
const ADDR_OK = '0x' + '11'.repeat(20);
const ADDR_MIX = '0x' + '22'.repeat(20);
const ADDR_V6 = '0x' + '33'.repeat(20);

const ETH = wallet.CHAINS.find((c) => c.key === 'ethereum');
const USDC = ETH.tokens.find((t) => t.symbol === 'USDC').address.toLowerCase();

function pkgVersion() {
  return require('ethers').version;
}

function hide(obj, name, saved) {
  if (!obj) return;
  const desc = Object.getOwnPropertyDescriptor(obj, name);
  if (!desc) return;
  saved.push([obj, name, desc]);
  Object.defineProperty(obj, name, {
    value: undefined, writable: true, configurable: true, enumerable: true,
  });
}

function stripFormatters() {
  const mod = require('ethers');
  const saved = [];
  for (const obj of [mod, mod.ethers, mod.utils, mod.ethers && mod.ethers.utils]) {
    hide(obj, 'formatEther', saved);
    hide(obj, 'formatUnits', saved);
  }
  assert.notStrictEqual(typeof mod.formatEther, 'function', 'formatEther still callable');
  if (mod.ethers) {
    assert.notStrictEqual(typeof mod.ethers.formatEther, 'function',
      'namespace formatEther still callable');
  }
  return () => {
    for (const [obj, name, desc] of saved.reverse()) Object.defineProperty(obj, name, desc);
  };
}

class FakeProvider {
  constructor({ native = 0n, balances = {}, boom = null } = {}) {
    this.native = native;
    this.balances = balances;
    this.boom = boom;
  }
  async getBalance() {
    if (this.boom) throw this.boom;
    return this.native;
  }
  async call(tx) {
    if (this.boom) throw this.boom;
    const raw = this.balances[String(tx.to).toLowerCase()] ?? 0n;
    return '0x' + raw.toString(16).padStart(64, '0');
  }
  async getNetwork() { return { chainId: 1n }; }
  async resolveName(n) { return n; }
}

async function userWith(address) {
  const email = `${address.slice(2, 10)}@example.com`;
  await pool.execute('INSERT INTO users (email, password_hash) VALUES (?, ?)',
    [email, 'x'.repeat(60)]);
  const [rows] = await pool.execute('SELECT * FROM users WHERE email = ?', [email]);
  await pool.execute('UPDATE users SET wallet_address = ? WHERE id = ?', [address, rows[0].id]);
  return rows[0].id;
}

function installProviders(map) {
  wallet.setProviderFactory((chain) => map[chain.key]);
  wallet.setTickerFetcher(async () => ({
    ETHUSDT: { price: 2500, change: 0, volume: 1 },
  }));
}

test('portfolio wei formatting', { concurrency: false }, async (t) => {
await t.test('installed ethers is v6 and exports formatEther on the module', () => {
  const mod = require('ethers');
  const major = pkgVersion().split('.')[0];
  assert.equal(major, '6');
  assert.strictEqual(typeof mod.formatEther, 'function');
  assert.strictEqual(typeof mod.utils, 'undefined');
});

await t.test('a readable balance uses the installed formatEther', async () => {
  const mod = require('ethers');
  const saved = [];
  function swap(obj) {
    if (!obj) return;
    const desc = Object.getOwnPropertyDescriptor(obj, 'formatEther');
    if (!desc) return;
    saved.push([obj, desc]);
    Object.defineProperty(obj, 'formatEther', {
      value: () => '7.0', writable: true, configurable: true,
    });
  }
  swap(mod);
  swap(mod.ethers);
  swap(mod.utils);
  try {
    installProviders({
      ethereum: new FakeProvider({ native: 3n * 10n ** 18n }),
      base: new FakeProvider({ native: 0n }),
    });
    const p = await wallet.getWalletPortfolio(ADDR_V6);
    const eth = p.chains.find((c) => c.chain === 'ethereum');
    assert.equal(eth.assets.find((a) => a.symbol === 'ETH').amount, 7);
    assert.equal(eth.error, undefined);
  } finally {
    for (const [obj, desc] of saved.reverse()) Object.defineProperty(obj, 'formatEther', desc);
  }
});

await t.test('a chain whose wei formats shows its balance when formatEther is not a function', async () => {
  const restore = stripFormatters();
  try {
    installProviders({
      ethereum: new FakeProvider({
        native: 2n * 10n ** 18n,
        balances: { [USDC]: 500n * 10n ** 6n },
      }),
      base: new FakeProvider({ native: 0n }),
    });
    const uid = await userWith(ADDR_OK);
    const p = await wallet.getWalletPortfolio(ADDR_OK);
    const eth = p.chains.find((c) => c.chain === 'ethereum');
    const base = p.chains.find((c) => c.chain === 'base');
    assert.equal(eth.error, undefined);
    assert.equal(eth.total_usd, 5500);
    assert.equal(eth.assets.find((a) => a.symbol === 'ETH').amount, 2);
    assert.equal(eth.assets.find((a) => a.symbol === 'USDC').amount, 500);
    // A measured empty chain is not an unread one, and not a coerced zero-from-failure.
    assert.equal(base.error, undefined);
    assert.equal(base.total_usd, 0);
    assert.equal(base.assets.length, 0);
    assert.equal(p.total_usd, 5500);
    assert.equal(JSON.stringify(p).includes(LEAK), false);

    const h = await buildHoldings({ id: '1' }, uid);
    assert.equal(h.partial, false);
    assert.equal(h.total_real_usd, 5500);
    const row = h.wallet.chains.find((c) => c.chain === 'ethereum');
    assert.equal(row.total_usd, 5500);
    assert.equal(row.detail, null);
    assert.equal(JSON.stringify(h).includes(LEAK), false);
  } finally {
    restore();
  }
});

await t.test('an unreadable RPC stays out of the total and the card names the class', async () => {
  const restore = stripFormatters();
  try {
    installProviders({
      ethereum: new FakeProvider({
        native: 2n * 10n ** 18n,
        balances: { [USDC]: 500n * 10n ** 6n },
      }),
      base: new FakeProvider({ boom: new TypeError(LEAK) }),
    });
    const uid = await userWith(ADDR_MIX);
    const p = await wallet.getWalletPortfolio(ADDR_MIX);
    const eth = p.chains.find((c) => c.chain === 'ethereum');
    const base = p.chains.find((c) => c.chain === 'base');
    assert.equal(eth.total_usd, 5500);
    assert.equal(eth.error, undefined);
    assert.equal(base.assets.length, 0);
    assert.equal(base.error, 'rpc unreadable');
    assert.equal(base.error_detail, 'TypeError');
    assert.equal(String(base.error_detail).includes('formatEther'), false);
    assert.equal(p.total_usd, 5500);

    const h = await buildHoldings({ id: '1' }, uid);
    const dead = h.wallet.chains.find((c) => c.chain === 'base');
    const live = h.wallet.chains.find((c) => c.chain === 'ethereum');
    assert.equal(live.total_usd, 5500);
    assert.equal(dead.total_usd, null);
    assert.equal(dead.detail, 'rpc unreadable — TypeError');
    assert.equal(h.partial, true);
    assert.equal(h.total_real_usd, 5500);
    assert.match(h.note, /never counted as zero/);
    const blob = JSON.stringify(h);
    assert.equal(blob.includes(LEAK), false);
    assert.equal(blob.includes('formatEther'), false);
  } finally {
    restore();
  }
});
});
