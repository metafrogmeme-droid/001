/*
 * The page's door to an injected EVM wallet (EIP-1193), shared by the DogeOS
 * app (/doge) and Memeclaw (/memeclaw): find the wallet, ask it for a chain
 * the server described, and tell a person's "no" from a failure.
 *
 * THE CHAIN IS THE SERVER'S READING. switchTo is handed the chain as the
 * registry describes it (lib/root_anchor.js publicChains: id, name, native
 * currency, public RPCs, explorer); nothing here spells a chain of its own.
 *
 * NOTHING HERE SENDS. Asking for accounts and switching chains are the only
 * requests; what a page then asks the wallet to sign or send is that page's,
 * where its tests can see it.
 */
(function (root, factory) {
  var api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  if (root) root.RCEvmWallet = api;
}(typeof window !== 'undefined' ? window : null, function () {
  'use strict';

  /** The injected wallet, or null when the page has none. */
  function wallet() { return (typeof window !== 'undefined' && window.ethereum) || null; }

  function hex(n) { return '0x' + Number(n).toString(16); }

  /**
   * Ask the wallet for chain `c`. A wallet that has never seen it answers
   * 4902; it is then asked to add the chain, described from the registry's
   * own entry (name, native currency, public RPCs, explorer).
   */
  function switchTo(eth, c) {
    var chainId = hex(c.chain_id);
    return eth.request({ method: 'wallet_switchEthereumChain', params: [{ chainId: chainId }] })
      .catch(function (err) {
        var unknownChain = err && (err.code === 4902 || /4902|unrecognized|not been added/i.test(String(err.message || '')));
        if (!unknownChain) throw err;
        var explorer = String(c.explorer_tx || '').replace(/\/tx\/?$/, '');
        var params = { chainId: chainId, chainName: c.name, nativeCurrency: c.native, rpcUrls: c.rpcs };
        if (explorer) params.blockExplorerUrls = [explorer];
        return eth.request({ method: 'wallet_addEthereumChain', params: [params] });
      });
  }

  /** True for the wallet's own "the person declined" answers (EIP-1193 4001 and its wordings). */
  function declined(err) {
    return !!(err && (err.code === 4001 || /rejected|denied|cancel/i.test(String(err.message || ''))));
  }

  /** The first account the wallet grants, lowercased, or a 4001-shaped refusal. */
  function account(eth) {
    return eth.request({ method: 'eth_requestAccounts' }).then(function (accounts) {
      var a = String((accounts && accounts[0]) || '').trim().toLowerCase();
      if (!/^0x[0-9a-f]{40}$/.test(a)) throw { code: 4001 };
      return a;
    });
  }

  return { wallet: wallet, hex: hex, switchTo: switchTo, declined: declined, account: account };
}));
