'use strict';
/**
 * The Risk Scanner's bridge check (lib/lz_verifiers.js, routes/risk.js): how
 * many verifiers a LayerZero app needs before it takes a message.
 *
 *   real answers   a live Base app, as both Base readers answered on
 *                  2026-10-22 (test/fixtures/layerzero), decodes to three
 *                  required verifiers each way, named from LayerZero's list
 *   one is enough  a single required verifier, or none required and one of
 *                  several optional, is said to be one verifier's word
 *   not read       a reader down, readers that disagree, an answer that
 *                  does not decode, a library this check does not know:
 *                  each "could not be read", never "safe"
 *   the route      400 for a malformed question, 503 when unread, and no
 *                  configured reader URL ever served
 */
for (const k of ['LZ_ETHEREUM_RPC_URLS', 'LZ_BASE_RPC_URLS', 'LZ_ARBITRUM_RPC_URLS', 'LZ_OPTIMISM_RPC_URLS']) delete process.env[k];

const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const express = require('express');

const lz = require('../lib/lz_verifiers');
const { keccak256, toUtf8Bytes } = require('../lib/keccak256');
const SNAP = require('../lib/layerzero_v2.json');
const FIX = JSON.parse(fs.readFileSync(path.join(__dirname, 'fixtures', 'layerzero', 'base_oapp_30320.json'), 'utf8'));

const w = (v) => BigInt(v).toString(16).padStart(64, '0');
const addr = (a) => a.replace(/^0x/, '').toLowerCase().padStart(64, '0');
/** abi.encode(UlnConfig) wrapped as getConfig's `bytes` answer, written out by hand from the ABI rules. */
function ulnAnswer({ confirmations = 5, required = [], optional = [], threshold = 0, requiredCount = required.length, optionalCount = optional.length }) {
  const head = [w(confirmations), w(requiredCount), w(optionalCount), w(threshold), w(6 * 32), w((6 + 1 + required.length) * 32)];
  const tail = [w(required.length), ...required.map(addr), w(optional.length), ...optional.map(addr)];
  const payload = [w(32), ...head, ...tail].join('');
  return '0x' + w(32) + w(payload.length / 2) + payload;
}
const A = (n) => '0x' + String(n).repeat(40);

/** A Base reader that answers from `calls` (data -> result), recording what it was asked. */
function reader(calls, { down = [], seen = [] } = {}) {
  return async (url, opts) => {
    seen.push(url);
    if (down.includes(url)) throw new TypeError('fetch failed');
    const { params } = JSON.parse(opts.body);
    assert.strictEqual(params[0].to, SNAP.chains.base.endpoint_v2, 'only the endpoint is asked');
    const data = params[0].data;
    const result = typeof calls === 'function' ? calls(url, data) : calls[data];
    if (result === undefined) return { ok: true, status: 200, json: async () => ({ jsonrpc: '2.0', id: 1, error: { code: 3, message: 'execution reverted' } }) };
    return { ok: true, status: 200, json: async () => ({ jsonrpc: '2.0', id: 1, result }) };
  };
}
const Q = { chain: 'base', oapp: FIX.oapp, eid: FIX.remote_eid };

test('the selectors are the signatures\' keccak, and the snapshot\'s addresses are the ones the live answers named', () => {
  const sel = (s) => keccak256(toUtf8Bytes(s)).slice(0, 10);
  assert.deepStrictEqual(lz.SEL, { sendLibrary: sel('getSendLibrary(address,uint32)'), receiveLibrary: sel('getReceiveLibrary(address,uint32)'),
    config: sel('getConfig(address,address,uint32,uint32)') });
  assert.strictEqual(FIX.readers_agree, true);
  assert.strictEqual(FIX.endpoint, SNAP.chains.base.endpoint_v2);
  const send = Object.entries(FIX.calls).find(([d]) => d.startsWith(lz.SEL.sendLibrary))[1];
  assert.strictEqual(lz.decodeAddress(send), SNAP.chains.base.send_uln_302);
  const recv = Object.entries(FIX.calls).find(([d]) => d.startsWith(lz.SEL.receiveLibrary))[1];
  assert.deepStrictEqual(lz.decodeReceiveLibrary(recv), { library: SNAP.chains.base.receive_uln_302, is_default: false });
  assert.deepStrictEqual(Object.keys(lz.chains()).sort(), ['arbitrum', 'base', 'ethereum', 'optimism']);
  assert.deepStrictEqual(Object.values(lz.chains()).map((c) => [c.key, c.eid]), [['ethereum', 30101], ['base', 30184], ['arbitrum', 30110], ['optimism', 30111]]);
});

