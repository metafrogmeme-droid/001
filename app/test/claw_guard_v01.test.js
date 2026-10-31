'use strict';
/**
 * Claw Guard v0.1, flag off by default.
 *
 *   lookalike   the first 4 and last 4 hex characters, and not the same address
 *   dust        a zero or a raw amount at or under the ceiling, received from
 *               that address in the last 30 days. Unreadable is not "none"
 *   acceptance  20 published poisoning pairs, at least 18 warnings; 100 normal
 *               sends, at most 2 warnings
 *   aat         draft-sharif-agent-audit-trail-08 field list, and rcverify
 *               still passes on the bundle the record is attached to
 *   flag        the v0 check is unchanged, and the new routes are 404 until
 *               CLAW_GUARD_V01 is on
 *
 * The poisoning pairs are Guan and Li's Poison-Hunter sample (ACM CCS 2024),
 * addresses and transaction hashes only.
 */

process.env.JWT_SECRET = process.env.JWT_SECRET || 'j'.repeat(64);
delete process.env.DATABASE_URL;
delete process.env.CLAW_GUARD_V01;

const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const express = require('express');

const poison = require('../public/js/address-poison.js');
const page = require('../public/js/guard-v01.js');
const v01 = require('../lib/claw_guard_v01');
const logs = require('../lib/poison_logs');
const { CHAINS } = require('../lib/evm_chains');
const aat = require('../lib/aat_export');
const { sealGuard } = require('../lib/callseal');
const { merkleRoot, merkleProof } = require('../lib/sealroot');
const { bundleOf } = require('../lib/proof_bundle');
const rcverify = require('../../tools/rcverify');
const { pool } = require('../db');
const scanSeal = require('../lib/scan_seal');

const CASES = JSON.parse(fs.readFileSync(path.join(__dirname, 'fixtures', 'address_poison_cases.json'), 'utf8'));
const NOW = Date.parse('2026-10-23T12:00:00.000Z');

function check(destination, counterparties, extra) {
  const before = destination;
  const input = Object.assign({ destination: destination, counterparties: counterparties || [], now: NOW }, extra || {});
  const out = poison.checkAddressPoisoning(input);
  assert.strictEqual(input.destination, before, 'the destination was rewritten');
  return out;
}

function textOf(html) {
  return String(html).replace(/<[^>]+>/g, '');
}

test('a lookalike of the first 4 and last 4 warns, and the same address does not', () => {
  const real = '0x' + 'ab'.repeat(2) + '11'.repeat(16) + 'cd'.repeat(2);
  const fake = '0x' + 'ab'.repeat(2) + '22'.repeat(16) + 'cd'.repeat(2);
  const hit = check(fake, [real]);
  assert.strictEqual(hit.ok, true);
  assert.strictEqual(hit.result.level, 'warn');
  assert.strictEqual(hit.result.reason, 'prefix_suffix_match');
  assert.strictEqual(hit.result.lookalike_of, real);
  assert.strictEqual(hit.result.check, 'address_poisoning');
  const same = check('0x' + real.slice(2).toUpperCase(), [real]);
  assert.strictEqual(same.result.level, 'ok');
  assert.strictEqual(same.result.reason, null);
  assert.strictEqual(same.result.lookalike_of, null);
  const prefixOnly = '0x' + 'ab'.repeat(2) + '22'.repeat(16) + 'ee'.repeat(2);
  assert.strictEqual(check(prefixOnly, [real]).result.level, 'ok');
  const suffixOnly = '0x' + 'ff'.repeat(2) + '22'.repeat(16) + 'cd'.repeat(2);
  assert.strictEqual(check(suffixOnly, [real]).result.level, 'ok');
});

test('an address that cannot be read is not an ok', () => {
  assert.deepStrictEqual(poison.checkAddressPoisoning({ destination: 'not an address', counterparties: [] }).ok, false);
  assert.strictEqual(poison.checkAddressPoisoning({ destination: '0x1234', counterparties: [] }).ok, false);
});

