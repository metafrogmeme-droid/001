'use strict';
/**
 * TOTP two-factor auth (RFC 6238 / RFC 4226) — dependency-free.
 *
 * Standard authenticator-app 2FA: HMAC-SHA1, 6 digits, 30s period, ±1 step
 * of clock drift accepted. Secrets are 20 random bytes (base32 on the wire,
 * the format every authenticator app expects). Backup codes are one-time:
 * only their SHA-256 hashes are stored, and a used code is removed.
 *
 * SHA-1 here is the RFC-mandated HMAC primitive for interop with Google
 * Authenticator/Aegis/1Password etc. — not a general-purpose hash choice.
 */

const crypto = require('crypto');

const B32_ALPHABET = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ234567';

function base32Encode(buf) {
  let bits = 0, value = 0, out = '';
  for (const byte of buf) {
    value = (value << 8) | byte;
    bits += 8;
    while (bits >= 5) {
      out += B32_ALPHABET[(value >>> (bits - 5)) & 31];
      bits -= 5;
    }
  }
  if (bits > 0) out += B32_ALPHABET[(value << (5 - bits)) & 31];
  return out;
}

function base32Decode(str) {
  const clean = String(str || '').toUpperCase().replace(/[^A-Z2-7]/g, '');
  let bits = 0, value = 0;
  const out = [];
  for (const ch of clean) {
    value = (value << 5) | B32_ALPHABET.indexOf(ch);
    bits += 5;
    if (bits >= 8) {
      out.push((value >>> (bits - 8)) & 0xff);
      bits -= 8;
    }
  }
  return Buffer.from(out);
}

function generateSecret() {
  return base32Encode(crypto.randomBytes(20));
}

/** RFC 4226 HOTP: HMAC-SHA1 + dynamic truncation, 6 digits. */
function hotp(secretB32, counter) {
  const key = base32Decode(secretB32);
  const msg = Buffer.alloc(8);
  msg.writeBigUInt64BE(BigInt(counter));
  const mac = crypto.createHmac('sha1', key).update(msg).digest();
  const offset = mac[mac.length - 1] & 0x0f;
  const code = ((mac[offset] & 0x7f) << 24)
    | (mac[offset + 1] << 16) | (mac[offset + 2] << 8) | mac[offset + 3];
  return String(code % 1_000_000).padStart(6, '0');
}

/** Verify a 6-digit code against now ±1 period (RFC 6238, 30s steps). */
/**
 * A stored secret is either an AES-GCM envelope or legacy plaintext base32.
 *
 * THE SECRET WAS PLAINTEXT BASE32 IN THE `users` TABLE. A database leak handed
 * over every enrolled seed, and a TOTP seed is a permanent second factor —
 * unlike a password hash there is nothing to slow an attacker down, and unlike
 * a session it does not expire. `creds_crypto.js` (AES-256-GCM, shared with
 * the bot) already existed for exchange keys; 2FA never used it.
 *
 * ENCRYPTION IS CONDITIONAL AND THAT IS STATED, NOT HIDDEN. Without
 * WEB_CREDS_KEY a deployment has no key to encrypt with, and refusing to run
 * would lock every 2FA user out of an app that was working a minute ago. So
 * new secrets are sealed when a key exists and stored plaintext when it does
 * not, and `secretsAreSealed()` reports which — so an operator can be told the
 * truth rather than assume the better half.
 */
const ENVELOPE = /^\s*\{.*"ct"\s*:/s;

/** Seal a fresh secret for storage. Plaintext passthrough when unconfigured. */
function sealSecret(plainB32) {
  const creds = require('./creds_crypto');
  if (!creds.isConfigured()) return plainB32;
  return creds.encryptJSON({ s: plainB32 });
}

/**
 * The base32 secret behind a stored value, or null when it cannot be read.
 *
 * NULL, NOT THE CIPHERTEXT. A wrong or rotated key must not fall through to
 * "try the envelope as a secret" — that would compare a code against garbage
 * and report a plain authentication failure, sending the operator hunting for
 * a user error instead of a key problem. Null propagates to a refusal, which
 * is the fail-closed answer for a second factor that cannot be checked.
 */
function openSecret(stored) {
  const v = String(stored || '');
  if (!v) return null;
  if (!ENVELOPE.test(v)) return v;                 // legacy plaintext row
  try {
    const creds = require('./creds_crypto');
    const obj = creds.decryptJSON(v);
    return obj && typeof obj.s === 'string' && obj.s ? obj.s : null;
  } catch (e) {
    return null;
  }
}

/** Whether newly-stored secrets are encrypted at rest on this deployment. */
function secretsAreSealed() {
  return require('./creds_crypto').isConfigured();
}

function verifyTotp(secretB32, code, nowMs) {
  const c = String(code || '').replace(/\s+/g, '');
  // EVERY CALL SITE INHERITS THE FIX. The secret is read in seven places
  // across five files and all of them arrive here or at stepUpBlock, which
  // calls here — so opening at this boundary is what makes the change
  // complete, rather than seven remembered call sites.
  secretB32 = openSecret(secretB32);
  if (!/^\d{6}$/.test(c) || !secretB32) return false;
  const counter = Math.floor((nowMs ?? Date.now()) / 30_000);
  for (const step of [0, -1, 1]) {
    const expected = hotp(secretB32, counter + step);
    if (crypto.timingSafeEqual(Buffer.from(expected), Buffer.from(c))) return true;
  }
  return false;
}

function otpauthUri(secretB32, email) {
  const label = encodeURIComponent(`RUNECLAW:${email || 'account'}`);
  return `otpauth://totp/${label}?secret=${secretB32}&issuer=RUNECLAW&algorithm=SHA1&digits=6&period=30`;
}

// ── One-time backup codes ────────────────────────────────────────────────────

function hashBackupCode(code) {
  return crypto.createHash('sha256')
    .update(String(code || '').toUpperCase().replace(/[^A-Z0-9]/g, ''))
    .digest('hex');
}

/** 8 codes like "7Q2M-KX9D". Return {codes (show ONCE), hashes (store)}. */
function generateBackupCodes(n = 8) {
  const codes = [];
  for (let i = 0; i < n; i++) {
    const raw = base32Encode(crypto.randomBytes(5)).slice(0, 8);
    codes.push(`${raw.slice(0, 4)}-${raw.slice(4)}`);
  }
  return { codes, hashes: codes.map(hashBackupCode) };
}

/** If `code` matches a stored hash, return the remaining hashes; else null. */
function consumeBackupCode(code, hashes) {
  const h = hashBackupCode(code);
  const list = Array.isArray(hashes) ? hashes : [];
  const idx = list.indexOf(h);
  if (idx === -1) return null;
  return list.slice(0, idx).concat(list.slice(idx + 1));
}

module.exports = {
  generateSecret, hotp, verifyTotp, otpauthUri,
  sealSecret, openSecret, secretsAreSealed,
  generateBackupCodes, consumeBackupCode, hashBackupCode,
  base32Encode, base32Decode,
};