test('a live Base app, as both readers answered: three verifiers each way, named from LayerZero\'s list', async () => {
  const seen = [];
  const r = await lz.bridgeCheck(Q, { fetchImpl: reader(FIX.calls, { seen }) });
  assert.strictEqual(r.status, 'read', JSON.stringify(r));
  for (const side of ['send', 'receive']) {
    assert.deepStrictEqual(r[side].required.map((x) => x.name), ['Canary', 'LayerZero Labs', 'Twinstake']);
    assert.deepStrictEqual([r[side].verifiers_needed, r[side].single_verifier, r[side].library_known], [3, false, true]);
  }
  assert.deepStrictEqual([r.send.confirmations, r.receive.confirmations, r.receive.default_library], ['10', '20', false]);
  assert.deepStrictEqual(r.readers, ['mainnet.base.org', 'base-rpc.publicnode.com']);
  assert.deepStrictEqual([...new Set(seen)].sort(), lz.chains().base.rpcs.slice().sort());
  assert.ok(!/"(safe|secure|trusted|verdict)"/.test(JSON.stringify(r)), 'a count, never a verdict');
});

/** The fixture's answers with both getConfig answers replaced by `config`, and an app on the default receive library. */
function withConfig(config, recvLib = SNAP.chains.base.receive_uln_302) {
  return (url, data) => {
    if (data.startsWith(lz.SEL.config)) return config;
    if (data.startsWith(lz.SEL.receiveLibrary)) return '0x' + addr(recvLib) + w(1);
    return FIX.calls[data];
  };
}

test('a configured reader is named by its place, never its URL, and only https URLs are taken', async () => {
  const env = { LZ_BASE_RPC_URLS: 'http://plain.example, https://k3y.quiknode.example/abc, https://two.example/v2/SECRET' };
  assert.deepStrictEqual(lz.chains(env).base.rpcs, ['https://k3y.quiknode.example/abc', 'https://two.example/v2/SECRET']);
  assert.deepStrictEqual(lz.chains({ LZ_BASE_RPC_URLS: 'http://only.example' }).base.rpcs, lz.chains({}).base.rpcs,
    'no usable configured URL falls back to the keyless readers');
  const r = await lz.bridgeCheck(Q, { env, fetchImpl: async (url, opts) => reader(FIX.calls)(url, opts) });
  assert.strictEqual(r.status, 'read');
  assert.deepStrictEqual(r.readers, ['configured rpc 1', 'configured rpc 2']);
  assert.ok(!/quiknode|SECRET|k3y/.test(JSON.stringify(r)), 'no part of a configured URL is in the answer');
  const long = await lz.bridgeCheck({ ...Q, oapp: FIX.oapp + 'a' }, { fetchImpl: () => assert.fail('nothing should be asked') });
  assert.deepStrictEqual([long.status, /0x address/.test(long.reason)], ['refused', true], 'an address one digit too long is not an address');
});

