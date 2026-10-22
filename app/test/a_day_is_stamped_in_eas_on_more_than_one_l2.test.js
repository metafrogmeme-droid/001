'use strict';
/**
 * A day's root stamped in EAS on Arbitrum One, Scroll or Linea (22 October
 * 2026), beside the Base stamp (app/test/a_day_is_stamped_on_mainnets.test.js).
 *
 *   the registry   each chain as read that day: its chain id, its EAS
 *                  contract, two keyless readers, and Base's independence
 *                  group, since each settles to Ethereum
 *   the plan       timestamp(root) to THAT chain's EAS, never another's
 *   the reading    recorded only when both of that chain's readers read the
 *                  stamp; a call to another chain's EAS is not one
 *   the quorum     more places to stamp, not more witnesses
 *   the page       stamps on the chain picked, and refuses an entry that is
 *                  not EAS
 *   the verifier   tools/rcverify.js keeps its own table, which must still
 *                  agree with the registry on every EAS chain
 */
process.env.JWT_SECRET = process.env.JWT_SECRET || 'j'.repeat(64);
for (const k of ['ARBITRUM_RPC_URLS', 'SCROLL_RPC_URLS', 'LINEA_RPC_URLS']) delete process.env[k];

const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

const anchor = require('../lib/root_anchor');
const { quorumOf } = require('../lib/anchor_quorum');
const { stamp, shortName, chainsLeft } = require('../public/js/roots-stamp.js');
const rcverify = require('../../tools/rcverify');
const { codeOnly } = require('./helpers/code_only');

const DAY = '2026-10-20';
const ROOT = 'ab'.repeat(32);
const TX = '0x' + '7'.repeat(64);
const BLOCK = '0x' + 'b'.repeat(64);
const TIME = 1761200000;

// As read on 22 October 2026 (the eas-contracts README, then each contract live).
const READ = {
  'arbitrum-eas': { chain_id: 42161, name: 'Arbitrum One', eas: '0xbD75f629A22Dc1ceD33dDA0b68c546A1c035c458' },
  'scroll-eas': { chain_id: 534352, name: 'Scroll', eas: '0xC47300428b6AD2c7D03BB76D05A176058b47E6B0' },
  'linea-eas': { chain_id: 59144, name: 'Linea', eas: '0xaEF4103A04090071165F78D45D83A0C0782c2B2a' },
};
const KEYS = Object.keys(READ);

/** One chain as its RPCs answer, recording every URL asked. */
function chainWorld(key, { to = READ[key].eas, status = '0x1', stamped = TIME, down = [], seen = [] } = {}) {
  const chain = anchor.chainInfo(key);
  return async (url, opts) => {
    seen.push(url);
    if (!chain.rpcs.includes(url)) throw new Error(`asked a reader of another chain: ${url}`);
    if (down.includes(url)) throw new TypeError('fetch failed');
    const { method, params } = JSON.parse(opts.body);
    let result = null;
    if (method === 'eth_getTransactionByHash') {
      result = { hash: TX, to, input: '0x4d003070' + ROOT, blockHash: BLOCK, blockNumber: '0x10' };
    } else if (method === 'eth_getTransactionReceipt') {
      result = { status };
    } else if (method === 'eth_getBlockByHash') {
      result = { hash: BLOCK, timestamp: '0x' + TIME.toString(16) };
    } else if (method === 'eth_call') {
      assert.strictEqual(params[0].to, READ[key].eas, 'EAS is asked at this chain\'s own address');
      assert.ok(params[0].data.startsWith(anchor.EAS_GET_TIMESTAMP));
      result = '0x' + BigInt(stamped).toString(16).padStart(64, '0');
    } else {
      throw new Error(`not modelled: ${method}`);
    }
    return { ok: true, status: 200, json: async () => ({ jsonrpc: '2.0', id: 1, result }) };
  };
}

