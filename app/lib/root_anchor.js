'use strict';
/**
 * On-chain anchoring for the daily seal roots — Provable Calls' missing leg,
 * on more than one chain.
 *
 * A mirrored root already proves no call can be back-inserted into a day
 * WITHOUT changing the root; what it cannot prove alone is WHEN the root
 * existed — for that you had to trust our database's timestamps. An anchor
 * transaction closes that gap: the day's root rides as calldata in a
 * zero-value transaction, and the BLOCK timestamp — a fact no one here
 * controls — becomes the independent upper bound on when every seal in that
 * day was minted.
 *
 * Non-custodial to the letter (same doctrine as the ERC-8257 plan): the
 * server never holds a key and never sends a transaction. buildAnchorPlan()
 * produces the exact transaction for a wallet's owner; verifyAnchor() READS
 * the chain through keyless RPCs and only ever records what it verified. On
 * an RPC failure it answers UNKNOWN — it never guesses either way.
 *
 * ONE PAYLOAD, EVERY CHAIN. The bytes are the same on Base, on the DogeOS
 * testnet (2026-10-19, the Vibe Code on DOGE entry) and, when its flag is on,
 * on Avalanche C-Chain: what differs is which chain's block clock bounds the
 * day, and a day may be anchored on each. The chain registry below is the
 * one reading of a chain's id, RPCs and explorer; the routes, the pages and
 * the DogeOS app read it from here (`GET /api/roots/chains`) rather than
 * carrying literals of their own.
 *
 * Payload format, self-describing and greppable on-chain:
 *   utf8("RCROOT1:" + day + ":" + root)   as the tx `data`, hex-encoded.
 */
const { agree, requiredOf, unread } = require('./reader_agreement');
const solana = require('./solana_anchor');

const PREFIX = 'RCROOT1';
const DAY_RE = /^\d{4}-\d{2}-\d{2}$/;
const ROOT_RE = /^[0-9a-f]{64}$/;
const TX_RE = /^0x[0-9a-f]{64}$/i;

// Same keyless Base endpoints the wallet mirror verified (lib/wallet.js).
const BASE_RPCS = ['https://mainnet.base.org', 'https://base-rpc.publicnode.com'];
// DogeOS's own public testnet endpoint (docs.dogeos.com, read 2026-10-19):
// the one pages and wallets are handed. The server also reads through dRPC's
// keyless endpoint (it answered as chain 6281971 on 2026-10-21), so one
// endpoint down is not "could not be read".
const DOGEOS_TESTNET_PUBLIC_RPCS = ['https://rpc.testnet.dogeos.com/'];
const DOGEOS_TESTNET_RPCS = [...DOGEOS_TESTNET_PUBLIC_RPCS, 'https://dogeos-testnet.drpc.org'];
/**
 * Avalanche C-Chain (chain id 43114). The same two keyless readers
 * lib/erc8004.js already asks for a registration there, re-read 23 Oct 2026:
 * both answered eth_chainId 0xa86a. Avalanche runs Snowman, not Ethereum
 * settlement, so a calldata anchor here is its own independence group — the
 * fifth that counts, after ethereum-l2, bitcoin, dogecoin and solana.
 * Off unless AVALANCHE_ANCHOR_ENABLED is set. The date is the operator's
 * target for turning it on; the flag does not read it.
 */
const AVALANCHE_CHAIN_ID = 43114;
const AVALANCHE_RPCS = ['https://api.avax.network/ext/bc/C/rpc', 'https://avalanche-c-chain-rpc.publicnode.com'];
const AVALANCHE_ANCHOR_FLAG = 'AVALANCHE_ANCHOR_ENABLED';
const AVALANCHE_ANCHOR_TARGET = '2026-11-05';

/** Whether Avalanche is in the registry. Unset is off. `env` is for tests. */
function avalancheEnabled(env) {
  return /^(1|true|yes|on)$/i.test(String((env || process.env)[AVALANCHE_ANCHOR_FLAG] || '').trim());
}