test('one verifier\'s word is said to be one: one required, or none required and one optional of several', async () => {
  let r = await lz.bridgeCheck(Q, { fetchImpl: reader(withConfig(ulnAnswer({ required: [A(1)] }))) });
  assert.deepStrictEqual([r.receive.verifiers_needed, r.receive.single_verifier, r.receive.default_library], [1, true, true]);
  assert.strictEqual(r.receive.required[0].name, null, 'an address LayerZero\'s list does not name is not given a name');
  r = await lz.bridgeCheck(Q, { fetchImpl: reader(withConfig(ulnAnswer({ optional: [A(1), A(2), A(3)], threshold: 1 }))) });
  assert.deepStrictEqual([r.send.verifiers_needed, r.send.single_verifier, r.send.optional.length], [1, true, 3]);
  r = await lz.bridgeCheck(Q, { fetchImpl: reader(withConfig(ulnAnswer({ requiredCount: 255, optional: [A(1), A(2), A(3)], threshold: 2 }))) });
  assert.deepStrictEqual([r.send.verifiers_needed, r.send.single_verifier], [2, false], 'nil required is none required');
  r = await lz.bridgeCheck(Q, { fetchImpl: reader(withConfig(ulnAnswer({ required: [A(1), A(2)], optional: [A(3), A(4)], threshold: 1 }))) });
  assert.deepStrictEqual([r.send.verifiers_needed, r.send.single_verifier, r.send.no_verifier], [3, false, false]);
  r = await lz.bridgeCheck(Q, { fetchImpl: reader(withConfig(ulnAnswer({ requiredCount: 255, threshold: 0 }))) });
  assert.deepStrictEqual([r.send.verifiers_needed, r.send.single_verifier, r.send.no_verifier], [0, false, true],
    'none needed is said to be none, not one');
});

test('not read is never safe: a reader down, readers that disagree, an answer that does not decode, an unknown library', async () => {
  let r = await lz.bridgeCheck(Q, { fetchImpl: reader(FIX.calls, { down: ['https://base-rpc.publicnode.com'] }) });
  assert.deepStrictEqual([r.status, /base-rpc\.publicnode\.com could not be read/.test(r.reason)], ['unknown', true]);
  const split = (url, data) => (url === 'https://mainnet.base.org' && data.startsWith(lz.SEL.config) ? ulnAnswer({ required: [A(1)] }) : FIX.calls[data]);
  r = await lz.bridgeCheck(Q, { fetchImpl: reader(split) });
  assert.deepStrictEqual([r.status, r.disagreement], ['unknown', true], 'two readers, two settings: neither is believed');
  r = await lz.bridgeCheck(Q, { fetchImpl: reader(withConfig(ulnAnswer({ required: [A(1)] }).slice(0, -64))) });
  assert.deepStrictEqual([r.status, r.reason], ['unknown', 'a configuration did not decode']);
  r = await lz.bridgeCheck(Q, { fetchImpl: reader(withConfig(ulnAnswer({ required: [A(1)] }), A(7))) });
  assert.strictEqual(r.status, 'read');
  assert.deepStrictEqual([r.receive.library_known, 'verifiers_needed' in r.receive, r.receive.library], [false, false, A(7)],
    'a library this check does not know is named, and its setting is not read');
  // Every malformed question is refused before anything is asked.
  const none = () => assert.fail('nothing should be asked');
  for (const [q, why] of [[{ ...Q, chain: 'polygon' }, /does not read/], [{ ...Q, oapp: '0x123' }, /0x address/],
    [{ ...Q, eid: 40245 }, /mainnet/], [{ ...Q, eid: 30184 }, /this chain itself/]]) {
    r = await lz.bridgeCheck(q, { fetchImpl: none });
    assert.strictEqual(r.status, 'refused');
    assert.match(r.reason, why);
  }
  assert.strictEqual(lz.decodeUlnConfig('0x'), null);
  assert.strictEqual(lz.decodeUlnConfig('0x' + 'zz'.repeat(32)), null);
});

