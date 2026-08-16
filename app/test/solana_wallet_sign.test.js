'use strict';
/**
 * The wallet call that ends the signing path.
 *
 * `signAndSend` hands a server-built transaction to the user's own wallet.
 * RUNECLAW holds no key and this file adds none: what is tested here is that
 * the bytes are passed through unaltered, and that every way the call can fail
 * throws rather than returning something a caller could read as "sent".
 *
 * The base58 encoder gets known vectors because it is the one piece of real
 * computation on the path, and because its characteristic bug — dropping
 * leading zero bytes — produces a valid-looking string for a DIFFERENT
 * transaction rather than an obvious error.
 */

const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

const MOD = path.join(__dirname, '..', 'public', 'js', 'solana_wallet.js');
const W = require(MOD);

const ADDR = '7xKXtg2CW87d97TXJSDpbD5jBkheTqA83TZRuJosgAsU';

/** A fake injected wallet. Records what it was asked to approve. */
function fakeWallet(over = {}) {
  const calls = [];
  const p = {
    isPhantom: true,
    publicKey: { toString: () => ADDR },
    connect: async () => ({ publicKey: { toString: () => ADDR } }),
    request: async (args) => { calls.push(args); return { signature: 'SIG111' }; },
    ...over,
  };
  p.calls = calls;
  global.window = { phantom: { solana: p } };
  return p;
}

test.afterEach(() => { delete global.window; });

const b64 = (bytes) => Buffer.from(bytes).toString('base64');

// ── base58, against vectors ───────────────────────────────────────────────

test('base58 matches the canonical vectors', () => {
  const cases = [
    [[], ''],
    [[0], '1'],
    [[0, 0], '11'],
    [[...Buffer.from('hello world')], 'StV1DL6CwTryKyV'],
    [[0, 0, 0x28, 0x7f, 0xb4, 0xcd], '11233QC4'],
    [[255, 255, 255, 255, 255, 255, 255, 255], 'jpXCZedGfVQ'],
    [[0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15],
      '12drXXUifSrRnXLGbXg8E'],
  ];
  for (const [bytes, want] of cases) {
    assert.strictEqual(W.base58(new Uint8Array(bytes)), want, JSON.stringify(bytes));
  }
});

test('leading zero bytes survive — each is a literal 1', () => {
  // The failure this pins is not an exception. An implementation that goes via
  // a number drops these and returns a well-formed string that decodes to a
  // shorter, different transaction. Solana buffers start with zeros routinely.
  const body = [0x28, 0x7f, 0xb4, 0xcd];
  for (let z = 0; z <= 5; z++) {
    const enc = W.base58(new Uint8Array([...Array(z).fill(0), ...body]));
    assert.strictEqual(enc.slice(0, z), '1'.repeat(z), `${z} zeros`);
    assert.notStrictEqual(enc[z], '1');
  }
});

test('an empty buffer is not one zero byte', () => {
  assert.strictEqual(W.base58(new Uint8Array([])), '');
  assert.strictEqual(W.base58(new Uint8Array([0])), '1');
});

test('base58 handles a transaction-sized buffer', () => {
  const big = new Uint8Array(1232).map((_, i) => (i * 37) % 256);
  const enc = W.base58(big);
  assert.ok(enc.length > 1600, 'a ~1.2kB transaction encodes to ~1.7k chars');
  assert.match(enc, /^[1-9A-HJ-NP-Za-km-z]+$/, 'no 0OIl in the alphabet');
});

// ── the transaction is passed through, not rebuilt ────────────────────────

test('the wallet is asked to approve exactly the bytes it was given', () => {
  const bytes = [0, 0, 9, 8, 7, 200, 201, 255];
  const p = fakeWallet();
  return W.signAndSend(b64(bytes)).then((r) => {
    assert.strictEqual(p.calls.length, 1);
    assert.strictEqual(p.calls[0].method, W.SIGN_METHOD);
    assert.strictEqual(p.calls[0].params.message, W.base58(new Uint8Array(bytes)));
    assert.strictEqual(r.signature, 'SIG111');
    assert.strictEqual(r.address, ADDR);
  });
});