/**
 * The Ethereum Attestation Service on Base: an OP Stack predeploy, so the
 * same address on every OP Stack chain. Read on 2026-10-21: code at the
 * address, version() "1.0.1", getTimestamp of an unknown hash 0, and a
 * simulated timestamp() answering the block's time.
 *   timestamp(bytes32)    0x4d003070   records block.timestamp for the hash,
 *                                      once; a second call reverts
 *                                      (AlreadyTimestamped)
 *   getTimestamp(bytes32) 0xd45c4435   that time, or 0
 * Selectors are keccak256 of the signatures (lib/keccak256.js), pinned by
 * app/test/a_day_is_stamped_on_mainnets.test.js.
 */
const EAS_BASE = '0x4200000000000000000000000000000000000021';
const EAS_TIMESTAMP = '0x4d003070';
const EAS_GET_TIMESTAMP = '0xd45c4435';

/**
 * EAS on three more Ethereum L2s. Addresses from the eas-contracts README,
 * read 2026-10-22, and each contract read live the same day through both
 * RPCs below: an unknown selector reverts, getTimestamp of an unknown hash
 * answers 0, and a simulated timestamp() answers the block's time.
 *   Arbitrum One  chain 42161   EAS 0.26 (version() reverts: it predates it)
 *   Scroll        chain 534352  EAS 1.3.0
 *   Linea         chain 59144   EAS 1.2.0
 * Each settles to Ethereum, so each joins Base's independence group: more
 * places a player can stamp a root, not more independent witnesses.
 */
const EAS_L2 = [
  { key: 'arbitrum-eas', chain_id: 42161, name: 'Arbitrum One', eas: '0xbD75f629A22Dc1ceD33dDA0b68c546A1c035c458',
    env: 'ARBITRUM_RPC_URLS', rpcs: ['https://arb1.arbitrum.io/rpc', 'https://arbitrum-one-rpc.publicnode.com'],
    explorer_tx: 'https://arbiscan.io/tx/' },
  { key: 'scroll-eas', chain_id: 534352, name: 'Scroll', eas: '0xC47300428b6AD2c7D03BB76D05A176058b47E6B0',
    env: 'SCROLL_RPC_URLS', rpcs: ['https://rpc.scroll.io', 'https://scroll-rpc.publicnode.com'],
    explorer_tx: 'https://scrollscan.com/tx/' },
  { key: 'linea-eas', chain_id: 59144, name: 'Linea', eas: '0xaEF4103A04090071165F78D45D83A0C0782c2B2a',
    env: 'LINEA_RPC_URLS', rpcs: ['https://rpc.linea.build', 'https://linea-rpc.publicnode.com'],
    explorer_tx: 'https://lineascan.build/tx/' },
];

/**
 * A comma list in the environment replaces a chain's default RPCs, for the
 * SERVER's reads only. It may carry a key (a provider's URL often does), so
 * it is never handed to a page or a wallet: those get `public_rpcs`, the
 * keyless defaults below.
 */
