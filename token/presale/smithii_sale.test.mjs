// The Smithii sale's derived numbers, its Launch-account decoder and the
// comparators the verify command prints — held against independent readings.
//
// WHAT "INDEPENDENT" MEANS HERE
//
// The expected numbers below were computed in Python with arbitrary-precision
// integers, not by asking smithii_lib.mjs, so the library cannot agree with
// itself. The decoder is tested against fixtures/smithii_launch_live.json: a
// REAL Launch account read from mainnet, whose authority and mint equal the
// accounts of a real `buy` transaction against it, whose first 8 bytes equal
// sha256('account:Launch')[0..8] (recomputed here, not imported), and whose
// vault held exactly hard cap / price tokens. A decoder tested only on bytes
// this repo wrote would agree with whatever offsets it was given.
//
// Every comparator row is mutation-tested: change the one field it reads and
// that row — and only that row — must fail, so a row that passes by default
// (the failure this repository names most often) is caught.
import { test } from 'node:test';
import assert from 'node:assert/strict';

import crypto from 'node:crypto';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

import {
  REQUIRED_DISCLOSURES,
  LAUNCH_DISCRIMINATOR_HEX,
  base58Encode,
  compareLaunch,
  compareMint,
  compareProgram,
  compareVault,
  decimalToUnits,
  decodeLaunch,
  deriveSmithiiSale,
  exitCodeFor,
  formatUnits,
  isBase58Address,
  loadSmithiiConfig,
  loadTokenRecord,
  priceReading,
  tokensForLamports,
  unitsToDecimal,
  validateSmithiiConfig,
} from './smithii_lib.mjs';
import { renderPlan } from './smithii_plan.mjs';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const fixture = JSON.parse(fs.readFileSync(path.join(HERE, 'fixtures', 'smithii_launch_live.json'), 'utf8'));
const cfg = () => structuredClone(loadSmithiiConfig());
const record = loadTokenRecord();
const SOL = 1_000_000_000n;

// ── exact decimals ──────────────────────────────────────────────────────────

test('decimals are exact, truncated and never float', () => {
  assert.equal(decimalToUnits('0.1', 9).units, 100000000n);
  assert.equal(decimalToUnits(0.25, 9).units, 250000000n);
  assert.equal(decimalToUnits(1000, 9).units, 1000n * SOL);
  assert.deepEqual(decimalToUnits('0.00003333333', 9), { units: 33333n, truncated: true });
  assert.deepEqual(decimalToUnits('0.000033333', 9), { units: 33333n, truncated: false });
  assert.equal(unitsToDecimal(33333n, 9), '0.000033333');
  assert.equal(unitsToDecimal(5000n * SOL, 9), '5000');
  assert.equal(formatUnits(150001500015000150n, 9, 3), '150,001,500.015');
  // 66.67% as basis points, the way the library reads a percentage.
  assert.equal(decimalToUnits(66.67, 2).units, 6667n);
  assert.equal(decimalToUnits(2.5, 2).units, 250n);
});

test('a value that is not a plain decimal is refused, not guessed at', () => {
  for (const bad of ['1e-7', '-1', 'abc', '', '1,5', '0x10', ' ']) {
    assert.throws(() => decimalToUnits(bad, 9), /not a plain decimal/, JSON.stringify(bad));
  }
  // A JSON number that stringifies with an exponent is the same trap.
  assert.throws(() => decimalToUnits(1e-7, 9), /not a plain decimal/);
});

// ── the price as the program holds it ───────────────────────────────────────

test('the typed price is stored as 33,333 lamports: 30,000.30 RCLAW per SOL', () => {
  const p = priceReading(cfg().sale.priceSol);
  assert.equal(p.typed, '0.00003333333');
  assert.equal(p.sdkLamports, 33333n);
  assert.equal(p.exactLamports, 33333n);
  assert.equal(p.floatTrap, false);
  assert.equal(p.subLamportDigits, true, 'the typed price has digits below one lamport; the program drops them');
  assert.equal(p.storedSol, '0.000033333');
  assert.ok(Math.abs(p.tokensPerSol - 30000.300003) < 1e-5, `rate ${p.tokensPerSol}`);
});

