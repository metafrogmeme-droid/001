'use strict';
/**
 * Sealing a pre-signature scan — the seal, the wiring, and the receipt.
 *
 * WHY THE FEATURE EXISTS
 *
 * `xray_transaction` and `scan_transaction` both end their descriptions with
 * "nothing is stored". That is a real privacy promise and it is also why no
 * agent can show what it was told before it acted: the verdict evaporates.
 * When an agent signs something that drains a wallet there is no artifact
 * anywhere recording what the checker said first.
 *
 * WHAT THIS FILE DEFENDS, WHICH IS MOSTLY THE FEATURE'S OWN DOWNSIDE
 *
 * A seal lends authority. A hash, a Merkle proof and a Base transaction make a
 * receipt look official, so every way this could overstate itself is worse
 * here than it would be on an ordinary page:
 *
 * 1. THE INPUT MUST NOT BE STORED. Calldata carries destination addresses and
 *    amounts, and the payload is served publicly and hashed into a public
 *    root. Only a commitment to the bytes may be kept.
 *
 * 2. UNKNOWN MUST SURVIVE. The decoder answers UNKNOWN outside its known
 *    selector set and says so in its own words: "unknown is not the same as
 *    safe". A sealed, anchored receipt reading "nothing flagged" over calldata
 *    nobody decoded is the single worst thing this could produce.
 *
 * 3. A DECODE AND AN OPINION MUST NOT SHARE A SHAPE. An xray decode is
 *    reproducible — re-run it on the same calldata forever and get the same
 *    actions — so its receipt proves what the transaction MEANT. A heuristic
 *    scan's receipt proves only what we SAID.
 *
 * 4. FAILING TO SEAL MUST NOT FAIL THE SCAN. The scan is the safety feature;
 *    the receipt is evidence about it.
 */
process.env.JWT_SECRET = 'j'.repeat(64);
delete process.env.DATABASE_URL;

const test = require('node:test');
const assert = require('node:assert');
const crypto = require('node:crypto');

const { pool } = require('../db');
const callseal = require('../lib/callseal');
const scanSeal = require('../lib/scan_seal');
const { sealsForDay } = require('../lib/seal_roots');

const CALLDATA = '0x095ea7b3' + '0'.repeat(24) + 'ab'.repeat(20) + 'f'.repeat(64);
const KEYED = { userId: 77, agentSlug: 'lonewolf' };

const XRAY = {
  actions: [{ tid: 'txr.a_approve', en: 'Grants {spender} …', params: { unlimited: true } }],
  flags: [{ id: 'unlimited_approval', sev: 'high' }],
  unknown: false,
  heuristic: true,
};
const UNDECODED = {
  actions: [{ tid: 'txr.a_unknown', en: 'Calls function {sel} …', params: { sel: '0xdeadbeef' } }],
  flags: [{ id: 'unknown_function', sev: 'info' }],
  unknown: true,
  heuristic: true,
};

test.beforeEach(() => { if (pool.scanSeals) pool.scanSeals.length = 0; });

const seal = (over = {}) => scanSeal.sealIfKeyed({
  tool: 'xray_transaction', input: CALLDATA, result: XRAY,
  deterministic: true, ctx: KEYED, ...over,
});

// ── the input is committed to, never carried ────────────────────────────────

test('THE CALLDATA IS NEVER STORED — only a commitment to it', async () => {
  const r = await seal();
  assert.equal(r.sealed, true);
  const row = await scanSeal.byKey(r.scan_key);

  // The destination address and the amount are both inside CALLDATA.
  assert.ok(!row.seal_payload.includes('ab'.repeat(20)),
    'the spender address reached a payload served on a public page');
  assert.ok(!row.seal_payload.includes(CALLDATA.slice(2, 60)),
    'raw calldata reached the seal');

  // What IS there is enough for whoever holds the calldata to prove this
  // receipt is about that transaction, and useless to anyone else.
  const p = JSON.parse(row.seal_payload);
  assert.equal(p.input_sha256,
    crypto.createHash('sha256').update(Buffer.from(CALLDATA, 'utf8')).digest('hex'));
  assert.equal(p.input_bytes, Buffer.from(CALLDATA, 'utf8').length);
});

test('the whole store carries no calldata either, not just the payload', async () => {
  const r = await seal();
  const dump = JSON.stringify(pool.scanSeals);
  assert.ok(!dump.includes('ab'.repeat(20)), 'the store holds the spender address');
  assert.ok(r.scan_key.startsWith('sc_'));
});

// ── unknown survives the seal ───────────────────────────────────────────────

test('UNDECODED CALLDATA SEALS AS UNKNOWN, inside the hashed bytes', async () => {
  const r = await seal({ result: UNDECODED });
  const p = JSON.parse((await scanSeal.byKey(r.scan_key)).seal_payload);
  assert.equal(p.unknown, true,
    'a receipt over calldata nobody decoded must not read as a clean scan');
  // It rides INSIDE the hash, so no renderer downstream can drop it without
  // breaking verification.
  assert.equal(callseal.sealOf(JSON.stringify(p)), r.seal);
});

