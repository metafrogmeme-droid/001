/* RUNECLAW — lightweight Solana wallet connector (Phantom / Backpack).
 *
 * Upgrades the watch-only Solana flow into connect-and-sign, mirroring the EVM
 * pattern (nonce -> sign -> link). Dependency-free: uses the wallet's injected
 * provider directly (window.solana / window.backpack), no @solana/web3.js bundle
 * needed just to connect and sign a login message.
 *
 * NON-CUSTODIAL, AND THAT IS A STATEMENT ABOUT KEYS, NOT ABOUT TRANSACTIONS.
 *
 * This file used to say it "never builds, signs, or sends a transaction", and
 * `signAndSend` below changes that sentence — so it is worth being exact about
 * what did and did not change. What did NOT change is the only part that was
 * ever a custody claim: no private key is read, held, derived or transmitted
 * here, and none can be. `signAndSend` hands an opaque server-built
 * transaction to the USER'S OWN wallet and asks the wallet to sign it. The
 * approval dialog is the wallet's, the key never leaves it, and a refusal
 * there is final.
 *
 * The server-side tests that assert RUNECLAW never signs (`web3_sign`,
 * `contract_route`, `cross_plan_route`, and others) are about the Node and
 * Python layers, and they still hold: nothing there gained a signing path.
 *
 * TRANSACTIONS ARE PASSED THROUGH, NEVER CONSTRUCTED. `signAndSend` takes the
 * base64 that `bot/core/meme_swap.py` received from Jupiter and never decoded.
 * This file re-encodes it to base58 because that is the wire format Phantom's
 * `request()` API wants, and re-encoding is not rewriting — the bytes the user
 * approves are the bytes the builder fetched. It cannot construct a transfer,
 * choose a recipient, or alter an amount, and there is no code path here that
 * could: what is displayed for review is decoded from the same terms the
 * server sent, not from the transaction.
 *
 * A missing injection is not "Phantom is not installed". The Phantom app on a
 * phone does not inject window.phantom / window.solana into Chrome. That
 * injection exists in the desktop extension and in Phantom's own in-app
 * browser. detectionState is the reading the Account page renders: a provider
 * can connect and verify, a phone browser is offered the same Phantom browse
 * link wallet-link.html already uses (or a pasted address, Watch only), and a
 * desktop browser with no extension may be told to install or enable one.
 *
 * Exposes window.RCSolanaWallet =
 *   { available, connect, signMessage, signAndSend, base58, SIGN_METHOD,
 *     detectionState, solanaLinkOffer, phantomBrowseHref, isSolanaAddress,
 *     prepareWatchOnly, watchOnlyPostBody }.
 */
