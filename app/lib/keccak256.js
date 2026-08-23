'use strict';
/**
 * Keccak-256 (the Ethereum one), with NO dependencies.
 *
 * This file exists because of a real, nearly-expensive incident. The ERC-8257
 * manifest hash was computed with `ethers.keccak256`. In production the web app
 * runs on a host that resolves `ethers` to a small STUB, and the stub's
 * `keccak256` was backed by Node's `crypto`. Node's crypto has no keccak256 at
 * all — `createHash('keccak256')` throws `Digest method not supported` — so the
 * stub fell through to SHA-256 and `/api/tool/registration-plan` served
 *
 *     0x848ee4da2d488d6ad2d3a20b6568f62a73e5b84e9c93feaa484c979b3f033a97
 *
 * where the true keccak256 of the same canonical bytes is
 *
 *     0x7fe3e5ecbf1d4e566d5670d99e78264d1740bd0b025198bcc086c3067737b053
 *
 * Nothing was wrong on this machine: `ethers@6` is really installed here, the
 * cross-check in tool8257.test.js compared our hash to `ethers.keccak256` and
 * passed, and the endpoint returned HTTP 200 with a well-formed 32-byte hex
 * string. A wrong hash and a right hash are the same SHAPE. Had it been sent,
 * `registerTool` would have written it to Base permanently and no ERC-8257
 * verifier could ever have reproduced it.
 *
 * Three doctrines from CLAUDE.md meet here:
 *
 *   * "A module nothing calls is indistinguishable from one that does not
 *     work" — one level out: a dependency the tests resolve differently from
 *     production is a dependency the tests do not test.
 *   * Unreadable is never zero. A hash function that cannot do keccak must
 *     RAISE, never quietly answer with a different algorithm.
 *   * Colour is a claim. So is a 0x-prefixed 32-byte string in a field named
 *     `manifest_hash`.
 *
 * So the algorithm lives here, in the repo, in plain portable JavaScript: no
 * BigInt (32-bit lane halves), no typed-array-only tricks beyond Uint8Array
 * and Uint32Array, no `crypto`, no npm. There is nothing left for a stub to
 * substitute. `app/test/keccak256.test.js` pins it against the published
 * Ethereum vectors AND against real `ethers` over random inputs, and
 * `app/test/ethers_stub_calldata.test.js` reproduces the stub itself.
 *
 * NOT interchangeable with SHA3-256. Keccak's padding byte is 0x01; NIST
 * SHA-3 changed it to 0x06 late in standardisation and Ethereum kept the
 * original. Same permutation, different digest, and the difference is
 * invisible unless you compare against a known vector — which is the whole
 * lesson above.
 */

const RATE = 136;            // 1088 bits — keccak256's rate, capacity 512
const PAD_BYTE = 0x01;       // Keccak, not SHA-3 (which is 0x06)

// Round constants as [low32, high32] little-endian halves.
const RC = [
  [0x00000001, 0x00000000], [0x00008082, 0x00000000], [0x0000808a, 0x80000000],
  [0x80008000, 0x80000000], [0x0000808b, 0x00000000], [0x80000001, 0x00000000],
  [0x80008081, 0x80000000], [0x00008009, 0x80000000], [0x0000008a, 0x00000000],
  [0x00000088, 0x00000000], [0x80008009, 0x00000000], [0x8000000a, 0x00000000],
  [0x8000808b, 0x00000000], [0x0000008b, 0x80000000], [0x00008089, 0x80000000],
  [0x00008003, 0x80000000], [0x00008002, 0x80000000], [0x00000080, 0x80000000],
  [0x0000800a, 0x00000000], [0x8000000a, 0x80000000], [0x80008081, 0x80000000],
  [0x00008080, 0x80000000], [0x80000001, 0x00000000], [0x80008008, 0x80000000],
];

// Rotation offsets r[x + 5y] and the pi permutation's destination lane.
const ROT = [
  0, 1, 62, 28, 27, 36, 44, 6, 55, 20, 3, 10, 43, 25, 39,
  41, 45, 15, 21, 8, 18, 2, 61, 56, 14,
];
const PI = (() => {
  const p = new Array(25);
  for (let i = 0; i < 25; i++) {
    const x = i % 5, y = (i / 5) | 0;
    p[i] = y + 5 * ((2 * x + 3 * y) % 5);   // B[y][2x+3y] = A[x][y]
  }
  return p;
})();

/**
 * Keccak-f[1600] in place. `s` is 50 uint32s: lane i is (s[2i] low, s[2i+1] high).
 */
