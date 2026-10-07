'use strict';
/**
 * A phone with the Phantom app installed does not inject window.phantom into
 * Chrome. That injection exists in the desktop extension and in Phantom's own
 * in-app browser. Missing injection is not "install Phantom".
 *
 * Both arms: a present provider can Connect & verify and is not told to
 * install; a mobile browser with no provider is not told to install and is
 * offered the Phantom browse link this repo already uses (or Watch only); a
 * desktop browser with no extension may be told to install or enable it.
 * Watch only posts a valid pasted address with no provider and no signature.
 * An invalid address does not post, and is not rewritten into a linked one.
 */

const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

const { codeOnly } = require('./helpers/code_only');
const server = require('../lib/solana');

const MOD = path.join(__dirname, '..', 'public', 'js', 'solana_wallet.js');
const W = require(MOD);

const GOOD = 'EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v';
const OTHER = '7xKXtg2CW87d97TXJSDpbD5jBkheTqA83TZRuJosgAsU';
// What a phone user can end up with in the field: a truncated paste.
const PARTIAL = 'EEoMVamYkEvZEDXe7cyMGCLg5BUG';
const PAGE = 'https://runeclaw.example/dashboard?tab=account';
const IPHONE = 'Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) '
  + 'AppleWebKit/605.1.15 (KHTML, like Gecko) CriOS/120.0.0.0 Mobile/15E148';
const ANDROID = 'Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 '
  + '(KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36';
const DESKTOP = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
  + '(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36';

function texts(offer) {
  return [offer.intro, offer.toast, offer.primary && offer.primary.label]
    .filter((s) => s != null)
    .join('\n');
}

test.afterEach(() => { delete global.window; });

test('the client address check agrees with the server', () => {
  const samples = [
    GOOD, OTHER, '', 'abc', null, undefined, '0x' + 'ab'.repeat(20),
    PARTIAL, GOOD + 'A', ' ' + GOOD + ' ', '1'.repeat(32), '1'.repeat(31),
    '1'.repeat(44), 'O' + '1'.repeat(40), 'I' + '1'.repeat(40),
    'l' + '1'.repeat(40), '0' + '1'.repeat(40),
  ];
  for (const s of samples) {
    assert.strictEqual(W.isSolanaAddress(s), server.isSolanaAddress(s), JSON.stringify(s));
  }
});

test('a present provider can connect and verify, and is not told to install', async () => {
  const calls = [];
  global.window = {
    navigator: { userAgent: IPHONE },
    location: { href: PAGE },
    phantom: { solana: {
      isPhantom: true,
      publicKey: { toString: () => GOOD },
      connect: async () => ({ publicKey: { toString: () => GOOD } }),
      request: async () => { calls.push('request'); throw new Error('transaction'); },
      signMessage: async () => { calls.push('signMessage'); throw new Error('message'); },
    } },
  };
  assert.strictEqual(W.available(), true);
  const offer = W.solanaLinkOffer(W.detectionState({
    provider: true,
    userAgent: IPHONE,
    pageUrl: PAGE,
  }));
  assert.strictEqual(offer.primary.kind, 'connect');
  assert.strictEqual(offer.primary.label, 'Connect & verify');
  assert.strictEqual(offer.showInstall, false);
  assert.strictEqual(offer.openInPhantom, undefined);
  assert.doesNotMatch(texts(offer), /install/i);
  const connected = await W.connect();
  assert.strictEqual(connected.address, GOOD);
  assert.deepStrictEqual(calls, [], 'connect does not sign a transaction');
});

test('a mobile browser with no provider is not told to install Phantom', () => {
  for (const ua of [IPHONE, ANDROID, 'Mozilla/5.0 (iPad)']) {
    const state = W.detectionState({ provider: false, userAgent: ua, pageUrl: PAGE });
    const offer = W.solanaLinkOffer(state);
    assert.strictEqual(state.connect, false, ua);
    assert.strictEqual(state.sayInstall, false, ua);
    assert.strictEqual(offer.showInstall, false, ua);
    assert.doesNotMatch(texts(offer), /install/i, ua);
    assert.match(offer.intro, /cannot see a Solana wallet in this browser/);
    assert.match(offer.intro, /Watch only/);
    assert.match(offer.intro, /does not|never signs|Nothing here signs/i);
    assert.strictEqual(offer.primary.kind, 'open', ua);
    assert.strictEqual(offer.primary.label, 'Open in Phantom');
    const expected = 'https://phantom.app/ul/browse/' + encodeURIComponent(PAGE)
      + '?ref=' + encodeURIComponent('https://runeclaw.example');
    assert.strictEqual(offer.primary.href, expected);
    assert.strictEqual(offer.watchOnly, true);
  }
});

