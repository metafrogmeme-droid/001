/*
 * "Stamp in EAS" on /roots: a visitor's own wallet stamps a completed day's
 * root in the Ethereum Attestation Service on a chain the registry names
 * (Base, Arbitrum One, Scroll or Linea), then the page hands the transaction
 * hash to the server, which reads the stamp back from that chain's EAS
 * before recording it (lib/root_anchor.js readEasStampAnchor).
 *
 * THE WALLET SENDS, NOT US. The server answers the plan (to = EAS, data =
 * timestamp(root)); the visitor's wallet asks them to confirm and pays the
 * chain's fee. The page refuses a plan whose `to` is not the registry's EAS
 * address, so a tampered plan cannot point the wallet anywhere else.
 *
 * Pure flow with its doors passed in, so app/test/a_day_is_stamped_on_mainnets.test.js
 * drives it with a stand-in wallet and server.
 */
(function (root, factory) {
  var api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  if (root) root.RCRootsStamp = api;
}(typeof window !== 'undefined' ? window : null, function () {
  'use strict';

  var METHOD = 'eas_timestamp';

  /** A registry chain's short name: "Arbitrum One (EAS timestamp)" → "Arbitrum One". */
  function shortName(chain) { return String((chain && chain.name) || '').replace(/ \(.*\)$/, ''); }

  /** The registry's EAS chains (`chains`, as /api/roots/chains serves them) a day is not yet anchored on (`anchored`: chain keys). */
  function chainsLeft(chains, anchored) {
    var have = Array.isArray(anchored) ? anchored : [];
    return (Array.isArray(chains) ? chains : []).filter(function (c) {
      return !!c && c.method === METHOD && have.indexOf(c.key) < 0;
    });
  }

  function sleep(ms) { return new Promise(function (r) { setTimeout(r, ms); }); }

  /**
   * Stamp `day`. deps: { eth, wallet (RCEvmWallet), chain (a registry entry
   * whose method is eas_timestamp), getJSON(url) → { status, body }, postJSON(url, body) →
   * { status, body }, onState(state), retryMs, retries }.
   * → { state: 'recorded' | 'already' | 'stamped_unrecorded', tx?, stamped_at?, reason? }
   * Throws an Error whose message is for the person (or the wallet's own
   * refusal, which the caller tells apart with wallet.declined).
   */
  async function stamp(day, deps) {
    var chain = deps.chain;
    if (!chain || chain.method !== METHOD || !/^[a-z0-9-]+$/.test(String(chain.key || ''))
      || !/^0x[0-9a-fA-F]{40}$/.test(String(chain.eas || ''))) {
      throw new Error('The site did not describe the EAS contract, so nothing was sent.');
    }
    var on = typeof deps.onState === 'function' ? deps.onState : function () {};
    var from = await deps.wallet.account(deps.eth);
    await deps.wallet.switchTo(deps.eth, chain);
    var plan = await deps.getJSON('/api/roots/anchor-plan/' + encodeURIComponent(day) + '?chain=' + encodeURIComponent(chain.key));
    if (plan.status !== 200) throw new Error((plan.body && plan.body.error) || ('The plan was refused (' + plan.status + ').'));
    var p = plan.body || {};
    if (p.already_stamped) return { state: 'already', stamped_at: p.stamped_at || null };
    if (p.already_anchored) return { state: 'already', tx: p.anchor_tx || null };
    if (String(p.to || '').toLowerCase() !== String(chain.eas).toLowerCase()) {
      throw new Error('The plan names another contract than EAS, so nothing was sent.');
    }
    if (!/^0x4d003070[0-9a-f]{64}$/.test(String(p.data || ''))) {
      throw new Error('The plan is not a timestamp() call, so nothing was sent.');
    }
    on('wallet');
    var tx = await deps.eth.request({ method: 'eth_sendTransaction',
      params: [{ from: from, to: p.to, data: p.data, value: '0x0' }] });
    on('waiting');
    var tries = deps.retries == null ? 20 : deps.retries;
    for (var i = 0; i <= tries; i += 1) {
      var r = await deps.postJSON('/api/roots/anchor', { day: day, tx_hash: tx, chain: chain.key });
      if (r.status === 200) return { state: 'recorded', tx: tx };
      if (r.status === 409) return { state: 'already', tx: (r.body && r.body.anchor_tx) || tx };
      if (r.status === 401) return { state: 'stamped_unrecorded', tx: tx };
      if (r.status !== 503) {
        return { state: 'stamped_unrecorded', tx: tx, reason: (r.body && (r.body.reason || r.body.error)) || ('HTTP ' + r.status) };
      }
      if (i < tries) await sleep(deps.retryMs == null ? 6000 : deps.retryMs);
    }
    return { state: 'stamped_unrecorded', tx: tx, reason: shortName(chain) + ' had not confirmed it yet' };
  }

  return { stamp: stamp, shortName: shortName, chainsLeft: chainsLeft, METHOD: METHOD };
}));