function keccakF(s) {
  const C = new Uint32Array(10);
  const D = new Uint32Array(10);
  const B = new Uint32Array(50);

  for (let round = 0; round < 24; round++) {
    // θ — column parities, then D[x] = C[x-1] ^ rotl64(C[x+1], 1)
    for (let x = 0; x < 5; x++) {
      C[2 * x] = s[2 * x] ^ s[2 * (x + 5)] ^ s[2 * (x + 10)]
        ^ s[2 * (x + 15)] ^ s[2 * (x + 20)];
      C[2 * x + 1] = s[2 * x + 1] ^ s[2 * (x + 5) + 1] ^ s[2 * (x + 10) + 1]
        ^ s[2 * (x + 15) + 1] ^ s[2 * (x + 20) + 1];
    }
    for (let x = 0; x < 5; x++) {
      const n = ((x + 1) % 5) * 2;
      const p = ((x + 4) % 5) * 2;
      const lo = C[n], hi = C[n + 1];
      D[2 * x] = C[p] ^ ((lo << 1) | (hi >>> 31));
      D[2 * x + 1] = C[p + 1] ^ ((hi << 1) | (lo >>> 31));
    }
    for (let i = 0; i < 25; i++) {
      const d = (i % 5) * 2;
      s[2 * i] ^= D[d];
      s[2 * i + 1] ^= D[d + 1];
    }

    // ρ (rotate) + π (permute) into B
    for (let i = 0; i < 25; i++) {
      const n = ROT[i];
      const lo = s[2 * i], hi = s[2 * i + 1];
      const d = PI[i] * 2;
      if (n === 0) {
        B[d] = lo; B[d + 1] = hi;
      } else if (n === 32) {
        B[d] = hi; B[d + 1] = lo;
      } else if (n < 32) {
        B[d] = (lo << n) | (hi >>> (32 - n));
        B[d + 1] = (hi << n) | (lo >>> (32 - n));
      } else {
        const m = n - 32;
        B[d] = (hi << m) | (lo >>> (32 - m));
        B[d + 1] = (lo << m) | (hi >>> (32 - m));
      }
    }

    // χ — A[x][y] = B[x][y] ^ (¬B[x+1][y] & B[x+2][y])
    for (let y = 0; y < 5; y++) {
      const r = y * 5;
      for (let x = 0; x < 5; x++) {
        const i = (r + x) * 2;
        const a = (r + (x + 1) % 5) * 2;
        const b = (r + (x + 2) % 5) * 2;
        s[i] = B[i] ^ (~B[a] & B[b]);
        s[i + 1] = B[i + 1] ^ (~B[a + 1] & B[b + 1]);
      }
    }

    // ι
    s[0] ^= RC[round][0];
    s[1] ^= RC[round][1];
  }
}

/** UTF-8 encode. Prefers TextEncoder; Buffer is the fallback, not the other way. */
function toUtf8Bytes(str) {
  if (typeof str !== 'string') throw new TypeError('toUtf8Bytes: not a string');
  if (typeof TextEncoder !== 'undefined') return new TextEncoder().encode(str);
  /* istanbul ignore next — only on runtimes without TextEncoder */
  if (typeof Buffer !== 'undefined') return new Uint8Array(Buffer.from(str, 'utf8'));
  throw new Error('no UTF-8 encoder available');
}

/**
 * Bytes for a value that is either a Uint8Array or a 0x-prefixed hex string.
 * REFUSES anything else — including a plain string, which would otherwise be
 * silently UTF-8 encoded. `keccak256('0x1234')` meaning two bytes and
 * `keccak256('hello')` meaning five characters cannot both be inferred, and
 * guessing is how the wrong bytes get hashed.
 */
function bytesOf(value) {
  if (value instanceof Uint8Array) return value;
  if (Array.isArray(value)) return Uint8Array.from(value);
  if (typeof Buffer !== 'undefined' && Buffer.isBuffer(value)) {
    return new Uint8Array(value);
  }
  if (typeof value === 'string' && /^0x([0-9a-fA-F]{2})*$/.test(value)) {
    const out = new Uint8Array((value.length - 2) / 2);
    for (let i = 0; i < out.length; i++) {
      out[i] = parseInt(value.substr(2 + i * 2, 2), 16);
    }
    return out;
  }
  throw new TypeError(
    'keccak256 needs bytes: pass a Uint8Array or an even-length 0x hex string '
    + '(use toUtf8Bytes() for text)');
}

function hexlify(bytes) {
  let out = '0x';
  for (let i = 0; i < bytes.length; i++) {
    out += (bytes[i] < 16 ? '0' : '') + bytes[i].toString(16);
  }
  return out;
}

