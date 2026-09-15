'use strict';
/**
 * The website reads the bot's vocabulary of secret shapes, not its own.
 *
 * `bot/utils/secret_shapes.py` is one table with eight rows, each carrying the
 * example it must scrub and the decoy it must leave alone, and its own coverage
 * note recorded this runtime as the hole: "`app/lib/safe_error.js` has its own
 * vocabulary — wider on labels, narrower on token shapes — and a second file
 * read by two runtimes is filed, not done."
 *
 * Driven before the fix, `safeErrorText` published every one of these:
 *
 *     send failed for bot 7123456789:AAHf…              a Telegram bot token
 *     provider rejected sk-proj-…  ·  xai-…             a bare provider key
 *     session eyJ….eyJ….dBjftJeZ…                       a session JWT
 *     RUNECLAW_SECRETS_KEY=…                            the key that opens the vault
 *     WEB3_SIGNER_PRIVATE_KEY=…                         the key that signs transactions
 *     WEB_CREDS_KEY=…
 *     api key: bg_1234…                                 the prose spelling
 *
 * and turned `Authorization: Bearer sk-ant-…` into
 * `Authorization: ***REDACTED*** sk-ant-…` — the LABEL redacted and the key
 * printed, because the label list matched `authorization` and `(\S+)` took the
 * word *Bearer* as the value. That is worse than a miss: the reader sees a
 * redaction marker and concludes the line was scrubbed.
 *
 * The reach is not theoretical. `safeErrorText` is the error body of
 * `routes/mcp.js`, `routes/tool8257.js`, `routes/sync.js` and `routes/agents.js`,
 * and `POST /api/tool/invoke` is public and unauthenticated — the reason the
 * file exists, in its own header.
 *
 * Every row here is driven through the REAL reader. A test that read the table
 * and applied it itself would be a third copy agreeing with both.
 */

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const { safeErrorText, LIMIT } = require('../lib/safe_error');
const { ROWS, REDACTED, scrubSecrets, looksLikeCredential, buildShapes } = require('../lib/secret_shapes');
const { codeOnly } = require('./helpers/code_only');

// ── 1. the table pins itself, through the reader ────────────────────────────

test('every row scrubs its own example', () => {
  for (const row of ROWS) {
    const out = safeErrorText(new Error(row.example), 4000);
    assert.notEqual(out, row.example, `${row.name}: its own example survived`);
    // The marker is the usual proof, EXCEPT where this runtime is stricter than
    // the card path: an error body drops a whole query string (`?***`), so the
    // secret-named parameter's own `***REDACTED***` is superseded rather than
    // missing. Either way the value is gone, which is the claim.
    const marked = out.includes(REDACTED) || out.includes('?***');
    assert.ok(marked, `${row.name}: scrubbed without a marker — ${out}`);
  }
});

test('every row leaves its own decoy alone', () => {
  for (const row of ROWS) {
    const out = safeErrorText(new Error(row.decoy), 4000);
    assert.ok(!out.includes(REDACTED), `${row.name}: its own decoy was redacted — ${out}`);
  }
});

test('the table is the eight rows the bot renders, in its order', () => {
  assert.deepEqual(ROWS.map((r) => r.name), [
    'telegram_bot_token', 'bearer_token', 'provider_key', 'jwt',
    'named_key_value', 'prose_key_label', 'env_name_value', 'secret_query_param',
  ]);
});

// ── 2. what this runtime published before ───────────────────────────────────

const LEAKED = {
  telegram_token: 'send failed for bot 7123456789:AAHfSomeRealLookingTokenValue_abcdef12345',
  provider_key: 'provider rejected sk-proj-abc123def456ghi789jkl012mno345pqr',
  xai_key: 'client built with xai-abcdefghijklmnopqrstuvwxyz0123456789',
  jwt: 'session eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.dBjftJeZ4CVPmB92K27uhbUJU1p1r_wW1gFWFOEjXk',
  master_key: 'config error: RUNECLAW_SECRETS_KEY=Zm9vYmFyYmF6cXV4Cg0123456789abcd',
  signer_key: 'WEB3_SIGNER_PRIVATE_KEY=4f3c2b1a0987654321fedcba0123456789abcdef0123456789abcdef01234567',
  creds_key: 'WEB_CREDS_KEY=0123456789abcdef0123456789abcdef',
  prose_label: 'api key: bg_1234567890abcdefghij failed to authenticate',
};

for (const [name, text] of Object.entries(LEAKED)) {
  test(`the shape that used to pass is scrubbed now: ${name}`, () => {
    const out = safeErrorText(new Error(text), 4000);
    assert.ok(out.includes(REDACTED), `${name} was not redacted — ${out}`);
    const secret = text.split(/[\s=:]+/).filter((w) => w.length >= 16).pop();
    if (secret) assert.ok(!out.includes(secret), `${name} still carries its value — ${out}`);
  });
}

test('the bearer row keeps the scheme word and takes the token', () => {
  const out = safeErrorText(new Error(
    'upstream said Authorization: Bearer sk-ant-api03-Zm9vYmFyYmF6cXV4Cg1234'));
  assert.equal(out, `upstream said Authorization: Bearer ${REDACTED}`);
  // The defect it replaces: the label redacted, the key printed after it.
  assert.ok(!/\*\*\*REDACTED\*\*\* sk-ant/.test(out), out);
});

