'use strict';
// Two asks from one pair of screenshots, and they turn out to be the same
// question asked twice: does this control do what it appears to offer?
//
// 1. THE DECISION-PICTURE MODAL HAD NO SHARE.
//    Closed trades could be shared; the live decision picture could not. The
//    share it gets here deliberately does NOT inherit one property of the
//    closed-trade share: that one publishes a RESULT. This modal shows a
//    PLAN. A target rendered in the same voice as a realized return is the
//    oldest way to mislead with a true number, so the target is labelled as
//    a plan and is omitted entirely when no geometry was passed — a symbol
//    view with no levels has no target to quote.
//
// 2. THE RESEND-VERIFICATION BUTTON STAYED LIVE AFTER REPORTING THAT EMAIL
//    CANNOT BE SENT.
//    The server answers `sent: false` when no SMTP is configured, the page
//    said so, and then left the button enabled — inviting a press that could
//    never succeed, forever. An offer the app cannot honour is worse than no
//    offer.
//
// Both surfaces also stop hardcoding English into dynamic text. The
// screenshot showed a Dutch button label above an English message, because
// the label went through the dictionary and the message did not.

const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

const dash = fs.readFileSync(
  path.join(__dirname, '..', 'public', 'js', 'dashboard.js'), 'utf8');
const index = fs.readFileSync(
  path.join(__dirname, '..', 'public', 'index.html'), 'utf8');
const i18n = require('../public/js/i18n.js');
const codes = i18n.LANGS.map((l) => l.code);

// ── 1. The modal share ──────────────────────────────────────────────────────

test('the modal offers a share control', () => {
  assert.match(dash, /id="symShare"/);
  const i = dash.indexOf('id="symShare"');
  const j = dash.indexOf('id="symStar"');
  assert.ok(j > 0 && i > j, 'it sits with the other modal actions');
});

test('the share is wired, not just rendered', () => {
  // A button with no handler is the defect this file is about, one level up.
  assert.match(dash, /getElementById\('symShare'\)/);
  assert.match(dash, /shareBtn\.onclick/);
});

function shareBlock() {
  const i = dash.indexOf("getElementById('symShare')");
  assert.ok(i > 0);
  return dash.slice(i, i + 1800);
}

test('a target is labelled a plan, never a result', () => {
  const b = shareBlock();
  assert.match(b, /a plan, not a result/,
    'a target in the voice of a realized return is the oldest way to '
    + 'mislead with a true number');
});

test('no geometry means no target is quoted', () => {
  const b = shareBlock();
  // The target text is built only from a levelMove that returned something.
  assert.match(b, /let target = ''/);
  assert.match(b, /if \(mv\) target =/);
  // ...and the "plan, not a result" caveat only appears when a target does.
  assert.match(b, /target \? ' \(a plan, not a result\)\.' : '\.'/);
});

test('the shared target uses the leverage-aware move', () => {
  const b = shareBlock();
  assert.match(b, /levelMove\(geo\.e, geo\.tp, geo\.d, geo\.l\)/,
    'sharing the raw price move would understate it by the leverage multiple '
    + '— the very complaint that prompted this');
});

test('the share carries no dollar amount', () => {
  // §4: percent and ratio only on anything that leaves the account.
  const b = shareBlock();
  assert.doesNotMatch(b, /\$\{?\s*(equity|balance|size_usd|pnl_usd|notional)/,
    'account size must never ride out on a share');
});

test('positions pass their leverage into the modal, signals do not', () => {
  // Positions know how they were sized. Signals do not — nobody has sized
  // them yet — and inventing a 1x for them would be a fabricated number.
  const posGeo = /data-geo='\$\{esc\(JSON\.stringify\(\{ e: p\.entry_price, sl: p\.stop_loss, tp: p\.take_profit, d: p\.direction, l: p\.leverage \}\)\)\}'/g;
  assert.strictEqual((dash.match(posGeo) || []).length, 2,
    'both position rows carry leverage');
  const sigGeo = /\{ e: s\.entry_price, sl: s\.stop_loss, tp: s\.take_profit, d: s\.direction \}/;
  assert.match(dash, sigGeo, 'the signal row carries no leverage, by design');
});

test('the chart is handed the leverage it was given', () => {
  assert.match(dash, /leverage: geo\.l/,
    'a value collected and then dropped before the renderer fixes nothing');
});

// ── 2. The dead resend button ───────────────────────────────────────────────

function resendFn() {
  const i = index.indexOf('async function resendVerification');
  assert.ok(i > 0);
  return index.slice(i, i + 1400);
}

test('the resend button is disabled once email is known unconfigured', () => {
  const f = resendFn();
  assert.match(f, /btn\.disabled\s*=\s*true/);
  assert.match(f, /aria-disabled/,
    'assistive tech must learn it too, not only sighted users');
});

test('the unconfigured message says who has to act', () => {
  const f = resendFn();
  assert.match(f, /acc\.verify_no_smtp/);
  const en = i18n.STRINGS['acc.verify_no_smtp'].en;
  assert.match(en, /operator/i);
  assert.match(en, /nothing you press here will change that/i,
    'the user should not keep trying');
});

test('a successful send leaves the button alone', () => {
  // Disabling on success would strand anyone whose mail genuinely got lost.
  const f = resendFn();
  const sentAt = f.indexOf('acc.verify_sent');
  const disableAt = f.indexOf('btn.disabled');
  assert.ok(sentAt > 0 && disableAt > sentAt,
    'the disable belongs to the no-SMTP branch only');
});

test('the dynamic verification messages are translated', () => {
  // The screenshot showed a Dutch label above an English message because the
  // label used the dictionary and the message was hardcoded.
  for (const k of ['acc.verify_sent', 'acc.verify_done', 'acc.verify_no_smtp']) {
    const entry = i18n.STRINGS[k];
    assert.ok(entry, `missing key: ${k}`);
    for (const c of codes) {
      assert.ok(entry[c] && String(entry[c]).trim(), `${k}:${c}`);
    }
  }
});

test('the messages go through the translate helper', () => {
  const f = resendFn();
  for (const k of ['acc.verify_sent', 'acc.verify_done', 'acc.verify_no_smtp']) {
    assert.match(f, new RegExp(`T\\('${k.replace('.', '\\.')}'`),
      `${k} must be read through T(), not hardcoded`);
  }
});