test('zero-value and dust warn, a real amount does not, and an unreadable amount is not none', () => {
  const dest = '0x' + '12'.repeat(20);
  const other = '0x' + '34'.repeat(20);
  function inbound(amount, at, from) {
    const row = { from: from || dest, amount: amount, at: arguments.length < 2 ? NOW : at };
    return check(dest, [], { inbound: [row] });
  }
  const zero = inbound('0');
  assert.strictEqual(zero.result.level, 'warn');
  assert.strictEqual(zero.result.reason, 'dust_sender');
  assert.strictEqual(zero.result.lookalike_of, null);
  assert.strictEqual(inbound('0x0').result.reason, 'dust_sender');
  assert.strictEqual(inbound('10000').result.dust, 'flagged');
  assert.strictEqual(inbound('10001').result.level, 'ok');
  assert.strictEqual(inbound('10001').result.dust, 'none');
  const old = inbound('0', NOW - 31 * 24 * 60 * 60 * 1000);
  assert.strictEqual(old.result.dust, 'none');
  assert.strictEqual(old.result.level, 'ok');
  const bad = inbound('1.5');
  assert.strictEqual(bad.result.dust, 'unread');
  assert.strictEqual(bad.result.level, 'ok');
  const noTime = inbound('0', null);
  assert.strictEqual(noTime.result.dust, 'unread');
  assert.strictEqual(inbound('0', NOW, other).result.dust, 'none', 'dust from someone else is not this address');
  assert.strictEqual(check(dest, []).result.dust, 'not_checked');
});

test('a lookalike is reported ahead of dust, and the destination is not a suggested field', () => {
  const real = '0x' + 'ab'.repeat(2) + '11'.repeat(16) + 'cd'.repeat(2);
  const fake = '0x' + 'ab'.repeat(2) + '22'.repeat(16) + 'cd'.repeat(2);
  const both = check(fake, [real], { inbound: [{ from: fake, amount: '0', at: NOW }] });
  assert.strictEqual(both.result.reason, 'prefix_suffix_match');
  assert.strictEqual(both.result.dust, 'flagged');
  assert.strictEqual(both.result.lookalike_of, real);
  for (const k of Object.keys(both.result)) {
    assert.ok(!/suggest|replac|block|submit/i.test(k), k);
  }
});

test('20 published poisoning pairs warn at least 18 times, and 100 normal sends warn at most twice', () => {
  assert.strictEqual(CASES.acceptance.length, 20);
  assert.strictEqual(CASES.normal.length, 100);
  let warns = 0;
  for (const row of CASES.acceptance) {
    const out = check(row.destination, [row.counterparty]);
    assert.strictEqual(out.ok, true, row.tx);
    assert.strictEqual(out.result.level, row.expect, row.tx + ' ' + row.why);
    if (row.expect === 'warn') {
      warns += 1;
      assert.strictEqual(out.result.reason, 'prefix_suffix_match');
      assert.strictEqual(out.result.lookalike_of, row.counterparty);
    }
  }
  assert.ok(warns >= 18, `published pairs warned ${warns} times`);
  let falseWarns = 0;
  const kinds = {};
  for (const row of CASES.normal) {
    const out = check(row.destination, row.counterparties);
    kinds[row.kind] = (kinds[row.kind] || 0) + 1;
    if (out.result.level === 'warn') falseWarns += 1;
    assert.strictEqual(out.result.level, 'ok', row.kind + ' ' + row.destination);
  }
  assert.ok(falseWarns <= 2, `normal sends warned ${falseWarns} times`);
  assert.deepStrictEqual(kinds, { exact: 40, unrelated: 40, prefix_only: 10, suffix_only: 10 });
});

test('the warning highlights differing characters and does not shorten either address', () => {
  const real = '0xcb9da91c741820157e81ae21cddd5d207997b1b6';
  const fake = '0xcb9d34bbb0480e714f4c004cfd8cb72b776db1b6';
  const pair = poison.highlightPair(fake, real);
  assert.strictEqual(textOf(pair.destination), fake);
  assert.strictEqual(textOf(pair.lookalike), real);
  assert.ok(pair.destination.includes('<mark class="poison-diff">'));
  const html = page.renderWarning({
    check: 'address_poisoning', level: 'warn', reason: 'prefix_suffix_match',
    lookalike_of: real, dust: 'not_checked', compared: 1,
  }, fake);
  assert.match(html, /data-poison="warn"/);
  assert.match(html, /v-caution/);
  assert.ok(textOf(html).includes(fake.slice(2)));
  assert.ok(textOf(html).includes(real.slice(2)));
  assert.doesNotMatch(html, /<input|<button|v-clear|✅|suggest|replace/i);
  const dust = page.renderWarning({
    check: 'address_poisoning', level: 'warn', reason: 'dust_sender',
    lookalike_of: null, dust: 'flagged', compared: 0,
  }, fake);
  assert.match(dust, /does not stop you from sending|does not block the send|still the one you typed|still go on/);
  assert.doesNotMatch(dust, /<input|<button/);
  const ok = page.renderWarning({
    check: 'address_poisoning', level: 'ok', reason: null, lookalike_of: null,
    dust: 'not_checked', compared: 2,
  }, fake);
  assert.match(ok, /data-poison="ok"/);
  assert.doesNotMatch(ok, /v-caution|v-clear|✅/);
  assert.match(ok, /Dust history was not checked/);
  const sameAddr = page.renderWarning({
    check: 'address_poisoning', level: 'ok', reason: null, lookalike_of: null,
    dust: 'not_checked', compared: 1,
  }, fake, [fake]);
  assert.match(sameAddr, /same address as a past counterparty/);
  assert.doesNotMatch(sameAddr, /does not match the first 4/);
  const unread = page.renderWarning({
    check: 'address_poisoning', level: 'ok', reason: null, lookalike_of: null,
    dust: 'unread', dust_scope: 'erc20_logs', compared: 1,
  }, fake);
  assert.match(unread, /could not be read/);
});

