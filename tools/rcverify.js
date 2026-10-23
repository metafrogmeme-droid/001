#!/usr/bin/env node
'use strict';
/**
 * rcverify — check a RUNECLAW proof bundle (rcproof/1) without trusting
 * RUNECLAW.
 *
 *   node tools/rcverify.js bundle.json            # every check, chains included
 *   node tools/rcverify.js bundle.json --offline  # the hash and the path only
 *   node tools/rcverify.js bundle.json --json     # one JSON line
 *
 * Get a bundle from GET /api/call/<key>/bundle. Node 18 or later; no
 * dependencies, and NOTHING SHARED WITH THE SERVER: this file recomputes
 * every hash itself, carries its own copy of the Merkle construction
 * (docs/PROVABLE_CALLS_SPEC.md §4), and asks each chain through public
 * endpoints it names itself. A bundle's own labels (which group a chain is
 * in, which endpoint to ask, which contract is EAS) are never trusted.
 *
 * WHAT IS CHECKED
 *   hash     sha256 over the UTF-8 bytes of seal_payload equals the seal
 *            (skipped while the payload is still sealed)
 *   path     the Merkle path from the seal reaches the day's root
 *   day      the seal was minted on the root's UTC day
 *   anchors  each recorded transaction, read from its chain, carries this
 *            root in the form its method names
 *   quorum   how many independent chains this verifier itself verified
 *   bitcoin  the OpenTimestamps statement and digest are this day's root;
 *            the proof against Bitcoin's headers is `ots verify`'s job, and
 *            --save-ots writes the file for it
 *
 * Exit status: 0 nothing failed and nothing was left unknown; 1 a check
 * FAILED; 3 nothing failed but something could not be checked. A chain that
 * could not be read is unknown, never a pass and never a fail.
 */
const crypto = require('crypto');
const fs = require('fs');
const path = require('path');

const PREFIX = 'RCROOT1';
const sha256hex = (s) => crypto.createHash('sha256').update(String(s), 'utf8').digest('hex');
const HEX64 = /^[0-9a-f]{64}$/;
const DAY_RE = /^\d{4}-\d{2}-\d{2}$/;

/**
 * This verifier's own view of the chains: where to ask, and what each is
 * independent of. Chosen here, not read from the bundle.
 */
const CHAINS = Object.freeze({
  base: { method: 'calldata', rpc: 'https://mainnet.base.org', group: 'ethereum-l2' },
  'base-eas': { method: 'eas_timestamp', rpc: 'https://mainnet.base.org', group: 'ethereum-l2',
    eas: '0x4200000000000000000000000000000000000021' },
  // EAS on other Ethereum L2s (the eas-contracts README): the same group as
  // Base, since each settles to Ethereum.
  'arbitrum-eas': { method: 'eas_timestamp', rpc: 'https://arb1.arbitrum.io/rpc', group: 'ethereum-l2',
    eas: '0xbD75f629A22Dc1ceD33dDA0b68c546A1c035c458' },
  'scroll-eas': { method: 'eas_timestamp', rpc: 'https://rpc.scroll.io', group: 'ethereum-l2',
    eas: '0xC47300428b6AD2c7D03BB76D05A176058b47E6B0' },
  'linea-eas': { method: 'eas_timestamp', rpc: 'https://rpc.linea.build', group: 'ethereum-l2',
    eas: '0xaEF4103A04090071165F78D45D83A0C0782c2B2a' },
  // A testnet is checked and never counted: it can be reset.
  'dogeos-testnet': { method: 'calldata', rpc: 'https://rpc.testnet.dogeos.com/', group: null },
  dogecoin: { method: 'dogecoin_op_return', api: 'https://api.blockcypher.com/v1/doge/main/txs/', group: 'dogecoin', depth: 6 },
  solana: { method: 'solana_memo', rpc: 'https://api.mainnet-beta.solana.com', group: 'solana',
    memo: ['MemoSq4gqABAXKb96qnH8TysNcWxMyWCqXgDLGmfcHr', 'Memo1UhkJRfHyvLMcVucJwxXeuD728EqVDDwQDxFMNo'] },
  // Avalanche C-Chain: the same calldata self-send as Base, its own consensus,
  // so its own group. This verifier does not read the server's
  // AVALANCHE_ANCHOR_ENABLED flag: a check that waited on the publisher's
  // switch would be trusting the publisher. The endpoint is Avalanche's own
  // public C-Chain RPC (chain id 0xa86a, read 23 Oct 2026).
  avalanche: { method: 'calldata', rpc: 'https://api.avax.network/ext/bc/C/rpc', group: 'avalanche' },
});

