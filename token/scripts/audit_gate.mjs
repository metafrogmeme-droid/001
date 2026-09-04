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
//
// EXIT CODES — THREE OUTCOMES, NOT TWO. The same vocabulary
// scripts/verify_deploy.sh and scripts/verify_deploy_source.sh already use,
// for the reason verify_deploy.sh's own header gives: reporting an
// unreachable endpoint as a failure sends someone to fix a problem that does
// not exist.
//
//   0  the tree was audited and nothing new appeared        A VERDICT
//   1  a new advisory, or a severity count above the floor   A VERDICT
//   3  the advisory data could not be READ                   NOT a verdict
//
// 3 is still a FAILING exit code and CI still goes red on it — these packages
// sign privileged transactions, so an unread audit is never a pass. What it
// buys is that nobody hunts a CVE that was never reported, and nobody runs
// --update against a report that was never read.
import fs from 'node:fs';
import path from 'node:path';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const TOKEN_ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');

// M3: which tree to audit. Defaults to token/ — this script's own package — so
// the existing invocation is byte-identical. `app/` passes its own root as a
// positional argument rather than getting a SECOND copy of this ratchet, which
// would then drift: the whole argument of the comment above is that the gate's
// shape is the hard part, and it is the same shape for any npm tree.
//
// A POSITIONAL ARG, NOT AN ENV VAR, deliberately. scripts/preflight.py runs CI
// steps by parsing the `run:` string out of ci.yml and ignores each step's
// `env:` block — so an AUDIT_ROOT environment variable would be set in CI and
// unset locally, and preflight would silently audit token/ while reporting the
// app/ step as passing. A gate that checks the wrong tree and says nothing is
// worse than no gate.
const _argRoot = process.argv.slice(2).find((a) => !a.startsWith('--'));
const ROOT = _argRoot ? path.resolve(_argRoot) : TOKEN_ROOT;
const BASELINE = path.join(ROOT, '.audit-baseline.json');
const SEVERITIES = ['critical', 'high', 'moderate', 'low'];
// The tree's own name, for every message this prints. Hardcoding "token/"
// here would have had the app/ run report advisories "in token/" — a gate
// naming the wrong subject in its own output is how somebody fixes the
// wrong tree.
const TREE = path.basename(ROOT);

/** Raised when the advisory data could not be READ. Exits 3, never 1. */
class Unreadable extends Error {}

// BOUND THE TOTAL, NOT THE ATTEMPT COUNT.
//
// npm does its own retrying underneath (fetch-retries 2, a 10s..60s backoff,
// two advisory endpoints, each request patient to fetch-timeout = 5 minutes),
// so one unbounded invocation can run for well over half an hour. Every job
// that runs this gate has a timeout-minutes between 10 and 20 — anchor-workspace
// has the least at 10 — and a job killed by ITS OWN timeout prints no message
// at all, which is strictly worse than a red step. In the web app's job the SCA
// step runs BEFORE the suite, so an unbounded audit takes the whole test run
// down with it.
//
// A FIRST ATTEMPT AT THIS CAPPED EACH TRY AT 90s AND ALLOWED THREE, AND THE CAP
// WAS ITSELF THE BUG. Measured against the live registry: three consecutive
// audits of the same tree took 1s, 2s and 127s — and all three SUCCEEDED. A 90s
// cap kills that third one and reports "could not read" about a registry that
// was answering, which is the same failure this gate exists to prevent, one
// level up. 150s sits above the slow-but-working case with margin.
//
// The attempt COUNT then has to float, because a fixed one multiplied by a
// generous cap does not fit in 10 minutes. Bounding the total instead is both
// safer and more useful: a registry refusing fast gives many tries inside the
// budget, a slow one gives fewer, and the worst case is stated once here rather
// than being an emergent product of three constants.
const ATTEMPT_TIMEOUT_MS = 150_000;
const TOTAL_BUDGET_MS = 330_000;     // 5.5 min, inside anchor-workspace's 10
const BACKOFF_MS = [10_000, 20_000, 20_000];

function sleepSync(ms) {
  // No await at module scope in the middle of a sync gate; this is the
  // standard synchronous sleep and needs no dependency.
  Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0, ms);
}

/** npm's OWN diagnosis of a failed audit, which it does send and we dropped.
 *
 * `npm audit --json` merges its error buffer into the output object, so a
 * failed run carries `message` ("request to .../security/advisories/bulk
 * failed, reason: ..."), `statusCode` and `uri` alongside the error envelope.
 * The old code sliced `parsed.error` — the one field that is ALWAYS EMPTY on
 * the registry-unreachable path — and threw the rest away. That is why four CI
 * jobs failed on 2026-09-04 with `{"summary":"","detail":""}` and no cause.
 */
