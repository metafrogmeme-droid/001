#!/usr/bin/env node
// Supply-chain ratchet for token/'s npm dependency tree.
//
// WHY THIS IS NOT `npm audit --audit-level=high`.
//
// The audit report noted that no npm SCA runs anywhere, so "a new advisory
// landing tomorrow will not surface anywhere". The obvious fix — wire
// `npm audit --audit-level=high` into CI — is the wrong one here, and wrong in a
// specific, instructive way: this tree already carries 1 critical and 15 high
// advisories (2026-07-26), almost all transitive through the Wormhole SDK. That
// gate would be RED ON ITS FIRST RUN and stay red on every unrelated pull
// request until a dependency upgrade nobody has scheduled. A permanently red
// check is not a control; it is training people to merge past a red check.
//
// So this is a ratchet, matching how ruff and mypy are already gated in this
// repo: the KNOWN advisory set is committed to .audit-baseline.json, and the
// gate fails only when something NEW appears. The pre-existing backlog is
// visible and counted but does not block; a newly-introduced vulnerable
// dependency does.
//
// It fails on either of two conditions, because they catch different things:
//   1. an advisory id that is not in the baseline  — catches a new CVE, and
//      catches swapping one high advisory for a different high advisory, which
//      a pure count comparison would miss entirely;
//   2. a per-severity count above the baseline     — catches a new advisory that
//      reports no numeric id to fingerprint.
//
// Usage:
//   node scripts/audit_gate.mjs             # gate (CI)
//   node scripts/audit_gate.mjs --update    # re-record the baseline, deliberately
import fs from 'node:fs';
import path from 'node:path';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const TOKEN_ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const BASELINE = path.join(TOKEN_ROOT, '.audit-baseline.json');
const SEVERITIES = ['critical', 'high', 'moderate', 'low'];

function runAudit() {
  // `npm audit` exits non-zero whenever anything is found, so the exit code
  // carries no information here — the JSON body does. Parse failure IS fatal
  // though: a gate that silently passes when it cannot read its own input is
  // worse than no gate.
  const res = spawnSync('npm', ['audit', '--json'], {
    cwd: TOKEN_ROOT,
    encoding: 'utf8',
    maxBuffer: 64 * 1024 * 1024,
  });
  if (!res.stdout) {
    throw new Error(
      `npm audit produced no output (status ${res.status}): ${(res.stderr || '').slice(0, 500)}`
    );
  }
  let parsed;
  try {
    parsed = JSON.parse(res.stdout);
  } catch (e) {
    throw new Error(`npm audit did not return JSON (status ${res.status}): ${e.message}`);
  }
  if (parsed.error) {
    throw new Error(`npm audit reported an error: ${JSON.stringify(parsed.error).slice(0, 500)}`);
  }
  return parsed;
}

/** Every advisory id reachable in the report, as a sorted array of strings. */
function advisoryIds(report) {
  const ids = new Set();
  for (const v of Object.values(report.vulnerabilities || {})) {
    for (const via of v.via || []) {
      // A string `via` is a package name (an indirect path to some other
      // advisory), not an advisory itself — the numeric `source` on the object
      // form is the stable identifier.
      if (via && typeof via === 'object' && via.source != null) ids.add(String(via.source));
    }
  }
  return [...ids].sort();
}

function counts(report) {
  const m = (report.metadata && report.metadata.vulnerabilities) || {};
  return Object.fromEntries(SEVERITIES.map((s) => [s, m[s] || 0]));
}

/** Human-readable detail for a set of advisory ids. */
function describe(report, ids) {
  const want = new Set(ids);
  const out = [];
  for (const [pkg, v] of Object.entries(report.vulnerabilities || {})) {
    for (const via of v.via || []) {
      if (via && typeof via === 'object' && want.has(String(via.source))) {
        out.push(`  [${via.severity || v.severity}] ${pkg} — ${via.title || 'advisory'} (${via.url || via.source})`);
      }
    }
  }
  return [...new Set(out)].sort();
}