test('float arithmetic can store a price one lamport LOW, and the reading says so', () => {
  // Premise first: if a JS engine ever rounds this the other way the vector is
  // no longer a trap and this test must say so rather than pass vacuously.
  assert.equal(Math.floor(0.000000015 * 1e9), 14, 'the premise: 0.000000015 * 1e9 is 14.999… in IEEE-754');
  const p = priceReading('0.000000015');
  assert.equal(p.exactLamports, 15n);
  assert.equal(p.sdkLamports, 14n);
  assert.equal(p.floatTrap, true);
  // …and an ordinary price is not flagged.
  assert.equal(priceReading('0.00004').floatTrap, false);
});

test('a price below one lamport is refused, not divided by', () => {
  assert.throws(() => tokensForLamports(1000n * SOL, 0n, 9), /rounds to 0 lamports/);
  const bad = cfg();
  bad.sale.priceSol = '0.0000000001';
  bad.liquidity.launchPriceSol = '0.0000000001';
  assert.ok(validateSmithiiConfig(bad, { record }).some((m) => /rounds to 0 lamports/.test(m)));
});

// ── the escrow formula, against a real sale ─────────────────────────────────

test('Create escrows hard cap / price tokens: proven on a real third-party sale', () => {
  const e = fixture.expected;
  const want = BigInt(fixture.vault.rawAmount);
  assert.equal(
    tokensForLamports(BigInt(e.hardcap), BigInt(e.publicPhase.price), fixture.vault.decimals),
    want,
    'the vault of a live 140 SOL / 400-lamport sale held exactly this many raw units');
  assert.equal(want, 350_000_000_000_000n, '350,000,000 tokens at 6 decimals');
});

// ── the sale, derived (numbers computed outside this library) ───────────────

test('what the form shows and what the sale pays, at the soft cap and the hard cap', () => {
  const d = deriveSmithiiSale(cfg());
  assert.equal(d.creationFeeSol, 0.1, 'no whitelist phase, so the 0.1 SOL creation fee');
  assert.equal(d.escrowAtCreateBase, 150001500015000150n, 'what the program takes: hard cap / the STORED price, not the form\'s "Sending" line');
  assert.equal(formatUnits(d.escrowAtCreateBase, d.decimals), '150,001,500.015');

  assert.equal(d.hardCap.smithiiFeeLamports, 125n * SOL);
  assert.equal(d.hardCap.creatorReceivesLamports, 4875n * SOL);
  assert.equal(d.hardCap.poolSolLamports, 3333500000000n);
  assert.equal(d.hardCap.poolTokensBase, 100006000060000600n);
  assert.equal(d.hardCap.afterPoolLamports, 1541500000000n);
  assert.equal(d.hardCap.buyersClaimBase, 150001500015000150n);

  assert.equal(d.softCap.smithiiFeeLamports, 25n * SOL);
  assert.equal(d.softCap.creatorReceivesLamports, 975n * SOL);
  assert.equal(d.softCap.poolSolLamports, 666700000000n);
  assert.equal(d.softCap.poolTokensBase, 20001200012000120n);
  assert.equal(d.softCap.afterPoolLamports, 308300000000n);
  assert.equal(d.softCap.buyersClaimBase, 30000300003000030n);
});

test('the pool at the sale price needs 20.0M RCLAW at the soft cap and 100.0M at the hard cap', () => {
  // The two figures the GitBook and the roadmap quote for pool sizing; they
  // come from the same arithmetic, so the prose cannot drift from it.
  const d = deriveSmithiiSale(cfg());
  assert.equal(d.softCap.poolTokensBase / 10n ** 9n, 20001200n);
  assert.equal(d.hardCap.poolTokensBase / 10n ** 9n, 100006000n);
});

test('the escrow is within a rounding step of the 150,000,000 allocation, and not equal to it', () => {
  const d = deriveSmithiiSale(cfg());
  const extra = d.escrowAtCreateBase - d.allocationBase;
  assert.ok(extra > 0n, 'a floored price sells slightly MORE than the round allocation');
  assert.equal(extra / 10n ** 9n, 1500n, '1,500 tokens: 0.001% of the allocation');
});

