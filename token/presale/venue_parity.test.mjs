// What the published sale terms are allowed to say, held against what the chosen
// venue's PROGRAM can enforce.
//
// THE HISTORY THIS REPLACES
//
// This file used to assert that the Smithii (fallback) config equalled the
// Metaplex Genesis (primary) config field for field — "so switching venues
// cannot silently change what buyers were told". It was written after the
// Smithii config had claimed a refund, a 60% pool share and a 12-month LP lock
// that Genesis did not have, and it fixed those three by forcing agreement.
//
// Forcing agreement was the wrong repair. On 2026-10-08 the operator chose to run
// the sale on Smithii, and reading CoinFabrik's audit of Smithii's program, its
// SDK and live mainnet transactions showed it has five instructions — initialize,
// edit, buy, claim, withdraw — and so cannot vest buyers, keep a wallet whitelist,
// refund, or create or lock liquidity. A config made to match Genesis was
// asserting four things the venue does not do, and this test was the thing
// holding them in place. A parity check is only as good as its idea of the thing
// being matched: it matched two files to each other, never either to a program.
//
// WHAT IT CHECKS NOW
//
//   A  the terms the two venues SHARE still agree (allocation, caps, per-wallet
//      bounds, public phase, price, the pool's share of the raise);
//   B  every term on which they DIFFER is declared, with a reason, and holds in
//      the declared direction — a new undeclared difference fails;
//   C  every field the Smithii config publishes is compared, declared or exempt
//      with a reason, so a field cannot be published unwatched;
//   D  neither config claims more than its program can enforce, and the Smithii
//      capability row is DERIVED from the program's instruction list, so adding
//      'refund' to that list is what lets a refund be claimed — not an edit here;
//   E  the GitBook (what a buyer reads) states the chosen venue's terms, each of
//      the eight disclosures, and none of the sentences it used to carry;
//   F  the roadmap records the decision and its tables agree with the GitBook's.
//
// Statements are asserted POSITIVELY and anchored to the row that makes them. The
// repository has recorded three times that asserting a short string absent
// matches prose that says the opposite; the few negatives below are the exact
// old sentences, which cannot occur in a sentence that means something else.
import { test } from 'node:test';
import assert from 'node:assert/strict';

import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

import { fixedPriceSolPerToken } from './genesis_lib.mjs';
import {
  REQUIRED_DISCLOSURES,
  deriveSmithiiSale,
  formatUnits,
  unitsToDecimal,
} from './smithii_lib.mjs';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const read = (f) => JSON.parse(fs.readFileSync(path.join(HERE, f), 'utf8'));
const DOCS = path.resolve(HERE, '..', '..', 'docs');

const genesis = read('metaplex-genesis.config.json');
const smithii = read('smithii.config.json');
const gitbook = fs.readFileSync(path.join(DOCS, 'gitbook', 'token-roadmap.md'), 'utf8');
const roadmap = fs.readFileSync(path.join(DOCS, 'TOKEN_ROADMAP.md'), 'utf8');
const sale = deriveSmithiiSale(smithii);

const HOUR_MS = 3600 * 1000;
const hoursBetween = (a, b) => (Date.parse(b) - Date.parse(a)) / HOUR_MS;
const short = (a) => `${a.slice(0, 4)}…${a.slice(-4)}`;

// ── A. the terms both venues share ──────────────────────────────────────────