test('a row the table marks case-insensitive is read case-insensitively here too', () => {
  // The flag is a FIELD in the rendered table; every example happens to be
  // lower case, so nothing else here would notice it being dropped.
  const out = safeErrorText(new Error('API KEY: bg_1234567890abcdefghij rejected'));
  assert.equal(out, `API KEY=${REDACTED} rejected`);
  assert.equal(safeErrorText(new Error('AUTH FAILED: API_KEY=sk-abcdefghijklmnopqrs')),
    `AUTH FAILED: API_KEY=${REDACTED}`);
});

test('the env-name row keeps the NAME, because which key was printed is the diagnostic', () => {
  const out = safeErrorText(new Error('config error: RUNECLAW_SECRETS_KEY=Zm9vYmFyYmF6cXV4Cg0123456789abcd'));
  assert.equal(out, `config error: RUNECLAW_SECRETS_KEY=${REDACTED}`);
});

// ── 3. what this runtime keeps, and why it is not a second vocabulary ───────

test('the rows an error body needs and a card does not still apply', () => {
  assert.equal(safeErrorText(new Error('mysql://user:pw@db.internal:3306/runeclaw down')),
    `${REDACTED} down`);
  assert.equal(safeErrorText(new Error('ENOENT /home/mulerun/.env missing')),
    'ENOENT ***path*** missing');
  assert.equal(safeErrorText(new Error('upstream sent cookie=abcd1234efgh5678ijkl')),
    `upstream sent cookie=${REDACTED}`);
  assert.equal(safeErrorText(new Error('GET https://api.example.com/v1/x?key=AIzaSyA1b2C3d4E5f6G7h8I9j0&v=3 failed')),
    'GET https://api.example.com/v1/x?*** failed');
});

test('a driver label leaves a value that is not one alone — which is what saves the scheme word', () => {
  assert.equal(looksLikeCredential('Bearer'), false);
  assert.equal(safeErrorText(new Error('auth: required')), 'auth: required');
});

test('what a caller is right to read comes back unchanged', () => {
  for (const kept of [
    'text is required',
    'api key: not configured',
    'tx 0x9d5f1c2b3a4e5d6c7b8a90112233445566778899aabbccddeeff00112233445566 confirmed',
  ]) assert.equal(safeErrorText(new Error(kept)), kept);
});

// ── 4. the second argument ──────────────────────────────────────────────────

test('a sentence is the sentence the reader gets, not a character limit', () => {
  const err = new Error('connect ECONNREFUSED 10.0.0.7:3306 (db agents_prod)');
  assert.equal(safeErrorText(err, 'Your agents could not be read'), 'Your agents could not be read');
  // Number('Your agents could not be read') is NaN, so the old reading published
  // the driver's text under a sentence its author thought they were sending.
  assert.ok(!safeErrorText(err, 'Your agents could not be read').includes('10.0.0.7'));
});

test('a number is still a character limit, and the scrub runs before the cut', () => {
  assert.equal(safeErrorText(new Error('connect ECONNREFUSED 10.0.0.7:3306'), 20), 'connect ECONNREFUSED');
  // The order is load-bearing and only ONE shape of input can tell: a bare
  // token whose row demands a minimum length, positioned so the cut leaves a
  // fragment too short to match. Scrubbed first, the key is gone before the
  // cut; cut first, the fragment no longer looks like a key and is published.
  // A LABELLED secret cannot tell the orders apart — its row still matches the
  // stump — which is why the first fixture here proved nothing.
  const long = `${'x'.repeat(LIMIT - 15)} sk-ABCDEFGHIJKLMNOPQRSTUVWXYZ012345`;
  const out = safeErrorText(new Error(long));
  assert.ok(!out.includes('sk-ABCDEFGHIJ'), `a cut-off key survived: ${out.slice(-40)}`);
});

test('a thrown-on message answers the caller\'s sentence, and "" without one', () => {
  const hostile = { get message() { throw new Error('nope'); } };
  assert.equal(safeErrorText(hostile), '');
  assert.equal(safeErrorText(hostile, 'The claim could not be recorded'), 'The claim could not be recorded');
});

// ── 5. a table nobody can read is not an empty table ────────────────────────

test('a table with no rows throws rather than becoming a no-op', () => {
  assert.throws(() => buildShapes({ rows: [] }), /no rows/);
  assert.throws(() => buildShapes(null), /no rows/);
});

// ── 6. the copies are gone on this side too ─────────────────────────────────

test('safe_error declares no token or key-value pattern of its own', () => {
  const src = codeOnly(fs.readFileSync(path.join(__dirname, '..', 'lib', 'safe_error.js'), 'utf8'));
  for (const gone of ['sk-', 'xai-', 'eyJ', 'api[_-]?key', 'apikey', "'secret'", 'Bearer']) {
    assert.ok(!src.includes(gone), `safe_error.js spells ${gone} again — the table is the vocabulary`);
  }
});

test('the shared scrub reaches every row, and the body is never wider', () => {
  for (const row of ROWS) {
    const shared = scrubSecrets(row.example);
    assert.ok(shared.includes(REDACTED), `${row.name}: the shared table did not reach it`);
    const body = safeErrorText(new Error(row.example), 4000);
    assert.notEqual(body, row.example, `${row.name}: the body published it`);
  }
});
