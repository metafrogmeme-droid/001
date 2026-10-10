// The locks, derived. locks.plan.json says WHAT each vested bucket releases and when, in months after T0
// (the sale's scheduled end); this turns that into instants, whole-token amounts per step, the text the
// tool's description box takes, and the funding table, and says what is wrong with a plan the tool
// cannot create. Nothing here signs or sends; nothing here is typed in by hand twice.
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

import { deriveSmithiiSale, formatUnits, scheduleInstants } from './smithii_lib.mjs';

const HERE = path.dirname(fileURLToPath(import.meta.url));
export const LOCKS_PLAN = path.join(HERE, 'locks.plan.json');

export function loadLocksPlan(file = LOCKS_PLAN) {
  return JSON.parse(fs.readFileSync(file, 'utf8'));
}

/** What the tool's Vesting url box keeps: lowercase letters, digits and hyphens, at most 30 of them. */
const SLUG = /^[a-z0-9-]{1,30}$/;

/** `ms` plus `months` calendar months in UTC, at the same clock time; a day the month lacks becomes its last day. */
export function addMonthsUtc(ms, months) {
  const d = new Date(ms);
  const y = d.getUTCFullYear();
  const m = d.getUTCMonth() + months;
  const lastDay = new Date(Date.UTC(y, m + 1, 0)).getUTCDate();
  const first = Date.UTC(y, m, 1, d.getUTCHours(), d.getUTCMinutes(), d.getUTCSeconds());
  return first + (Math.min(d.getUTCDate(), lastDay) - 1) * 86400000;
}

const isoMinute = (ms) => new Date(ms).toISOString().slice(0, 16) + ':00Z';

/** The one gap, in months, between consecutive steps; null when there is not exactly one (or only one step). */
export function commonGap(steps) {
  const gaps = new Set(steps.slice(1).map((s, i) => s.months - steps[i].months));
  return gaps.size === 1 ? [...gaps][0] : null;
}

const cliffOf = (lock) => lock.target.cliffMonths ?? 0;

/** The schedule in one sentence, e.g. "12-month cliff, then 4 unlocks of 25% every 6 months (the last at month 36)". */
export function describeLock(lock) {
  const steps = lock.steps;
  const n = steps.length;
  const gap = commonGap(steps);
  const equal = steps.every((s) => s.percent === steps[0].percent);
  const amounts = equal ? `${n} unlocks of ${steps[0].percent}%` : `${n} unlocks (${steps.map((s) => `${s.percent}%`).join(', ')})`;
  const last = steps[n - 1].months;
  let when;
  if (gap === null) when = `${n} unlock${n === 1 ? '' : 's'} at month${n === 1 ? '' : 's'} ${steps.map((s) => s.months).join(', ')}`;
  else if (cliffOf(lock)) when = `${cliffOf(lock)}-month cliff, then ${amounts} every ${gap} months`;
  else if (steps[0].months === 0) when = `${amounts}: at TGE, then every ${gap} months`;
  else when = `${amounts} every ${gap} months from month ${steps[0].months}`;
  return `${when} (the last at month ${last})`;
}

/** The same in the few words a card's row has room for: "12-mo cliff, then 4 unlocks every 6 mo". */
export function shortNote(lock) {
  const n = lock.steps.length;
  const gap = commonGap(lock.steps);
  if (gap === null) throw new Error(`${lock.bucket}: uneven spacing has no one-line note`);
  if (cliffOf(lock)) return `${cliffOf(lock)}-mo cliff, then ${n} unlocks every ${gap} mo`;
  return lock.steps[0].months === 0 ? `${n} unlocks: at TGE, then every ${gap} mo` : `${n} unlocks every ${gap} mo from month ${lock.steps[0].months}`;
}

const rowFor = (rows, bucket) => rows.rows.filter((r) => r.label.startsWith(bucket));