function rpcList(envKey, defaults, env) {
  const raw = String((env || process.env)[envKey] || '').trim();
  const urls = raw ? raw.split(',').map((s) => s.trim()).filter((s) => /^https?:\/\//.test(s)) : [];
  return urls.length ? urls : defaults.slice();
}

/**
 * The chains a day's root may be anchored on. `key` is what the API and the
 * database call the chain; `chain_id` is what a wallet is asked to switch to;
 * `native` is what `wallet_addEthereumChain` needs to describe it.
 */
function chains(env) {
  const all = {
    base: {
      key: 'base', chain_id: 8453, name: 'Base', testnet: false,
      rpcs: rpcList('WEB3_RPC_URL_BASE_ANCHOR', BASE_RPCS, env),
      public_rpcs: BASE_RPCS.slice(),
      explorer_tx: 'https://basescan.org/tx/',
      native: { name: 'Ether', symbol: 'ETH', decimals: 18 },
      // What a witness on this chain is independent OF (lib/anchor_quorum.js):
      // Base settles to Ethereum, so another Ethereum L2 joins this group
      // rather than counting as a second witness.
      independence_group: 'ethereum-l2',
      // QuickNode's name for this chain, through Flovia (lib/flovia.js): a
      // second reader of a transaction when every RPC above fails to answer.
      // A chain without one (the DogeOS testnet) has no fallback.
      quicknode: 'base',
    },
    'dogeos-testnet': {
      key: 'dogeos-testnet', chain_id: 6281971, name: 'DogeOS testnet', testnet: true,
      rpcs: rpcList('DOGEOS_TESTNET_RPC_URLS', DOGEOS_TESTNET_RPCS, env),
      public_rpcs: DOGEOS_TESTNET_PUBLIC_RPCS.slice(),
      explorer_tx: 'https://dogeos-testnet.l2scan.co/tx/',
      // A second explorer with its own index (DogeOS runs both; L2Scan's API
      // answers one call at a time, Blockscout's is keyless).
      explorer_tx_alt: 'https://blockscout.testnet.dogeos.com/tx/',
      // "Reorgs can be up to 17 blocks deep" (docs.dogeos.com, Ethereum and
      // DogeOS differences, read 2026-10-21). Nothing read from this chain is
      // recorded until its block is that deep; `finalized` (about 2.5 hours
      // behind the head, measured the same day) is reported, not waited on.
      reorg_depth: 17,
      // A testnet can be reset and its blocks rewritten: shown, never counted
      // toward a day's quorum.
      independence_group: 'dogeos-testnet', counts_toward_quorum: false,
      faucet: 'https://faucet.testnet.dogeos.com',
      native: { name: 'Dogecoin', symbol: 'DOGE', decimals: 18 },
    },
    // The same day's root, stamped in EAS's own registry on Base mainnet by a
    // player's wallet: a call to timestamp(root) instead of a self-send. The
    // stamp is then readable BY THE ROOT (getTimestamp), with no transaction
    // hash needed, and only the first stamp of a root ever lands.
    'base-eas': {
      key: 'base-eas', chain_id: 8453, name: 'Base (EAS timestamp)', testnet: false,
      method: 'eas_timestamp', eas: EAS_BASE,
      // The same chain as `base`: a calldata anchor and an EAS stamp there are one witness.
      independence_group: 'ethereum-l2',
      rpcs: rpcList('WEB3_RPC_URL_BASE_ANCHOR', BASE_RPCS, env),
      public_rpcs: BASE_RPCS.slice(),
      explorer_tx: 'https://basescan.org/tx/',
      native: { name: 'Ether', symbol: 'ETH', decimals: 18 },
    },
    // The same stamp on the other L2s (EAS_L2 above), each its own registry.
    ...Object.fromEntries(EAS_L2.map((c) => [c.key, {
      key: c.key, chain_id: c.chain_id, name: `${c.name} (EAS timestamp)`, testnet: false,
      method: 'eas_timestamp', eas: c.eas,
      independence_group: 'ethereum-l2',
      rpcs: rpcList(c.env, c.rpcs, env),
      public_rpcs: c.rpcs.slice(),
      explorer_tx: c.explorer_tx,
      native: { name: 'Ether', symbol: 'ETH', decimals: 18 },
    }])),
    // Dogecoin itself (mainnet, the owner's go on 2026-10-21): the root as an
    // OP_RETURN output a wallet's owner sends from Dogecoin Core, read back
    // through a keyless explorer API (lib/dogecoin_anchor.js). Not an EVM
    // chain: no chain id and no RPCs to hand a wallet.
    dogecoin: {
      key: 'dogecoin', chain_id: null, name: 'Dogecoin', testnet: false,
      method: 'dogecoin_op_return',
      independence_group: 'dogecoin',
      rpcs: [], public_rpcs: [],
      explorer_tx: 'https://blockchair.com/dogecoin/transaction/',
      // Six confirmations, about six minutes at Dogecoin's one-minute blocks:
      // the customary Bitcoin-family depth, a convention and not a protocol
      // parameter. Nothing is recorded before it.
      reorg_depth: 6,
      // BlockCypher's keyless tier allows 100 reads an hour, so a verdict
      // past the depth is kept for six hours rather than five minutes.
      verify_cache_ms: 6 * 3600 * 1000,
      native: { name: 'Dogecoin', symbol: 'DOGE', decimals: 8 },
    },
    // Solana (mainnet, 2026-10-22): the root as a Memo-program instruction a
    // wallet's owner sends, read back from two keyless JSON-RPC readers at
    // `finalized` commitment (lib/solana_anchor.js). Not an EVM chain: no
    // chain id and nothing to hand a wallet to add. Its own consensus, so its
    // own independence group.
    solana: {
      key: 'solana', chain_id: null, name: 'Solana', testnet: false,
      method: 'solana_memo',
      independence_group: 'solana',
      rpcs: rpcList('SOLANA_RPC_URLS', solana.DEFAULT_RPCS, env), public_rpcs: [],
      explorer_tx: 'https://solscan.io/tx/',
      native: { name: 'Solana', symbol: 'SOL', decimals: 9 },
    },
  };
  // Same calldata self-send as Base. Absent until the flag is on, so the
  // public registry (and the privacy section that lists it) does not grow
  // before an operator turns it on. A recorded anchor on a chain this build
  // does not know reads as unknown, never as verified.
  if (avalancheEnabled(env)) {
    all.avalanche = {
      key: 'avalanche', chain_id: AVALANCHE_CHAIN_ID, name: 'Avalanche C-Chain', testnet: false,
      rpcs: rpcList('AVALANCHE_RPC_URLS', AVALANCHE_RPCS, env),
      public_rpcs: AVALANCHE_RPCS.slice(),
      explorer_tx: 'https://snowtrace.io/tx/',
      native: { name: 'Avalanche', symbol: 'AVAX', decimals: 18 },
      independence_group: 'avalanche',
    };
  }
  return all;
}

/**
 * Whether a chain's transaction ids are case-sensitive (Solana's base58
 * signatures). Every other chain's is hex, which is lower-cased before it is
 * checked or stored.
 */
function caseSensitiveTx(chain) {
  return !!chain && chain.method === 'solana_memo';
}
const DEFAULT_CHAIN = 'base';

/** The registry entry for `key`, or null for a chain this build does not know. */
function chainInfo(key, env) {
  const all = chains(env);
  return Object.prototype.hasOwnProperty.call(all, String(key || '')) ? all[String(key)] : null;
}

/**
 * What a page or an app may be told about every chain. The RPCs are the
 * keyless defaults, never the environment's override: until 21 October 2026
 * this returned `c.rpcs`, so a provider URL set in DOGEOS_TESTNET_RPC_URLS or
 * WEB3_RPC_URL_BASE_ANCHOR, key and all, was served on /api/roots/chains and
 * handed to every wallet asked to add the chain.
 */
function publicChains(env) {
  return Object.values(chains(env)).map((c) => ({
    key: c.key, chain_id: c.chain_id, name: c.name, testnet: c.testnet,
    rpcs: c.public_rpcs.slice(), explorer_tx: c.explorer_tx, native: c.native,
    ...(c.faucet ? { faucet: c.faucet } : {}),
    ...(c.explorer_tx_alt ? { explorer_tx_alt: c.explorer_tx_alt } : {}),
    ...(c.reorg_depth ? { reorg_depth: c.reorg_depth } : {}),
    // How a root is put on this chain: a self-send whose calldata is the
    // tagged root, or a call to EAS's timestamp() at `eas`.
    method: c.method || 'calldata',
    ...(c.eas ? { eas: c.eas } : {}),
    independence_group: c.independence_group,
    counts_toward_quorum: c.counts_toward_quorum !== false,
  }));
}

function payloadFor(day, root) {
  return '0x' + Buffer.from(`${PREFIX}:${day}:${root}`, 'utf8').toString('hex');
}

/** The call that stamps `root` in EAS: timestamp(bytes32 root). The root is SHA-256, 32 bytes. */
function easStampCalldata(root) {
  return EAS_TIMESTAMP + String(root).toLowerCase();
}

/**
 * The transaction a wallet's owner sends: a zero-value self-send on `chain`
 * whose data is the tagged root. DRY RUN ONLY. Null for a malformed day or
 * root, or a chain this build does not know.
 */
function buildAnchorPlan(day, root, chainKey = DEFAULT_CHAIN, env) {
  if (!DAY_RE.test(String(day)) || !ROOT_RE.test(String(root))) return null;
  const chain = chainInfo(chainKey, env);
  if (!chain) return null;
  if (chain.method === 'dogecoin_op_return') return require('./dogecoin_anchor').buildPlan(day, root, chain);
  if (chain.method === 'solana_memo') return solana.buildPlan(day, root, chain);
  if (chain.method === 'eas_timestamp') {
    const data = easStampCalldata(root);
    return {
      dry_run: true, chain_id: chain.chain_id, chain: chain.name, chain_key: chain.key,
      method: chain.method, to: chain.eas, value: '0', data, day, root,
      instructions: [
        `Send a 0-value transaction FROM your own wallet TO ${chain.eas} (the Ethereum Attestation `
          + `Service on ${chain.name.replace(/ \(.*\)$/, '')}), with \`data\` set to the data field above: `
          + 'timestamp(root). Only the first stamp of a root lands; a second one reverts.',
        `Or with foundry: cast send ${chain.eas} "timestamp(bytes32)" 0x${root} --rpc-url `
          + `${chain.public_rpcs[0]} --private-key <YOUR_KEY_NEVER_SHARED_WITH_THE_SERVER>`,
        'Then submit the transaction hash back via POST /api/roots/anchor '
          + `{ day, tx_hash, chain: "${chain.key}" } — the server reads the stamp back from EAS `
          + 'before recording anything.',
      ],
      non_custodial_note: 'The server never holds a key and never sends a '
        + 'transaction. This is a plan, not an action.',
    };
  }
  const data = payloadFor(day, root);
  return {
    dry_run: true,
    chain_id: chain.chain_id,
    chain: chain.name,
    chain_key: chain.key,
    to: 'YOUR OWN ADDRESS (a zero-value self-send — no contract needed)',
    value: '0',
    data,
    day, root,
    instructions: [
      `Send a 0-value transaction FROM your own wallet TO the same address on ${chain.name}, `
        + 'with `data` set to the data field above. Any wallet that allows hex data works.',
      `Or with foundry: cast send <YOUR_ADDRESS> --value 0 --rpc-url ${chain.public_rpcs[0]} `
        + `--private-key <YOUR_KEY_NEVER_SHARED_WITH_THE_SERVER> ${data}`,
      'Then submit the transaction hash back via POST /api/roots/anchor '
        + `{ day, tx_hash, chain: "${chain.key}" } — the server verifies the calldata `
        + 'against the chain before recording anything.',
    ],
    non_custodial_note: 'The server never holds a key and never sends a '
      + 'transaction. This is a plan, not an action.',
  };
}

/** A JSON-RPC caller over `f`; throws on a transport or RPC error. */
function rpcCaller(f) {
  return async (url, method, params) => {
    const r = await f(url, {
      method: 'POST', headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ jsonrpc: '2.0', id: 1, method, params }),
    });
    if (!r.ok) throw new Error(`rpc http ${r.status}`);
    const j = await r.json();
    if (j.error) throw new Error(`rpc ${j.error.code}: ${j.error.message}`);
    return j.result;
  };
}