test('a desktop browser with no extension may say install or enable it', () => {
  const state = W.detectionState({ provider: false, userAgent: DESKTOP, pageUrl: PAGE });
  const offer = W.solanaLinkOffer(state);
  assert.strictEqual(state.connect, false);
  assert.strictEqual(state.sayInstall, true);
  assert.strictEqual(offer.showInstall, true);
  assert.strictEqual(offer.primary.kind, 'connect');
  assert.strictEqual(offer.openInPhantom, undefined);
  assert.match(offer.toast, /install or enable/i);
  assert.match(offer.toast, /extension/i);
  assert.match(offer.intro, /Watch only/);
  assert.match(state.message, /Phantom or Backpack/);
});

test('connect() on a phone names the missing injection, not a missing install', async () => {
  global.window = { navigator: { userAgent: IPHONE }, location: { href: PAGE } };
  await assert.rejects(() => W.connect(), (e) => {
    assert.strictEqual(e.code, 'NO_WALLET');
    assert.doesNotMatch(e.message, /install/i);
    assert.match(e.message, /cannot see a Solana wallet in this browser/);
    return true;
  });
});

test('connect() on a desktop with no extension may say install or enable', async () => {
  global.window = { navigator: { userAgent: DESKTOP }, location: { href: PAGE } };
  await assert.rejects(() => W.connect(), (e) => {
    assert.strictEqual(e.code, 'NO_WALLET');
    assert.match(e.message, /install or enable/i);
    assert.match(e.message, /extension/i);
    return true;
  });
});

test('watch only accepts a valid address without a provider and without signing', () => {
  let touched = false;
  global.window = {
    phantom: { solana: {
      isPhantom: true,
      connect: async () => { touched = true; },
      signMessage: async () => { touched = true; },
      request: async () => { touched = true; },
    } },
  };
  assert.strictEqual(W.available(), true, 'a provider is present and must still be ignored');
  const watched = W.watchOnlyPostBody('  ' + GOOD + '\n');
  assert.deepStrictEqual(watched, { post: true, body: { address: GOOD } });
  assert.strictEqual(Object.hasOwn(watched.body, 'signature'), false);
  assert.notStrictEqual(watched.body.address, OTHER);
  assert.strictEqual(touched, false);

  delete global.window;
  const bare = W.watchOnlyPostBody(OTHER);
  assert.deepStrictEqual(bare, { post: true, body: { address: OTHER } });
  assert.strictEqual(W.available(), false);
});

test('an invalid address does not link and is not coerced into one', () => {
  global.window = {
    phantom: { solana: {
      isPhantom: true,
      publicKey: { toString: () => GOOD },
      connect: async () => { throw new Error('must not connect'); },
    } },
  };
  for (const bad of [PARTIAL, 'not-base58!', '0x' + 'ab'.repeat(20), GOOD + 'A',
    '', '   ', null, undefined]) {
    const watched = W.watchOnlyPostBody(bad);
    assert.strictEqual(watched.post, false, JSON.stringify(bad));
    assert.strictEqual(watched.body, undefined, JSON.stringify(bad));
    assert.notStrictEqual(watched.address, GOOD);
    const prep = W.prepareWatchOnly(bad);
    assert.strictEqual(prep.ok, false, JSON.stringify(bad));
    assert.strictEqual(prep.address, null, JSON.stringify(bad));
  }
  assert.strictEqual(W.prepareWatchOnly(PARTIAL).reason, 'invalid');
  assert.strictEqual(W.prepareWatchOnly('   ').reason, 'empty');
});

test('the account page renders the offer and posts the watch body unchanged', () => {
  const dash = codeOnly(fs.readFileSync(
    path.join(__dirname, '..', 'public', 'js', 'dashboard.js'), 'utf8'));
  assert.match(dash, /detectionState/);
  assert.match(dash, /solanaLinkOffer/);
  assert.match(dash, /id="solOpenPhantom"/);
  assert.match(dash, /watchOnlyPostBody/);
  assert.match(dash, /body: watched\.body/);
  assert.doesNotMatch(dash, /No Solana wallet detected/);
  assert.doesNotMatch(dash, /install Phantom or Backpack/i);
  const watch = dash.slice(
    dash.indexOf("closest('#solWatch')"),
    dash.indexOf("closest('#solUnwatch')"));
  assert.ok(watch.includes('solanaWatchBody(window.RCSolanaWallet'));
  assert.match(watch, /body: watched\.body/);
  assert.doesNotMatch(watch, /signature|signMessage|signAndSend/);
});

