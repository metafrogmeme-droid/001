'use strict';
// Wallet linking, made usable.
//
// Three things were wrong, and none of them was the backend — the EVM link,
// the QR handoff and the nonce/verify round-trip were all exercised end to end
// with real signatures and all worked.
//
//  1. The PRIMARY button was "Link wallet" unconditionally, including on a
//     machine where the page had already detected no wallet and said so in the
//     hint directly underneath. The most prominent control apologised via toast;
//     the one that could actually finish was the grey one beside it.
//  2. Nothing said WHICH wallet was found, so a user with two extensions could
//     not tell which one was about to open — and a user with one had no signal
//     the page could see it at all before committing to a click.
//  3. The QR poll stopped after ~2 minutes and said nothing, leaving a
//     live-looking QR that no longer did anything. The copy promised 10
//     minutes; the code gave up at two and never mentioned it.

const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

const i18n = require('../public/js/i18n.js');
const codes = i18n.LANGS.map((l) => l.code);
const dash = fs.readFileSync(path.join(__dirname, '..', 'public', 'js', 'dashboard.js'), 'utf8');
const css = fs.readFileSync(path.join(__dirname, '..', 'public', 'styles.css'), 'utf8');

const W_KEYS = ['dd.w_generic', 'dd.w_link_named', 'dd.w_link_generic', 'dd.w_link_phone',
  'dd.w_open_in_app', 'dd.w_detected', 'dd.w_detected_multi', 'dd.w_hint_mobile',
  'dd.w_hint_desktop', 'dd.w_intro', 'dd.w_qr_help', 'dd.w_qr_waiting',
  'dd.w_qr_expired', 'dd.w_qr_renew', 'dd.w_qr_linked'];

test('the achievable action is the primary button', () => {
  // With a wallet: sign here. Without: the phone route is the only one that
  // can finish, so it leads. The old code hardcoded linkBtn as primary.
  assert.match(dash, /const buttons = w\.present\s*\n\s*\? linkBtn\(true\) \+ phoneBtn\(false\)\s*\n\s*: phoneBtn\(true\) \+ linkBtn\(false\)/,
    'the primary button no longer follows wallet detection');
  assert.doesNotMatch(dash, /btn--primary btn--sm" id="walletLink"/,
    'a hardcoded primary Link-wallet button is back');
});

