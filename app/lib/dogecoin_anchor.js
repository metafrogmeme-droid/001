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
 * READS the transaction from BlockCypher's keyless API (it returns an
 * OP_RETURN output's bytes as `data_hex`; free tier "up to 3 requests/sec and
 * 100 requests/hr", blockcypher.com/dev, read 2026-10-21). Blockchair blocks
 * keyless callers and Trezor's Blockbook challenges them (same day), so there
 * is no second keyless reader: when BlockCypher does not answer, the verdict
 * is "unknown", never a guess.
 */

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
 * Read Dogecoin and answer: does transaction `txid` anchor this day's root?
 * → { status: 'verified', block_time, from }   one OP_RETURN, exactly the payload, `depth` blocks deep
 * → { status: 'mismatch', reason }             the transaction exists and does not anchor this root
 * → { status: 'unknown', reason }              not readable, not mined, or not deep enough yet
 */
async function verify(txid, day, root, chain, fetchImpl) {
  const id = String(txid || '').toLowerCase();
  if (!TXID_RE.test(id)) return { status: 'mismatch', reason: 'malformed transaction id' };
  const expected = payloadHex(day, root);
  if (!expected) return { status: 'mismatch', reason: 'malformed day or root' };
  const f = fetchImpl || global.fetch;
  let r;
  try {
    r = await f(TX_API + id, { headers: { accept: 'application/json' } });
  } catch (e) {
    return { status: 'unknown', reason: `Dogecoin could not be read (${e && e.name ? e.name : 'Error'})` };
  }
  if (r.status === 404) return { status: 'mismatch', reason: 'no such transaction on Dogecoin' };
  if (!r.ok) return { status: 'unknown', reason: `the Dogecoin reader answered ${r.status}` };
  let tx;
  try { tx = await r.json(); } catch (e) { return { status: 'unknown', reason: 'the Dogecoin reader answered no JSON' }; }
  if (!tx || String(tx.hash || '').toLowerCase() !== id || !Array.isArray(tx.outputs)) {
    return { status: 'unknown', reason: 'the Dogecoin reader answered another shape' };
  }
  const data = tx.outputs.filter((o) => o && o.script_type === 'null-data');
  if (data.length !== 1) {
    return { status: 'mismatch', reason: data.length ? 'more than one OP_RETURN output' : 'no OP_RETURN output' };
  }
  // data_hex is the pushed bytes; exact equality is the claim (a payload
  // buried in a longer push is not this anchor).
  if (String(data[0].data_hex || '').toLowerCase() !== expected) {
    return { status: 'mismatch', reason: 'the OP_RETURN bytes are not this day\'s root' };
  }
  const confs = Number(tx.confirmations);
  if (!(Number(tx.block_height) > 0) || !Number.isFinite(confs) || confs < 1) {
    return { status: 'unknown', reason: 'transaction not yet mined' };
  }
  if (confs < chain.reorg_depth) {
    return { status: 'unknown', reason: `${confs} of the ${chain.reorg_depth} confirmations ${chain.name} needs` };
  }
  const when = Date.parse(tx.confirmed);
  if (!Number.isFinite(when)) return { status: 'unknown', reason: 'block time unreadable' };
  const payer = tx.inputs && tx.inputs[0] && Array.isArray(tx.inputs[0].addresses) ? tx.inputs[0].addresses[0] : '';
  return { status: 'verified', block_time: new Date(when).toISOString(), from: String(payer || '') };
}

module.exports = { payloadHex, buildPlan, verify, TAG, MAX_OP_RETURN_BYTES, TX_API, TXID_RE };