test('this file never constructs a transaction', () => {
  // The custody claim, asserted rather than left to the docstring. Re-encoding
  // base64 to base58 is not rewriting; building instructions would be.
  const src = fs.readFileSync(MOD, 'utf8')
    .replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '');
  for (const banned of ['Keypair', 'privateKey', 'private_key', 'secretKey',
    'mnemonic', 'fromSeed', 'SystemProgram', 'TransactionInstruction']) {
    assert.ok(!src.includes(banned), `${banned} must not appear in code`);
  }
});

// ── every failure throws; none returns a falsy "success" ──────────────────

test('a missing transaction throws rather than resolving', async () => {
  fakeWallet();
  for (const bad of [null, undefined, '', 0, {}]) {
    await assert.rejects(() => W.signAndSend(bad), /No transaction/, String(bad));
  }
});

test('undecodable base64 throws and sends nothing', async () => {
  const p = fakeWallet();
  await assert.rejects(() => W.signAndSend('!!!not base64!!!'),
    (e) => e.code === 'BAD_TRANSACTION');
  assert.strictEqual(p.calls.length, 0, 'the wallet was never asked');
});

test('base64 that decodes to nothing throws and sends nothing', async () => {
  // Whitespace is legal base64 padding and decodes to zero bytes, so this
  // branch is reachable rather than defensive: atob(' ') === ''.
  const p = fakeWallet();
  for (const empty of [' ', '\n', '\t\n ']) {
    await assert.rejects(() => W.signAndSend(empty),
      (e) => e.code === 'BAD_TRANSACTION', JSON.stringify(empty));
  }
  assert.strictEqual(p.calls.length, 0, 'the wallet was never asked');
});

test('a wallet with no request() is refused, not silently skipped', async () => {
  fakeWallet({ request: undefined });
  await assert.rejects(() => W.signAndSend(b64([1, 2, 3])),
    (e) => e.code === 'NO_SEND_SUPPORT');
});

test('no signature back means NOT sent, and says so', async () => {
  // The dangerous reading of a quiet return is "it worked". A wallet that
  // returned nothing has told us nothing.
  for (const res of [{}, { signature: '' }, { signature: null }, null,
    { signature: 123 }]) {
    fakeWallet({ request: async () => res });
    await assert.rejects(() => W.signAndSend(b64([1, 2, 3])),
      (e) => e.code === 'NO_SIGNATURE' && /NOT sent/.test(e.message),
      JSON.stringify(res));
  }
});

test('a user rejecting in the wallet propagates', async () => {
  fakeWallet({ request: async () => { throw new Error('User rejected the request.'); } });
  await assert.rejects(() => W.signAndSend(b64([1, 2, 3])), /User rejected/);
});

test('no wallet installed throws NO_WALLET', async () => {
  global.window = {};
  await assert.rejects(() => W.signAndSend(b64([1, 2, 3])),
    (e) => e.code === 'NO_WALLET');
});

// ── the browser still gets its global ─────────────────────────────────────

test('loading the file in a browser sets window.RCSolanaWallet', () => {
  // The file became a UMD module so tests could reach it, and the failure mode
  // of that change is silent: every page that calls RCSolanaWallet.connect()
  // breaks at once, and no server-side test would notice.
  const src = fs.readFileSync(MOD, 'utf8');
  const sandboxWindow = {};
  // No `module` in scope — exactly what a <script> tag provides.
  new Function('window', src)(sandboxWindow);
  assert.ok(sandboxWindow.RCSolanaWallet, 'the page global must be set');
  for (const fn of ['available', 'connect', 'signMessage', 'signAndSend']) {
    assert.strictEqual(typeof sandboxWindow.RCSolanaWallet[fn], 'function', fn);
  }
});

// ── the older surface still works ─────────────────────────────────────────

test('connect and signMessage are unchanged', async () => {
  const p = fakeWallet({
    signMessage: async () => ({ signature: new Uint8Array([1, 2, 3]) }),
  });
  assert.strictEqual(W.available(), true);
  const { address } = await W.connect();
  assert.strictEqual(address, ADDR);
  const s = await W.signMessage('login');
  assert.strictEqual(s.address, ADDR);
  assert.strictEqual(s.signature, Buffer.from([1, 2, 3]).toString('base64'));
  assert.ok(p);
});
