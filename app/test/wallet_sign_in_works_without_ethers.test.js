'use strict';
/**
 * Wallet sign-in, with `ethers` resolved to the stub production has.
 *
 * On 21 October 2026 the live site answered every wallet sign-in 401
 * "Signature verification failed": `auth.js` called `ethers.verifyMessage`,
 * the stub production resolves `ethers` to has no such function
 * (app/test/ethers_stub_calldata.test.js), and the call threw inside its try.
 * Every other wallet test signs and verifies with the real `ethers`
 * installed here, so all of them passed. The probe that showed it: a
 * well-formed signature from a different key, which a working check answers
 * "Signature does not match the wallet".
 *
 * The verifier is now lib/eth_signature.js. This file checks it against real
 * ethers signature by signature, then installs the stub and signs in through
 * the routes, both arms: the right key gets a session, a different key gets
 * "does not match".
 */
process.env.JWT_SECRET = 'j'.repeat(64);
delete process.env.DATABASE_URL;

const test = require('node:test');
const assert = require('node:assert');
const http = require('node:http');

// The real ethers, held before the stub replaces it: it makes the signatures
// and is the reference the in-house verifier is held to.
const real = require('ethers').ethers;
const es = require('../lib/eth_signature');

const N = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEBAAEDCE6AF48A03BBFD25E8CD0364141n;
const realRecover = (msg, sig) => { try { return real.verifyMessage(msg, sig).toLowerCase(); } catch (_) { return null; } };
const withS = (sig, s) => sig.slice(0, 66) + s.toString(16).padStart(64, '0') + sig.slice(130);
const withV = (sig, v) => sig.slice(0, 130) + v.toString(16).padStart(2, '0');

test('the in-house verifier recovers what real ethers recovers, on random keys and messages', async () => {
  const messages = ['RUNECLAW — sign in with your wallet.\n\nNonce: 1', '', 'ü 💡 日本語', 'x'.repeat(500)];
  let parities = new Set();
  for (let i = 0; i < 16; i += 1) {
    const w = real.Wallet.createRandom();
    const msg = messages[i % messages.length] + (i >= messages.length ? ` ${i}` : '');
    const sig = await w.signMessage(msg);
    parities.add(sig.slice(-2));
    assert.strictEqual(es.recoverPersonalSignAddress(msg, sig), w.address.toLowerCase(), `key ${i}`);
    assert.strictEqual(es.recoverPersonalSignAddress(msg, sig), realRecover(msg, sig));
    // v as 0/1 is the same signature, as ethers reads it; so is EIP-2098's
    // 64-byte compact form, with the recovery bit in s's top bit.
    const v01 = withV(sig, parseInt(sig.slice(-2), 16) - 27);
    assert.strictEqual(es.recoverPersonalSignAddress(msg, v01), realRecover(msg, v01));
    const compact = real.Signature.from(sig).compactSerialized;
    assert.strictEqual(compact.length, 130);
    assert.strictEqual(es.recoverPersonalSignAddress(msg, compact), w.address.toLowerCase());
    assert.strictEqual(es.recoverPersonalSignAddress(msg, compact), realRecover(msg, compact));
  }
  assert.deepStrictEqual([...parities].sort(), ['1b', '1c'], 'both recovery parities were exercised');
  assert.strictEqual(es.hashPersonalMessage('hello'), real.hashMessage('hello'));
});

test('a signature for another message or by another key recovers someone else', async () => {
  const a = real.Wallet.createRandom();
  const b = real.Wallet.createRandom();
  const sig = await b.signMessage('nonce 1');
  assert.strictEqual(es.signedBy('nonce 1', sig, b.address), true, 'the other arm: the right key is accepted');
  assert.strictEqual(es.signedBy('nonce 1', sig, a.address), false);
  assert.strictEqual(es.signedBy('nonce 2', sig, b.address), false, 'a signature does not carry to another message');
});