const SHARED = [
  ['token.decimals', () => genesis.token.decimals, () => smithii.token.decimals, ['token.decimals']],
  ['token.symbol', () => genesis.token.symbol, () => smithii.token.symbol, ['token.symbol']],
  ['sale.presaleAllocation', () => genesis.sale.presaleAllocation, () => smithii.sale.presaleAllocation, ['sale.presaleAllocation']],
  ['sale.softCapSol', () => genesis.sale.softCapSol, () => smithii.sale.softCapSol, ['sale.softCapSol']],
  ['sale.hardCapSol', () => genesis.sale.hardCapSol, () => smithii.sale.hardCapSol, ['sale.hardCapSol']],
  ['sale.minContributionSol', () => genesis.sale.minContributionSol, () => smithii.sale.minContributionSol, ['sale.minContributionSol']],
  ['sale.maxContributionSol', () => genesis.sale.maxContributionSol, () => smithii.sale.maxContributionSol, ['sale.maxContributionSol']],
  ['sale.refundIfSoftCapMissed', () => genesis.sale.refundIfSoftCapMissed, () => smithii.sale.refundIfSoftCapMissed, ['sale.refundIfSoftCapMissed']],
  ['payment currency', () => genesis.sale.quoteSymbol, () => smithii.sale.paymentCurrency, ['sale.paymentCurrency']],
  // Genesis states an absolute timeline; Smithii's form takes durations. Derived,
  // not restated, so a moved date cannot leave the two disagreeing unnoticed.
  ['public phase (h)',
    () => hoursBetween(genesis.timeline.publicStart, genesis.timeline.depositEnd),
    () => smithii.publicPhaseHours, ['publicPhaseHours']],
  // The same window as instants: the length alone would pass a sale shifted by a day.
  ['public phase opens', () => genesis.timeline.publicStart, () => smithii.schedule.startUtc, ['schedule.startUtc']],
  ['public phase closes', () => genesis.timeline.depositEnd, () => smithii.schedule.endUtc, ['schedule.endUtc']],
  ['liquidity.dex', () => genesis.liquidity.dex, () => smithii.liquidity.dex, ['liquidity.dex']],
];

test('A: the terms both venues share agree', () => {
  const diffs = [];
  for (const [label, g, s] of SHARED) {
    const a = g();
    const b = s();
    if (a !== b) diffs.push(`${label}: genesis ${JSON.stringify(a)} != smithii ${JSON.stringify(b)}`);
  }
  assert.deepEqual(diffs, [], 'switching venues would change a shared term:\n  ' + diffs.join('\n  '));
});

test('A: the price Smithii stores is the price Genesis derives, to a lamport of rounding', () => {
  const g = fixedPriceSolPerToken(genesis);
  const s = Number(sale.price.storedSol);
  assert.ok(Math.abs(g - s) / g < 1e-4, `genesis ${g} vs smithii stored ${s}`);
  assert.notEqual(g, s, 'they are not equal: a lamport-quantised price is the declared rounding, and this guards the premise');
});

test('A: both venues refuse to promise a refund', () => {
  // Equality is not the property: two configs agreeing on `true` would pass A and
  // still publish a refund neither program can make.
  assert.equal(genesis.sale.refundIfSoftCapMissed, false);
  assert.equal(smithii.sale.refundIfSoftCapMissed, false);
});

// ── B. every difference is declared, and holds in the declared direction ────

const DECLARED_DIFFERENCES = [
  {
    keys: ['vesting.enabled', 'vesting.tgeUnlockPercent', 'vesting.linearMonthsAfterTge'],
    holds: () => genesis.vesting.tgeUnlockPercent === 33 && genesis.vesting.linearMonthsAfterTge === 2
      && smithii.vesting.enabled === false && smithii.vesting.tgeUnlockPercent === 100 && smithii.vesting.linearMonthsAfterTge === 0,
    reason: 'Genesis releases a claim schedule (33% at TGE, then linear). Smithii\'s claim() takes no arguments and runs once per buyer: 100% when the sale ends.',
  },
  {
    keys: ['whitelist.enabled'],
    holds: () => hoursBetween(genesis.timeline.whitelistStart, genesis.timeline.publicStart) > 0 && smithii.whitelist.enabled === false,
    reason: 'Genesis gates a round with a Merkle allowlist. Smithii\'s "whitelist phase" is a time window and buy() takes only an amount, so there is no wallet list to gate with.',
  },
  {
    keys: ['liquidity.createdBy', 'liquidity.enforcedByProgram', 'liquidity.launchPriceSol', 'liquidity.lpDisposition', 'liquidity.lpLock', 'liquidity.lpLockMonths'],
    holds: () => /never-claim/.test(genesis.liquidity.lpLock) && smithii.liquidity.enforcedByProgram === false
      && smithii.liquidity.lpDisposition === 'burn' && smithii.liquidity.lpLockMonths === null && !/never-claim/.test(smithii.liquidity.lpLock),
    reason: 'Genesis creates the pool and locks the LP in-program. Smithii\'s program never touches liquidity: the operator creates the pool and burns the LP, which nothing enforces.',
  },
  {
    keys: ['liquidity.intendedPercentOfGrossRaise'],
    holds: () => smithii.liquidity.intendedPercentOfGrossRaise > genesis.liquidity.raisedSolToLiquidityBps / 100,
    reason: 'The operator raised Smithii\'s pool share above the 66.67% both venues shared (80% from 2026-10-10). On Smithii the pool is the operator\'s action, sized once the raise is known. Genesis encodes its split at create and prices its LP token side against it (lp_parity.test.mjs), so the alternative nobody is running keeps the share it was priced for.',
  },
  {
    keys: ['unsold.mechanism', 'unsold.destination'],
    holds: () => genesis.sale.unsoldRollover.destination === 'reserve' && /withdraw/.test(smithii.unsold.mechanism),
    reason: 'Genesis needs an on-chain rollover behaviour or unsold tokens are stranded. Smithii returns them to the signer through one withdraw call.',
  },
];

