'use strict';
/**
 * Minimal ABI decoder for what this server READS from a chain: event logs in
 * a transaction receipt and the return value of an eth_call.
 *
 * The encoder's twin (`abi_call.js`), for the same reason: production
 * resolves `ethers` to a stub whose hashing is SHA-256 and whose encoding is
 * '0x', so nothing here may route through it. The type set is exactly what
 * Memeclaw's factory emits and returns, and anything else THROWS rather than
 * decode approximately: a misread reserve or a misread creator would be a
 * confident wrong answer about someone's coin.
 *
 * `app/test/abi_decode.test.js` checks every decoding against real `ethers`.
 */

const { keccak256, toUtf8Bytes } = require('./keccak256');

const SUPPORTED = ['address', 'uint256', 'uint64', 'bool', 'bytes32', 'string'];
const WORD_HEX = 64;

function body(hex) {
  const s = String(hex == null ? '' : hex);
  if (!/^0x([0-9a-fA-F]{2})*$/.test(s)) throw new Error('abi_decode: not even-length 0x hex');
  return s.slice(2).toLowerCase();
}

function wordAt(b, i) {
  const w = b.slice(i * WORD_HEX, (i + 1) * WORD_HEX);
  if (w.length !== WORD_HEX) throw new Error('abi_decode: data ends inside a word');
  return w;
}

function checkType(t) {
  if (!SUPPORTED.includes(t)) {
    throw new Error(`abi_decode: unsupported type "${t}". Supported: ${SUPPORTED.join(', ')}`);
  }
}

function decodeStatic(t, w) {
  if (t === 'uint256') return BigInt('0x' + w);
  if (t === 'uint64') {
    const n = BigInt('0x' + w);
    if (n >= (1n << 64n)) throw new Error('abi_decode: uint64 out of range');
    return n;
  }
  if (t === 'address') {
    if (!/^0{24}/.test(w)) throw new Error('abi_decode: address word has high bits set');
    return '0x' + w.slice(24);
  }
  if (t === 'bool') {
    if (w === '0'.repeat(63) + '0') return false;
    if (w === '0'.repeat(63) + '1') return true;
    throw new Error('abi_decode: bool word is neither 0 nor 1');
  }
  return '0x' + w; // bytes32
}

function decodeString(b, offsetBytes) {
  if (offsetBytes % 32n !== 0n) throw new Error('abi_decode: unaligned string offset');
  const start = Number(offsetBytes / 32n);
  const len = BigInt('0x' + wordAt(b, start));
  if (len > 1_000_000n) throw new Error('abi_decode: string too long');
  const hex = b.slice((start + 1) * WORD_HEX, (start + 1) * WORD_HEX + Number(len) * 2);
  if (hex.length !== Number(len) * 2) throw new Error('abi_decode: data ends inside a string');
  return Buffer.from(hex, 'hex').toString('utf8');
}

/** Decode an ABI-encoded tuple of `types` from 0x hex. Returns an array. */
function decodeWords(types, hex) {
  types.forEach(checkType);
  const b = body(hex);
  return types.map((t, i) => {
    const w = wordAt(b, i);
    return t === 'string' ? decodeString(b, BigInt('0x' + w)) : decodeStatic(t, w);
  });
}

/** keccak256 of an event's canonical signature: its topic0. */
function eventTopic(event) {
  event.inputs.forEach((x) => checkType(x.type));
  const sig = `${event.name}(${event.inputs.map((x) => x.type).join(',')})`;
  return keccak256(toUtf8Bytes(sig));
}

/**
 * Decode one log against `event` ({ name, inputs: [{ name, type, indexed }] }),
 * or return null when the log is not that event. Indexed string arguments
 * are hashes on chain and are refused (none are used here).
 */
function decodeLog(event, log) {
  const topics = (log && log.topics) || [];
  if (!topics.length || String(topics[0]).toLowerCase() !== eventTopic(event)) return null;
  const indexed = event.inputs.filter((x) => x.indexed);
  if (topics.length !== indexed.length + 1) throw new Error(`abi_decode: ${event.name} topic count`);
  const plain = event.inputs.filter((x) => !x.indexed);
  const values = decodeWords(plain.map((x) => x.type), log.data || '0x');
  const args = {};
  let ti = 1; let pi = 0;
  for (const x of event.inputs) {
    if (x.indexed) {
      if (x.type === 'string') throw new Error('abi_decode: indexed strings are hashes; not supported');
      args[x.name] = decodeStatic(x.type, body(topics[ti]).padStart(WORD_HEX, '0'));
      ti += 1;
    } else {
      args[x.name] = values[pi];
      pi += 1;
    }
  }
  return { name: event.name, address: String(log.address || '').toLowerCase(), args };
}

module.exports = { decodeWords, decodeLog, eventTopic, SUPPORTED };