/** Everything wrong with the plan, as sentences; empty means the tool can create it and the roadmap's numbers hold. */
export function locksProblems(plan, { rows, cfg, record }) {
  const out = [];
  const bad = (m) => out.push(m);
  const { maxSteps, descriptionMaxLength } = plan.tool.limits;
  const slugs = new Set();
  const buckets = new Set();
  if (!Array.isArray(plan.locks) || plan.locks.length === 0) return ['the plan has no locks'];
  for (const lock of plan.locks) {
    const name = lock.bucket;
    if (buckets.has(name)) bad(`${name} is planned twice`);
    buckets.add(name);
    if (!SLUG.test(lock.slug ?? '')) bad(`${name}: the url ${JSON.stringify(lock.slug)} must be lowercase letters, digits and hyphens, at most 30 characters (the tool lowercases and strips anything else)`);
    if (slugs.has(lock.slug)) bad(`${name}: the url ${lock.slug} is used twice`);
    slugs.add(lock.slug);
    const hit = rowFor(rows, name);
    if (hit.length !== 1) {
      bad(`${name}: matches ${hit.length} rows of the roadmap's allocation table`);
      continue;
    }
    const tokens = BigInt(hit[0].tokens);
    const steps = lock.steps;
    if (!Array.isArray(steps) || steps.length === 0) {
      bad(`${name}: no steps`);
      continue;
    }
    if (steps.length > maxSteps) bad(`${name}: ${steps.length} steps; the tool allows ${maxSteps}`);
    if (!steps.every((s) => Number.isInteger(s.percent) && s.percent >= 1 && s.percent <= 100)) bad(`${name}: every percent must be a whole number from 1 to 100`);
    if (steps.reduce((a, s) => a + s.percent, 0) !== 100) bad(`${name}: the percents must sum to exactly 100`);
    const months = steps.map((s) => s.months);
    if (!months.every((m) => Number.isInteger(m) && m >= 0) || months.some((m, i) => i > 0 && m <= months[i - 1])) {
      bad(`${name}: the step months must be whole numbers, 0 or more, strictly ascending`);
      continue;
    }
    for (const s of steps) {
      // A percent that is not a whole number was refused above; BigInt() would throw on it, and a refusal is a sentence.
      if (Number.isInteger(s.percent) && (tokens * BigInt(s.percent)) % 100n !== 0n) bad(`${name}: ${s.percent}% of ${tokens} tokens is not a whole number of tokens, and the tool takes whole tokens only`);
    }
    const gap = commonGap(steps);
    if (gap === null && steps.length > 1) bad(`${name}: the steps are not evenly spaced, which the one-line descriptions assume`);
    // The published schedule is the target.
    const last = months[months.length - 1];
    const t = lock.target ?? {};
    if (t.releaseMonths !== undefined) {
      if (last !== t.releaseMonths) bad(`${name}: the last step is at month ${last}; the published release ends at month ${t.releaseMonths}`);
      if (steps[0].months !== 0) bad(`${name}: the published release has no cliff, so the first step must be at month 0`);
    } else if (Number.isInteger(t.cliffMonths) && Number.isInteger(t.linearMonths)) {
      if (last !== t.cliffMonths + t.linearMonths) bad(`${name}: the last step is at month ${last}; the published schedule ends at month ${t.cliffMonths + t.linearMonths}`);
      if (gap !== null && months[0] !== t.cliffMonths + gap) bad(`${name}: the first unlock should come one gap (${gap} months) after the ${t.cliffMonths}-month cliff, at month ${t.cliffMonths + gap}`);
      let cum = 0;
      for (const s of steps) {
        cum += s.percent;
        const line = (100 * (s.months - t.cliffMonths)) / t.linearMonths;
        if (cum > line + 1) bad(`${name}: by month ${s.months} the plan has unlocked ${cum}% where the published linear schedule has ${line.toFixed(1)}%`);
      }
    } else {
      bad(`${name}: the target must be { releaseMonths } or { cliffMonths, linearMonths }`);
    }
    // The text the tool's description box takes must fit.
    if (lockDescription(lock, { tokens, symbol: 'RCLAW', mint: '1'.repeat(44), endUtc: '2026-10-29T14:00:00Z' }).length > descriptionMaxLength) {
      bad(`${name}: the description would not fit the tool's ${descriptionMaxLength}-character box`);
    }
  }
  // With the sale and the token record in hand: the supply must cover the sale wallet, the locks, the treasury and the partners.
  if (cfg && record && out.length === 0) {
    const f = fundingPlan({ cfg, rows, record, locks: deriveLocks(plan, { cfg, rows, record }) });
    if (f.multisig.reserve < 0n) bad(`the sale wallet, the locks, the treasury and the partners need ${fmtBase(-f.multisig.reserve)} RCLAW more than the supply holds: the reserve would be negative`);
  }
  return out;
}