test('B: each difference between the venues is declared, with a reason, and holds as declared', () => {
  for (const d of DECLARED_DIFFERENCES) {
    assert.ok(d.reason.length > 40, `${d.keys[0]}: a difference needs a reason a reviewer can check`);
    assert.ok(d.holds(), `${d.keys.join(', ')}: the declared difference no longer holds — ${d.reason}`);
  }
});

// ── C. nothing the Smithii config publishes goes unwatched ──────────────────

const leaves = (obj, prefix = '') => Object.entries(obj).flatMap(([k, v]) =>
  k.startsWith('_') ? []
    : (v && typeof v === 'object' && !Array.isArray(v))
      ? leaves(v, `${prefix}${k}.`)
      : [`${prefix}${k}`]);

const EXEMPT = {
  venue: 'the point is that the venues differ',
  cluster: 'Genesis is a devnet draft, Smithii a mainnet sale',
  'token.mint': 'checked against the one record, token/config/rclaw.mainnet.json, by validateSmithiiConfig; Genesis\'s is a devnet placeholder',
  'token.standard': 'a description of the mint, read off the chain by presale:smithii-verify',
  'sale.priceSol': 'Genesis derives its price from allocation / hard cap; compared numerically in A',
  'program.*': 'facts about Smithii\'s own program, with no Genesis counterpart; each is re-read by presale:smithii-verify',
  'platformFee.*': 'the venue\'s own fees; Genesis has no counterpart',
  'disclosures.*': 'compared with the GitBook in E, key by key',
};
const isExempt = (k) => Object.keys(EXEMPT).some((p) => (p.endsWith('.*') ? k.startsWith(p.slice(0, -1)) : k === p));

test('C: every field the Smithii config publishes is compared, declared or exempt', () => {
  const covered = new Set([
    ...SHARED.flatMap(([, , , keys]) => keys),
    ...DECLARED_DIFFERENCES.flatMap((d) => d.keys),
  ]);
  const published = leaves(smithii);
  const unwatched = published.filter((k) => !covered.has(k) && !isExempt(k));
  assert.deepEqual(unwatched, [],
    'published by the Smithii config and watched by nothing — compare it in SHARED, declare the difference, or exempt it with a reason:\n  ' + unwatched.join('\n  '));
  for (const [k, why] of Object.entries(EXEMPT)) assert.ok(why.length > 20, `${k}: an exemption needs a reason`);
  // Vacuity: if the walker broke, `published` empties and the assertion above passes over nothing.
  assert.ok(published.length >= 30, `only ${published.length} fields found in smithii.config.json — the shape changed and this test no longer reads it`);
});

// ── D. a config may not claim more than its program can enforce ─────────────

/** What each venue's program can ENFORCE. Smithii's row is derived below, not trusted. */
const CAN = {
  'smithii-launchpad': { buyerVesting: false, walletAllowlist: false, softCapRefund: false, createsLiquidity: false, locksLiquidity: false },
  'metaplex-genesis': { buyerVesting: true, walletAllowlist: true, softCapRefund: false, createsLiquidity: true, locksLiquidity: true },
};
// How a capability would show up in a program's instruction names.
const EVIDENCE = {
  buyerVesting: /vest/i,
  walletAllowlist: /allow|whitelist/i,
  softCapRefund: /refund/i,
  createsLiquidity: /liquid|pool/i,
  locksLiquidity: /lock/i,
};

