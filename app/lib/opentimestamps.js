'use strict';
/**
 * The daily seal root timestamped in Bitcoin, through OpenTimestamps.
 *
 * WHAT IT ADDS. A root anchored on Base or DogeOS is bounded by that chain's
 * block clock, which one sequencer sets. OpenTimestamps commits a hash into a
 * Bitcoin block: public calendar servers (run by the OpenTimestamps project
 * and others) aggregate the hashes they are sent into a Merkle tree and put
 * its root in a Bitcoin transaction, a few hours apart. The proof is the path
 * from our hash to that block's Merkle root, and anyone can check it with the
 * reference client (`ots verify`) against their own Bitcoin node.
 *
 * NOTHING HERE SIGNS OR SENDS A TRANSACTION. Submitting is an HTTP POST of a
 * 32-byte hash to the calendars; the calendars pay for and send the Bitcoin
 * transaction. No key, no wallet, no fee (the owner's rule: the server never
 * signs or sends any transaction).
 *
 * WHAT IS STAMPED. The same statement the chains carry, as a file:
 * utf8("RCROOT1:<day>:<root>") (lib/root_anchor.js payloadFor). Its SHA-256 is
 * the digest sent, so the proof is a standard detached `.ots` file for that
 * text: download both and run `ots verify RCROOT1-<day>.txt`.
 *
 * THE FORMAT is OpenTimestamps' own (python-opentimestamps, core/timestamp.py,
 * core/op.py, core/notary.py, core/serialize.py): a tree of operations from
 * the digest, each leaf an attestation. Only what the calendars use is
 * computed here; a proof carrying an operation this file cannot compute is
 * "cannot be checked here", never "invalid".
 */
const crypto = require('crypto');
const { keccak256 } = require('./keccak256');

/**
 * The calendars the reference client uses by default
 * (opentimestamps-client, otsclient/cmds.py), each answering a POST of a
 * digest on 21 October 2026.
 */
const CALENDARS = Object.freeze([
  'https://a.pool.opentimestamps.org',
  'https://b.pool.opentimestamps.org',
  'https://a.pool.eternitywall.com',
  'https://ots.btc.catallaxy.com',
]);

/** Keyless Bitcoin block-header readers (Esplora API), the second a fallback. */
const BITCOIN_HEADER_APIS = Object.freeze(['https://blockstream.info/api', 'https://mempool.space/api']);

const HEADER_MAGIC = Buffer.from('004f70656e54696d657374616d7073000050726f6f6600bf89e2e884e89294', 'hex');
const MAJOR_VERSION = 1;
const TAG_PENDING = '83dfe30d2ef90c8e';
const TAG_BITCOIN = '0588960d73d71901';
const MAX_MSG = 4096;
const MAX_DEPTH = 256;

// Operations: tag -> [name, arity]. sha256 is the file-hash op the header names.
const OPS = {
  0x08: ['sha256', 1], 0x02: ['sha1', 1], 0x03: ['ripemd160', 1], 0x67: ['keccak256', 1],
  0xf0: ['append', 2], 0xf1: ['prepend', 2], 0xf2: ['reverse', 1], 0xf3: ['hexlify', 1],
};

class OtsError extends Error {}

function hashWith(name, msg) {
  if (name === 'keccak256') return Buffer.from(keccak256(msg).slice(2), 'hex');
  return crypto.createHash(name).update(msg).digest();
}

/** Apply one operation to a message. Throws OtsError when it cannot be computed here. */
function applyOp(op, msg) {
  let out;
  switch (op.name) {
    case 'append': out = Buffer.concat([msg, op.arg]); break;
    case 'prepend': out = Buffer.concat([op.arg, msg]); break;
    case 'reverse': out = Buffer.from(msg).reverse(); break;
    case 'hexlify': out = Buffer.from(msg.toString('hex'), 'utf8'); break;
    default:
      try { out = hashWith(op.name, msg); } catch (e) {
        throw new OtsError(`the ${op.name} operation cannot be computed here`);
      }
  }
  if (out.length > MAX_MSG) throw new OtsError('message too long');
  return out;
}

