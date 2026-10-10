#!/usr/bin/env node
// The locks — OFFLINE plan. Prints, for each vested bucket, what to type into Smithii's Token Vesting tool
// (method Cliffs), the dates, the amounts the tool should show back, and who must hold which tokens before
// anything is created. Nothing is sent; it exits 1 if the plan is one the tool cannot create.
//
//   npm run presale:locks-plan [-- --tz Europe/Amsterdam] [--plan <file>]
//
// --plan reads another plan file than locks.plan.json (to try a change before making it); the exit code is 1 when
// the plan is one the tool cannot create, 2 for an option that cannot be read.
//
// Everything printed is derived by locks_lib.mjs from locks.plan.json, smithii.config.json and the roadmap's table.
import { pathToFileURL } from 'node:url';

import { deriveLocks, fmtBase, fundingPlan, loadLocksPlan, locksProblems } from './locks_lib.mjs';
import { loadAllocation } from './roadmap_table.mjs';
import { zoneClock, zoneOffsetMinutes } from './smithii_plan.mjs';
import { loadSmithiiConfig, loadTokenRecord } from './smithii_lib.mjs';

const short = (a) => `${a.slice(0, 4)}…${a.slice(-4)}`;
const utcStamp = (iso) => `${iso.slice(0, 10)} ${iso.slice(11, 16)} UTC`;
const whole = (n) => n.toLocaleString('en-US');

export function renderLocksPlan(plan, { cfg, rows, record }, { tz } = {}) {
  const locks = deriveLocks(plan, { cfg, rows, record });
  const fund = fundingPlan({ cfg, rows, record, locks });
  const L = plan.tool.limits;
  const dot = (label) => `${label} ${'.'.repeat(Math.max(2, 18 - label.length))} `;
  const out = [];
  const line = (s = '') => out.push(s);
  const when = (iso) => (tz ? `${utcStamp(iso)}   (${zoneClock(Date.parse(iso), tz)})` : utcStamp(iso));

  line(`Locks — offline plan: ${plan.tool.name}, method ${plan.tool.method} (${plan.tool.url}). Nothing is sent.`);
  line(`T0 = the sale's scheduled end, ${utcStamp(cfg.schedule.endUtc)}. Month n is n calendar months after it, at the same clock time.`);
  line(`The tool: a STAIRCASE of at most ${L.maxSteps} unlocks (no linear mode); each step's percent is whole and they sum to 100; ONE vesting per wallet per token;`);
  line(`only the creating wallet can claim (receivers OFF); no cancel, no edit; ${L.feeSol} SOL each. Program ${short(plan.tool.program)}, UPGRADEABLE by ${short(plan.tool.programUpgradeAuthority)}`);
  line(`(a PDA), last deployed ${plan.tool.programLastDeployed}: do not call these locks audited.`);
  line();
  for (const [i, l] of locks.entries()) {
    line(`LOCK ${i + 1} — ${l.bucket} · ${whole(l.tokens)} ${cfg.token.symbol}`);
    line(`  ${dot('Schedule')}${l.describe}`);
    line(`  ${dot('Creating wallet')}a fresh wallet holding exactly ${whole(l.tokens)} ${cfg.token.symbol} in its own token account, plus about 0.5 SOL`);
    line(`  ${dot('Vesting Method')}Cliffs`);
    line(`  ${dot('Token')}${record.name} (${cfg.token.symbol}) ${short(cfg.token.mint)}`);
    line(`  ${dot('Amount')}${l.tokens}   (whole tokens, no separators)`);
    line(`  ${dot('Vesting url')}${l.slug}`);
    line(`  ${dot('Description')}${l.description}`);
    line(`  ${dot('Vesting Duration')}the "Fixed Dates" tab (the Years/Days tab has no months); End Date = ${when(l.steps[l.steps.length - 1].atUtc)}. Do this FIRST: confirming it resets every percent.`);
    line(`  ${dot('Step amount')}${l.steps.length}`);
    for (const [j, s] of l.steps.entries()) {
      line(`  ${dot(`Period ${j + 1}`)}End Date ${when(s.atUtc)} · ${s.percent}%  → "Tokens available to Claim on end date" ${whole(s.cumulativeTokens)}`);
    }
    line(`  ${dot('Advanced Options')}receivers OFF; Tokenomics Image (optional, an upload, 1000×1000): docs/assets/presale/rclaw_tokenomics_1000x1000.png; fee ${L.feeSol} SOL`);
    line();
  }
  line('WHO HOLDS WHAT before the sale opens (the sale wallet and the three lock wallets are four different wallets)');
  line(`  ${dot('Sale wallet')}${fmtBase(fund.sale.total)} ${cfg.token.symbol}: ${fmtBase(fund.sale.escrow)} that Create takes, plus ${fmtBase(fund.sale.pool)} for the pool at the hard cap`);
  for (const l of fund.locks) line(`  ${dot(l.bucket.split(' ')[0])}${fmtBase(l.base)} ${cfg.token.symbol}: its own wallet, for the ${l.bucket} lock`);
  line(`  ${dot('Multisig')}${fmtBase(fund.multisig.total)} ${cfg.token.symbol}: treasury ${fmtBase(fund.multisig.treasury)} + partners ${fmtBase(fund.multisig.partners)} + what is left of the reserve ${fmtBase(fund.multisig.reserve)}`);
  line(`  Nothing here locks the treasury, the partners or the reserve: the tool's locks are claimed by one ordinary wallet, never by a multisig.`);
  return out.join('\n');
}

function main() {
  const optionValue = (name) => {
    const at = process.argv.indexOf(name);
    return at > -1 ? { value: process.argv[at + 1] } : undefined;
  };
  const tzOption = optionValue('--tz');
  const planOption = optionValue('--plan');
  const tz = tzOption?.value;
  if (tzOption) {
    try {
      zoneOffsetMinutes(Date.now(), tz);
    } catch {
      console.error(`--tz needs an IANA zone name such as Europe/Amsterdam, not ${JSON.stringify(tz)}`);
      process.exit(2);
    }
  }
  if (planOption && !planOption.value) {
    console.error('--plan needs the path of a plan file');
    process.exit(2);
  }
  const cfg = loadSmithiiConfig();
  const record = loadTokenRecord();
  const rows = loadAllocation();
  const plan = planOption ? loadLocksPlan(planOption.value) : loadLocksPlan();
  const problems = locksProblems(plan, { rows, cfg, record });
  if (problems.length === 0) console.log(renderLocksPlan(plan, { cfg, rows, record }, { tz }));
  else {
    console.log('THE LOCKS PLAN IS NOT ONE THE TOOL CAN CREATE:');
    for (const p of problems) console.log(`  - ${p}`);
    process.exitCode = 1;
  }
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) main();
