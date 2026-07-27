'use strict';
/**
 * The go-live runbook is operator-facing documentation, but its claims are
 * code-adjacent enough to rot: endpoints it names must exist, the registry
 * address must match the code's, and F-15 hygiene must hold (env var NAMES
 * only — never a value, never key material).
 */
const test = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const path = require('path');

const read = (...p) => fs.readFileSync(path.join(__dirname, '..', ...p), 'utf8');
const doc = read('docs', 'GOLIVE_RUNBOOK.md');

test('every endpoint the runbook names really exists', () => {
  const server = read('server.js') + read('routes', 'tool8257.js');
  for (const ep of ['/api/tool/registration-plan', '/api/nft', '/api/roots']) {
    const prefix = ep.split('/').slice(0, 3).join('/');
    assert.ok(server.includes(`'${prefix}`), `${ep} is claimed but not mounted`);
  }
  assert.match(doc, /\/api\/roots\/anchor-plan\/<YYYY-MM-DD>/);
});

test('the registry address matches the code', () => {
  const m = doc.match(/0x[0-9a-fA-F]{40}/g) || [];
  assert.ok(m.includes('0x265BB2DBFC0A8165C9A1941Eb1372F349baD2cf1'),
    'the registry address rides in the doc');
  const t8257 = read('routes', 'tool8257.js') + read('lib', 'tool8257.js');
  assert.ok(t8257.includes('0x265BB2DBFC0A8165C9A1941Eb1372F349baD2cf1'),
    'and matches the address the code uses');
});

test('the creatorAddress-inside-the-hash trap is documented', () => {
  assert.match(doc, /creatorAddress.*INSIDE the hashed\s+manifest/s);
  assert.match(doc, /never a saved copy/);
  assert.match(doc, /every MCP tool change moves the hash/i);
});

test('F-15: env var names only — no secret-shaped value in the doc', () => {
  // Names are allowed (they are in .env.example); values are not.
  assert.ok(!/=\s*0x[0-9a-fA-F]{16,}/.test(doc), 'no hex key material after =');
  assert.ok(!/[A-Za-z0-9+/]{40,}={0,2}/.test(doc.replace(/`[^`]*`/g, '')),
    'no base64-shaped secret outside code formatting');
  for (const bad of ['LLM_API_KEY', 'JWT_SECRET', 'BOT_SYNC', 'setllm',
    'GATEWAY_SECRET', 'runeclaw123']) {
    assert.ok(!doc.includes(bad), `runbook must not mention ${bad}`);
  }
  assert.match(doc, /No secret value in this repo, ever/);
});

test('the non-custodial doctrine anchors the whole runbook', () => {
  assert.match(doc, /server never holds keys, never signs transactions, never sends them/i);
  assert.match(doc, /vouchers, not\s+transactions/i, 'the voucher-key blast radius is stated');
});