/** EAS's stamp of `root` as seconds (a BigInt), 0n when it has none; throws when unreadable. */
async function readEasStamp(call, url, chain, root) {
  const out = await call(url, 'eth_call', [{ to: chain.eas, data: EAS_GET_TIMESTAMP + root }, 'latest']);
  if (typeof out !== 'string' || !/^0x[0-9a-fA-F]{64}$/.test(out)) throw new Error('getTimestamp answered no uint64');
  return BigInt(out);
}

/**
 * Whether `root` is stamped in EAS on `chainKey`, read by the root alone:
 * → { status: 'stamped', stamped_at }   EAS holds a time for it
 * → { status: 'unstamped' }             EAS answered 0
 * → { status: 'unknown', reason }       no RPC answered — not a verdict
 */
async function easStampOf(root, fetchImpl, chainKey = 'base-eas') {
  const chain = chainInfo(chainKey);
  if (!chain || chain.method !== 'eas_timestamp') return { status: 'unknown', reason: 'not an EAS chain' };
  if (!ROOT_RE.test(String(root))) return { status: 'unknown', reason: 'malformed root' };
  const call = rpcCaller(fetchImpl || global.fetch);
  let lastErr = null;
  for (const url of chain.rpcs) {
    try {
      const t = await readEasStamp(call, url, chain, root);
      return t === 0n ? { status: 'unstamped' } : { status: 'stamped', stamped_at: new Date(Number(t) * 1000).toISOString() };
    } catch (e) { lastErr = e; }
  }
  return { status: 'unknown', reason: lastErr ? `${chain.name} could not be read (${lastErr.name || 'Error'})` : 'rpc unreachable' };
}

