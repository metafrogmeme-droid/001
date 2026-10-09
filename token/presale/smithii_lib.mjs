// Smithii presale — what the form shows, what the program stores, and what the
// sale costs, DERIVED from smithii.config.json instead of written by hand.
//
// Pure: no network, no keys, nothing but node:fs/path, so the offline plan and
// every test run on a bare checkout. The chain-reading half lives in
// smithii_verify.mjs and hands its readings to the compare* functions below.
//
// WHY THIS EXISTS
//
// smithii.config.json is transcribed into a web form by a person, so nothing
// between a wrong number and a published sale term can reject it. Three of the
// numbers a buyer or the operator will quote are not typed values at all — they
// are what the program does to typed values:
//
//   * The program stores the price as a whole number of LAMPORTS per token.
//     Smithii's SDK (which says it mirrors the site) computes
//     Math.floor(price * 1e9), so the typed 0.00003333333 SOL is stored as
//     33,333 lamports and sells 30,000.30 RCLAW per SOL, not the 30,000 the
//     roadmap says. `priceReading` states both, and flags the case where float
//     arithmetic lands a lamport BELOW the decimal the operator typed.
//   * Create moves hard cap / STORED price tokens out of the signer's account
//     into a vault. Checked against a live third-party sale (140 SOL hard cap,
//     400 lamports per token, 6 decimals: the vault held exactly 350,000,000
//     tokens); see fixtures/smithii_launch_live.json. That is NOT the form's
//     "Sending" line, which divides the hard cap by the price AS TYPED: the live
//     form printed 150.000.015 for 5,000 SOL at 0.00003333333, where the stored
//     price gives 150,001,500.015 (fixtures/smithii_form_reading.json). They
//     differ by the price's lamport rounding, 0.001% here, which is why a vault
//     read-back is compared with `escrowAtCreateBase` and never with the number
//     on the form. This file used to call the two the same line.
//   * The 2.5% fee comes out of the creator's side, so what the operator
//     receives at each cap is derived here rather than recalled.
//
// WHAT THIS DOES NOT PROVE
//
// That the live site rounds like the SDK (it is Smithii's own client, and the
// first smithii_verify run after Create is what settles it), and how odd
// amounts round inside the fee (only an exact multiple has been observed).
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
export const SMITHII_CONFIG = path.join(HERE, 'smithii.config.json');
export const RCLAW_RECORD = path.join(HERE, '..', 'config', 'rclaw.mainnet.json');

export function loadSmithiiConfig(file = SMITHII_CONFIG) {
  return JSON.parse(fs.readFileSync(file, 'utf8'));
}

export function loadTokenRecord(file = RCLAW_RECORD) {
  return JSON.parse(fs.readFileSync(file, 'utf8'));
}

/** Keys disclosures must carry; each must also be stated in the GitBook. */
export const REQUIRED_DISCLOSURES = [
  'noRefund',
  'softCapNotEnforced',
  'noBuyerVesting',
  'noWalletWhitelist',
  'liquidityIsAnOperatorAction',
  'programIsUpgradeable',
  'auditScope',
  'proceedsGoToTheSigningWallet',
];

// ── decimals, exactly ───────────────────────────────────────────────────────

/**
 * A plain decimal (string or number) as an integer count of 10^-places units,
 * TRUNCATED, plus whether anything non-zero was dropped. Floats are never
 * multiplied here: 0.1 * 3 is not 0.3 and a money figure must not depend on it.
 */
export function decimalToUnits(value, places) {
  const s = String(value).trim();
  const m = /^(\d+)(?:\.(\d+))?$/.exec(s);
  if (!m) throw new Error(`not a plain decimal: ${JSON.stringify(value)}`);
  const frac = m[2] ?? '';
  const kept = (frac + '0'.repeat(places)).slice(0, places);
  return {
    // `kept` is empty only for places === 0, where the fraction contributes
    // exactly nothing by definition; it is not a stand-in for a missing reading.
    units: BigInt(m[1]) * 10n ** BigInt(places) + (kept === '' ? 0n : BigInt(kept)),
    truncated: /[1-9]/.test(frac.slice(places)),
  };
}

/** Inverse of decimalToUnits, trailing zeros trimmed: 33333n, 9 -> "0.000033333". */
export function unitsToDecimal(units, places) {
  const neg = units < 0n;
  const s = (neg ? -units : units).toString().padStart(places + 1, '0');
  const whole = s.slice(0, s.length - places);
  const frac = s.slice(s.length - places).replace(/0+$/, '');
  return (neg ? '-' : '') + whole + (frac ? `.${frac}` : '');
}

