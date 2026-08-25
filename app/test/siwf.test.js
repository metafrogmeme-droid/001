'use strict';
/**
 * Sign In With Farcaster, driven at every way it can be abused.
 *
 * This is the first thing in the repo that turns a stranger's assertion into a
 * RUNECLAW session, so the tests are written against the ATTACKS rather than
 * the happy path. A signature is only evidence of what it is bound to, and
 * SIWF binds three things — nonce, domain, fid. Each of the three has its own
 * section here because dropping any one leaves the other two still verifying,
 * which is what makes this class of bug quiet enough to ship.
 *
 * The happy path gets one test. The ways in get eleven.
 */

const test = require('node:test');
const assert = require('node:assert');
const S = require('../lib/siwf');

const DOMAIN = 'www.humanoid-traders.com';
const FID = 3347978;
const NOW = Date.UTC(2026, 7, 24, 13, 0, 0);

function siwe(over) {
  const o = Object.assign({ domain: DOMAIN, nonce: 'abc123def456', fid: FID, exp: null }, over || {});
  return [
    `${o.domain} wants you to sign in with your Ethereum account:`,
    '0x' + 'a'.repeat(40),
    '',
    'Farcaster Auth',
    '',
    `URI: https://${o.domain}`,
    'Version: 1',
    'Chain ID: 10',
    `Nonce: ${o.nonce}`,
    'Issued At: 2026-08-24T12:59:00Z',
    ...(o.exp ? [`Expiration Time: ${o.exp}`] : []),
    'Resources:',
    `- farcaster://fid/${o.fid}`,
  ].join('\n');
}

/** A nonce store that accepts one specific nonce exactly once. */
function storeWith(nonce) {
  let left = new Set(nonce ? [nonce] : []);
  return {
    consume: async (n) => {
      if (!left.has(n)) return false;
      left.delete(n);
      return true;
    },
    _remaining: () => left.size,
  };
}

const passVerifier = async () => ({ ok: true, fid: FID });
const base = (over) => Object.assign({
  allowedDomains: [DOMAIN],
  store: storeWith('abc123def456'),
  verifier: passVerifier,
  nowMs: NOW,
}, over || {});

// ── it works ──────────────────────────────────────────────────────────────

test('a well-formed sign-in for our domain with a fresh nonce succeeds', async () => {
  const r = await S.verifySignIn({ message: siwe(), signature: '0xsig' }, base());
  assert.equal(r.ok, true);
  assert.equal(r.fid, FID);
});

// ── nonce: without single-use, a captured signature is a permanent key ─────

test('the same signature cannot be presented twice', async () => {
  // THE REPLAY. Without a consumed nonce, a message captured once verifies
  // forever — every check still passes, because nothing about the signature
  // changes on the second presentation. This is the only test that catches it.
  const opts = base();
  const msg = siwe();
  const first = await S.verifySignIn({ message: msg, signature: '0xsig' }, opts);
  assert.equal(first.ok, true);

  const second = await S.verifySignIn({ message: msg, signature: '0xsig' }, opts);
  assert.equal(second.ok, false, 'a replayed sign-in was accepted');
  assert.equal(second.reason, 'unknown_or_used_nonce');
});

test('a nonce we never issued is refused', async () => {
  // A self-chosen nonce means the attacker picks what the signature is bound
  // to, which is the same as it being bound to nothing.
  const r = await S.verifySignIn(
    { message: siwe({ nonce: 'attackerchosen' }), signature: '0xsig' },
    base({ store: storeWith('abc123def456') }));
  assert.equal(r.ok, false);
  assert.equal(r.reason, 'unknown_or_used_nonce');
});

test('the nonce is consumed BEFORE the signature is checked', async () => {
  // Ordering matters: a verifier that is slow or unreachable must not leave a
  // nonce spendable. If consumption happened after verification, an attacker
  // could hammer a captured message while the verifier was down and spend the
  // nonce the moment it recovered.
  const store = storeWith('abc123def456');
  await assert.rejects(
    () => S.verifySignIn({ message: siwe(), signature: '0xsig' },
      base({ store, verifier: async () => { throw new Error('verifier down'); } })),
    /verifier down/);
  assert.equal(store._remaining(), 0, 'the nonce survived a failed verification');
});

// ── domain: the cross-site reuse nobody sees coming ───────────────────────

test('a signature for someone ELSE\'s Mini App is refused', async () => {
  // The quiet one. The Farcaster client sets the domain from the URL it is
  // rendering, so a signature obtained in another Mini App is genuinely VALID
  // — for that app. It would pass signature verification here unchanged. Only
  // the domain check stops any other Mini App from logging its visitors into
  // RUNECLAW as themselves.
  const r = await S.verifySignIn(
    { message: siwe({ domain: 'someone-elses-app.xyz' }), signature: '0xsig' },
    base({ store: storeWith('abc123def456') }));
  assert.equal(r.ok, false);
  assert.equal(r.reason, 'domain_mismatch');
});

