'use strict';
/**
 * A day's seal root anchored on Dogecoin itself (mainnet), by the owner's or a
 * player's own wallet: one zero-value OP_RETURN output carrying the root.
 *
 * WHY A SHORTER PAYLOAD. Dogecoin Core relays one OP_RETURN output of at most
 * 80 data bytes (MAX_OP_RETURN_RELAY = 83, "+1 for OP_RETURN, +2 for the
 * pushdata opcodes", src/script/standard.h on master and 1.14-maint, read
 * 2026-10-21). The EVM chains' text `RCROOT1:<day>:<64 hex>` is 83 bytes, so
 * here the root rides as its 32 raw bytes behind its own tag:
 *     utf8("RCD1:" + day + ":") ++ root (32 bytes)   = 48 bytes
 * A separate tag, so a reader never parses one format as the other.
 *
 * NOTHING HERE SIGNS, FUNDS OR BROADCASTS. buildPlan hands out the payload and
 * the Dogecoin Core console commands; the wallet's owner runs them. verify()
 * READS the transaction from two keyless readers and requires them to agree
 * (BlockCypher: OP_RETURN bytes as `data_hex`, "up to 3 requests/sec and 100
 * requests/hr"; SoChain v3: the output script, read here; both 2026-10-21).
 * One reader that lies, lags or is down never turns a day green or refuses a
 * real anchor: the verdict is "unknown". Blockchair blocks keyless callers and
 * Trezor's Blockbook challenges them (same day).
 */

const { agree, requiredOf, unread } = require('./reader_agreement');

const DAY_RE = /^\d{4}-\d{2}-\d{2}$/;
const ROOT_RE = /^[0-9a-f]{64}$/;
const TXID_RE = /^[0-9a-f]{64}$/;
const TAG = 'RCD1';
const MAX_OP_RETURN_BYTES = 80;
const TX_API = 'https://api.blockcypher.com/v1/doge/main/txs/';

/** The OP_RETURN payload for a day's root, as lowercase hex (no 0x). */
function payloadHex(day, root) {
  if (!DAY_RE.test(String(day)) || !ROOT_RE.test(String(root))) return null;
  const hex = Buffer.from(`${TAG}:${day}:`, 'utf8').toString('hex') + String(root);
  if (hex.length / 2 > MAX_OP_RETURN_BYTES) return null;
  return hex;
}

/** The plan for `chain` (the registry's Dogecoin entry). DRY RUN ONLY. */
function buildPlan(day, root, chain) {
  const data = payloadHex(day, root);
  if (!data) return null;
  return {
    dry_run: true, chain: chain.name, chain_key: chain.key, method: chain.method,
    value: '0', data, payload_bytes: data.length / 2, day, root,
    payload_format: `utf8("${TAG}:<day>:") followed by the root's 32 bytes`,
    instructions: [
      'In Dogecoin Core 1.14 (Window → Console, or dogecoin-cli), with your own wallet:',
      `createrawtransaction "[]" "{\\"data\\":\\"${data}\\"}"`,
      'fundrawtransaction <the hex it printed>   — adds your inputs and change; the fee comes '
        + 'from your wallet (at least 0.01 DOGE per kB, a few thousandths of a DOGE here)',
      'signrawtransaction <the "hex" fundrawtransaction printed>',
      'sendrawtransaction <the "hex" signrawtransaction printed>   — prints the transaction id',
      'Then submit that id via POST /api/roots/anchor '
        + `{ day, tx_hash, chain: "${chain.key}" }. The server reads the transaction back `
        + `and records it once it is ${chain.reorg_depth} blocks deep.`,
    ],
    non_custodial_note: 'The server never holds a key and never sends a '
      + 'transaction. This is a plan, not an action.',
  };
}

/**
 * The data of a script that is exactly OP_RETURN and one push, as hex; null
 * for any other script. A payload buried in a longer script is not this
 * anchor.
 */
function opReturnData(scriptHex) {
  const h = String(scriptHex || '').toLowerCase();
  if (!/^6a([0-9a-f]{2})+$/.test(h)) return null;
  const op = parseInt(h.slice(2, 4), 16);
  let len; let at;
  if (op >= 0x01 && op <= 0x4b) { len = op; at = 4; }
  else if (op === 0x4c) { len = parseInt(h.slice(4, 6), 16); at = 6; }
  else if (op === 0x4d) { len = parseInt(h.slice(6, 8) + h.slice(4, 6), 16); at = 8; }
  else return null;
  const data = h.slice(at);
  return data.length === len * 2 ? data : null;
}

/**
 * Two keyless readers of a Dogecoin transaction, each answering
 * { found: false } (it said there is no such transaction), the transaction
 * as { found: true, hash, confirmations, block_hash, time, op_returns, payer },
 * or throwing when it could not be read. The second (SoChain, 2026-10-21)
 * answered from this server's egress where Blockchair and Trezor's Blockbook
 * did not.
 */