// ── reading ──────────────────────────────────────────────────────────────────

class Reader {
  constructor(buf) { this.buf = buf; this.at = 0; }
  bytes(n) {
    if (this.at + n > this.buf.length) throw new OtsError('truncated proof');
    const b = this.buf.subarray(this.at, this.at + n);
    this.at += n;
    return Buffer.from(b);
  }
  byte() { return this.bytes(1)[0]; }
  varuint() {
    let value = 0;
    let shift = 0;
    for (;;) {
      const b = this.byte();
      value += (b & 0x7f) * 2 ** shift;
      if (!(b & 0x80)) return value;
      shift += 7;
      if (shift > 49) throw new OtsError('varuint too long');
    }
  }
  varbytes(max) {
    const n = this.varuint();
    if (n > max) throw new OtsError('field too long');
    return this.bytes(n);
  }
}

function readAttestation(r) {
  const tag = r.bytes(8).toString('hex');
  const payload = r.varbytes(8192);
  if (tag === TAG_PENDING) {
    const p = new Reader(payload);
    const uri = p.varbytes(1000).toString('utf8');
    if (!/^https:\/\/[A-Za-z0-9.:\-/]+$/.test(uri)) throw new OtsError('pending attestation with an unusable calendar URI');
    return { kind: 'pending', uri };
  }
  if (tag === TAG_BITCOIN) return { kind: 'bitcoin', height: new Reader(payload).varuint() };
  return { kind: 'unknown', tag, payload };
}

function readOp(r, tag) {
  const def = OPS[tag];
  if (!def) throw new OtsError(`unknown operation 0x${tag.toString(16)}`);
  const op = { tag, name: def[0] };
  if (def[1] === 2) op.arg = r.varbytes(MAX_MSG);
  return op;
}

/** A timestamp tree for `msg`: { msg, attestations: [...], ops: [{ op, stamp }] }. */
function readTimestamp(r, msg, depth = 0) {
  if (depth > MAX_DEPTH) throw new OtsError('proof nested too deep');
  const t = { msg, attestations: [], ops: [] };
  const one = (tag) => {
    if (tag === 0x00) { t.attestations.push(readAttestation(r)); return; }
    const op = readOp(r, tag);
    t.ops.push({ op, stamp: readTimestamp(r, applyOp(op, msg), depth + 1) });
  };
  let tag = r.byte();
  while (tag === 0xff) {
    one(r.byte());
    tag = r.byte();
  }
  one(tag);
  return t;
}

/** A calendar's answer for `digest`: the timestamp it returned. */
function parseCalendarAnswer(buf, digest) {
  const r = new Reader(buf);
  const t = readTimestamp(r, digest);
  if (r.at !== buf.length) throw new OtsError('trailing bytes after the timestamp');
  return t;
}

/** A detached `.ots` file: { digest, timestamp }. Only SHA-256 file digests. */
function parseProofFile(buf) {
  const r = new Reader(buf);
  if (!r.bytes(HEADER_MAGIC.length).equals(HEADER_MAGIC)) throw new OtsError('not an OpenTimestamps proof');
  if (r.varuint() !== MAJOR_VERSION) throw new OtsError('unsupported proof version');
  if (r.byte() !== 0x08) throw new OtsError('only SHA-256 file digests are read here');
  const digest = r.bytes(32);
  const timestamp = readTimestamp(r, digest);
  if (r.at !== buf.length) throw new OtsError('trailing bytes after the timestamp');
  return { digest, timestamp };
}

// ── writing ──────────────────────────────────────────────────────────────────

function varuint(n) {
  const out = [];
  let v = n;
  do {
    let b = v % 128;
    v = Math.floor(v / 128);
    if (v > 0) b |= 0x80;
    out.push(b);
  } while (v > 0);
  return Buffer.from(out);
}
const varbytes = (b) => Buffer.concat([varuint(b.length), b]);