test('what ethers refuses, the verifier refuses: no address is ever made up', async () => {
  const w = real.Wallet.createRandom();
  const msg = 'nonce';
  const sig = await w.signMessage(msg);
  const bad = {
    'not hex': '0x' + 'zz'.repeat(65),
    'too short': sig.slice(0, 120),
    'too long': sig + '00',
    'v of 29': withV(sig, 29),
    'r of zero': '0x' + '00'.repeat(32) + sig.slice(66),
    // 5³ + 7 has no square root mod p: no curve point has x = 5.
    'r off the curve': '0x' + (5).toString(16).padStart(64, '0') + sig.slice(66),
    's of zero': withS(sig, 0n),
    's of N': withS(sig, N),
    's with its top bit set': withS(sig, 2n ** 255n + 1n),
  };
  for (const [what, s] of Object.entries(bad)) {
    assert.strictEqual(es.recoverPersonalSignAddress(msg, s), null, what);
    assert.strictEqual(realRecover(msg, s), null, `ethers refuses ${what} too, or this case proves nothing`);
  }
  // A high s that ethers still takes (top bit clear, above N/2) is taken alike.
  const s = BigInt('0x' + sig.slice(66, 130));
  const flipped = withV(withS(sig, N - s), parseInt(sig.slice(-2), 16) === 27 ? 28 : 27);
  if (N - s < 2n ** 255n) assert.strictEqual(es.recoverPersonalSignAddress(msg, flipped), realRecover(msg, flipped));
});

test('with production\'s ethers stub installed, the routes sign the right key in and refuse another', async () => {
  // The stub, as app/test/ethers_stub_calldata.test.js has it: no verifyMessage.
  const stub = { keccak256: () => '0x', toUtf8Bytes: (s) => Buffer.from(String(s)), getAddress: (a) => String(a),
    isAddress: (a) => /^0x[0-9a-fA-F]{40}$/.test(String(a)) };
  const ethersPath = require.resolve('ethers');
  require.cache[ethersPath] = { id: ethersPath, filename: ethersPath, loaded: true, exports: { ethers: stub, ...stub } };
  assert.strictEqual(typeof require('ethers').verifyMessage, 'undefined', 'the stub is not in place');

  const express = require('express');
  const authModule = require('../auth');
  const app = express();
  app.use(express.json());
  app.use('/api/auth', authModule.router);
  const server = await new Promise((res) => { const s = app.listen(0, '127.0.0.1', () => res(s)); });
  const base = `http://127.0.0.1:${server.address().port}`;
  const post = (p, body) => new Promise((resolve, reject) => {
    const payload = JSON.stringify(body);
    const r = http.request(base + p, { method: 'POST', headers: { 'content-type': 'application/json' } }, (res) => {
      let d = '';
      res.on('data', (c) => { d += c; });
      res.on('end', () => resolve({ status: res.statusCode, body: d ? JSON.parse(d) : {} }));
    });
    r.on('error', reject);
    r.end(payload);
  });
  try {
    const owner = real.Wallet.createRandom();
    const other = real.Wallet.createRandom();
    // Another key's signature over the right message: "does not match", the
    // answer production gave as "verification failed" while it was broken.
    let n = await post('/api/auth/wallet/nonce', { address: owner.address });
    assert.strictEqual(n.status, 200);
    let v = await post('/api/auth/wallet/verify', { address: owner.address, signature: await other.signMessage(n.body.message) });
    assert.strictEqual(v.status, 401);
    assert.strictEqual(v.body.error, 'Signature does not match the wallet.');
    // The owner's own signature signs in.
    n = await post('/api/auth/wallet/nonce', { address: owner.address });
    v = await post('/api/auth/wallet/verify', { address: owner.address, signature: await owner.signMessage(n.body.message) });
    assert.strictEqual(v.status, 200, JSON.stringify(v.body));
    assert.ok(v.body.token, 'a session token, as the /doge app takes it');
    // A malformed signature is still "verification failed", and the nonce is single-use.
    n = await post('/api/auth/wallet/nonce', { address: owner.address });
    v = await post('/api/auth/wallet/verify', { address: owner.address, signature: '0x1234' });
    assert.strictEqual(v.body.error, 'Signature verification failed.');
  } finally {
    server.close();
  }
});
