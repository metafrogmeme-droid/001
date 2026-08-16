'use strict';
/**
 * docs/PROVABLE_CALLS_SPEC.md, pinned to the code it describes.
 *
 * A published format is a promise to people outside this repo: they write a
 * verifier against the document, not against callseal.js. So the document
 * failing to match the implementation is not a docs bug — it breaks every
 * third-party checker silently, and the receipts keep verifying against the
 * WRONG rules, which is the worst possible failure for a trust format.
 *
 * Everything asserted below is DERIVED from the running code and compared to
 * the prose. Nothing is transcribed by hand, because a transcription is what
 * rots.
 */

const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');

const ROOT = path.join(__dirname, '..', '..');
const SPEC = fs.readFileSync(path.join(ROOT, 'docs', 'PROVABLE_CALLS_SPEC.md'), 'utf8');

const seal = require(path.join(__dirname, '..', 'lib', 'callseal.js'));
const sealroot = require(path.join(__dirname, '..', 'lib', 'sealroot.js'));

const AT = new Date('2026-08-16T12:00:00.000Z');
const keysOf = (json) => Object.keys(JSON.parse(json));

// ── the payload field order the spec publishes is the real one ────────────

test('v1 field order matches the implementation exactly', () => {
  const real = keysOf(seal.canonicalPayload({
    signal_key: 'sig:1', symbol: 'BTC/USDT', direction: 'LONG',
    entry_price: 1, stop_loss: 1, take_profit: 1, confidence: 1,
    pattern: 'p', regime: 'r', created_at: AT,
  }));
  // Key insertion order IS the contract, so the spec must list them in order.
  const block = SPEC.slice(SPEC.indexOf('### 2.1'), SPEC.indexOf('### 2.2'));
  let cursor = -1;
  for (const k of real) {
    const at = block.indexOf(`"${k}"`);
    assert.ok(at > cursor, `v1: "${k}" is missing from the spec or out of order`);
    cursor = at;
  }
});

test('v2 field order matches the implementation exactly', () => {
  const real = keysOf(seal.canonicalArenaPayload({
    trade_key: 'arena:aa', handle: 'h', symbol: 'BTC/USDT', direction: 'LONG',
    entry: 1, leverage: 2, tp: 3, sl: 1, opened_at: AT,
  }));
  const block = SPEC.slice(SPEC.indexOf('### 2.2'), SPEC.indexOf('### 2.3'));
  let cursor = -1;
  for (const k of real) {
    const at = block.indexOf(`"${k}"`);
    assert.ok(at > cursor, `v2: "${k}" is missing from the spec or out of order`);
    cursor = at;
  }
});

test('v3 field order matches the implementation exactly', () => {
  const real = keysOf(seal.canonicalDuelPayload({
    round_id: 1, day: '2026-08-16', symbol: 'BTC/USDT', handle: 'h',
    pick: 'UP', entry_price: 60000, resolves_at: AT, picked_at: AT,
  }));
  const block = SPEC.slice(SPEC.indexOf('### 2.3'), SPEC.indexOf('### 2.4'));
  let cursor = -1;
  for (const k of real) {
    const at = block.indexOf(`"${k}"`);
    assert.ok(at > cursor, `v3: "${k}" is missing from the spec or out of order`);
    cursor = at;
  }
});

test('no payload carries an outcome field, at any version', () => {
  // Rule 2 of §2 and conformance point 2. This is what makes a seal a
  // pre-commitment rather than a summary written afterwards.
  const OUTCOME = ['pnl', 'status', 'exit_price', 'exit', 'closed_at',
    'resolved_at', 'result', 'won', 'win', 'outcome', 'pct'];
  const payloads = [
    seal.canonicalPayload({ signal_key: 's', symbol: 'B', direction: 'L', created_at: AT }),
    seal.canonicalArenaPayload({ trade_key: 't', symbol: 'B', direction: 'L', opened_at: AT }),
    seal.canonicalDuelPayload({ round_id: 1, day: 'd', symbol: 'B', pick: 'UP',
      entry_price: 1, resolves_at: AT, picked_at: AT }),
  ];
  for (const p of payloads) {
    for (const k of keysOf(p)) {
      assert.ok(!OUTCOME.includes(k), `${k} is an outcome and must not be sealed`);
    }
  }
});

// ── the hash rule ─────────────────────────────────────────────────────────