test('the decoders take an answer only when it is exactly what the ABI says', async () => {
  const good = ulnAnswer({ required: [A(1)], optional: [A(2)], threshold: 1 });
  assert.deepStrictEqual(lz.decodeUlnConfig(good).optionalDVNs, [A(2)]);
  assert.strictEqual(lz.decodeUlnConfig(good + '00'), null, 'not whole 32-byte words');
  assert.strictEqual(lz.decodeUlnConfig(good.replace(addr(A(2)), 'ff'.repeat(12) + addr(A(2)).slice(24))), null, 'a list entry that is not an address');
  assert.strictEqual(lz.decodeUlnConfig(good.replace(w(1) + addr(A(2)), w(3) + addr(A(2)))), null, 'a list longer than the answer');
  assert.strictEqual(lz.decodeUlnConfig('0x' + w(32) + w(64) + w(32) + w(5)), null, 'a struct head shorter than six words');
  assert.strictEqual(lz.decodeAddress('0x' + addr(A(1)) + addr(A(2))), null, 'two words are not one address');
  assert.deepStrictEqual(lz.decodeReceiveLibrary('0x' + addr(A(1)) + w(1)), { library: A(1), is_default: true });
  assert.strictEqual(lz.decodeReceiveLibrary('0x' + addr(A(1)) + w(2)), null, 'a bool is 0 or 1');
  // A nil optional threshold is none, not 255.
  let r = await lz.bridgeCheck(Q, { fetchImpl: reader(withConfig(ulnAnswer({ required: [A(1), A(2)], threshold: 255 }))) });
  assert.deepStrictEqual([r.receive.verifiers_needed, r.receive.optional_threshold], [2, 0]);
  // The endpoint naming a library that does not decode, or one side's setting not decoding, is not read.
  r = await lz.bridgeCheck(Q, { fetchImpl: reader((url, data) => (data.startsWith(lz.SEL.sendLibrary) ? '0x1234' : FIX.calls[data])) });
  assert.deepStrictEqual([r.status, r.reason], ['unknown', 'the endpoint answered something that does not decode']);
  const recvLib = addr(SNAP.chains.base.receive_uln_302);
  r = await lz.bridgeCheck(Q, { fetchImpl: reader((url, data) => (data.startsWith(lz.SEL.config) && data.includes(recvLib) ? '0x' + w(32) : FIX.calls[data])) });
  assert.deepStrictEqual([r.status, r.reason], ['unknown', 'a configuration did not decode'], 'the receive side alone not decoding is not read');
});

test('the route: 400 for a malformed question, 503 when unread, 200 with the count, and no configured reader served', async () => {
  const real = global.fetch;
  const app = express();
  app.use('/api/risk', require('../routes/risk'));
  const server = await new Promise((r) => { const s = app.listen(0, '127.0.0.1', () => r(s)); });
  const base = `http://127.0.0.1:${server.address().port}`;
  const get = (p) => new Promise((resolve, reject) => {
    http.get(`${base}${p}`, (res) => { let d = ''; res.on('data', (c) => { d += c; }); res.on('end', () => resolve({ status: res.statusCode, headers: res.headers, body: JSON.parse(d) })); }).on('error', reject);
  });
  try {
    const q = `/api/risk/bridge?chain=base&oapp=${FIX.oapp}&eid=${FIX.remote_eid}`;
    global.fetch = reader(FIX.calls, { down: lz.chains().base.rpcs });
    let r = await get(q);
    assert.deepStrictEqual([r.status, r.body.error], [503, 'could_not_read']);
    global.fetch = reader(FIX.calls);
    r = await get(q);
    assert.strictEqual(r.status, 200);
    assert.strictEqual(r.body.receive.verifiers_needed, 3);
    assert.strictEqual(r.headers['cache-control'], 'no-store');
    r = await get('/api/risk/bridge?chain=base&oapp=nope&eid=30320');
    assert.strictEqual(r.status, 400);
    process.env.LZ_BASE_RPC_URLS = 'https://base.example/v2/SECRETKEY';
    r = await get('/api/risk/chains');
    assert.deepStrictEqual(r.body.chains.map((c) => c.key), ['ethereum', 'base', 'arbitrum', 'optimism']);
    assert.ok(!JSON.stringify(r.body).includes('SECRETKEY') && !JSON.stringify(r.body).includes('http'), 'no reader URL is served');
  } finally {
    delete process.env.LZ_BASE_RPC_URLS;
    global.fetch = real;
    server.close();
  }
});

