'use strict';
/**
 * ERC-8004 (Trustless Agents) on mainnet: the registration file RUNECLAW
 * serves, the unsigned plan the owner signs to register it, and the read that
 * says whether a registration is on chain.
 *
 * THE REGISTRIES. One address pair on every mainnet, another on every testnet,
 * as the standard's own contracts repository lists them
 * (github.com/erc-8004/erc-8004-contracts, README, read 22 Oct 2026). On Base
 * mainnet both addresses hold code (ERC-1967 proxies) and the identity
 * registry answers name "AgentIdentity", symbol "AGENT" (read the same day).
 * The design doc named only the Base Sepolia pair; that is the testnet row.
 * `MAINNETS` lists the mainnets read so far; each carries the mainnet pair.
 * There is no Validation registry in that list, so none is named here.
 *
 * THE SERVER NEVER SIGNS. `registrationPlan` returns calldata for
 * `register(string agentURI)` on the identity registry. The owner sends it
 * from their own wallet; the registry mints the agent id to that wallet and
 * records it as the agent's `agentWallet`. After it confirms, the owner
 * records the id and chain in the website's env (ERC8004_AGENT_ID,
 * ERC8004_CHAIN_ID) so the registration file can list it. A registration
 * nobody recorded is not listed: an absent id is not an id.
 *
 * THE FILE IS THE STANDARD'S SHAPE. `registrationFile` is the
 * registration-v1 document (EIP-8004, "Agent URI and Agent Registration
 * File"): the services an agent can reach (the website, the MCP endpoint, the
 * ERC-8257 tool manifest), `x402Support: false` (nothing is sold per call),
 * and no `supportedTrust`, because RUNECLAW uses this ERC for discovery today:
 * nothing it does yet reads or writes the Reputation registry.
 */

const { encodeCall } = require('./abi_call');
const { decodeLog } = require('./abi_decode');

/** The pair the standard deploys at the same addresses on every mainnet. */
const MAINNET_PAIR = Object.freeze({
  identity: '0x8004a169fb4a3325136eb29fa0ceb6d2e539a432',
  reputation: '0x8004baa17c55a88189ae136b182e5fda19de9b63',
});

/**
 * The mainnets this module registers on and reads. Each one's pair was read
 * on 22 Oct 2026: both addresses held code and the identity registry answered
 * name "AgentIdentity". Each lists two public endpoints from two operators
 * (each answered its chain id that day), so a verify read survives one being
 * down; Base's come from lib/nft.js, which serves it already.
 * [chain id, name, explorer, readers]
 */
const MAINNETS = [
  [8453, 'Base', 'https://basescan.org', null],
  // Avalanche runs its own consensus, so a registration here is not a second
  // copy of an Ethereum L2's.
  [43114, 'Avalanche C-Chain', 'https://snowtrace.io', ['https://api.avax.network/ext/bc/C/rpc', 'https://avalanche-c-chain-rpc.publicnode.com']],
  [1, 'Ethereum', 'https://etherscan.io', ['https://ethereum-rpc.publicnode.com', 'https://eth.drpc.org']],
  [56, 'BNB Smart Chain', 'https://bscscan.com', ['https://bsc-dataseed.bnbchain.org', 'https://bsc-rpc.publicnode.com']],
  [42161, 'Arbitrum One', 'https://arbiscan.io', ['https://arb1.arbitrum.io/rpc', 'https://arbitrum-one-rpc.publicnode.com']],
  [10, 'OP Mainnet', 'https://optimistic.etherscan.io', ['https://mainnet.optimism.io', 'https://optimism-rpc.publicnode.com']],
  [137, 'Polygon', 'https://polygonscan.com', ['https://polygon-bor-rpc.publicnode.com', 'https://polygon.drpc.org']],
  [59144, 'Linea', 'https://lineascan.build', ['https://rpc.linea.build', 'https://linea-rpc.publicnode.com']],
  [534352, 'Scroll', 'https://scrollscan.com', ['https://rpc.scroll.io', 'https://scroll-rpc.publicnode.com']],
];