// ── the form's "Sending" line is not the vault figure ────────────────────────
//
// This file's expectations used to be computed from the SDK and the chain alone, and called the
// form's "Sending" line the vault figure. A photograph of the live form (2026-10-09) showed
// 150.000.015 instead: the hard cap divided by the price as TYPED. The oracle below is that
// reading, held as data; the Python fractions in the comments are what it is checked against.

const formReading = JSON.parse(fs.readFileSync(path.join(HERE, 'fixtures', 'smithii_form_reading.json'), 'utf8'));

test('the form\'s "Sending" line is hard cap / the price AS TYPED: read off the live form on 2026-10-09', () => {
  const c = cfg();
  assert.equal(c.sale.priceSol, formReading.inputs.priceSol, 'the reading was taken with the price this config types');
  assert.equal(String(c.sale.hardCapSol), formReading.inputs.hardCapSol);
  const d = deriveSmithiiSale(c);
  // The form groups digits with ".": "150.000.015 RCLAW" is 150,000,015 and "30.000 RCLAW/SOL" is 30,000.
  const shownSending = BigInt(formReading.shown.sending.replace(/ RCLAW$/, '').replaceAll('.', ''));
  assert.equal(d.formSendingBase / 10n ** 9n, shownSending, 'the whole tokens the form printed');
  // floor(Fraction(5000e9 * 1e9) / (Fraction("0.00003333333") * 1e9)) in Python:
  assert.equal(d.formSendingBase, 150000015000001500n, 'hard cap / the typed price, to the base unit');
  assert.equal(String(Math.floor(1 / Number(c.sale.priceSol))), formReading.shown.saleRate.replace(/ RCLAW\/SOL$/, '').replaceAll('.', ''),
    'Sale Rate is 1 / the typed price');
  assert.equal(d.creationFeeSol, Number(formReading.shown.totalFees.replace(/ SOL$/, '')));
  // Decimals scale the base units and nothing else: the same 150,000,015.0000015 tokens at 6
  // decimals is floor(150000015.0000015 * 10^6) = 150000015000001 raw units (Python fractions).
  const six = cfg();
  six.token.decimals = 6;
  assert.equal(deriveSmithiiSale(six).formSendingBase, 150000015000001n, 'the form figure follows the token\'s decimals');
});

