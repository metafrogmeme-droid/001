'use strict';
/**
 * One EVM reader for every chain in lib/rwa_chains.js.
 *
 * Read-only. eth_getBlockByNumber, eth_getCode, eth_getStorageAt, eth_call.
 * No signing method is ever sent. A revert is an answer: the next RPC URL is
 * not asked. An empty eth_call result is not a zero, and a reverted
 * isVerified is not "unverified".
 *
 * Selector scan is PUSH4 only (opcode 0x63 plus four bytes). A miss is
 * "not observed", which is not proof the function is absent. Code `0xef`
 * (a Base stock-token marker, no EVM body) is not scanned, and the view
 * calls are still made. The ERC-1967 implementation slot is preferred over
 * implementation(): that getter reverts on contracts whose slot is set.
 * A T-REX proxy stores neither. It exposes getImplementationAuthority(),
 * and that authority's getTokenImplementation() is the code to scan.
 *
 * A feed's answer is discarded. Only updatedAt, the heartbeat and a
 * classification of the description are kept. updatedAt 0 is "no update",
 * not age 0. A heartbeat that reverts leaves stale as null.
 */

const { Interface, id: keccakId } = require('ethers');
const { toneOf } = require('./rwa_words');

const IFACE = new Interface([
  'function name() view returns (string)',
  'function symbol() view returns (string)',
  'function version() view returns (string)',
  'function identityRegistry() view returns (address)',
  'function compliance() view returns (address)',
  'function paused() view returns (bool)',
  'function owner() view returns (address)',
  'function onchainID() view returns (address)',
  'function isFrozen(address) view returns (bool)',
  'function isVerified(address) view returns (bool)',
  'function isAgent(address) view returns (bool)',
  'function topicsRegistry() view returns (address)',
  'function issuersRegistry() view returns (address)',
  'function identityStorage() view returns (address)',
  'function getModules() view returns (address[])',
  'function latestRoundData() view returns (uint80,int256,uint256,uint256,uint80)',
  'function description() view returns (string)',
  'function heartbeat() view returns (uint256)',
  'function implementation() view returns (address)',
  'function getImplementationAuthority() view returns (address)',
  'function getTokenImplementation() view returns (address)',
  'function oracle() view returns (address)',
  'function priceOracle() view returns (address)',
  'function navOracle() view returns (address)',
  'function porFeed() view returns (address)',
  'function proofOfReserveFeed() view returns (address)',
  'function reserveFeed() view returns (address)',
  'function priceFeed() view returns (address)',
  'function navFeed() view returns (address)',
  'function feed() view returns (address)',
  'function aggregator() view returns (address)',
  'function chainlinkAggregator() view returns (address)',
]);

const ORACLE_GETTERS = [
  'oracle', 'priceOracle', 'navOracle', 'porFeed', 'proofOfReserveFeed',
  'reserveFeed', 'priceFeed', 'navFeed', 'feed', 'aggregator', 'chainlinkAggregator',
];

const SLOT_IMPL = '0x360894a13ba1a3210667c828492db98dca3e2076cc3735a920a3ca505d382bbc';
const SLOT_ADMIN = '0xb53127684a568b3173ae13b9f8a6016e243e63b6e8ee1178d6a717850b5d6103';
const SLOT_BEACON = '0xa3f0ad74e5423aebfd80d3ef4346578335a9a72aeaee59ff6cb3582b35133d50';

const ZERO = '0x' + '0'.repeat(40);
const PUSH20_CAP = 24;

const SELECTOR_OF = {
  forcedTransfer: keccakId('forcedTransfer(address,address,uint256)').slice(0, 10),
  setAddressFrozen: keccakId('setAddressFrozen(address,bool)').slice(0, 10),
  freezePartialTokens: keccakId('freezePartialTokens(address,uint256)').slice(0, 10),
  pause: keccakId('pause()').slice(0, 10),
  unpause: keccakId('unpause()').slice(0, 10),
  mint: keccakId('mint(address,uint256)').slice(0, 10),
  burn: keccakId('burn(address,uint256)').slice(0, 10),
  upgradeTo: keccakId('upgradeTo(address)').slice(0, 10),
  upgradeToAndCall: keccakId('upgradeToAndCall(address,bytes)').slice(0, 10),
  setImplementationAuthority: keccakId('setImplementationAuthority(address)').slice(0, 10),
  addAgent: keccakId('addAgent(address)').slice(0, 10),
  canTransfer: keccakId('canTransfer(address,address,uint256)').slice(0, 10),
};