/** The standard's registries, by chain. */
const REGISTRIES = Object.freeze({
  ...Object.fromEntries(MAINNETS.map(([id, name, explorer, rpcs]) => [id, Object.freeze({
    name, testnet: false, ...MAINNET_PAIR, explorer, ...(rpcs ? { rpcs: Object.freeze(rpcs.slice()) } : {}),
  })])),
  84532: Object.freeze({
    name: 'Base Sepolia', testnet: true,
    identity: '0x8004a818bfb912233c491871b3d84c89a494bd9e',
    reputation: '0x8004b663056a597dffe9eccc1965a193b7388713',
    explorer: 'https://sepolia.basescan.org',
  }),
});

const REGISTRATION_TYPE = 'https://eips.ethereum.org/EIPS/eip-8004#registration-v1';
/** Where the registration file is served: the domain-verification path the ERC names. */
const REGISTRATION_PATH = '/.well-known/agent-registration.json';

const REGISTERED = { name: 'Registered', inputs: [
  { name: 'agentId', type: 'uint256', indexed: true },
  { name: 'agentURI', type: 'string', indexed: false },
  { name: 'owner', type: 'address', indexed: true },
] };

const TX_RE = /^0x[0-9a-f]{64}$/i;

/** `eip155:<chain>:<identity registry>`, the ERC's global name for a registry. */
function agentRegistry(chainId) {
  const r = REGISTRIES[chainId];
  return r ? `eip155:${chainId}:${r.identity}` : null;
}

/**
 * The registration the owner recorded, or null. Both env vars must read: an
 * id with no chain, or a chain this module does not know, is no registration.
 */
function recorded(env = process.env) {
  const id = String(env.ERC8004_AGENT_ID || '').trim();
  const chain = String(env.ERC8004_CHAIN_ID || '').trim();
  if (!/^\d{1,30}$/.test(id) || !/^\d{1,10}$/.test(chain) || !REGISTRIES[Number(chain)]) return null;
  return { agentId: id, chainId: Number(chain) };
}

/** The registration-v1 document for this deployment's public origin. */
function registrationFile(origin, opts = {}) {
  const env = opts.env || process.env;
  const base = String(origin).replace(/\/+$/, '');
  const mcpVersion = opts.mcpVersion || null;
  const slug = opts.toolSlug || null;
  const rec = recorded(env);
  const services = [
    { name: 'web', endpoint: `${base}/` },
    { name: 'MCP', endpoint: `${base}/mcp`, ...(mcpVersion ? { version: mcpVersion } : {}) },
  ];
  if (slug) services.push({ name: 'ERC-8257', endpoint: `${base}/.well-known/ai-tool/${slug}.json` });
  return {
    type: REGISTRATION_TYPE,
    name: 'RUNECLAW',
    description: 'A trading agent that seals its calls before the market settles them, with a paper Arena, '
      + 'a free Daily Duel against its own sealed calls, and pre-signature safety checks. Practice and '
      + 'education: it sells nothing per call and gives no investment advice.',
    image: `${base}/app_icon_512.png`,
    services,
    x402Support: false,
    active: true,
    registrations: rec ? [{ agentId: Number(rec.agentId) <= Number.MAX_SAFE_INTEGER ? Number(rec.agentId) : rec.agentId,
      agentRegistry: agentRegistry(rec.chainId) }] : [],
  };
}

/**
 * The unsigned transaction the owner sends to register. `origin` must be the
 * configured public origin: the URI goes on chain, and one taken from a
 * request's Host header would be whatever the request said.
 */
