// The token record announces the sale, and states what the sale config sets.
//
// token/config/rclaw.mainnet.json is the one record the website (/token) and the bot (/rclaw)
// read, and both print a presale term exactly as the record writes it. Once the presale was
// announced (2026-10-10) the record carried the sale's terms as sentences: a second copy of
// smithii.config.json. recordTerms (smithii_lib.mjs) derives those sentences from the config,
// and this file holds the record to them, so a changed price, window, cap or pool share that
// is not carried into the record fails here instead of the site and the bot announcing the
// old terms.

import { test } from 'node:test';
import assert from 'node:assert/strict';

import { loadSmithiiConfig, loadTokenRecord, recordTerms } from './smithii_lib.mjs';

const clone = (o) => JSON.parse(JSON.stringify(o));
const terms = (presale) => Object.fromEntries(
  Object.entries(presale).filter(([k]) => !k.startsWith('_') && k !== 'status' && k !== 'sale_url'));

test('the record announces the sale with exactly the terms the config sets', () => {
  const presale = loadTokenRecord().presale;
  assert.equal(presale.status, 'announced');
  const want = recordTerms(loadSmithiiConfig());
  assert.deepEqual(terms(presale), want,
    `token/config/rclaw.mainnet.json presale differs from smithii.config.json; the terms it should carry:\n${
      JSON.stringify(want, null, 2)}\nthen run node app/scripts/sync_content.js`);
  // Keys in the order the config's sentences come in: the bot prints the record in its own order.
  assert.deepEqual(Object.keys(terms(presale)), Object.keys(want));
});

test('the sale link is not set before Create', () => {
  // A link is announced only once the sale exists and presale:smithii-verify has read it back;
  // until then the readers say "not created yet". This pins the state the record is in today,
  // and is the line to change, with the link, after Create.
  assert.equal(loadTokenRecord().presale.sale_url, null);
});

test('each sentence follows the config value it states', () => {
  const base = loadSmithiiConfig();
  const moved = clone(base);
  moved.sale.priceSol = '0.00005';
  moved.liquidity.launchPriceSol = '0.00005';
  moved.sale.hardCapSol = 4000;
  moved.sale.softCapSol = 800;
  moved.sale.minContributionSol = 0.5;
  moved.sale.maxContributionSol = 20;
  moved.sale.presaleAllocation = '120000000';
  moved.liquidity.intendedPercentOfGrossRaise = 66.67;
  moved.schedule.startUtc = '2026-12-28T14:00:00Z';
  moved.schedule.endUtc = '2027-01-11T14:00:00Z';
  const t = recordTerms(moved);
  assert.equal(t.price, '20,000 RCLAW per SOL');
  assert.equal(t.hard_cap, '4,000 SOL (soft cap 800 SOL: a target, not enforced)');
  assert.equal(t.per_wallet, '0.5–20 SOL');
  assert.equal(t.allocation_tokens, '120000000');
  assert.match(t.after_sale, / with 66\.67% of the SOL raised /);
  assert.equal(t.date, '28 Dec 2026 → 11 Jan 2027, or until the hard cap');
  // and the real config reads as the sale was announced
  const real = recordTerms(base);
  assert.equal(real.price, '30,000.3 RCLAW per SOL');
  assert.equal(real.date, '15 Oct → 29 Oct 2026, or until the hard cap');
});

test('a config these sentences cannot describe throws instead of printing them', () => {
  const base = loadSmithiiConfig();
  const cases = {
    'a whitelist phase': (c) => { c.whitelist.enabled = true; },
    'buyer vesting': (c) => { c.vesting.enabled = true; },
    'a partial unlock': (c) => { c.vesting.tgeUnlockPercent = 33; },
    'a refund': (c) => { c.sale.refundIfSoftCapMissed = true; },
    'a pool off the sale price': (c) => { c.liquidity.launchPriceSol = '0.00004'; },
    'an LP that is kept': (c) => { c.liquidity.lpDisposition = 'lock'; },
    'a pool the program creates': (c) => { c.liquidity.enforcedByProgram = true; },
    'another venue': (c) => { c.venue = 'metaplex-genesis'; },
    'another cluster': (c) => { c.cluster = 'devnet'; },
    'an impossible window': (c) => { c.schedule.endUtc = '2026-11-31T14:00:00Z'; },
  };
  for (const [name, mutate] of Object.entries(cases)) {
    const c = clone(base);
    mutate(c);
    assert.throws(() => recordTerms(c), Error, name);
  }
  assert.doesNotThrow(() => recordTerms(clone(base)));
});