/** "150001500.015" -> "150,001,500.015", fraction cut to maxFrac places. */
export function formatUnits(units, places, maxFrac = 3) {
  const [whole, frac = ''] = unitsToDecimal(units, places).split('.');
  const grouped = whole.replace(/\B(?=(\d{3})+(?!\d))/g, ',');
  const f = frac.slice(0, maxFrac).replace(/0+$/, '');
  return f ? `${grouped}.${f}` : grouped;
}

const B58 = '123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz';

export function base58Encode(bytes) {
  const b = Uint8Array.from(bytes);
  let n = 0n;
  for (const byte of b) n = (n << 8n) | BigInt(byte);
  let out = '';
  while (n > 0n) {
    out = B58[Number(n % 58n)] + out;
    n /= 58n;
  }
  let zeros = 0;
  while (zeros < b.length && b[zeros] === 0) zeros += 1;
  return '1'.repeat(zeros) + out;
}

export const isBase58Address = (s) =>
  typeof s === 'string' && /^[1-9A-HJ-NP-Za-km-z]{32,44}$/.test(s);

// ── the price, as the program will hold it ──────────────────────────────────

/**
 * What a typed price becomes. `sdkLamports` is Math.floor(price * 1e9), the
 * arithmetic Smithii's SDK uses; `exactLamports` is the typed decimal
 * truncated at the ninth place with no float involved. They differ when float
 * multiplication lands just below an integer (0.000000015 * 1e9 = 14.999…),
 * which silently stores a price one lamport LOW — `floatTrap`.
 */
export function priceReading(typed) {
  const { units: exactLamports, truncated } = decimalToUnits(typed, 9);
  const sdkLamports = BigInt(Math.floor(Number(typed) * 1e9));
  return {
    typed: String(typed),
    exactLamports,
    sdkLamports,
    subLamportDigits: truncated,
    floatTrap: sdkLamports !== exactLamports,
    storedSol: unitsToDecimal(sdkLamports, 9),
    tokensPerSol: sdkLamports > 0n ? 1e9 / Number(sdkLamports) : Infinity,
  };
}

/** Base units of the sale token that `solLamports` buys at `priceLamports` per WHOLE token. */
export function tokensForLamports(solLamports, priceLamports, decimals) {
  if (priceLamports <= 0n) throw new Error('the price rounds to 0 lamports per token');
  return (BigInt(solLamports) * 10n ** BigInt(decimals)) / BigInt(priceLamports);
}

// ── the sale, derived ───────────────────────────────────────────────────────

export function deriveSmithiiSale(cfg) {
  const decimals = cfg.token.decimals;
  const price = priceReading(cfg.sale.priceSol);
  const lamports = (sol) => decimalToUnits(sol, 9).units;
  const feeBps = decimalToUnits(cfg.platformFee.percentOfEachPurchase, 2).units;
  const poolBps = decimalToUnits(cfg.liquidity.intendedPercentOfGrossRaise, 2).units;
  const tokens = (solLamports) => tokensForLamports(solLamports, price.sdkLamports, decimals);

  /** The money and tokens at a GROSS raise of `gross` lamports. */
  const at = (gross) => {
    const smithiiFeeLamports = (gross * feeBps) / 10000n;
    const poolSolLamports = (gross * poolBps) / 10000n;
    return {
      grossLamports: gross,
      smithiiFeeLamports,
      creatorReceivesLamports: gross - smithiiFeeLamports,
      poolSolLamports,
      // A pool opening at the sale price: this much RCLAW against that SOL.
      poolTokensBase: tokens(poolSolLamports),
      afterPoolLamports: gross - smithiiFeeLamports - poolSolLamports,
      buyersClaimBase: tokens(gross),
    };
  };

  const hard = lamports(cfg.sale.hardCapSol);
  const soft = lamports(cfg.sale.softCapSol);
  // The form's own "Sending" line: the hard cap divided by the price EXACTLY AS TYPED, with no
  // flooring to a lamport (the live form, 2026-10-09: fixtures/smithii_form_reading.json). The
  // program takes `escrowAtCreateBase` instead. A typed price is held at 20 decimal places, so a
  // lamport is 10^11 of its units; a price of 0 yields 0n here and is refused by `tokens()` below.
  const typedUnits = decimalToUnits(cfg.sale.priceSol, 20).units;
  const formSendingBase = typedUnits > 0n ? (hard * 10n ** BigInt(decimals) * 10n ** 11n) / typedUnits : 0n;
  return {
    decimals,
    price,
    perWalletLamports: {
      min: lamports(cfg.sale.minContributionSol),
      max: lamports(cfg.sale.maxContributionSol),
    },
    hardCap: at(hard),
    softCap: at(soft),
    escrowAtCreateBase: tokens(hard),
    formSendingBase,
    allocationBase: BigInt(cfg.sale.presaleAllocation) * 10n ** BigInt(decimals),
    creationFeeSol: cfg.whitelist.enabled
      ? cfg.platformFee.creationSolWithWhitelistPhase
      : cfg.platformFee.creationSol,
    at,
  };
}

