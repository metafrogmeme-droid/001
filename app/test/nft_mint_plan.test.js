'use strict';
/**
 * Rune of Entry mint plan — the contract under test:
 * - The voucher is a REAL EIP-712 signature over the same domain the deployed
 *   contract checks (name pinned against the Solidity source itself).
 * - The server signs vouchers, never transactions; the voucher key never
 *   appears in any payload (F-15).
 * - Honest when not ready, honest when the chain is unreadable (null, not 0).
 * - The dashboard flow guards the linked address, switches to Base, and sends
 *   a zero-value transaction from the USER's wallet.
 */
process.env.JWT_SECRET = process.env.JWT_SECRET || 'j'.repeat(64);
delete process.env.DATABASE_URL;

const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const { ethers } = require('ethers');

const KEY = '0x' + '11'.repeat(32);
const CONTRACT = '0x' + 'cc'.repeat(20);
const USER = '0x' + 'ab'.repeat(20);
process.env.NFT_CONTRACT_ADDRESS = CONTRACT;
process.env.NFT_VOUCHER_KEY = KEY;

const nft = require('../lib/nft.js');
const read = (...p) => fs.readFileSync(path.join(__dirname, '..', ...p), 'utf8');

// eth_call stub: tokenOf → tokenId, totalMinted → count, tokenURI → data URI.
function rpcStub({ tokenOf = 0, total = 0, uri = null } = {}) {
  const IF = new ethers.Interface([
    'function tokenOf(address) view returns (uint256)',
    'function totalMinted() view returns (uint256)',
    'function tokenURI(uint256) view returns (string)',
  ]);
  return async (url, opts) => ({
    ok: true,
    json: async () => {
      const call = JSON.parse(opts.body).params[0].data;
      let out;
      if (call.startsWith(IF.getFunction('tokenOf').selector)) {
        out = IF.encodeFunctionResult('tokenOf', [tokenOf]);
      } else if (call.startsWith(IF.getFunction('totalMinted').selector)) {
        out = IF.encodeFunctionResult('totalMinted', [total]);
      } else {
        out = IF.encodeFunctionResult('tokenURI', [uri || '']);
      }
      return { jsonrpc: '2.0', result: out };
    },
  });
}

test('the voucher domain matches the deployed contract, source to source', () => {
  const sol = fs.readFileSync(path.join(__dirname, '..', '..', 'contracts', 'rune', 'RuneOfEntry.sol'), 'utf8');
  assert.match(sol, new RegExp(`name = "${nft.DOMAIN_NAME}"`),
    'a domain-name drift between server and contract silently bricks every voucher');
  assert.match(sol, /MintVoucher\(address to\)/);
  assert.equal(nft.CHAIN_ID, 8453, 'vouchers pin to Base, where the contract deploys');
});

test('ready plan: real voucher, decodable calldata, and the key never rides along', async () => {
  nft.setNftFetcher(rpcStub({ tokenOf: 0 }));
  try {
    const plan = await nft.buildMintPlan(USER);
    assert.equal(plan.ready, true);
    assert.equal(plan.chain_id, 8453);
    assert.equal(plan.contract, CONTRACT);
    assert.equal(plan.minted_token_id, 0);
    // Decode the calldata back out and verify the EIP-712 signature inside it
    // recovers to the voucher signer — the same check the contract performs.
    const IF = new ethers.Interface(['function mint(bytes sig)']);
    const [sig] = IF.decodeFunctionData('mint', plan.calldata);
    const recovered = ethers.verifyTypedData(
      { name: nft.DOMAIN_NAME, chainId: nft.CHAIN_ID, verifyingContract: CONTRACT },
      nft.VOUCHER_TYPES, { to: ethers.getAddress(USER) }, sig);
    assert.equal(recovered, new ethers.Wallet(KEY).address);
    // F-15: the private key must never appear anywhere in the answer.
    assert.ok(!JSON.stringify(plan).toLowerCase().includes(KEY.slice(2).toLowerCase()),
      'the voucher key must never ride in a payload');
    assert.match(plan.non_custodial_note, /cannot send transactions or move funds/);
    assert.match(plan.soulbound, /not an investment/);
  } finally { nft.setNftFetcher(null); }
});