test('the swap page uses the same detection reading', () => {
  const swap = codeOnly(fs.readFileSync(
    path.join(__dirname, '..', 'public', 'js', 'swap-page.js'), 'utf8'));
  assert.match(swap, /detectionState/);
  assert.doesNotMatch(swap, /Install Phantom or Backpack/);
});

test('the page global exposes the detection reading', () => {
  const src = fs.readFileSync(MOD, 'utf8');
  const sandboxWindow = {};
  new Function('window', src)(sandboxWindow);
  assert.ok(sandboxWindow.RCSolanaWallet);
  for (const fn of ['available', 'detectionState', 'solanaLinkOffer',
    'prepareWatchOnly', 'watchOnlyPostBody', 'phantomBrowseHref']) {
    assert.strictEqual(typeof sandboxWindow.RCSolanaWallet[fn], 'function', fn);
  }
});


// ── iPad, the Phantom sign-in, and an address nobody could check ──────────

const IPAD_DESKTOP_CLASS = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 '
  + '(KHTML, like Gecko) Version/17.0 Safari/605.1.15';

test('an iPad sending a Mac user agent is a tablet, not a desktop told to install', () => {
  const ipad = W.detectionState({ provider: false, userAgent: IPAD_DESKTOP_CLASS,
    pageUrl: PAGE, maxTouchPoints: 5 });
  assert.strictEqual(ipad.mobile, true);
  assert.strictEqual(ipad.sayInstall, false);
  assert.strictEqual(W.solanaLinkOffer(ipad).primary.kind, 'open');
  // The same UA on a Mac, which has no touch points, may still say install.
  const mac = W.detectionState({ provider: false, userAgent: IPAD_DESKTOP_CLASS,
    pageUrl: PAGE, maxTouchPoints: 0 });
  assert.strictEqual(mac.mobile, false);
  assert.strictEqual(mac.sayInstall, true);
});

test('the touch points are read off the page when the caller does not pass them', () => {
  global.window = { navigator: { userAgent: IPAD_DESKTOP_CLASS, maxTouchPoints: 5 },
    location: { href: PAGE } };
  assert.strictEqual(W.detectionState({ provider: false }).mobile, true);
  global.window = { navigator: { userAgent: IPAD_DESKTOP_CLASS, maxTouchPoints: 0 },
    location: { href: PAGE } };
  assert.strictEqual(W.detectionState({ provider: false }).mobile, false);
});

test('the Phantom offer says its browser signs in on its own', () => {
  const offer = W.solanaLinkOffer(W.detectionState({ provider: false, userAgent: IPHONE, pageUrl: PAGE }));
  assert.match(offer.intro, /keeps its own sign-in, so you log in there once/);
});

function watchSeam() {
  const vm = require('node:vm');
  const raw = fs.readFileSync(path.join(__dirname, '..', 'public', 'js', 'dashboard.js'), 'utf8');
  const a = raw.indexOf('// ── the watch-only answer: renderer start ─');
  const b = raw.indexOf('// ── the watch-only answer: renderer end ─');
  assert.ok(a > 0 && b > a, 'the watch-only answer lost its markers');
  const ctx = { out: null };
  vm.runInNewContext(raw.slice(a, b) + '\nout = { solanaWatchBody, solanaWatchRefusal };', ctx);
  return ctx.out;
}

test('an address the missing wallet script could not check is not called invalid', () => {
  const { solanaWatchBody, solanaWatchRefusal } = watchSeam();
  const tr = (k, en) => en;
  const unchecked = solanaWatchBody(undefined, GOOD);
  assert.deepStrictEqual({ ...unchecked }, { post: false, reason: 'unchecked' });
  assert.match(solanaWatchRefusal(unchecked, tr), /did not load, so the address was not checked/);
  assert.doesNotMatch(solanaWatchRefusal(unchecked, tr), /not a Solana address/);
  // With the script, the module's own answer, both arms.
  assert.strictEqual(solanaWatchBody(W, GOOD).post, true);
  const bad = solanaWatchBody(W, PARTIAL);
  assert.strictEqual(bad.post, false);
  assert.match(solanaWatchRefusal(bad, tr), /That is not a Solana address/);
  assert.match(solanaWatchRefusal(solanaWatchBody(W, ''), tr), /Paste a Solana address first/);
});