/**
 * Everything wrong with the config, as sentences; empty means sane. `record`
 * is token/config/rclaw.mainnet.json — the one record the site and bot read —
 * so this file cannot name a second mint.
 */
export function validateSmithiiConfig(cfg, { record } = {}) {
  const problems = [];
  const bad = (m) => problems.push(m);

  if (record) {
    if (cfg.token.mint !== record.mint) bad(`token.mint ${cfg.token.mint} is not the recorded mint ${record.mint}`);
    if (cfg.token.decimals !== record.decimals) bad(`token.decimals ${cfg.token.decimals} is not the recorded ${record.decimals}`);
    if (cfg.token.symbol !== record.symbol) bad(`token.symbol ${cfg.token.symbol} is not the recorded ${record.symbol}`);
  }
  for (const [k, v] of [['program.id', cfg.program.id], ['program.upgradeAuthority', cfg.program.upgradeAuthority], ['platformFee.receiver', cfg.platformFee.receiver]]) {
    if (!isBase58Address(v)) bad(`${k} is not a base58 address: ${JSON.stringify(v)}`);
  }

  let d;
  try {
    d = deriveSmithiiSale(cfg);
  } catch (e) {
    bad(`the sale cannot be derived: ${e.message}`);
    return problems;
  }
  const lam = (sol) => decimalToUnits(sol, 9).units;
  const { softCapSol, hardCapSol, minContributionSol, maxContributionSol } = cfg.sale;

  if (lam(hardCapSol) <= 0n) bad('hard cap must be positive');
  if (lam(softCapSol) > lam(hardCapSol)) bad(`soft cap ${softCapSol} exceeds hard cap ${hardCapSol}`);
  if (lam(minContributionSol) <= 0n) bad('minimum buy must be positive');
  if (lam(minContributionSol) > lam(maxContributionSol)) bad(`minimum buy ${minContributionSol} exceeds maximum ${maxContributionSol}`);
  if (lam(maxContributionSol) > lam(hardCapSol)) bad(`maximum buy ${maxContributionSol} exceeds the hard cap ${hardCapSol}`);

  if (d.price.sdkLamports < 1n) bad('the sale price rounds to 0 lamports per token');
  if (d.price.floatTrap) {
    bad(`the typed price ${d.price.typed} is ${d.price.exactLamports} lamports but float arithmetic stores ${d.price.sdkLamports}: type ${unitsToDecimal(d.price.sdkLamports, 9)} (what would actually be stored) or another price that multiplies cleanly, and publish that one`);
  }
  if (decimalToUnits(cfg.liquidity.launchPriceSol, 9).units < d.price.sdkLamports) {
    bad(`LP launch price ${cfg.liquidity.launchPriceSol} is below the stored sale price ${d.price.storedSol}: a pool opening under what buyers paid is the unrecoverable case`);
  }

  // The escrow the program will take should be the allocation the roadmap names,
  // give or take the lamport rounding of the price (0.001% here).
  const diff = d.escrowAtCreateBase > d.allocationBase ? d.escrowAtCreateBase - d.allocationBase : d.allocationBase - d.escrowAtCreateBase;
  if (diff * 10000n > d.allocationBase) {
    bad(`Create would escrow ${formatUnits(d.escrowAtCreateBase, d.decimals)} RCLAW, more than 0.01% away from the ${cfg.sale.presaleAllocation} allocation`);
  }

  // What the program cannot do must not be claimed.
  if (cfg.sale.refundIfSoftCapMissed !== false) bad('sale.refundIfSoftCapMissed must be false: the program has no refund and pays the creator directly');
  if (cfg.vesting.enabled !== false || cfg.vesting.tgeUnlockPercent !== 100) bad('the program cannot vest: vesting.enabled must be false and tgeUnlockPercent 100');
  if (cfg.whitelist.enabled !== false) bad('a whitelist phase is not modelled here (and the program has no wallet list): whitelist.enabled must be false');
  if (cfg.liquidity.enforcedByProgram !== false) bad('liquidity.enforcedByProgram must be false: the program never touches liquidity');
  if (!(Number.isInteger(cfg.publicPhaseHours) && cfg.publicPhaseHours > 0)) bad('publicPhaseHours must be a positive integer');

  for (const k of REQUIRED_DISCLOSURES) {
    const v = cfg.disclosures?.[k];
    if (typeof v !== 'string' || v.trim().length < 40) bad(`disclosures.${k} is missing or empty`);
  }
  return problems;
}

