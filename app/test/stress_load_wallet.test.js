'use strict';
// The digital twin was always hypothetical: you typed a book and broke it.
// If a wallet is linked, the read-only mirror already knows the real holdings —
// so the twin can be YOUR book instead of an invented one.
//
// The caveats are the product here, not decoration. The mirror sees SPOT
// balances on tracked chains. It has no leverage and no perp legs, so every row
// loads at 1x long. Someone holding leverage elsewhere would otherwise read a
// far gentler drawdown than their real one and believe it.

const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

const i18n = require('../public/js/i18n.js');
const codes = i18n.LANGS.map((l) => l.code);
const page = fs.readFileSync(path.join(__dirname, '..', 'public', 'stress.html'), 'utf8');
const model = require('../public/js/stress-model.js');

const KEYS = ['sx.load_wallet', 'sx.w_loading', 'sx.w_signed_out', 'sx.w_none',
  'sx.w_empty', 'sx.w_loaded', 'sx.w_dropped', 'sx.w_failed'];

test('the loader is wired and reads the mirror read-only', () => {
  assert.match(page, /id="loadWallet"/);
  assert.match(page, /\$\('loadWallet'\)\.addEventListener\('click', loadWallet\)/);
  assert.match(page, /fetch\('\/api\/wallet\/portfolio'/,
    'it must use the existing read-only mirror, not a new data path');
  // GET only — this page must never be able to change anything.
  assert.doesNotMatch(page, /\/api\/wallet\/portfolio'[^)]*method:\s*'POST'/,
    'the stress page must never POST to the wallet');
});

test('weights are percent — no dollar figure reaches the page', () => {
  assert.match(page, /Number\(a\.usd\) \/ total/, 'weights must be a share of the total');
  // The computed weight is the only thing that leaves the usd values.
  const loader = page.slice(page.indexOf('async function loadWallet'), page.indexOf('function sevOf'));
  assert.doesNotMatch(loader, /\$\{[^}]*usd[^}]*\}/, 'a raw usd value is interpolated into the page');
  assert.doesNotMatch(loader, /toLocaleString|\$'/, 'a currency figure is being formatted for display');
});

test('the leverage caveat is stated, and in every language', () => {
  // The single most important sentence: 1x long is what the mirror can see,
  // not what the user necessarily holds.
  const e = i18n.STRINGS['sx.w_loaded'];
  assert.match(e.en, /1x long/i);
  assert.match(e.en, /real drawdown is worse/i);
  const lev = {
    en: /1x long/i, hi: /1x long/i, it: /1x long/i, es: /1x largo/i, zh: /1 倍做多/,
    pt: /1x long/i, fr: /1x long/i, de: /1x long/i, nl: /1x long/i, ja: /1 倍ロング/,
    ko: /1배 롱/, ru: /1x лонг/i, tr: /1x uzun/i, ar: /1x شراء/,
  };
  for (const c of codes) {
    assert.match(String(e[c]), lev[c],
      `sx.w_loaded:${c} dropped the "every row is 1x long" caveat — the result `
        + 'would understate a leveraged book without saying so');
  }
});

test('the row cap is the model’s own, and truncation is announced', () => {
  // Silently dropping holdings would make the twin quietly wrong. stress_portfolio
  // caps at 60 server-side; the page must use the same number and say what it cut.
  assert.match(page, /var MAX_ROWS = 60;/);
  assert.match(page, /sx\.w_dropped/, 'holdings can be dropped with no mention');
  for (const c of codes) {
    assert.match(String(i18n.STRINGS['sx.w_dropped'][c]), /\{d\}/, `sx.w_dropped:${c} lost {d}`);
    assert.match(String(i18n.STRINGS['sx.w_dropped'][c]), /\{max\}/, `sx.w_dropped:${c} lost {max}`);
  }
});

test('every not-loaded state is distinct and honest', () => {
  // Signed out, no wallet, empty wallet and a failed read are four different
  // answers. Collapsing them would tell a signed-out user they have no wallet.
  for (const k of ['sx.w_signed_out', 'sx.w_none', 'sx.w_empty', 'sx.w_failed']) {
    assert.match(page, new RegExp(k.replace('.', '\\.')), `${k} is never shown`);
  }
  assert.match(i18n.STRINGS['sx.w_failed'].en, /nothing was changed/i);
});

test('wrapped majors are not mis-shocked as alts', () => {
  // A mirrored wallet holds WBTC/WETH, not BTC/ETH. If the model classed them
  // as alts they would take the harsher alt shock and overstate the drawdown.
  assert.equal(model.classify('WBTC'), 'major');
  assert.equal(model.classify('WETH'), 'major');
  assert.equal(model.classify('USDC'), 'stable');
});

test('a mirrored spot book runs through the real scenarios', () => {
  // End-to-end on the shape the loader actually produces: percent weights,
  // leverage 1, long. Nothing should liquidate — spot cannot.
  const book = [
    { asset: 'WBTC', weight: 50, leverage: 1, dir: 'long' },
    { asset: 'WETH', weight: 30, leverage: 1, dir: 'long' },
    { asset: 'USDC', weight: 20, leverage: 1, dir: 'long' },
  ];
  const out = model.runAll(book);
  assert.ok(out.length >= 5, 'every scenario runs');
  for (const s of out) {
    assert.equal(typeof s.result.drawdownPct, 'number');
    assert.equal(s.result.liquidatedCount, 0, 'unleveraged spot cannot liquidate');
  }
});

test('every wallet-loader string exists in all 14 languages', () => {
  for (const k of KEYS) {
    const e = i18n.STRINGS[k];
    assert.ok(e, `${k} missing from the dictionary`);
    for (const c of codes) assert.ok(String(e[c] || '').trim().length, `${k} is missing ${c}`);
  }
});
