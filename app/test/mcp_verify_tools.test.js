'use strict';
// The honesty layer becomes agent-consumable: verify_call and get_seal_roots
// let an AI agent audit RUNECLAW programmatically — recompute the seal, replay
// the Merkle proof, compare the Base anchor — with everything needed for
// independent re-verification riding in the response.

process.env.JWT_SECRET = process.env.JWT_SECRET || 'j'.repeat(64);
delete process.env.DATABASE_URL;

const test = require('node:test');
const assert = require('node:assert');
const crypto = require('node:crypto');

const { TOOLS } = require('../routes/mcp.js');
const { pool } = require('../db.js');

test('both tools exist and are published-data family (no computesOnInput)', () => {
  for (const name of ['verify_call', 'get_seal_roots']) {
    assert.ok(TOOLS[name], `${name} missing from TOOLS`);
    assert.ok(!TOOLS[name].computesOnInput,
      `${name} serves published records — the manifest family split must say so`);
  }
});

test('verify_call returns the record with a server-side recomputation check', async () => {
  // Seed a sealed signal through the sync path's own sealer for realism.
  const { sealCall } = require('../lib/callseal.js');
  const fixed = { signal_key: 'mcpv1', symbol: 'BTCUSDT', direction: 'LONG',
    confidence: 0.7, entry_price: 64000, stop_loss: 62000, take_profit: 70000,
    pattern: null, regime: null, created_at: new Date() };
  const receipt = sealCall(fixed);
  await pool.execute(
    `INSERT INTO signals (signal_key, symbol, direction, confidence, score, pattern, regime,
       entry_price, stop_loss, take_profit, rr, thesis, status, pnl, created_at, resolved_at,
       seal, seal_payload, sealed_at)
     VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
    [fixed.signal_key, fixed.symbol, fixed.direction, fixed.confidence, 0, null, null,
      fixed.entry_price, fixed.stop_loss, fixed.take_profit, 0, null, 'NEW', null,
      fixed.created_at, null, receipt.seal, receipt.seal_payload, fixed.created_at]);

  const out = await TOOLS.verify_call.handler({ key: 'mcpv1' });
  assert.equal(out.found, true);
  assert.equal(out.kind, 'signal');
  assert.equal(out.seal, receipt.seal);
  assert.equal(out.seal_matches_payload, true,
    'the server-side recomputation must agree with the stored seal');
  // And the check is real: the same recomputation from the response fields.
  const re = crypto.createHash('sha256').update(String(out.seal_payload), 'utf8').digest('hex');
  assert.equal(re, out.seal);
  assert.match(out.verify_yourself, /sha256\(utf8\(seal_payload\)\)/);
  assert.match(out.verify_yourself, /RCROOT1/);
});

test('verify_call answers honestly for unknown and malformed keys', async () => {
  const missing = await TOOLS.verify_call.handler({ key: 'sig:doesnotexist' });
  assert.equal(missing.found, false);
  assert.match(missing.error, /No sealed call/);
  const bad = await TOOLS.verify_call.handler({ key: '!!' });
  assert.equal(bad.found, false);
});

test('get_seal_roots documents both constructions alongside the data', async () => {
  const out = await TOOLS.get_seal_roots.handler({});
  assert.ok(Array.isArray(out.roots));
  assert.match(out.construction, /deduped \+ sorted asc/);
  assert.match(out.anchor_payload_format, /RCROOT1:<day>:<root>/,
    'an agent must be able to verify the anchor without reading our source');
});

test('the manifest hash moved — the registration card must be regenerated', () => {
  // Adding tools changes the ERC-8257 manifest hash by design. This test
  // exists so the fact is stated in CI, not discovered on-chain: any
  // registration must use the hash the live /api/tool/registration-plan
  // reports AFTER this deploys.
  const t8257 = require('../lib/tool8257.js');
  const manifest = t8257.buildManifest({ tools: TOOLS });
  const fams = manifest.toolFamilies;
  assert.ok(fams.publishedData.includes('verify_call'));
  assert.ok(fams.publishedData.includes('get_seal_roots'));
  assert.ok(!fams.callerInput.includes('verify_call'));
});
