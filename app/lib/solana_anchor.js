'use strict';
/**
 * A day's seal root anchored on Solana (mainnet), by the owner's or a
 * player's own wallet: one Memo-program instruction carrying the root as
 * text, the same `RCROOT1:<day>:<root>` the EVM chains carry as calldata. A
 * memo is UTF-8 and takes far more than these 83 bytes, so no second format.
 *
 * NOTHING HERE SIGNS, FUNDS OR BROADCASTS. buildPlan hands out the memo and
 * the Solana CLI command; the wallet's owner runs it. verify() READS the
 * transaction from two keyless JSON-RPC readers at `finalized` commitment and
 * requires them to agree (lib/reader_agreement.js): on the memo and on the
 * slot it landed in. One reader that lies, lags or is down never turns a day
 * green or refuses a real anchor: the verdict is "unknown".
 *
 * WHY `finalized`. A Solana transaction confirmed by a supermajority can
 * still, in principle, be on a fork that loses; `finalized` is the block the
 * cluster has rooted, about thirteen seconds behind the head. Asked at that
 * commitment, a reader answers null until then, which reads as "not yet",
 * never as "no such transaction".
 *
 * A SIGNATURE IS CASE-SENSITIVE. Base58 tells 'a' from 'A'; the EVM routes
 * lower-case a hash, and this one must not be (routes/roots.js asks
 * `caseSensitiveTx`).
 */

const { agree, requiredOf, unread } = require('./reader_agreement');

const DAY_RE = /^\d{4}-\d{2}-\d{2}$/;
const ROOT_RE = /^[0-9a-f]{64}$/;
/** A transaction signature: 64 bytes in base58, 64 to 88 characters. */
const SIG_RE = /^[1-9A-HJ-NP-Za-km-z]{64,88}$/;
const PREFIX = 'RCROOT1';
/** The SPL Memo program, version 2, and the original version 1 a wallet may still use. */
const MEMO_PROGRAMS = Object.freeze(['MemoSq4gqABAXKb96qnH8TysNcWxMyWCqXgDLGmfcHr', 'Memo1UhkJRfHyvLMcVucJwxXeuD728EqVDDwQDxFMNo']);
/** Keyless JSON-RPC endpoints, read 2026-10-22: Solana's own and PublicNode's. */
const DEFAULT_RPCS = Object.freeze(['https://api.mainnet-beta.solana.com', 'https://solana-rpc.publicnode.com']);

/** The memo text for a day's root, or null for a malformed one. */
function memoFor(day, root) {
  if (!DAY_RE.test(String(day)) || !ROOT_RE.test(String(root))) return null;
  return `${PREFIX}:${day}:${root}`;
}

/** The plan for `chain` (the registry's Solana entry). DRY RUN ONLY. */
function buildPlan(day, root, chain) {
  const memo = memoFor(day, root);
  if (!memo) return null;
  return {
    dry_run: true, chain: chain.name, chain_key: chain.key, method: chain.method,
    value: '0', memo, memo_bytes: Buffer.byteLength(memo, 'utf8'), day, root,
    memo_program: MEMO_PROGRAMS[0],
    instructions: [
      'With the Solana CLI (Agave) and your own keypair, never shared with the server, send yourself one '
        + 'lamport with the memo. The lamport comes back to you; the network fee is the cost:',
      `solana transfer <YOUR_OWN_ADDRESS> 0.000000001 --with-memo "${memo}"`,
      'It prints the transaction signature. Any wallet that can add a memo to a transaction works the same way: '
        + 'the anchor is a successful transaction carrying exactly that memo.',
      'Then submit the signature via POST /api/roots/anchor '
        + `{ day, tx_hash: <signature>, chain: "${chain.key}" }. The server reads it back from two Solana readers `
        + 'and records it once the cluster has finalized it.',
    ],
    non_custodial_note: 'The server never holds a key and never sends a '
      + 'transaction. This is a plan, not an action.',
  };
}

/** A reader's printable name: the host of a keyless default, else its place (a configured URL may carry a key). */
function readerName(url, i) {
  if (DEFAULT_RPCS.includes(url)) {
    try { return new URL(url).hostname; } catch (e) { /* by place */ }
  }
  return `configured solana rpc ${i + 1}`;
}