const GROUPS = [
  { id: 'forced_transfer', keys: ['forcedTransfer'],
    observed: 'forcedTransfer was observed in the deployed bytecode. An agent can move balances without the holder signing.',
    missed: 'forcedTransfer was not observed in the deployed bytecode. That is not proof the function is absent.' },
  { id: 'freeze', keys: ['setAddressFrozen', 'freezePartialTokens'],
    observed: 'A freeze function was observed in the deployed bytecode. An agent can freeze an address or part of a balance.',
    missed: 'No freeze function was observed in the deployed bytecode. That is not proof a freeze is impossible.' },
  { id: 'pause_control', keys: ['pause', 'unpause'],
    observed: 'pause or unpause was observed in the deployed bytecode. An admin can halt transfers.',
    missed: 'pause was not observed in the deployed bytecode. That is not proof the contract cannot be halted.' },
  { id: 'mint_burn', keys: ['mint', 'burn'],
    observed: 'mint or burn was observed in the deployed bytecode. Supply can be changed by whoever may call it.',
    missed: 'mint and burn were not observed in the deployed bytecode. That is not proof supply is fixed.' },
  { id: 'agent_admin', keys: ['addAgent'],
    observed: 'addAgent was observed in the deployed bytecode. New agents can be appointed.',
    missed: 'addAgent was not observed in the deployed bytecode. That is not proof there is no agent role.' },
  { id: 'can_transfer', keys: ['canTransfer'],
    observed: 'canTransfer was observed. This pre-check does not call it: a refusal can mean the balance, not a compliance rule.',
    missed: 'canTransfer was not observed in the deployed bytecode. That is not proof transfers are unrestricted.' },
];

function isRevert(error) {
  if (!error || typeof error !== 'object') return false;
  if (error.code === 3) return true;
  if (/revert/i.test(String(error.message || ''))) return true;
  return false;
}

/**
 * JSON-RPC over one or more URLs. Failover only when the transport itself
 * fails. The URL is never logged and never put on the returned reason.
 */
function httpRpc(urls, fetchImpl, timeoutMs) {
  const f = fetchImpl || fetch;
  const budget = timeoutMs || 8000;
  const list = Array.isArray(urls) ? urls : [];
  return async function rpc(method, params) {
    for (let i = 0; i < list.length; i += 1) {
      const url = list[i];
      try {
        const res = await f(url, {
          method: 'POST',
          headers: { 'content-type': 'application/json' },
          body: JSON.stringify({ jsonrpc: '2.0', id: 1, method, params }),
          signal: AbortSignal.timeout(budget),
        });
        if (!res || res.ok !== true) continue;
        const body = await res.json();
        if (body && body.error) {
          if (isRevert(body.error)) return { ok: false, reason: 'revert' };
          continue;
        }
        if (!body || !Object.prototype.hasOwnProperty.call(body, 'result')) continue;
        return { ok: true, result: body.result };
      } catch (err) {
        // The message can contain the URL. It is not logged and not returned.
        continue;
      }
    }
    return { ok: false, reason: 'unreadable' };
  };
}

function hexQty(hex) {
  if (typeof hex !== 'string' || !/^0x[0-9a-fA-F]+$/.test(hex)) return null;
  try {
    const n = BigInt(hex);
    return n;
  } catch (err) {
    return null;
  }
}

function addressOf(word) {
  if (typeof word !== 'string') return null;
  const hex = word.toLowerCase().replace(/^0x/, '');
  if (hex.length !== 64 || !/^[0-9a-f]+$/.test(hex)) return null;
  return '0x' + hex.slice(24);
}

function checksummed(value) {
  if (typeof value !== 'string' || !/^0x[0-9a-fA-F]{40}$/.test(value)) return null;
  return value.toLowerCase();
}

function flag(id, state, text) {
  const tone = toneOf(id, state);
  if (!tone) {
    const err = new Error('untone');
    err.name = 'UntoneFlag';
    throw err;
  }
  return { id, state, tone, text };
}

