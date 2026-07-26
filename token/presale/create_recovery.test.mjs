// Offline regression tests for the presale artifact lifecycle.
//
// `presale:create` is two non-atomic transactions. The dangerous failure is the
// second one: initializeV2 lands, addPresaleBucketV2 fails, and a genesis
// account exists on-chain whose address — in mint mode — was only ever held by
// an ephemeral in-memory signer. The artifact is therefore written BEFORE the
// first send and only flipped to "complete" after the second, which creates a
// state the rest of the CLI must handle correctly:
//
//   pending  => the addresses are real and must be kept for recovery, but the
//               presale bucket does not exist, so nothing may transact on it.
//   complete => the sale exists.
//
// Getting that backwards in either direction is a real failure: refusing to
// record a pending run loses the genesis account forever, and accepting one as
// live sends deposits into a bucket that was never configured.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

// A unique artifact name per run, so these never read or write the real
// presale.devnet.json — the exact hazard PRESALE_ARTIFACT exists to prevent.
const ARTIFACT = `presale.test.${process.pid}.json`;
process.env.PRESALE_ARTIFACT = ARTIFACT;

const { requirePresale, persistBaseMintKeypair, saveArtifact, TOKEN_ROOT } = await (async () => {
  const mod = await import('./genesis_presale.mjs');
  const lib = await import('./genesis_lib.mjs');
  return { ...mod, TOKEN_ROOT: lib.TOKEN_ROOT };
})();

const ARTIFACT_PATH = path.join(TOKEN_ROOT, '.artifacts', ARTIFACT);
const cleanup = () => fs.rmSync(ARTIFACT_PATH, { force: true });
process.on('exit', cleanup);

const COMPLETE = {
  status: 'complete',
  baseMint: 'So11111111111111111111111111111111111111112',
  genesisAccount: 'GNS1S5J5AspKXgpjz6SvKL66kPaKWAhaGRhCqPRxii2B',
  bucket: '11111111111111111111111111111111',
  completedSteps: ['initializeV2', 'addPresaleBucketV2'],
};

test('a missing artifact is refused', () => {
  cleanup();
  assert.throws(() => requirePresale(), /run .*presale:create.* first/);
});

test('a completed presale is accepted', () => {
  saveArtifact(ARTIFACT, COMPLETE);
  const a = requirePresale();
  assert.equal(a.genesisAccount, COMPLETE.genesisAccount);
});

test('a PENDING presale is refused for every transacting command', () => {
  // This is the half-created state: the genesis account exists, the bucket does
  // not. Depositing into it, triggering it or claiming from it would all be
  // acting on an account that was never configured.
  saveArtifact(ARTIFACT, {
    status: 'pending',
    baseMint: COMPLETE.baseMint,
    genesisAccount: COMPLETE.genesisAccount,
    completedSteps: ['initializeV2'],
  });
  assert.throws(() => requirePresale(), /INCOMPLETE presale/);
  // …and it must still name the addresses, because nothing else on disk has
  // them and .artifacts/ is gitignored.
  assert.throws(() => requirePresale(), new RegExp(COMPLETE.genesisAccount));
  assert.throws(() => requirePresale(), new RegExp(COMPLETE.baseMint));
  assert.throws(() => requirePresale(), /do not delete the artifact/i);
});

test('a pending artifact is never silently deleted or downgraded', () => {
  // Recovery depends on the file surviving the refusal above.
  const before = fs.readFileSync(ARTIFACT_PATH, 'utf8');
  assert.throws(() => requirePresale());
  assert.equal(fs.readFileSync(ARTIFACT_PATH, 'utf8'), before);
});

test('an artifact predating status tracking is treated as complete', () => {
  // Written only after both sends confirmed, so it is complete by construction.
  // Rejecting it would strand a real presale created by an earlier build.
  const { status, ...legacy } = COMPLETE;
  void status;
  saveArtifact(ARTIFACT, legacy);
  const a = requirePresale();
  assert.equal(a.genesisAccount, COMPLETE.genesisAccount);
});

test('an unknown future status fails closed', () => {
  saveArtifact(ARTIFACT, { ...COMPLETE, status: 'partially-rolled-back' });
  assert.throws(() => requirePresale(), /INCOMPLETE presale/);
});

// ── The ephemeral base-mint keypair ─────────────────────────────────────────

