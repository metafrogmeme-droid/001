'use strict';
// The live-trading controls are the only place on the site where reading the
// wrong sentence costs money.
//
// The emergency-stop confirm() asks whether to disable live, pause, and CLOSE
// YOUR OPEN POSITIONS. A user who cannot read the question cannot answer it —
// and the two possible mistakes are not symmetric: cancelling when you meant to
// stop leaves live positions running, and confirming when you meant to cancel
// closes them at market.
//
// The same panel holds the live/paper state chip a user reads to know whether
// real money is at risk, and the paper-mode banner that says stops are
// simulated and no exchange order exists.

const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

const i18n = require('../public/js/i18n.js');
const codes = i18n.LANGS.map((l) => l.code);
const dash = fs.readFileSync(path.join(__dirname, '..', 'public', 'js', 'dashboard.js'), 'utf8');

const CTL_KEYS = [
  'ctl.live_on', 'ctl.pending_approval', 'ctl.paper', 'ctl.applying',
  'ctl.live_trading', 'ctl.needs_approval', 'ctl.pause', 'ctl.max_margin',
  'ctl.apply', 'ctl.emergency_stop', 'ctl.stop_note', 'ctl.stop_confirm',
  'ctl.stop_queued', 'ctl.stop_failed', 'ctl.unavailable', 'ctl.paper_stops',
];

test('every control string exists in all 14 languages', () => {
  for (const k of CTL_KEYS) {
    const e = i18n.STRINGS[k];
    assert.ok(e, `${k} missing from the dictionary`);
    for (const c of codes) {
      assert.ok(String(e[c] || '').trim().length, `${k} is missing ${c}`);
    }
  }
});

test('the destructive confirm is translated, not just the button', () => {
  // Translating "Emergency stop" while leaving the confirm() in English is the
  // worst of both: the control looks localized and the question that actually
  // decides the outcome is not.
  assert.match(dash, /confirm\(T\('ctl\.stop_confirm'/,
    'the emergency-stop confirm() is still a bare English string');
  assert.match(dash, /T\('ctl\.emergency_stop'/);
});

test('the confirm keeps BOTH consequences in every language', () => {
  // "disable live" alone is not the whole story — it also closes open
  // positions. A translation that drops the closing clause understates what
  // the user is agreeing to.
  const e = i18n.STRINGS['ctl.stop_confirm'];
  const closes = {
    en: /close/i, hi: /बंद/, it: /chiuder/i, es: /cerrar/i, zh: /平掉|平倉/,
    pt: /fechar/i, fr: /clôtur/i, de: /schließen/i, nl: /sluiten/i, ja: /決済/,
    ko: /청산/, ru: /закрыть/i, tr: /kapat/i, ar: /إغلاق/,
  };
  for (const c of codes) {
    assert.match(String(e[c]), closes[c],
      `ctl.stop_confirm:${c} does not say the positions get closed`);
  }
});

test('the paper-mode stop banner keeps its honesty clause', () => {
  // §4: a simulated stop must never read as a real exchange order. Every
  // language has to keep the "no exchange order" / simulated distinction.
  const e = i18n.STRINGS['ctl.paper_stops'];
  assert.match(e.en, /no exchange order/i, 'the English lost its honesty clause');
  for (const c of codes) {
    assert.ok(String(e[c]).length > 40,
      `ctl.paper_stops:${c} looks truncated — this sentence carries the sim/real distinction`);
  }
  assert.match(dash, /T\('ctl\.paper_stops'/, 'the banner is still hardcoded English');
});

test('the live / paper state chip is translated', () => {
  // This chip is how a user knows whether real money is at risk.
  for (const k of ['ctl.live_on', 'ctl.paper', 'ctl.pending_approval']) {
    assert.match(dash, new RegExp(`T\\('${k.replace('.', '\\.')}'`), `${k} not wired`);
  }
});

test('queue and failure wording is shared with the venue panel deliberately', () => {
  // Same meaning, one translation — duplicating it would let the two copies
  // drift. This test makes the coupling explicit rather than hidden, so a
  // future edit to venue.queued knows the controls panel depends on it.
  const usesQueued = dash.match(/T\('venue\.queued'/g) || [];
  const usesFailed = dash.match(/T\('venue\.failed'/g) || [];
  assert.ok(usesQueued.length >= 2,
    'both the venue panel and the controls panel should share venue.queued');
  assert.ok(usesFailed.length >= 2,
    'both the venue panel and the controls panel should share venue.failed');
  for (const c of codes) {
    assert.ok(String(i18n.STRINGS['venue.queued'][c] || '').trim().length);
    assert.ok(String(i18n.STRINGS['venue.failed'][c] || '').trim().length);
  }
});

test('no control string is left as a bare English literal', () => {
  // The specific literals this PR replaced. If one comes back, it is a
  // regression, not a new string.
  const gone = [
    "'Controls unavailable.'",
    "'Emergency stop failed.'",
    "'Emergency stop queued — closing positions.'",
    ">Emergency stop</button>",
    ">Apply</button>",
  ];
  for (const g of gone) {
    // Allowed only as the inline English fallback inside a T(...) call.
    const bare = dash.split(g).length - 1;
    const inT = (dash.match(new RegExp("T\\('ctl\\.[a-z_]+', " + g.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'), 'g')) || []).length;
    assert.equal(bare - inT, 0, `${g} still appears as a bare literal`);
  }
});