test('detection names the wallet, and never denies one it can see', () => {
  assert.match(dash, /function detectWallet\(\)/);
  // An unrecognised-but-present provider must still count as present.
  assert.match(dash, /name: names\[0\] \|\| T\('dd\.w_generic'/,
    'an unknown wallet brand would report as no wallet at all');
  assert.match(dash, /Array\.isArray\(eth\.providers\)/,
    'EIP-1193 multi-provider (two extensions installed) is not handled');
});

test('MetaMask is matched LAST, because other wallets impersonate it', () => {
  // Rabby, Trust, OKX and others set isMetaMask for compatibility. If it were
  // checked first, every one of them would be labelled MetaMask.
  const start = dash.indexOf('const WALLET_BRANDS');
  const block = dash.slice(start, dash.indexOf('];', start));
  const mm = block.indexOf("'isMetaMask'");
  const rabby = block.indexOf("'isRabby'");
  assert.ok(rabby > -1 && mm > rabby,
    'isMetaMask must be the last-resort match, not the first');
});

test('the QR reports its own expiry instead of dying silently', () => {
  assert.match(dash, /dd\.w_qr_waiting/, 'no countdown while waiting');
  assert.match(dash, /dd\.w_qr_expired/, 'expiry is never announced');
  assert.match(dash, /is-expired/, 'the expired QR is not visually marked');
  assert.match(dash, /walletQrRenew/, 'no way to get a fresh code once expired');
  // The old bounded poll counter is gone: it stopped at ~2 min regardless of
  // the code's real lifetime and said nothing when it did.
  assert.doesNotMatch(dash, /\+\+polls > 24/, 'the silent 2-minute give-up is back');
});

test('the countdown is driven by the server TTL, not a hardcoded guess', () => {
  assert.match(dash, /Number\(r\.data\.expires_in_sec\) \|\| 600/,
    'the countdown must use the TTL the server actually issued');
  assert.match(dash, /Math\.max\(30, /, 'a nonsensical TTL should still be floored');
});

test('the renew button is matched before the open button', () => {
  // #walletQrRenew lives INSIDE the QR box. If #walletQr were tested first it
  // would not match it at all — but ordering it wrongly is an easy regression.
  const renew = dash.indexOf("closest('#walletQrRenew')");
  const open = dash.indexOf("closest('#walletQr')");
  assert.ok(renew > -1 && open > renew, 'renew must be handled before the open button');
});

test('every new wallet string exists in all 14 languages', () => {
  for (const k of W_KEYS) {
    const e = i18n.STRINGS[k];
    assert.ok(e, `${k} missing from the dictionary`);
    for (const c of codes) assert.ok(String(e[c] || '').trim().length, `${k} is missing ${c}`);
  }
});

test('the {wallet} and {mmss} slots survive translation', () => {
  const slots = {
    'dd.w_link_named': /\{wallet\}/,
    'dd.w_detected': /\{wallet\}/,
    'dd.w_detected_multi': /\{wallet\}/,
    'dd.w_qr_waiting': /\{mmss\}/,
  };
  for (const [k, re] of Object.entries(slots)) {
    for (const c of codes) {
      assert.match(String(i18n.STRINGS[k][c]), re,
        `${k}:${c} dropped its slot — the message would name nothing`);
    }
  }
});

test('the read-only promise survives translation', () => {
  // §4: linking must never read as authorising a transaction. Every language
  // has to keep the "signs a login message, never a transaction" distinction.
  // Checked by MEANING, not by length: a character count would call the
  // Chinese translation truncated purely because CJK is denser than Latin.
  const e = i18n.STRINGS['dd.w_intro'];
  const promise = {
    en: /never a transaction/i, hi: /ट्रांज़ैक्शन नहीं/, it: /mai una transazione/i,
    es: /nunca una transacción/i, zh: /絕不簽交易/, pt: /nunca uma transação/i,
    fr: /jamais une transaction/i, de: /nie eine Transaktion/i,
    nl: /nooit een transactie/i, ja: /取引には署名しません/,
    ko: /거래에는 서명하지 않습니다/, ru: /не транзакцию/i,
    tr: /asla bir işlem imzalamaz/i, ar: /ولا توقّع أي معاملة/,
  };
  for (const c of codes) {
    assert.match(String(e[c]), promise[c],
      `dd.w_intro:${c} dropped "signs a login message, NEVER a transaction" — `
        + 'linking would read as authorising one');
  }
});

test('the QR is framed and visibly dead once expired', () => {
  assert.match(css, /\.wl-qr \{/, 'the QR is still a bare white square');
  assert.match(css, /\.wl-qr\.is-expired \{/, 'an expired QR looks identical to a live one');
});

// ── Progress + honest failure ────────────────────────────────────────────────
// Approving the account and signing are each a multi-second round trip into the
// wallet's own UI, and the panel used to show nothing at all until a final
// toast. Worse, the catch reported EVERY failure as "Wallet linking was
// cancelled" — so a server 500 blamed the user for changing their mind, and hid
// the real reason.

const STEP_KEYS = ['dd.w_step_approve', 'dd.w_step_nonce', 'dd.w_step_sign',
  'dd.w_step_verify', 'dd.w_step_done', 'dd.w_step_failed', 'dd.w_step_declined',
  'dd.w_install_x', 'dd.t_wallet_linked', 'dd.t_wallet_link_failed',
  'dd.t_wallet_link_error', 'dd.t_wallet_unlinked', 'dd.t_wallet_unlink_failed'];

test('a declined signature is distinguished from a failure', () => {
  // EIP-1193 4001 is the user declining. Anything else is us failing.
  assert.match(dash, /err\.code === 4001 \|\| err\.code === 'ACTION_REJECTED'/,
    'user rejection is not distinguished from an error');
  assert.match(dash, /dd\.w_step_declined/, 'a decline is not reported as a decline');
  assert.match(dash, /dd\.t_wallet_link_error/,
    'a real error must surface its reason, not be reported as a cancellation');
});

test('a failure says nothing was changed', () => {
  // After a failed link the user must know their account was not half-modified.
  const e = i18n.STRINGS['dd.w_step_failed'];
  assert.match(e.en, /nothing was changed/i);
  const declined = i18n.STRINGS['dd.w_step_declined'];
  assert.match(declined.en, /nothing was linked/i);
});

test('each slow step reports itself while it waits', () => {
  for (const k of ['dd.w_step_approve', 'dd.w_step_sign', 'dd.w_step_verify']) {
    assert.match(dash, new RegExp(k.replace('.', '\\.')), `${k} is never shown`);
  }
  assert.match(dash, /id="walletStep"/, 'there is nowhere to show progress');
  assert.match(dash, /aria-live="polite"/, 'progress is invisible to a screen reader');
});

test('the signing step still says it is NOT a transaction', () => {
  // The moment the wallet pops up is exactly when a user needs telling.
  const e = i18n.STRINGS['dd.w_step_sign'];
  for (const c of codes) assert.match(String(e[c]), /\{wallet\}/, `dd.w_step_sign:${c} lost its slot`);
  assert.match(e.en, /not a transaction/i);
});

test('the button is disabled while the flow is in flight', () => {
  assert.match(dash, /if \(link\) link\.disabled = true;/, 'double-click starts two flows');
  assert.match(dash, /finally \{\s*\n\s*if \(link\) link\.disabled = false;/,
    'a thrown error would leave the button permanently disabled');
});

test('desktop without a wallet gets install options, not just a QR', () => {
  assert.match(dash, /const INSTALL = \[/);
  assert.match(dash, /const installBlock = \(w\.present \|\| onMobile\) \? '' :/,
    'install links must not show when a wallet is already present, nor on mobile '
      + '(where the deep-link picker is the right affordance)');
  assert.match(css, /\.wl-grid \{/, 'the picker grid is not styled outside wallet-link.html');
});

test('every progress and install string exists in all 14 languages', () => {
  for (const k of STEP_KEYS) {
    const e = i18n.STRINGS[k];
    assert.ok(e, `${k} missing from the dictionary`);
    for (const c of codes) assert.ok(String(e[c] || '').trim().length, `${k} is missing ${c}`);
  }
});

test('a failed attempt does not repaint its own explanation away', () => {
  // Caught in the browser, not by these tests: the handler ended with an
  // unconditional drawWalletLink(), which re-rendered the panel and wiped the
  // step message the instant it was written. Both failure paths showed an
  // empty explanation. Re-render only when something actually changed.
  assert.match(dash, /if \(changed\) drawWalletLink\(\);/,
    'an unconditional re-render wipes the failure explanation');
  assert.match(dash, /changed = true;/, 'a successful link must still refresh the panel');
  assert.match(dash, /changed = !!v\?\.ok;/, 'unlink must refresh only when it succeeded');
});