const READERS = Object.freeze([
  {
    name: 'blockcypher',
    url: (id) => TX_API + id,
    parse(status, j) {
      if (status === 404) return { found: false };
      if (status !== 200 || !j || !Array.isArray(j.outputs)) throw new Error(`blockcypher answered ${status}`);
      const conf = Number(j.confirmations);
      return {
        found: true, hash: String(j.hash || '').toLowerCase(),
        confirmations: Number(j.block_height) > 0 && Number.isFinite(conf) ? conf : 0,
        block_hash: j.block_hash ? String(j.block_hash).toLowerCase() : null,
        time: Number.isFinite(Date.parse(j.confirmed)) ? new Date(Date.parse(j.confirmed)).toISOString() : null,
        op_returns: j.outputs.filter((o) => o && o.script_type === 'null-data').map((o) => String(o.data_hex || '').toLowerCase()),
        payer: j.inputs && j.inputs[0] && Array.isArray(j.inputs[0].addresses) ? String(j.inputs[0].addresses[0] || '') : '',
      };
    },
  },
  {
    name: 'sochain',
    url: (id) => `https://sochain.com/api/v3/transaction/DOGE/${id}`,
    parse(status, j) {
      if (status === 404) return { found: false };
      const d = j && j.data;
      if (status !== 200 || !j || j.status !== 'success' || !d || !Array.isArray(d.outputs)) throw new Error(`sochain answered ${status}`);
      const conf = Number(d.confirmations);
      return {
        found: true, hash: String(d.hash || '').toLowerCase(),
        confirmations: Number(d.block_height) > 0 && Number.isFinite(conf) ? conf : 0,
        block_hash: d.block_hash ? String(d.block_hash).toLowerCase() : null,
        time: Number.isFinite(Number(d.time)) && Number(d.time) > 0 ? new Date(Number(d.time) * 1000).toISOString() : null,
        op_returns: d.outputs.filter((o) => o && o.type === 'nulldata').map((o) => opReturnData(o.script && o.script.hex) || ''),
        payer: d.inputs && d.inputs[0] ? String(d.inputs[0].address || '') : '',
      };
    },
  },
]);

/** One reader's verdict on one transaction. */
function judge(tx, id, expected, chain) {
  if (!tx.found) return { status: 'mismatch', reason: 'no such transaction on Dogecoin' };
  if (tx.hash !== id) return { status: 'unknown', reason: 'the Dogecoin reader answered another transaction' };
  if (tx.op_returns.length !== 1) {
    return { status: 'mismatch', reason: tx.op_returns.length ? 'more than one OP_RETURN output' : 'no OP_RETURN output' };
  }
  // Exact equality is the claim: a payload buried in a longer push is not this anchor.
  if (tx.op_returns[0] !== expected) return { status: 'mismatch', reason: 'the OP_RETURN bytes are not this day\'s root' };
  if (tx.confirmations < 1) return { status: 'unknown', reason: 'transaction not yet mined' };
  if (tx.confirmations < chain.reorg_depth) {
    return { status: 'unknown', reason: `${tx.confirmations} of the ${chain.reorg_depth} confirmations ${chain.name} needs` };
  }
  if (!tx.time) return { status: 'unknown', reason: 'block time unreadable' };
  return { status: 'verified', block_time: tx.time, same: tx.block_hash, from: tx.payer };
}

/**
 * Read Dogecoin and answer: does transaction `txid` anchor this day's root?
 * Every reader is asked; the answer is the readers' agreement:
 * → { status: 'verified', block_time, from, readers }   every reader answered, each
 *     saw exactly the payload `depth` blocks deep, in the same block
 * → { status: 'mismatch', reason }   every reader answered that it does not anchor this root
 * → { status: 'unknown', reason }    a reader could not be read, it is not deep enough
 *     yet, or the readers disagree (`disagreement: true`): one reader that lies
 *     or lags must not turn a day green, nor refuse a real anchor.
 */
async function verify(txid, day, root, chain, fetchImpl, readers = READERS) {
  const id = String(txid || '').toLowerCase();
  if (!TXID_RE.test(id)) return { status: 'mismatch', reason: 'malformed transaction id' };
  const expected = payloadHex(day, root);
  if (!expected) return { status: 'mismatch', reason: 'malformed day or root' };
  const f = fetchImpl || global.fetch;
  const answers = await Promise.all(readers.map(async (rd) => {
    try {
      const r = await f(rd.url(id), { headers: { accept: 'application/json' } });
      let j = null;
      try { j = await r.json(); } catch (e) { j = null; }
      return { reader: rd.name, ...judge(rd.parse(r.status, j), id, expected, chain) };
    } catch (e) { return unread(rd.name, e); }
  }));
  return agree(answers, requiredOf(readers.length), 'the Dogecoin readers');
}

module.exports = { payloadHex, buildPlan, verify, opReturnData, READERS, TAG, MAX_OP_RETURN_BYTES, TX_API, TXID_RE };
