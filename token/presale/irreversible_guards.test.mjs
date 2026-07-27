// Two irreversible steps were guarded by a console.warn.
//
// THE DEFECT
//
// `presale:create` runs initializeV2, which fixes the genesis authority
// PERMANENTLY — there is no set-authority instruction for a finalized genesis
// account. With `treasury.authority` unset it fell back to the signing hot key
// and printed:
//
//     WARNING: presale authority is a single hot key (…)
//
// …then carried on and created the presale. The comment directly above that
// code said the authority "has to be right here, not later". It was right, and
// the code below it warned and proceeded anyway. A warning scrolls past in a
// log; initializeV2 does not come round again.
//
// The same shape guarded `sale.unsoldRollover`: the end behavior is attached at
// bucket creation and cannot be added later, and withdrawUnsoldPresaleV1 is
// V1-only (rejects a V2 genesis account, 0x2f). Without it every token nobody
// buys is unreachable by anyone, forever — roughly 120,000,000 tokens, 12% of
// supply, at a soft-cap raise. The warning's own wording said this was "only
// acceptable if the sale is certain to sell out", and no sale is certain to
// sell out. A condition nothing can satisfy is not a caveat; it is a refusal
// that forgot to refuse.
//
// WHY THIS IS A DEFECT AND NOT A PREFERENCE
//
// The inconsistency. In the SAME FILE, `presale:allocate` throws when a bucket
// recipient is unset ("it would default to the signing key"), and
// `presale:trigger` throws rather than open a pool below the presale price,
// offering `--accept-below-presale` as an explicit override. So the guards that
// refused covered a recoverable allocation and a bad-but-known price, while the
// guards that merely warned covered total permanent control of the raise and
// the permanent loss of every unsold token. That is backwards, and it is not a
// judgement call about how strict to be — it is the same author applying two
// different standards to the more and less dangerous cases.
//
// WHY THE CHECKS MOVED TO genesis_lib.mjs
//
// They were unreachable by any test. Both sat inside `cmdCreate`, a ~200-line
// async command that needs a funded devnet signer and a live cluster before it
// reaches either line. A guard no test can execute is the defect this audit
// keeps finding, so the decision is now a pure function over
// (config, signer, flags) and this file runs both directions of both.
import { test } from 'node:test';
import assert from 'node:assert/strict';

import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

import { hotKeyAuthorityRefusal, strandedUnsoldRefusal } from './genesis_lib.mjs';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const CLI = fs.readFileSync(path.join(HERE, 'genesis_presale.mjs'), 'utf8');
const realConfig = () => JSON.parse(
  fs.readFileSync(path.join(HERE, 'metaplex-genesis.config.json'), 'utf8'));

const SIGNER = 'HotKey1111111111111111111111111111111111111';

test('the SHIPPED config refuses — treasury.authority is unset today', () => {
  // Not a hypothetical. This is the state of the repo: every recipient and the
  // treasury authority are empty strings awaiting a human decision, and until
  // this change the tooling would have created the presale anyway.
  const cfg = realConfig();
  assert.equal(cfg.treasury.authority, '',
    'treasury.authority is now set — if that is the real Squads vault PDA, good; ' +
    'update this test to assert the value rather than the emptiness');
  const refusal = hotKeyAuthorityRefusal(cfg, SIGNER);
  assert.ok(refusal, 'presale:create would proceed with a single hot-key authority');
  assert.match(refusal, /CANNOT be changed/);
  assert.match(refusal, new RegExp(SIGNER), 'the refusal must name the key it refused');
});

test('setting the authority allows it through', () => {
  const cfg = realConfig();
  cfg.treasury.authority = 'Vau1t1111111111111111111111111111111111111';
  assert.equal(hotKeyAuthorityRefusal(cfg, SIGNER), null);
});

test('the override is explicit, and only the override', () => {
  const cfg = realConfig();
  assert.equal(hotKeyAuthorityRefusal(cfg, SIGNER, { override: true }), null);
  // Anything short of the flag still refuses — including the shapes a hurried
  // operator might reach for.
  for (const opts of [{}, { override: false }, { override: undefined }]) {
    assert.ok(hotKeyAuthorityRefusal(cfg, SIGNER, opts),
      `refusal bypassed by ${JSON.stringify(opts)}`);
  }
});