test('the page says the count, keeps amber for one or none, and never turns not-read into a reading', async () => {
  const V = require('../public/js/risk-bridge.js');
  const read = await lz.bridgeCheck(Q, { fetchImpl: reader(FIX.calls) });
  let v = V.view(200, read);
  assert.match(v.html, /Messages into this app[\s\S]*3 verifiers must agree[\s\S]*Messages it sends[\s\S]*3 verifiers must agree/);
  assert.ok(!v.html.includes('rk-one'), 'three is plain text, not amber');
  assert.match(v.html, /Canary[\s\S]*LayerZero Labs[\s\S]*Twinstake/);
  assert.match(v.html, /Read by mainnet\.base\.org, base-rpc\.publicnode\.com, who agreed\./);
  const one = await lz.bridgeCheck(Q, { fetchImpl: reader(withConfig(ulnAnswer({ required: [A(1)] }))) });
  v = V.view(200, one);
  assert.match(v.html, /<span class="rk-count rk-one">One verifier’s word is enough<\/span>/);
  assert.match(v.html, /not in LayerZero’s list/, 'an unnamed verifier is said to be unnamed');
  const none = await lz.bridgeCheck(Q, { fetchImpl: reader(withConfig(ulnAnswer({ requiredCount: 255, threshold: 0 }))) });
  assert.match(V.view(200, none).html, /<span class="rk-count rk-one">No verifier has to agree<\/span>/);
  assert.ok(!/One verifier/.test(V.view(200, none).html), 'none is not one');
  const lib = await lz.bridgeCheck(Q, { fetchImpl: reader(withConfig(ulnAnswer({ required: [A(1)] }), A(7))) });
  v = V.view(200, lib);
  assert.match(v.html, /Messages into this app<\/h3><p class="muted">A library this check does not know: not read/);
  assert.match(v.html, /Messages it sends<\/h3><p><span class="rk-count rk-one">/, 'the side that was read is still said');
  // Not a reading, in every shape it arrives: each is "could not be read", never a count.
  const UNREAD = 'Could not be read: not a verdict either way.';
  for (const [status, body] of [[503, { error: 'could_not_read' }], [200, null], [200, { status: 'unknown', receive: read.receive, send: read.send }],
    [200, { ...read, send: undefined }], [500, read], [0, null]]) {
    assert.deepStrictEqual(V.view(status, body), { text: UNREAD }, `${status} ${JSON.stringify(body)}`);
  }
  assert.deepStrictEqual(V.view(400, { error: 'oapp must be a 0x address' }), { text: 'Check the chain, the address and the endpoint id.' });
  assert.strictEqual(V.sideHtml('x', { verifiers_needed: '3' }, (k, en) => en), '<div class="rk-side"><h3>x</h3><p class="muted">' + UNREAD + '</p></div>',
    'a count that is not a number is not printed as one');
  // Whatever the chain answered is text, never markup; the words come through the page's translator.
  v = V.view(200, { ...lib, receive: { library: '<img src=x onerror=alert(1)>', library_known: false } }, (k, en) => (k === 'rk.r_in' ? 'IN' : en));
  assert.ok(v.html.includes('&lt;img src=x onerror=alert(1)&gt;') && !v.html.includes('<img'));
  assert.match(v.html, /<h3>IN<\/h3>/);
  // The page loads this module at its recorded version and asks only the check's own routes.
  const page = fs.readFileSync(path.join(__dirname, '..', 'public', 'scanner.html'), 'utf8');
  const manifest = JSON.parse(fs.readFileSync(path.join(__dirname, 'asset_versions.json'), 'utf8'));
  assert.ok(page.includes(`/js/risk-bridge.js?v=${manifest['js/risk-bridge.js'].v}"`));
  assert.deepStrictEqual([...new Set([...page.matchAll(/'(\/api\/[a-z/]+)/g)].map((m) => m[1]))].sort(), ['/api/risk/bridge', '/api/risk/chains']);
  assert.ok(!/\$\s?\d/.test(page), 'no dollar figure on a public page');
});