test('each L2 is in the registry as read on 22 October: its chain, its EAS, two keyless readers, Base\'s group', () => {
  const pub = Object.fromEntries(anchor.publicChains().map((c) => [c.key, c]));
  for (const key of KEYS) {
    const c = anchor.chainInfo(key);
    assert.deepStrictEqual([c.chain_id, c.eas, c.method, c.testnet], [READ[key].chain_id, READ[key].eas, 'eas_timestamp', false], key);
    assert.strictEqual(c.name, `${READ[key].name} (EAS timestamp)`);
    assert.deepStrictEqual([pub[key].independence_group, pub[key].counts_toward_quorum], ['ethereum-l2', true]);
    assert.strictEqual(pub[key].rpcs.length, 2);
    assert.ok(pub[key].rpcs.every((u) => /^https:\/\//.test(u)), key);
    assert.match(pub[key].explorer_tx, /^https:\/\/[a-z.]+\/tx\/$/);
  }
  // A configured reader replaces the defaults for the server's reads, and is never served.
  const env = { ARBITRUM_RPC_URLS: 'https://arb.example/v1/SECRETKEY' };
  assert.deepStrictEqual(anchor.chainInfo('arbitrum-eas', env).rpcs, ['https://arb.example/v1/SECRETKEY']);
  assert.ok(!JSON.stringify(anchor.publicChains(env)).includes('SECRETKEY'));
  assert.deepStrictEqual(anchor.chainInfo('scroll-eas', env).rpcs, pub['scroll-eas'].rpcs, 'one chain\'s setting is not another\'s');
});

test('the plan is timestamp(root) to that chain\'s own EAS', () => {
  for (const key of KEYS) {
    const p = anchor.buildAnchorPlan(DAY, ROOT, key);
    assert.deepStrictEqual([p.to, p.data, p.chain_id, p.method, p.dry_run], [READ[key].eas, '0x4d003070' + ROOT, READ[key].chain_id, 'eas_timestamp', true]);
    assert.match(p.instructions[0], new RegExp(`the Ethereum Attestation Service on ${READ[key].name}\\)`));
    assert.match(p.instructions[1], new RegExp(`--rpc-url ${anchor.chainInfo(key).public_rpcs[0].replace(/[.]/g, '\\.')} `));
    assert.match(p.instructions[2], new RegExp(`chain: "${key}"`));
  }
});

test('a stamp is recorded only when both of the chain\'s readers read it, at its own EAS', async () => {
  for (const key of KEYS) {
    const seen = [];
    const v = await anchor.verifyAnchor(TX, DAY, ROOT, chainWorld(key, { seen }), key);
    assert.strictEqual(v.status, 'verified', `${key}: ${JSON.stringify(v)}`);
    assert.deepStrictEqual([...new Set(seen)].sort(), anchor.chainInfo(key).rpcs.slice().sort(), 'both readers asked, no other');
  }
  const key = 'linea-eas';
  // The same call sent to Base's EAS (or Arbitrum's) is not a stamp on Linea.
  for (const to of [anchor.EAS_BASE, READ['arbitrum-eas'].eas]) {
    const v = await anchor.verifyAnchor(TX, DAY, ROOT, chainWorld(key, { to }), key);
    assert.strictEqual(v.status, 'mismatch', to);
  }
  assert.strictEqual((await anchor.verifyAnchor(TX, DAY, ROOT, chainWorld(key, { status: '0x0' }), key)).status, 'mismatch', 'reverted');
  assert.strictEqual((await anchor.verifyAnchor(TX, DAY, ROOT, chainWorld(key, { stamped: TIME - 60 }), key)).status, 'mismatch',
    'an earlier stamp of the root is someone else\'s');
  const one = await anchor.verifyAnchor(TX, DAY, ROOT, chainWorld(key, { down: [anchor.chainInfo(key).rpcs[1]] }), key);
  assert.strictEqual(one.status, 'unknown', 'one reader is not two');
  assert.match(one.reason, /linea-rpc\.publicnode\.com could not be read/, 'a keyless reader is named by its host');
});

test('EAS is asked whether a root is stamped by the root alone, at the chain\'s own address', async () => {
  for (const key of KEYS) {
    assert.strictEqual((await anchor.easStampOf(ROOT, chainWorld(key, { stamped: 0 }), key)).status, 'unstamped');
    assert.deepStrictEqual(await anchor.easStampOf(ROOT, chainWorld(key), key),
      { status: 'stamped', stamped_at: new Date(TIME * 1000).toISOString() });
  }
  const rpcs = anchor.chainInfo('scroll-eas').rpcs;
  const down = await anchor.easStampOf(ROOT, chainWorld('scroll-eas', { down: rpcs }), 'scroll-eas');
  assert.strictEqual(down.status, 'unknown', 'unreadable is not "unstamped"');
});

test('more EAS chains are more places to stamp, not more witnesses', () => {
  const W = (key, status = 'verified') => {
    const c = anchor.publicChains().find((x) => x.key === key);
    return { key, group: c.independence_group, status, counts: c.counts_toward_quorum };
  };
  let q = quorumOf([W('base'), W('arbitrum-eas'), W('scroll-eas'), W('linea-eas')]);
  assert.deepStrictEqual([q.status, q.groups], ['short', ['ethereum-l2']]);
  q = quorumOf([W('linea-eas'), W('dogecoin')]);
  assert.deepStrictEqual([q.status, q.groups], ['multi_anchored', ['dogecoin', 'ethereum-l2']]);
});

test('the roots page stamps on the chain picked, and refuses an entry that is not EAS', async () => {
  const chain = anchor.publicChains().find((c) => c.key === 'linea-eas');
  const sent = [];
  const asked = [];
  const eth = { request: async ({ method, params }) => { sent.push({ method, params }); return method === 'eth_sendTransaction' ? TX : null; } };
  const wallet = { account: async () => '0x' + 'a'.repeat(40), switchTo: async (e, c) => { sent.push({ method: 'switch', params: [c.chain_id] }); } };
  const posts = [];
  const out = await stamp(DAY, { eth, wallet, chain, retryMs: 0,
    getJSON: async (u) => { asked.push(u); return { status: 200, body: anchor.buildAnchorPlan(DAY, ROOT, 'linea-eas') }; },
    postJSON: async (u, b) => { posts.push(b); return { status: 200, body: { ok: true } }; } });
  assert.deepStrictEqual(out, { state: 'recorded', tx: TX });
  assert.deepStrictEqual(asked, [`/api/roots/anchor-plan/${DAY}?chain=linea-eas`]);
  assert.deepStrictEqual(sent.find((x) => x.method === 'switch').params, [59144]);
  assert.deepStrictEqual(sent.find((x) => x.method === 'eth_sendTransaction').params,
    [{ from: '0x' + 'a'.repeat(40), to: READ['linea-eas'].eas, data: '0x4d003070' + ROOT, value: '0x0' }]);
  assert.deepStrictEqual(posts, [{ day: DAY, tx_hash: TX, chain: 'linea-eas' }]);
  // Not yet confirmed: the reason names the chain the stamp was sent on.
  const slow = await stamp(DAY, { eth, wallet, chain, retryMs: 0, retries: 0,
    getJSON: async () => ({ status: 200, body: anchor.buildAnchorPlan(DAY, ROOT, 'linea-eas') }),
    postJSON: async () => ({ status: 503, body: {} }) });
  assert.deepStrictEqual(slow, { state: 'stamped_unrecorded', tx: TX, reason: 'Linea had not confirmed it yet' });
  // A plan for Base's EAS, handed to the Linea entry, sends nothing.
  sent.length = 0;
  await assert.rejects(stamp(DAY, { eth, wallet, chain, getJSON: async () => ({ status: 200, body: anchor.buildAnchorPlan(DAY, ROOT, 'base-eas') }),
    postJSON: async () => assert.fail('nothing to record') }), /another contract than EAS/);
  assert.ok(!sent.some((x) => x.method === 'eth_sendTransaction'));
  // A calldata chain is not an EAS chain, whatever it is called.
  const base = anchor.publicChains().find((c) => c.key === 'base');
  await assert.rejects(stamp(DAY, { eth, wallet, chain: { ...base, eas: READ['linea-eas'].eas }, getJSON: async () => assert.fail(), postJSON: async () => assert.fail() }),
    /did not describe the EAS contract/);
  assert.strictEqual(shortName(chain), 'Linea');

  // The page offers the registry's EAS chains a day is not stamped on yet, named nowhere by hand.
  const all = anchor.publicChains();
  assert.deepStrictEqual(chainsLeft(all, []).map((c) => c.key), ['base-eas', 'arbitrum-eas', 'scroll-eas', 'linea-eas']);
  assert.deepStrictEqual(chainsLeft(all, ['base', 'base-eas', 'scroll-eas']).map((c) => c.key), ['arbitrum-eas', 'linea-eas'],
    'a calldata anchor on Base is not a stamp in EAS');
  assert.deepStrictEqual(chainsLeft(all, all.map((c) => c.key)), []);
  assert.deepStrictEqual([chainsLeft(null, null), chainsLeft([null], [])], [[], []]);
  const page = codeOnly(fs.readFileSync(path.join(__dirname, '..', 'public', 'roots.html'), 'utf8'));
  assert.match(page, /RCRootsStamp\.chainsLeft\(/);
  assert.ok(!/base-eas|arbitrum-eas|scroll-eas|linea-eas/.test(page), 'no EAS chain is hard-coded in the page');
});

test('the verifier\'s own table still agrees with the registry on every EAS chain', () => {
  const eas = anchor.publicChains().filter((c) => c.method === 'eas_timestamp');
  assert.deepStrictEqual(eas.map((c) => c.key).sort(), ['arbitrum-eas', 'base-eas', 'linea-eas', 'scroll-eas']);
  for (const c of eas) {
    const v = rcverify.CHAINS[c.key];
    assert.ok(v, `the verifier knows ${c.key}`);
    assert.deepStrictEqual([v.method, v.eas.toLowerCase(), v.group], ['eas_timestamp', c.eas.toLowerCase(), c.independence_group], c.key);
    assert.ok(c.rpcs.includes(v.rpc), `${c.key}: the verifier asks a reader the registry also names`);
  }
});