function npmDiagnosis(parsed, res) {
  const bits = [];
  for (const k of ['message', 'statusCode', 'uri', 'method', 'code']) {
    const v = parsed && parsed[k];
    if (v !== undefined && v !== null && v !== '') bits.push(`${k}=${String(v).slice(0, 200)}`);
  }
  const err = (parsed && parsed.error) || {};
  for (const k of ['code', 'summary', 'detail']) {
    if (err[k]) bits.push(`error.${k}=${String(err[k]).slice(0, 200)}`);
  }
  const stderr = (res && res.stderr ? String(res.stderr) : '').trim();
  if (stderr) bits.push(`stderr=${stderr.slice(0, 300)}`);
  return bits.length ? bits.join(' | ') : '(npm sent no diagnosis at all)';
}

/** True when npm's error envelope is EMPTY — its fingerprint for "the advisory
 *  endpoint could not be read", never for a finding.
 *
 *  Provenance, because none of it is guessable from the message: arborist sets
 *  `.error` only after BOTH the bulk and quick advisory endpoints fail, npm
 *  then THROWS A BARE STRING, and its JSON formatter reads `.code`/`.summary`/
 *  `.detail` off that string — yielding `{summary:'', detail:''}` with `code`
 *  undefined and dropped by JSON.stringify. A real advisory never lands here:
 *  a found vulnerability sets process.exitCode and throws nothing, so
 *  `parsed.error` is absent entirely.
 */
function isEmptyErrorEnvelope(err) {
  if (!err || typeof err !== 'object') return false;
  return !err.code && !err.summary && !err.detail;
}

function auditOnce() {
  // --fetch-retries=0 hands the retrying to the loop below, which can SAY what
  // it is doing. npm's own retries burn the same wall-clock invisibly, inside
  // an attempt we then have to cap; moving that budget out here buys more real
  // attempts and an honest log line for each. It does not make the gate more
  // fragile: every retry npm would have done, runAudit() does, with backoff.
  const res = spawnSync('npm', ['audit', '--json', '--fetch-retries=0'], {
    cwd: ROOT,
    encoding: 'utf8',
    maxBuffer: 64 * 1024 * 1024,
    timeout: ATTEMPT_TIMEOUT_MS,
  });
  if (res.error && res.error.code === 'ETIMEDOUT') {
    throw new Unreadable(
      `npm audit exceeded its ${ATTEMPT_TIMEOUT_MS / 1000}s cap (the registry did not answer)`
    );
  }
  if (!res.stdout) {
    throw new Unreadable(
      `npm audit produced no output (status ${res.status}): ${(res.stderr || '').slice(0, 500)}`
    );
  }
  let parsed;
  try {
    parsed = JSON.parse(res.stdout);
  } catch (e) {
    // Not Unreadable: a non-JSON body from a tool invoked with --json is a
    // broken toolchain, not a network blip, and retrying cannot help.
    throw new Error(`npm audit did not return JSON (status ${res.status}): ${e.message}`);
  }
  if (parsed.error) {
    const detail = npmDiagnosis(parsed, res);
    if (isEmptyErrorEnvelope(parsed.error)) {
      throw new Unreadable(`npm could not read the advisory endpoint — ${detail}`);
    }
    // A NON-empty envelope is npm saying something specific. Retrying it would
    // just repeat the same answer more slowly.
    throw new Error(`npm audit reported an error: ${detail}`);
  }
  return parsed;
}

