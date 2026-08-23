'use strict';
/**
 * Minimal ABI call encoder for calldata this server hands to a USER'S WALLET.
 *
 * Same incident as `keccak256.js`, second half. Production resolves `ethers`
 * to a stub whose `encodeFunctionData` returns the string `'0x'`. Three
 * modules build transactions a person is told to send:
 *
 *   * tool8257.js  registerTool(...)   — the ERC-8257 registration
 *   * allowances.js approve(spender,0) — REVOKING a token approval
 *   * nft.js       mint(voucher)       — the badge mint
 *
 * With `'0x'` calldata all three become a zero-value call to the contract that
 * does nothing. The mint wastes gas. The registration wastes gas. The revoke
 * is the one that matters: the user is shown "revoke this approval", their
 * wallet confirms a real transaction, the block explorer shows it succeeded,
 * and the spender's allowance is still unlimited. A safety control that
 * reports success while doing nothing is the worst shape in this repo, and it
 * is not detectable from any response the server returns.
 *
 * So calldata is built here, from the same in-repo keccak256, with no
 * dependency for a host to substitute. The supported type set is deliberately
 * tiny — exactly what those three calls need — and anything else THROWS.
 * A partial encoder that quietly mis-encodes an unknown type would be the
 * original bug with extra steps.
 *
 * `app/test/abi_call.test.js` checks every encoding against real `ethers`
 * (installed here, and the reference implementation), and
 * `app/test/ethers_stub_calldata.test.js` reproduces the stub to prove the
 * three call sites no longer route through it.
 */

const { keccak256, toUtf8Bytes } = require('./keccak256');

const WORD = 32;
const SUPPORTED = ['address', 'uint256', 'bytes32', 'string', 'bytes'];

/** `name(type,type)` → the canonical signature string, whitespace stripped. */
function canonicalSignature(sig) {
  const m = /^\s*([A-Za-z_$][\w$]*)\s*\(([^)]*)\)\s*$/.exec(String(sig || ''));
  if (!m) throw new Error(`abi_call: cannot parse signature: ${sig}`);
  const types = m[2].split(',').map((t) => t.trim()).filter(Boolean);
  for (const t of types) {
    if (!SUPPORTED.includes(t)) {
      throw new Error(
        `abi_call: unsupported type "${t}" in ${sig}. Supported: `
        + `${SUPPORTED.join(', ')}. Add it here with a test against ethers `
        + 'rather than encoding it approximately.');
    }
  }
  return { name: m[1], types, canonical: `${m[1]}(${types.join(',')})` };
}

/** The 4-byte function selector, 0x-prefixed. */
function selector(sig) {
  return keccak256(toUtf8Bytes(canonicalSignature(sig).canonical)).slice(0, 10);
}

function hexBody(hex) {
  return String(hex).replace(/^0x/i, '');
}

/** Left-pad a hex body to one 32-byte word. */
function padLeft(body) {
  if (body.length > WORD * 2) throw new Error('abi_call: value wider than 32 bytes');
  return '0'.repeat(WORD * 2 - body.length) + body;
}

/** Right-pad a hex body to a whole number of 32-byte words (empty stays empty). */
function padRight(body) {
  if (!body.length) return '';
  const rem = body.length % (WORD * 2);
  return rem === 0 ? body : body + '0'.repeat(WORD * 2 - rem);
}

function encodeUint(value) {
  let n;
  if (typeof value === 'bigint') n = value;
  else if (typeof value === 'number') {
    if (!Number.isInteger(value) || value < 0) {
      throw new Error(`abi_call: uint256 needs a non-negative integer, got ${value}`);
    }
    n = BigInt(value);
  } else if (typeof value === 'string' && /^\d+$/.test(value.trim())) {
    n = BigInt(value.trim());
  } else if (typeof value === 'string' && /^0x[0-9a-fA-F]+$/.test(value.trim())) {
    n = BigInt(value.trim());
  } else {
    throw new Error(`abi_call: cannot encode uint256 from ${typeof value}`);
  }
  if (n < 0n || n >= (1n << 256n)) throw new Error('abi_call: uint256 out of range');
  return padLeft(n.toString(16));
}

