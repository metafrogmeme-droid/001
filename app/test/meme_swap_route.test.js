'use strict';
/**
 * POST /api/meme/swap/build — the web layer in front of the swap builder.
 *
 * Two claims are worth testing here and they are different in kind. The
 * validation is real logic, so it is DRIVEN. The wiring — authed, rate
 * limited, identity resolved server-side, never signs — is a property of where
 * calls sit rather than what they return, so it is scanned; that is the split
 * CLAUDE.md draws, and the scan is not standing in for behaviour nothing else
 * covers (`swap_sign_model` and `solana_wallet_sign` cover the deciding).
 */

process.env.JWT_SECRET = process.env.JWT_SECRET || 'j'.repeat(64);

const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

const ROUTE = path.join(__dirname, '..', 'routes', 'meme.js');
const src = fs.readFileSync(ROUTE, 'utf8');
const { validateBuildRequest } = require(ROUTE);

const MINT = 'So11111111111111111111111111111111111111112';
const WALLET = '7xKXtg2CW87d97TXJSDpbD5jBkheTqA83TZRuJosgAsU';
const good = (over = {}) => ({ mint: MINT, user_public_key: WALLET, size_usd: 25, ...over });

// ── the validation, driven ────────────────────────────────────────────────

test('a well-formed request passes and is normalised', () => {
  const r = validateBuildRequest(good({ mint: '  ' + MINT + ' ', size_usd: '25' }));
  assert.strictEqual(r.ok, true);
  assert.strictEqual(r.value.mint, MINT, 'trimmed');
  assert.strictEqual(r.value.size_usd, 25, 'numeric');
});

test('a mint that is not a Solana address is refused', () => {
  // 0, O, I and l are absent from the base58 alphabet, and an EVM address is
  // the shape a user is most likely to paste by mistake.
  for (const bad of ['', '   ', 'not-a-mint', MINT.slice(0, 20),
    '0x' + 'a'.repeat(40), MINT + MINT, 'O' + MINT.slice(1),
    'l' + MINT.slice(1), null, undefined, 42, {}]) {
    const r = validateBuildRequest(good({ mint: bad }));
    assert.strictEqual(r.ok, false, JSON.stringify(bad));
    assert.strictEqual(r.error, 'bad_mint');
  }
});

test('a missing or malformed wallet is refused before anything is built', () => {
  for (const bad of ['', '0x' + 'a'.repeat(40), 'nope', null, undefined]) {
    const r = validateBuildRequest(good({ user_public_key: bad }));
    assert.strictEqual(r.ok, false, JSON.stringify(bad));
    assert.strictEqual(r.error, 'bad_wallet');
    assert.match(r.detail, /signs nothing/);
  }
});

test('a size that is absent, zero, negative or not a number is refused', () => {
  for (const bad of [undefined, null, '', 0, -1, -0.01, 'abc', NaN, Infinity,
    -Infinity, {}, []]) {
    const r = validateBuildRequest(good({ size_usd: bad }));
    assert.strictEqual(r.ok, false, JSON.stringify(bad));
    assert.strictEqual(r.error, 'bad_size');
  }
});

test('a partly-numeric size is refused, not truncated', () => {
  // parseFloat('25abc') is 25. A size that was partly garbage is a size the
  // user did not type, and quietly trading the prefix is worse than refusing.
  for (const bad of ['25abc', '2 5', '25,000']) {
    assert.strictEqual(validateBuildRequest(good({ size_usd: bad })).error,
      'bad_size', bad);
  }
  assert.strictEqual(validateBuildRequest(good({ size_usd: '25.5' })).ok, true);
});

test('every refusal names itself and explains itself', () => {
  for (const b of [{}, good({ mint: 'x' }), good({ user_public_key: 'x' }),
    good({ size_usd: 0 })]) {
    const r = validateBuildRequest(b);
    assert.strictEqual(r.ok, false);
    assert.ok(r.error && r.detail, JSON.stringify(r));
    assert.strictEqual(r.value, undefined, 'a refusal carries no value to use');
  }
});

test('an absent body refuses rather than throwing', () => {
  for (const b of [null, undefined]) {
    assert.strictEqual(validateBuildRequest(b).ok, false);
  }
});

// ── the wiring ────────────────────────────────────────────────────────────

test('the route is authed, rate limited and identity-resolved server-side', () => {
  assert.match(src, /router\.use\(authMiddleware\)/);
  assert.match(src, /rateLimit\(\{[^}]*key: userKey/);
  assert.match(src, /router\.post\('\/swap\/build'/);
  assert.match(src, /resolveBotIdentity\(req\)/);
  assert.ok(!/req\.body\.telegram_id|b\.telegram_id/.test(src),
    'identity must never be taken from the request body');
});

test('this layer never signs and never touches a key', () => {
  const code = src.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '');
  for (const banned of ['signTransaction', 'sendRawTransaction', 'signAndSend',
    'private_key', 'privateKey', 'secretKey', 'Keypair', 'mnemonic']) {
    assert.ok(!code.includes(banned), `${banned} must not appear in this route`);
  }
});

test('it does not re-derive whether the build may be signed', () => {
  // The server states `signable` once, in meme_swap.py. A second derivation
  // here is how two answers to one safety question come to disagree.
  const code = src.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '');
  assert.ok(!/signable/.test(code),
    'signable is relayed, never recomputed in the web layer');
  assert.ok(!/mainnet/.test(code),
    'the web layer has no opinion about which network may be signed');
});

test('an unreachable gateway is a 503, never an empty success', () => {
  const code = src.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '');
  assert.match(code, /catch \(err\)[\s\S]*?res\.status\(503\)/,
    'a failed build must not render as a plan with no findings');
  assert.ok(!/catch[\s\S]{0,200}res\.json\(\{\s*(plans|build)\s*:\s*\[?\]?\s*\}/.test(code),
    'no catch may return an empty-but-successful payload');
});

test('the error log carries a stack', () => {
  assert.match(src, /console\.error\([^)]*err\.stack/);
});