test('the base-mint keypair is persisted 0600 and round-trips', () => {
  const secretKey = new Uint8Array(64).fill(7);
  const pubkey = 'BaseMintTestOnly1111111111111111111111111111';
  const file = persistBaseMintKeypair({ publicKey: { toString: () => pubkey }, secretKey });
  try {
    assert.ok(file.includes(pubkey), 'the filename must name the mint it belongs to');

    // 0600 matters as much as the file existing: this is a signing key, and the
    // audit's F-04 was precisely a world-readable one.
    const mode = fs.statSync(file).mode & 0o777;
    assert.equal(mode, 0o600, `keypair must be 0600, got ${mode.toString(8)}`);
    const dirMode = fs.statSync(path.dirname(file)).mode & 0o777;
    assert.equal(dirMode, 0o700, `.keys/ must be 0700, got ${dirMode.toString(8)}`);

    // The stored form must be what the Solana tooling expects: a bare JSON array
    // of the 64 secret bytes, loadable by the same reader as the payer key.
    const parsed = JSON.parse(fs.readFileSync(file, 'utf8'));
    assert.ok(Array.isArray(parsed) && parsed.length === 64);
    assert.deepEqual(Uint8Array.from(parsed), secretKey);
  } finally {
    fs.rmSync(file, { force: true });
  }
});

// ── flag parsing ────────────────────────────────────────────────────────────
//
// `--accept-below-presale` is the override on the one guard standing between a
// weak raise and a permanently underwater pool. It was checked with
// `arg('--accept-below-presale') !== undefined`, which could never be true: arg()
// returns argv[i+1], and a valueless flag has nothing after it. The override was
// dead on arrival, and the only thing that showed it was running the command.
//
// Both helpers are pinned here because the failure is silent in BOTH directions
// — a boolean read by arg() is always "absent", and a valued flag read by
// hasFlag() is always "present".

test('hasFlag detects a valueless flag, including as the final argument', async () => {
  const { hasFlag } = await import('./genesis_presale.mjs');
  const saved = process.argv;
  try {
    process.argv = ['node', 'x.mjs', 'trigger', '--accept-below-presale'];
    assert.equal(hasFlag('--accept-below-presale'), true, 'trailing flag must be seen');
    process.argv = ['node', 'x.mjs', 'trigger', '--accept-below-presale', '--other'];
    assert.equal(hasFlag('--accept-below-presale'), true);
    process.argv = ['node', 'x.mjs', 'trigger'];
    assert.equal(hasFlag('--accept-below-presale'), false);
  } finally {
    process.argv = saved;
  }
});

test('arg() is the WRONG helper for a valueless flag — this is the bug, pinned', async () => {
  const { arg } = await import('./genesis_presale.mjs');
  const saved = process.argv;
  try {
    process.argv = ['node', 'x.mjs', 'trigger', '--accept-below-presale'];
    // Present, yet arg() reports undefined — identical to absent.
    assert.equal(arg('--accept-below-presale'), undefined);
    assert.equal(arg('--accept-below-presale'), arg('--never-passed-at-all'),
      'a present boolean flag is indistinguishable from a missing one via arg()');
  } finally {
    process.argv = saved;
  }
});

test('arg() still reads valued flags correctly', async () => {
  const { arg } = await import('./genesis_presale.mjs');
  const saved = process.argv;
  try {
    process.argv = ['node', 'x.mjs', 'deposit', '--amount', '0.01'];
    assert.equal(arg('--amount'), '0.01');
    assert.equal(arg('--recipient'), undefined);
  } finally {
    process.argv = saved;
  }
});

test('REGRESSION: the override call site uses hasFlag, not arg', () => {
  // The two tests above pin the HELPERS. They do not pin the CALL SITE, and a
  // mutation proved it: reverting `hasFlag('--accept-below-presale')` back to
  // `arg('--accept-below-presale') !== undefined` broke the override on a real
  // validator while both of them stayed green. A guard that cannot fail when the
  // bug returns is not a guard.
  //
  // So this reads the source. Crude, and the only thing here that actually
  // fails when the defect comes back.
  const src = fs.readFileSync(
    path.join(path.dirname(fileURLToPath(import.meta.url)), 'genesis_presale.mjs'), 'utf8'
  );
  const code = src.split('\n').filter((l) => !/^\s*(\/\/|\*|\/\*)/.test(l)).join('\n');
  assert.ok(
    code.includes("hasFlag('--accept-below-presale')"),
    'the below-presale override must be read with hasFlag'
  );
  assert.ok(
    !/arg\(\s*'--accept-below-presale'/.test(code),
    'arg() cannot see a valueless flag — the override would be permanently dead'
  );
  // Same defect, same shape, other flag.
  assert.ok(!/arg\(\s*'--force'/.test(code), "--force is valueless too");
});