test('an empty public log is logs_clear, not a claim that no dust exists', () => {
  const dest = '0x' + '12'.repeat(20);
  const local = poison.checkAddressPoisoning({ destination: dest, counterparties: [], now: NOW });
  const fromLogs = poison.checkAddressPoisoning({ destination: dest, counterparties: [], inbound: [], now: NOW });
  const merged = poison.mergePoison(local, fromLogs);
  assert.strictEqual(merged.result.level, 'ok');
  assert.strictEqual(merged.result.dust, 'logs_clear');
  assert.strictEqual(merged.result.dust_scope, 'erc20_logs');
  const failed = poison.mergePoison(local, { read: false });
  assert.strictEqual(failed.result.dust, 'unread');
  assert.strictEqual(failed.result.level, 'ok');
});

test('the log reader asks only the chains the guard already has, and a failure is unread', async () => {
  assert.deepStrictEqual(Object.keys(logs.BLOCK_SEC).sort(), Object.keys(CHAINS).map(String).sort());
  const asked = [];
  const fetchImpl = async (url, init) => {
    asked.push(url);
    const body = JSON.parse(init.body);
    if (body.method === 'eth_blockNumber') return { ok: true, json: async () => ({ result: '0x100' }) };
    if (body.method === 'eth_getLogs') return { ok: true, json: async () => ({ result: [] }) };
    return { ok: false, status: 500, json: async () => ({}) };
  };
  const dest = '0x' + 'ab'.repeat(20);
  const account = '0x' + 'cd'.repeat(20);
  const ok = await logs.readErc20Inbound({ chainId: 8453, account: account, destination: dest, fetchImpl: fetchImpl });
  assert.strictEqual(ok.read, true);
  assert.deepStrictEqual(ok.inbound, []);
  assert.ok(asked.every((u) => CHAINS[8453].rpcs.includes(u)));
  const sep = await logs.readErc20Inbound({ chainId: 84532, account: account, destination: dest, fetchImpl: fetchImpl });
  assert.strictEqual(sep.read, true);
  assert.ok(asked.some((u) => CHAINS[84532].rpcs.includes(u)));
  asked.length = 0;
  const no = await logs.readErc20Inbound({ chainId: 1, account: account, destination: dest, fetchImpl: fetchImpl });
  assert.deepStrictEqual(no, { read: false, reason: 'unsupported_chain' });
  assert.strictEqual(asked.length, 0);
  const down = await logs.readErc20Inbound({
    chainId: 8453, account: account, destination: dest,
    fetchImpl: async () => { throw new Error('down'); },
  });
  assert.strictEqual(down.read, false);
  assert.strictEqual(down.reason, 'unreachable');
});

function bundleFor(level) {
  const scannedAt = new Date('2026-10-01T12:00:00.000Z');
  const sealed = sealGuard({
    scan_key: 'sc_aatv01testkey',
    request: 'tx',
    input_sha256: 'ab'.repeat(32),
    input_bytes: 12,
    level: level,
    reasons: [{ id: 'new_counterparty', sev: 'info' }],
    actions: [],
    unknown: false,
    simulation: null,
    scanned_at: scannedAt,
  });
  const leaves = [sealed.seal, 'a'.repeat(64), 'b'.repeat(64)];
  const receipt = {
    kind: 'presign_scan',
    seal: sealed.seal,
    seal_payload: sealed.seal_payload,
    sealed_at: scannedAt.toISOString(),
    anchor: {
      day: '2026-10-01', root: merkleRoot(leaves), seal_count: 3,
      proof: merkleProof(leaves, sealed.seal), anchors: [],
    },
  };
  return { sealed: sealed, bundle: bundleOf('sc_aatv01testkey', receipt, {}) };
}