/** The memo texts of a parsed transaction's top-level Memo instructions. */
function memosOf(tx) {
  const ixs = (tx && tx.transaction && tx.transaction.message && tx.transaction.message.instructions) || [];
  return ixs.filter((ix) => ix && MEMO_PROGRAMS.includes(String(ix.programId || '')))
    .map((ix) => (typeof ix.parsed === 'string' ? ix.parsed : null));
}

/** The fee payer: the first account key, as jsonParsed gives it ({ pubkey }) or as a string. */
function payerOf(tx) {
  const keys = (tx && tx.transaction && tx.transaction.message && tx.transaction.message.accountKeys) || [];
  const k = keys[0];
  return k == null ? '' : String(typeof k === 'string' ? k : k.pubkey || '');
}

/** One reader's verdict on one getTransaction result (null: not finalized, or no such transaction yet). */
function judge(result, sig, expected) {
  if (result == null) return { status: 'unknown', reason: 'not finalized on Solana yet (or not yet seen by this reader)' };
  const signatures = (result.transaction && result.transaction.signatures) || [];
  if (signatures[0] !== sig) return { status: 'unknown', reason: 'the Solana reader answered another transaction' };
  if (result.meta && result.meta.err != null) return { status: 'mismatch', reason: 'the transaction failed on Solana' };
  const memos = memosOf(result);
  if (memos.length !== 1) return { status: 'mismatch', reason: memos.length ? 'more than one memo' : 'no memo instruction' };
  // Exact equality is the claim: the root inside a longer memo is not this anchor.
  if (memos[0] !== expected) return { status: 'mismatch', reason: 'the memo is not this day\'s root' };
  const t = Number(result.blockTime);
  if (!Number.isFinite(t) || t <= 0) return { status: 'unknown', reason: 'block time unreadable' };
  const slot = Number(result.slot);
  if (!Number.isInteger(slot) || slot <= 0) return { status: 'unknown', reason: 'slot unreadable' };
  return { status: 'verified', block_time: new Date(t * 1000).toISOString(), same: String(slot), slot, from: payerOf(result), final: true };
}

/**
 * Read Solana and answer: does transaction `sig` anchor this day's root?
 * Every reader is asked; the answer is the readers' agreement:
 * → { status: 'verified', block_time, slot, from, final, readers }
 * → { status: 'mismatch', reason }
 * → { status: 'unknown', reason, disagreement? }
 */
async function verify(sig, day, root, chain, fetchImpl) {
  const id = String(sig || '').trim();
  if (!SIG_RE.test(id)) return { status: 'mismatch', reason: 'malformed transaction signature' };
  const expected = memoFor(day, root);
  if (!expected) return { status: 'mismatch', reason: 'malformed day or root' };
  const f = fetchImpl || global.fetch;
  const urls = (chain && chain.rpcs && chain.rpcs.length) ? chain.rpcs : DEFAULT_RPCS;
  const body = JSON.stringify({ jsonrpc: '2.0', id: 1, method: 'getTransaction',
    params: [id, { encoding: 'jsonParsed', commitment: 'finalized', maxSupportedTransactionVersion: 0 }] });
  const answers = await Promise.all(urls.map(async (url, i) => {
    const reader = readerName(url, i);
    try {
      const r = await f(url, { method: 'POST', headers: { 'content-type': 'application/json', accept: 'application/json' }, body });
      let j = null;
      try { j = await r.json(); } catch (e) { j = null; }
      if (!r.ok || !j || j.jsonrpc !== '2.0' || j.error || !('result' in j)) {
        throw new Error(`answered ${r.status}${j && j.error && j.error.code != null ? ' rpc ' + j.error.code : ''}`);
      }
      return { reader, ...judge(j.result, id, expected) };
    } catch (e) { return unread(reader, e); }
  }));
  return agree(answers, requiredOf(urls.length), 'the Solana readers');
}

module.exports = { memoFor, buildPlan, verify, judge, memosOf, readerName, SIG_RE, MEMO_PROGRAMS, DEFAULT_RPCS, PREFIX };