function writeAttestation(a) {
  let tag;
  let payload;
  if (a.kind === 'pending') { tag = TAG_PENDING; payload = varbytes(Buffer.from(a.uri, 'utf8')); }
  else if (a.kind === 'bitcoin') { tag = TAG_BITCOIN; payload = varuint(a.height); }
  else { tag = a.tag; payload = a.payload; }
  return Buffer.concat([Buffer.from(tag, 'hex'), varbytes(payload)]);
}

function writeOp(op) {
  return op.arg ? Buffer.concat([Buffer.from([op.tag]), varbytes(op.arg)]) : Buffer.from([op.tag]);
}

/** The reference serialisation: every branch but the last behind a 0xff fork marker. */
function writeTimestamp(t) {
  const branches = [
    ...t.attestations.map((a) => Buffer.concat([Buffer.from([0x00]), writeAttestation(a)])),
    ...t.ops.map(({ op, stamp }) => Buffer.concat([writeOp(op), writeTimestamp(stamp)])),
  ];
  if (!branches.length) throw new OtsError('an empty timestamp cannot be written');
  return Buffer.concat(branches.map((b, i) => (i < branches.length - 1 ? Buffer.concat([Buffer.from([0xff]), b]) : b)));
}

function writeProofFile(digest, timestamp) {
  return Buffer.concat([HEADER_MAGIC, varuint(MAJOR_VERSION), Buffer.from([0x08]), digest, writeTimestamp(timestamp)]);
}

// ── reading a tree ───────────────────────────────────────────────────────────

/** Every attestation in the tree, with the message it attests: [{ attestation, msg }]. */
function attestationsOf(t, out = []) {
  for (const a of t.attestations) out.push({ attestation: a, msg: t.msg });
  for (const { stamp } of t.ops) attestationsOf(stamp, out);
  return out;
}

/** Merge `other` (a timestamp for the same message) into `t`, in place. */
function merge(t, other) {
  if (!t.msg.equals(other.msg)) throw new OtsError('cannot merge timestamps of different messages');
  for (const a of other.attestations) {
    if (!t.attestations.some((b) => writeAttestation(b).equals(writeAttestation(a)))) t.attestations.push(a);
  }
  for (const branch of other.ops) {
    const same = t.ops.find((x) => writeOp(x.op).equals(writeOp(branch.op)));
    if (same) merge(same.stamp, branch.stamp);
    else t.ops.push(branch);
  }
  return t;
}

/** The digest stamped for a day: SHA-256 of the same statement the chains carry. */
function statementFor(day, root) { return `RCROOT1:${day}:${root}`; }
function digestFor(day, root) { return crypto.createHash('sha256').update(statementFor(day, root), 'utf8').digest(); }

// ── the calendars ────────────────────────────────────────────────────────────

async function fetchBytes(f, url, opts, timeoutMs) {
  const ctl = typeof AbortController === 'function' ? new AbortController() : null;
  const timer = ctl ? setTimeout(() => ctl.abort(), timeoutMs) : null;
  try {
    const r = await f(url, { ...opts, ...(ctl ? { signal: ctl.signal } : {}) });
    const body = Buffer.from(await r.arrayBuffer());
    return { status: r.status, body };
  } finally { if (timer) clearTimeout(timer); }
}

const HEADERS = { Accept: 'application/vnd.opentimestamps.v1', 'User-Agent': 'runeclaw-seal-roots' };

/**
 * Submit `digest` to every calendar. → { timestamp, calendars: [uri...] } when
 * at least one answered with a timestamp for it; throws OtsError when none did.
 */
async function submit(digest, { fetchImpl, calendars = CALENDARS, timeoutMs = 15000 } = {}) {
  const f = fetchImpl || global.fetch;
  const t = { msg: Buffer.from(digest), attestations: [], ops: [] };
  const answered = [];
  await Promise.all(calendars.map(async (cal) => {
    try {
      const { status, body } = await fetchBytes(f, `${cal}/digest`,
        { method: 'POST', headers: { ...HEADERS, 'Content-Type': 'application/x-www-form-urlencoded' }, body: digest }, timeoutMs);
      if (status !== 200) return;
      merge(t, parseCalendarAnswer(body, Buffer.from(digest)));
      answered.push(cal);
    } catch (e) { /* that calendar did not answer usably; the others may */ }
  }));
  if (!answered.length) throw new OtsError('no calendar accepted the digest');
  return { timestamp: t, calendars: answered.sort() };
}

