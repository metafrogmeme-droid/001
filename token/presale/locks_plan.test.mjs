// The locks plan: what Smithii's Token Vesting tool can create for the three vested buckets, derived to dates and
// amounts, held against numbers computed independently.
//
// WHAT "INDEPENDENT" MEANS HERE
//
// The expected instants and token amounts below were computed in Python with the calendar module and
// arbitrary-precision integers, not by asking locks_lib.mjs, so the library cannot agree with itself. The
// limits themselves (five steps, whole percents summing to 100, one vesting per wallet per token) were read
// from the tool's own code and from real vestings on mainnet-beta on 2026-10-09; locks.plan.json records how.
//
// Every refusal is mutation-tested: the clean plan must pass first, so a case cannot pass for the wrong reason.
import { test } from 'node:test';
import assert from 'node:assert/strict';

import {
  addMonthsUtc,
  commonGap,
  deriveLocks,
  describeLock,
  fundingPlan,
  loadLocksPlan,
  lockDescription,
  locksProblems,
  shortNote,
} from './locks_lib.mjs';
import { renderLocksPlan } from './locks_plan.mjs';
import { loadAllocation } from './roadmap_table.mjs';
import { loadSmithiiConfig, loadTokenRecord } from './smithii_lib.mjs';
import { spawnSync } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));

const plan = () => structuredClone(loadLocksPlan());
const cfg = loadSmithiiConfig();
const record = loadTokenRecord();
const table = () => structuredClone(loadAllocation());
const ctx = () => ({ rows: table(), cfg, record });

test('the real plan is one the tool can create, against the real roadmap table and sale', () => {
  assert.deepEqual(locksProblems(plan(), ctx()), []);
  assert.equal(plan().locks.length, 3);
  assert.ok(plan().tool.limits.maxSteps === 5, 'the limit this file holds the plan to is the tool\'s own');
});

// ── calendar months in UTC ──────────────────────────────────────────────────

test('adding calendar months keeps the clock time and clamps a day the month lacks', () => {
  const at = (iso) => Date.parse(iso);
  const iso = (ms) => new Date(ms).toISOString();
  assert.equal(iso(addMonthsUtc(at('2026-10-29T14:00:00Z'), 9)), '2027-07-29T14:00:00.000Z');
  assert.equal(iso(addMonthsUtc(at('2026-10-29T14:00:00Z'), 36)), '2029-10-29T14:00:00.000Z');
  assert.equal(iso(addMonthsUtc(at('2026-10-29T14:00:00Z'), 0)), '2026-10-29T14:00:00.000Z');
  assert.equal(iso(addMonthsUtc(at('2027-01-31T09:30:00Z'), 1)), '2027-02-28T09:30:00.000Z', '31 Jan + 1 month is the last day of February');
  assert.equal(iso(addMonthsUtc(at('2028-01-31T09:30:00Z'), 1)), '2028-02-29T09:30:00.000Z', 'in a leap year it is the 29th');
  assert.equal(iso(addMonthsUtc(at('2028-02-29T09:30:00Z'), 12)), '2029-02-28T09:30:00.000Z');
  assert.equal(iso(addMonthsUtc(at('2026-11-30T09:30:00Z'), 3)), '2027-02-28T09:30:00.000Z');
});

// ── the derived locks, against numbers computed elsewhere ───────────────────

const EXPECT = {
  'Community & ecosystem': {
    tokens: 250_000_000n,
    steps: [
      ['2026-10-29T14:00:00Z', 20, 50_000_000n, 50_000_000n],
      ['2027-07-29T14:00:00Z', 20, 50_000_000n, 100_000_000n],
      ['2028-04-29T14:00:00Z', 20, 50_000_000n, 150_000_000n],
      ['2029-01-29T14:00:00Z', 20, 50_000_000n, 200_000_000n],
      ['2029-10-29T14:00:00Z', 20, 50_000_000n, 250_000_000n],
    ],
  },
  'Team & contributors': {
    tokens: 150_000_000n,
    steps: [
      ['2028-04-29T14:00:00Z', 25, 37_500_000n, 37_500_000n],
      ['2028-10-29T14:00:00Z', 25, 37_500_000n, 75_000_000n],
      ['2029-04-29T14:00:00Z', 25, 37_500_000n, 112_500_000n],
      ['2029-10-29T14:00:00Z', 25, 37_500_000n, 150_000_000n],
    ],
  },
  Advisors: {
    tokens: 20_000_000n,
    steps: [
      ['2027-10-29T14:00:00Z', 33, 6_600_000n, 6_600_000n],
      ['2028-04-29T14:00:00Z', 33, 6_600_000n, 13_200_000n],
      ['2028-10-29T14:00:00Z', 34, 6_800_000n, 20_000_000n],
    ],
  },
};