/** Raw digest of raw bytes — no self-test, so the self-test can use it. */
function digest(bytes) {
  const state = new Uint32Array(50);
  const padLen = RATE - (bytes.length % RATE);
  const total = bytes.length + padLen;
  const buf = new Uint8Array(total);
  buf.set(bytes);
  buf[bytes.length] = PAD_BYTE;
  buf[total - 1] |= 0x80;            // pad10*1; when padLen === 1 this is 0x81

  for (let off = 0; off < total; off += RATE) {
    for (let i = 0; i < RATE / 4; i++) {
      const b = off + i * 4;
      state[i] ^= buf[b] | (buf[b + 1] << 8) | (buf[b + 2] << 16) | (buf[b + 3] << 24);
    }
    keccakF(state);
  }

  const out = new Uint8Array(32);
  for (let i = 0; i < 8; i++) {
    const w = state[i];
    out[i * 4] = w & 0xff;
    out[i * 4 + 1] = (w >>> 8) & 0xff;
    out[i * 4 + 2] = (w >>> 16) & 0xff;
    out[i * 4 + 3] = (w >>> 24) & 0xff;
  }
  return out;
}

/**
 * Known-answer vectors, checked ONCE before the first real hash.
 *
 * The point is not this file — the code below does not change under us. It is
 * everything between here and a browser or a serverless bundler: a transpile
 * that mangles `>>>`, a runtime without Uint32Array wrap-around, a minifier
 * that reorders the round constants. That class of fault produces a
 * well-formed 32-byte hex string, which is exactly what the incident above
 * looked like from outside.
 *
 * Vectors chosen to exercise the parts that break independently: the empty
 * input (pure padding), a short input, and the three rate-boundary cases —
 * 135 bytes (padLen === 1, where the 0x01 and the 0x80 land on the SAME byte
 * as 0x81), 136 (a whole extra padding block), and 137 (two blocks).
 *
 * These are not decorative. The first draft of this file carried two invented
 * placeholder digests for the long cases, and the self-test refused to hash
 * anything until they were replaced with values cross-checked against
 * `ethers.keccak256`. A vector nobody verified is a vector that pins whatever
 * the code happened to do.
 */
const VECTORS = [
  ['', '0xc5d2460186f7233c927e7db2dcc703c0e500b653ca82273b7bfad8045d85a470'],
  ['abc', '0x4e03657aea45a94fc7d47ba826c8d667c0d1e6e33a64a036ec44f58fa12d6c45'],
  ['hello', '0x1c8aff950685c2ed4bc3174f3472287b56d9517b9c948127319a09a7a36deac8'],
  ['a'.repeat(135), '0x34367dc248bbd832f4e3e69dfaac2f92638bd0bbd18f2912ba4ef454919cf446'],
  ['a'.repeat(136), '0xa6c4d403279fe3e0af03729caada8374b5ca54d8065329a3ebcaeb4b60aa386e'],
  ['a'.repeat(137), '0xd869f639c7046b4929fc92a4d988a8b22c55fbadb802c0c66ebcd484f1915f39'],
];

let selfTestState = null;   // null = not run; true = passed; Error = failed

/**
 * Run the vectors. Idempotent, memoised, and THROWS on mismatch rather than
 * returning a boolean nobody checks.
 */
function selfTest() {
  if (selfTestState === true) return true;
  if (selfTestState instanceof Error) throw selfTestState;
  for (const [input, want] of VECTORS) {
    const got = hexlify(digest(toUtf8Bytes(input)));
    if (got !== want) {
      selfTestState = new Error(
        `keccak256 self-test FAILED (input length ${input.length}): `
        + `got ${got}, want ${want}. Refusing to hash — a wrong digest here is `
        + 'indistinguishable from a right one and would be registered on-chain '
        + 'permanently.');
      throw selfTestState;
    }
  }
  selfTestState = true;
  return true;
}

/**
 * keccak256 of bytes (Uint8Array / Buffer / 0x-hex), as a 0x-prefixed 32-byte
 * hex string. Self-tests on first use, then throws forever if that failed.
 */
function keccak256(value) {
  selfTest();
  return hexlify(digest(bytesOf(value)));
}

/** keccak256 of a string's UTF-8 bytes — the manifest-hash case. */
function keccak256Utf8(str) {
  return keccak256(toUtf8Bytes(str));
}

module.exports = {
  keccak256,
  keccak256Utf8,
  toUtf8Bytes,
  hexlify,
  selfTest,
  VECTORS,
  RATE,
  PAD_BYTE,
};
