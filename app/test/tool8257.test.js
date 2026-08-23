'use strict';
/**
 * ERC-8257 tool surface — the contract under test:
 * - The manifest is FREE and OPEN (no pricing block, no access predicate) and
 *   generated from the same read-only MCP tool registry as /mcp (no drift).
 * - manifestHash = keccak256(RFC 8785 canonical JSON) — recomputed
 *   independently here.
 * - The registration plan is a DRY RUN: calldata for the operator's own
 *   wallet; the module contains no signing/broadcast primitive (source grep).
 * - The invoke endpoint reaches ONLY whitelisted read-only tools.
 */
process.env.JWT_SECRET = 'j'.repeat(64);
delete process.env.DATABASE_URL;
process.env.APP_BASE_URL = 'https://runeclaw.test';
process.env.TOOL_CREATOR_ADDRESS = '0x' + 'ab'.repeat(20);

const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const express = require('express');
const { ethers } = require('ethers');
const t8257 = require('../lib/tool8257');

// ── canonicalization + hash ──────────────────────────────────────────────────

test('jcs: recursively sorted keys, no whitespace, deterministic', () => {
  const a = t8257.jcs({ b: 1, a: { z: [1, 'x', true, null], y: 2 } });
  assert.equal(a, '{"a":{"y":2,"z":[1,"x",true,null]},"b":1}');
  assert.equal(a, t8257.jcs({ a: { y: 2, z: [1, 'x', true, null] }, b: 1 }));
  assert.throws(() => t8257.jcs({ x: Infinity }), /non-finite/);
});

test('manifestHash is keccak256 over the canonical bytes', () => {
  const m = { name: 'x', tags: ['a'] };
  assert.equal(t8257.manifestHash(m),
    ethers.keccak256(ethers.toUtf8Bytes('{"name":"x","tags":["a"]}')));
});

// ── manifest posture ─────────────────────────────────────────────────────────

test('manifest: free, open, read-only, drift-proof against the MCP registry', () => {
  const TOOLS = require('../routes/mcp').TOOLS;
  const m = t8257.buildManifest({ tools: TOOLS });
  assert.equal(m.type, 'https://ercs.ethereum.org/ERCS/erc-8257#tool-manifest-v1');
  assert.equal(m.name, 'runeclaw-intel');
  assert.equal(m.endpoint, 'https://runeclaw.test/api/tool/invoke');
  assert.equal(m.creatorAddress, '0x' + 'ab'.repeat(20));
  assert.ok(!('pricing' in m), 'NO pricing block — x402 stays behind the INTEROP §4 gates');
  assert.ok(!('access' in m), 'NO access predicate — open');
  assert.deepEqual(m.inputs.properties.tool.enum, Object.keys(TOOLS),
    'the advertised tool set IS the MCP registry — one source of truth');
  assert.equal(m.verifiability.tier, 'self-attested');
});

test('registration plan: dry-run calldata for the canonical registry, zero-address predicate', () => {
  const TOOLS = require('../routes/mcp').TOOLS;
  const plan = t8257.buildRegistrationPlan({ tools: TOOLS });
  assert.equal(plan.dry_run, true);
  assert.equal(plan.ready, true);
  assert.equal(plan.registry, '0x265BB2DBFC0A8165C9A1941Eb1372F349baD2cf1');
  assert.equal(plan.recommended_chain_id, 8453, 'Base — same chain as the ERC-8004 root anchor');
  assert.equal(plan.access_predicate, t8257.ZERO_ADDRESS);
  assert.equal(plan.metadata_uri,
    'https://runeclaw.test/.well-known/ai-tool/runeclaw-intel.json');
  // Independently decode the calldata and check it round-trips.
  const iface = new ethers.Interface(
    ['function registerTool(string,bytes32,address) returns (uint256)']);
  const [uri, hash, predicate] = iface.decodeFunctionData('registerTool', plan.calldata);
  assert.equal(uri, plan.metadata_uri);
  assert.equal(hash, t8257.manifestHash(t8257.buildManifest({ tools: TOOLS })));
  assert.equal(predicate.toLowerCase(), t8257.ZERO_ADDRESS);
});

