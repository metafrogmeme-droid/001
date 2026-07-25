'use strict';
// Verify a Solana wallet's signMessage() signature — dependency-free.
//
// Solana wallets (Phantom/Backpack) sign with ed25519. Node's built-in `crypto`
// can verify ed25519 if we wrap the raw 32-byte public key in an SPKI DER header,
// so we avoid pulling in tweetnacl / @solana/web3.js just to check a login proof.
//
// Non-custodial: this only ever verifies a signed *login message*. It never sees
// a private key and never authorizes a transaction. DRAFT feature — see
// docs/TOKEN_ROADMAP.md.
const crypto = require('crypto');

const B58 = '123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz';
const B58_MAP = Object.fromEntries([...B58].map((c, i) => [c, i]));

/** Decode a base58 string to a Buffer (Bitcoin/Solana alphabet). */
function base58Decode(str) {
  const s = String(str || '');
  let n = 0n;
  for (const c of s) {
    const v = B58_MAP[c];
    if (v === undefined) throw new Error('invalid base58 character');
    n = n * 58n + BigInt(v);
  }
  const bytes = [];
  while (n > 0n) {
    bytes.unshift(Number(n & 0xffn));
    n >>= 8n;
  }
  // Restore leading zero bytes (each encoded as '1').
  for (const c of s) {
    if (c === '1') bytes.unshift(0);
    else break;
  }
  return Buffer.from(bytes);
}

// Fixed SPKI DER prefix for an Ed25519 public key (RFC 8410), followed by the
// raw 32-byte key.
const ED25519_SPKI_PREFIX = Buffer.from('302a300506032b6570032100', 'hex');

/**
 * Verify that `signature` over `message` was produced by `addressB58`.
 * @param {string} message   the exact UTF-8 message that was signed
 * @param {string} signatureB64  base64-encoded 64-byte ed25519 signature
 * @param {string} addressB58    base58 Solana address (32-byte ed25519 pubkey)
 * @returns {boolean}
 */
function verifySignedMessage(message, signatureB64, addressB58) {
  try {
    const pub = base58Decode(addressB58);
    if (pub.length !== 32) return false;
    const sig = Buffer.from(String(signatureB64 || ''), 'base64');
    if (sig.length !== 64) return false;
    const der = Buffer.concat([ED25519_SPKI_PREFIX, pub]);
    const keyObject = crypto.createPublicKey({ key: der, format: 'der', type: 'spki' });
    return crypto.verify(null, Buffer.from(String(message), 'utf8'), keyObject, sig);
  } catch (_) {
    return false;
  }
}

module.exports = { verifySignedMessage, base58Decode };
