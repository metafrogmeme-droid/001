/* RUNECLAW — lightweight Solana wallet connector (Phantom / Backpack).
 *
 * Upgrades the watch-only Solana flow into connect-and-sign, mirroring the EVM
 * pattern (nonce -> sign -> link). Dependency-free: uses the wallet's injected
 * provider directly (window.solana / window.backpack), no @solana/web3.js bundle
 * needed just to connect and sign a login message.
 *
 * Non-custodial: only ever calls connect() and signMessage() on a login string.
 * It never builds, signs, or sends a transaction. DRAFT feature — see the token
 * roadmap. Exposes window.RCSolanaWallet = { available, connect, signMessage }.
 */
(function () {
  'use strict';

  function provider() {
    if (window.phantom && window.phantom.solana && window.phantom.solana.isPhantom) {
      return window.phantom.solana;
    }
    if (window.backpack && window.backpack.isBackpack) return window.backpack;
    if (window.solana) return window.solana; // Phantom (legacy) or other injected
    return null;
  }

  function available() {
    return !!provider();
  }

  async function connect() {
    const p = provider();
    if (!p) {
      const e = new Error('No Solana wallet found. Install Phantom or Backpack.');
      e.code = 'NO_WALLET';
      throw e;
    }
    const res = await p.connect();
    const pk = (res && res.publicKey) || p.publicKey;
    if (!pk) throw new Error('Wallet did not return a public key.');
    return { provider: p, address: pk.toString() };
  }

  function toBase64(bytes) {
    const b = new Uint8Array(bytes);
    let bin = '';
    for (let i = 0; i < b.length; i++) bin += String.fromCharCode(b[i]);
    return btoa(bin);
  }

  // Sign a UTF-8 message; returns { address, signature } with signature base64.
  async function signMessage(message) {
    const { provider: p, address } = await connect();
    const encoded = new TextEncoder().encode(String(message));
    const signed = await p.signMessage(encoded, 'utf8');
    const sigBytes = (signed && signed.signature) ? signed.signature : signed;
    return { address, signature: toBase64(sigBytes) };
  }

  window.RCSolanaWallet = { available, connect, signMessage };
})();