/**
 * The EAS leg of verifyAnchor, as one reader (`url`) sees it: the
 * transaction is a call to EAS's timestamp(root) that succeeded, and EAS's
 * own record of the root is that transaction's block time. The registry is
 * the final word: a transaction that reverted (the root was stamped first by
 * someone else) did not stamp it, whatever its calldata says. Throws when
 * the reader does not answer.
 */
async function readEasStampAnchor(call, url, chain, txHash, root) {
  const tx = await call(url, 'eth_getTransactionByHash', [txHash]);
  if (!tx) return { status: 'mismatch', reason: `no such transaction on ${chain.name}` };
  if (!tx.blockHash) return { status: 'unknown', reason: 'transaction not yet mined' };
  if (String(tx.to || '').toLowerCase() !== chain.eas.toLowerCase()) {
    return { status: 'mismatch', reason: 'the transaction is not a call to the Ethereum Attestation Service' };
  }
  if (String(tx.input || tx.data || '').toLowerCase() !== easStampCalldata(root)) {
    return { status: 'mismatch', reason: 'the transaction does not stamp this root' };
  }
  const rc = await call(url, 'eth_getTransactionReceipt', [txHash]);
  if (!rc || !rc.status) return { status: 'unknown', reason: 'the receipt could not be read' };
  if (rc.status !== '0x1') {
    return { status: 'mismatch', reason: 'the transaction reverted: the root may already have been stamped by another' };
  }
  const block = await call(url, 'eth_getBlockByHash', [tx.blockHash, false]);
  if (!block || !block.timestamp) return { status: 'unknown', reason: 'block unreadable' };
  const stamped = await readEasStamp(call, url, chain, root);
  if (stamped === 0n) return { status: 'unknown', reason: 'EAS does not hold the stamp yet' };
  if (stamped !== BigInt(block.timestamp)) {
    return { status: 'mismatch', reason: 'EAS holds an earlier stamp of this root than this transaction' };
  }
  return {
    status: 'verified', same: String(tx.blockHash).toLowerCase(),
    block_time: new Date(Number(stamped) * 1000).toISOString(),
    from: String(tx.from || ''),
  };
}