function callData(fragment, args) {
  return IFACE.encodeFunctionData(fragment, args || []);
}

async function callView(rpc, to, fragment, args, tag) {
  let data;
  try {
    data = callData(fragment, args);
  } catch (err) {
    return { status: 'unreadable' };
  }
  const res = await rpc('eth_call', [{ to, data }, tag]);
  if (!res || res.ok !== true) {
    if (res && res.reason === 'revert') return { status: 'revert' };
    return { status: 'unreadable' };
  }
  if (typeof res.result !== 'string' || res.result === '0x' || res.result.length < 4) {
    return { status: 'empty' };
  }
  try {
    const decoded = IFACE.decodeFunctionResult(fragment, res.result);
    return { status: 'ok', value: decoded };
  } catch (err) {
    return { status: 'unreadable' };
  }
}

async function readCode(rpc, addr, tag) {
  const res = await rpc('eth_getCode', [addr, tag]);
  if (!res || res.ok !== true || typeof res.result !== 'string') return { status: 'unreadable' };
  const body = res.result.toLowerCase();
  if (!/^0x[0-9a-f]*$/.test(body) || (body.length % 2) !== 0) return { status: 'unreadable' };
  if (body === '0x' || body === '0x0') return { status: 'ok', form: 'empty', hex: '0x' };
  if (body === '0xef') return { status: 'ok', form: 'marker', hex: body };
  return { status: 'ok', form: 'evm', hex: body };
}

async function readSlot(rpc, addr, slot, tag) {
  const res = await rpc('eth_getStorageAt', [addr, slot, tag]);
  if (!res || res.ok !== true || typeof res.result !== 'string') return { status: 'unreadable' };
  const address = addressOf(res.result.toLowerCase());
  if (!address) return { status: 'unreadable' };
  return { status: 'ok', address };
}

/** PUSH4 immediates only. Metadata that merely contains the four bytes does not count. */
function push4s(hex) {
  const raw = hex.slice(2);
  if (!raw.length || raw.length % 2 !== 0) return new Set();
  const buf = Buffer.from(raw, 'hex');
  const found = new Set();
  for (let i = 0; i + 4 < buf.length; i += 1) {
    if (buf[i] !== 0x63) continue;
    found.add('0x' + buf.subarray(i + 1, i + 5).toString('hex'));
    i += 4;
  }
  return found;
}

function push20s(hex) {
  const raw = hex.slice(2);
  if (!raw.length || raw.length % 2 !== 0) return [];
  const buf = Buffer.from(raw, 'hex');
  const out = [];
  const seen = new Set();
  let more = false;
  for (let i = 0; i + 20 < buf.length; i += 1) {
    if (buf[i] !== 0x73) continue;
    const addr = '0x' + buf.subarray(i + 1, i + 21).toString('hex');
    i += 20;
    if (addr === ZERO) continue;
    if (seen.has(addr)) continue;
    seen.add(addr);
    if (out.length >= PUSH20_CAP) { more = true; break; }
    out.push(addr);
  }
  return { addresses: out, capped: more };
}

function minimalProxy(hex) {
  const raw = hex.slice(2);
  const prefix = '363d3d373d3d3d363d73';
  const suffix = '5af43d82803e903d91602b57fd5bf3';
  if (!raw.startsWith(prefix) || !raw.endsWith(suffix)) return null;
  const mid = raw.slice(prefix.length, raw.length - suffix.length);
  if (mid.length !== 40) return null;
  return '0x' + mid;
}

function feedKind(text) {
  const s = String(text).toLowerCase();
  if (s.includes('reserve') || /(^|[^a-z])por([^a-z]|$)/.test(s)) return 'por';
  if (s.includes('nav') || s.includes('net asset')) return 'nav';
  return 'other';
}

function ageOf(updatedAt, blockTime) {
  if (updatedAt === 0n) return { age_seconds: null, update: 'no_update' };
  if (updatedAt > blockTime) return { age_seconds: null, update: 'clock_skew' };
  const delta = blockTime - updatedAt;
  if (delta > BigInt(Number.MAX_SAFE_INTEGER)) return { age_seconds: null, update: 'unreadable' };
  return { age_seconds: Number(delta), update: 'read' };
}

