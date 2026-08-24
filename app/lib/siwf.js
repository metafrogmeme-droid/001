'use strict';
/**
 * Sign In With Farcaster — the server half.
 *
 * A Mini App asks its host to sign a SIWE message; the host returns
 * `{message, signature}` and the app posts them here. If they check out, the
 * holder controls the Farcaster account named in the message and we mint a
 * RUNECLAW session for it.
 *
 * THE THREE CHECKS, AND WHY EACH ONE IS LOAD-BEARING. A signature is only
 * evidence of what it is bound to, and SIWF binds three things. Drop any one
 * and the remaining two still "verify" — that is what makes this class of bug
 * so quiet.
 *
 *   nonce   OURS, single-use, short-lived. Without it a signature captured
 *           once is a permanent credential: replay it tomorrow and the same
 *           verification passes. `consumeNonce` is the only reason presenting
 *           the same message twice fails.
 *
 *   domain  MUST be ours. The Farcaster client sets it from the URL it is
 *           rendering, so a signature obtained on someone else's Mini App is
 *           valid FOR THEIR DOMAIN — and would sail through signature
 *           verification here. Checking it is what stops any other Mini App
 *           from logging its visitors into RUNECLAW as themselves.
 *
 *   fid     what the message CLAIMS. It is only worth anything because the
 *           verifier confirmed the signer is authorised for that fid.
 *
 * WHAT IS TRUSTED, STATED PLAINLY. Signature verification is delegated to
 * Farcaster's auth service, which is what the official SDK does. That service
 * is therefore in the trust path: if it were compromised or wrong, it could
 * assert an fid the signer does not control, and this module would believe it.
 *
 * That is a deliberate, proportionate choice for a PAPER-TRADING competition —
 * the stakes are virtual and the harm of a forged identity is reputational,
 * not financial — and it is not a choice that should be inherited silently by
 * anything that later moves money. `verifySignIn` takes its verifier as an
 * argument for exactly that reason: replacing the hosted check with a local
 * one (recover the signer, resolve the fid's authorised keys from Farcaster's
 * on-chain registries) is a swap of one function, not a rewrite.
 *
 * The nonce and domain checks above are OURS either way, and they are the two
 * that stop replay and cross-domain reuse. A compromised verifier could forge
 * an identity; it could not reuse a signature or borrow one from another site.
 */

const crypto = require('crypto');

/** Where the hosted verifier lives. Overridable for tests and for forks. */
const AUTH_ORIGIN = process.env.FARCASTER_AUTH_ORIGIN || 'https://auth.farcaster.xyz';

/** How long an issued nonce stays usable. Long enough to sign, short enough
 *  that a captured one is worthless by the time it is replayed. */
const NONCE_TTL_MS = 5 * 60 * 1000;

/**
 * SIWE nonces must be alphanumeric and at least 8 characters. 32 hex chars of
 * CSPRNG output is comfortably past that and has no ambiguous characters.
 */
function newNonce() {
  return crypto.randomBytes(16).toString('hex');
}

/**
 * Parse the fields we check out of a SIWE message.
 *
 * Deliberately NOT a full SIWE parser: this reads the three lines the checks
 * below depend on and returns null for anything it cannot read. A partial
 * parse that guessed at a missing field would be a check that passes without
 * having looked.
 *
 * The shape is fixed by the SIWE spec:
 *
 *   <domain> wants you to sign in with your Ethereum account:
 *   <address>
 *   ...
 *   URI: https://...
 *   Nonce: <nonce>
 *   ...
 *   Resources:
 *   - farcaster://fid/<fid>
 */
function parseSiwe(message) {
  const text = String(message == null ? '' : message);
  if (!text) return null;

  const domain = (text.match(/^([^\s]+) wants you to sign in with your Ethereum account:/m) || [])[1];
  const address = (text.match(/^(0x[0-9a-fA-F]{40})$/m) || [])[1];
  const nonce = (text.match(/^Nonce:\s*(\S+)\s*$/m) || [])[1];
  const uri = (text.match(/^URI:\s*(\S+)\s*$/m) || [])[1];
  const expiry = (text.match(/^Expiration Time:\s*(\S+)\s*$/m) || [])[1];
  // The fid rides in Resources as a farcaster:// URI. It is a CLAIM until the
  // verifier confirms the signer is authorised for it.
  const fidRaw = (text.match(/farcaster:\/\/fid\/(\d+)/) || [])[1];

  if (!domain || !nonce || !fidRaw) return null;
  return {
    domain,
    address: address || null,
    nonce,
    uri: uri || null,
    fid: Number(fidRaw),
    expirationTime: expiry || null,
  };
}

/**
 * Does the message's domain match a host we actually serve?
 *
 * Compared on the HOST, not the whole origin: SIWE carries `example.com` or
 * `example.com:8443`, never a scheme. An `endsWith` would accept
 * `evil-example.com`, so this is exact or a labelled subdomain.
 */