/**
 * The calldata leg of verifyAnchor, as one reader (`url`) sees it: the
 * transaction's calldata is exactly the tagged root payload, and on a chain
 * with a reorg depth its block is that deep. Throws when the reader does not
 * answer.
 */
async function readCalldataAnchor(call, url, chain, txHash, day, root) {
  const tx = await call(url, 'eth_getTransactionByHash', [txHash]);
  if (!tx) return { status: 'mismatch', reason: `no such transaction on ${chain.name}` };
  if (!tx.blockHash) return { status: 'unknown', reason: 'transaction not yet mined' };
  const expected = payloadFor(day, root).toLowerCase();
  const data = String(tx.input || tx.data || '').toLowerCase();
  if (data !== expected) {
    // includes() would accept a payload buried in unrelated calldata —
    // exact equality is the claim being made.
    return { status: 'mismatch', reason: 'calldata does not equal the tagged root payload' };
  }
  const block = await call(url, 'eth_getBlockByHash', [tx.blockHash, false]);
  if (!block || !block.timestamp) return { status: 'unknown', reason: 'block unreadable' };
  let final;
  if (chain.reorg_depth) {
    // In a block is not yet on the chain for good: wait out the
    // documented reorg depth before calling the anchor verified.
    const headHex = await call(url, 'eth_blockNumber', []);
    if (!headHex || !tx.blockNumber) return { status: 'unknown', reason: 'the block height could not be read' };
    const deep = BigInt(headHex) - BigInt(tx.blockNumber);
    if (deep < BigInt(chain.reorg_depth)) {
      return { status: 'unknown',
        reason: `${deep < 0n ? 0 : deep} of the ${chain.reorg_depth} confirmations ${chain.name} needs` };
    }
    let fin = null;
    try { fin = await call(url, 'eth_getBlockByNumber', ['finalized', false]); } catch (e) { fin = null; }
    final = fin && fin.number ? BigInt(fin.number) >= BigInt(tx.blockNumber) : null;
  }
  return {
    status: 'verified', same: String(tx.blockHash).toLowerCase(),
    block_time: new Date(parseInt(block.timestamp, 16) * 1000).toISOString(),
    from: String(tx.from || ''),
    ...(chain.reorg_depth ? { final } : {}),
  };
}