function heartbeatOf(word) {
  if (typeof word !== 'bigint') return null;
  if (word > BigInt(Number.MAX_SAFE_INTEGER)) return null;
  return Number(word);
}

function staleOf(age, heartbeat) {
  if (typeof age !== 'number' || !Number.isFinite(age)) return null;
  if (typeof heartbeat !== 'number' || !Number.isFinite(heartbeat)) return null;
  return age > heartbeat;
}

async function readFeed(rpc, addr, tag, blockTime) {
  const round = await callView(rpc, addr, 'latestRoundData', [], tag);
  if (round.status !== 'ok' || !round.value) return { status: round.status === 'unreadable' ? 'unreadable' : 'no' };
  const updatedAt = round.value[3];
  if (typeof updatedAt !== 'bigint') return { status: 'unreadable' };
  const desc = await callView(rpc, addr, 'description', [], tag);
  const beat = await callView(rpc, addr, 'heartbeat', [], tag);
  let kind = 'unclassified';
  if (desc.status === 'ok' && typeof desc.value[0] === 'string') kind = feedKind(desc.value[0]);
  const heartbeat = beat.status === 'ok' ? heartbeatOf(beat.value[0]) : null;
  const age = ageOf(updatedAt, blockTime);
  return {
    status: 'feed',
    feed: addr,
    kind,
    heartbeat_seconds: heartbeat,
    age_seconds: age.age_seconds,
    update: age.update,
    stale: staleOf(age.age_seconds, heartbeat),
  };
}

function oracleText(feed, capped) {
  const kind = feed.kind === 'por' ? 'proof-of-reserve'
    : feed.kind === 'nav' ? 'NAV'
      : feed.kind === 'other' ? 'oracle'
        : 'oracle';
  const where = feed.source === 'self' ? 'This contract itself answers as a ' + kind + ' feed.'
    : feed.source === 'getter' ? 'This contract names a ' + kind + ' feed.'
      : 'Bytecode on this contract embeds an address that answers as a ' + kind + ' feed.';
  if (feed.update === 'no_update') {
    return where + ' The feed reports no update. Age was not treated as zero, and stale was not decided.';
  }
  if (feed.update === 'clock_skew') {
    return where + ' The update time is later than the block, so age was not computed and stale was not decided.';
  }
  if (feed.update !== 'read' || typeof feed.age_seconds !== 'number') {
    return where + ' The age of the last update could not be read, so stale was not decided.';
  }
  const age = ' The last update was ' + feed.age_seconds + ' seconds before this block.';
  if (feed.stale === true) return where + age + ' That is older than the heartbeat.';
  if (feed.stale === false) return where + age + ' That is within the heartbeat. The comparison does not say the data is right.';
  const cap = capped ? ' The bytecode scan stopped after ' + PUSH20_CAP + ' embedded addresses.' : '';
  return where + age + ' The heartbeat could not be read, so this pre-check does not call the feed stale or current.' + cap;
}

function noneText(form, capped) {
  if (form === 'marker') {
    return 'No oracle getter returned a feed, and this address has no EVM bytecode to scan. A feed published somewhere else is outside this pre-check.';
  }
  if (form === 'empty') {
    return 'No contract code and no oracle getter returned a feed.';
  }
  if (capped) {
    return 'No oracle getter returned a feed, and none of the first ' + PUSH20_CAP
      + ' addresses embedded in the bytecode answered as a proof-of-reserve or NAV feed.';
  }
  return 'No oracle getter returned a feed, and no proof-of-reserve or NAV feed was found in the bytecode.';
}

function setupField(view) {
  // A revert is not "the zero address", and an empty return is not a decode.
  // Only a 32-byte word of zeros is `unset`.
  if (!view) return { state: 'unreadable' };
  if (view.status === 'revert') return { state: 'revert' };
  if (view.status !== 'ok') return { state: 'unreadable' };
  const addr = checksummed(view.value[0]);
  if (!addr) return { state: 'unreadable' };
  if (addr === ZERO) return { state: 'unset' };
  return { state: 'set', address: addr };
}

function boolField(view) {
  if (!view || view.status !== 'ok' || typeof view.value[0] !== 'boolean') {
    if (view && (view.status === 'revert' || view.status === 'empty')) return { state: 'revert' };
    return { state: 'unreadable' };
  }
  return { state: 'read', value: view.value[0] };
}

