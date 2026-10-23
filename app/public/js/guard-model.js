/**
 * Claw Guard — one plain-words verdict from what a request SAYS (the
 * transaction X-ray or the signature X-ray) and what it DOES (the
 * simulation), before anything is signed.
 *
 * Pure, dependency-free, usable from the browser and via require() on the
 * server. It reads the other models' results; it decodes and fetches
 * nothing itself.
 *
 * The verdict is a reading with reasons, never a guarantee:
 *   'stop'     at least one high-severity reason: read before signing
 *   'check'    medium reasons only: check the details
 *   'clear'    no warning in what could be read; not a guarantee
 *   'unknown'  the request was not decoded and nothing ran that could see
 *              its effects; unknown is not safe
 *   'invalid'  the input is not a transaction or a signature this reads
 *
 * A simulation that could not run is a reason of its own
 * ('sim_unavailable'), so "nothing flagged" is never printed over an
 * effect nobody checked.
 */
(function (root, factory) {
  if (typeof module === 'object' && module.exports) module.exports = factory();
  else root.RCGuard = factory();
}(typeof self !== 'undefined' ? self : this, function () {
  'use strict';

  var RANK = { high: 3, medium: 2, info: 1 };

  /** One sentence per reason, in English; `grd.r_<id>` carries the translations. */
  var REASONS = {
    unlimited_approval: 'It grants an allowance with no practical limit: whoever holds it can take every unit of that token you hold, now or later.',
    approval_for_all: 'It hands control of every item you hold in this collection to one address.',
    delegation_7702: 'It hands your account’s code to a contract until you sign a new delegation. Whatever that contract allows can move everything the account holds.',
    any_chain: 'It is valid on every chain, not only this one.',
    chain_mismatch: 'It is for a different chain than the one your wallet is on.',
    permit2_lookalike: 'It uses Permit2’s message names, but it is not addressed to Permit2’s contract.',
    gasless_permit: 'It is an approval made by signature: someone else submits it later, and no wallet prompt appears when it is used.',
    signature_transfer: 'It lets the spender take the tokens once, with no further signature from you.',
    long_lived: 'It stays usable for more than 30 days. Old signatures have drained wallets long after they were signed.',
    no_chain: 'It names no chain, so it may be usable on more than one.',
    expired: 'Its deadline has passed, so it can no longer be used.',
    sends_native: 'It sends the chain’s native coin.',
    sim_assets_out: 'In the simulation, assets leave your account (listed below).',
    sim_reverted: 'In the simulation it fails: nothing would change, and a sent transaction would still cost gas.',
    sim_unavailable: 'The simulation could not run, so its effects are unchecked.',
    unknown_function: 'This calls a function this tool does not decode. Unknown is not the same as safe.',
    unknown_message: 'This is a message type this tool does not decode. Unknown is not the same as safe.',
    undecoded_witness: 'It also signs an order that the spender’s contract reads, which this tool does not decode.',
    undecodable: 'Part of it could not be read.',
  };

  /** Each verdict's headline; `grd.v_<level>` carries the translations. */
  var HEADLINES = {
    stop: 'Stop and read before signing.',
    check: 'Check the details before signing.',
    clear: 'No warning in what could be read. This is not a guarantee.',
    unknown: 'Not decoded. Unknown is not the same as safe.',
    invalid: 'This is not a transaction or a signature this tool reads.',
  };

  /**
   * @param decoded    a txray `decodeTx` or sigray `decode*` result
   * @param simulated  a txsim reading ({ ok, ... }), or null when none was asked for
   * → { level, headline, reasons: [{ id, sev, en }], actions, changes, block, heuristic: true }
   */
  function verdict(decoded, simulated) {
    var d = decoded || {};
    var base = { heuristic: true, actions: Array.isArray(d.actions) ? d.actions : [],
      changes: [], block: null, reasons: [] };
    if (d.invalid || (!base.actions.length && !d.empty && !(simulated && simulated.ok))) {
      base.level = 'invalid';
      base.headline = HEADLINES.invalid;
      return base;
    }
    var flags = (Array.isArray(d.flags) ? d.flags : []).slice();
    var simRan = !!(simulated && simulated.ok);
    if (simRan) {
      base.changes = Array.isArray(simulated.changes) ? simulated.changes : [];
      base.block = simulated.block == null ? null : String(simulated.block);
      flags = flags.concat(Array.isArray(simulated.flags) ? simulated.flags : []);
    } else if (simulated) {
      flags.push({ id: 'sim_unavailable', sev: 'info' });
    }
    var seen = {};
    var reasons = [];
    flags.forEach(function (f) {
      if (!f || !f.id) return;
      var sev = RANK[f.sev] ? f.sev : 'info';
      // Assets leaving through a call nobody decoded: the list is the only
      // account of why, so it is read before signing, not skimmed.
      if (f.id === 'sim_assets_out' && d.unknown && RANK[sev] < RANK.medium) sev = 'medium';
      if (seen[f.id]) {
        // The same reason from the decode and the simulation: keep the graver one.
        if (RANK[sev] > RANK[seen[f.id].sev]) seen[f.id].sev = sev;
        return;
      }
      seen[f.id] = { id: f.id, sev: sev, en: REASONS[f.id] || null };
      reasons.push(seen[f.id]);
    });
    reasons.sort(function (a, b) { return RANK[b.sev] - RANK[a.sev]; });
    base.reasons = reasons;
    var top = reasons.length ? RANK[reasons[0].sev] : 0;
    if (top === 3) base.level = 'stop';
    else if (top === 2) base.level = 'check';
    // Undecoded, and nothing ran that could see what it does. An empty call
    // is in this case too: to a contract, it runs the contract's fallback.
    else if ((d.unknown || d.empty) && !simRan) base.level = 'unknown';
    else base.level = 'clear';
    base.headline = HEADLINES[base.level];
    return base;
  }

  return { verdict: verdict, REASONS: REASONS, HEADLINES: HEADLINES };
}));
