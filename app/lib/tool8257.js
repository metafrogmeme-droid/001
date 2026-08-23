'use strict';
/**
 * ERC-8257 Agent Tool Registry — RUNECLAW's tool manifest + registration plan.
 *
 * ERC-8257 (draft, authored by OpenSea) is a permissionless on-chain registry
 * where AI agents discover tools: each record binds a creator address, a
 * metadata URI and a manifest hash, with an optional access-predicate
 * contract. It layers on ERC-8004 (agent identity) — which RUNECLAW anchors
 * on Base — so registering here makes our verifiable intelligence
 * discoverable by other agents on the same chain as our identity root.
 *
 * Hard lines, enforced structurally (tests grep this file):
 * - NON-CUSTODIAL: this module builds a DRY-RUN registration plan (calldata
 *   included). The OPERATOR signs it from their own wallet — the server never
 *   holds a key, never signs, never broadcasts.
 * - FREE + OPEN: the manifest carries NO `pricing` block and NO `access`
 *   predicate. Per-call charging is x402 payment machinery, which stays
 *   design-only behind the four independent gates in docs/INTEROP.md §4.
 * - READ-ONLY: the tool endpoint dispatches ONLY to the public MCP tool set —
 *   the same read-only handlers the /mcp server exposes. No accounts, no
 *   orders, no funds.
 *
 * Manifest contract (mirrors @opensea/tool-sdk v0.28, inspected 2026-07):
 * - schema: { type, name, description, version, endpoint, tags, inputs,
 *   outputs, creatorAddress, [verifiability] } — inputs/outputs are JSON
 *   Schema objects.
 * - served at /.well-known/ai-tool/<slug>.json
 * - manifestHash = keccak256(utf8(JCS(manifest))) — RFC 8785 canonical JSON.
 * - registerTool(string metadataURI, bytes32 manifestHash, address
 *   accessPredicate) on the canonical ToolRegistry.
 *
 * NO `ethers` HERE, DELIBERATELY. Both values a wallet acts on — the hash and
 * the calldata — are built from `./keccak256` and `./abi_call`, which have no
 * dependencies at all. This module used to call `ethers.keccak256` and
 * `ethers.Interface`, and in production the host resolves `ethers` to a stub:
 * the hash came back as SHA-256 and the calldata came back as the string
 * `'0x'`. Both are well-formed, both are wrong, and both were about to be
 * written to Base permanently. See the header of `./keccak256` for the full
 * post-mortem. `app/test/ethers_stub_calldata.test.js` reproduces the stub and
 * fails if either path finds its way back to a substitutable module.
 */

const keccak = require('./keccak256');
const { encodeCall } = require('./abi_call');

// Canonical ToolRegistry deployment (same address cross-chain; Base 8453 is
// where our ERC-8004 identity root lives, Ethereum 1 is the promotion path).
const TOOL_REGISTRY_ADDRESS = '0x265BB2DBFC0A8165C9A1941Eb1372F349baD2cf1';
const REGISTRY_CHAINS = { 8453: 'Base', 1: 'Ethereum' };
const ZERO_ADDRESS = '0x' + '00'.repeat(20);
const MANIFEST_TYPE = 'https://ercs.ethereum.org/ERCS/erc-8257#tool-manifest-v1';
const TOOL_SLUG = 'runeclaw-intel';

const REGISTER_SIG = 'registerTool(string,bytes32,address)';

/**
 * RFC 8785 (JCS) canonicalization for the manifest we author: recursively
 * sorted object keys, no insignificant whitespace, UTF-8. JSON.stringify's
 * number serialization is the ECMAScript algorithm JCS specifies, and we
 * refuse non-finite numbers, so this reproduces the SDK's `canonicalize`
 * output byte-for-byte for this data.
 */
function jcs(value) {
  if (value === null || typeof value === 'boolean' || typeof value === 'string') {
    return JSON.stringify(value);
  }
  if (typeof value === 'number') {
    if (!isFinite(value)) throw new Error('non-finite number in manifest');
    return JSON.stringify(value);
  }
  if (Array.isArray(value)) return '[' + value.map(jcs).join(',') + ']';
  if (typeof value === 'object') {
    const keys = Object.keys(value).filter(k => value[k] !== undefined).sort();
    return '{' + keys.map(k => JSON.stringify(k) + ':' + jcs(value[k])).join(',') + '}';
  }
  throw new Error(`unsupported manifest value type: ${typeof value}`);
}

function manifestHash(manifest) {
  return keccak.keccak256Utf8(jcs(manifest));
}

function baseUrl() {
  // Same single source as every other public URL. The metadataURI here is
  // hashed and registered ON-CHAIN, so an internal hostname would be baked into
  // a registration that can never resolve.
  return require('./public_origin').configured()
    .trim().replace(/\/+$/, '');
}

function creatorAddress() {
  const a = (process.env.TOOL_CREATOR_ADDRESS
    || process.env.PROOFOFPNL_AGENT_ADDRESS || '').trim().toLowerCase();
  return /^0x[0-9a-f]{40}$/.test(a) ? a : null;
}