test('a lookalike domain is not our domain', async () => {
  // `endsWith` would accept this, which is why the check is exact-or-labelled.
  assert.equal(S.domainMatches('evil-humanoid-traders.com', ['humanoid-traders.com']), false);
  assert.equal(S.domainMatches('humanoid-traders.com.evil.xyz', ['humanoid-traders.com']), false);
  assert.equal(S.domainMatches('', ['humanoid-traders.com']), false);
});

test('a real subdomain and a port are accepted', () => {
  assert.equal(S.domainMatches('app.humanoid-traders.com', ['humanoid-traders.com']), true);
  assert.equal(S.domainMatches('humanoid-traders.com:8443', ['humanoid-traders.com']), true);
  // The allowlist may be given as a full origin; it is compared on the host.
  assert.equal(S.domainMatches('www.humanoid-traders.com', ['https://www.humanoid-traders.com/']), true);
});

test('no allowlist accepts nothing', () => {
  // Fail closed. An unconfigured origin must not mean "any domain will do" —
  // that is the fail-open default CLAUDE.md records on the guardian rollup,
  // and here it would accept a signature from every Mini App in existence.
  assert.equal(S.domainMatches('www.humanoid-traders.com', []), false);
  assert.equal(S.domainMatches('www.humanoid-traders.com', undefined), false);
});

// ── fid: the claim vs the confirmation ────────────────────────────────────

test('the verifier\'s fid wins, and a disagreement is refused outright', async () => {
  // The message CLAIMS an fid; the verifier CONFIRMS one. If they differ we do
  // not pick a winner — that would be guessing which half of a contradiction
  // to authenticate.
  const r = await S.verifySignIn({ message: siwe({ fid: 999 }), signature: '0xsig' },
    base({ store: storeWith('abc123def456') }));
  assert.equal(r.ok, false);
  assert.equal(r.reason, 'fid_mismatch');
});

// ── malformed input is refused, never guessed at ──────────────────────────

test('an unparseable message is refused rather than partially read', async () => {
  for (const bad of ['', null, undefined, 'hello', {}, 'Nonce: x']) {
    const r = await S.verifySignIn({ message: bad, signature: '0xsig' },
      base({ store: storeWith('abc123def456') }));
    assert.equal(r.ok, false, `verifySignIn accepted ${JSON.stringify(bad)}`);
    assert.equal(r.reason, 'unparseable_message');
  }
});

test('a missing signature is refused before anything else runs', async () => {
  const store = storeWith('abc123def456');
  const r = await S.verifySignIn({ message: siwe(), signature: null }, base({ store }));
  assert.equal(r.ok, false);
  assert.equal(r.reason, 'missing_signature');
  assert.equal(store._remaining(), 1, 'a nonce was burned by a request with no signature');
});

test('an expired message is refused, and an unreadable expiry is NOT treated as valid', async () => {
  const expired = await S.verifySignIn(
    { message: siwe({ exp: '2026-08-24T12:00:00Z' }), signature: '0xsig' },
    base({ store: storeWith('abc123def456') }));
  assert.equal(expired.reason, 'expired');

  // "We cannot tell" is not "it is fine". An unreadable expiry on a credential
  // is exactly the absent-is-never-a-measurement rule.
  const garbled = await S.verifySignIn(
    { message: siwe({ exp: 'whenever' }), signature: '0xsig' },
    base({ store: storeWith('abc123def456') }));
  assert.equal(garbled.reason, 'unreadable_expiry');
});

// ── a verifier that cannot be reached is not a rejection ──────────────────

test('an unreachable verifier REJECTS rather than reporting a bad signature', async () => {
  // "We could not check" and "it is invalid" are different sentences. Folding
  // the first into the second would tell a legitimate user their signature was
  // refused when in fact our dependency was down — and it would look identical
  // to a real rejection in the logs.
  await assert.rejects(
    () => S.verifySignIn({ message: siwe(), signature: '0xsig' },
      base({ store: storeWith('abc123def456'), verifier: async () => { throw new Error('ECONNRESET'); } })),
    /ECONNRESET/);
});

/** A JWT with the given claims, unsigned — only the payload is ever read. */
function jwt(claims) {
  const b64 = (o) => Buffer.from(JSON.stringify(o)).toString('base64url');
  return `${b64({ alg: 'none', typ: 'JWT' })}.${b64(claims)}.sig`;
}

/**
 * THE ENDPOINT AND THE RESPONSE SHAPE, WHICH WERE BOTH WRONG.
 *
 * The first version POSTed to `/siwf/parse` and read `{fid}` off the body.
 * The real service is `POST /verify-siwf` returning `{token}` — a JWT whose
 * `sub` is the fid. I inferred both instead of reading the client that calls
 * it, so every real sign-in 404'd and surfaced to the operator as
 * "Sign-in is unavailable right now — this is on our side."
 *
 * Nothing caught it because every other test in this file injects its own
 * verifier: the contract with the outside world was the one thing the suite
 * could not see. These pin it.
 */
