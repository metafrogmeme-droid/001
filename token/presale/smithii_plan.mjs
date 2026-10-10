#!/usr/bin/env node
// Smithii presale — OFFLINE plan. Prints what to type into each field of the
// form, what the form should show back, what the sale pays, the pool the
// operator will have to create, and every disclosure. No network, no keys,
// nothing is sent; it exits 1 if the config is not sane, so CI fails a config
// that cannot produce a coherent sale (the same job presale:plan does for
// Genesis).
//
//   npm run presale:smithii-plan
//
// Everything printed is derived by smithii_lib.mjs from smithii.config.json —
// none of it is typed here.
import { pathToFileURL } from 'node:url';

import {
  decimalToUnits,
  deriveSmithiiSale,
  formatUnits,
  loadSmithiiConfig,
  loadTokenRecord,
  scheduleInstants,
  validateSmithiiConfig,
} from './smithii_lib.mjs';

const short = (a) => `${a.slice(0, 4)}…${a.slice(-4)}`;
const utcStamp = (ms) => `${new Date(ms).toISOString().slice(0, 16).replace('T', ' ')} UTC`;

/** Minutes the zone `tz` is ahead of UTC at the instant `ms` (a RangeError for a zone that does not exist). */
export function zoneOffsetMinutes(ms, tz) {
  const parts = new Intl.DateTimeFormat('en-US', {
    timeZone: tz, hourCycle: 'h23', year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit',
  }).formatToParts(ms);
  const v = Object.fromEntries(parts.filter((p) => p.type !== 'literal').map((p) => [p.type, Number(p.value)]));
  return Math.round((Date.UTC(v.year, v.month - 1, v.day, v.hour, v.minute, v.second) - Math.floor(ms / 1000) * 1000) / 60000);
}

