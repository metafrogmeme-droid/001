// Key material: every load site checks permissions, and the check has one body.
//
// WHY THIS IS STRUCTURAL, LIKE cluster_coverage.test.mjs
//
// This is the second time in this audit that a correct guard existed and a call
// site simply never got it. The first was `assertDevnet`: right where it ran,
// absent from five of the nine commands that open a connection. This one is
// `assertKeyfilePermissions`, which `token/scripts/lib.mjs` calls before loading
// a keypair and which `token/presale/genesis_lib.mjs` did not call at all — and
// the presale key is the one that signs deposits, the allocations, finalize and
// the irreversible LP lock.
//
// Two instances is a class, not a coincidence, so the check here is on the
// SHAPE of the code rather than on any one behaviour. Tests that exercise a
// guarded path can only ever confirm the paths somebody remembered to guard.
//
// F-04 in docs/TOKEN_SECURITY_AUDIT.md: one plaintext file holds mint,
// metadata, presale and LP authority plus the entire supply, so a single read
// by any other account on the box is total loss with no recovery path.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';

import { PRESALE_DIR } from './genesis_lib.mjs';
import { assertKeyfilePermissions } from '../scripts/lib.mjs';

const TOKEN_ROOT = path.join(PRESALE_DIR, '..');
const LOADERS = [
  path.join(TOKEN_ROOT, 'scripts', 'lib.mjs'),
  path.join(TOKEN_ROOT, 'presale', 'genesis_lib.mjs'),
];

test('every module that reads a keypair file checks its permissions first', () => {
  for (const file of LOADERS) {
    const src = fs.readFileSync(file, 'utf8');
    const rel = path.relative(TOKEN_ROOT, file);
    // Find the loadKeypair body and require the check inside it. Checking the
    // whole file would pass on an import that is never called.
    const m = /function loadKeypair[\s\S]*?\n}/.exec(src);
    assert.ok(m, `${rel}: no loadKeypair found — did it move?`);
    assert.match(
      m[0],
      /assertKeyfilePermissions\(/,
      `${rel}: loadKeypair reads a secret key without checking that the file is not ` +
        'group/world-readable. KEYPAIR_PATH is operator-supplied, so the check belongs ' +
        'at the load site, not only where keygen writes it.'
    );
  }
});

test('the check has exactly one definition', () => {
  // Two copies drift. The loopback guard already had to grow a test asserting
  // its copies agreed; the fix for that class is to not have copies.
  const defs = LOADERS.filter((f) =>
    /export function assertKeyfilePermissions/.test(fs.readFileSync(f, 'utf8'))
  );
  assert.equal(defs.length, 1, `assertKeyfilePermissions is defined in ${defs.length} places`);
});

test('a group- or world-readable key is refused, a private one accepted', () => {
  // The check itself, exercised — the structural tests above only prove it is
  // called, which is worth nothing if it does not work.
  if (process.platform === 'win32') return;
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'rclaw-keyperm-'));
  const f = path.join(dir, 'k.json');
  fs.writeFileSync(f, '[1,2,3]');

  for (const mode of [0o644, 0o640, 0o604, 0o660, 0o606]) {
    fs.chmodSync(f, mode);
    assert.throws(
      () => assertKeyfilePermissions(f),
      /group\/world-readable/,
      `mode ${mode.toString(8)} was accepted`
    );
  }
  for (const mode of [0o600, 0o400]) {
    fs.chmodSync(f, mode);
    assert.doesNotThrow(() => assertKeyfilePermissions(f), `mode ${mode.toString(8)} was refused`);
  }
  fs.rmSync(dir, { recursive: true, force: true });
});

test('key material on disk is actually private', () => {
  // The end state, not the intent. Modes are set at write time and a file that
  // predates that code keeps its old mode, so assert what is really there.
  if (process.platform === 'win32') return;
  const dir = path.join(TOKEN_ROOT, '.keys');
  if (!fs.existsSync(dir)) return; // nothing generated in this checkout

  assert.equal(fs.statSync(dir).mode & 0o077, 0, `${dir} is group/world-accessible`);
  for (const name of fs.readdirSync(dir)) {
    if (!name.endsWith('.json')) continue;
    const p = path.join(dir, name);
    assert.equal(fs.statSync(p).mode & 0o077, 0, `${p} is group/world-readable`);
  }
});