test('it posts to /verify-siwf, not a path somebody guessed', async () => {
  let calledUrl = null;
  const fakeFetch = async (url) => {
    calledUrl = url;
    return { ok: true, status: 200, json: async () => ({ token: jwt({ sub: FID, aud: DOMAIN }) }) };
  };
  await S.verifyWithService({ domain: DOMAIN, message: siwe(), signature: '0xsig' },
    { fetch: fakeFetch, authOrigin: 'https://auth.example' });
  assert.equal(calledUrl, 'https://auth.example/verify-siwf',
    'the verifier path is wrong — every real sign-in will 404 and render as an '
    + 'outage on our side');
});

test('the fid comes out of the token, not a top-level field', async () => {
  const fakeFetch = async () => ({
    ok: true, status: 200, json: async () => ({ token: jwt({ sub: FID, aud: DOMAIN }) }),
  });
  const r = await S.verifyWithService({ domain: DOMAIN, message: siwe(), signature: '0xsig' },
    { fetch: fakeFetch });
  assert.equal(r.ok, true);
  assert.equal(r.fid, FID);
});

test('the service refusing a signature is a rejection, not an outage', async () => {
  // Its own shape: {valid:false, message}. A rejection must reach the caller
  // as 401-with-a-reason, never as 503 — telling someone our service is down
  // when their signature was refused sends them to wait instead of retry.
  const fakeFetch = async () => ({
    ok: true, status: 200, json: async () => ({ valid: false, message: 'Invalid signature' }),
  });
  const r = await S.verifyWithService({ domain: DOMAIN, message: siwe(), signature: '0xsig' },
    { fetch: fakeFetch });
  assert.equal(r.ok, false);
  assert.match(r.reason, /Invalid signature/);
});

test('a token minted for another domain is refused', async () => {
  // `aud` binds the token to the domain it was issued for. A token for
  // someone else's app is not evidence about a visitor to ours, and this is
  // the cheap check that says so.
  const fakeFetch = async () => ({
    ok: true, status: 200,
    json: async () => ({ token: jwt({ sub: FID, aud: 'someone-elses-app.xyz' }) }),
  });
  const r = await S.verifyWithService({ domain: DOMAIN, message: siwe(), signature: '0xsig' },
    { fetch: fakeFetch });
  assert.equal(r.ok, false);
  assert.equal(r.reason, 'audience_mismatch');
});

test('a 200 with no token is not a pass', async () => {
  // Reading a missing field as success would authenticate a session with no
  // identity behind it.
  const fakeFetch = async () => ({ ok: true, status: 200, json: async () => ({ success: true }) });
  await assert.rejects(
    () => S.verifyWithService({ domain: DOMAIN, message: siwe(), signature: '0xsig' },
      { fetch: fakeFetch }),
    /no_token/);
});

test('an unreadable token is bad_token, not a confusing no_fid', async () => {
  for (const bad of ['not-a-jwt', 'a.b', 'a.!!!.c', '']) {
    const fakeFetch = async () => ({ ok: true, status: 200, json: async () => ({ token: bad }) });
    await assert.rejects(
      () => S.verifyWithService({ domain: DOMAIN, message: siwe(), signature: '0xsig' },
        { fetch: fakeFetch }),
      /bad_token|no_token/, `token ${JSON.stringify(bad)} was accepted`);
  }
});

test('a token with no sub is refused', async () => {
  const fakeFetch = async () => ({
    ok: true, status: 200, json: async () => ({ token: jwt({ aud: DOMAIN }) }),
  });
  await assert.rejects(
    () => S.verifyWithService({ domain: DOMAIN, message: siwe(), signature: '0xsig' },
      { fetch: fakeFetch }),
    /no_fid/);
});

test('a non-2xx from the verifier throws rather than resolving false', async () => {
  // "We could not check" is not "it is invalid". This is the path that turned
  // a wrong URL into a 503 — correctly, which is how the outage was legible
  // at all once somebody looked.
  for (const status of [404, 500, 503]) {
    const fakeFetch = async () => ({ ok: false, status, json: async () => ({}) });
    await assert.rejects(
      () => S.verifyWithService({ domain: DOMAIN, message: siwe(), signature: '0xsig' },
        { fetch: fakeFetch }),
      new RegExp(`siwf_verifier_${status}`));
  }
});

test('decodeJwtPayload refuses anything that is not a real payload', () => {
  assert.equal(S.decodeJwtPayload('a.b'), null);
  assert.equal(S.decodeJwtPayload(''), null);
  assert.equal(S.decodeJwtPayload(null), null);
  // A JSON scalar in the payload position is not a claims object.
  const b64 = (v) => Buffer.from(JSON.stringify(v)).toString('base64url');
  assert.equal(S.decodeJwtPayload(`x.${b64(42)}.y`), null);
  assert.deepEqual(S.decodeJwtPayload(`x.${b64({ sub: 7 })}.y`), { sub: 7 });
});

// ── the nonce itself ──────────────────────────────────────────────────────

test('nonces are alphanumeric, long, and do not repeat', () => {
  const seen = new Set();
  for (let i = 0; i < 500; i += 1) {
    const n = S.newNonce();
    assert.match(n, /^[a-z0-9]{16,}$/, 'SIWE requires an alphanumeric nonce of 8+ chars');
    assert.ok(!seen.has(n), 'newNonce repeated within 500 draws');
    seen.add(n);
  }
});
