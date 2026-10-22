'use strict';
/**
 * Who signed a wallet's personal_sign message: EIP-191 hashing and secp256k1
 * public-key recovery, with no dependency on `ethers`.
 *
 * WHY THIS EXISTS. Production resolves `ethers` to a stub with no
 * `verifyMessage` (app/test/ethers_stub_calldata.test.js reproduces the stub),
 * so `auth.js` threw inside its try and every wallet sign-in answered 401
 * "Signature verification failed". Measured on the live site on 21 October
 * 2026: a well-formed signature from a different key, which a working check
 * answers "Signature does not match the wallet", got "verification failed".
 * keccak256 (lib/keccak256.js) and ABI encoding (lib/abi_call.js) were moved
 * in-house for the same stub; this is the third.
 *
 * Checked against real ethers, signature by signature, in
 * app/test/wallet_sign_in_works_without_ethers.test.js.
 */
const { keccak256, toUtf8Bytes } = require('./keccak256');

// secp256k1: y² = x³ + 7 over F_P, base point G of prime order N.
const P = 2n ** 256n - 2n ** 32n - 977n;
const N = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEBAAEDCE6AF48A03BBFD25E8CD0364141n;
const G = [0x79BE667EF9DCBBAC55A06295CE870B07029BFCDB2DCE28D959F2815B16F81798n,
  0x483ADA7726A3C4655DA4FBFC0E1108A8FD17B448A68554199C47D08FFB10D4B8n, 1n];
const INFINITY = [0n, 1n, 0n];

const mod = (a, m) => { const r = a % m; return r >= 0n ? r : r + m; };
function powMod(b, e, m) {
  let r = 1n;
  let x = mod(b, m);
  let k = e;
  while (k > 0n) {
    if (k & 1n) r = (r * x) % m;
    x = (x * x) % m;
    k >>= 1n;
  }
  return r;
}
const inv = (a, m) => powMod(a, m - 2n, m); // both moduli are prime

// Jacobian coordinates [X, Y, Z] for x = X/Z², y = Y/Z³; Z = 0 is infinity.
function dbl([X, Y, Z]) {
  if (Z === 0n || Y === 0n) return INFINITY;
  const YY = (Y * Y) % P;
  const S = (4n * X * YY) % P;
  const M = (3n * X * X) % P; // the curve's a is 0
  const X3 = mod(M * M - 2n * S, P);
  return [X3, mod(M * (S - X3) - 8n * YY * YY, P), (2n * Y * Z) % P];
}

function add(p1, p2) {
  if (p1[2] === 0n) return p2;
  if (p2[2] === 0n) return p1;
  const [X1, Y1, Z1] = p1;
  const [X2, Y2, Z2] = p2;
  const Z1Z1 = (Z1 * Z1) % P;
  const Z2Z2 = (Z2 * Z2) % P;
  const U1 = (X1 * Z2Z2) % P;
  const U2 = (X2 * Z1Z1) % P;
  const S1 = (((Y1 * Z2) % P) * Z2Z2) % P;
  const S2 = (((Y2 * Z1) % P) * Z1Z1) % P;
  if (U1 === U2) return S1 === S2 ? dbl(p1) : INFINITY;
  const H = mod(U2 - U1, P);
  const R = mod(S2 - S1, P);
  const HH = (H * H) % P;
  const HHH = (H * HH) % P;
  const V = (U1 * HH) % P;
  const X3 = mod(R * R - HHH - 2n * V, P);
  return [X3, mod(R * (V - X3) - S1 * HHH, P), (((H * Z1) % P) * Z2) % P];
}

function mul(k, point) {
  let r = INFINITY;
  let a = point;
  let e = k;
  while (e > 0n) {
    if (e & 1n) r = add(r, a);
    a = dbl(a);
    e >>= 1n;
  }
  return r;
}

function affine([X, Y, Z]) {
  if (Z === 0n) return null;
  const zi = inv(Z, P);
  const zi2 = (zi * zi) % P;
  return [(X * zi2) % P, (((Y * zi2) % P) * zi) % P];
}

const hex32 = (n) => n.toString(16).padStart(64, '0');

/** keccak256 of the EIP-191 personal message: "\x19Ethereum Signed Message:\n" + length + message. */
function hashPersonalMessage(message) {
  const body = typeof message === 'string' ? toUtf8Bytes(message) : Uint8Array.from(message);
  const prefix = toUtf8Bytes(`\x19Ethereum Signed Message:\n${body.length}`);
  const all = new Uint8Array(prefix.length + body.length);
  all.set(prefix, 0);
  all.set(body, prefix.length);
  return keccak256(all);
}

/**
 * A signature as { r, s, recid }, or null. Accepts what ethers accepts for a
 * personal_sign signature: 65 bytes with v of 27 or 28 (or 0 or 1), or the
 * 64-byte compact form of EIP-2098 (the recovery bit in s's top bit); r and s
 * in range, and s with its top bit clear (ethers refuses a "non-canonical s").
 */
function parseSignature(signature) {
  const m = /^0x([0-9a-fA-F]{128}|[0-9a-fA-F]{130})$/.exec(String(signature || '').trim());
  if (!m) return null;
  const r = BigInt('0x' + m[1].slice(0, 64));
  let s = BigInt('0x' + m[1].slice(64, 128));
  let recid;
  if (m[1].length === 128) {
    recid = s >= 2n ** 255n ? 1 : 0;
    s &= 2n ** 255n - 1n;
  } else {
    const v = parseInt(m[1].slice(128, 130), 16);
    recid = v === 27 || v === 0 ? 0 : v === 28 || v === 1 ? 1 : -1;
  }
  if (recid < 0) return null;
  if (r <= 0n || r >= N || s <= 0n || s >= N) return null;
  if (s >= 2n ** 255n) return null;
  return { r, s, recid };
}

/**
 * The address (lowercase 0x-hex) whose key produced `signature` over the
 * personal message `message`, or null when the signature is malformed or
 * recovers no point. Never throws for bad input: a sign-in route turns null
 * into "does not match".
 */
function recoverPersonalSignAddress(message, signature) {
  const sig = parseSignature(signature);
  if (!sig) return null;
  const x = sig.r; // r < N < P, so x = r (the r + N branch needs v of 29/30, refused above)
  const y2 = mod(x * x * x + 7n, P);
  let y = powMod(y2, (P + 1n) / 4n, P);
  if ((y * y) % P !== y2) return null; // r is not the x of any curve point
  if ((y & 1n) !== BigInt(sig.recid)) y = P - y;
  const e = mod(BigInt(hashPersonalMessage(message)), N);
  const rInv = inv(sig.r, N);
  const u1 = mod(-e * rInv, N);
  const u2 = (sig.s * rInv) % N;
  const q = affine(add(mul(u1, G), mul(u2, [x, y, 1n])));
  if (!q) return null;
  const pub = Buffer.from(hex32(q[0]) + hex32(q[1]), 'hex');
  return '0x' + keccak256(pub).slice(-40);
}

/** True only when `signature` over `message` was made by `address`'s key. */
function signedBy(message, signature, address) {
  const got = recoverPersonalSignAddress(message, signature);
  return got !== null && got === String(address || '').toLowerCase();
}

module.exports = { recoverPersonalSignAddress, signedBy, hashPersonalMessage, parseSignature };
