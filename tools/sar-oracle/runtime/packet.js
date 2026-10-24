'use strict';
/**
 * Canonical copy. app/lib/sar is a byte copy the site loads.
 * Do not edit one side.
 *
 * EIP-712 packets for SaRVerifier. The domain chain is Base Sepolia
 * (84532) only. On any other chain this module does not read
 * SAR_ORACLE_SIGNING_KEY and does not sign. A mainnet process holds no key.
 *
 * Schema v1 is the public packet. The signature is over an epoch Merkle
 * root of on-chain Metric structs, not over a single market, and only
 * when every on-chain field was actually read. A missing field is left
 * out of the tree. It is not encoded as zero.
 *
 * The key is read only after the chain id is known to be Base Sepolia.
 */

const { BASE_SEPOLIA } = require('./chains');

const DOMAIN_NAME = 'SaR Oracle';
const DOMAIN_VERSION = '1';
const KEY_RE = /^0x[0-9a-fA-F]{64}$/;
const ADDR_RE = /^0x[0-9a-fA-F]{40}$/;

const EPOCH_TYPES = {
  Epoch: [
    { name: 'epoch', type: 'uint64' },
    { name: 'root', type: 'bytes32' },
    { name: 'l1Block', type: 'uint64' },
    { name: 'scheme', type: 'uint8' },
  ],
};

const SCHEME_SECP256K1 = 0;

const TYPES = {
  MarketPacket: [
    { name: 'venue', type: 'string' },
    { name: 'market', type: 'string' },
    { name: 'present', type: 'uint8' },
    { name: 'slippageWad', type: 'int256' },
    { name: 'nEffWad', type: 'uint256' },
    { name: 'cr1Wad', type: 'uint256' },
    { name: 'oiWithinBandWad', type: 'uint256' },
    { name: 'observedAt', type: 'uint256' },
    { name: 'maxStaleness', type: 'uint256' },
  ],
};

/**
 * What this process may sign with. The key is read only after the chain
 * id is known to be Base Sepolia, so a wrong chain cannot throw on a
 * malformed key and cannot load one.
 */
function signerConfig(env) {
  const src = env || process.env;
  const raw = src.SAR_ORACLE_CHAIN_ID;
  let chainId = BASE_SEPOLIA;
  if (!(raw == null || String(raw).trim() === '')) {
    const t = String(raw).trim();
    if (!/^\d+$/.test(t)) return { ok: false, reason: 'chain_unreadable', chainId: null };
    chainId = Number(t);
  }
  if (chainId !== BASE_SEPOLIA) {
    return { ok: false, reason: 'not_base_sepolia', chainId };
  }
  const key = src.SAR_ORACLE_SIGNING_KEY;
  if (typeof key !== 'string' || !KEY_RE.test(key.trim())) {
    return { ok: false, reason: 'key_unreadable', chainId };
  }
  const verifier = src.SAR_ORACLE_VERIFIER_ADDRESS;
  if (typeof verifier !== 'string' || !ADDR_RE.test(verifier.trim())) {
    return { ok: false, reason: 'verifier_unreadable', chainId };
  }
  return { ok: true, reason: null, chainId, key: key.trim(), verifier: verifier.trim() };
}

function signingLabel(cfg) {
  if (cfg && cfg.ok) return 'base-sepolia';
  if (cfg && cfg.reason === 'not_base_sepolia') return 'refused_not_testnet';
  return 'unsigned';
}

function messageFrom(packet) {
  return {
    venue: packet.venue,
    market: packet.market,
    present: packet.present,
    slippageWad: BigInt(packet.slippage_wad),
    nEffWad: BigInt(packet.n_eff_wad),
    cr1Wad: BigInt(packet.cr1_wad),
    oiWithinBandWad: BigInt(packet.oi_within_band_wad),
    observedAt: BigInt(packet.observed_at),
    maxStaleness: BigInt(packet.max_staleness),
  };
}

function domainOf(cfg) {
  return {
    name: DOMAIN_NAME,
    version: DOMAIN_VERSION,
    chainId: cfg.chainId,
    verifyingContract: cfg.verifier,
  };
}

async function signPacket(cfg, packet) {
  const { Wallet } = require('ethers');
  const wallet = new Wallet(cfg.key);
  const domain = domainOf(cfg);
  const signature = await wallet.signTypedData(domain, TYPES, messageFrom(packet));
  return {
    venue: packet.venue,
    market: packet.market,
    present: packet.present,
    slippage_wad: packet.slippage_wad,
    n_eff_wad: packet.n_eff_wad,
    cr1_wad: packet.cr1_wad,
    oi_within_band_wad: packet.oi_within_band_wad,
    observed_at: packet.observed_at,
    max_staleness: packet.max_staleness,
    signature,
    signer: wallet.address,
    domain: {
      name: domain.name,
      version: domain.version,
      chain_id: domain.chainId,
      verifying_contract: domain.verifyingContract,
    },
  };
}

