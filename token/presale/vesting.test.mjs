// Linear vesting, pinned.
//
// §4 of docs/TOKEN_ROADMAP.md has always said team is "12-month cliff, then
// 24-month linear", advisors "6-month cliff, then 18-month linear", community
// "released over 36 months". Until 2026-07-26 every allocation bucket was built
// with `addUnlockedBucketV2`, which is a hard cliff for the FULL amount — so the
// chain would have released 100% of each of those buckets in a single instant
// while the published tokenomics described a multi-year taper. Not a rounding
// difference: a 150,000,000-token difference in what unlocks on one day.
//
// Two properties carry the fix, and both are the kind that look obviously fine
// and are not:
//
//   CONSERVATION - a stream pays cliffAmount + amountPerPeriod * periods.
//     Integer division makes that != the allocation unless something absorbs
//     the remainder, and whatever it fails to release is stranded forever.
//   THE FLAGS - a Streamflow stream can be cancelable, pausable, transferable
//     or rate-editable. Any of those turns "vesting" into a revocable IOU, and
//     none of them is visible from the sentence "12-month cliff".
import { test } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';

import {
  deriveAllocationBuckets,
  deriveStreamConfig,
  REQUIRED_STREAM_FLAGS,
  VESTING_DAYS_PER_MONTH,
  PRESALE_DIR,
} from './genesis_lib.mjs';

const committed = () =>
  JSON.parse(fs.readFileSync(path.join(PRESALE_DIR, 'metaplex-genesis.config.json'), 'utf8'));
const clone = (o) => JSON.parse(JSON.stringify(o));
const DAY = 86_400n;

function linearBuckets(cfg = committed()) {
  return deriveAllocationBuckets(cfg).buckets.filter((b) => b.kind === 'linear');
}

test('the buckets §4 calls linear are actually linear on chain', () => {
  const byName = Object.fromEntries(deriveAllocationBuckets(committed()).buckets.map((b) => [b.name, b]));
  // The three §4 describes with a taper.
  for (const name of ['community', 'team', 'advisors']) {
    assert.equal(byName[name].kind, 'linear', `${name} must be a Streamflow bucket, not a cliff`);
    assert.ok(byName[name].streamConfig, `${name} has no stream config`);
  }
  // The three §4 describes as governance-gated, which a cliff models correctly.
  for (const name of ['treasury', 'partnerships', 'reserve']) {
    assert.equal(byName[name].kind, 'cliff', `${name} should stay an unlocked bucket`);
    assert.equal(byName[name].streamConfig, null);
  }
});

test('CONSERVATION: every stream releases its allocation exactly', () => {
  // The invariant. cliffAmount + amountPerPeriod * periods must equal the
  // allocation to the base unit — anything less is stranded in the stream with
  // no instruction to recover it.
  for (const b of linearBuckets()) {
    const { cliffAmount, amountPerPeriod, periods } = b._schedule;
    assert.equal(
      cliffAmount + amountPerPeriod * periods,
      b.baseTokenAllocation,
      `${b.name} strands ${b.baseTokenAllocation - (cliffAmount + amountPerPeriod * periods)} base units`
    );
  }
});

test('CONSERVATION holds for awkward amounts, not just round ones', () => {
  // 250M over 1080 daily periods divides evenly, which would let a broken
  // remainder path pass unnoticed. These do not divide evenly.
  const cfg = clone(committed());
  for (const tokens of ['1', '7', '999999999', '123456789']) {
    const target = cfg.allocations.buckets.find((b) => b.name === 'team');
    target.tokens = tokens;
    const scale = 10n ** BigInt(cfg.token.decimals);
    const alloc = BigInt(tokens) * scale;
    const { config, _schedule } = deriveStreamConfig(cfg, target, alloc);
    assert.equal(
      config.cliffAmount + config.amountPerPeriod * _schedule.periods,
      alloc,
      `allocation ${tokens} does not conserve`
    );
  }
});

test('the remainder goes to the cliff, so the END DATE stays exact', () => {
  // Absorbing it in a ragged final period would move the completion date, which
  // is the number people actually check against the published schedule.
  const cfg = clone(committed());
  const team = cfg.allocations.buckets.find((b) => b.name === 'team');
  team.tokens = '123456789';
  const scale = 10n ** BigInt(cfg.token.decimals);
  const { config, _schedule } = deriveStreamConfig(cfg, team, BigInt(team.tokens) * scale);

  assert.ok(_schedule.remainder >= 0n && _schedule.remainder < _schedule.periods,
    'remainder must be smaller than one period-worth of rounding');
  assert.equal(config.cliffAmount, _schedule.remainder, 'cliffPercent is 0, so the cliff IS the remainder');
  assert.equal(_schedule.endTime, _schedule.cliffTime + _schedule.periods * DAY);
});

