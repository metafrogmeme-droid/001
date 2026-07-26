'use strict';
/**
 * Rune of Entry — server-side mint planning for the soulbound signup NFT
 * (contracts/rune/RuneOfEntry.sol, deployed by the operator on Base).
 *
 * Non-custodial line, precisely drawn: the server NEVER signs or sends a
 * transaction and never holds a funds key. What it signs here is an EIP-712
 * *voucher* — an authorization message saying "this linked wallet may mint
 * once". The user's own wallet builds, signs and pays for the transaction.
 * Voucher-key blast radius: worst-case compromise is unauthorized free
 * mints; it can never move funds or touch an existing token.
 *
 * F-15: NFT_VOUCHER_KEY must never appear in any payload, log or error.
 */

const { ethers } = require('ethers');

const CHAIN_ID = 8453; // Base — where the contract deploys; the EIP-712
                       // domain pins vouchers to exactly that deployment.
const BASE_RPCS = ['https://mainnet.base.org', 'https://base-rpc.publicnode.com'];

const IFACE = new ethers.Interface([
  'function mint(bytes sig) returns (uint256)',
  'function tokenOf(address) view returns (uint256)',
  'function totalMinted() view returns (uint256)',
  'function tokenURI(uint256) view returns (string)',
]);

const DOMAIN_NAME = 'RUNECLAW Rune of Entry';
const VOUCHER_TYPES = { MintVoucher: [{ name: 'to', type: 'address' }] };

function contractAddress() {
  const a = String(process.env.NFT_CONTRACT_ADDRESS || '').trim();
  return /^0x[0-9a-fA-F]{40}$/.test(a) ? a : null;
}

function voucherWallet() {
  const k = String(process.env.NFT_VOUCHER_KEY || '').trim();
  if (!/^0x[0-9a-fA-F]{64}$/.test(k)) return null;
  try { return new ethers.Wallet(k); } catch (e) { return null; }
}

// Injectable fetch for tests (the lib/tickers pattern).
let fetchImpl = null;
function setNftFetcher(fn) { fetchImpl = fn || null; }

/** Bounded eth_call over the Base RPC set. Returns hex data or null —
 *  an unreadable chain is an unknown, never a zero. */
async function ethCall(to, data) {
  const f = fetchImpl || global.fetch;
  for (const url of BASE_RPCS) {
    try {
      const ctrl = new AbortController();
      const timer = setTimeout(() => ctrl.abort(), 3500);
      const r = await f(url, {
        method: 'POST', headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ jsonrpc: '2.0', id: 1, method: 'eth_call',
          params: [{ to, data }, 'latest'] }),
        signal: ctrl.signal,
      });
      clearTimeout(timer);
      if (!r.ok) continue;
      const j = await r.json();
      if (j && typeof j.result === 'string') return j.result;
    } catch (e) { /* rotate */ }
  }
  return null;
}

/** tokenId already minted by this wallet: 0 = none, null = could not read. */
async function mintedTokenOf(owner) {
  const contract = contractAddress();
  if (!contract) return null;
  const out = await ethCall(contract, IFACE.encodeFunctionData('tokenOf', [owner]));
  if (out == null) return null;
  try { return Number(IFACE.decodeFunctionResult('tokenOf', out)[0]); }
  catch (e) { return null; }
}

/** The minted token's fully on-chain image (data URI) — or null, honestly. */
async function tokenImage(id) {
  const contract = contractAddress();
  if (!contract || !id) return null;
  const out = await ethCall(contract, IFACE.encodeFunctionData('tokenURI', [id]));
  if (out == null) return null;
  try {
    const uri = IFACE.decodeFunctionResult('tokenURI', out)[0];
    const json = JSON.parse(Buffer.from(uri.split(',')[1], 'base64').toString('utf8'));
    return typeof json.image === 'string' && json.image.startsWith('data:image/svg+xml;base64,')
      ? json.image : null;
  } catch (e) { return null; }
}

/**
 * The mint plan for a linked wallet: an EIP-712 voucher + the exact calldata
 * the user's wallet sends. Honest when not ready — and the voucher key never
 * rides in the answer.
 */
async function buildMintPlan(walletAddress) {
  const contract = contractAddress();
  const signer = voucherWallet();
  const linked = /^0x[0-9a-fA-F]{40}$/.test(String(walletAddress || ''));
  const notReady = [
    ...(linked ? [] : ['link a wallet first — the voucher binds to your linked address']),
    ...(contract ? [] : ['NFT_CONTRACT_ADDRESS is not set (contract not deployed yet)']),
    ...(signer ? [] : ['NFT_VOUCHER_KEY is not set on the server']),
  ];
  if (notReady.length) return { ready: false, not_ready_reasons: notReady };

  const to = ethers.getAddress(walletAddress);
  const sig = await signer.signTypedData(
    { name: DOMAIN_NAME, chainId: CHAIN_ID, verifyingContract: contract },
    VOUCHER_TYPES, { to });
  // Already minted? An unreadable chain answers null — the plan still ships,
  // because the contract itself enforces one-per-wallet either way.
  const minted = await mintedTokenOf(to);
  const image = minted ? await tokenImage(minted) : null;
  return {
    ready: true,
    chain_id: CHAIN_ID,
    contract,
    calldata: IFACE.encodeFunctionData('mint', [sig]),
    minted_token_id: minted, // 0 = none, null = chain unreadable right now
    ...(image ? { minted_image: image } : {}),
    free: 'The mint function is not payable — the only cost is Base network gas.',
    soulbound: 'ERC-5192: the token can never be transferred or approved. '
      + 'A badge, not an investment.',
    non_custodial_note: 'Your wallet builds, signs and sends the transaction. '
      + 'The server signed only an authorization voucher — it cannot send '
      + 'transactions or move funds.',
  };
}

/** Public collection stats: a count is a count — no values, no owners. */
async function readStats() {
  const contract = contractAddress();
  if (!contract) return { deployed: false };
  const out = await ethCall(contract, IFACE.encodeFunctionData('totalMinted', []));
  let count = null; // unreadable = unknown, never zero
  if (out != null) {
    try { count = Number(IFACE.decodeFunctionResult('totalMinted', out)[0]); }
    catch (e) { count = null; }
  }
  return { deployed: true, chain_id: CHAIN_ID, contract, minted_count: count };
}

module.exports = {
  CHAIN_ID, DOMAIN_NAME, VOUCHER_TYPES, BASE_RPCS,
  contractAddress, buildMintPlan, readStats, mintedTokenOf, tokenImage,
  setNftFetcher,
};