/**
 * Ask each pending attestation's calendar for the rest of the path, in place.
 * → the number of pending attestations that now lead to a completed one.
 * A calendar that has not reached Bitcoin yet answers 404, which leaves the
 * pending attestation as it was.
 */
async function upgrade(t, { fetchImpl, timeoutMs = 15000 } = {}) {
  const f = fetchImpl || global.fetch;
  let upgraded = 0;
  const walk = async (node) => {
    for (const a of node.attestations.slice()) {
      if (a.kind !== 'pending') continue;
      try {
        const { status, body } = await fetchBytes(f, `${a.uri}/timestamp/${node.msg.toString('hex')}`,
          { method: 'GET', headers: HEADERS }, timeoutMs);
        if (status !== 200) continue;
        const more = parseCalendarAnswer(body, node.msg);
        if (!attestationsOf(more).some((x) => x.attestation.kind !== 'pending')) continue;
        merge(node, more);
        upgraded += 1;
      } catch (e) { /* stays pending */ }
    }
    for (const { stamp } of node.ops) await walk(stamp);
  };
  await walk(t);
  return upgraded;
}

/**
 * Check the tree's Bitcoin attestations against Bitcoin block headers.
 * → { status: 'verified', height, block_time }   the earliest attested block's
 *     Merkle root equals the message the proof leads to
 * → { status: 'pending' }                         no Bitcoin attestation yet
 * → { status: 'mismatch', reason }                a header was read and does not match
 * → { status: 'unknown', reason }                 no header could be read — not a verdict
 */
async function verifyBitcoin(t, { fetchImpl, apis = BITCOIN_HEADER_APIS, timeoutMs = 15000 } = {}) {
  const f = fetchImpl || global.fetch;
  const btc = attestationsOf(t).filter((x) => x.attestation.kind === 'bitcoin')
    .sort((a, b) => a.attestation.height - b.attestation.height);
  if (!btc.length) return { status: 'pending' };
  let lastReason = 'no Bitcoin header source answered';
  for (const { attestation, msg } of btc) {
    if (msg.length !== 32) return { status: 'mismatch', reason: 'the attested message is not 32 bytes' };
    for (const api of apis) {
      try {
        const h = await fetchBytes(f, `${api}/block-height/${attestation.height}`, { method: 'GET' }, timeoutMs);
        if (h.status !== 200) { lastReason = `header source answered ${h.status}`; continue; }
        const hash = h.body.toString('utf8').trim();
        if (!/^[0-9a-f]{64}$/.test(hash)) { lastReason = 'header source answered no block hash'; continue; }
        const b = await fetchBytes(f, `${api}/block/${hash}`, { method: 'GET' }, timeoutMs);
        if (b.status !== 200) { lastReason = `header source answered ${b.status}`; continue; }
        const block = JSON.parse(b.body.toString('utf8'));
        if (!block || block.height !== attestation.height || typeof block.merkle_root !== 'string') {
          lastReason = 'header source answered another block'; continue;
        }
        // The header's Merkle root is shown byte-reversed; the proof's message is in header order.
        const expected = Buffer.from(msg).reverse().toString('hex');
        if (block.merkle_root.toLowerCase() !== expected) {
          return { status: 'mismatch', reason: `the proof does not lead to block ${attestation.height}'s Merkle root` };
        }
        return { status: 'verified', height: attestation.height,
          block_time: new Date(Number(block.timestamp) * 1000).toISOString() };
      } catch (e) { lastReason = `header source unreachable (${e && e.name ? e.name : 'Error'})`; }
    }
  }
  return { status: 'unknown', reason: lastReason };
}

module.exports = {
  CALENDARS, BITCOIN_HEADER_APIS, OtsError,
  statementFor, digestFor, submit, upgrade, verifyBitcoin,
  parseProofFile, writeProofFile, writeTimestamp, parseCalendarAnswer, attestationsOf, merge, applyOp,
};