function walkKeys(o, acc) {
  if (!o || typeof o !== 'object') return acc;
  for (const k of Object.keys(o)) {
    acc.push(k);
    walkKeys(o[k], acc);
  }
  return acc;
}

test('an exported receipt matches the draft-08 field list and still verifies', async () => {
  const { sealed, bundle } = bundleFor('stop');
  const payloadBefore = bundle.seal_payload;
  const proofBefore = JSON.stringify(bundle.merkle);
  const exported = aat.exportAat(bundle);
  assert.strictEqual(exported.ok, true);
  assert.strictEqual(bundle.seal_payload, payloadBefore);
  assert.strictEqual(JSON.stringify(bundle.merkle), proofBefore);
  assert.strictEqual(exported.body.seal, sealed.seal);
  assert.strictEqual(exported.body.seal_payload, sealed.seal_payload);
  assert.strictEqual(exported.body.format, 'rcproof/1');
  assert.strictEqual(exported.body.aat_draft, 'draft-sharif-agent-audit-trail-08');
  assert.deepStrictEqual(aat.validateAatRecord(exported.body.aat), []);
  const again = aat.exportAat(bundle);
  assert.strictEqual(again.body.aat.record_id, exported.body.aat.record_id);
  assert.notStrictEqual(exported.body.aat.record_id, exported.body.aat.session_id);
  const record = exported.body.aat;
  assert.strictEqual(record.action_type, 'decision');
  assert.strictEqual(record.action_detail.decision_type, 'classify');
  assert.strictEqual(record.action_detail.verdict_level, 'stop');
  assert.strictEqual(record.outcome, 'success');
  assert.strictEqual(record.record_phase, 'post_execution');
  assert.strictEqual(record.trust_level, 'L0');
  assert.strictEqual(record.prev_hash, null);
  assert.strictEqual(record.parent_record_id, null);
  assert.strictEqual(record.principal.principal_type, 'human');
  assert.strictEqual(record.principal.authority_ref, null);
  assert.strictEqual(record.output_hash, sealed.seal);
  assert.strictEqual(record.input_hash, 'ab'.repeat(32));
  assert.strictEqual(record.agent_version, '10.0.0');
  assert.ok(!('signature' in record));
  assert.ok(!('cost_estimate' in record));
  const names = walkKeys(record, []);
  for (const banned of ['user_id', 'telegram', 'telegram_id', 'chat_id', 'stake', 'pnl', 'margin', 'amount', 'currency']) {
    assert.ok(!names.includes(banned), banned);
  }
  const out = await rcverify.verifyBundle(exported.body, { offline: true });
  assert.strictEqual(out.exit, 0, JSON.stringify(out.checks));
  assert.ok(out.checks.some((c) => c.name === 'hash' && c.status === 'pass'));
  assert.ok(out.checks.some((c) => c.name === 'path' && c.status === 'pass'));
  const locked = aat.exportAat(Object.assign({}, bundle, { seal_payload: null }));
  assert.strictEqual(locked.ok, false);
});

test('the flag is off unless it is set, and the v0 page file does not load the warning', () => {
  assert.strictEqual(v01.enabled({}), false);
  assert.strictEqual(v01.enabled({ CLAW_GUARD_V01: '0' }), false);
  assert.strictEqual(v01.enabled({ CLAW_GUARD_V01: '1' }), true);
  assert.strictEqual(v01.enabled({ CLAW_GUARD_V01: 'true' }), true);
  const file = v01.readGuardPage();
  assert.ok(!file.includes('guard-v01'));
  assert.match(file, /guard-page\.js\?v=3/);
  const on = v01.guardPageHtml(file);
  assert.ok(on.includes('/js/guard-v01.js?v=1'));
  assert.ok(on.includes('/js/address-poison.js?v=1'));
  assert.strictEqual(v01.guardPageHtml(on), on);
  const guardJs = fs.readFileSync(path.join(__dirname, '..', 'routes', 'guard.js'), 'utf8');
  assert.ok(!guardJs.includes('CLAW_GUARD_V01'));
  assert.ok(!guardJs.includes('address_poison'));
});

function serve() {
  const app = express();
  app.use(express.json({ limit: '256kb' }));
  app.use('/api/guard/poison', require('../routes/guard_v01'));
  app.use('/api/guard', require('../routes/guard'));
  app.use('/v1/receipts', require('../routes/receipts_v01'));
  return http.createServer(app);
}

