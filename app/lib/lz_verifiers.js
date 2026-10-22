'use strict';
/**
 * How many verifiers must agree before a LayerZero app takes a message: the
 * Risk Scanner's bridge check, read-only.
 *
 * A LayerZero V2 app (an OApp, an OFT) chooses who verifies its messages: a
 * set of DVNs, some required and some optional with a threshold. When one
 * verifier's word is enough, whoever runs or fools that one verifier decides
 * what the app receives. That is the failure behind the April 2026 KelpDAO
 * loss (about $292M), a 1-of-1 verifier fed by poisoned RPCs (reports/
 * Mainnet roadmap). This file reads the setting from the chain, the way
 * LayerZero's docs give it (docs.layerzero.network, "DVN & Executor
 * configuration", read 2026-10-22):
 *
 *   EndpointV2.getSendLibrary(oapp, eid)               -> the send library
 *   EndpointV2.getReceiveLibrary(oapp, eid)            -> (library, isDefault)
 *   EndpointV2.getConfig(oapp, library, eid, 2)        -> abi.encode(UlnConfig)
 *   UlnConfig (uint64 confirmations, uint8 requiredDVNCount,
 *     uint8 optionalDVNCount, uint8 optionalDVNThreshold,
 *     address[] requiredDVNs, address[] optionalDVNs); 255 is "nil"
 *
 * The answer is the app's effective setting, its own or the default it falls
 * back to. Each chain is read through two keyless readers that must give the
 * same answer (lib/reader_agreement.js). The endpoint, the two libraries and
 * the verifiers' names are LayerZero's own, snapshotted with a date in
 * lib/layerzero_v2.json. A library that is not the one the snapshot names is
 * not decoded: its configuration may mean something else.
 *
 * A COUNT, NEVER A VERDICT. "Three verifiers must agree" rules one failure
 * out; it does not say the three are independent, honest or well run. The
 * answer says how many and who, and that one alone is enough when it is.
 */

const { keccak256, toUtf8Bytes } = require('./keccak256');
const { agree, requiredOf, unread } = require('./reader_agreement');
const SNAPSHOT = require('./layerzero_v2.json');

const ULN_CONFIG_TYPE = 2;
const NIL = 255;
const ADDR_RE = /^0x[0-9a-fA-F]{40}$/;
/** Mainnet endpoint ids are 30000 and up; testnets are 40000 and up. */
const MAINNET_EID = (n) => Number.isInteger(n) && n >= 30000 && n < 40000;