function registrationPlan(chainId, origin) {
  const r = REGISTRIES[chainId];
  if (!r) return { error: 'unsupported_chain' };
  if (!origin) return { error: 'no_public_origin' };
  const agentURI = String(origin).replace(/\/+$/, '') + REGISTRATION_PATH;
  return {
    chain_id: chainId,
    chain: r.name,
    testnet: r.testnet,
    to: r.identity,
    value: '0',
    function: 'register(string agentURI)',
    agent_uri: agentURI,
    calldata: encodeCall('register(string)', [agentURI]),
    signs: 'the owner, from their own wallet; the server never signs',
    after: [
      `Wait for the transaction to confirm, then open ${r.explorer}/tx/<hash> and read the agent id from its Registered event.`,
      'Set ERC8004_AGENT_ID=<that id> and ERC8004_CHAIN_ID=' + chainId + ' in the website\'s web.env and redeploy.',
      `Check: GET /api/erc8004/verify?tx=<hash>&chain=${chainId} answers "verified".`,
    ],
  };
}

async function rpcCall(fetchImpl, url, method, params) {
  const res = await fetchImpl(url, {
    method: 'POST', headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ jsonrpc: '2.0', id: 1, method, params }),
    signal: AbortSignal.timeout(8000),
  });
  if (!res || !res.ok) throw new Error(`rpc ${method} http ${res ? res.status : '?'}`);
  const body = await res.json();
  if (body && body.error) throw new Error('rpc error');
  return body ? body.result : null;
}

/**
 * Is `txHash` a registration of `expectedURI` on `chainId`'s identity registry?
 *
 * → { status: 'verified', agent_id, owner, block }
 * → { status: 'mismatch', reason }   the transaction exists and is not that registration
 * → { status: 'pending' }            not mined yet
 * → { status: 'unknown', reason }    the chain could not be read: not a verdict
 */
async function verifyRegistration(txHash, chainId, expectedURI, deps = {}) {
  const r = REGISTRIES[chainId];
  if (!r) return { status: 'mismatch', reason: 'unsupported chain' };
  if (!TX_RE.test(String(txHash || ''))) return { status: 'mismatch', reason: 'malformed transaction hash' };
  const f = deps.fetchImpl || (typeof fetch === 'function' ? fetch : null);
  const rpcs = deps.rpcs || r.rpcs || require('./nft').CHAINS[chainId].rpcs;
  if (!f) return { status: 'unknown', reason: 'no fetch implementation available' };
  let receipt;
  let read = false;
  for (const url of rpcs) {
    try {
      receipt = await rpcCall(f, url, 'eth_getTransactionReceipt', [txHash]);
      read = true;
      break;
    } catch (e) { /* the next endpoint */ }
  }
  if (!read) return { status: 'unknown', reason: 'no endpoint answered' };
  if (!receipt) return { status: 'pending' };
  if (receipt.status !== '0x1') return { status: 'mismatch', reason: 'the transaction reverted' };
  // One transaction can register several agents (the registry on Base takes
  // over a thousand registrations in a hundred blocks): the one that counts
  // is the event carrying this URI, wherever it sits in the receipt.
  let registered = 0;
  for (const log of receipt.logs || []) {
    if (String(log.address || '').toLowerCase() !== r.identity) continue;
    let ev;
    try { ev = decodeLog(REGISTERED, log); } catch (e) { ev = null; }
    if (!ev) continue;
    registered += 1;
    if (ev.args.agentURI !== expectedURI) continue;
    return { status: 'verified', agent_id: String(ev.args.agentId), owner: ev.args.owner,
      agent_uri: ev.args.agentURI, block: parseInt(receipt.blockNumber, 16) };
  }
  return { status: 'mismatch', reason: registered
    ? 'registered a different agent URI' : 'no Registered event from the identity registry' };
}

module.exports = {
  REGISTRIES, REGISTRATION_TYPE, REGISTRATION_PATH, REGISTERED,
  agentRegistry, recorded, registrationFile, registrationPlan, verifyRegistration,
};
