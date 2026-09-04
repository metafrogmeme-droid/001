// The advisory gate's THREE outcomes, driven with a stubbed `npm` on PATH.
//
// 2026-09-04: four CI jobs failed with
//     Error: npm audit reported an error: {"summary":"","detail":""}
// which from the outside is indistinguishable from this gate reporting a
// supply-chain finding. It was not one — that empty envelope is npm's
// fingerprint for "the advisory endpoint could not be read". These tests pin
// the difference, because the cost of confusing them is an investigation into
// a CVE that was never reported, and the cost of confusing them the OTHER way
// would be shipping past a real one.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const GATE = path.join(path.dirname(fileURLToPath(import.meta.url)), 'audit_gate.mjs');

/** A tree with its own baseline, so these tests never depend on the repo's. */
function tree(advisoryIds = [], counts = { critical: 0, high: 0, moderate: 0, low: 0 }) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'gate-tree-'));
  fs.writeFileSync(
    path.join(dir, '.audit-baseline.json'),
    JSON.stringify({ _comment: 'test', recorded: '2026-01-01', counts, advisoryIds }, null, 2)
  );
  return dir;
}

/** Run the gate with `npm` replaced by a script printing exactly `stdout`. */
function run(stdout, root) {
  const bin = fs.mkdtempSync(path.join(os.tmpdir(), 'fake-npm-'));
  fs.writeFileSync(path.join(bin, 'npm'), `#!/bin/sh\ncat <<'JSON'\n${stdout}\nJSON\nexit 1\n`);
  fs.chmodSync(path.join(bin, 'npm'), 0o755);
  const res = spawnSync('node', [GATE, root], {
    encoding: 'utf8',
    env: { ...process.env, PATH: `${bin}:${process.env.PATH}` },
    // Longer than the gate's own TOTAL_BUDGET_MS. A stub that fails INSTANTLY
    // fits many attempts inside that budget, so the unreadable case runs for
    // most of it — killing the child here would report the gate as broken when
    // it is doing exactly what it should.
    timeout: 700_000,
  });
  return { code: res.status, out: (res.stdout || '') + (res.stderr || '') };
}

const CLEAN = JSON.stringify({
  vulnerabilities: {},
  metadata: { vulnerabilities: { critical: 0, high: 0, moderate: 0, low: 0 } },
});

test('a real npm error is a VERDICT: exit 1, and not retried', () => {
  const r = run(JSON.stringify({
    error: { code: 'E401', summary: 'Unauthorized', detail: 'check your token' },
  }), tree());
  assert.equal(r.code, 1);
  assert.match(r.out, /Unauthorized/);
  assert.doesNotMatch(r.out, /could not read the advisory data/,
    'a specific answer must not be retried');
});

test('a clean tree against an empty baseline passes: exit 0', () => {
  const r = run(CLEAN, tree());
  assert.equal(r.code, 0);
  assert.match(r.out, /No new advisories/);
});

test('a report with no vulnerability metadata is unreadable, not clean: exit 3', () => {
  const r = run(JSON.stringify({ vulnerabilities: {} }), tree());
  assert.equal(r.code, 3);
  assert.match(r.out, /COULD NOT CHECK/);
});

test('every baselined advisory vanishing at once is not an all-clear: exit 3', () => {
  // A degraded registry answering 200 with nothing looks exactly like a tree
  // that was fully remediated. Only one of those is good news.
  const r = run(CLEAN, tree(['1103747', '1113686'], { critical: 0, high: 2, moderate: 0, low: 0 }));
  assert.equal(r.code, 3);
  assert.match(r.out, /2 baselined advisories vanished/);
  assert.match(r.out, /--update/, 'a genuine remediation needs a stated way through');
});

test('an audit that is SLOW but succeeds is not killed by the cap', () => {
  // Measured against the live registry: three consecutive audits of one tree
  // took 1s, 2s and 127s, and all three SUCCEEDED. The first version of this
  // gate capped an attempt at 90s and would have reported "could not read"
  // about a registry that was answering.
  const bin = fs.mkdtempSync(path.join(os.tmpdir(), 'slow-npm-'));
  fs.writeFileSync(path.join(bin, 'npm'),
    `#!/bin/sh\nsleep 100\ncat <<'JSON'\n${CLEAN}\nJSON\nexit 0\n`);
  fs.chmodSync(path.join(bin, 'npm'), 0o755);
  const res = spawnSync('node', [GATE, tree()], {
    encoding: 'utf8',
    env: { ...process.env, PATH: `${bin}:${process.env.PATH}` },
    timeout: 400_000,
  });
  assert.equal(res.status, 0, (res.stdout || '') + (res.stderr || ''));
});

test('an EMPTY error envelope is could-not-check, retried, exit 3 — never an advisory report', () => {
  const r = run(JSON.stringify({
    error: { summary: '', detail: '' },
    message: 'request to https://registry.npmjs.org/-/npm/v1/security/advisories/bulk failed, reason: socket hang up',
    statusCode: 503,
  }), tree());
  assert.equal(r.code, 3, 'the registry being unreachable is not a verdict');
  assert.match(r.out, /COULD NOT CHECK/);
  assert.doesNotMatch(r.out, /NEW SUPPLY-CHAIN/, 'it must not read as a finding');
  assert.match(r.out, /attempt 1 could not read/, 'a blip deserves a second look');
  assert.match(r.out, /of budget left/, 'the budget it is spending must be visible');
  // npm DOES send a diagnosis; the old code sliced the one field that is
  // always empty on this path and threw the rest away.
  assert.match(r.out, /socket hang up/);
  assert.match(r.out, /statusCode=503/);
});