test('unset creator: the plan warns the hash will move BEFORE a wallet can send stale calldata', () => {
  // creatorAddress is hashed into the manifest. A plan built while the env is
  // unset carries a hash that dies the moment TOOL_CREATOR_ADDRESS is set —
  // the plan must say so next to the calldata, not just report ready:false.
  const TOOLS = require('../routes/mcp').TOOLS;
  const saved = process.env.TOOL_CREATOR_ADDRESS;
  const savedAlt = process.env.PROOFOFPNL_AGENT_ADDRESS;
  delete process.env.TOOL_CREATOR_ADDRESS;
  delete process.env.PROOFOFPNL_AGENT_ADDRESS;
  try {
    const plan = t8257.buildRegistrationPlan({ tools: TOOLS });
    assert.equal(plan.ready, false);
    assert.match(plan.hash_warning, /CHANGE manifest_hash/);
    assert.match(plan.instructions[0], /Do not register this calldata yet/);
    // The warning is honest: the hash really does move when the env lands.
    process.env.TOOL_CREATOR_ADDRESS = saved;
    const after = t8257.buildRegistrationPlan({ tools: TOOLS });
    assert.notEqual(plan.manifest_hash, after.manifest_hash,
      'creatorAddress must be part of the hashed bytes');
    assert.ok(!('hash_warning' in after), 'a ready plan carries no warning');
    assert.doesNotMatch(after.instructions[0], /Do not register/);
  } finally {
    process.env.TOOL_CREATOR_ADDRESS = saved;
    if (savedAlt !== undefined) process.env.PROOFOFPNL_AGENT_ADDRESS = savedAlt;
  }
});

test('non-custodial pin: no signing/broadcast primitive in the module', () => {
  const src = fs.readFileSync(path.join(__dirname, '..', 'lib', 'tool8257.js'), 'utf8')
    + fs.readFileSync(path.join(__dirname, '..', 'routes', 'tool8257.js'), 'utf8');
  for (const forbidden of ['sendTransaction', 'signTransaction', 'Wallet(',
    'PRIVATE_KEY', 'signer', 'broadcastTransaction']) {
    assert.ok(!src.includes(forbidden), `must never contain ${forbidden}`);
  }
});

// ── HTTP surface ─────────────────────────────────────────────────────────────

let server, base;

function req(method, p, body) {
  return new Promise((resolve, reject) => {
    const payload = body ? JSON.stringify(body) : null;
    const r = http.request(`${base}${p}`, {
      method,
      headers: payload ? { 'Content-Type': 'application/json' } : {},
    }, (res) => {
      let d = '';
      res.on('data', c => d += c);
      res.on('end', () => resolve({ status: res.statusCode, data: d ? JSON.parse(d) : {} }));
    });
    r.on('error', reject);
    if (payload) r.write(payload);
    r.end();
  });
}

test.before(async () => {
  const app = express();
  app.use(express.json());
  app.use(require('../routes/tool8257'));
  await new Promise((res) => { server = app.listen(0, '127.0.0.1', res); });
  base = `http://127.0.0.1:${server.address().port}`;
});

test.after(() => { if (server) server.close(); });

test('well-known route serves the manifest; unknown slugs 404', async () => {
  const r = await req('GET', '/.well-known/ai-tool/runeclaw-intel.json');
  assert.equal(r.status, 200);
  assert.equal(r.data.name, 'runeclaw-intel');
  assert.ok(!('pricing' in r.data));
  const miss = await req('GET', '/.well-known/ai-tool/other.json');
  assert.equal(miss.status, 404);
});

test('invoke: whitelisted read-only tool answers; junk is rejected', async () => {
  require('../lib/rwa').setTickerFetcher(async () => ({
    ONDO: null, BTCUSDT: { price: 100000, change: 1, volume: 1e9 },
    ONDOUSDT: { price: 1, change: 2, volume: 1e7 },
  }));
  const ok = await req('POST', '/api/tool/invoke', { tool: 'get_rwa_radar', args: {} });
  assert.equal(ok.status, 200);
  assert.equal(ok.data.tool, 'get_rwa_radar');
  assert.ok(ok.data.result);
  require('../lib/rwa').setTickerFetcher(null);

  const unknown = await req('POST', '/api/tool/invoke', { tool: 'place_order', args: {} });
  assert.equal(unknown.status, 400, 'no order machinery, no unknown tools');
  const badArgs = await req('POST', '/api/tool/invoke',
    { tool: 'get_agent_card', args: { nope: 1 } });
  assert.equal(badArgs.status, 400, 'args validated against the tool schema');
});

test('registration plan endpoint is public dry-run data', async () => {
  const r = await req('GET', '/api/tool/registration-plan');
  assert.equal(r.status, 200);
  assert.equal(r.data.dry_run, true);
  assert.match(r.data.non_custodial_note, /never holds a key/);
});


// ── registration drift ───────────────────────────────────────────────────────
//
// The plan cannot detect drift from a value it was never told, and the old
// hash_warning fired ONLY when the creator was unset — so an
// address-to-address change, or an APP_BASE_URL change (endpoint and
// metadataURI are inside the hashed bytes), moved the hash in complete
// silence. Both happened in one deployment on 2026-08-23. Nothing was
// registered yet, so it cost nothing; after a registration the same change
// breaks verification permanently with `ready: true` throughout.

