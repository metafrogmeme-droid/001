/**
 * Transaction simulation — what a transaction WOULD change for the account
 * sending it, read from an `eth_simulateV1` answer.
 *
 * The X-ray decodes what a transaction SAYS. An unknown function says
 * nothing the X-ray can read, and that is where a drain hides: the call is
 * "claimRewards()" and the effect is every token leaving. Simulating it on
 * top of the chain's latest block shows the effect: which tokens leave this
 * account, which arrive, which allowances it grants.
 *
 * `eth_simulateV1` is a standard JSON-RPC method, answered by Base's own
 * public endpoints with no key (checked 22 Oct 2026), so no simulation vendor
 * sees the request. This module builds the request and reads the answer; it
 * does no I/O. Pure, usable from the browser and via require() on the server.
 *
 * Honesty contract:
 * - A simulation is a reading of ONE block's state. The answer names the
 *   block it ran on; the same transaction a block later can do something
 *   else (a contract can check the time, or be upgraded in between).
 * - A simulation that could not run is said as such. It never reads as
 *   "no balance changes".
 * - Amounts are RAW UNITS; native amounts are wei.
 * - Only effects on the sending account are listed; every other event is
 *   counted, not hidden.
 */
(function (root, factory) {
  if (typeof module === 'object' && module.exports) module.exports = factory();
  else root.RCTxSim = factory();
}(typeof self !== 'undefined' ? self : this, function () {
  'use strict';

  var TOPIC = {
    transfer: '0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef',
    approval: '0x8c5be1e5ebec7d5bd14f71427d1e84f3dd0314c0f7b2291e5b200ac8c7c3b925',
    approvalForAll: '0x17307eab39ab6107e8899845ad3d59bd9653f200f220920489ca2b5937696c31',
    transferSingle: '0xc3d58168c5ae7397731d063d5bbf3d657854427343f4c083240f7aacaa2d0f62',
  };
  /** Where `traceTransfers` reports native-coin movements. */
  var NATIVE = '0xeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee';
  var UNLIMITED = (1n << 128n);
  var ADDR = /^0x[0-9a-f]{40}$/;
  var HEX = /^0x[0-9a-f]*$/;

  function lower(v) { return String(v == null ? '' : v).trim().toLowerCase(); }
  function topicAddr(t) { return '0x' + lower(t).slice(-40); }
  function wordAt(data, i) {
    var h = lower(data).replace(/^0x/, '');
    var w = h.slice(i * 64, i * 64 + 64);
    if (w.length !== 64) throw new Error('short data');
    return BigInt('0x' + w);
  }

  /**
   * The transaction as the simulation takes it, or null when it is not one.
   * `from` is required: a simulation answers "what would this do to THIS
   * account", and without the account there is no question.
   */
  function normalize(tx) {
    var t = tx || {};
    var from = lower(t.from), to = lower(t.to), data = lower(t.data || '0x');
    if (!ADDR.test(from) || !ADDR.test(to) || !HEX.test(data) || data.length % 2) return null;
    var value;
    try { value = BigInt(String(t.value == null || t.value === '' ? '0' : t.value).trim()); }
    catch (e) { return null; }
    if (value < 0n) return null;
    return { from: from, to: to, data: data, value: '0x' + value.toString(16) };
  }

  /** The JSON-RPC body. `validation: false` so an account short of gas still gets an answer. */
  function request(tx) {
    var n = normalize(tx);
    if (!n) return null;
    return {
      jsonrpc: '2.0', id: 1, method: 'eth_simulateV1',
      params: [{ blockStateCalls: [{ calls: [n] }], validation: false, traceTransfers: true }, 'latest'],
    };
  }

  function amountOf(v) { return v >= UNLIMITED ? 'UNLIMITED' : v.toString(); }

  /** One log, read from the sending account's side, or null when it is not about that account. */
  function change(log, me) {
    var topics = Array.isArray(log.topics) ? log.topics.map(lower) : [];
    var at = lower(log.address);
    var t0 = topics[0];
    if (t0 === TOPIC.transfer && topics.length === 3) {
      var from = topicAddr(topics[1]), to = topicAddr(topics[2]), v = wordAt(log.data, 0);
      if (at === NATIVE) {
        if (from === me) return { kind: 'native_out', amount: v.toString(), counterparty: to };
        if (to === me) return { kind: 'native_in', amount: v.toString(), counterparty: from };
        return null;
      }
      if (from === me) return { kind: 'token_out', token: at, amount: v.toString(), counterparty: to };
      if (to === me) return { kind: 'token_in', token: at, amount: v.toString(), counterparty: from };
      return null;
    }
    if (t0 === TOPIC.transfer && topics.length === 4) {
      var nf = topicAddr(topics[1]), nt = topicAddr(topics[2]), id = BigInt(topics[3]).toString();
      if (nf === me) return { kind: 'nft_out', token: at, id: id, counterparty: nt };
      if (nt === me) return { kind: 'nft_in', token: at, id: id, counterparty: nf };
      return null;
    }
    if (t0 === TOPIC.approval && topics.length === 3 && topicAddr(topics[1]) === me) {
      var a = wordAt(log.data, 0);
      return { kind: 'approval', token: at, spender: topicAddr(topics[2]), amount: amountOf(a),
        unlimited: a >= UNLIMITED };
    }
    if (t0 === TOPIC.approval && topics.length === 4 && topicAddr(topics[1]) === me) {
      return { kind: 'nft_approval', token: at, spender: topicAddr(topics[2]), id: BigInt(topics[3]).toString() };
    }
    if (t0 === TOPIC.approvalForAll && topics.length === 3 && topicAddr(topics[1]) === me) {
      return { kind: 'approval_for_all', token: at, operator: topicAddr(topics[2]), on: wordAt(log.data, 0) !== 0n };
    }
    if (t0 === TOPIC.transferSingle && topics.length === 4) {
      var sf = topicAddr(topics[2]), st = topicAddr(topics[3]);
      var sid = wordAt(log.data, 0).toString(), sv = wordAt(log.data, 1).toString();
      if (sf === me) return { kind: 'nft_out', token: at, id: sid, amount: sv, counterparty: st };
      if (st === me) return { kind: 'nft_in', token: at, id: sid, amount: sv, counterparty: sf };
      return null;
    }
    return null;
  }

  /**
   * Read an `eth_simulateV1` JSON-RPC answer for the account `from`.
   *
   * → { ok: true, status: 'success' | 'reverted', block, changes, other_events, flags }
   * → { ok: false, reason: 'rpc_error', code }   the endpoint refused the request
   * → { ok: false, reason: 'malformed' }          the answer is not one this reads
   */
  function read(body, from) {
    var me = lower(from);
    if (!body || typeof body !== 'object') return { ok: false, reason: 'malformed' };
    if (body.error) {
      var code = body.error && typeof body.error.code === 'number' ? body.error.code : null;
      return { ok: false, reason: 'rpc_error', code: code };
    }
    var blocks = body.result;
    var b = Array.isArray(blocks) ? blocks[0] : null;
    var call = b && Array.isArray(b.calls) ? b.calls[0] : null;
    // A status is 0x1 or 0x0. Anything else is an answer this does not
    // read, never a success and never a revert.
    if (!call || (call.status !== '0x1' && call.status !== '0x0')) return { ok: false, reason: 'malformed' };
    var block;
    try {
      // The simulated block is one past the state it ran on.
      block = (BigInt(b.number) - 1n).toString();
    } catch (e) { return { ok: false, reason: 'malformed' }; }
    var out = { ok: true, status: call.status === '0x1' ? 'success' : 'reverted', block: block,
      changes: [], other_events: 0, flags: [] };
    if (out.status === 'reverted') {
      // A reverted call changes nothing; any logs it carried were undone.
      // Not dangerous, but a transaction that fails still costs gas: checked, not cleared.
      out.flags.push({ id: 'sim_reverted', sev: 'medium' });
      return out;
    }
    var logs = Array.isArray(call.logs) ? call.logs : [];
    for (var i = 0; i < logs.length; i++) {
      var c = null;
      try { c = change(logs[i] || {}, me); } catch (e) { c = null; }
      if (c) out.changes.push(c);
      else out.other_events++;
    }
    var seen = {};
    out.changes.forEach(function (c) {
      var id = null, sev = null;
      if (c.kind === 'approval' && c.unlimited) { id = 'unlimited_approval'; sev = 'high'; }
      else if (c.kind === 'approval_for_all' && c.on) { id = 'approval_for_all'; sev = 'high'; }
      else if (c.kind === 'token_out' || c.kind === 'nft_out' || c.kind === 'native_out') { id = 'sim_assets_out'; sev = 'info'; }
      if (id && !seen[id]) { seen[id] = true; out.flags.push({ id: id, sev: sev }); }
    });
    return out;
  }

  return { request: request, read: read, normalize: normalize, TOPIC: TOPIC, NATIVE: NATIVE };
}));