/** This scanner's chains: where to read, and LayerZero's own addresses for each. */
function chains(env = process.env) {
  const list = (key, defaults) => {
    const raw = String(env[key] || '').trim();
    const urls = raw ? raw.split(',').map((s) => s.trim()).filter((s) => /^https:\/\//.test(s)) : [];
    return urls.length ? urls : defaults.slice();
  };
  const base = { ethereum: { name: 'Ethereum', env: 'LZ_ETHEREUM_RPC_URLS', rpcs: ['https://ethereum-rpc.publicnode.com', 'https://cloudflare-eth.com'] },
    base: { name: 'Base', env: 'LZ_BASE_RPC_URLS', rpcs: ['https://mainnet.base.org', 'https://base-rpc.publicnode.com'] },
    arbitrum: { name: 'Arbitrum One', env: 'LZ_ARBITRUM_RPC_URLS', rpcs: ['https://arb1.arbitrum.io/rpc', 'https://arbitrum-one-rpc.publicnode.com'] },
    optimism: { name: 'OP Mainnet', env: 'LZ_OPTIMISM_RPC_URLS', rpcs: ['https://mainnet.optimism.io', 'https://optimism-rpc.publicnode.com'] } };
  const out = {};
  for (const [key, c] of Object.entries(base)) {
    const s = SNAPSHOT.chains[key];
    out[key] = { key, name: c.name, eid: s.eid, chain_id: s.chain_id, endpoint: s.endpoint_v2,
      send_library: s.send_uln_302, receive_library: s.receive_uln_302, keyless: c.rpcs.slice(), rpcs: list(c.env, c.rpcs) };
  }
  return out;
}

const sel = (sig) => keccak256(toUtf8Bytes(sig)).slice(0, 10);
const SEL = {
  sendLibrary: sel('getSendLibrary(address,uint32)'),
  receiveLibrary: sel('getReceiveLibrary(address,uint32)'),
  config: sel('getConfig(address,address,uint32,uint32)'),
};
const word = (v) => String(v).replace(/^0x/, '').toLowerCase().padStart(64, '0');

/** The 32-byte words of an eth_call answer, or null when it is not whole words. */
function words(hex) {
  const h = String(hex || '').replace(/^0x/, '');
  if (!/^[0-9a-fA-F]*$/.test(h) || h.length === 0 || h.length % 64 !== 0) return null;
  const out = [];
  for (let i = 0; i < h.length; i += 64) out.push(h.slice(i, i + 64).toLowerCase());
  return out;
}

const asAddress = (w) => (/^0{24}[0-9a-f]{40}$/.test(w) ? '0x' + w.slice(24) : null);
const asSmall = (w, max) => {
  const n = BigInt('0x' + w);
  return n <= BigInt(max) ? Number(n) : null;
};

/** An address answer (getSendLibrary), or null. */
function decodeAddress(hex) {
  const w = words(hex);
  return w && w.length === 1 ? asAddress(w[0]) : null;
}

/** (address library, bool isDefault) (getReceiveLibrary), or null. */
function decodeReceiveLibrary(hex) {
  const w = words(hex);
  if (!w || w.length !== 2) return null;
  const lib = asAddress(w[0]);
  const flag = asSmall(w[1], 1);
  return lib && flag !== null ? { library: lib, is_default: flag === 1 } : null;
}

/**
 * getConfig's answer: `bytes` holding abi.encode(UlnConfig), whose head is
 * the struct's own offset (it has dynamic members). Null on anything that
 * does not decode exactly: a half-read config is not a config.
 */
function decodeUlnConfig(hex) {
  const w = words(hex);
  if (!w || w.length < 2) return null;
  const at = (bytes) => (bytes % 32 === 0 ? bytes / 32 : -1);
  const off = at(Number(BigInt('0x' + w[0])));
  if (off < 0 || off >= w.length) return null;
  const len = Number(BigInt('0x' + w[off]));
  if (len % 32 !== 0 || off + 1 + len / 32 > w.length) return null;
  const p = w.slice(off + 1, off + 1 + len / 32);
  const t = at(Number(BigInt('0x' + (p[0] || 'f'.repeat(64)))));
  if (t < 0 || t + 6 > p.length) return null;
  const s = p.slice(t);
  const confirmations = BigInt('0x' + s[0]);
  const [required, optional, threshold] = [s[1], s[2], s[3]].map((x) => asSmall(x, 255));
  if (confirmations > 0xffffffffffffffffn || [required, optional, threshold].includes(null)) return null;
  const list = (i) => {
    const o = at(Number(BigInt('0x' + s[i])));
    if (o < 0 || o >= s.length) return null;
    const n = Number(BigInt('0x' + s[o]));
    if (n > 255 || o + 1 + n > s.length) return null;
    const out = s.slice(o + 1, o + 1 + n).map(asAddress);
    return out.includes(null) ? null : out;
  };
  const requiredDVNs = list(4);
  const optionalDVNs = list(5);
  if (!requiredDVNs || !optionalDVNs) return null;
  return { confirmations: confirmations.toString(), requiredDVNCount: required, optionalDVNCount: optional,
    optionalDVNThreshold: threshold, requiredDVNs, optionalDVNs };
}

/** LayerZero's name for a verifier on `chain`, or null when its list does not name it. */
function verifierName(chain, address) {
  const names = (SNAPSHOT.chains[chain] || {}).dvns || {};
  return Object.prototype.hasOwnProperty.call(names, address) ? names[address] : null;
}

/**
 * One direction's effective setting, in plain terms: who must verify, how
 * many of the optional ones, and whether one verifier alone is enough.
 */
function describe(chain, cfg) {
  const required = cfg.requiredDVNCount === NIL ? 0 : cfg.requiredDVNCount;
  const threshold = cfg.optionalDVNThreshold === NIL ? 0 : cfg.optionalDVNThreshold;
  const needed = required + threshold;
  const who = (a) => ({ address: a, name: verifierName(chain, a) });
  return {
    confirmations: cfg.confirmations,
    required: cfg.requiredDVNs.map(who),
    optional: cfg.optionalDVNs.map(who),
    optional_threshold: threshold,
    verifiers_needed: needed,
    // Three facts, not two: none needed is not "one is enough", and a setting
    // read as needing none is reported as itself.
    single_verifier: needed === 1,
    no_verifier: needed === 0,
  };
}

/** One reader's view of both directions of `oapp`'s route to `eid`. Throws when the reader does not answer. */
async function readOnce(call, url, chain, oapp, eid) {
  const a = word(oapp);
  const e = word(eid.toString(16));
  const sendLib = decodeAddress(await call(url, chain.endpoint, SEL.sendLibrary + a + e));
  const recv = decodeReceiveLibrary(await call(url, chain.endpoint, SEL.receiveLibrary + a + e));
  if (!sendLib || !recv) return { status: 'unknown', reason: 'the endpoint answered something that does not decode' };
  const side = async (library, known) => {
    if (library !== known) return { library, library_known: false };
    const cfg = decodeUlnConfig(await call(url, chain.endpoint, SEL.config + a + word(library) + e + word(ULN_CONFIG_TYPE.toString(16))));
    if (!cfg) return { library, library_known: true, unreadable: true };
    return { library, library_known: true, ...describe(chain.key, cfg) };
  };
  const send = await side(sendLib, chain.send_library);
  const receive = { ...(await side(recv.library, chain.receive_library)), default_library: recv.is_default };
  if (send.unreadable || receive.unreadable) return { status: 'unknown', reason: 'a configuration did not decode' };
  const reading = { send, receive };
  return { status: 'verified', same: JSON.stringify(reading), reading };
}

function rpcCaller(fetchImpl) {
  return async (url, to, data) => {
    const r = await fetchImpl(url, { method: 'POST', headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ jsonrpc: '2.0', id: 1, method: 'eth_call', params: [{ to, data }, 'latest'] }) });
    if (!r.ok) throw new Error(`rpc http ${r.status}`);
    const j = await r.json();
    if (!j || j.error || typeof j.result !== 'string') throw new Error('rpc answered no result');
    return j.result;
  };
}