test('seal = sha256 of the served payload string, verbatim', () => {
  const r = seal.sealCall({ signal_key: 'sig:1', symbol: 'BTC/USDT',
    direction: 'LONG', entry_price: 1, stop_loss: 1, take_profit: 1,
    confidence: 1, created_at: AT });
  const derived = crypto.createHash('sha256').update(r.seal_payload, 'utf8').digest('hex');
  assert.strictEqual(r.seal, derived);
  assert.match(r.seal, /^[0-9a-f]{64}$/, 'lowercase hex, as the spec says');
  // Re-serialising must NOT be required — that is the whole point of "hash the
  // served string". If a verifier had to re-canonicalise, it could drift.
  const reserialised = JSON.stringify(JSON.parse(r.seal_payload));
  assert.strictEqual(reserialised, r.seal_payload,
    'the served string must already be canonical');
});

// ── the Merkle rules the spec publishes are the real ones ─────────────────

test('the parent hashes concatenated HEX STRINGS, not decoded bytes', () => {
  const a = 'a'.repeat(64);
  const b = 'b'.repeat(64);
  const expected = crypto.createHash('sha256').update(a + b, 'utf8').digest('hex');
  assert.strictEqual(sealroot.merkleRoot([a, b]), expected,
    'an implementation hashing decoded bytes would produce a different root');
  assert.match(SPEC, /sha256\(\s*utf8\(\s*leftHex \+ rightHex\s*\)\s*\)/);
});

test('leaves are deduplicated and sorted ascending', () => {
  const a = 'a'.repeat(64), b = 'b'.repeat(64);
  // A seal that appears on two surfaces is ONE leaf.
  assert.strictEqual(sealroot.merkleRoot([b, a, a]), sealroot.merkleRoot([a, b]));
  assert.deepStrictEqual(sealroot.canonicalLeaves([b, a, a]), [a, b]);
});

test('an unpaired last node is promoted unchanged', () => {
  const a = 'a'.repeat(64), b = 'b'.repeat(64), c = 'c'.repeat(64);
  const ab = crypto.createHash('sha256').update(a + b, 'utf8').digest('hex');
  const expected = crypto.createHash('sha256').update(ab + c, 'utf8').digest('hex');
  assert.strictEqual(sealroot.merkleRoot([a, b, c]), expected);
});

test('a lone leaf is its own root, and an empty day has none', () => {
  const a = 'a'.repeat(64);
  assert.strictEqual(sealroot.merkleRoot([a]), a);
  // null, not a hash of nothing — an empty day is not a commitment.
  assert.strictEqual(sealroot.merkleRoot([]), null);
});

test('a proof replays exactly as the spec documents it', () => {
  const seals = Array.from({ length: 5 }, (_, i) => String(i).repeat(64));
  const root = sealroot.merkleRoot(seals);
  for (const s of seals) {
    const proof = sealroot.merkleProof(seals, s);
    assert.ok(proof, `proof exists for ${s.slice(0, 4)}`);
    // The exact loop printed in §4.1.
    let h = s;
    for (const step of proof) {
      const pair = step.pos === 'L' ? step.hash + h : h + step.hash;
      h = crypto.createHash('sha256').update(pair, 'utf8').digest('hex');
    }
    assert.strictEqual(h, root, 'the documented replay must reach the root');
  }
});

test('a non-member gets no proof rather than a plausible one', () => {
  assert.strictEqual(sealroot.merkleProof(['a'.repeat(64)], 'b'.repeat(64)), null);
});

// ── the spec keeps its honest limits ──────────────────────────────────────

test('the spec states what a seal does NOT prove', () => {
  // A trust format that omits its limits invites exactly the misreading it
  // should prevent — that a hash is a performance claim.
  for (const claim of ['does not prove', 'was good', 'future performance',
    'completeness gap', 'tamper-evidence']) {
    assert.ok(SPEC.toLowerCase().includes(claim.toLowerCase()),
      `the spec must state its limits — missing: ${claim}`);
  }
});

test('the spec forbids rooting an unfinished day', () => {
  assert.match(SPEC, /MUST NOT be served/);
  assert.match(SPEC, /still open/);
});

test('the spec forbids a partial receipt', () => {
  // Conformance point 5 — the red-herring failure, written down.
  const c = SPEC.slice(SPEC.indexOf('## 5 · Conformance'));
  assert.match(c, /never as an empty or\s*\n?\s*partial one/);
});

test('the spec promises field order is frozen per version', () => {
  assert.match(SPEC, /MUST NOT change an existing version's field order/);
});

test('every implementation file the spec cites exists', () => {
  for (const rel of ['app/lib/callseal.js', 'app/lib/sealroot.js']) {
    assert.ok(fs.existsSync(path.join(ROOT, rel)), rel);
    assert.ok(SPEC.includes(path.basename(rel)), `${rel} is cited`);
  }
});
