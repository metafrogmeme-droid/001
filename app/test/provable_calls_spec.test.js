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

test('the duel payload\'s field order (v4) matches the implementation exactly', () => {
  const real = keysOf(seal.canonicalDuelPayload({
    round_id: 1, day: '2026-08-16', kind: 'mover', symbol: 'BTC/USDT', symbol_b: 'ETH/USDT',
    handle: 'h', pick: 'first', entry_price: 60000, entry_price_b: 3000, resolves_at: AT, picked_at: AT,
  }));
  assert.equal(JSON.parse(seal.canonicalDuelPayload({ round_id: 1, day: 'd', symbol: 'B', pick: 'UP',
    entry_price: 1, resolves_at: AT, picked_at: AT })).v, 4);
  // The v4 document comes first in §2.3, so each key's first occurrence is
  // v4's; v3, frozen, is documented beneath it.
  const block = SPEC.slice(SPEC.indexOf('### 2.3'), SPEC.indexOf('### 2.4'));
  let cursor = -1;
  for (const k of real) {
    const at = block.indexOf(`"${k}"`);
    assert.ok(at > cursor, `v4: "${k}" is missing from the spec or out of order`);
    cursor = at;
  }
  assert.ok(block.indexOf('"v":4') < block.indexOf('"v":3'), 'v3 is still documented, after v4');
});

test('the dealt-round payload\'s field order (v6) matches the implementation exactly', () => {
  const real = keysOf(seal.canonicalDuelRoundPayload({
    day: '2026-10-19', idx: 6, kind: 'sports', symbol: 'FD:441', agent_direction: null,
    match: { event_id: 441, home: 'Arsenal FC', away: 'Chelsea FC', kickoff: AT }, nonce: 'abcdef0123456789', dealt_at: AT,
  }));
  const block = SPEC.slice(SPEC.indexOf('### 2.5'), SPEC.indexOf('### 2.6'));
  let cursor = -1;
  for (const k of real) {
    const at = block.indexOf(`"${k}"`);
    assert.ok(at > cursor, `v6: "${k}" is missing from the spec or out of order`);
    cursor = at;
  }
  const p = JSON.parse(seal.canonicalDuelRoundPayload({ day: 'd', idx: 0, symbol: 'B', agent_direction: 'long', nonce: 'n', dealt_at: AT }));
  assert.equal(p.v, 6);
  assert.equal(p.agent_direction, 'long', 'the stance IS in the commitment; that is the point');
  assert.strictEqual(p.match, null);
  assert.match(seal.newRoundNonce(), /^[0-9a-f]{32}$/, 'thirty-two hex characters (128 bits), as the spec says');
  assert.notEqual(seal.newRoundNonce(), seal.newRoundNonce());
});

test('the settlement payload\'s field order (v7) matches the implementation exactly, and it names the call it settles', () => {
  const real = keysOf(seal.canonicalDuelSettlePayload({
    round_id: 1, call_seal: 'a'.repeat(64), round_seal: 'b'.repeat(64), kind: 'mover', pick: 'first',
    settle_state: 'settled', settle_price: 1, settle_price_b: 2, outcome: 'first', result: 'correct', marks: 1, settled_at: AT,
  }));
  const block = SPEC.slice(SPEC.indexOf('### 2.6'), SPEC.indexOf('### 2.7'));
  let cursor = -1;
  for (const k of real) {
    const at = block.indexOf(`"${k}"`);
    assert.ok(at > cursor, `v7: "${k}" is missing from the spec or out of order`);
    cursor = at;
  }
  const un = JSON.parse(seal.canonicalDuelSettlePayload({ round_id: 1, call_seal: 'a'.repeat(64), round_seal: null,
    kind: 'direction', pick: 'long', settle_state: 'unresolved', settle_price: null, settle_price_b: null,
    outcome: null, result: 'unresolved', marks: 0, settled_at: AT }));
  assert.equal(un.v, 7);
  assert.strictEqual(un.settle_price, null, 'an unresolved settlement seals a null, never a zero');
  assert.strictEqual(un.round_seal, null, 'a call on a round dealt before v6 names no round seal');
  assert.equal(un.marks, 0);
});