test('a missing config is refused, not treated as configured', () => {
  // Fail closed on the shapes a partial edit produces. `cfg.treasury?.authority`
  // on an absent treasury block is undefined, which must read as "unset".
  for (const cfg of [undefined, null, {}, { treasury: {} }, { treasury: { authority: '' } }]) {
    assert.ok(hotKeyAuthorityRefusal(cfg, SIGNER),
      `an unusable config (${JSON.stringify(cfg)}) read as a configured authority`);
  }
});

test('unsold rollover: the shipped config passes, an unset one refuses', () => {
  const cfg = realConfig();
  assert.ok(cfg.sale.unsoldRollover, 'premise changed: the config no longer sets a rollover');
  assert.equal(strandedUnsoldRefusal(cfg), null);

  delete cfg.sale.unsoldRollover;
  const refusal = strandedUnsoldRefusal(cfg);
  assert.ok(refusal, 'a presale bucket would be created with no rollover');
  assert.match(refusal, /STRANDED PERMANENTLY/);
  assert.equal(strandedUnsoldRefusal(cfg, { override: true }), null);
});

test('THE CLI ACTUALLY CALLS THEM — and throws on the result', () => {
  // The whole point. A precondition that returns a message nobody acts on is
  // the same "guard not reached" defect in a new place, and moving the logic
  // into a testable function is exactly the refactor that could introduce it.
  for (const fn of ['hotKeyAuthorityRefusal', 'strandedUnsoldRefusal']) {
    assert.match(CLI, new RegExp(`${fn}\\(`), `${fn} is never called by the CLI`);
    const i = CLI.indexOf(`${fn}(`);
    const after = CLI.slice(i, i + 400);
    assert.match(after, /throw new Error\(/,
      `${fn}'s result is computed but never thrown — the guard is decorative`);
  }
  assert.match(CLI, /hotKeyAuthorityRefusal[\s\S]{0,400}?throw new Error/);
});

test('the guard runs BEFORE the irreversible instruction is built', () => {
  // Ordering is the guard. Refusing after initializeV2 has been sent would be
  // an error message about something that already happened.
  const guard = CLI.indexOf('hotKeyAuthorityRefusal(');
  const initialize = CLI.indexOf('initializeV2(');
  assert.ok(guard > 0 && initialize > 0, 'anchors moved — re-derive this check');
  assert.ok(guard < initialize,
    'the hot-key authority check now runs after initializeV2 is built');
});

test('neither warning-only version survives', () => {
  // Pinned by their exact old wording, so a revert is caught rather than a
  // paraphrase being accepted. Both strings were the entire guard.
  assert.doesNotMatch(CLI, /WARNING: presale authority is a single hot key/,
    'the hot-key authority guard is a console.warn again');
  assert.doesNotMatch(CLI, /This is only acceptable if the sale is certain to sell out/,
    'the stranded-unsold guard is a console.warn again');
});

test('the two override flags are distinct and named in their own refusals', () => {
  // A refusal that does not say how to proceed deliberately gets worked around
  // by editing the source, which is worse than the override existing.
  const cfg = realConfig();
  const a = hotKeyAuthorityRefusal(cfg, SIGNER);
  const noRollover = realConfig();
  delete noRollover.sale.unsoldRollover;
  const b = strandedUnsoldRefusal(noRollover);
  assert.match(a, /--accept-hot-key-authority/);
  assert.match(b, /--accept-stranded-unsold/);
  assert.doesNotMatch(a, /--accept-stranded-unsold/,
    'one flag would silently satisfy both guards');
  // ...and the CLI reads them with hasFlag, not arg(). `arg('--x')` returns
  // argv[i+1], so for a valueless flag it yields undefined — indistinguishable
  // from absent. That exact bug already killed --accept-below-presale once.
  for (const flag of ['--accept-hot-key-authority', '--accept-stranded-unsold']) {
    assert.match(CLI, new RegExp(`hasFlag\\('${flag}'\\)`),
      `${flag} is not read with hasFlag — if it uses arg(), the override can never fire`);
  }
});
