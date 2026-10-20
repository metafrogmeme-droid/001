/*
 * The DogeOS app: the arena session, signed in with a DogeOS wallet.
 *
 * The session, the arena and the Daily Duel are the Pi app's (arena-session.js,
 * pi-duel.js); what this file adds is the sign-in and the start. Sign-in is
 * the site's own wallet sign-in: the page asks the wallet for an account,
 * asks it to switch to the DogeOS testnet, fetches a one-time message from
 * /api/auth/wallet/nonce, has the wallet sign it (personal_sign: a signature,
 * not a transaction, no gas) and hands the signature to /api/auth/wallet/verify,
 * which answers the session token the arena API takes as a bearer.
 *
 * THE CHAIN IS THE SERVER'S READING. Its id, name, RPCs, explorer and native
 * currency come from /api/roots/chains, the registry lib/root_anchor.js keeps
 * and verifies anchors against; nothing here spells a chain id of its own, so
 * the wallet is asked for the chain the anchors are checked on and no other.
 *
 * HONESTY. A wallet that is not there is said to be missing (the session's
 * 'no-host'); a person who declined is 'rejected' and gets no warning colour;
 * a server that could not answer is 'unavailable', which is ours to fix; and
 * everything the page cannot tell apart is 'unknown', never a guess.
 */
(function () {
  'use strict';

  /** The chain this app anchors on and signs in through. */
  var CHAIN_KEY = 'dogeos-testnet';

  var chainPromise = null;
  /** The DogeOS testnet as the registry describes it, read once; a failed read is asked again next time. */
  function chain() {
    if (!chainPromise) {
      chainPromise = fetch('/api/roots/chains', { headers: { accept: 'application/json' } })
        .then(function (r) { if (!r.ok) throw new Error('HTTP ' + r.status); return r.json(); })
        .then(function (j) {
          var c = ((j && j.chains) || []).filter(function (x) { return x && x.key === CHAIN_KEY; })[0];
          if (!c) throw new Error('chain not in the registry');
          return c;
        });
      chainPromise.catch(function () { chainPromise = null; });
    }
    return chainPromise;
  }

  /** The injected EVM wallet, or null when the page has none. */
  function wallet() { return (typeof window !== 'undefined' && window.ethereum) || null; }

  function hex(n) { return '0x' + Number(n).toString(16); }

  /**
   * Ask the wallet for the DogeOS testnet. A wallet that has never seen it
   * answers 4902; it is then asked to add the chain, described from the
   * registry's own entry (name, native DOGE, RPCs, explorer).
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

  function signIn(api) {
    var eth = wallet();
    if (!eth) return Promise.resolve({ status: 'no-host' });
    var address = '';
    return chain().then(function (c) {
      return eth.request({ method: 'eth_requestAccounts' }).then(function (accounts) {
        address = String((accounts && accounts[0]) || '').trim();
        if (!address) throw { code: 4001 };
        return switchTo(eth, c);
      });
    }).then(function () {
      return api('/api/auth/wallet/nonce', { method: 'POST', body: { address: address } });
    }).then(function (n) {
      if (!n.ok || !n.body || !n.body.message) return { status: n.status === 503 ? 'unavailable' : 'unknown' };
      return eth.request({ method: 'personal_sign', params: [n.body.message, address] })
        .then(function (signature) {
          return api('/api/auth/wallet/verify', { method: 'POST', body: { address: address, signature: signature } });
        })
        .then(function (r) {
          if (r.ok && r.body && r.body.token) {
            window.RCDogeApp.address = address;
            // null is a real answer: no handle chosen yet, so this account is
            // on no board. The page says so rather than inventing one.
            return { status: 'signed-in', token: r.body.token, me: { handle: (r.body.leaderboard_handle || null) } };
          }
          if (r.status === 503) return { status: 'unavailable' };
          return { status: 'refused', reason: (r.body && r.body.error) || null };
        });
    }).catch(function (err) {
      if (declined(err)) return { status: 'rejected' };
      return { status: 'unknown' };
    });
  }

  window.RCDogeApp = { CHAIN_KEY: CHAIN_KEY, chain: chain, wallet: wallet, switchTo: switchTo, declined: declined, signIn: signIn, address: null };

  if (typeof document !== 'undefined' && document.getElementById('root') && window.RCArenaSession) {
    window.RCArenaSession.start({
      root: document.getElementById('root'),
      host: 'DogeOS',
      waiting: ['Waiting for your wallet…',
        'Approve the connection, switch to the DogeOS testnet, and sign the message. It is not a transaction and costs no gas.'],
      signIn: signIn,
      extras: [
        // The Daily Duel's card first: quick to play.
        window.RCPiDuel.create(),
        // The proof: every day's root and its anchors, and the anchoring itself.
        window.RCDogeProof.create(),
      ],
    });
  }
}());
