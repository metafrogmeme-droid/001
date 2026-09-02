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

/**
 * A 502 is not an absent wallet.
 *
 * Both pages gated on `if (!r.ok || !d || !d.linked)` and rendered
 * `sx.w_none` — "No linked wallet found. Link one in Account, then come back."
 * `!r.ok` is a FAILED READ (502, 500, a rate limit); `!d` is unparseable JSON.
 * Neither is an absence of a wallet, and the remedy is actively wrong for
 * them: the user goes to Account and finds the wallet already linked.
 *
 * `sx.w_failed` — "Could not read the wallet mirror just now" — already
 * existed for exactly this and was reachable only from the outer `catch`,
 * which fires when `fetch` THROWS and never when it returns a 502. The string
 * was right and the branch could not reach it.
 */
const PAGES = ['escape.html', 'stress.html'];

function pageSrc(name) {
  return fs.readFileSync(path.join(__dirname, '..', 'public', name), 'utf8');
}

test('a non-ok wallet response is reported as a failed read, not as no wallet', () => {
  for (const name of PAGES) {
    const src = pageSrc(name);
    assert.ok(!/if \(!r\.ok \|\| !d \|\| !d\.linked\)/.test(src),
      `${name}: the three conditions are collapsed again — a 502 renders as "no wallet"`);
    assert.match(src, /if \(!r\.ok \|\| !d\)[\s\S]{0,200}sx\.w_failed/,
      `${name}: !r.ok must reach sx.w_failed`);
    assert.match(src, /if \(!d\.linked\)[\s\S]{0,200}sx\.w_none/,
      `${name}: only !d.linked may claim there is no wallet`);
  }
});

// Anchor on the CALL, never the bare key. The first draft of the two tests
// below used `src.indexOf('sx.w_failed')`, which found the COMMENT above the
// fix — the comment-matching trap, self-inflicted: two mutations that swapped
// the branch order and moved the wrong remedy onto the failed read both
// survived, because the assertions were reading an explanation of the code
// rather than the code.
const CALL = (key) => `walletNote(T('${key}'`;

test('the failed-read branch precedes the no-wallet branch', () => {
  // Order matters: `!d.linked` on a null `d` would throw, and on a 502 body
  // that happens to lack `linked` it would claim no wallet.
  for (const name of PAGES) {
    const src = pageSrc(name);
    const failed = src.indexOf(CALL('sx.w_failed'));
    const none = src.indexOf(CALL('sx.w_none'));
    assert.ok(failed > 0 && none > 0, `${name}: both calls must be present`);
    assert.ok(failed < none,
      `${name}: the failed-read guard must run before the no-wallet claim`);
  }
});

test('the remedy is only offered when it is the right one', () => {
  // "Link one in Account" is correct advice for exactly one of the three
  // conditions. It must not travel with the other two.
  for (const name of PAGES) {
    const src = pageSrc(name);
    const i = src.indexOf(CALL('sx.w_failed'));
    assert.ok(i > 0, `${name}: the failed-read call must exist`);
    const window = src.slice(i, i + 200);
    assert.ok(!/Link one in Account/.test(window),
      `${name}: a failed read must not tell the user to link a wallet they have`);
  }
});
