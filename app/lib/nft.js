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
// The calldata the USER'S WALLET sends is encoded here, not by ethers: the
// production host resolves `ethers` to a stub whose encodeFunctionData
// returns '0x', which mints nothing and still costs gas. See app/lib/abi_call.js.
// The EIP-712 voucher signature genuinely needs ethers and stays on it —
// a stub that cannot sign fails loudly (no voucher, ready:false), which is
// the difference between the two.
const { encodeCall } = require('./abi_call');

/**
 * WHICH CHAIN, AND WHY IT IS NOT A BARE CONSTANT ANY MORE.
 *
 * The contract derives its EIP-712 domain from `block.chainid`, so the same
 * bytecode is valid on any EVM chain. The address comes from an env var. The
 * chain id used to be a hardcoded 8453 — so deploying that bytecode anywhere
 * other than Base made the two disagree SILENTLY: every voucher would be
 * signed for a chain the contract is not on, every mint would revert, and the
 * revert reads as "bad voucher", which looks like the USER did something
 * wrong. The reads would meanwhile go to Base RPCs and find nothing at that
 * address, rendering as "unreadable" rather than "misconfigured".
 *
 * So the chain is configuration now, and it travels WITH its RPCs — the pair
 * cannot drift because there is only one place to set it.
 *
 * A chain this build does not know is a REFUSAL, not a fallback to Base.
 * Quietly defaulting on an unrecognised NFT_CHAIN_ID is the same defect one
 * level up: an operator who set it deliberately would get Base anyway, and the
 * mint would fail the way described above. Unknown fails closed, with the
 * reason said out loud.
 */
const DEFAULT_CHAIN_ID = 8453; // Base
const CHAINS = {
  8453: {
    name: 'Base',
    rpcs: ['https://mainnet.base.org', 'https://base-rpc.publicnode.com'],
    explorer: 'https://basescan.org',
  },
  // Base Sepolia exists here for one reason: this contract has no owner, no
  // pause and no upgrade path, so the mainnet deploy is permanent and worth
  // rehearsing end-to-end first.
  84532: {
    name: 'Base Sepolia',
    rpcs: ['https://sepolia.base.org'],
    explorer: 'https://sepolia.basescan.org',
  },
};

/** The configured chain id, or NaN when NFT_CHAIN_ID is set but unusable. */
function chainId() {
  const raw = String(process.env.NFT_CHAIN_ID || '').trim();
  if (!raw) return DEFAULT_CHAIN_ID;
  return /^[0-9]{1,10}$/.test(raw) ? Number(raw) : NaN;
}

/** Config for the configured chain, or null if this build cannot reach it. */
function chainConfig() {
  return CHAINS[chainId()] || null;
}

// Kept for callers that predate the env var; it is the default chain's set.
const CHAIN_ID = DEFAULT_CHAIN_ID;
const BASE_RPCS = CHAINS[DEFAULT_CHAIN_ID].rpcs;

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
  const cfg = chainConfig();
  if (!cfg) return null; // unknown chain: unreadable, never a guess at Base
  for (const url of cfg.rpcs) {
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
  const cfg = chainConfig();
  const linked = /^0x[0-9a-fA-F]{40}$/.test(String(walletAddress || ''));
  const notReady = [
    ...(linked ? [] : ['link a wallet first — the voucher binds to your linked address']),
    ...(contract ? [] : ['NFT_CONTRACT_ADDRESS is not set (contract not deployed yet)']),
    ...(signer ? [] : ['NFT_VOUCHER_KEY is not set on the server']),
    // Refuse rather than sign a voucher for a chain we cannot name. A voucher
    // is only valid on the chain it was signed for; getting this wrong burns
    // the user's gas on a revert that blames them.
    ...(cfg ? [] : [`NFT_CHAIN_ID=${process.env.NFT_CHAIN_ID} is not a chain `
      + `this build can reach (known: ${Object.keys(CHAINS).join(', ')})`]),
  ];
  if (notReady.length) return { ready: false, not_ready_reasons: notReady };

  const to = ethers.getAddress(walletAddress);
  const sig = await signer.signTypedData(
    { name: DOMAIN_NAME, chainId: chainId(), verifyingContract: contract },
    VOUCHER_TYPES, { to });
  // Already minted? An unreadable chain answers null — the plan still ships,
  // because the contract itself enforces one-per-wallet either way.
  const minted = await mintedTokenOf(to);
  const image = minted ? await tokenImage(minted) : null;
  return {
    ready: true,
    chain_id: chainId(),
    chain_name: cfg.name,
    // The wallet needs these when it does not already know the chain
    // (EIP-1193 error 4902 -> wallet_addEthereumChain). They ship WITH the
    // voucher so the browser cannot add a network that disagrees with the
    // chain the voucher was signed for — the dashboard used to hardcode
    // "Base" and the mainnet RPC beside a chain id it read from this payload.
    rpc_urls: cfg.rpcs.slice(),
    explorer: cfg.explorer,
    contract,
    calldata: encodeCall('mint(bytes)', [sig]),
    minted_token_id: minted, // 0 = none, null = chain unreadable right now
    ...(image ? { minted_image: image } : {}),
    free: 'The mint function is not payable — the only cost is '
      + `${cfg.name} network gas.`,
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
  const cfg = chainConfig();
  // An address IS configured, so `deployed: false` would be a false negative —
  // the contract may well be live and we simply cannot say where. Both the
  // chain and the count come back null: two separate unknowns, neither
  // rendered as a number.
  if (!cfg) return { deployed: true, chain_id: null, contract, minted_count: null };
  const out = await ethCall(contract, IFACE.encodeFunctionData('totalMinted', []));
  let count = null; // unreadable = unknown, never zero
  if (out != null) {
    try { count = Number(IFACE.decodeFunctionResult('totalMinted', out)[0]); }
    catch (e) { count = null; }
  }
  return {
    deployed: true,
    chain_id: chainId(),
    chain_name: cfg.name,
    explorer: `${cfg.explorer}/address/${contract}`,
    contract,
    minted_count: count,
  };
}

module.exports = {
  CHAIN_ID, DEFAULT_CHAIN_ID, CHAINS, DOMAIN_NAME, VOUCHER_TYPES, BASE_RPCS,
  chainId, chainConfig,
  contractAddress, buildMintPlan, readStats, mintedTokenOf, tokenImage,
  setNftFetcher,
};