/** The keyless defaults, named by host in a verdict. */
const KEYLESS_RPCS = new Set([...BASE_RPCS, ...DOGEOS_TESTNET_RPCS, ...AVALANCHE_RPCS, ...EAS_L2.flatMap((c) => c.rpcs)]);

/** A reader's name in a verdict: a keyless default by its host; a configured URL, which may carry a key, by its place only. */
function readerName(url, i) {
  if (KEYLESS_RPCS.has(url)) {
    try { return new URL(url).hostname; } catch (e) { /* by place */ }
  }
  return `configured rpc ${i + 1}`;
}

/** Whether QuickNode, through Flovia, can be asked as one more reader of `chain`. */
function quickNodeReady(chain) {
  if (!chain.quicknode) return false;
  try { return require('./flovia').configured(); } catch (e) { return false; }
}

/**
 * Read the chain and answer: does this transaction anchor this day's root?
 * Every RPC in the chain's list is asked, and the answer is their agreement
 * (lib/reader_agreement.js): two readers must give the same verdict, in the
 * same block, and none may contradict it. One RPC that lies, lags or is
 * down cannot turn a day green, nor refuse a real anchor.
 * → { status: 'verified', block_time, from, readers }    the calldata matches, and on a
 *   chain with a reorg depth the block is that deep; there `final` says
 *   whether the chain's `finalized` block has passed it (null: not read)
 * → { status: 'mismatch', reason, readers }      the readers agree the tx does not anchor this root
 * → { status: 'unknown', reason, disagreement? } too few readers answered, or they disagree — NOT a verdict
 *
 * `fetchImpl` is injectable for tests; production uses global fetch against
 * the chain's RPCs. A chain QuickNode serves gets it, through Flovia, as one
 * more reader when an RPC does not answer.
 */
async function verifyAnchor(txHash, day, root, fetchImpl, chainKey = DEFAULT_CHAIN, env) {
  const chain = chainInfo(chainKey, env);
  if (!chain) return { status: 'mismatch', reason: 'unknown chain' };
  // Dogecoin's transaction ids have no 0x; the module checks its own shape.
  if (chain.method === 'dogecoin_op_return') {
    return require('./dogecoin_anchor').verify(txHash, day, root, chain, fetchImpl);
  }
  if (chain.method === 'solana_memo') return solana.verify(txHash, day, root, chain, fetchImpl);
  if (!TX_RE.test(String(txHash))) return { status: 'mismatch', reason: 'malformed transaction hash' };
  if (!DAY_RE.test(String(day)) || !ROOT_RE.test(String(root))) {
    return { status: 'mismatch', reason: 'malformed day or root' };
  }
  const call = rpcCaller(fetchImpl || global.fetch);
  const readOne = chain.method === 'eas_timestamp'
    ? (url) => readEasStampAnchor(call, url, chain, txHash, root)
    : (url) => readCalldataAnchor(call, url, chain, txHash, day, root);
  const answers = await Promise.all(chain.rpcs.map(async (url, i) => {
    const reader = readerName(url, i);
    try { return { reader, ...(await readOne(url)) }; } catch (e) { return unread(reader, e); }
  }));
  const quickNode = chain.method !== 'eas_timestamp' && quickNodeReady(chain);
  const required = requiredOf(chain.rpcs.length + (quickNode ? 1 : 0));
  const what = `the ${chain.name} readers`;
  let v = agree(answers, required, what);
  // QuickNode takes a seat no RPC filled: one that did not answer, or one
  // never configured (a list of one URL). Readers that answered and differ
  // are not settled by asking another.
  const answered = answers.filter((a) => a.status !== 'unread').length;
  if (v.status === 'unknown' && !v.disagreement && quickNode && answered < required) {
    const second = await verifyThroughQuickNode(txHash, day, root, chain);
    if (second) {
      const { source, ...rest } = second;
      answers.push({ reader: 'quicknode', ...rest });
    } else {
      answers.push({ reader: 'quicknode', status: 'unread', reason: 'quicknode could not be read' });
    }
    v = agree(answers, required, what);
  }
  if (v.status === 'verified' && chain.reorg_depth) {
    // Final only when every reader that verified it saw it final.
    const finals = answers.filter((a) => a.status === 'verified').map((a) => a.final);
    v.final = finals.includes(false) ? false : finals.every((x) => x === true) ? true : null;
  }
  return v;
}