function encodeAddress(value) {
  const a = String(value == null ? '' : value).trim();
  if (!/^0x[0-9a-fA-F]{40}$/.test(a)) {
    throw new Error(`abi_call: not a 20-byte address: ${a}`);
  }
  return padLeft(a.slice(2).toLowerCase());
}

function encodeBytes32(value) {
  const b = String(value == null ? '' : value).trim();
  if (!/^0x[0-9a-fA-F]{64}$/.test(b)) {
    throw new Error(`abi_call: not a 32-byte value: ${b}`);
  }
  return b.slice(2).toLowerCase();
}

/** Dynamic bytes/string → { head-less } length word + right-padded data. */
function encodeDynamic(type, value) {
  let body;
  if (type === 'string') {
    if (typeof value !== 'string') throw new Error('abi_call: string arg is not a string');
    const bytes = toUtf8Bytes(value);
    body = '';
    for (let i = 0; i < bytes.length; i++) {
      body += (bytes[i] < 16 ? '0' : '') + bytes[i].toString(16);
    }
  } else {
    const raw = String(value == null ? '' : value).trim();
    if (!/^0x([0-9a-fA-F]{2})*$/.test(raw)) {
      throw new Error('abi_call: bytes arg must be an even-length 0x hex string');
    }
    body = raw.slice(2).toLowerCase();
  }
  return encodeUint(body.length / 2) + padRight(body);
}

/**
 * Encode a full call: 0x + selector + head words + tail.
 *
 * @param {string} sig  e.g. 'approve(address,uint256)'
 * @param {Array} args  one per type, in order
 * @returns {string} 0x-prefixed calldata
 */
function encodeCall(sig, args) {
  const { types, canonical } = canonicalSignature(sig);
  const list = args || [];
  if (list.length !== types.length) {
    throw new Error(
      `abi_call: ${canonical} takes ${types.length} args, got ${list.length}`);
  }

  const dynamic = types.map((t) => t === 'string' || t === 'bytes');
  const tails = types.map((t, i) => (dynamic[i] ? encodeDynamic(t, list[i]) : ''));

  // Every head slot is one word, so the tail starts after all of them.
  let offset = types.length * WORD;
  const heads = types.map((t, i) => {
    if (dynamic[i]) {
      const head = encodeUint(offset);
      offset += tails[i].length / 2;
      return head;
    }
    if (t === 'address') return encodeAddress(list[i]);
    if (t === 'uint256') return encodeUint(list[i]);
    return encodeBytes32(list[i]);
  });

  const data = '0x' + hexBody(selector(canonical)) + heads.join('') + tails.join('');
  assertCalldata(data, canonical);
  return data;
}

/**
 * Refuse calldata that is not a real call to `sig`.
 *
 * Called on our own output — belt and braces there — but exported because it
 * is the cheap check any OTHER encoder's result should pass before a plan
 * carrying it is handed to a wallet. `'0x'` fails. A selector for a different
 * function fails. Truncated head words fail.
 */
function assertCalldata(data, sig) {
  const { canonical } = canonicalSignature(sig);
  const want = selector(canonical);
  if (typeof data !== 'string' || !/^0x[0-9a-fA-F]*$/.test(data)) {
    throw new Error(`abi_call: ${canonical} produced non-hex calldata`);
  }
  if (data.length < 10) {
    throw new Error(
      `abi_call: ${canonical} produced ${data.length <= 2 ? 'EMPTY' : 'truncated'} `
      + `calldata (${data}) — a transaction with this data calls nothing and `
      + 'would succeed while doing nothing.');
  }
  if (data.slice(0, 10).toLowerCase() !== want) {
    throw new Error(
      `abi_call: calldata selector ${data.slice(0, 10)} is not ${canonical} (${want})`);
  }
  if ((data.length - 10) % (WORD * 2) !== 0) {
    throw new Error(`abi_call: ${canonical} calldata is not a whole number of words`);
  }
  return data;
}

module.exports = { encodeCall, selector, assertCalldata, canonicalSignature, SUPPORTED };