async function pool(items, limit, fn) {
  const out = new Array(items.length);
  let next = 0;
  const n = Math.min(limit, items.length);
  async function worker() {
    while (next < items.length) {
      const i = next;
      next += 1;
      out[i] = await fn(items[i], i);
    }
  }
  if (n > 0) await Promise.all(Array.from({ length: n }, () => worker()));
  return out;
}

async function readEvm({ chain, asset, account, rpc }) {
  const blockRes = await rpc('eth_getBlockByNumber', ['latest', false]);
  if (!blockRes || blockRes.ok !== true || !blockRes.result) {
    return { read: false, reason: 'unreadable', chain: chainPublic(chain), asset, account: account || null };
  }
  const number = hexQty(blockRes.result.number);
  const timestamp = hexQty(blockRes.result.timestamp);
  if (number == null || timestamp == null) {
    return { read: false, reason: 'unreadable', chain: chainPublic(chain), asset, account: account || null };
  }
  const tag = '0x' + number.toString(16);
  const block = { number: number.toString(10), timestamp: timestamp.toString(10) };

  const [code, implSlot, adminSlot, beaconSlot] = await Promise.all([
    readCode(rpc, asset, tag),
    readSlot(rpc, asset, SLOT_IMPL, tag),
    readSlot(rpc, asset, SLOT_ADMIN, tag),
    readSlot(rpc, asset, SLOT_BEACON, tag),
  ]);

  const views = {};
  const viewNames = ['name', 'symbol', 'version', 'identityRegistry', 'compliance', 'paused',
    'owner', 'onchainID', 'implementation', 'getImplementationAuthority', 'latestRoundData',
    ...ORACLE_GETTERS];
  await pool(viewNames, 8, async (name) => {
    views[name] = await callView(rpc, asset, name, [], tag);
  });

  let implAddr = null;
  let implUnread = false;
  if (implSlot.status === 'ok' && implSlot.address !== ZERO) implAddr = implSlot.address;
  else if (code.status === 'ok' && code.form === 'evm') {
    const proxied = minimalProxy(code.hex);
    if (proxied) implAddr = proxied;
    else {
      const viaGetter = setupField(views.implementation);
      if (viaGetter.state === 'set') implAddr = viaGetter.address;
    }
  }
  // T-REX keeps the implementation on an authority. The ERC-1967 slot on
  // those proxies is zero and implementation() reverts. A revert here is
  // not "no implementation": only a decoded zero address is unset.
  if (!implAddr && code.status === 'ok' && code.form === 'evm') {
    const authority = setupField(views.getImplementationAuthority);
    if (authority.state === 'set') {
      const via = await callView(rpc, authority.address, 'getTokenImplementation', [], tag);
      const got = setupField(via);
      if (got.state === 'set') implAddr = got.address;
      else if (got.state !== 'unset') implUnread = true;
    }
  }
  let beaconImpl = null;
  if (beaconSlot.status === 'ok' && beaconSlot.address !== ZERO) {
    const beaconImplView = await callView(rpc, beaconSlot.address, 'implementation', [], tag);
    const got = setupField(beaconImplView);
    if (got.state === 'set') beaconImpl = got.address;
  }

  const extraCodes = [];
  if (implAddr) extraCodes.push(await readCode(rpc, implAddr, tag));
  else if (implUnread) extraCodes.push({ status: 'unreadable' });
  if (beaconImpl && beaconImpl !== implAddr) extraCodes.push(await readCode(rpc, beaconImpl, tag));

  const flags = [];
  const unreadable = [];
  const form = code.status === 'ok' ? code.form : 'unreadable';

  if (form === 'unreadable') {
    unreadable.push('code');
  } else if (form === 'empty') {
    flags.push(flag('contract', 'empty', 'No contract code was found at this address on this block.'));
  } else if (form === 'marker') {
    flags.push(flag('selectors', 'not_evm',
      'This address has no EVM bytecode (the code is a marker). View calls were still read. Functions were not reported as absent.'));
  }

  let selectors = null;
  let selectorGap = false;
  if (form === 'evm') {
    selectors = push4s(code.hex);
    for (const extra of extraCodes) {
      if (extra.status === 'ok' && extra.form === 'evm') {
        for (const s of push4s(extra.hex)) selectors.add(s);
      } else {
        // The implementation was not EVM bytecode we could scan. Hits already
        // seen stay; a miss must not be reported as "not observed".
        selectorGap = true;
      }
    }
  }

  const registry = setupField(views.identityRegistry);
  const compliance = setupField(views.compliance);
  const owner = setupField(views.owner);
  const onchain = setupField(views.onchainID);
  const paused = boolField(views.paused);

  if (registry.state === 'set') {
    flags.push(flag('erc3643', 'detected',
      'identityRegistry() returned an address. That is how this pre-check recognises an ERC-3643 / T-REX interface. It is not a certification.'));
    flags.push(flag('identity_registry', 'set', 'An identity registry is set.'));
  } else if (registry.state === 'unset') {
    flags.push(flag('erc3643', 'not_detected',
      'identityRegistry() returned the zero address. This pre-check did not recognise an ERC-3643 interface. A reverted call is not this result.'));
    flags.push(flag('identity_registry', 'unset', 'The identity registry call returned the zero address.'));
  } else if (registry.state === 'revert') {
    flags.push(flag('erc3643', 'not_observed',
      'identityRegistry() reverted. A revert is not a reading that this contract is outside ERC-3643.'));
    flags.push(flag('identity_registry', 'not_observed',
      'identityRegistry() reverted. That was not read as "no registry".'));
  } else {
    unreadable.push('erc3643', 'identity_registry');
  }

  if (compliance.state === 'set') flags.push(flag('compliance', 'set', 'A compliance contract is set.'));
  else if (compliance.state === 'unset') flags.push(flag('compliance', 'unset', 'The compliance call returned the zero address.'));
  else if (compliance.state === 'revert') flags.push(flag('compliance', 'not_observed', 'compliance() reverted. That was not read as "no compliance contract".'));
  else unreadable.push('compliance');

  if (owner.state === 'set') flags.push(flag('owner', 'set', 'An owner is set.'));
  else if (owner.state === 'unset') flags.push(flag('owner', 'unset', 'The owner call returned the zero address.'));
  else if (owner.state === 'revert') flags.push(flag('owner', 'not_observed', 'owner() reverted. That was not read as "no owner".'));
  else unreadable.push('owner');

  if (paused.state === 'read' && paused.value === true) {
    flags.push(flag('paused', 'yes', 'This contract reports that it is paused.'));
  } else if (paused.state === 'read' && paused.value === false) {
    flags.push(flag('paused', 'no', 'This contract reports that it is not paused.'));
  } else {
    unreadable.push('paused');
  }

  let modules = null;
  if (compliance.state === 'set') {
    const mods = await callView(rpc, compliance.address, 'getModules', [], tag);
    if (mods.status === 'ok' && Array.isArray(mods.value[0])) {
      modules = mods.value[0].length;
      if (modules > 0) {
        flags.push(flag('compliance_modules', 'set', 'The compliance contract returned ' + modules + ' modules.'));
      } else {
        flags.push(flag('compliance_modules', 'empty', 'The compliance contract returned no modules.'));
      }
    } else if (mods.status === 'revert' || mods.status === 'empty') {
      unreadable.push('compliance_modules');
    } else {
      unreadable.push('compliance_modules');
    }
  }

  if (registry.state === 'set') {
    const sub = await pool(
      [['identity_topics', 'topicsRegistry'], ['identity_issuers', 'issuersRegistry'], ['identity_storage', 'identityStorage']],
      3,
      async (pair) => {
        const view = await callView(rpc, registry.address, pair[1], [], tag);
        return [pair[0], setupField(view)];
      },
    );
    for (const [idName, field] of sub) {
      if (field.state === 'set') flags.push(flag(idName, 'set', 'The identity registry has ' + idName.replace('identity_', '') + ' set.'));
      else if (field.state === 'unset') flags.push(flag(idName, 'unset', 'The identity registry returned no address for ' + idName.replace('identity_', '') + '.'));
      else unreadable.push(idName);
    }
  }

  const upgradeSelector = selectors
    && (selectors.has(SELECTOR_OF.upgradeTo) || selectors.has(SELECTOR_OF.upgradeToAndCall)
      || selectors.has(SELECTOR_OF.setImplementationAuthority));
  const upgradeBySlot = implAddr != null || beaconImpl != null;
  if (upgradeBySlot || upgradeSelector) {
    flags.push(flag('upgradeable', 'yes',
      'This contract can be pointed at new code: a proxy slot, a minimal proxy, or an upgrade function was found.'));
  } else if (form === 'evm' && selectors && !selectorGap && implSlot.status === 'ok' && beaconSlot.status === 'ok') {
    flags.push(flag('upgradeable', 'not_observed',
      'No proxy slot and no upgrade function was observed. That is not proof the contract is immutable.'));
  } else if (form === 'empty' || form === 'marker') {
    // Nothing to scan. Don't claim "not upgradeable".
  } else {
    unreadable.push('upgradeable');
  }

  if (adminSlot.status === 'ok' && adminSlot.address !== ZERO) {
    flags.push(flag('proxy_admin', 'set', 'A proxy admin slot is set, so an admin can point this contract at new code.'));
  } else if (adminSlot.status !== 'ok' && form === 'evm') {
    unreadable.push('proxy_admin');
  }

  if (form === 'evm' && selectors) {
    for (const g of GROUPS) {
      const hit = g.keys.some((k) => selectors.has(SELECTOR_OF[k]));
      if (hit) flags.push(flag(g.id, 'observed', g.observed));
      else if (selectorGap) unreadable.push(g.id);
      else flags.push(flag(g.id, 'not_observed', g.missed));
    }
  } else if (form === 'evm') {
    for (const g of GROUPS) unreadable.push(g.id);
  }

  if (!account) {
    flags.push(flag('investor', 'not_checked',
      'No account was supplied, so isVerified, isFrozen and isAgent were not read. A transfer was not simulated.'));
  } else {
    const frozen = await callView(rpc, asset, 'isFrozen', [account], tag);
    const frozenField = boolField(frozen);
    if (frozenField.state === 'read' && frozenField.value === true) {
      flags.push(flag('investor_frozen', 'yes', 'This contract reports the account as frozen.'));
    } else if (frozenField.state === 'read') {
      flags.push(flag('investor_frozen', 'no', 'This contract reports the account as not frozen.'));
    } else {
      unreadable.push('investor_frozen');
    }
    const agent = await callView(rpc, asset, 'isAgent', [account], tag);
    const agentField = boolField(agent);
    if (agentField.state === 'read' && agentField.value === true) {
      flags.push(flag('investor_agent', 'yes', 'This account is an agent on this contract.'));
    } else if (agentField.state === 'read') {
      flags.push(flag('investor_agent', 'no', 'This account is not reported as an agent. That does not say the contract has no agents.'));
    } else {
      unreadable.push('investor_agent');
    }
    if (registry.state === 'set') {
      const verified = await callView(rpc, registry.address, 'isVerified', [account], tag);
      const verifiedField = boolField(verified);
      if (verifiedField.state === 'read' && verifiedField.value === true) {
        flags.push(flag('registry_isVerified', 'yes', 'The identity registry reports this account as verified.'));
      } else if (verifiedField.state === 'read' && verifiedField.value === false) {
        flags.push(flag('registry_isVerified', 'no',
          'The identity registry reports this account as not verified. A transfer can be refused for that reason.'));
      } else {
        // A revert is not "unverified".
        unreadable.push('registry_isVerified');
      }
    } else if (registry.state === 'unreadable') {
      unreadable.push('registry_isVerified');
    }
  }

  const oracle = await concludeOracle(rpc, asset, tag, timestamp, form, code, extraCodes, views);
  if (oracle.unreadable) unreadable.push('oracle');
  else if (oracle.flag) flags.push(oracle.flag);

  const token = {
    name: textOrNull(views.name),
    symbol: textOrNull(views.symbol),
    version: textOrNull(views.version),
  };

  return {
    read: true,
    chain: chainPublic(chain),
    block,
    asset,
    account: account || null,
    code: form === 'evm' ? 'present' : form,
    token,
    setup: {
      registry: publicSetup(registry),
      compliance: publicSetup(compliance),
      owner: publicSetup(owner),
      onchain_id: publicSetup(onchain),
      implementation: implAddr
        ? { state: 'set', address: implAddr }
        : (implUnread || implSlot.status !== 'ok')
          ? { state: 'unreadable' }
          : { state: 'unset' },
      modules,
    },
    flags,
    unreadable,
    oracle: oracle.public,
  };
}