test('D: Smithii\'s capability row is what its instruction list shows', () => {
  for (const [cap, re] of Object.entries(EVIDENCE)) {
    assert.equal(CAN['smithii-launchpad'][cap], smithii.program.instructions.some((i) => re.test(i)),
      `${cap}: the capability table and program.instructions (${smithii.program.instructions.join(', ')}) disagree`);
  }
  assert.deepEqual([...smithii.program.instructions].sort(), ['buy', 'claim', 'edit', 'initialize', 'withdraw']);
});

test('D: the Smithii config claims nothing its program cannot enforce', () => {
  const can = CAN[smithii.venue];
  assert.ok(can, `no capability row for venue ${smithii.venue}`);
  assert.ok(!smithii.vesting.enabled || can.buyerVesting, 'vesting claimed');
  assert.ok(!smithii.whitelist.enabled || can.walletAllowlist, 'a wallet whitelist claimed');
  assert.ok(!smithii.sale.refundIfSoftCapMissed || can.softCapRefund, 'a refund claimed');
  assert.ok(!smithii.liquidity.enforcedByProgram || (can.createsLiquidity && can.locksLiquidity), 'program-enforced liquidity claimed');
});

test('D: the Genesis config claims nothing its program cannot enforce', () => {
  const can = CAN[genesis.venue];
  assert.ok(can, `no capability row for venue ${genesis.venue}`);
  assert.ok(genesis.vesting.tgeUnlockPercent === 100 || can.buyerVesting, 'vesting claimed');
  assert.ok(hoursBetween(genesis.timeline.whitelistStart, genesis.timeline.publicStart) <= 0 || can.walletAllowlist, 'a whitelist round claimed');
  assert.ok(!genesis.sale.refundIfSoftCapMissed || can.softCapRefund, 'a refund claimed');
  assert.ok(!/never-claim/.test(genesis.liquidity.lpLock) || can.locksLiquidity, 'an LP lock claimed');
});

// ── E. what a buyer reads ───────────────────────────────────────────────────

/** The text of a markdown table row, found by the row's own label. */
const rowOf = (text, label) => new RegExp(`^\\|\\s*${label}\\s*\\|(.*)$`, 'm').exec(text)?.[1];

/** The sale window as the docs write it, derived from the config's two instants: "15 Oct → 29 Oct 2026". */
function saleWindowText() {
  const day = (iso, year) => new Date(iso).toLocaleDateString('en-GB', { day: 'numeric', month: 'short', ...(year ? { year: 'numeric' } : {}), timeZone: 'UTC' });
  return `${day(smithii.schedule.startUtc, false)} → ${day(smithii.schedule.endUtc, true)}`;
}

