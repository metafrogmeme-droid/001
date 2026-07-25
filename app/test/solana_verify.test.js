/**
 * Solana signMessage verification (lib/solana_verify.js).
 *
 * Pins the ed25519 proof used by the connect-and-sign wallet flow: a real
 * ed25519 signature over the exact message verifies; tampered message, wrong
 * address, malformed signature, and non-64-byte signatures all fail. No new
 * dependency — verification uses Node's built-in crypto (SPKI-wrapped key).
 *
 * Run: npm test  (node --test test/)
 */
const test = require('node:test');
const assert = require('node:assert');
const crypto = require('node:crypto');

const { verifySignedMessage, base58Decode } = require('../lib/solana_verify');

// base58 ENCODE helper (test-only) to render a raw pubkey as a Solana address.
const B58 = '123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz';
function b58encode(buf) {
  let n = 0n;
  for (const b of buf) n = n * 256n + BigInt(b);
  let s = '';
  while (n > 0n) { s = B58[Number(n % 58n)] + s; n /= 58n; }
  for (const b of buf) { if (b === 0) s = '1' + s; else break; }
  return s;
}

function newWallet() {
  const { publicKey, privateKey } = crypto.generateKeyPairSync('ed25519');
  const rawPub = publicKey.export({ format: 'der', type: 'spki' }).subarray(-32);
  return { address: b58encode(rawPub), privateKey };
}

test('valid signature verifies', () => {
  const w = newWallet();
  const msg = 'RUNECLAW — link your Solana wallet.\nNonce: deadbeef';
  const sig = crypto.sign(null, Buffer.from(msg, 'utf8'), w.privateKey);
  assert.equal(verifySignedMessage(msg, sig.toString('base64'), w.address), true);
});

test('tampered message fails', () => {
  const w = newWallet();
  const msg = 'RUNECLAW — link your Solana wallet.\nNonce: abc';
  const sig = crypto.sign(null, Buffer.from(msg, 'utf8'), w.privateKey);
  assert.equal(verifySignedMessage(msg + 'x', sig.toString('base64'), w.address), false);
});

test('wrong address fails', () => {
  const w = newWallet();
  const other = newWallet();
  const msg = 'sign me';
  const sig = crypto.sign(null, Buffer.from(msg, 'utf8'), w.privateKey);
  assert.equal(verifySignedMessage(msg, sig.toString('base64'), other.address), false);
});

test('malformed signature fails safely', () => {
  const w = newWallet();
  assert.equal(verifySignedMessage('m', 'not-base64-!!', w.address), false);
  assert.equal(verifySignedMessage('m', Buffer.from('short').toString('base64'), w.address), false);
});

test('base58Decode yields a 32-byte pubkey', () => {
  const w = newWallet();
  assert.equal(base58Decode(w.address).length, 32);
});