function chainPublic(chain) {
  return { id: chain.id, chain_id: chain.chain_id, name: chain.name, family: chain.family };
}

function publicSetup(field) {
  if (field.state === 'set') return { state: 'set', address: field.address };
  if (field.state === 'unset') return { state: 'unset' };
  if (field.state === 'revert') return { state: 'revert' };
  return { state: 'unreadable' };
}

function textOrNull(view) {
  if (!view || view.status !== 'ok' || typeof view.value[0] !== 'string') return null;
  const s = view.value[0];
  if (!s) return null;
  return s.length > 80 ? s.slice(0, 80) : s;
}

async function concludeOracle(rpc, asset, tag, blockTime, form, code, extraCodes, views) {
  const self = await feedFromRound(views.latestRoundData, asset, rpc, tag, blockTime);
  if (self && self.status === 'feed') {
    self.source = 'self';
    return publishFeed(self, false);
  }

  let getterUnreadable = false;
  for (const name of ORACLE_GETTERS) {
    const view = views[name];
    if (!view || view.status === 'unreadable') { getterUnreadable = true; continue; }
    if (view.status !== 'ok') continue;
    const addr = checksummed(view.value[0]);
    if (!addr || addr === ZERO) continue;
    const found = await readFeed(rpc, addr, tag, blockTime);
    if (found.status === 'unreadable') { getterUnreadable = true; continue; }
    if (found.status === 'feed') {
      found.source = 'getter';
      return publishFeed(found, false);
    }
  }

  if (form !== 'evm' || code.status !== 'ok') {
    // No bytecode to scan. A transport failure, or code we never read, is not
    // "this contract names no feed".
    if (getterUnreadable || form === 'unreadable') return { unreadable: true, public: null, flag: null };
    return publishNone(form, false);
  }

  const blobs = [code.hex];
  let codeGap = false;
  for (const extra of extraCodes) {
    if (extra.status === 'ok' && extra.form === 'evm') blobs.push(extra.hex);
    else if (extra.status !== 'ok') codeGap = true;
  }
  const embedded = [];
  let capped = false;
  const seen = new Set();
  for (const hex of blobs) {
    const got = push20s(hex);
    if (got.capped) capped = true;
    for (const addr of got.addresses) {
      if (addr === asset || seen.has(addr)) continue;
      seen.add(addr);
      if (embedded.length >= PUSH20_CAP) { capped = true; break; }
      embedded.push(addr);
    }
  }
  for (const addr of embedded) {
    const found = await readFeed(rpc, addr, tag, blockTime);
    if (found.status === 'unreadable') { getterUnreadable = true; continue; }
    if (found.status === 'feed' && (found.kind === 'por' || found.kind === 'nav')) {
      found.source = 'bytecode';
      return publishFeed(found, capped);
    }
  }
  if (getterUnreadable || codeGap) return { unreadable: true, public: null, flag: null };
  return publishNone(form, capped);
}

async function feedFromRound(view, asset, rpc, tag, blockTime) {
  if (!view || view.status !== 'ok' || !view.value || typeof view.value[3] !== 'bigint') {
    return null;
  }
  return readFeed(rpc, asset, tag, blockTime);
}

function publishFeed(feed, capped) {
  const state = feed.stale === true ? 'stale' : feed.stale === false ? 'within_heartbeat' : 'unknown';
  const body = {
    referenced: true,
    kind: feed.kind,
    source: feed.source,
    feed: feed.feed,
    age_seconds: feed.age_seconds,
    heartbeat_seconds: feed.heartbeat_seconds,
    stale: feed.stale,
    update: feed.update,
  };
  return { unreadable: false, public: body, flag: flag('oracle', state, oracleText(feed, capped)) };
}

function publishNone(form, capped) {
  return {
    unreadable: false,
    public: { referenced: false },
    flag: flag('oracle', 'none', noneText(form, capped)),
  };
}

module.exports = {
  httpRpc, readEvm, SELECTOR_OF, SLOT_IMPL, PUSH20_CAP, isRevert,
};