(function (root, factory) {
  const api = factory();
  // Exported for tests as well as the page. The base58 encoder in particular
  // is pure and has known vectors, and a signing path whose only coverage is
  // "a human clicked it once" is the shape CLAUDE.md warns about.
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  if (root) root.RCSolanaWallet = api;
}(typeof window !== 'undefined' ? window : null, function () {
  'use strict';

  function win() {
    return typeof window !== 'undefined' ? window : {};
  }

  function provider() {
    const w = win();
    if (w.phantom && w.phantom.solana && w.phantom.solana.isPhantom) {
      return w.phantom.solana;
    }
    if (w.backpack && w.backpack.isBackpack) return w.backpack;
    if (w.solana) return w.solana; // Phantom (legacy) or other injected
    return null;
  }

  function available() {
    return !!provider();
  }

  function navigatorUA() {
    const n = win().navigator;
    return (n && n.userAgent) || '';
  }

  function pageHref() {
    const loc = win().location;
    return (loc && loc.href) || '';
  }

  // Same universal link as public/wallet-link.html. It reopens THIS page
  // inside Phantom's in-app browser, which is where the provider is injected.
  // The encoded page is the path; `ref` is Phantom's own query, not part of
  // the page URL.
  function phantomBrowseHref(pageUrl) {
    const full = String(pageUrl || '');
    if (!full) return null;
    let origin = '';
    try { origin = new URL(full).origin; } catch (e) { return null; }
    if (!origin || origin === 'null') return null;
    return 'https://phantom.app/ul/browse/' + encodeURIComponent(full)
      + '?ref=' + encodeURIComponent(origin);
  }

  const MOBILE_UA = /Android|iPhone|iPad|iPod|Mobile/i;

  /**
   * What this page can actually see.
   *
   * `provider: true` means an injected Solana provider is present — Connect
   * & verify can use it, including inside Phantom's own browser on a phone.
   * `provider: false` on a mobile browser is "this browser has no injection",
   * which is the normal state of Chrome with the Phantom app installed.
   * Desktop with no extension is the case where "install or enable" is fair.
   */
  function detectionState(ctx) {
    const c = ctx || {};
    const has = Object.prototype.hasOwnProperty.call(c, 'provider')
      ? !!c.provider
      : available();
    const ua = c.userAgent != null ? String(c.userAgent) : navigatorUA();
    const pageUrl = c.pageUrl != null ? String(c.pageUrl) : pageHref();
    const mobile = MOBILE_UA.test(ua);
    if (has) {
      return {
        connect: true,
        mobile: mobile,
        sayInstall: false,
        message: '',
        openInPhantom: null,
      };
    }
    if (mobile) {
      return {
        connect: false,
        mobile: true,
        sayInstall: false,
        message: 'This page cannot see a Solana wallet in this browser. '
          + 'Open this page in Phantom\'s browser to connect and verify.',
        openInPhantom: phantomBrowseHref(pageUrl),
      };
    }
    return {
      connect: false,
      mobile: false,
      sayInstall: true,
      message: 'No Solana wallet extension in this browser. '
        + 'Install or enable Phantom or Backpack, then reload.',
      openInPhantom: null,
    };
  }

  /** Account-panel copy and the one control that can succeed from this state. */
  function solanaLinkOffer(state) {
    const s = state || detectionState();
    if (s.connect) {
      return {
        intro: 'Also on Solana? Connect Phantom or Backpack to verify ownership, '
          + 'or paste an address to watch read-only. Either way it never signs a transaction.',
        primary: { kind: 'connect', label: 'Connect & verify' },
        toast: null,
        showInstall: false,
        watchOnly: true,
      };
    }
    if (s.mobile) {
      const intro = s.message
        + ' Or paste an address and Watch only. Nothing here signs a transaction.';
      return {
        intro: intro,
        primary: s.openInPhantom
          ? { kind: 'open', label: 'Open in Phantom', href: s.openInPhantom }
          : { kind: 'connect', label: 'Connect & verify' },
        toast: s.openInPhantom ? null : intro,
        showInstall: false,
        watchOnly: true,
      };
    }
    const intro = s.message
      + ' You can still paste an address and Watch only. Nothing here signs a transaction.';
    return {
      intro: intro,
      primary: { kind: 'connect', label: 'Connect & verify' },
      toast: s.message,
      showInstall: true,
      watchOnly: true,
    };
  }

  async function connect() {
    const p = provider();
    if (!p) {
      const st = detectionState({ provider: false });
      const e = new Error(st.message);
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

  const B58 = '123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz';
  // Same predicate as app/lib/solana.js isSolanaAddress. The browser cannot
  // require that module; solana_wallet_detect.test.js compares the two on a
  // shared set of inputs so a drift fails the test rather than linking a
  // different string than the server would accept.
  const B58_MAP = Object.fromEntries([...B58].map((c, i) => [c, BigInt(i)]));

  function isSolanaAddress(addr) {
    const s = String(addr || '');
    if (s.length < 32 || s.length > 44) return false;
    let n = 0n;
    for (const c of s) {
      const v = B58_MAP[c];
      if (v === undefined) return false;
      n = n * 58n + v;
    }
    let bytes = 0;
    for (const c of s) { if (c === '1') bytes++; else break; }
    let m = n;
    while (m > 0n) { bytes++; m >>= 8n; }
    return bytes === 32;
  }

  /**
   * Watch only is a pasted public address. It does not read window, does not
   * connect a provider, and does not sign. A bad address is refused whole —
   * it is not trimmed down, rewritten, or replaced with whatever wallet
   * happens to be injected.
   */
  function prepareWatchOnly(address) {
    const addr = String(address == null ? '' : address).trim();
    if (!addr) return { ok: false, reason: 'empty', address: null };
    if (!isSolanaAddress(addr)) return { ok: false, reason: 'invalid', address: null };
    return { ok: true, reason: null, address: addr };
  }

  function watchOnlyPostBody(address) {
    const prep = prepareWatchOnly(address);
    if (!prep.ok) return { post: false, reason: prep.reason };
    return { post: true, body: { address: prep.address } };
  }

  /**
   * Bytes -> base58 (Bitcoin alphabet, the one Solana uses).
   *
   * Long division on a digit array rather than BigInt: a serialized swap
   * transaction is ~1.2kB, which is a ~10000-bit integer, and this has to work
   * in whatever browser the user brought.
   *
   * LEADING ZERO BYTES ARE NOT OPTIONAL. Each one encodes as a literal '1',
   * and they carry no numeric value — so an implementation that converts to a
   * number and back silently drops them and produces a *valid-looking* string
   * that decodes to a different, shorter transaction. Solana public keys and
   * transaction buffers begin with zero bytes routinely.
   */
  function base58(bytes) {
    const b = bytes instanceof Uint8Array ? bytes : new Uint8Array(bytes || []);
    let zeros = 0;
    while (zeros < b.length && b[zeros] === 0) zeros++;
    // Seeded EMPTY, not [0]. Seeded with a zero digit, an all-zero input
    // renders one '1' per zero byte and then one more for the seed — so a
    // single zero byte encoded as '11', which is the encoding of two.
    const digits = [];
    for (let i = zeros; i < b.length; i++) {
      let carry = b[i];
      for (let j = 0; j < digits.length; j++) {
        carry += digits[j] << 8;
        digits[j] = carry % 58;
        carry = (carry / 58) | 0;
      }
      while (carry > 0) { digits.push(carry % 58); carry = (carry / 58) | 0; }
    }
    let out = '';
    for (let i = 0; i < zeros; i++) out += '1';
    for (let i = digits.length - 1; i >= 0; i--) out += B58[digits[i]];
    return out;
  }

  function fromBase64(b64) {
    const bin = atob(String(b64));
    const out = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
    return out;
  }

  const SIGN_METHOD = 'signAndSendTransaction';

  /**
   * Ask the USER'S wallet to sign and broadcast a server-built transaction.
   *
   * `b64` is passed through from `meme_swap.build_swap`, which took it from
   * Jupiter and never decoded it. Returns `{ address, signature }` where
   * `signature` is the transaction signature — an on-chain fact, not a login
   * proof, and named the way the wallet names it.
   *
   * THE RETURN VALUE IS NOT A RECEIPT. A signature means the wallet accepted
   * and forwarded the transaction; it does not mean the transaction confirmed,
   * and it certainly does not mean the swap filled at the quoted price. Any
   * caller that renders this as "done" is making a claim the wallet did not.
   *
   * Every failure throws. There is no branch that returns a falsy signature or
   * an empty object, because on this path a caller that reads "no error" as
   * "sent" would report a swap that never left the browser.
   */
  async function signAndSend(b64) {
    if (!b64 || typeof b64 !== 'string') {
      const e = new Error('No transaction to sign.');
      e.code = 'NO_TRANSACTION';
      throw e;
    }
    const { provider: p, address } = await connect();
    if (typeof p.request !== 'function') {
      const e = new Error('This wallet cannot send transactions from the browser.');
      e.code = 'NO_SEND_SUPPORT';
      throw e;
    }
    let message;
    try {
      message = base58(fromBase64(b64));
    } catch (err) {
      // A transaction we cannot even decode is one we must not forward: the
      // wallet would be asked to approve something neither side has read.
      const e = new Error('The transaction could not be decoded — nothing was sent.');
      e.code = 'BAD_TRANSACTION';
      throw e;
    }
    if (!message) {
      const e = new Error('The transaction decoded to nothing — nothing was sent.');
      e.code = 'BAD_TRANSACTION';
      throw e;
    }
    const res = await p.request({ method: SIGN_METHOD, params: { message: message } });
    const sig = res && res.signature;
    if (!sig || typeof sig !== 'string') {
      // Absent is not success. A wallet that returned no signature has not
      // told us it sent anything, and saying "sent" here would invent the one
      // fact the user most needs to be true.
      const e = new Error('The wallet returned no signature — treat this swap as NOT sent.');
      e.code = 'NO_SIGNATURE';
      throw e;
    }
    return { address: address, signature: sig };
  }

  return {
    available: available,
    connect: connect,
    signMessage: signMessage,
    signAndSend: signAndSend,
    base58: base58,
    SIGN_METHOD: SIGN_METHOD,
    detectionState: detectionState,
    solanaLinkOffer: solanaLinkOffer,
    phantomBrowseHref: phantomBrowseHref,
    isSolanaAddress: isSolanaAddress,
    prepareWatchOnly: prepareWatchOnly,
    watchOnlyPostBody: watchOnlyPostBody,
  };
}));