function req(method, pathName, body) {
  return new Promise((resolve, reject) => {
    const s = serve();
    s.listen(0, '127.0.0.1', () => {
      const data = body == null ? null : JSON.stringify(body);
      const r = http.request({
        port: s.address().port, path: pathName, method: method,
        headers: data ? { 'content-type': 'application/json', 'content-length': Buffer.byteLength(data) } : {},
      }, (res) => {
        let b = '';
        res.on('data', (d) => { b += d; });
        res.on('end', () => {
          s.close();
          let parsed = null;
          try { parsed = b ? JSON.parse(b) : null; } catch (e) { parsed = b; }
          resolve({ status: res.statusCode, body: parsed });
        });
      });
      r.on('error', (e) => { s.close(); reject(e); });
      if (data) r.end(data); else r.end();
    });
  });
}

test('the new routes stay shut until the flag is on, and a warning is not a block', async () => {
  const real = '0x' + 'ab'.repeat(2) + '11'.repeat(16) + 'cd'.repeat(2);
  const fake = '0x' + 'ab'.repeat(2) + '22'.repeat(16) + 'cd'.repeat(2);
  const body = { destination: fake, counterparties: [real], chain_id: 8453 };
  delete process.env.CLAW_GUARD_V01;
  const shut = await req('POST', '/api/guard/poison', body);
  assert.strictEqual(shut.status, 404);
  const shutExport = await req('GET', '/v1/receipts/sc_nope?format=aat');
  assert.strictEqual(shutExport.status, 404);

  process.env.CLAW_GUARD_V01 = '1';
  try {
    const warned = await req('POST', '/api/guard/poison', body);
    assert.strictEqual(warned.status, 200);
    assert.strictEqual(warned.body.level, 'warn');
    assert.strictEqual(warned.body.reason, 'prefix_suffix_match');
    assert.strictEqual(warned.body.chain_id, 8453);
    const sepolia = await req('POST', '/api/guard/poison', Object.assign({}, body, { chain_id: 84532 }));
    assert.strictEqual(sepolia.status, 200);
    assert.strictEqual(sepolia.body.chain_id, 84532);
    const other = await req('POST', '/api/guard/poison', Object.assign({}, body, { chain_id: 1 }));
    assert.strictEqual(other.status, 400);
    const bad = await req('POST', '/api/guard/poison', { destination: '0xzz', chain_id: 8453 });
    assert.strictEqual(bad.status, 400);

    const before = (await pool.execute('SELECT scan_key FROM scan_seals', []))[0].length;
    await req('POST', '/api/guard/poison', body);
    const after = (await pool.execute('SELECT scan_key FROM scan_seals', []))[0].length;
    assert.strictEqual(after, before, 'the warning stored a row');

    const check = await req('POST', '/api/guard/check', {
      request: 'tx', chain_id: 8453,
      tx: { to: real, data: '0x' },
      counterparties: [fake],
    });
    assert.strictEqual(check.status, 200);
    assert.ok(check.body.level);
    assert.ok(!('address_poisoning' in check.body));
    assert.ok(!('lookalike_of' in check.body));

    const missing = await req('GET', '/v1/receipts/sc_doesnotexist00?format=aat');
    assert.strictEqual(missing.status, 404);
    const noFormat = await req('GET', '/v1/receipts/sc_doesnotexist00');
    assert.strictEqual(noFormat.status, 400);

    const sealed = await scanSeal.sealGuardIfSignedIn({
      request: 'tx', input: '{"to":"0x1"}',
      verdict: { level: 'check', reasons: [{ id: 'new_counterparty', sev: 'info' }], actions: [] },
      decoded: { unknown: false }, simulation: null, userId: 424242,
    });
    assert.strictEqual(sealed.sealed, true, sealed.reason);
    const exp = await req('GET', '/v1/receipts/' + sealed.scan_key + '?format=aat');
    assert.strictEqual(exp.status, 200, JSON.stringify(exp.body));
    assert.deepStrictEqual(aat.validateAatRecord(exp.body.aat), []);
    assert.strictEqual(exp.body.aat_draft, aat.AAT_DRAFT);
    const verified = await rcverify.verifyBundle(exp.body, { offline: true });
    assert.notStrictEqual(verified.exit, 1, JSON.stringify(verified.checks));
    assert.ok(verified.checks.some((c) => c.name === 'hash' && c.status === 'pass'));
    const raw = JSON.stringify(exp.body);
    assert.ok(!raw.includes('424242'));
    assert.ok(!/telegram|user_id|cost_estimate|"stake"/i.test(raw));
    assert.ok(!raw.includes('$'));
  } finally {
    delete process.env.CLAW_GUARD_V01;
  }
});