// ── the Launch account, decoded ─────────────────────────────────────────────

/** sha256('account:Launch')[0..8] — the Anchor tag for the program's Launch account. */
export const LAUNCH_DISCRIMINATOR_HEX = '903333a3ce55d526';
/** Where the documented layout ends; the live account is 192 bytes with a zero tail. */
export const LAUNCH_MIN_BYTES = 185;

/**
 * Layout (little-endian): discriminator(8) | authority(32) | mint(32) |
 * hardcap u64 | softcap u64 | sold_amount u64 | whitelist_phase(5 x u64) |
 * whitelist_limit u64 | public_phase(5 x u64) | payment_method u8, with a phase
 * being price, start, end, min, max. Amounts are in the payment currency's base
 * units (lamports for SOL); price is base units per WHOLE token.
 *
 * Tested against a real account (fixtures/smithii_launch_live.json), not bytes
 * this file wrote.
 */
export function decodeLaunch(bytes) {
  const b = Buffer.from(bytes);
  if (b.length < LAUNCH_MIN_BYTES) throw new Error(`a Launch account is at least ${LAUNCH_MIN_BYTES} bytes, got ${b.length}`);
  const u64 = (o) => b.readBigUInt64LE(o);
  const phase = (o) => ({ price: u64(o), startDate: u64(o + 8), endDate: u64(o + 16), minAmount: u64(o + 24), maxAmount: u64(o + 32) });
  return {
    discriminatorHex: b.subarray(0, 8).toString('hex'),
    authority: base58Encode(b.subarray(8, 40)),
    mint: base58Encode(b.subarray(40, 72)),
    hardcap: u64(72),
    softcap: u64(80),
    soldAmount: u64(88),
    whitelistPhase: phase(96),
    whitelistLimit: u64(136),
    publicPhase: phase(144),
    paymentMethod: b[184],
    trailingBytes: b.length - LAUNCH_MIN_BYTES,
  };
}

// ── comparing readings with the config ──────────────────────────────────────
// Each returns rows { check, status, detail }; status is PASS, FAIL, WARN or
// INFO. UNVERIFIED is added by the caller when a READ failed — never here, so a
// reading that did not happen cannot be mistaken for one that matched.

const row = (check, ok, detail) => ({ check, status: ok ? 'PASS' : 'FAIL', detail });

export function compareLaunch(cfg, launch, { authority, nowSeconds }) {
  const d = deriveSmithiiSale(cfg);
  const lam = (sol) => decimalToUnits(sol, 9).units;
  const sol = (u) => `${unitsToDecimal(u, 9)} SOL`;
  const pub = launch.publicPhase;
  const wl = launch.whitelistPhase;
  const hours = Number(pub.endDate - pub.startDate) / 3600;
  const started = nowSeconds >= Number(pub.startDate);

  const rows = [
    row('account is a Launch', launch.discriminatorHex === LAUNCH_DISCRIMINATOR_HEX, `tag ${launch.discriminatorHex}`),
    row('authority is the signing wallet', launch.authority === authority, `on chain ${launch.authority}`),
    row('sale token is RCLAW', launch.mint === cfg.token.mint, `on chain ${launch.mint}`),
    row('paid in SOL', launch.paymentMethod === 0, `payment_method ${launch.paymentMethod} (0 = SOL)`),
    row('hard cap', launch.hardcap === lam(cfg.sale.hardCapSol), `${sol(launch.hardcap)} on chain, ${cfg.sale.hardCapSol} SOL in config`),
    row('soft cap (stored, never read by the program)', launch.softcap === lam(cfg.sale.softCapSol), `${sol(launch.softcap)} on chain, ${cfg.sale.softCapSol} SOL in config`),
    row('sale price', pub.price === d.price.sdkLamports, `${pub.price} lamports per token on chain; the typed ${d.price.typed} predicts ${d.price.sdkLamports} by the SDK's Math.floor(price*1e9)`),
    row('minimum buy', pub.minAmount === d.perWalletLamports.min, `${sol(pub.minAmount)} on chain, ${cfg.sale.minContributionSol} SOL in config`),
    row('maximum buy', pub.maxAmount === d.perWalletLamports.max, `${sol(pub.maxAmount)} on chain, ${cfg.sale.maxContributionSol} SOL in config`),
    row('public phase length', hours === cfg.publicPhaseHours, `${hours} h on chain, ${cfg.publicPhaseHours} h in config`),
    cfg.whitelist.enabled
      ? row('whitelist phase', false, 'enabled in the config, which this tool does not model')
      : row('whitelist phase is off', wl.price === 0n && wl.startDate === 0n && wl.endDate === 0n, `price ${wl.price}, start ${wl.startDate}, end ${wl.endDate}`),
  ];
  rows.push(started
    ? { check: 'edit window', status: 'WARN', detail: 'the sale has started: the launch can no longer be edited, so any FAIL above stands' }
    : { check: 'edit window', status: 'INFO', detail: `open until ${new Date(Number(pub.startDate) * 1000).toISOString()}: a FAIL above can still be fixed with Smithii's edit` });
  rows.push(started
    ? { check: 'sold so far', status: 'INFO', detail: `${sol(launch.soldAmount)} (gross)` }
    : row('nothing sold before the start', launch.soldAmount === 0n, `${sol(launch.soldAmount)} recorded`));
  return rows;
}