test('already minted: the plan says which token, and carries the on-chain image', async () => {
  const svg = Buffer.from('<svg xmlns="x"></svg>').toString('base64');
  const json = Buffer.from(JSON.stringify({ name: 'Rune of Entry #3',
    image: 'data:image/svg+xml;base64,' + svg })).toString('base64');
  nft.setNftFetcher(rpcStub({ tokenOf: 3, uri: 'data:application/json;base64,' + json }));
  try {
    const plan = await nft.buildMintPlan(USER);
    assert.equal(plan.minted_token_id, 3);
    assert.match(plan.minted_image, /^data:image\/svg\+xml;base64,/);
  } finally { nft.setNftFetcher(null); }
});

test('unreadable chain: minted state is null (unknown), never zero — and the plan still ships', async () => {
  nft.setNftFetcher(async () => { throw new Error('ECONNREFUSED'); });
  try {
    const plan = await nft.buildMintPlan(USER);
    assert.equal(plan.ready, true, 'the contract enforces one-per-wallet itself');
    assert.equal(plan.minted_token_id, null);
    assert.ok(!('minted_image' in plan));
  } finally { nft.setNftFetcher(null); }
});

test('not ready is honest: missing wallet and missing config are named, key state is not leaked', async () => {
  const noWallet = await nft.buildMintPlan(null);
  assert.equal(noWallet.ready, false);
  assert.match(noWallet.not_ready_reasons.join(' '), /link a wallet/);

  const savedC = process.env.NFT_CONTRACT_ADDRESS;
  const savedK = process.env.NFT_VOUCHER_KEY;
  delete process.env.NFT_CONTRACT_ADDRESS;
  delete process.env.NFT_VOUCHER_KEY;
  try {
    const bare = await nft.buildMintPlan(USER);
    assert.equal(bare.ready, false);
    assert.match(bare.not_ready_reasons.join(' '), /NFT_CONTRACT_ADDRESS/);
    assert.match(bare.not_ready_reasons.join(' '), /NFT_VOUCHER_KEY is not set/);
  } finally {
    process.env.NFT_CONTRACT_ADDRESS = savedC;
    process.env.NFT_VOUCHER_KEY = savedK;
  }
});

test('stats: a count is a count — and an unreadable count is unknown, not zero', async () => {
  nft.setNftFetcher(rpcStub({ total: 42 }));
  try {
    const s = await nft.readStats();
    assert.deepEqual(s, { deployed: true, chain_id: 8453, contract: CONTRACT, minted_count: 42 });
  } finally { nft.setNftFetcher(null); }
  nft.setNftFetcher(async () => { throw new Error('down'); });
  try {
    const s = await nft.readStats();
    assert.equal(s.minted_count, null);
  } finally { nft.setNftFetcher(null); }
  const saved = process.env.NFT_CONTRACT_ADDRESS;
  delete process.env.NFT_CONTRACT_ADDRESS;
  try {
    assert.deepEqual(await nft.readStats(), { deployed: false });
  } finally { process.env.NFT_CONTRACT_ADDRESS = saved; }
});

test('route wiring: mint-plan is authed, stats is public, unreadable stats are never cached', () => {
  const src = read('routes', 'nft.js');
  assert.match(src, /router\.get\('\/mint-plan', authMiddleware/,
    'vouchers are per-account — no anonymous issuance');
  assert.doesNotMatch(src.split("'/stats'")[1].split('router.get')[0], /authMiddleware/,
    'the mint count is public data');
  assert.match(src, /if \(!body\.deployed \|\| body\.minted_count != null\) statsCache =/);
});

test('the dashboard mint flow guards the linked address and sends a zero-value user transaction', () => {
  const js = read('public', 'js', 'dashboard.js');
  // indexOf, not split: the handler mentions #runeMint twice, and split()[1]
  // would end at the SECOND occurrence instead of spanning the handler.
  const start = js.indexOf("closest('#runeMint')");
  assert.ok(start > 0, 'the mint handler exists');
  const flow = js.slice(start, js.indexOf('#walletLink', start));
  assert.match(flow, /data-linked/, 'the voucher binds to the linked address — the UI must check');
  assert.match(flow, /wallet_switchEthereumChain/);
  assert.match(flow, /eth_sendTransaction/);
  assert.doesNotMatch(flow, /value\s*:/,
    'no value field — the mint is free and the contract is not even payable');
  const i18n = require('../public/js/i18n.js');
  for (const k of ['dd.r_minted', 'dd.r_pitch', 'dd.r_btn', 'dd.r_wrong_acct', 'dd.r_chain',
    'dd.r_confirm', 'dd.r_sent', 'dd.r_cancelled', 'dd.t_rune_sent']) {
    for (const l of i18n.LANGS) {
      assert.ok(String(i18n.STRINGS[k][l.code] || '').trim().length, `${k} missing ${l.code}`);
    }
  }
});
