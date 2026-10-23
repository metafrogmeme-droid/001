'use strict';
/**
 * Replay recorded chain answers through the same reader. No network.
 * A fixture is the JSON-RPC transcript of one real contract, plus the
 * flags that reading produced on the day it was recorded.
 */
const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

const { precheck, DISCLAIMER } = require('../lib/rwa_precheck');

const DIR = path.join(__dirname, 'fixtures', 'rwa');
const FILES = fs.readdirSync(DIR).filter((name) => name.endsWith('.json')).sort();

function replay(calls) {
  const pending = calls.slice();
  return async (method, params) => {
    const key = JSON.stringify([method, params]);
    const at = pending.findIndex((row) => JSON.stringify([row.method, row.params]) === key);
    assert.ok(at >= 0, 'no recorded answer for ' + method);
    const row = pending.splice(at, 1)[0];
    if (row.ok === true) return { ok: true, result: row.result };
    return { ok: false, reason: row.reason || 'unreadable' };
  };
}

function walkKeys(value, bad) {
  if (Array.isArray(value)) {
    value.forEach((item) => walkKeys(item, bad));
    return;
  }
  if (!value || typeof value !== 'object') return;
  for (const key of Object.keys(value)) {
    if (/^(price|answer|usd|pnl|dollar)$/i.test(key)) bad.push(key);
    walkKeys(value[key], bad);
  }
}

test('recorded fixtures cover an ERC-3643 contract and a feed where one was found', () => {
  const byRole = {};
  for (const file of FILES) {
    const fx = JSON.parse(fs.readFileSync(path.join(DIR, file), 'utf8'));
    if (!byRole[fx.chain]) byRole[fx.chain] = new Set();
    byRole[fx.chain].add(fx.role);
  }
  for (const chain of ['ethereum', 'base', 'avalanche', 'arbitrum', 'polygon']) {
    assert.ok(byRole[chain] && byRole[chain].has('erc3643'), chain + ' erc3643');
  }
  assert.ok(byRole.ethereum.has('por') && byRole.ethereum.has('nav'));
  assert.ok(byRole.base.has('oracle') && byRole.base.has('marker'));
  assert.ok(byRole.avalanche.has('por'));
  assert.ok(byRole.arbitrum.has('por'));
  assert.ok(byRole.polygon.has('por'));
  assert.ok(byRole.bnb && byRole.bnb.has('tokenized'), 'bnb tokenized');
  assert.ok(byRole.zksync && byRole.zksync.has('tokenized'), 'zksync tokenized');
  assert.ok(!FILES.some((name) => name.startsWith('monad-')));
});

for (const file of FILES) {
  test('fixture ' + file + ' replays to the same flags', async () => {
    const raw = fs.readFileSync(path.join(DIR, file), 'utf8');
    assert.ok(!/https?:\/\//i.test(raw), 'a fixture must not name an RPC URL');
    const fx = JSON.parse(raw);
    const reading = await precheck({
      chain: fx.chain,
      asset: fx.asset,
      rpc: replay(fx.calls),
      env: {},
    });
    assert.equal(reading.read, true, reading.reason);
    assert.equal(reading.disclaimer, DISCLAIMER);
    assert.equal(reading.chain.id, fx.chain);
    assert.equal(reading.block.number, fx.block.number);
    assert.equal(reading.code, fx.code);
    const got = reading.flags.map((f) => f.id + ':' + f.state);
    assert.deepEqual(got, fx.flags);
    assert.deepEqual(reading.unreadable, fx.unreadable);
    for (const f of reading.flags) {
      assert.ok(f.tone === 'caution' || f.tone === 'muted' || f.tone === 'neutral', f.id);
      assert.ok(!/recommend|investment advice|safe|\$/i.test(f.text), f.id);
    }
    const bad = [];
    walkKeys(reading, bad);
    assert.deepEqual(bad, []);
    if (fx.role === 'erc3643') {
      assert.ok(got.includes('erc3643:detected'));
      assert.equal(reading.setup.registry.state, 'set');
      assert.ok(got.includes('forced_transfer:observed'));
      assert.equal(reading.token.version, '4.1.3');
    }
    if (fx.role === 'por' || fx.role === 'nav' || fx.role === 'oracle') {
      assert.equal(reading.oracle.referenced, true);
      assert.equal(reading.oracle.kind, fx.oracle.kind);
      assert.equal(reading.oracle.source, fx.oracle.source);
      assert.equal(reading.oracle.stale, fx.oracle.stale);
      assert.equal(reading.oracle.heartbeat_seconds, null);
      assert.equal(typeof reading.oracle.age_seconds, 'number');
      assert.ok(!('answer' in reading.oracle));
    }
    if (fx.role === 'marker') {
      assert.equal(reading.code, 'marker');
      assert.ok(got.includes('selectors:not_evm'));
      assert.ok(!got.includes('forced_transfer:not_observed'));
    }
    if (fx.role === 'tokenized') {
      assert.ok(!got.includes('erc3643:detected'), fx.chain);
      assert.ok(fx.note.includes('had no code'), fx.chain);
      assert.equal(typeof reading.token.name, 'string');
      assert.ok(reading.token.name.length > 0, fx.chain);
    }
  });
}