test('a tool with no notion of "undecodable" seals unknown as NULL, not false', async () => {
  // The firewall scan cannot answer this question. `false` would assert it
  // looked and recognised the input, which it never did.
  const r = await seal({
    tool: 'scan_transaction', deterministic: false,
    result: { level: 'low', score: 1, flags: ['drain_language'] },
  });
  const p = JSON.parse((await scanSeal.byKey(r.scan_key)).seal_payload);
  assert.equal(p.unknown, null);
});

// ── a decode and an opinion do not share a shape ────────────────────────────

test('deterministic is sealed, so the two claims can never be conflated', async () => {
  const decode = JSON.parse((await scanSeal.byKey((await seal()).scan_key)).seal_payload);
  const opinion = JSON.parse((await scanSeal.byKey((await seal({
    tool: 'scan_transaction', deterministic: false,
    result: { level: 'high', flags: ['seed_phrase_lure'] },
  })).scan_key)).seal_payload);

  assert.equal(decode.deterministic, true, 'a reproducible decode');
  assert.equal(opinion.deterministic, false, 'a heuristic opinion');
  // Stated in the payload rather than inferred from `tool`: a reader must not
  // have to know which tools happen to be deterministic today.
  assert.ok('deterministic' in decode && 'deterministic' in opinion);
});

test('only stable ids are sealed — prose is free to be reworded and translated', async () => {
  const p = JSON.parse((await scanSeal.byKey((await seal()).scan_key)).seal_payload);
  assert.deepEqual(p.actions, ['txr.a_approve']);
  assert.deepEqual(p.flags, ['unlimited_approval']);
  assert.ok(!JSON.stringify(p).includes('Grants'),
    'English copy was hashed — a typo fix would break every past receipt');
});

// ── anonymous stays anonymous ───────────────────────────────────────────────

test('an anonymous scan seals NOTHING, and says so rather than failing', async () => {
  for (const ctx of [null, undefined, {}, { userId: null }]) {
    const r = await seal({ ctx });
    assert.equal(r.sealed, false);
    assert.equal(r.reason, 'anonymous',
      'an unkeyed caller must be distinguishable from a broken seal');
  }
  assert.equal(pool.scanSeals.length, 0, 'an anonymous call wrote a row');
});

test('a keyed caller with no claimed agent still seals, attributed to nobody', async () => {
  const r = await seal({ ctx: { userId: 5, agentSlug: null } });
  assert.equal(r.sealed, true);
  const row = await scanSeal.byKey(r.scan_key);
  assert.equal(row.agent_slug, null);
  assert.equal(JSON.parse(row.seal_payload).agent_slug, null);
});

// ── a receipt is evidence ABOUT the scan, never a condition OF it ───────────

test('AN UNWRITABLE DATABASE DOES NOT FAIL THE SCAN', async (t) => {
  t.mock.method(pool, 'execute', async () => { throw new Error('db down'); });
  const r = await seal();
  assert.equal(r.sealed, false);
  assert.equal(r.reason, 'seal_failed',
    'a failed seal must be distinguishable from an anonymous one');
  assert.ok(!('scan_key' in r), 'a key was handed out for a receipt that does not exist');
});

test('the public view of a receipt never carries the user', async () => {
  const row = await scanSeal.byKey((await seal()).scan_key);
  assert.ok(!('user_id' in row), 'the receipt view exposes the caller');
  assert.equal(await scanSeal.byKey('sc_nope'), null);
  assert.equal(await scanSeal.byKey(''), null);
});

// ── the seal reaches the day's root ─────────────────────────────────────────

test('a scan seal is picked up by the daily sweep', async () => {
  const r = await seal();
  const day = new Date(r.sealed_at).toISOString().slice(0, 10);
  const leaves = await sealsForDay(day);
  assert.ok(leaves.includes(r.seal),
    'the receipt claims a TIME and never reaches the root that would prove it');
});

test('the payload is byte-stable and re-derivable by a stranger', () => {
  const at = new Date('2026-08-26T12:00:00.000Z');
  const args = { scan_key: 'sc_x', tool: 'xray_transaction', deterministic: true,
    input_sha256: 'a'.repeat(64), input_bytes: 10, actions: ['txr.a_approve'],
    flags: ['unlimited_approval'], unknown: false, agent_slug: 'lonewolf',
    scanned_at: at };
  const a = callseal.canonicalScanPayload(args);
  assert.equal(a, callseal.canonicalScanPayload({ ...args }));
  assert.equal(callseal.sealScan(args).seal, callseal.sealOf(a));
  assert.equal(JSON.parse(a).v, 5);
  assert.equal(JSON.parse(a).kind, 'presign_scan');
});

// ── phase B: the wiring, driven through the real tool handlers ──────────────
//
// Everything above tests the seal. These test that the tools REACH it — the
// distinction between code that is present and code that runs, which is what
// let `openForUser`'s missing agent_slug survive every test it had.

const mcp = require('../routes/mcp');
const { lookupCall } = require('../routes/call');