/** "15 Oct 2026, 16:00 (UTC+2)" — the clock reading of an instant in `tz`. */
export function zoneClock(ms, tz) {
  const off = zoneOffsetMinutes(ms, tz);
  const f = new Intl.DateTimeFormat('en-GB', { timeZone: tz, day: 'numeric', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit', hourCycle: 'h23' });
  const sign = off < 0 ? '-' : '+';
  const abs = Math.abs(off);
  return `${f.format(ms).replace(' at ', ', ')} (UTC${sign}${Math.floor(abs / 60)}${abs % 60 ? `:${String(abs % 60).padStart(2, '0')}` : ''})`;
}

export function renderPlan(cfg, record, { tz } = {}) {
  const d = deriveSmithiiSale(cfg);
  const sol = (u) => `${formatUnits(u, 9)} SOL`;
  const rclaw = (u, frac = 3) => `${formatUnits(u, d.decimals, frac)} ${cfg.token.symbol}`;
  const dot = (label) => `${label} ${'.'.repeat(Math.max(2, 20 - label.length))} `;
  const out = [];
  const line = (s = '') => out.push(s);

  line(`Smithii presale — offline plan (venue of record). Nothing is sent.`);
  line(`Cluster ${cfg.cluster} · program ${short(cfg.program.id)} · UPGRADEABLE by ${short(cfg.program.upgradeAuthority)} (a PDA; read ${cfg.program.measuredAt})`);
  line();
  line('FORM, step 1 — type exactly this');
  line(`  ${dot('Sale token')}${record.name} (${cfg.token.symbol}) ${short(cfg.token.mint)}   [the one record: token/config/rclaw.mainnet.json]`);
  line(`  ${dot('Payment currency')}${cfg.sale.paymentCurrency}`);
  line(`  ${dot('Sale price')}${cfg.sale.priceSol}`);
  line(`  ${dot('LP launch price')}${cfg.liquidity.launchPriceSol}   (not stored on-chain: a promise on the sale page)`);
  line(`  ${dot('Minimum buy')}${cfg.sale.minContributionSol} SOL`);
  line(`  ${dot('Maximum buy')}${cfg.sale.maxContributionSol} SOL`);
  line(`  ${dot('Softcap')}${cfg.sale.softCapSol} SOL   (descriptive: the program never reads it)`);
  line(`  ${dot('Hardcap')}${cfg.sale.hardCapSol} SOL`);
  line('  Type digits only — no thousands separators.');
  line();
  const w = scheduleInstants(cfg);
  line("FORM — the sale window (type INSTANTS: the form's date picker uses your browser's clock, not UTC)");
  line(`  ${dot('Public phase starts')}${utcStamp(w.startMs)}`);
  line(`  ${dot('Public phase ends')}${utcStamp(w.endMs)}   (${w.hours} hours = ${w.hours / 24} days)`);
  line(`  ${dot('Whitelist phase')}off: leave its price and dates empty (a whitelist phase would also raise the creation fee to 0.2 SOL)`);
  if (tz) {
    const oS = zoneOffsetMinutes(w.startMs, tz);
    const oE = zoneOffsetMinutes(w.endMs, tz);
    line(`  In ${tz} the form needs: starts ${zoneClock(w.startMs, tz)} · ends ${zoneClock(w.endMs, tz)}`);
    if (oS !== oE) {
      const gap = (oS - oE) / 60;
      line(`  The clock offset changes by ${Math.abs(gap)} h between the two dates in ${tz}: typing the start's clock time on the end date as well would end the sale ${Math.abs(gap)} h ${gap > 0 ? 'late' : 'early'}.`);
    }
  } else {
    line('  Not on UTC? Re-run with  --tz <your IANA zone>  (for example Europe/Amsterdam) to see the clock times to type.');
  }
  line('  After Create, presale:smithii-verify compares the on-chain start and end with these two instants, to the minute.');
  line();
  line('THE FORM SHOULD SHOW BACK (as read off the live form, 2026-10-09: fixtures/smithii_form_reading.json)');
  line(`  ${dot('Sending')}${rclaw(d.formSendingBase, 0)}   (hard cap / the price AS TYPED; the form groups digits with "." and may print ${formatUnits(d.formSendingBase / 10n ** BigInt(d.decimals), 0).replaceAll(',', '.')})`);
  const formRate = 1 / Number(d.price.typed);
  line(`  ${dot('Sale rate')}${Math.floor(formRate).toLocaleString('en-US')} per SOL on the form (1 / the typed price is ${formRate.toLocaleString('en-US', { maximumFractionDigits: 3 })}; the form prints the whole number, "${Math.floor(formRate).toLocaleString('en-US').replaceAll(',', '.')}")`);
  line(`  ${dot('Total fees')}${d.creationFeeSol} SOL${cfg.whitelist.enabled ? '' : ' (no whitelist phase; a whitelist phase would make it 0.2)'}`);
  line();
  line('ON CHAIN once Create is signed — expected, and not yet measured on this sale');
  line(`  ${dot('Stored price')}${d.price.sdkLamports} lamports per token = ${d.price.storedSol} SOL = ${d.price.tokensPerSol.toLocaleString('en-US', { maximumFractionDigits: 2 })} ${cfg.token.symbol} per SOL — publish THIS rate`);
  const more = d.escrowAtCreateBase - d.formSendingBase;
  line(`  ${dot('Vault')}${rclaw(d.escrowAtCreateBase)}   (hard cap / the STORED price: ${formatUnits(more, d.decimals, 0)} more than the form's Sending line. Your wallet's preview before you sign shows what will actually move, and presale:smithii-verify reads the vault afterwards; NO pool tokens are included)`);
  if (d.price.subLamportDigits) {
    line(`  Note: the typed price has digits below one lamport; the program drops them (${d.price.typed} -> ${d.price.storedSol}).`);
  }
  line();
  line(`WHAT THE SALE PAYS — Smithii's ${cfg.platformFee.percentOfEachPurchase}% comes out of YOUR side; buyers are credited the gross amount`);
  for (const [label, at] of [['soft cap', d.softCap], ['hard cap', d.hardCap]]) {
    line(`  at the ${label} ${sol(at.grossLamports).padEnd(11)}: Smithii ${sol(at.smithiiFeeLamports)} · you receive ${sol(at.creatorReceivesLamports)} · buyers can claim ${rclaw(at.buyersClaimBase, 0)}`);
  }
  const wallets = (cap) => (decimalToUnits(cap, 9).units + d.perWalletLamports.max - 1n) / d.perWalletLamports.max;
  line(`  wallets at the ${cfg.sale.maxContributionSol} SOL maximum to reach the soft cap: ${wallets(cfg.sale.softCapSol)}; the hard cap: ${wallets(cfg.sale.hardCapSol)}`);
  line();
  line(`POOL — YOUR action after the sale (${cfg.liquidity.intendedPercentOfGrossRaise}% of the gross raise, opened at the sale price); the program does none of this`);
  for (const [label, at] of [['soft cap', d.softCap], ['hard cap', d.hardCap]]) {
    line(`  ${label}: ${sol(at.poolSolLamports)} + ${rclaw(at.poolTokensBase, 0)} · left after the pool: ${sol(at.afterPoolLamports)}`);
  }
  line(`  Then burn the LP tokens (${cfg.liquidity.lpDisposition}) and publish the pool address and the burn transaction.`);
  line();
  line(`WHAT THE PROGRAM CANNOT DO — refund · vest buyers · keep a wallet whitelist · create or lock liquidity`);
  line(`  Its instructions: ${cfg.program.instructions.join(', ')}.`);
  line();
  line('DISCLOSURES (each must be in the published terms)');
  for (const [k, v] of Object.entries(cfg.disclosures)) {
    if (k.startsWith('_')) continue;
    line(`  [${k}]`);
    line(`    ${v}`);
  }
  return out.join('\n');
}

function main() {
  const cfg = loadSmithiiConfig();
  const record = loadTokenRecord();
  const problems = validateSmithiiConfig(cfg, { record });
  const at = process.argv.indexOf('--tz');
  const tz = at > -1 ? process.argv[at + 1] : undefined;
  if (at > -1) {
    try {
      zoneOffsetMinutes(Date.now(), tz);
    } catch {
      console.error(`--tz needs an IANA zone name such as Europe/Amsterdam, not ${JSON.stringify(tz)}`);
      process.exit(2);
    }
  }
  console.log(renderPlan(cfg, record, { tz }));
  if (problems.length) {
    console.log('\nTHE CONFIG IS NOT SANE:');
    for (const p of problems) console.log(`  - ${p}`);
    process.exitCode = 1;
  }
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) main();