/** The text for the tool's description box; it is public, on the vesting certificate. */
export function lockDescription(lock, { tokens, symbol, mint, endUtc }) {
  const n = tokens.toLocaleString('en-US');
  const tge = `${endUtc.slice(0, 10)} ${endUtc.slice(11, 16)} UTC`;
  return `RUNECLAW ($${symbol}) ${lock.bucket} allocation: ${n} ${symbol}. ${describeLock(lock)}. TGE is the end of the Smithii presale, ${tge}. Mint ${mint}.`;
}

/** The locks as dates and whole-token amounts, from the plan, the sale schedule and the roadmap's table. */
export function deriveLocks(plan, { cfg, rows, record }) {
  const t0 = scheduleInstants(cfg).endMs;
  return plan.locks.map((lock) => {
    const tokens = BigInt(rowFor(rows, lock.bucket)[0].tokens);
    let cum = 0n;
    const steps = lock.steps.map((s) => {
      const atMs = addMonthsUtc(t0, s.months);
      const step = (tokens * BigInt(s.percent)) / 100n;
      cum += step;
      return { months: s.months, atMs, atUtc: isoMinute(atMs), percent: s.percent, tokens: step, cumulativeTokens: cum };
    });
    return {
      bucket: lock.bucket,
      slug: lock.slug,
      tokens,
      steps,
      lastMs: steps[steps.length - 1].atMs,
      describe: describeLock(lock),
      short: shortNote(lock),
      description: lockDescription(lock, { tokens, symbol: cfg.token.symbol, mint: record.mint, endUtc: cfg.schedule.endUtc }),
    };
  });
}

/**
 * Who holds what before the sale opens, in base units. The sale wallet keeps what Create takes (hard cap at the
 * STORED price) and what the pool needs at the hard cap; each lock's creating wallet holds exactly its amount;
 * the rest is the multisig's: the treasury, the partners, and what is left of the reserve.
 */
export function fundingPlan({ cfg, rows, record, locks }) {
  const d = deriveSmithiiSale(cfg);
  const unit = 10n ** BigInt(d.decimals);
  const supply = BigInt(record.supply_base_units);
  const sale = { escrow: d.escrowAtCreateBase, pool: d.hardCap.poolTokensBase };
  const lockTotal = locks.reduce((a, l) => a + l.tokens * unit, 0n);
  const tok = (bucket) => {
    const hit = rowFor(rows, bucket);
    if (hit.length !== 1) throw new Error(`the roadmap table has ${hit.length} rows for ${bucket}`);
    return BigInt(hit[0].tokens) * unit;
  };
  const rest = supply - sale.escrow - sale.pool - lockTotal;
  const treasury = tok('Treasury / DAO');
  const partners = tok('Partnerships & market makers');
  return { supply, sale: { ...sale, total: sale.escrow + sale.pool }, locks: locks.map((l) => ({ bucket: l.bucket, base: l.tokens * unit })), multisig: { treasury, partners, reserve: rest - treasury - partners, total: rest } };
}

export const fmtBase = (u, decimals = 9) => formatUnits(u, decimals, 3);