/** The spec's construction: replay a path of { pos: 'L'|'R', hash } over hex strings. */
function replay(seal, proof) {
  let h = String(seal);
  for (const step of proof || []) {
    if (!step || !HEX64.test(String(step.hash)) || (step.pos !== 'L' && step.pos !== 'R')) return null;
    h = step.pos === 'L' ? sha256hex(step.hash + h) : sha256hex(h + step.hash);
  }
  return h;
}

const utf8hex = (s) => Buffer.from(s, 'utf8').toString('hex');

async function rpc(fetchImpl, url, method, params) {
  const r = await fetchImpl(url, { method: 'POST', headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ jsonrpc: '2.0', id: 1, method, params }) });
  const j = await r.json();
  if (!r.ok || !j || j.error || !('result' in j)) throw new Error(`rpc ${method} answered ${r.status}`);
  return j.result;
}

/** One anchor, read from its chain: { status: 'pass'|'fail'|'unknown', detail }. */
async function checkAnchor(a, day, root, fetchImpl) {
  const c = CHAINS[a && a.chain];
  if (!c) return { status: 'unknown', detail: `a chain this verifier does not know (${a && a.chain})` };
  const tx = String(a.tx || '');
  try {
    if (c.method === 'calldata') {
      const t = await rpc(fetchImpl, c.rpc, 'eth_getTransactionByHash', [tx]);
      if (!t) return { status: 'unknown', detail: 'no such transaction yet at this endpoint' };
      if (!t.blockHash) return { status: 'unknown', detail: 'not yet mined' };
      if (String(t.input || '').toLowerCase() !== '0x' + utf8hex(`${PREFIX}:${day}:${root}`)) {
        return { status: 'fail', detail: 'the calldata is not this day\'s root' };
      }
      const rc = await rpc(fetchImpl, c.rpc, 'eth_getTransactionReceipt', [tx]);
      if (!rc) return { status: 'unknown', detail: 'receipt not readable yet' };
      if (rc.status !== '0x1') return { status: 'fail', detail: 'the transaction failed' };
      return { status: 'pass', detail: `calldata carries the root (block ${parseInt(t.blockNumber, 16)})` };
    }
    if (c.method === 'eas_timestamp') {
      // getTimestamp(bytes32) on the EAS contract this verifier knows, never the bundle's.
      const data = '0xd45c4435' + root;
      const out = await rpc(fetchImpl, c.rpc, 'eth_call', [{ to: c.eas, data }, 'latest']);
      const t = /^0x[0-9a-f]+$/i.test(String(out)) ? parseInt(String(out).slice(-16), 16) : NaN;
      if (!Number.isFinite(t)) return { status: 'unknown', detail: 'EAS answered something unreadable' };
      if (t === 0) return { status: 'fail', detail: 'EAS holds no stamp of this root' };
      return { status: 'pass', detail: `stamped in EAS at ${new Date(t * 1000).toISOString()}` };
    }
    if (c.method === 'dogecoin_op_return') {
      const r = await fetchImpl(c.api + tx.toLowerCase(), { headers: { accept: 'application/json' } });
      if (r.status === 404) return { status: 'fail', detail: 'no such transaction on Dogecoin' };
      const j = await r.json();
      if (!r.ok || !j || !Array.isArray(j.outputs)) return { status: 'unknown', detail: `explorer answered ${r.status}` };
      const want = utf8hex(`RCD1:${day}:`) + root;
      const data = j.outputs.filter((o) => o && o.script_type === 'null-data').map((o) => String(o.data_hex || '').toLowerCase());
      if (data.length !== 1 || data[0] !== want) return { status: 'fail', detail: 'the OP_RETURN is not this day\'s root' };
      if (!(Number(j.confirmations) >= c.depth)) return { status: 'unknown', detail: `${Number(j.confirmations) || 0} of ${c.depth} confirmations` };
      return { status: 'pass', detail: `OP_RETURN carries the root (${j.confirmations} confirmations)` };
    }
    if (c.method === 'solana_memo') {
      const t = await rpc(fetchImpl, c.rpc, 'getTransaction', [tx, { encoding: 'jsonParsed', commitment: 'finalized', maxSupportedTransactionVersion: 0 }]);
      if (!t) return { status: 'unknown', detail: 'not finalized (or not seen) at this endpoint' };
      if (!t.transaction || !Array.isArray(t.transaction.signatures) || t.transaction.signatures[0] !== tx) {
        return { status: 'unknown', detail: 'the endpoint answered another transaction' };
      }
      if (t.meta && t.meta.err != null) return { status: 'fail', detail: 'the transaction failed' };
      const memos = ((t.transaction.message && t.transaction.message.instructions) || [])
        .filter((ix) => ix && c.memo.includes(String(ix.programId))).map((ix) => ix.parsed);
      if (memos.length !== 1 || memos[0] !== `${PREFIX}:${day}:${root}`) return { status: 'fail', detail: 'the memo is not this day\'s root' };
      return { status: 'pass', detail: `memo carries the root (slot ${t.slot}, finalized)` };
    }
    return { status: 'unknown', detail: `a method this verifier does not check (${c.method})` };
  } catch (e) {
    return { status: 'unknown', detail: `${a.chain} could not be read (${(e && e.name) || 'Error'})` };
  }
}