// The expected flag values, written out as LITERALS on purpose.
//
// The first version of this test compared `streamConfig[flag]` against
// `REQUIRED_STREAM_FLAGS[flag]` — and since the config is built by spreading
// that same constant, both sides moved together. Flipping cancelableBySender to
// true in genesis_lib left the suite green. A test that compares a value to
// itself asserts nothing, and only mutating the source showed it.
//
// So the truth is restated here, independently. If someone changes the constant
// they must also change this list, which is the point: it forces the decision to
// be made twice, in two places, by someone who has to type out
// `cancelableBySender: true` and look at it.
const EXPECTED_FLAGS = {
  cancelableBySender: false,
  cancelableByRecipient: false,
  transferableBySender: false,
  transferableByRecipient: false,
  canTopup: false,
  pausable: false,
  canUpdateRate: false,
  automaticWithdrawal: false,
};

test('SAFETY: no stream is cancelable, pausable, transferable or rate-editable', () => {
  // The properties that decide whether "vesting" means anything. A stream the
  // sender can cancel can be emptied by whoever holds the genesis authority.
  for (const b of linearBuckets()) {
    for (const [flag, expected] of Object.entries(EXPECTED_FLAGS)) {
      assert.equal(
        b.streamConfig[flag], expected,
        `${b.name}.${flag} is ${b.streamConfig[flag]}, must be ${expected} — this is a rug vector`
      );
    }
  }
});

test('SAFETY: the pinned set matches the constant, and neither is short', () => {
  // Two independent comparisons, because each catches what the other cannot.
  //
  // 1. the constant vs the literals above — catches editing one and not the
  //    other, which is how a flag flips without anyone noticing.
  assert.deepEqual({ ...REQUIRED_STREAM_FLAGS }, EXPECTED_FLAGS);

  // 2. the literals vs the SDK's own type — catches Streamflow ADDING a
  //    permission we then never set. That source is external to this repo, so
  //    it cannot move in sympathy with our constant the way the first version
  //    of this test did.
  const dts = fs.readFileSync(
    path.join(PRESALE_DIR, '..', 'node_modules', '@metaplex-foundation', 'genesis',
      'dist', 'src', 'generated', 'types', 'streamflowConfig.d.ts'),
    'utf8'
  );
  const block = dts.slice(dts.indexOf('export type StreamflowConfig = {'));
  const sdkFlags = [...block.slice(0, block.indexOf('};')).matchAll(/^\s*(\w+):\s*boolean;/gm)]
    .map((m) => m[1]).sort();

  assert.ok(sdkFlags.length > 0, 'could not parse boolean flags out of the SDK type');
  assert.deepEqual(
    sdkFlags, Object.keys(EXPECTED_FLAGS).sort(),
    'the SDK has a boolean stream flag this repo does not pin — decide it explicitly'
  );
});

test('the schedules match what §4 publishes', () => {
  const cfg = committed();
  const tge = BigInt(Math.floor(Date.parse(cfg.timeline.tge) / 1000));
  const months = (n) => BigInt(n) * VESTING_DAYS_PER_MONTH * DAY;
  const byName = Object.fromEntries(linearBuckets(cfg).map((b) => [b.name, b._schedule]));

  // team: 12-month cliff, then 24-month linear.
  assert.equal(byName.team.cliffTime, tge + months(12));
  assert.equal(byName.team.endTime, tge + months(12) + months(24));
  // advisors: 6-month cliff, then 18-month linear.
  assert.equal(byName.advisors.cliffTime, tge + months(6));
  assert.equal(byName.advisors.endTime, tge + months(6) + months(18));
  // community: no cliff, 36 months.
  assert.equal(byName.community.cliffTime, tge);
  assert.equal(byName.community.endTime, tge + months(36));
});

test('nothing unlocks before TGE', () => {
  const cfg = committed();
  const tge = BigInt(Math.floor(Date.parse(cfg.timeline.tge) / 1000));
  for (const b of linearBuckets(cfg)) {
    assert.ok(b.streamConfig.startTime >= tge, `${b.name} starts streaming before TGE`);
    assert.ok(b._schedule.cliffTime >= tge, `${b.name} cliffs before TGE`);
  }
});

test('a linear schedule with no duration is refused, not silently made a cliff', () => {
  const cfg = clone(committed());
  const team = cfg.allocations.buckets.find((b) => b.name === 'team');
  team.vesting.linearMonths = 0;
  assert.throws(() => deriveAllocationBuckets(cfg), /linearMonths is 0/);
  team.vesting.linearMonths = 24;
  team.vesting.cliffPercent = 101;
  assert.throws(() => deriveAllocationBuckets(cfg), /cliffPercent must be 0-100/);
});

test('a schedule too long for its allocation is refused rather than rounding to zero', () => {
  // 1 base unit over 1080 daily periods releases nothing per period, which would
  // create a stream that pays out only its rounding remainder.
  const cfg = clone(committed());
  const team = cfg.allocations.buckets.find((b) => b.name === 'team');
  assert.throws(
    () => deriveStreamConfig(cfg, team, 1n),
    /rounds to zero per period/
  );
});

test('the supply invariant still holds with streams in the mix', () => {
  // Streams change how tokens leave a bucket, never how many it holds.
  // finalizeV2 rejects anything but an exact sum.
  const r = deriveAllocationBuckets(committed());
  assert.equal(r.allocated, r.totalSupply);
});
