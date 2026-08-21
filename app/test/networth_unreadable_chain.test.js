'use strict';
// A dead chain contributed $0 to a total presented as the whole wallet.
//
// lib/wallet.js readChain() never throws. A chain whose RPC is down returns
// `{assets: [], total_usd: 0, error: 'rpc unreadable'}`, and readWallet() sums
// those zeros. networth.js then kept `total_usd` and DROPPED `p.chains`,
// destroying the only evidence a chain had failed — so nothing downstream
// could tell a $0 chain from an unread one.
//
// Hold 2 ETH on Arbitrum and 500 USDC on Base, have Arbitrum's RPC 429, and
// the dashboard printed "👛 Wallet $500.00" and "Real net worth $500.00"
// beside the note "Real total = connected exchange + on-chain wallet".
// Meanwhile the /web3 panel ON THE SAME PAGE said "arbitrum unreadable right
// now (RPC)". Two panels, one dataset, opposite claims.
//
// The sibling reading the identical payload already got it right —
// lib/holdings.js does `total_usd: c.error ? null : ...` — and
// test/networth_connected_honesty.test.js records the production case: "Three
// chains all reported 'rpc unreadable'". The fix reached holdings.js and the
// /web3 panel and never reached the net-worth total.
//
// Exercised, not grepped: `wallet` is stubbed through the require cache and
// the real buildNetWorth is run, so this asserts the OUTPUT rather than the
// presence of a line of source.

const test = require('node:test');
const assert = require('node:assert');
const path = require('node:path');

const WALLET = require.resolve('../lib/wallet');
const GATEWAY = require.resolve('../lib/gateway');
const NETWORTH = require.resolve('../lib/networth');

function withStubs({ chains, total_usd }, fn) {
  const saved = { w: require.cache[WALLET], g: require.cache[GATEWAY], n: require.cache[NETWORTH] };
  require.cache[WALLET] = {
    id: WALLET, filename: WALLET, loaded: true, exports: {
      walletAddressOf: async () => '0xabc',
      getWalletPortfolio: async () => ({
        address: '0xabc',
        chains,
        assets: chains.flatMap((c) => c.assets || []),
        total_usd,
        unpriced: 0,
      }),
    },
  };
  // The gateway is not under test; report it unconfigured so the wallet is
  // the only source and the total is unambiguous.
  require.cache[GATEWAY] = {
    id: GATEWAY, filename: GATEWAY, loaded: true, exports: {
      isConfigured: () => false,
      getGateway: async () => { throw new Error('not configured in this test'); },
    },
  };
  // opensea is required lazily inside the collectibles block; stub it so the
  // test does not reach the network.
  const OPENSEA = require.resolve('../lib/opensea');
  const savedOpensea = require.cache[OPENSEA];
  require.cache[OPENSEA] = {
    id: OPENSEA, filename: OPENSEA, loaded: true, exports: {
      getWalletNfts: async () => ({ available: false, reason: 'stubbed' }),
    },
  };
  saved.o = savedOpensea;
  saved.oKey = OPENSEA;
  delete require.cache[NETWORTH];
  try {
    return fn(require('../lib/networth'));
  } finally {
    delete require.cache[NETWORTH];
    if (saved.w) require.cache[WALLET] = saved.w; else delete require.cache[WALLET];
    if (saved.g) require.cache[GATEWAY] = saved.g; else delete require.cache[GATEWAY];
    if (saved.n) require.cache[NETWORTH] = saved.n; else delete require.cache[NETWORTH];
    if (saved.oKey) {
      if (saved.o) require.cache[saved.oKey] = saved.o;
      else delete require.cache[saved.oKey];
    }
  }
}

const OK_CHAIN = { chain: 'base', label: 'Base', assets: [{ usd: 500 }], total_usd: 500, unpriced: 0 };
const DEAD_CHAIN = { chain: 'arbitrum', label: 'Arbitrum', assets: [], total_usd: 0, unpriced: 0, error: 'rpc unreadable' };