/**
 * Every check on one bundle → { checks: [{ name, status, detail }], exit }.
 * status: 'pass' | 'fail' | 'unknown' | 'skip' | 'info'.
 */
async function verifyBundle(bundle, { offline = false, fetchImpl = global.fetch } = {}) {
  const checks = [];
  const add = (name, status, detail) => checks.push({ name, status, detail });
  const b = bundle || {};
  if (b.format !== 'rcproof/1') {
    add('format', 'unknown', `not an rcproof/1 bundle (${b.format === undefined ? 'no format' : String(b.format)})`);
    return { checks, exit: 3 };
  }
  const seal = String(b.seal || '');
  if (!HEX64.test(seal)) { add('seal', 'fail', 'the seal is not 64 hex characters'); return { checks, exit: 1 }; }

  if (typeof b.seal_payload === 'string') {
    add('hash', sha256hex(b.seal_payload) === seal ? 'pass' : 'fail',
      sha256hex(b.seal_payload) === seal ? 'sha256(seal_payload) equals the seal' : 'sha256(seal_payload) is NOT the seal: the receipt was altered');
  } else {
    add('hash', 'skip', 'the payload is still sealed: nothing to hash until it is revealed');
  }

  const m = b.merkle;
  if (!m) {
    add('path', 'skip', 'no Merkle path in the bundle (the day may still be open)');
    return finish(checks);
  }
  const day = String(m.day || '');
  const root = String(m.root || '').toLowerCase();
  if (!DAY_RE.test(day) || !HEX64.test(root)) { add('path', 'fail', 'the bundle\'s day or root is malformed'); return finish(checks); }
  const reached = replay(seal, m.proof);
  add('path', reached === root ? 'pass' : 'fail', reached === root ? `the path reaches the ${day} root` : 'the path does NOT reach the root');
  const minted = b.sealed_at ? new Date(b.sealed_at) : null;
  if (minted && Number.isFinite(minted.getTime())) {
    const d = minted.toISOString().slice(0, 10);
    add('day', d === day ? 'pass' : 'fail', d === day ? 'sealed on the root\'s UTC day' : `sealed on ${d}, not on the root's day ${day}`);
  }

  const groups = new Set();
  for (const a of Array.isArray(b.anchors) ? b.anchors : []) {
    if (offline) { add(`anchor ${a.chain}`, 'skip', 'offline: not asked'); continue; }
    const r = await checkAnchor(a, day, root, fetchImpl);
    add(`anchor ${a.chain}`, r.status, r.detail);
    const c = CHAINS[a.chain];
    if (r.status === 'pass' && c && c.group) groups.add(c.group);
  }
  if (!offline) {
    add('quorum', 'info', groups.size >= 2
      ? `multi-anchored: ${groups.size} independent chains verified here (${[...groups].sort().join(', ')}); this proves when, never how good`
      : `${groups.size} independent chain${groups.size === 1 ? '' : 's'} verified here`);
  }

  if (b.bitcoin) {
    const statement = `${PREFIX}:${day}:${root}`;
    const ok = b.bitcoin.statement === statement && b.bitcoin.digest === sha256hex(statement);
    add('bitcoin', ok ? 'info' : 'fail', ok
      ? 'the OpenTimestamps statement is this day\'s root; check the proof against Bitcoin with `ots verify` (--save-ots writes it)'
      : 'the Bitcoin statement or digest is not this day\'s root');
  } else if (b.bitcoin_unread) {
    add('bitcoin', 'unknown', 'the server could not read the day\'s Bitcoin proof when it made this bundle');
  }
  return finish(checks);
}