/**
 * The same verification over QuickNode's read of the chain (two reads: the
 * transaction, then its block's header for the time). Null when Flovia is
 * unconfigured or refused, or answered something this cannot read, so the
 * caller's own "unknown" stands with its own reason; a verdict otherwise,
 * marked `source: 'quicknode'`.
 */
async function verifyThroughQuickNode(txHash, day, root, chain) {
  let flovia;
  try { flovia = require('./flovia'); } catch (e) { return null; }
  if (!flovia.configured()) return null;
  const ask = (method, params, block) => flovia.call('flovia_quicknode', {
    chain: chain.quicknode, method, params, ...(block ? { block } : {}),
    request_text: `RUNECLAW seal-root anchor check: ${method} on ${chain.name} for ${txHash}`,
    purpose: 'Verifying a day\'s seal-root anchor when the public RPCs do not answer; the verdict is shown on the roots page.',
  });
  const txR = await ask('eth_getTransactionByHash', { hash: txHash });
  if (!txR.read) return null;
  const tx = txR.data && typeof txR.data === 'object' ? (txR.data.result !== undefined ? txR.data.result : txR.data) : null;
  if (tx === null) return { status: 'mismatch', reason: `no such transaction on ${chain.name}`, source: 'quicknode' };
  if (!tx || typeof tx !== 'object') return null;
  if (!tx.blockHash && !tx.blockNumber) return { status: 'unknown', reason: 'transaction not yet mined', source: 'quicknode' };
  const expected = payloadFor(day, root).toLowerCase();
  const data = String(tx.input || tx.data || '').toLowerCase();
  if (data !== expected) return { status: 'mismatch', reason: 'calldata does not equal the tagged root payload', source: 'quicknode' };
  const num = typeof tx.blockNumber === 'string' && /^0x/i.test(tx.blockNumber) ? parseInt(tx.blockNumber, 16) : Number(tx.blockNumber);
  if (!Number.isFinite(num)) return { status: 'unknown', reason: 'block unreadable', source: 'quicknode' };
  const blockR = await ask('eth_getBlockByNumber', { header_only: true }, num);
  if (!blockR.read) return { status: 'unknown', reason: 'block unreadable', source: 'quicknode' };
  const block = blockR.data && typeof blockR.data === 'object' ? (blockR.data.result !== undefined ? blockR.data.result : blockR.data) : null;
  const ts = block && (block.timestamp !== undefined ? block.timestamp : block.time);
  const secs = typeof ts === 'string' && /^0x/i.test(ts) ? parseInt(ts, 16) : Number(ts);
  if (!Number.isFinite(secs) || secs <= 0) return { status: 'unknown', reason: 'block unreadable', source: 'quicknode' };
  return {
    status: 'verified', source: 'quicknode',
    // The block it saw, so its verdict can be held against the RPCs'.
    same: tx.blockHash ? String(tx.blockHash).toLowerCase() : null,
    block_time: new Date(secs * 1000).toISOString(),
    from: String(tx.from || ''),
  };
}

module.exports = {
  buildAnchorPlan, verifyAnchor, verifyThroughQuickNode, payloadFor, PREFIX, BASE_RPCS, DOGEOS_TESTNET_RPCS,
  DOGEOS_TESTNET_PUBLIC_RPCS, AVALANCHE_CHAIN_ID, AVALANCHE_RPCS, AVALANCHE_ANCHOR_FLAG, AVALANCHE_ANCHOR_TARGET,
  avalancheEnabled, EAS_BASE, EAS_L2, EAS_TIMESTAMP, EAS_GET_TIMESTAMP, easStampCalldata, easStampOf,
  chains, chainInfo, publicChains, DEFAULT_CHAIN, caseSensitiveTx,
};