test('the dates and whole-token amounts of each step are the ones computed independently, from the sale end 2026-10-29 14:00 UTC', () => {
  const locks = deriveLocks(plan(), ctx());
  assert.equal(locks.length, 3);
  for (const l of locks) {
    const want = EXPECT[l.bucket];
    assert.ok(want, `an unexpected bucket ${l.bucket}`);
    assert.equal(l.tokens, want.tokens, l.bucket);
    assert.deepEqual(l.steps.map((s) => [s.atUtc, s.percent, s.tokens, s.cumulativeTokens]), want.steps, l.bucket);
    assert.equal(l.steps.at(-1).cumulativeTokens, l.tokens, `${l.bucket}: the steps release exactly the whole bucket`);
  }
  assert.equal(locks.find((l) => l.bucket === 'Community & ecosystem').steps[0].atMs / 1000, 1793282400, 'T0 as epoch seconds, from Python');
});

test('the funding table: what each wallet holds before the sale opens, and that it adds up to the supply', () => {
  const f = fundingPlan({ cfg, rows: table(), record, locks: deriveLocks(plan(), ctx()) });
  assert.equal(f.sale.escrow, 150_001_500_015_000_150n, 'hard cap / the stored price, in base units');
  assert.equal(f.sale.pool, 120_001_200_012_000_120n, '80% of the hard cap at the stored price');
  assert.equal(f.sale.total, 270_002_700_027_000_270n);
  assert.deepEqual(f.locks.map((l) => [l.bucket, l.base]), [
    ['Community & ecosystem', 250_000_000_000_000_000n],
    ['Team & contributors', 150_000_000_000_000_000n],
    ['Advisors', 20_000_000_000_000_000n],
  ]);
  assert.equal(f.multisig.treasury, 200_000_000_000_000_000n);
  assert.equal(f.multisig.partners, 80_000_000_000_000_000n);
  assert.equal(f.multisig.total, 309_997_299_972_999_730n);
  assert.equal(f.multisig.reserve, 29_997_299_972_999_730n);
  const all = f.sale.total + f.locks.reduce((a, l) => a + l.base, 0n) + f.multisig.total;
  assert.equal(all, f.supply, 'nothing is counted twice and nothing is missing');
});

// ── what the tool cannot create ─────────────────────────────────────────────