function withEnv(vars, fn) {
  const saved = {};
  for (const [k, v] of Object.entries(vars)) {
    saved[k] = process.env[k];
    if (v === null) delete process.env[k]; else process.env[k] = v;
  }
  try { return fn(); } finally {
    for (const [k, v] of Object.entries(saved)) {
      if (v === undefined) delete process.env[k]; else process.env[k] = v;
    }
  }
}

const REG_TOOLS = () => require('../routes/mcp').TOOLS;

test('drift: a recorded hash that no longer matches is reported LOUDLY', () => {
  withEnv({ REGISTERED_MANIFEST_HASH: '0x' + '11'.repeat(32) }, () => {
    const plan = t8257.buildRegistrationPlan({ tools: REG_TOOLS() });
    assert.equal(plan.registration_check, 'drifted');
    assert.match(plan.registration_drift, /NO LONGER HASHES/);
    assert.match(plan.registration_drift, /0x1111/);
    // First, ahead of everything — an operator reads the top of a list.
    assert.match(plan.instructions[0], /REGISTRATION DRIFT/);
    // Still usable: re-registering is what you DO about drift.
    assert.equal(plan.ready, true);
    assert.ok(plan.calldata && plan.calldata !== '0x');
  });
});

test('drift: a matching recorded hash is quiet', () => {
  const known = t8257.manifestHash(t8257.buildManifest({ tools: REG_TOOLS() }));
  withEnv({ REGISTERED_MANIFEST_HASH: known.toUpperCase() }, () => {
    const plan = t8257.buildRegistrationPlan({ tools: REG_TOOLS() });
    assert.equal(plan.registration_check, 'matches',
      'case must not decide whether a registration verifies');
    assert.ok(!('registration_drift' in plan));
    assert.ok(!plan.instructions.some((i) => /DRIFT/.test(i)));
  });
});

test('drift: NOT RECORDED is its own state, never silence', () => {
  // The state that gets dropped. An operator who registered and forgot to
  // record the hash would otherwise see exactly what a verifying deployment
  // sees — absent rendered as fine.
  withEnv({ REGISTERED_MANIFEST_HASH: null }, () => {
    const plan = t8257.buildRegistrationPlan({ tools: REG_TOOLS() });
    assert.equal(plan.registration_check, 'not_recorded');
    assert.match(plan.registration_note, /CANNOT be detected/);
    assert.ok(!('registered_manifest_hash' in plan));
  });
});

test('drift: a malformed recorded hash is not_recorded, not a false match', () => {
  for (const bad of ['0xnothex', '0x1234', 'deadbeef', '   ']) {
    withEnv({ REGISTERED_MANIFEST_HASH: bad }, () => {
      const plan = t8257.buildRegistrationPlan({ tools: REG_TOOLS() });
      assert.equal(plan.registration_check, 'not_recorded', `for ${bad}`);
    });
  }
});

test('drift: the two changes that silently moved the hash now BOTH trip it', () => {
  // The real incident, replayed. Register at one config, change either input,
  // and the endpoint must say verification is broken.
  const base0 = process.env.APP_BASE_URL;
  const creator0 = process.env.TOOL_CREATOR_ADDRESS;
  const at = (env) => withEnv(env, () =>
    t8257.manifestHash(t8257.buildManifest({ tools: REG_TOOLS() })));

  const registered = at({ APP_BASE_URL: base0, TOOL_CREATOR_ADDRESS: creator0 });

  // (a) APP_BASE_URL moves — endpoint + metadataURI are hashed.
  withEnv({ REGISTERED_MANIFEST_HASH: registered,
    APP_BASE_URL: 'https://www.example-moved.test' }, () => {
    assert.equal(t8257.buildRegistrationPlan({ tools: REG_TOOLS() })
      .registration_check, 'drifted', 'a base-URL change went undetected');
  });

  // (b) creatorAddress moves — the case the old hash_warning could never see,
  // because it only fired when the creator was UNSET.
  withEnv({ REGISTERED_MANIFEST_HASH: registered,
    TOOL_CREATOR_ADDRESS: '0x' + 'cd'.repeat(20) }, () => {
    const plan = t8257.buildRegistrationPlan({ tools: REG_TOOLS() });
    assert.equal(plan.registration_check, 'drifted',
      'an address-to-address change went undetected — the exact old gap');
    assert.ok(!('hash_warning' in plan),
      'the creator IS set here, so the old warning correctly stays silent — '
      + 'which is precisely why the drift check had to exist separately');
  });
});