function runAudit() {
  // `npm audit` exits non-zero whenever anything is found, so the exit code
  // carries no information here — the JSON body does. Parse failure IS fatal
  // though: a gate that silently passes when it cannot read its own input is
  // worse than no gate.
  //
  // Retried ONLY for the unreadable case, and only a bounded number of times.
  // This does not make the gate lenient: exhausting the attempts still fails
  // the build. It converts a single blip into a pass and a real outage into an
  // honest "could not check" instead of a fabricated advisory report.
  const started = Date.now();
  const spent = () => Date.now() - started;
  let last, attempt = 0;
  for (;;) {
    attempt++;
    try {
      return auditOnce();
    } catch (e) {
      if (!(e instanceof Unreadable)) throw e;
      last = e;
      const wait = BACKOFF_MS[attempt - 1] ?? BACKOFF_MS[BACKOFF_MS.length - 1];
      // Only start another attempt if a WHOLE one still fits. Starting one we
      // would have to abandon spends the budget and answers nothing.
      if (spent() + wait + ATTEMPT_TIMEOUT_MS > TOTAL_BUDGET_MS) break;
      console.error(
        `npm audit attempt ${attempt} could not read the advisory data ` +
        `(${e.message}); retrying in ${wait / 1000}s ` +
        `(${Math.round((TOTAL_BUDGET_MS - spent()) / 1000)}s of budget left)`
      );
      sleepSync(wait);
    }
  }
  throw new Unreadable(
    `the advisory data for ${TREE}/ could not be read in ${Math.round(spent() / 1000)}s ` +
    `across ${attempt} attempt(s). Last: ${last ? last.message : 'unknown'}`
  );
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
  // `(... ) || {}` then `m[s] || 0` scored a report WITHOUT metadata as a
  // perfectly clean tree — the house rule's own shape, in the gate that exists
  // to enforce it. It is unreachable today only because npm happens to set
  // `.error` on every failing path, which is a property of npm's output, not
  // something this gate asserts. Guard at the boundary so it stays unreachable.
  const m = report && report.metadata && report.metadata.vulnerabilities;
  if (!m || typeof m !== 'object') {
    throw new Unreadable(
      'npm audit returned a report with no vulnerability metadata — ' +
      'a count cannot be read from it, and zero is not the answer'
    );
  }
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

let report, nowIds, nowCounts, total;
try {
  report = runAudit();
  nowIds = advisoryIds(report);
  nowCounts = counts(report);
  total = SEVERITIES.reduce((a, s) => a + nowCounts[s], 0);
} catch (e) {
  if (e instanceof Unreadable) {
    // EXIT 3: could not be checked. Not a verdict, and deliberately not exit 1
    // — the last four times this happened, the message read exactly like a
    // supply-chain finding and cost an investigation each time. Still red.
    console.error(`\nCOULD NOT CHECK ${TREE}/ — ${e.message}`);
    console.error(
      'This is NOT an advisory report: nothing was scored, and no conclusion\n' +
      'about this tree can be drawn from it. The build fails anyway, because an\n' +
      'unread audit is not a pass. Re-run when the registry is reachable.'
    );
    process.exit(3);
  }
  throw e;
}

if (process.argv.includes('--update')) {
  const baseline = {
    _comment:
      `Known npm advisories in ${TREE}/, recorded deliberately. This is a RATCHET floor, ` +
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
  console.error(`No ${BASELINE}. Create it with: node token/scripts/audit_gate.mjs ${ROOT} --update`);
  process.exit(1);
}
const baseline = JSON.parse(fs.readFileSync(BASELINE, 'utf8'));
const baseIds = new Set(baseline.advisoryIds || []);

const newIds = nowIds.filter((id) => !baseIds.has(id));
const goneIds = [...baseIds].filter((id) => !nowIds.includes(id));
const grew = SEVERITIES.filter((s) => nowCounts[s] > (baseline.counts?.[s] ?? 0));

console.log(`npm advisories in ${TREE}/: ${total} (${SEVERITIES.map((s) => `${s} ${nowCounts[s]}`).join(', ')})`);
console.log(`baseline (${baseline.recorded}):  ${SEVERITIES.map((s) => `${s} ${baseline.counts?.[s] ?? 0}`).join(', ')}`);

if (goneIds.length) {
  console.log(
    `\n${goneIds.length} baselined advisor${goneIds.length === 1 ? 'y is' : 'ies are'} gone — ` +
    'tighten the floor with `node scripts/audit_gate.mjs --update`.'
  );
}

// A TOTAL WIPEOUT IS NOT AN ALL-CLEAR UNTIL SOMEBODY SAYS SO.
//
// The failure above (an empty error envelope) is npm's LOUD way of failing.
// The quiet way is a 200 carrying no advisories at all: it parses, counts to
// zero, satisfies both conditions and prints "No new advisories" on the way to
// exit 0 — silent AND green, on the gate whose entire job is to be neither.
// Nothing distinguishes that from a genuine full remediation except intent, so
// this asks for the intent rather than guessing it. `goneIds` already knows;
// it was only ever spent on an encouragement.
//
// Only trees with something recorded can be protected this way: app/ and
// site/ have empty baselines by construction and no check here can help them.
if (total === 0 && baseIds.size > 0) {
  console.error(
    `\nCOULD NOT CHECK ${TREE}/ — every one of the ${baseIds.size} baselined advisor` +
    `${baseIds.size === 1 ? 'y' : 'ies'} vanished at once and the tree now reports NOTHING.`
  );
  console.error(
    'A registry that answers but knows nothing looks exactly like a tree that was\n' +
    'fully remediated, and only one of those is good news. If the dependencies\n' +
    'really were fixed, say so in the commit message and record it deliberately:\n' +
    '  node token/scripts/audit_gate.mjs ' + ROOT + ' --update'
  );
  process.exit(3);
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