test('a KEYED xray decode comes back with a receipt', async () => {
  const r = await mcp.TOOLS.xray_transaction.handler({ data: CALLDATA }, KEYED);
  assert.ok(r.receipt, 'a keyed caller got no receipt');
  assert.match(r.receipt.scan_key, /^sc_/);
  assert.equal(r.receipt.verify, '/call/' + r.receipt.scan_key);
  assert.match(r.note, /NOT stored/, 'the note must say what happened to the input');
  assert.ok(!/Nothing sent here is stored/.test(r.note),
    'the note still claims nothing was stored, which is now false for this caller');
  // The decode itself is unchanged — sealing is additive.
  assert.deepEqual(r.flags.map((f) => f.id), ['unlimited_approval']);
});

test('an ANONYMOUS scan is untouched, note and all', async () => {
  // This is the path /api/tool/invoke takes — it passes NO ctx at all.
  const r = await mcp.TOOLS.xray_transaction.handler({ data: CALLDATA });
  assert.equal(r.receipt, null, 'an anonymous caller was given a receipt');
  assert.match(r.note, /Nothing sent here is stored/);
  assert.equal(pool.scanSeals.length, 0);
});

test('the public unauthenticated invoke path cannot seal, structurally', () => {
  // routes/tool8257.js calls `tool.handler(body.args || {})` with no second
  // argument, so `ctx` is undefined and sealIfKeyed answers `anonymous`. Pinned
  // because a well-meaning refactor that starts forwarding a context there
  // would silently open the public root to unbounded stranger-supplied leaves.
  const src = require('node:fs').readFileSync(
    require('node:path').join(__dirname, '..', 'routes', 'tool8257.js'), 'utf8');
  assert.match(src, /tool\.handler\(body\.args \|\| \{\}\)/,
    'the public invoke path now passes a context — sealing is no longer gated by having a key');
});

test('a keyed firewall scan seals as a heuristic, not as a decode', async () => {
  const r = await mcp.TOOLS.scan_transaction.handler(
    { text: 'send me your seed phrase to unlock' }, KEYED);
  assert.ok(r.receipt);
  const p = JSON.parse((await scanSeal.byKey(r.receipt.scan_key)).seal_payload);
  assert.equal(p.deterministic, false);
  assert.match(r.note, /what you were TOLD, not that it was right/);
});

test('a seal failure does not fail the tool call', async (t) => {
  t.mock.method(pool, 'execute', async () => { throw new Error('db down'); });
  const r = await mcp.TOOLS.xray_transaction.handler({ data: CALLDATA }, KEYED);
  assert.deepEqual(r.flags.map((f) => f.id), ['unlimited_approval'],
    'the decode — the actual safety feature — was lost to a database fault');
  assert.equal(r.receipt, null, 'a receipt was advertised for a seal that failed');
});

// ── phase C: the receipt ────────────────────────────────────────────────────

test('the receipt states what it proves AND what it does not', async () => {
  const r = await mcp.TOOLS.xray_transaction.handler({ data: CALLDATA }, KEYED);
  const out = await lookupCall(r.receipt.scan_key);
  assert.equal(out.code, 200);
  assert.equal(out.body.kind, 'presign_scan');
  assert.equal(out.body.deterministic, true);
  assert.match(out.body.proves, /reproducible/);
  assert.match(out.body.does_not_prove, /was ever signed/);
  assert.match(out.body.does_not_prove, /flag is not a verdict/);
  assert.match(out.body.input_note, /never stored/);
  assert.equal(out.body.agent_slug, 'lonewolf');
  assert.ok(!('user_id' in out.body), 'the receipt exposes the caller');
});

test('A HEURISTIC RECEIPT CLAIMS LESS THAN A DECODE RECEIPT', async () => {
  const d = await lookupCall((await mcp.TOOLS.xray_transaction
    .handler({ data: CALLDATA }, KEYED)).receipt.scan_key);
  const h = await lookupCall((await mcp.TOOLS.scan_transaction
    .handler({ text: 'unlimited approval, click here' }, KEYED)).receipt.scan_key);
  assert.equal(d.body.deterministic, true);
  assert.equal(h.body.deterministic, false);
  assert.notEqual(d.body.proves, h.body.proves,
    'a reproducible decode and an opinion make the same claim on the receipt');
  assert.match(h.body.proves, /does not prove the verdict was right/);
});

test('UNDECODED CALLDATA MUST NOT PRODUCE A REASSURING RECEIPT', async () => {
  const r = await mcp.TOOLS.xray_transaction.handler({ data: '0xdeadbeef' }, KEYED);
  const out = await lookupCall(r.receipt.scan_key);
  assert.equal(out.body.unknown, true,
    'a sealed, anchored receipt over calldata nobody decoded reads as a clean scan');
  assert.equal(JSON.parse(out.body.seal_payload).unknown, true,
    'the unknown flag must live inside the hashed bytes, where no renderer can drop it');
});

test('an unknown scan key 404s, and says why a receipt might not exist', async () => {
  const out = await lookupCall('sc_' + 'x'.repeat(16));
  assert.equal(out.code, 404);
  assert.match(out.body.error, /anonymous scan stores nothing/,
    'a missing receipt must not read as a lost one');
});