/**
 * The manifest for RUNECLAW's single registered tool: a dispatcher over the
 * public read-only MCP tool set, so the on-chain record and /mcp can never
 * drift — both are generated from the same TOOLS registry at request time.
 */
function buildManifest({ tools }) {
  const base = baseUrl() || 'https://runeclaw.example';
  const creator = creatorAddress() || ZERO_ADDRESS;
  return {
    type: MANIFEST_TYPE,
    name: TOOL_SLUG,
    description: 'RUNECLAW read-only trading intelligence and agent-safety '
      + 'checks, in two families. INTELLIGENCE tools serve data the public '
      + 'site already publishes: a cryptographically verifiable track record '
      + '(Proof-of-PnL with re-derivable hashes), ERC-8004 agent identity '
      + 'cards, engine signals, tamper-evident flight records, token research '
      + 'and sector radars (RWA, meme, airdrops). SAFETY tools instead '
      + 'evaluate input the caller supplies — a pre-signature scan for '
      + 'prompt-injection and drain patterns, a plain-language mandate '
      + 'compiled into typed revocable rules, a hypothetical book run through '
      + 'stress scenarios, and a dependency-aware exit plan — storing nothing '
      + 'that is sent and reading no account. Which tool is in which family is '
      + 'machine-readable under `toolFamilies`. No tool sees an account, '
      + 'places an order or moves funds, and every safety answer is a '
      + 'heuristic read with reasons, never a verdict. Free and open: no '
      + 'pricing, no access gate. Past performance never predicts future '
      + 'results.',
    version: '1.0.0',
    endpoint: `${base}/api/tool/invoke`,
    tags: ['trading', 'crypto', 'verifiable-track-record', 'proof-of-pnl',
      'erc-8004', 'read-only', 'intelligence', 'agent-safety',
      'prompt-injection', 'risk'],
    // Derived from the registry, never hand-maintained: an agent can tell which
    // tools answer from published data and which evaluate what IT sends,
    // without parsing the prose above. This is what stops the manifest from
    // silently over-claiming again the next time a tool family is added.
    toolFamilies: {
      publishedData: Object.keys(tools).filter((n) => !tools[n].computesOnInput).sort(),
      callerInput: Object.keys(tools).filter((n) => tools[n].computesOnInput).sort(),
    },
    inputs: {
      type: 'object',
      properties: {
        tool: {
          type: 'string',
          enum: Object.keys(tools),
          description: 'Which read-only intelligence tool to invoke.',
        },
        args: {
          type: 'object',
          description: 'Arguments for the selected tool (see per-tool schema '
            + 'via MCP tools/list at the /mcp endpoint of the same host).',
        },
      },
      required: ['tool'],
    },
    outputs: {
      type: 'object',
      properties: {
        tool: { type: 'string' },
        result: { type: 'object', description: 'The selected tool\'s JSON result.' },
      },
    },
    creatorAddress: creator,
    verifiability: {
      tier: 'self-attested',
      execution: 'Express server backed by recorded trading history; the '
        + 'Proof-of-PnL statement and identity card it serves are '
        + 'independently re-derivable (documented canonicalization + hashes).',
      dataRetention: 'none',
      sourceVisibility: 'open-source',
      reproducibleBuild: {
        sourceCodeURI: 'https://github.com/Humanoid-Traders/RUNECLAW',
      },
    },
  };
}

/**
 * DRY RUN ONLY — the registerTool transaction the operator sends from their
 * own wallet. accessPredicate is the zero address: open access, no gate, no
 * charge. Never signs, never broadcasts.
 */