function domainMatches(messageDomain, allowed) {
  const d = String(messageDomain || '').toLowerCase().replace(/:\d+$/, '');
  if (!d) return false;
  for (const raw of (allowed || [])) {
    const a = String(raw || '').toLowerCase()
      .replace(/^https?:\/\//, '').replace(/\/.*$/, '').replace(/:\d+$/, '');
    if (!a) continue;
    if (d === a) return true;
    if (d.endsWith('.' + a)) return true;   // a real subdomain, dot included
  }
  return false;
}

/**
 * Ask the hosted verifier whether this signature is valid for this message.
 *
 * Returns `{ ok: true, fid }` or `{ ok: false, reason }`. NEVER throws for a
 * failed verification — a rejected sign-in is an answer, not an exception —
 * but a network fault DOES reject, because "we could not check" is not the
 * same as "it is invalid" and must not be reported as a bad signature.
 */
async function verifyWithService({ domain, message, signature }, deps) {
  const d = deps || {};
  const fetchImpl = d.fetch || globalThis.fetch;
  const origin = d.authOrigin || AUTH_ORIGIN;
  if (typeof fetchImpl !== 'function') throw new Error('siwf_no_fetch');

  const res = await fetchImpl(`${origin}/siwf/parse`, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ domain, message, signature, acceptAuthAddress: true }),
  });

  // A non-2xx from the verifier is ambiguous by design: 400 means it read the
  // request and rejected the signature; anything else means we could not get
  // an answer. They are different outcomes and the caller renders them
  // differently, so they are not collapsed here.
  if (res.status === 400) {
    let reason = 'invalid_signature';
    try {
      const body = await res.json();
      if (body && body.message) reason = String(body.message).slice(0, 120);
    } catch (e) { /* keep the generic reason */ }
    return { ok: false, reason };
  }
  if (!res.ok) throw new Error(`siwf_verifier_${res.status}`);

  let body;
  try { body = await res.json(); } catch (e) { throw new Error('siwf_verifier_bad_json'); }

  // A 200 with no fid is not a pass. Reading a missing field as success is the
  // shape this whole repo is a correction for, and here it would authenticate
  // a session with no identity behind it.
  const fid = body && (body.fid != null ? Number(body.fid)
    : (body.data && body.data.fid != null ? Number(body.data.fid) : null));
  if (!Number.isInteger(fid) || fid <= 0) throw new Error('siwf_verifier_no_fid');
  return { ok: true, fid };
}

/**
 * The whole check, in the order that fails cheapest first.
 *
 * `store` is the single-use nonce store; `verifier` is the signature check.
 * Both are injected so the tests below can drive every branch without a
 * network, and so the trust decision documented at the top of this file is one
 * argument rather than a hardcoded dependency.
 *
 * Resolves to `{ ok, fid }` or `{ ok: false, reason }`. Rejects ONLY when the
 * verifier could not be reached — an unreadable answer must not render as a
 * rejected sign-in.
 */
async function verifySignIn({ message, signature }, opts) {
  const o = opts || {};
  const parsed = parseSiwe(message);
  if (!parsed) return { ok: false, reason: 'unparseable_message' };

  if (!signature || typeof signature !== 'string') {
    return { ok: false, reason: 'missing_signature' };
  }

  // DOMAIN before anything expensive. A signature for another Mini App's
  // domain is valid — for them — and this is the only thing that stops it
  // being spent here.
  if (!domainMatches(parsed.domain, o.allowedDomains)) {
    return { ok: false, reason: 'domain_mismatch' };
  }

  if (parsed.expirationTime) {
    const exp = new Date(parsed.expirationTime).getTime();
    // An unreadable expiry is not an expired one, and not a valid one either:
    // we cannot tell, so we do not accept it.
    if (!isFinite(exp)) return { ok: false, reason: 'unreadable_expiry' };
    if (exp <= (o.nowMs || Date.now())) return { ok: false, reason: 'expired' };
  }

  // NONCE, consumed here. Single-use is what makes a captured signature
  // worthless the second time it is presented.
  const consumed = await o.store.consume(parsed.nonce, o.nowMs);
  if (!consumed) return { ok: false, reason: 'unknown_or_used_nonce' };

  const verified = await (o.verifier || verifyWithService)(
    { domain: parsed.domain, message, signature }, o);
  if (!verified.ok) return { ok: false, reason: verified.reason || 'invalid_signature' };

  // The verifier's fid is authoritative; the message's is a claim. If they
  // disagree, something is wrong enough that we do not guess which to believe.
  if (Number(verified.fid) !== Number(parsed.fid)) {
    return { ok: false, reason: 'fid_mismatch' };
  }

  return { ok: true, fid: Number(verified.fid), address: parsed.address };
}

module.exports = {
  newNonce,
  parseSiwe,
  domainMatches,
  verifyWithService,
  verifySignIn,
  NONCE_TTL_MS,
  AUTH_ORIGIN,
};