test('the Memeclaw launch payload\'s field order (v8) matches the implementation exactly', () => {
  const real = keysOf(seal.canonicalMemeclawLaunchPayload({
    chain_id: 6281971, factory: '0x' + 'a'.repeat(40), token: '0x' + 'b'.repeat(40), creator: '0x' + 'c'.repeat(40),
    name: 'Much Claw', symbol: 'CLAW', description: '', terms_hash: '0x' + 'd'.repeat(64), tx: '0x' + 'e'.repeat(64),
    block_number: 1, launched_at: AT, creator_bought: '0',
  }));
  const block = SPEC.slice(SPEC.indexOf('### 2.7'), SPEC.indexOf('## 3 '));
  assert.ok(block.length > 100, 'the spec has a v8 section');
  // Both ways: the spec's example payload has exactly the implementation's fields.
  const example = JSON.parse(/```json\n([\s\S]*?)```/.exec(block)[1]);
  assert.deepEqual(Object.keys(example), real, 'v8: the spec\'s payload and the sealed one name the same fields, in order');
  let cursor = -1;
  for (const k of real) {
    const at = block.indexOf(`"${k}"`);
    assert.ok(at > cursor, `v8: "${k}" is missing from the spec or out of order`);
    cursor = at;
  }
  const p = JSON.parse(seal.canonicalMemeclawLaunchPayload({ chain_id: 6281971, factory: '0xAB', token: '0xCD',
    creator: '0xEF', name: 'N', symbol: 'S', terms_hash: '0xAA', tx: '0xBB', block_number: 2, launched_at: AT }));
  assert.equal(p.v, 8);
  assert.equal(p.factory, '0xab', 'addresses and hashes are sealed lowercased, one spelling each');
  assert.strictEqual(p.creator_bought, '0', 'no buy is a zero the creator made, stated as such');
  assert.strictEqual(p.description, '');
  for (const k of ['pnl', 'status', 'result', 'outcome', 'settle_price', 'price', 'reserve']) {
    assert.ok(!(k in p), `a launch receipt carries no outcome field: ${k}`);
  }
  // The terms hash in the payload is the one the page computed and the chain recorded.
  const mc = require('../lib/memeclaw');
  const terms = { chain_id: 6281971, factory: '0x' + 'a'.repeat(40), creator: '0x' + 'c'.repeat(40),
    name: 'Much Claw', symbol: 'CLAW', description: '' };
  assert.equal(mc.termsHash(terms), '0x' + crypto.createHash('sha256').update(mc.canonicalTerms(terms), 'utf8').digest('hex'));
  const termsKeys = keysOf(mc.canonicalTerms(terms));
  const termsText = block.slice(block.indexOf('{"v":1,"kind":"memeclaw_terms"'));
  assert.ok(termsText.length > 0 && termsText.length < block.length, 'the spec writes the terms out');
  const specTerms = termsText.slice(0, termsText.indexOf('}') + 1).replace(/:"memeclaw_terms"|:1/g, '');
  assert.deepEqual([...specTerms.matchAll(/"([a-z_]+)"/g)].map((m) => m[1]), termsKeys,
    'the terms the spec writes out are the terms the page hashes, field for field');
  let at = -1;
  for (const k of termsKeys) {
    const i = termsText.indexOf(`"${k}"`, at + 1);
    assert.ok(i > at, `terms: "${k}" is missing from the spec or out of order`);
    at = i;
  }
});

test('no CALL payload carries an outcome field, at any version; only the settlement receipt does', () => {
  // Rule 2 of §2 and conformance point 2. This is what makes a seal a
  // pre-commitment rather than a summary written afterwards. The one payload
  // that carries an outcome is the settlement's (v7), and it names the call
  // seal it settles rather than touching the call's own receipt.
  const OUTCOME = ['pnl', 'status', 'exit_price', 'exit', 'closed_at',
    'resolved_at', 'result', 'won', 'win', 'outcome', 'pct', 'settle_price', 'settle_state'];
  const payloads = [
    seal.canonicalPayload({ signal_key: 's', symbol: 'B', direction: 'L', created_at: AT }),
    seal.canonicalArenaPayload({ trade_key: 't', symbol: 'B', direction: 'L', opened_at: AT }),
    seal.canonicalDuelPayload({ round_id: 1, day: 'd', symbol: 'B', pick: 'UP',
      entry_price: 1, resolves_at: AT, picked_at: AT }),
    seal.canonicalSignalPayload({ signal_key: 's', symbol: 'B', direction: 'L', created_at: AT }),
    seal.canonicalDuelRoundPayload({ day: 'd', idx: 0, symbol: 'B', nonce: 'n', dealt_at: AT }),
  ];
  for (const p of payloads) {
    for (const k of keysOf(p)) {
      assert.ok(!OUTCOME.includes(k), `${k} is an outcome and must not be sealed`);
    }
  }
  const settle = keysOf(seal.canonicalDuelSettlePayload({ round_id: 1, call_seal: 'a'.repeat(64), kind: 'direction',
    pick: 'long', settle_state: 'settled', settle_price: 1, outcome: 'up', result: 'correct', marks: 1, settled_at: AT }));
  assert.ok(settle.includes('outcome') && settle.includes('result') && settle.includes('call_seal'));
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
