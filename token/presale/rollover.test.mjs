// Unsold presale tokens, pinned.
//
// A partial raise leaves `presaleAllocation - sold` sitting in the presale
// bucket, and nothing can move it out afterwards: `withdrawUnsoldPresaleV1` and
// `withdrawPresaleV1` are V1-only and reject a V2 genesis account (0x2f), and
// the SDK ships no V2 equivalent FOR A PRESALE BUCKET. (LaunchPool buckets are a
// different story — see the CORRECTION tests at the bottom of this file.) Without a rollover behavior attached AT
// CREATION — end behaviors cannot be added after finalize — that supply is
// stranded permanently. At a soft-cap raise that is ~120,000,000 tokens, 12% of
// supply, frozen in an account no key can reach.
//
// The destination choice is the substantive decision and is asserted here
// rather than left to whoever edits the config:
//
//   NOT treasury/team/advisors - hands unsold supply to insiders and raises
//     their share above the published §4 table.
//   NOT the liquidity bucket   - adds tokens to the pool's base side against an
//     unchanged SOL side, pushing the opening price DOWN. That is the exact
//     underwater outcome the LP sizing decision was taken to avoid.
//   NOT a Streamflow bucket    - amountPerPeriod was derived from the original
//     allocation, so rolled-in tokens would never be released. Stranded again,
//     one level down.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';

import {
  deriveUnsoldRollover,
  deriveAllocationBuckets,
  ALLOCATION_BUCKET_START,
  LIQUIDITY_BUCKET_INDEX,
  PRESALE_DIR,
} from './genesis_lib.mjs';

const committed = () =>
  JSON.parse(fs.readFileSync(path.join(PRESALE_DIR, 'metaplex-genesis.config.json'), 'utf8'));
const clone = (o) => JSON.parse(JSON.stringify(o));

test('DECISION: unsold presale tokens are rolled over, not stranded', () => {
  const r = deriveUnsoldRollover(committed());
  assert.ok(r, 'no rollover configured — unsold supply would be stranded permanently');
  assert.equal(r.percentageBps, 10_000, 'anything less than 100% strands the remainder');
});

test('DECISION: the destination is a locked, governance-gated bucket', () => {
  const cfg = committed();
  const r = deriveUnsoldRollover(cfg);
  const dest = deriveAllocationBuckets(cfg).buckets.find((b) => b.name === r.name);

  assert.equal(dest.kind, 'cliff', 'a stream destination would never release the rolled-in tokens');
  // Not an insider bucket. If this list ever needs editing, the tokenomics
  // changed and §4 needs editing with it.
  assert.ok(
    !['team', 'advisors', 'partnerships'].includes(r.name),
    `unsold supply routed to "${r.name}" — that hands it to insiders above the §4 table`
  );
  // Not the pool. Adding base tokens against an unchanged SOL side lowers the
  // opening price, which is the failure the LP sizing decision exists to avoid.
  assert.notEqual(r.bucketIndex, LIQUIDITY_BUCKET_INDEX);
});

test('the destination index matches the shared bucket layout', () => {
  // create, allocate and trigger all derive this PDA independently. If the
  // index they compute ever differs, the behavior points at an account that is
  // not the one allocate created, and the tokens go nowhere recoverable.
  const cfg = committed();
  const r = deriveUnsoldRollover(cfg);
  const idx = cfg.allocations.buckets.findIndex((b) => b.name === r.name);
  assert.equal(r.bucketIndex, ALLOCATION_BUCKET_START + idx);
});

test('a missing rollover is null, not a silent default', () => {
  // presale:create warns loudly on null. What must NOT happen is quietly
  // picking a destination on the operator's behalf.
  const cfg = clone(committed());
  delete cfg.sale.unsoldRollover;
  assert.equal(deriveUnsoldRollover(cfg), null);
});

test('a Streamflow destination is refused, with the reason', () => {
  const cfg = clone(committed());
  cfg.sale.unsoldRollover.destination = 'team'; // a linear/Streamflow bucket
  assert.throws(() => deriveUnsoldRollover(cfg), /Streamflow/);
  assert.throws(() => deriveUnsoldRollover(cfg), /never be released/);
});

test('an unknown destination is refused, naming the valid ones', () => {
  const cfg = clone(committed());
  cfg.sale.unsoldRollover.destination = 'nope';
  assert.throws(() => deriveUnsoldRollover(cfg), /not an allocation bucket/);
  assert.throws(() => deriveUnsoldRollover(cfg), /reserve/);
});

test('an out-of-range percentage is refused rather than clamped', () => {
  const cfg = clone(committed());
  for (const bps of [0, -1, 10_001, 1.5]) {
    cfg.sale.unsoldRollover.percentageBps = bps;
    assert.throws(() => deriveUnsoldRollover(cfg), /must be 1-10000/, `bps ${bps} was accepted`);
  }
});

test('the sale still explains that unsold tokens have no other exit', () => {
  // The note is what tells a future reader why a rollover is mandatory rather
  // than a nicety. If it goes, the reasoning goes with it.
  const cfg = committed();
  assert.match(cfg.sale._unsoldRolloverNote, /withdrawUnsoldPresaleV1/);
  assert.match(cfg.sale._unsoldRolloverNote, /stranded permanently/);
});

test('CORRECTION: the no-refund disclosure is scoped to the bucket type', () => {
  // This disclosure previously said no refund exists "by any instruction in the
  // Genesis program". That was false — refundLaunchPoolV2 and
  // withdrawLaunchPoolV2 exist, and LaunchPool buckets carry an on-chain
  // SoftCap. The claim is true only of the fixed-price PRESALE bucket this
  // tooling builds, and a disclosure published to buyers must not overstate.
  const cfg = committed();
  const d = cfg.disclosures.noRefund;
  assert.match(d, /THIS SALE STRUCTURE|presale bucket/,
    'the no-refund claim must be scoped, not absolute');
  assert.ok(
    !/no instruction in the Genesis program/.test(d),
    'the over-broad claim is back — the program does have refundLaunchPoolV2'
  );
  // And the alternative has to stay recorded, with its trade-off.
  assert.match(cfg.sale._launchPoolAlternative, /refundLaunchPoolV2/);
  assert.match(cfg.sale._launchPoolAlternative, /NOT PROVEN EITHER WAY/,
    'the gating is inferred from error names and has not been executed — say so');
  // The decision, and the bar for revisiting it. A rejected alternative with no
  // recorded reopening condition is indistinguishable from one nobody thought
  // about, and this one was thought about.
  assert.match(cfg.sale._launchPoolAlternative, /CONSIDERED AND REJECTED/);
  assert.match(cfg.sale._launchPoolAlternative, /WHAT WOULD REOPEN THIS/);
});

test('CORRECTION: the SDK really does ship the launch-pool refund path', () => {
  // The evidence for the correction, checked rather than remembered. If a future
  // SDK bump removes these, the correction above needs revisiting.
  const dir = path.join(PRESALE_DIR, '..', 'node_modules', '@metaplex-foundation',
    'genesis', 'dist', 'src', 'generated');
  for (const f of ['instructions/refundLaunchPoolV2.d.ts',
                   'instructions/withdrawLaunchPoolV2.d.ts',
                   'types/softCap.d.ts']) {
    assert.ok(fs.existsSync(path.join(dir, f)), `${f} is gone — recheck the refund correction`);
  }
  const errs = fs.readFileSync(path.join(dir, 'errors', 'genesis.js'), 'utf8');
  assert.match(errs, /LaunchPoolFundingThresholdNotMet/);
});