function finish(checks) {
  const exit = checks.some((c) => c.status === 'fail') ? 1 : checks.some((c) => c.status === 'unknown') ? 3 : 0;
  return { checks, exit };
}

async function main(argv = process.argv.slice(2), log = console.log, fetchImpl = global.fetch) {
  const offline = argv.includes('--offline');
  const json = argv.includes('--json');
  const saveOts = argv.includes('--save-ots');
  const file = argv.find((a) => !a.startsWith('--'));
  if (!file) { log('usage: node tools/rcverify.js <bundle.json | https://…/bundle> [--offline] [--json] [--save-ots]'); return 3; }
  let bundle;
  try {
    bundle = /^https:\/\//.test(file)
      ? await (await fetchImpl(file, { headers: { accept: 'application/json' } })).json()
      : JSON.parse(fs.readFileSync(file, 'utf8'));
  } catch (e) {
    log(`the bundle could not be read (${(e && e.name) || 'Error'})`);
    return 3;
  }
  const out = await verifyBundle(bundle, { offline, fetchImpl });
  // The file name is built from the bundle's day, so only a well-formed day
  // names a file: a crafted bundle must not write outside the bundle's folder.
  // The statement written beside it is this verifier's own, never the bundle's text.
  const m = bundle && bundle.merkle;
  const otsDay = m && DAY_RE.test(String(m.day)) && HEX64.test(String(m.root).toLowerCase()) ? String(m.day) : null;
  if (saveOts && otsDay && bundle.bitcoin && bundle.bitcoin.ots_proof_base64 && !/^https:\/\//.test(file)) {
    const target = path.join(path.dirname(file), `${PREFIX}-${otsDay}.txt.ots`);
    fs.writeFileSync(target, Buffer.from(String(bundle.bitcoin.ots_proof_base64), 'base64'));
    fs.writeFileSync(target.replace(/\.ots$/, ''), `${PREFIX}:${otsDay}:${String(m.root).toLowerCase()}`);
    out.checks.push({ name: 'bitcoin', status: 'info', detail: `wrote ${target} and its statement: run \`ots verify ${path.basename(target)}\`` });
  }
  if (json) { log(JSON.stringify(out)); return out.exit; }
  // A name or a detail can carry the bundle's own text: no control characters reach the terminal.
  const shown = (s) => String(s).replace(/[\u0000-\u001f\u007f-\u009f]/g, '?');
  for (const c of out.checks) log(`${c.status.toUpperCase().padEnd(7)} ${shown(c.name).padEnd(22)} ${shown(c.detail)}`);
  log(out.exit === 0 ? 'every check that ran passed' : out.exit === 1 ? 'A CHECK FAILED' : 'nothing failed, but something could not be checked');
  return out.exit;
}

if (require.main === module) {
  main().then((code) => { process.exitCode = code; }, (e) => { console.log(`rcverify could not run (${(e && e.name) || 'Error'})`); process.exitCode = 3; });
}

module.exports = { verifyBundle, checkAnchor, replay, main, CHAINS };