const MUTATIONS = [
  ['six steps (the tool allows five)', (p) => { p.locks[0].steps.push({ months: 45, percent: 1 }); p.locks[0].steps[0].percent = 19; }, /6 steps; the tool allows 5/],
  ['percents that sum to 99', (p) => { p.locks[1].steps[0].percent = 24; }, /Team & contributors: the percents must sum to exactly 100/],
  ['a fractional percent', (p) => { p.locks[1].steps[0].percent = 12.5; p.locks[1].steps[1].percent = 37.5; }, /every percent must be a whole number from 1 to 100/],
  ['a zero percent', (p) => { p.locks[1].steps[0].percent = 0; p.locks[1].steps[1].percent = 50; }, /every percent must be a whole number from 1 to 100/],
  ['months that do not ascend', (p) => { p.locks[1].steps[1].months = 18; }, /step months must be whole numbers, 0 or more, strictly ascending/],
  ['a step in fractional months', (p) => { p.locks[1].steps[0].months = 18.5; }, /step months must be whole numbers/],
  ['a last step after the published end', (p) => { p.locks[1].steps[3].months = 42; }, /last step is at month 42; the published schedule ends at month 36/],
  ['a community release that ends at month 28', (p) => { p.locks[0].steps.forEach((s, i) => { s.months = 7 * i; }); }, /last step is at month 28; the published release ends at month 36/],
  ['a community release that starts late (that is a cliff)', (p) => { for (const s of p.locks[0].steps) s.months = s.months === 0 ? 3 : s.months; }, /no cliff, so the first step must be at month 0/],
  ['a first team unlock inside the cliff', (p) => { p.locks[1].steps = [{ months: 12, percent: 25 }, { months: 20, percent: 25 }, { months: 28, percent: 25 }, { months: 36, percent: 25 }]; }, /first unlock should come one gap \(8 months\) after the 12-month cliff/],
  ['team unlocks ahead of the published line', (p) => { p.locks[1].steps = [{ months: 18, percent: 40 }, { months: 24, percent: 20 }, { months: 30, percent: 20 }, { months: 36, percent: 20 }]; }, /by month 18 the plan has unlocked 40% where the published linear schedule has 25\.0%/],
  ['uneven spacing', (p) => { p.locks[2].steps = [{ months: 12, percent: 33 }, { months: 14, percent: 33 }, { months: 24, percent: 34 }]; }, /not evenly spaced/],
  ['an uppercase url', (p) => { p.locks[0].slug = 'RCLAW-Community'; }, /must be lowercase letters, digits and hyphens, at most 30 characters/],
  ['a 31-character url', (p) => { p.locks[0].slug = 'a'.repeat(31); }, /at most 30 characters/],
  ['two locks with one url', (p) => { p.locks[1].slug = p.locks[0].slug; }, /the url rclaw-community is used twice/],
  ['a bucket the roadmap does not have', (p) => { p.locks[2].bucket = 'Marketing'; }, /Marketing: matches 0 rows of the roadmap/],
  ['a bucket name that fits two rows', (p) => { p.locks[2].bucket = 'T'; }, /T: matches 2 rows of the roadmap/],
  ['a description that would not fit the tool\'s box', (p) => { p.tool.limits.descriptionMaxLength = 100; }, /the description would not fit the tool's 100-character box/],
  ['a bucket planned twice', (p) => { p.locks[2].bucket = p.locks[1].bucket; p.locks[2].slug = 'rclaw-team-2'; }, /Team & contributors is planned twice/],
  ['a target of no known shape', (p) => { p.locks[0].target = { somethingElse: 1 }; }, /the target must be \{ releaseMonths \} or \{ cliffMonths, linearMonths \}/],
  ['a plan with no locks', (p) => { p.locks = []; }, /the plan has no locks/],
];

for (const [name, mutate, expected] of MUTATIONS) {
  test(`the plan is refused: ${name}`, () => {
    assert.deepEqual(locksProblems(plan(), ctx()), [], 'the clean plan must pass, or every case below passes for the wrong reason');
    const p = plan();
    mutate(p);
    const problems = locksProblems(p, ctx());
    assert.ok(problems.some((m) => expected.test(m)), `expected ${expected}; got ${JSON.stringify(problems)}`);
  });
}

test('a step that is not a whole number of tokens is refused: the tool takes whole tokens only', () => {
  const rows = table();
  rows.rows.find((r) => r.label.startsWith('Team')).tokens = 150_000_001;
  const problems = locksProblems(plan(), { rows, cfg, record });
  assert.ok(problems.some((m) => /25% of 150000001 tokens is not a whole number of tokens/.test(m)), JSON.stringify(problems));
});

test('a plan whose wallets need more than the supply is refused, with the shortfall', () => {
  const rows = table();
  rows.rows.find((r) => r.label.startsWith('Treasury')).tokens = 300_000_000;
  const problems = locksProblems(plan(), { rows, cfg, record });
  assert.ok(problems.some((m) => /the reserve would be negative/.test(m)), JSON.stringify(problems));
});

// ── the words ───────────────────────────────────────────────────────────────

test('the one-sentence and one-line descriptions, derived from the steps', () => {
  const byName = Object.fromEntries(plan().locks.map((l) => [l.bucket, l]));
  assert.equal(describeLock(byName['Community & ecosystem']), '5 unlocks of 20%: at TGE, then every 9 months (the last at month 36)');
  assert.equal(describeLock(byName['Team & contributors']), '12-month cliff, then 4 unlocks of 25% every 6 months (the last at month 36)');
  assert.equal(describeLock(byName.Advisors), '6-month cliff, then 3 unlocks (33%, 33%, 34%) every 6 months (the last at month 24)');
  assert.equal(shortNote(byName['Community & ecosystem']), '5 unlocks: at TGE, then every 9 mo');
  assert.equal(shortNote(byName['Team & contributors']), '12-mo cliff, then 4 unlocks every 6 mo');
  assert.equal(shortNote(byName.Advisors), '6-mo cliff, then 3 unlocks every 6 mo');
  assert.equal(commonGap(byName.Advisors.steps), 6);
  assert.equal(commonGap([{ months: 0 }, { months: 3 }, { months: 7 }]), null);
});

test('the roadmap table states each schedule in the words the plan derives: the docs follow the code', () => {
  const rows = table().rows;
  for (const lock of plan().locks) {
    const row = rows.find((r) => r.label.startsWith(lock.bucket));
    assert.ok(row.vesting.includes(describeLock(lock)), `${lock.bucket}: the roadmap's vesting cell must say "${describeLock(lock)}": ${row.vesting}`);
    assert.match(row.vesting, /Smithii Vesting/, `${lock.bucket}: the roadmap must name the tool that creates the lock`);
    assert.doesNotMatch(row.vesting, /Streamflow|linear/i, `${lock.bucket}: the roadmap must not keep promising what Smithii's tool cannot do`);
  }
});

test('the GitBook states each schedule in the words the plan derives, and does not promise Streamflow or linear release', () => {
  // Prose wraps: compare on collapsed whitespace, so a line break cannot hide a sentence.
  const gitbook = fs.readFileSync(path.join(HERE, '..', '..', 'docs', 'gitbook', 'token-roadmap.md'), 'utf8').replace(/\s+/g, ' ');
  for (const lock of plan().locks) {
    assert.ok(gitbook.includes(describeLock(lock)), `the GitBook must say "${describeLock(lock)}" for ${lock.bucket}`);
  }
  assert.ok(gitbook.includes('no linear mode'), 'the GitBook must say why these are steps');
  assert.ok(gitbook.includes('not called audited'), 'the GitBook must not let the locks borrow the audit of another program');
  assert.doesNotMatch(gitbook, /vesting streams/i, 'the GitBook must not still promise streams');
});

test('the description each lock carries fits the tool\'s 500-character box, names the mint and the TGE', () => {
  for (const l of deriveLocks(plan(), ctx())) {
    assert.ok(l.description.length <= plan().tool.limits.descriptionMaxLength, `${l.bucket}: ${l.description.length} characters`);
    assert.ok(l.description.includes(record.mint), 'the certificate must name the one mint');
    assert.ok(l.description.includes('2026-10-29 14:00 UTC'), 'and the TGE it counts from');
  }
  assert.match(lockDescription(plan().locks[1], { tokens: 150_000_000n, symbol: 'RCLAW', mint: 'M', endUtc: '2026-10-29T14:00:00Z' }), /^RUNECLAW \(\$RCLAW\) Team & contributors allocation: 150,000,000 RCLAW\. 12-month cliff/);
});

test('the printed plan carries the form entries, the local clock times on request, and the caveats', () => {
  const p = plan();
  const plain = renderLocksPlan(p, ctx());
  assert.match(plain, /LOCK 1 — Community & ecosystem · 250,000,000 RCLAW/);
  assert.match(plain, /Vesting url \.+ rclaw-team/);
  const blocks = plain.split(/^LOCK \d — /m).slice(1);
  assert.equal(blocks.length, 3, 'one block per lock');
  const [community, team, advisors] = blocks;
  assert.match(community, /^Community & ecosystem/);
  assert.match(community, /Step amount \.+ 5\n/);
  assert.match(team, /^Team & contributors/);
  assert.match(team, /Step amount \.+ 4\n/);
  assert.match(advisors, /^Advisors/);
  assert.match(advisors, /Step amount \.+ 3\n/);
  assert.match(plain, /Period 3 \.+ End Date 2029-04-29 14:00 UTC · 25%  → "Tokens available to Claim on end date" 112,500,000/);
  assert.match(plain, /ONE vesting per wallet per token/);
  assert.match(plain, /do not call these locks audited/);
  assert.match(plain, /Sale wallet \.+ 270,002,700\.027 RCLAW/);
  assert.match(plain, /Multisig \.+ 309,997,299\.972 RCLAW/);
  assert.doesNotMatch(plain, /\(UTC\+[12]\)/, 'no local times unless a zone is asked for');
  const ams = renderLocksPlan(p, ctx(), { tz: 'Europe/Amsterdam' });
  assert.match(ams, /Period 1 \.+ End Date 2026-10-29 14:00 UTC {3}\(29 Oct 2026, 15:00 \(UTC\+1\)\)/);
  assert.match(ams, /Period 2 \.+ End Date 2027-07-29 14:00 UTC {3}\(29 Jul 2027, 16:00 \(UTC\+2\)\)/, 'a summer date is two hours ahead of UTC there');
});

test('a card that names a file claims it exists: the image the plan tells the operator to upload is in the repo, at the size the tool asks', () => {
  const plain = renderLocksPlan(plan(), ctx());
  const named = [...plain.matchAll(/Tokenomics Image \(optional, an upload, (\d+)×(\d+)\): (docs\/assets\/presale\/[\w.-]+\.png)/g)];
  assert.equal(named.length, 3, 'each of the three locks names the image once');
  for (const [, w, h, rel] of named) {
    const file = path.join(path.dirname(fileURLToPath(import.meta.url)), '..', '..', rel);
    assert.ok(fs.existsSync(file), `${rel} is named in the printed plan but is not in the repository`);
    const head = fs.readFileSync(file).subarray(0, 24);
    assert.deepEqual([head.readUInt32BE(16), head.readUInt32BE(20)], [Number(w), Number(h)], `${rel} is not the ${w}×${h} the plan says the tool asks for`);
  }
});

test('an unevenly spaced lock has no one-line note: it is refused, not worded as if it were even', () => {
  const p = plan();
  p.locks[2].steps = [{ months: 12, percent: 33 }, { months: 14, percent: 33 }, { months: 24, percent: 34 }];
  assert.equal(commonGap(p.locks[2].steps), null);
  assert.throws(() => shortNote(p.locks[2]), /uneven spacing has no one-line note/);
  assert.equal(describeLock(p.locks[2]), '3 unlocks at months 12, 14, 24 (the last at month 24)', 'the long sentence still names every month');
});

test('the command: a plan the tool can create exits 0; one it cannot prints why and exits 1; an unreadable option exits 2', () => {
  const run = (...args) => spawnSync(process.execPath, [path.join(HERE, 'locks_plan.mjs'), ...args], { encoding: 'utf8' });
  const ok = run();
  assert.equal(ok.status, 0, ok.stderr);
  assert.match(ok.stdout, /LOCK 3 — Advisors · 20,000,000 RCLAW/);
  assert.match(run('--tz', 'Europe/Amsterdam').stdout, /\(29 Oct 2026, 15:00 \(UTC\+1\)\)/);

  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'locks-plan-'));
  try {
    const sixSteps = plan();
    sixSteps.locks[0].steps.push({ months: 45, percent: 1 });
    sixSteps.locks[0].steps[0].percent = 19;
    const file = path.join(dir, 'six-steps.json');
    fs.writeFileSync(file, JSON.stringify(sixSteps));
    const bad = run('--plan', file);
    assert.equal(bad.status, 1, 'a plan the tool cannot create must fail CI');
    assert.match(bad.stdout, /THE LOCKS PLAN IS NOT ONE THE TOOL CAN CREATE/);
    assert.match(bad.stdout, /6 steps; the tool allows 5/);
    assert.doesNotMatch(bad.stdout, /LOCK 1 —/, 'a refused plan prints no form entries to type from');
    const clean = path.join(dir, 'clean.json');
    fs.writeFileSync(clean, JSON.stringify(plan()));
    assert.equal(run('--plan', clean).status, 0, 'the same file with nothing wrong in it passes');
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
  assert.equal(run('--tz', 'Not/AZone').status, 2);
  assert.equal(run('--plan').status, 2, '--plan with no file is not silently the default plan');
});