test('the program is expected to take 1,485 RCLAW more than the form prints, and the plan says both', () => {
  const d = deriveSmithiiSale(cfg());
  assert.notEqual(d.escrowAtCreateBase, d.formSendingBase, 'one is floored to a lamport and the other is not');
  assert.equal((d.escrowAtCreateBase - d.formSendingBase) / 10n ** 9n, 1485n, '1,485.01...: 0.00099% of the allocation');
  const plan = renderPlan(cfg(), record);
  assert.match(plan, /Sending \.+ 150,000,015 RCLAW/, 'the form figure sits on the Sending line');
  assert.match(plan, /Vault \.+ 150,001,500\.015 RCLAW/, 'the program figure sits on the Vault line');
  assert.match(plan, /1,485 more than the form's Sending line/);
  assert.doesNotMatch(plan, /Sending \.+ 150,001,500/, 'the vault figure must not be presented as what the form shows');
  assert.match(plan, /150\.000\.015/, 'the dotted form the operator will actually see');
});

// ── the config, validated ───────────────────────────────────────────────────

test('the real config is sane against the one token record', () => {
  assert.deepEqual(validateSmithiiConfig(cfg(), { record }), []);
  assert.equal(cfg().token.mint, record.mint, 'no second mint');
  for (const a of [cfg().program.id, cfg().program.upgradeAuthority, cfg().platformFee.receiver, cfg().token.mint]) {
    assert.ok(isBase58Address(a), a);
  }
});

test('the program id in the config is the owner of a real launch account', () => {
  assert.equal(cfg().program.id, fixture.program,
    'the id this config names must be the program that actually owns Launch accounts on mainnet');
});

const MUTATIONS = [
  ['soft cap above hard cap', (c) => { c.sale.softCapSol = 6000; }, /exceeds hard cap/],
  ['minimum above maximum', (c) => { c.sale.minContributionSol = 30; }, /exceeds maximum/],
  ['maximum above the hard cap', (c) => { c.sale.maxContributionSol = 6000; }, /exceeds the hard cap/],
  ['LP price below the sale price', (c) => { c.liquidity.launchPriceSol = '0.00003333'; }, /below the stored sale price/],
  ['a float-trap price', (c) => { c.sale.priceSol = '0.000000015'; c.liquidity.launchPriceSol = '0.000000015'; }, /float arithmetic stores/],
  ['a price far from the allocation', (c) => { c.sale.priceSol = '0.00004'; c.liquidity.launchPriceSol = '0.00004'; }, /more than 0\.01% away/],
  ['a different mint', (c) => { c.token.mint = 'So11111111111111111111111111111111111111112'; }, /is not the recorded mint/],
  ['a refund promised', (c) => { c.sale.refundIfSoftCapMissed = true; }, /refundIfSoftCapMissed must be false/],
  ['vesting claimed', (c) => { c.vesting.enabled = true; c.vesting.tgeUnlockPercent = 33; }, /cannot vest/],
  ['a whitelist enabled', (c) => { c.whitelist.enabled = true; }, /whitelist\.enabled must be false/],
  ['liquidity claimed as enforced', (c) => { c.liquidity.enforcedByProgram = true; }, /enforcedByProgram must be false/],
  ['a disclosure removed', (c) => { delete c.disclosures.noRefund; }, /disclosures\.noRefund is missing/],
  ['a non-address program id', (c) => { c.program.id = 'not-an-address'; }, /program\.id is not a base58 address/],
  ['a zero public phase', (c) => { c.publicPhaseHours = 0; }, /publicPhaseHours must be a positive integer/],
];

for (const [name, mutate, expected] of MUTATIONS) {
  test(`validation refuses: ${name}`, () => {
    assert.deepEqual(validateSmithiiConfig(cfg(), { record }), [], 'the clean config must pass, or every case below passes for the wrong reason');
    const c = cfg();
    mutate(c);
    const problems = validateSmithiiConfig(c, { record });
    assert.ok(problems.some((m) => expected.test(m)), `expected ${expected}; got ${JSON.stringify(problems)}`);
  });
}

test('every required disclosure is present and says something', () => {
  for (const k of REQUIRED_DISCLOSURES) {
    assert.ok(typeof cfg().disclosures[k] === 'string' && cfg().disclosures[k].length > 100, k);
  }
  assert.ok(REQUIRED_DISCLOSURES.length >= 8, 'the list this test iterates has not been emptied');
});

// ── the Launch account, decoded from real bytes ─────────────────────────────

test('the account tag is sha256("account:Launch")[0..8], recomputed here', () => {
  const tag = crypto.createHash('sha256').update('account:Launch').digest('hex').slice(0, 16);
  assert.equal(tag, LAUNCH_DISCRIMINATOR_HEX);
  assert.equal(tag, fixture.expected.discriminatorHex);
});

test('a real mainnet Launch account decodes to the values read independently of the decoder', () => {
  const raw = Buffer.from(fixture.dataBase64, 'base64');
  assert.equal(raw.length, 192);
  const l = decodeLaunch(raw);
  const e = fixture.expected;
  assert.equal(l.discriminatorHex, e.discriminatorHex);
  assert.equal(l.authority, e.authority);
  assert.equal(l.mint, e.mint);
  assert.equal(l.hardcap, BigInt(e.hardcap));
  assert.equal(l.softcap, BigInt(e.softcap));
  assert.equal(l.soldAmount, BigInt(e.soldAmount));
  assert.equal(l.whitelistLimit, BigInt(e.whitelistLimit));
  assert.equal(l.paymentMethod, e.paymentMethod);
  for (const key of ['price', 'startDate', 'endDate', 'minAmount', 'maxAmount']) {
    assert.equal(l.publicPhase[key], BigInt(e.publicPhase[key]), `public ${key}`);
    assert.equal(l.whitelistPhase[key], BigInt(e.whitelistPhase[key]), `whitelist ${key}`);
  }
  assert.equal(l.trailingBytes, e.trailingBytes);
  assert.ok(raw.subarray(185).every((x) => x === 0), 'the unread tail is all zero, so nothing meaningful sits past byte 185');
});

test('the decoder refuses an account too short to be a Launch, and reads base58 correctly', () => {
  assert.throws(() => decodeLaunch(Buffer.alloc(100)), /at least 185 bytes/);
  assert.equal(base58Encode(Buffer.alloc(32)), '1'.repeat(32), 'leading zero bytes are leading 1s');
  assert.equal(base58Encode(Buffer.from([0, 0, 1])), '112');
});

// ── the comparators, row by row ─────────────────────────────────────────────

const AUTH = 'So11111111111111111111111111111111111111112';
const START = 1_800_000_000;

function matchingLaunch(c) {
  const wl = { price: 0n, startDate: 0n, endDate: 0n, minAmount: 0n, maxAmount: 0n };
  return {
    discriminatorHex: LAUNCH_DISCRIMINATOR_HEX,
    authority: AUTH,
    mint: c.token.mint,
    hardcap: 5000n * SOL,
    softcap: 1000n * SOL,
    soldAmount: 0n,
    whitelistPhase: wl,
    whitelistLimit: 0n,
    publicPhase: {
      price: 33333n,
      startDate: BigInt(START),
      endDate: BigInt(START + 72 * 3600),
      minAmount: 250000000n,
      maxAmount: 25n * SOL,
    },
    paymentMethod: 0,
    trailingBytes: 7,
  };
}

const failing = (rows) => rows.filter((r) => r.status === 'FAIL').map((r) => r.check);
const before = START - 86400;

test('a launch that matches the config passes every row', () => {
  const rows = compareLaunch(cfg(), matchingLaunch(cfg()), { authority: AUTH, nowSeconds: before });
  assert.deepEqual(failing(rows), []);
  assert.equal(exitCodeFor(rows), 0);
  assert.ok(rows.length >= 12, 'the rows were not silently dropped');
  assert.ok(rows.some((r) => r.check === 'edit window' && r.status === 'INFO'));
});

const LAUNCH_MUTATIONS = [
  ['account is a Launch', (l) => { l.discriminatorHex = '0000000000000000'; }],
  ['authority is the signing wallet', (l) => { l.authority = 'EEoMVamYkEvZEDXe7cyMGCLg5BUGNAuvSXC1w2LWELVy'; }],
  ['sale token is RCLAW', (l) => { l.mint = '78TYyPb9cpv9742nZrcKgYf1G6CM6C8p6eiuVGPgJmV7'; }],
  ['paid in SOL', (l) => { l.paymentMethod = 2; }],
  ['hard cap', (l) => { l.hardcap += 1n; }],
  ['soft cap (stored, never read by the program)', (l) => { l.softcap += 1n; }],
  ['sale price', (l) => { l.publicPhase.price = 33334n; }],
  ['minimum buy', (l) => { l.publicPhase.minAmount = 200000000n; }],
  ['maximum buy', (l) => { l.publicPhase.maxAmount = 26n * SOL; }],
  ['public phase length', (l) => { l.publicPhase.endDate += 3600n; }],
  ['whitelist phase is off', (l) => { l.whitelistPhase.price = 1n; }],
  // The phase is "off" only when price AND both dates are zero; the program uses
  // price > 0 as its existence flag, but a date left behind is a half-configured phase.
  ['whitelist phase is off', (l) => { l.whitelistPhase.startDate = 5n; }],
  ['whitelist phase is off', (l) => { l.whitelistPhase.endDate = 5n; }],
  ['nothing sold before the start', (l) => { l.soldAmount = 1n; }],
];

for (const [n, [row, mutate]] of LAUNCH_MUTATIONS.entries()) {
  test(`launch row "${row}" fails when, and only when, its own field is wrong (#${n})`, () => {
    const l = matchingLaunch(cfg());
    mutate(l);
    const rows = compareLaunch(cfg(), l, { authority: AUTH, nowSeconds: before });
    assert.deepEqual(failing(rows), [row]);
    assert.equal(exitCodeFor(rows), 1);
  });
}

test('after the start the edit window is a WARN and sold-so-far is information, not a failure', () => {
  const l = matchingLaunch(cfg());
  l.soldAmount = 3n * SOL;
  const rows = compareLaunch(cfg(), l, { authority: AUTH, nowSeconds: START + 60 });
  assert.equal(rows.find((r) => r.check === 'edit window').status, 'WARN');
  assert.equal(rows.find((r) => r.check === 'sold so far').status, 'INFO');
  assert.deepEqual(failing(rows), []);
});

test('the vault must hold exactly hard cap / the stored price; less is a WARN only once the sale has started', () => {
  const want = deriveSmithiiSale(cfg()).escrowAtCreateBase;
  assert.equal(compareVault(cfg(), want, { started: false })[0].status, 'PASS');
  assert.equal(compareVault(cfg(), want - 1n, { started: false })[0].status, 'FAIL');
  assert.equal(compareVault(cfg(), want - 1n, { started: true })[0].status, 'WARN');
  assert.equal(compareVault(cfg(), want + 1n, { started: true })[0].status, 'FAIL', 'more than escrowed is never explained by a claim');
  assert.equal(compareVault(cfg(), 0n, { started: false })[0].status, 'FAIL', 'an empty vault before the start is a failure, not a zero');
});

test('a vault holding the figure the FORM printed fails, and the row says where that number came from', () => {
  const d = deriveSmithiiSale(cfg());
  const onForm = compareVault(cfg(), d.formSendingBase, { started: false })[0];
  assert.equal(onForm.status, 'FAIL', 'the form\'s number is not the vault figure');
  assert.match(onForm.detail, /the figure the form printed/);
  assert.match(onForm.detail, /'sale price' row/);
  for (const other of [d.escrowAtCreateBase - 1n, d.formSendingBase + 1n, 0n]) {
    assert.doesNotMatch(compareVault(cfg(), other, { started: false })[0].detail, /the figure the form printed/,
      'the note appears for that one figure only');
  }
  assert.doesNotMatch(compareVault(cfg(), d.escrowAtCreateBase, { started: false })[0].detail, /figure the form printed/);
});

test('the program rows: executable, the disclosed upgrade authority, and not redeployed', () => {
  const c = cfg();
  const ok = { executable: true, upgradeAuthority: c.program.upgradeAuthority, lastDeployedSlot: c.program.lastDeployedSlot };
  assert.deepEqual(failing(compareProgram(c, ok)), []);
  assert.deepEqual(failing(compareProgram(c, { ...ok, executable: false })), ['program is executable']);
  assert.deepEqual(failing(compareProgram(c, { ...ok, upgradeAuthority: AUTH })), ['upgrade authority is the one disclosed']);
  assert.deepEqual(failing(compareProgram(c, { ...ok, upgradeAuthority: null })), ['upgrade authority is the one disclosed'],
    'an immutable program is a CHANGE of what the disclosures say, not a pass');
  assert.deepEqual(failing(compareProgram(c, { ...ok, lastDeployedSlot: c.program.lastDeployedSlot + 1 })),
    ['program has not been redeployed since it was measured']);
});

test('the mint rows: classic SPL, both authorities revoked, decimals, supply', () => {
  const c = cfg();
  const ok = { owner: record.token_program, mintAuthority: null, freezeAuthority: null, decimals: 9, supplyBase: record.supply_base_units };
  assert.deepEqual(failing(compareMint(c, record, ok)), []);
  assert.deepEqual(failing(compareMint(c, record, { ...ok, owner: 'TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb' })), ['mint is a classic SPL token']);
  assert.deepEqual(failing(compareMint(c, record, { ...ok, mintAuthority: AUTH })), ['mint authority revoked']);
  assert.deepEqual(failing(compareMint(c, record, { ...ok, freezeAuthority: AUTH })), ['freeze authority revoked']);
  assert.deepEqual(failing(compareMint(c, record, { ...ok, decimals: 6 })), ['decimals']);
  assert.deepEqual(failing(compareMint(c, record, { ...ok, supplyBase: '1' })), ['supply is the recorded supply']);
});

test('exit codes: a failure is 1, a reading that did not happen is 3, and a failure outranks it', () => {
  const r = (status) => ({ check: 'x', status, detail: '' });
  assert.equal(exitCodeFor([r('PASS'), r('INFO'), r('WARN')]), 0);
  assert.equal(exitCodeFor([r('PASS'), r('UNVERIFIED')]), 3);
  assert.equal(exitCodeFor([r('FAIL'), r('PASS')]), 1);
  assert.equal(exitCodeFor([r('FAIL'), r('UNVERIFIED')]), 1);
  assert.equal(exitCodeFor([]), 0);
});