/**
 * The vault's balance against what Create should have moved in: hard cap / the STORED price.
 * Not the form's "Sending" line, which divides by the typed price and is slightly lower.
 */
export function compareVault(cfg, vaultRawAmount, { started }) {
  const d = deriveSmithiiSale(cfg);
  let detail = `${formatUnits(vaultRawAmount, d.decimals)} RCLAW in the vault; Create should escrow ${formatUnits(d.escrowAtCreateBase, d.decimals)}`;
  // A vault holding exactly the figure the FORM printed means the price is not stored the way this
  // file assumes. Say so, or the number on the form would read as the right answer.
  if (vaultRawAmount === d.formSendingBase && d.formSendingBase !== d.escrowAtCreateBase) {
    detail += `; that is the figure the form printed (hard cap / the typed price), so the price may not be stored as ${d.price.sdkLamports} lamports: read the 'sale price' row`;
  }
  if (vaultRawAmount === d.escrowAtCreateBase) return [row('vault holds hard cap / price tokens', true, detail)];
  if (started && vaultRawAmount < d.escrowAtCreateBase) {
    return [{ check: 'vault holds hard cap / price tokens', status: 'WARN', detail: `${detail}; the sale has started, so claims or a withdraw may have moved some` }];
  }
  return [row('vault holds hard cap / price tokens', false, detail)];
}

/** The program against what the disclosures say about it. */
export function compareProgram(cfg, observed) {
  return [
    row('program is executable', observed.executable === true, `executable=${observed.executable}`),
    row('upgrade authority is the one disclosed', observed.upgradeAuthority === cfg.program.upgradeAuthority,
      `on chain ${observed.upgradeAuthority ?? 'none (immutable)'}; disclosed ${cfg.program.upgradeAuthority}`),
    row('program has not been redeployed since it was measured', Number(observed.lastDeployedSlot) === cfg.program.lastDeployedSlot,
      `last deployed slot ${observed.lastDeployedSlot}; measured ${cfg.program.lastDeployedSlot} on ${cfg.program.measuredAt}`),
  ];
}

/** The mint against the one record and the program's requirements. */
export function compareMint(cfg, record, observed) {
  return [
    row('mint is a classic SPL token', observed.owner === record.token_program, `owner ${observed.owner}`),
    row('mint authority revoked', observed.mintAuthority == null, `mintAuthority ${observed.mintAuthority ?? 'none'}`),
    row('freeze authority revoked', observed.freezeAuthority == null, `freezeAuthority ${observed.freezeAuthority ?? 'none'}`),
    row('decimals', observed.decimals === cfg.token.decimals, `${observed.decimals} on chain, ${cfg.token.decimals} in config`),
    row('supply is the recorded supply', String(observed.supplyBase) === record.supply_base_units, `${observed.supplyBase} on chain, ${record.supply_base_units} recorded`),
  ];
}

/** 1 if anything FAILED, else 3 if anything could not be read, else 0. */
export function exitCodeFor(rows) {
  if (rows.some((r) => r.status === 'FAIL')) return 1;
  if (rows.some((r) => r.status === 'UNVERIFIED')) return 3;
  return 0;
}