const report = runAudit();
const nowIds = advisoryIds(report);
const nowCounts = counts(report);
const total = SEVERITIES.reduce((a, s) => a + nowCounts[s], 0);

if (process.argv.includes('--update')) {
  const baseline = {
    _comment:
      'Known npm advisories in token/, recorded deliberately. This is a RATCHET floor, ' +
      'not an approval: every entry is an outstanding vulnerability. Regenerate with ' +
      '`node scripts/audit_gate.mjs --update` only when you have reviewed the delta. ' +
      'Shrinking this file is always good; growing it needs a reason in the commit message.',
    recorded: new Date().toISOString().slice(0, 10),
    counts: nowCounts,
    advisoryIds: nowIds,
  };
  fs.writeFileSync(BASELINE, JSON.stringify(baseline, null, 2) + '\n');
  console.log(`Baseline updated: ${total} advisories (${JSON.stringify(nowCounts)})`);
  process.exit(0);
}

if (!fs.existsSync(BASELINE)) {
  console.error(`No ${path.basename(BASELINE)}. Create it with: node scripts/audit_gate.mjs --update`);
  process.exit(1);
}
const baseline = JSON.parse(fs.readFileSync(BASELINE, 'utf8'));
const baseIds = new Set(baseline.advisoryIds || []);

const newIds = nowIds.filter((id) => !baseIds.has(id));
const goneIds = [...baseIds].filter((id) => !nowIds.includes(id));
const grew = SEVERITIES.filter((s) => nowCounts[s] > (baseline.counts?.[s] ?? 0));

console.log(`npm advisories in token/: ${total} (${SEVERITIES.map((s) => `${s} ${nowCounts[s]}`).join(', ')})`);
console.log(`baseline (${baseline.recorded}):  ${SEVERITIES.map((s) => `${s} ${baseline.counts?.[s] ?? 0}`).join(', ')}`);

if (goneIds.length) {
  console.log(
    `\n${goneIds.length} baselined advisor${goneIds.length === 1 ? 'y is' : 'ies are'} gone — ` +
    'tighten the floor with `node scripts/audit_gate.mjs --update`.'
  );
}

if (!newIds.length && !grew.length) {
  console.log('\nNo new advisories. (The baselined backlog above is still outstanding.)');
  process.exit(0);
}

// Distinguish the two ways this fails, because they mean very different things
// and the wrong headline sends someone hunting for a vulnerability that is not
// there. A count can rise with no new advisory id when a fix RE-RATES existing
// ones — dropping a package out of `high` moves its dependents into whatever
// lower-severity advisory was previously masked, so `low` goes UP while the
// advisory set shrinks. That is what an improvement looks like, and calling it
// "NEW ADVISORIES" is simply wrong.
if (newIds.length) {
  console.error('\n=== NEW SUPPLY-CHAIN ADVISORIES ===');
  console.error(`${newIds.length} advisory id(s) not in the baseline:`);
  for (const line of describe(report, newIds)) console.error(line);
  for (const s of grew) {
    console.error(`  ${s} count rose ${baseline.counts?.[s] ?? 0} -> ${nowCounts[s]}`);
  }
  console.error(
    '\nThese packages sign privileged transactions. Upgrade or replace the affected\n' +
    'dependency. If the advisory is genuinely not applicable, say why in the commit\n' +
    'message and re-record with `node scripts/audit_gate.mjs --update`.'
  );
} else {
  console.error('\n=== SEVERITY COUNT ROSE, BUT NO NEW ADVISORY ===');
  for (const s of grew) {
    console.error(`  ${s}: ${baseline.counts?.[s] ?? 0} -> ${nowCounts[s]}`);
  }
  console.error(
    `\nEvery advisory id is already in the baseline (${nowIds.length} now vs ` +
    `${(baseline.advisoryIds || []).length} recorded), so nothing new was introduced — the\n` +
    'severity mix was re-rated, which is what happens when a fix drops packages out of a\n' +
    'higher band. Confirm that reading, then re-record with\n' +
    '`node scripts/audit_gate.mjs --update`.'
  );
}
process.exit(1);