test('a wallet with an unreadable chain does not report a confident total', async () => {
  await withStubs({ chains: [OK_CHAIN, DEAD_CHAIN], total_usd: 500 }, async (nw) => {
    const out = await nw.buildNetWorth({}, 'u1');
    assert.strictEqual(out.sections.wallet.total_usd, null,
      'a wallet missing a chain is not a smaller wallet, it is an unknown one');
    assert.strictEqual(out.sections.wallet.partial, true);
  });
});

test('it names WHICH chain could not be read', async () => {
  // "unreadable" alone leaves an operator unable to act.
  await withStubs({ chains: [OK_CHAIN, DEAD_CHAIN], total_usd: 500 }, async (nw) => {
    const out = await nw.buildNetWorth({}, 'u1');
    assert.deepStrictEqual(out.sections.wallet.unreadable_chains, ['Arbitrum']);
  });
});

test('a fully readable wallet still reports its total', async () => {
  // The control. Over-guarding would blank a perfectly good reading, which is
  // the same defect pointing the other way.
  await withStubs({ chains: [OK_CHAIN], total_usd: 500 }, async (nw) => {
    const out = await nw.buildNetWorth({}, 'u1');
    assert.strictEqual(out.sections.wallet.total_usd, 500);
    assert.strictEqual(out.sections.wallet.partial, false);
    assert.strictEqual(out.total_real_usd, 500);
  });
});

test('a genuinely empty wallet is zero, not unreadable', async () => {
  // An empty chain with no error is a real, measured $0.00.
  await withStubs({ chains: [{ chain: 'base', label: 'Base', assets: [], total_usd: 0, unpriced: 0 }], total_usd: 0 },
    async (nw) => {
      const out = await nw.buildNetWorth({}, 'u1');
      assert.strictEqual(out.sections.wallet.total_usd, 0);
      assert.strictEqual(out.sections.wallet.partial, false);
    });
});

test('the null total does not sneak into total_real_usd as a zero', async () => {
  // THE TRAP THIS FIX WOULD OTHERWISE HAVE WALKED INTO. The global
  // `isFinite` COERCES, so `isFinite(null)` is TRUE — the very null introduced
  // above would have passed the guard meant to catch it and added 0 to the
  // total. `Number.isFinite(null)` is false, which is the real question.
  await withStubs({ chains: [DEAD_CHAIN], total_usd: 0 }, async (nw) => {
    const out = await nw.buildNetWorth({}, 'u1');
    assert.strictEqual(out.total_real_usd, null,
      'no source was readable, so there is no real total');
    assert.strictEqual(out.sources_counted, 0);
    assert.ok(out.sources_unknown >= 1, 'the unread source must be counted somewhere');
  });
});

test('the global isFinite would have accepted null — proving the guard matters', () => {
  // Stated as an executable fact rather than a comment, because this is the
  // exact coercion that makes the bug invisible on inspection.
  assert.strictEqual(isFinite(null), true);
  assert.strictEqual(Number.isFinite(null), false);
  assert.strictEqual(isFinite(''), true);
  assert.strictEqual(Number.isFinite(''), false);
});

test('networth.js uses Number.isFinite, not the coercing global', () => {
  // Wiring, so a future edit cannot reintroduce the bare form. Comments are
  // stripped first: a comment naming `isFinite(null)` is indistinguishable
  // from code calling it, which has produced six false failures in this repo.
  const fs = require('node:fs');
  const raw = fs.readFileSync(path.join(__dirname, '..', 'lib', 'networth.js'), 'utf8');
  const code = raw
    .replace(/\/\*[\s\S]*?\*\//g, '')
    .split('\n').map((l) => l.replace(/\/\/.*$/, '')).join('\n');
  const bare = code.match(/(^|[^.\w])isFinite\s*\(/g) || [];
  assert.deepStrictEqual(bare, [], `bare isFinite( in networth.js: ${bare}`);
});