function buildRegistrationPlan({ tools }) {
  const manifest = buildManifest({ tools });
  const metadataURI = `${baseUrl() || 'https://runeclaw.example'}`
    + `/.well-known/ai-tool/${TOOL_SLUG}.json`;
  const creator = creatorAddress();

  // The hash and the calldata are the only two fields a wallet acts on, and
  // each is caught SEPARATELY. Collapsing them into one try would blank a
  // perfectly good hash because the encoder tripped — the composite-view case
  // in the CLAUDE.md table. Absent is never a measurement: a field that could
  // not be computed is missing from the payload, never a plausible-looking
  // placeholder, because a wrong 0x-string in `manifest_hash` reads exactly
  // like a right one.
  let canonical = null, hash = null, calldata = null;
  const buildFailures = [];
  try {
    canonical = jcs(manifest);
    hash = manifestHash(manifest);
  } catch (e) {
    buildFailures.push(`manifest hashing failed (${e.message}) — do NOT register`);
  }
  if (hash) {
    try {
      calldata = encodeCall(REGISTER_SIG, [metadataURI, hash, ZERO_ADDRESS]);
    } catch (e) {
      buildFailures.push(`calldata encoding failed (${e.message}) — the cast `
        + 'command below still works, but there is no calldata to copy');
    }
  }

  return {
    dry_run: true,
    ready: Boolean(creator && baseUrl() && hash && calldata),
    not_ready_reasons: [
      ...(creator ? [] : ['set TOOL_CREATOR_ADDRESS (or PROOFOFPNL_AGENT_ADDRESS)']),
      ...(baseUrl() ? [] : ['set APP_BASE_URL so metadataURI resolves publicly']),
      ...buildFailures,
    ],
    registry: TOOL_REGISTRY_ADDRESS,
    chains: Object.entries(REGISTRY_CHAINS)
      .map(([id, name]) => ({ chain_id: Number(id), name })),
    recommended_chain_id: 8453,
    metadata_uri: metadataURI,
    // null, never a placeholder, when hashing could not be done.
    manifest_hash: hash,
    hash_algorithm: 'keccak256 (Ethereum) over the UTF-8 bytes of RFC 8785 '
      + 'canonical JSON — NOT SHA3-256 (different padding) and NOT SHA-256.',
    // The canonical bytes themselves, so the hash above is CHECKABLE without
    // trusting this server. It is a re-serialization of the public manifest —
    // sorted keys, no whitespace — and the exact preimage. Publishing it is
    // the difference between "the server says the hash is X" and a claim the
    // operator can refute in one command, which is precisely what nobody could
    // do while a stubbed keccak256 was quietly serving SHA-256 here.
    manifest_canonical: canonical,
    // creatorAddress lives INSIDE the hashed manifest. While the env var is
    // unset it hashes as the zero address — so a registration sent from this
    // plan stops verifying the moment TOOL_CREATOR_ADDRESS is set. Say so
    // next to the hash, where the wallet-holder is actually reading.
    ...(creator ? {} : {
      hash_warning: 'creatorAddress is part of the hashed manifest and is '
        + 'currently the zero address. Setting TOOL_CREATOR_ADDRESS later '
        + 'will CHANGE manifest_hash and break verification of a '
        + 'registration sent with this calldata. Set the env var first, '
        + 'redeploy, then register from the refreshed plan.',
    }),
    access_predicate: ZERO_ADDRESS,
    access_note: 'zero address = open access. No NFT gate, no per-call charge '
      + '— pricing/x402 stays design-only behind the four gates in '
      + 'docs/INTEROP.md §4.',
    calldata,
    instructions: [
      ...(buildFailures.length ? [
        'DO NOT REGISTER. ' + buildFailures.join('; ') + '. A registration is '
          + 'permanent: an unverifiable hash cannot be corrected, only '
          + 'superseded by a second paid registration.',
      ] : []),
      ...(creator ? [] : [
        'Do not register this calldata yet: set TOOL_CREATOR_ADDRESS first '
          + '(see hash_warning), then use the refreshed plan.',
      ]),
      // Named FIRST among the do-this steps because it is the check that was
      // missing: the hash served here was SHA-256 for a while, it looked
      // exactly like a keccak256 hash, and nothing on this endpoint could have
      // told the operator apart from a correct one.
      ...(hash ? [
        'Verify the hash before you send it — a second implementation, not '
          + 'this one. Take manifest_canonical verbatim and keccak256 it: '
          + '`cast keccak "$(curl -s ' + `${baseUrl() || ''}` + '/api/tool/'
          + 'registration-plan | jq -r .manifest_canonical)"`, or in Python '
          + '`from Crypto.Hash import keccak; keccak.new(digest_bits=256, '
          + 'data=b).hexdigest()`. It must equal manifest_hash. If it does '
          + 'not, stop: something in the serving path is hashing with a '
          + 'different algorithm.',
      ] : []),
      ...(calldata ? [
        `Send from your own wallet (ideally the creator address ${creator || '<unset>'}) `
          + `to the ToolRegistry ${TOOL_REGISTRY_ADDRESS} on Base — value 0, `
          + 'data = the calldata field above.',
      ] : [
        'There is no calldata in this plan — do not send a transaction with an '
          + 'empty data field. It would succeed, cost gas, and register '
          + 'nothing.',
      ]),
      ...(hash ? [
        'Or with foundry: cast send ' + TOOL_REGISTRY_ADDRESS
          + ` "registerTool(string,bytes32,address)" "${metadataURI}" ${hash} `
          + `${ZERO_ADDRESS} --rpc-url https://mainnet.base.org `
          + '--private-key <YOUR_KEY_NEVER_SHARED_WITH_THE_SERVER>',
      ] : []),
      'The manifest served at the metadata URI must stay byte-identical for '
        + 'the on-chain hash to keep verifying — re-register (or '
        + 'update-metadata) after any manifest change. That includes '
        + 'APP_BASE_URL: the endpoint and metadataURI are inside the hashed '
        + 'bytes, so switching between the apex and www moves the hash.',
    ],
    non_custodial_note: 'The server never holds a key and never sends a '
      + 'transaction. This is a plan, not an action.',
  };
}

module.exports = {
  TOOL_REGISTRY_ADDRESS,
  ZERO_ADDRESS,
  MANIFEST_TYPE,
  TOOL_SLUG,
  jcs,
  manifestHash,
  buildManifest,
  buildRegistrationPlan,
};