// The GitBook's token page is served by the website at /docs, and the owner took the presale off
// the website and the bot on 22 October 2026: it describes the token (locks, multisig, tiers) and
// no sale. The sale's terms, venue and disclosures are held where the sale is still described, the
// token tooling's own roadmap (docs/TOKEN_ROADMAP.md, the F tests below).
test('E: the GitBook, which the website serves, describes no sale', () => {
  const SALE = /presale|smithii\.io\/launch|per SOL|hard cap|soft cap|buyer vesting|launchpad|\bTGE\b.*sale|gross raise/i;
  const hits = gitbook.split('\n').filter((line) => SALE.test(line));
  assert.deepEqual(hits, [], 'the GitBook still describes the sale');
  assert.equal(rowOf(gitbook, '\\*\\*Smithii Launchpad\\*\\*'), undefined, 'the venue table is still on the page');
  // Both arms: the page is the token's, and the locks are still on it.
  assert.match(gitbook, /## Locks and treasury/);
  assert.ok(gitbook.includes('not called audited'), 'the locks still do not borrow another program\'s audit');
  // The disclosures the config requires are each anchored in the roadmap instead.
  for (const key of REQUIRED_DISCLOSURES) {
    assert.ok(typeof smithii.disclosures[key] === 'string' && smithii.disclosures[key].length > 100, `config disclosure ${key}`);
  }
});

test('E: the GitBook no longer carries the sentences the Smithii program makes false', () => {
  // The EXACT old sentences. They cannot occur inside a sentence that means
  // something else, which is what makes a negative safe here.
  const OLD = [
    'Whitelist round (48h) → public round (72h or until cap).',
    'LP **permanently locked** (never-claim).',
    '**66.67% of raised SOL → DEX liquidity**',
    'presale vests 33% at TGE',
    '| Metaplex Genesis** | **Best** — on-chain, trustless, audit-aligned → **chosen; integrated in draft (devnet)** |',
    'The soft cap is enforced\n  **operationally**',
  ];
  for (const s of OLD) assert.ok(!gitbook.includes(s), `the GitBook still says: ${s}`);
});

// ── F. the roadmap records the decision, and the two docs agree ─────────────

test('F: the roadmap records the venue decision and no longer recommends the other venue', () => {
  assert.ok(roadmap.includes('**Decided 2026-10-08: Smithii.**'), 'section 6 must record the decision');
  assert.ok(roadmap.includes('**Venue decision (2026-10-08): the sale runs on Smithii, not Metaplex Genesis.**'), 'the status block must record it');
  for (const s of [
    '**Decided: Metaplex Genesis, and now integrated in draft.**',
    '**Smithii Launchpad is the recommended fallback**',
    'Smithii remains the documented fallback but is no longer the expected path.',
    '| Round 1 — Whitelist / OG |',
    '| Buyer vesting | **33% at TGE**',
    'the sale is cancelled and contributions are **refundable**',
  ]) assert.ok(!roadmap.includes(s), `the roadmap still says: ${s}`);
});

test('F: the roadmap\'s presale table states what the Smithii program does', () => {
  const rows = {
    'Buyer vesting': /\*\*None\*\*/,
    'Public sale': /no whitelist round/,
    'Soft cap': /target/,
    'Price': new RegExp(`${Number(sale.price.sdkLamports).toLocaleString('en-US')} lamports per token stored`),
  };
  for (const [label, pattern] of Object.entries(rows)) {
    const cell = rowOf(roadmap, label);
    assert.ok(cell !== undefined && pattern.test(cell), `the roadmap's "${label}" row does not say ${pattern}: ${cell}`);
  }
});

test('F: the numbers the roadmap quotes are the ones the plan derives', () => {
  const sol = (u) => formatUnits(u, 9);
  for (const s of [
    `${sol(sale.softCap.poolSolLamports)} SOL`,
    `${sol(sale.hardCap.poolSolLamports)} SOL`,
    `${(Number((sale.softCap.afterPoolLamports * 10000n) / sale.softCap.grossLamports) / 100).toFixed(2)}%`,
    `${Number(sale.price.sdkLamports).toLocaleString('en-US')} SOL`,
    unitsToDecimal(sale.price.sdkLamports, 9) === '0.000033333' ? '33,333 lamports' : 'UNREACHABLE',
  ]) assert.ok(roadmap.includes(s), `the roadmap does not state ${s}`);
  const publicSale = rowOf(roadmap, 'Public sale') ?? '';
  assert.ok(publicSale.includes(saleWindowText()) && publicSale.includes(`${smithii.publicPhaseHours} hours`),
    `the roadmap's Public sale row must state ${saleWindowText()} and ${smithii.publicPhaseHours} hours: ${publicSale}`);
});

test('F: the roadmap\'s allocation table sums to 100%, and the GitBook carries none', () => {
  const rowsOf = (text) => [...text.matchAll(/^\|\s*([^|]+?)\s*\|\s*([\d.]+)%\s*\|/gm)].map((m) => [m[1], Number(m[2])]);
  const pct = (text, name) => rowsOf(text).find(([n]) => n.startsWith(name))?.[1];
  const NAMES = ['Public presale', 'DEX liquidity', 'Community', 'Team', 'Treasury', 'Partnerships', 'Advisors', 'Reserve'];
  for (const name of NAMES) assert.ok(pct(roadmap, name) !== undefined, `roadmap table has no "${name}" row`);
  const allocation = NAMES.reduce((sum, n) => sum + pct(roadmap, n), 0);
  assert.ok(Math.abs(allocation - 100) < 1e-9, `the roadmap's allocation rows sum to ${allocation}%, not 100%`);
  // The allocation names the sale's slice, so the page the website serves carries no table of it.
  assert.equal(pct(gitbook, 'Public presale'), undefined, 'the GitBook still carries the allocation table');
});