function typedDigest(cfg, packet) {
  const { TypedDataEncoder } = require('ethers');
  return TypedDataEncoder.hash(domainOf(cfg), TYPES, messageFrom(packet));
}

const METRIC_TYPES = [
  'uint32', 'uint16', 'uint64', 'uint64', 'uint64',
  'uint32', 'uint32', 'uint32', 'uint128', 'uint128', 'uint32',
];

function fit(n, bits) {
  if (typeof n !== 'bigint' || n < 0n) return null;
  if (n >= (1n << BigInt(bits))) return null;
  return n;
}

/**
 * On-chain Metric, or null when any signed field was not read.
 * Zero is kept when the caller measured zero. Null is not coerced.
 */
function metricTuple(m) {
  if (!m) return null;
  const values = [
    fit(m.asset, 32), fit(m.dex, 16), fit(m.l1Block, 64), fit(m.ts, 64), fit(m.seq, 64),
    fit(m.sAdjBps, 32), fit(m.neffE2, 32), fit(m.cr1Bps, 32),
    fit(m.ld2UsdE6, 128), fit(m.tsar95UsdE6, 128), fit(m.flags, 32),
  ];
  if (values.some((v) => v == null)) return null;
  return values;
}

function metricLeaf(m) {
  const values = metricTuple(m);
  if (!values) return null;
  const { AbiCoder, keccak256 } = require('ethers');
  const encoded = AbiCoder.defaultAbiCoder().encode(METRIC_TYPES, values);
  return keccak256(encoded);
}

function hashPair(a, b) {
  const { keccak256, concat } = require('ethers');
  const x = a.toLowerCase();
  const y = b.toLowerCase();
  const [lo, hi] = x < y ? [x, y] : [y, x];
  return keccak256(concat([lo, hi]));
}

function merkle(leaves) {
  if (!Array.isArray(leaves) || leaves.length === 0) return { root: null, proofs: [] };
  const layers = [leaves.slice()];
  while (layers[layers.length - 1].length > 1) {
    const prev = layers[layers.length - 1];
    const padded = prev.length % 2 === 1 ? prev.concat([prev[prev.length - 1]]) : prev;
    const next = [];
    for (let i = 0; i < padded.length; i += 2) next.push(hashPair(padded[i], padded[i + 1]));
    layers.push(next);
  }
  const proofs = leaves.map((_, leafIndex) => {
    const proof = [];
    let index = leafIndex;
    for (let layer = 0; layer < layers.length - 1; layer += 1) {
      const level = layers[layer];
      const pair = index % 2 === 0 ? index + 1 : index - 1;
      proof.push(pair < level.length ? level[pair] : level[index]);
      index = Math.floor(index / 2);
    }
    return proof;
  });
  return { root: layers[layers.length - 1][0], proofs };
}

async function signEpoch(cfg, fields) {
  const { Wallet } = require('ethers');
  const wallet = new Wallet(cfg.key);
  const message = {
    epoch: BigInt(fields.epoch),
    root: fields.root,
    l1Block: BigInt(fields.l1Block),
    scheme: SCHEME_SECP256K1,
  };
  const signature = await wallet.signTypedData(domainOf(cfg), EPOCH_TYPES, message);
  return {
    signature,
    signer: wallet.address,
    scheme: 'SECP256K1_EIP712',
    scheme_id: SCHEME_SECP256K1,
    domain: {
      name: DOMAIN_NAME,
      version: DOMAIN_VERSION,
      chain_id: cfg.chainId,
      verifying_contract: cfg.verifier,
    },
  };
}

function epochDigest(cfg, fields) {
  const { TypedDataEncoder } = require('ethers');
  return TypedDataEncoder.hash(domainOf(cfg), EPOCH_TYPES, {
    epoch: BigInt(fields.epoch),
    root: fields.root,
    l1Block: BigInt(fields.l1Block),
    scheme: SCHEME_SECP256K1,
  });
}

module.exports = {
  DOMAIN_NAME, DOMAIN_VERSION, TYPES, EPOCH_TYPES, SCHEME_SECP256K1,
  signerConfig, signingLabel, signPacket, typedDigest,
  metricTuple, metricLeaf, hashPair, merkle, signEpoch, epochDigest,
};