/** A reader's name: a keyless default by its host; a configured URL, which may carry a key, by its place. */
function readerName(chain, url, i) {
  if (chain.keyless.includes(url)) {
    try { return new URL(url).hostname; } catch (e) { /* by place */ }
  }
  return `configured rpc ${i + 1}`;
}

/**
 * The bridge check for `oapp` on `chainKey`, toward remote endpoint `eid`:
 *   { status: 'read', chain, oapp, remote_eid, send, receive, readers }
 *   { status: 'unknown', reason, disagreement? }   not read, or the readers disagree
 *   { status: 'refused', reason }                  the question is malformed
 */
async function bridgeCheck({ chain: chainKey, oapp, eid }, { fetchImpl = global.fetch, env = process.env } = {}) {
  const all = chains(env);
  const chain = Object.prototype.hasOwnProperty.call(all, String(chainKey)) ? all[String(chainKey)] : null;
  if (!chain) return { status: 'refused', reason: 'a chain this scanner does not read' };
  if (!ADDR_RE.test(String(oapp || ''))) return { status: 'refused', reason: 'the app address is not a 0x address' };
  const remote = Number(eid);
  if (!MAINNET_EID(remote)) return { status: 'refused', reason: 'the remote endpoint id is not a mainnet one (30000–39999)' };
  if (remote === chain.eid) return { status: 'refused', reason: 'the remote endpoint is this chain itself' };
  const app = String(oapp).toLowerCase();
  const call = rpcCaller(fetchImpl);
  const answers = await Promise.all(chain.rpcs.map(async (url, i) => {
    const reader = readerName(chain, url, i);
    try { return { reader, ...(await readOnce(call, url, chain, app, remote)) }; } catch (e) { return unread(reader, e); }
  }));
  const v = agree(answers, requiredOf(chain.rpcs.length), `the ${chain.name} readers`);
  if (v.status !== 'verified') return { status: 'unknown', reason: v.reason, ...(v.disagreement ? { disagreement: true } : {}) };
  return { status: 'read', chain: chain.key, chain_name: chain.name, oapp: app, remote_eid: remote,
    send: v.reading.send, receive: v.reading.receive, readers: v.readers, names_read: SNAPSHOT.read };
}

module.exports = { chains, bridgeCheck, decodeUlnConfig, decodeReceiveLibrary, decodeAddress, describe, verifierName, SEL, NIL };
