'use strict';
// Open ONE engine signal as a paper trade.
//
// The Arena had two ways in — type a ticket, or flip practice-follow on and
// mirror everything from now on. Neither is "I like THAT call". This is that.
//
// Almost every test below is about the same temptation: the flattering version
// of this feature fills you at the price the signal was posted at, keeps the
// signal's targets whatever the market has done since, and lets you open a
// call from this morning as though the engine still stood behind it. All three
// manufacture a trade the user could not actually have taken.

const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

const st = require('../lib/arena_signal_trade');
const arena = require('../lib/arena');
const i18n = require('../public/js/i18n.js');
const codes = i18n.LANGS.map((l) => l.code);
const read = (...p) => fs.readFileSync(path.join(__dirname, '..', ...p), 'utf8');

const NOW = new Date('2026-07-26T12:00:00Z');
const sig = (over) => Object.assign({
  id: 7, symbol: 'BTCUSDT', direction: 'LONG',
  entry_price: 60000, stop_loss: 58000, take_profit: 66000,
  created_at: new Date(NOW.getTime() - 10 * 60000),
}, over || {});
const plan = (over, ctx) => st.planSignalOpen(Object.assign({
  signal: sig(over), positions: [], balance: 10000,
  margin: 200, leverage: 2, mark: 60600, now: NOW,
}, ctx || {}));

test('the fill is the LIVE mark, never the price the call was posted at', () => {
  // The whole feature turns dishonest here. Filling at 60000 when the market
  // is at 60600 hands the user a +1% head start they never had.
  const r = plan();
  assert.ok(r.ok, r.error);
  assert.equal(r.data.entry, 60600, 'filled at the signal price, not the live mark');
  assert.equal(r.data.signal_entry, 60000, 'the call’s own price must still be reported');
});

test('the drift since the call is measured and reported', () => {
  assert.equal(plan().data.drift_pct, 1);
  assert.equal(plan({}, { mark: 59400 }).data.drift_pct, -1);
  // Beyond the warn threshold the UI has to say something louder.
  assert.equal(plan().data.drift_warn, false);
  assert.equal(plan({}, { mark: 61200 }).data.drift_warn, true, '+2% is a materially different trade');
});

test('a signal with no entry price of its own reports no drift, not zero drift', () => {
  // Zero would read as "the market has not moved", which is a claim. Null is
  // the truth: there is nothing to compare against.
  const r = plan({ entry_price: 0 });
  assert.equal(r.data.drift_pct, null);
  assert.equal(r.data.drift_warn, false);
});

test('exits the market has already passed are DROPPED, not carried', () => {
  // A long called at 60000 with a 66000 target, taken when the market is at
  // 67000: that target is behind us. Carrying it opens a position that
  // instantly "hits" its own take-profit — a fabricated win.
  const r = plan({}, { mark: 67000 });
  assert.ok(r.ok, r.error);
  assert.equal(r.data.tp, null, 'a passed take-profit was carried forward');
  assert.equal(r.data.sl, 58000, 'a still-valid stop was discarded along with it');
  assert.deepEqual(r.data.dropped, ['tp']);
});

test('a passed stop does not take a still-valid target down with it', () => {
  // Long, mark below the signal's stop: the stop is behind us, the target
  // is not. Validating both in one call would reject the pair together.
  const r = plan({}, { mark: 57000 });
  assert.ok(r.ok, r.error);
  assert.equal(r.data.sl, null);
  assert.equal(r.data.tp, 66000, 'a valid take-profit was dropped with the stop');
  assert.deepEqual(r.data.dropped, ['sl']);
});

test('a short’s exits are checked on the correct side', () => {
  const r = st.planSignalOpen({
    signal: sig({ direction: 'SHORT', entry_price: 60000, take_profit: 55000, stop_loss: 62000 }),
    positions: [], balance: 10000, margin: 200, leverage: 2, mark: 59000, now: NOW });
  assert.ok(r.ok, r.error);
  assert.equal(r.data.tp, 55000, 'a short’s target sits BELOW the fill and is valid');
  assert.equal(r.data.sl, 62000, 'a short’s stop sits ABOVE the fill and is valid');
  assert.deepEqual(r.data.dropped, []);
});

test('a stale call is refused — the engine is not standing behind it any more', () => {
  const old = plan({ created_at: new Date(NOW.getTime() - st.MAX_SIGNAL_AGE_MS - 60000) });
  assert.equal(old.ok, false);
  assert.equal(old.code, 'stale');
  // The boundary itself is still open — refusing at exactly the limit would
  // make the cutoff depend on clock jitter.
  const edge = plan({ created_at: new Date(NOW.getTime() - st.MAX_SIGNAL_AGE_MS) });
  assert.equal(edge.ok, true, 'a signal exactly at the age limit must still open');
});

test('no live mark means no trade — never a fill at a guessed price', () => {
  for (const m of [0, null, undefined, -5, NaN, 'abc']) {
    const r = plan({}, { mark: m });
    assert.equal(r.ok, false, `mark ${String(m)} produced a fill`);
    assert.equal(r.code, 'no_mark');
  }
});

test('signal opens obey exactly the limits a manual ticket obeys', () => {
  // Two validators that can drift apart is how "the signal path lets you use
  // 50× " happens. There is one, and this pins that it is the ticket's.
  assert.equal(plan({}, { margin: arena.MIN_MARGIN - 1 }).code, 'limits');
  assert.equal(plan({}, { leverage: arena.MAX_LEVERAGE + 1 }).code, 'limits');
  assert.equal(plan({}, { balance: 10 }).code, 'limits', 'opened beyond the balance');
  const full = Array.from({ length: arena.MAX_OPEN }, (_, i) => ({ symbol: 'X' + i + 'USDT' }));
  assert.equal(plan({}, { positions: full }).code, 'limits', 'opened past the slot limit');
});

test('a symbol already open is refused before the limits are even consulted', () => {
  const r = plan({}, { positions: [{ symbol: 'btcusdt' }] });
  assert.equal(r.ok, false);
  assert.equal(r.code, 'already_open', 'case-insensitive symbol match failed');
});

test('a signal with no tradeable direction is refused', () => {
  for (const d of ['', 'FLAT', null, 'hold']) {
    assert.equal(plan({ direction: d }).code, 'direction', `direction ${String(d)} was accepted`);
  }
  // ...but a lowercase real direction is fine.
  assert.equal(plan({ direction: 'long' }).ok, true);
});

test('a missing signal is a refusal, not a crash', () => {
  assert.equal(st.planSignalOpen({}).code, 'no_signal');
  assert.equal(st.planSignalOpen({ signal: null, mark: 1 }).code, 'no_signal');
});

// ── the picker list ────────────────────────────────────────────────────────

test('the picker marks what cannot be opened, and why', () => {
  const rows = st.decorateForPicker([
    sig({ id: 1 }),
    sig({ id: 2, symbol: 'ETHUSDT' }),
    sig({ id: 3, symbol: 'SOLUSDT', created_at: new Date(NOW.getTime() - st.MAX_SIGNAL_AGE_MS - 1) }),
    sig({ id: 4, symbol: 'XRPUSDT', direction: '' }),
  ], { positions: [{ symbol: 'ETHUSDT' }], marks: { BTCUSDT: { price: 60600 } }, now: NOW });
  assert.deepEqual(rows.map((r) => [r.id, r.tradeable, r.blocked_reason]), [
    [1, true, null], [2, false, 'already_open'], [3, false, 'stale'], [4, false, 'direction'],
  ]);
  assert.equal(rows[0].drift_pct, 1);
  assert.equal(rows[1].drift_pct, null, 'no mark must give no drift, not a made-up one');
});

test('the picker survives a missing mark map — the list must still render', () => {
  // The mark read is bounded and allowed to fail; the list is not allowed to.
  const rows = st.decorateForPicker([sig()], { now: NOW });
  assert.equal(rows.length, 1);
  assert.equal(rows[0].mark, null);
  assert.equal(rows[0].tradeable, true, 'a slow ticker read must not hide a good call');
});

test('the picker never emits an amount — §4', () => {
  const rows = st.decorateForPicker([sig()], { marks: { BTCUSDT: { price: 60600 } }, now: NOW });
  const keys = Object.keys(rows[0]);
  for (const banned of ['margin', 'balance', 'equity', 'pnl', 'usd', 'vusdt']) {
    assert.ok(!keys.some((k) => k.toLowerCase().includes(banned)),
      `the signal picker exposes ${banned}`);
  }
});

// ── the route and the shim ─────────────────────────────────────────────────

const routeSrc = read('routes', 'arena.js');
const db = read('db.js');

test('the route fills from the live ticker, not the bounded display read', () => {
  // getTickersWithin() is fine for a list; a FILL may never be priced off a
  // stale mark, which is why the open path uses getTickers() and 503s.
  const openSig = routeSrc.slice(routeSrc.indexOf("router.post('/open-signal'"));
  const body = openSig.slice(0, openSig.indexOf("router.post('/close'"));
  assert.match(body, /await getTickers\(\)/);
  assert.doesNotMatch(body, /getTickersWithin/,
    'a signal open must not be priced off a deliberately stale mark');
});

test('the position is recorded as coming from a signal, not as manual', () => {
  // (window widened past 4000: the agent-attribution block sits between the
  // season check and the INSERT now)
  const openSig = routeSrc.slice(routeSrc.indexOf("router.post('/open-signal'"));
  assert.match(openSig.slice(0, 7000), /d\.leverage, 'signal'/,
    'a signal-opened position must be attributable to the signal');
  assert.match(openSig.slice(0, 7000), /sig\.signal_key \|\| null, agentSlug/,
    'and it records WHICH signal, plus any verified agent attribution');
});

test('a live season constrains signal opens exactly as it constrains manual ones', () => {
  const openSig = routeSrc.slice(routeSrc.indexOf("router.post('/open-signal'"));
  assert.match(openSig.slice(0, 4000), /checkSeasonRules\(seasons\[0\], d\)/,
    'the engine’s name on a call does not exempt it from the season rules');
});

test('the in-memory shim resolves WHERE id = ? instead of treating it as a LIMIT', () => {
  // The catch-all `FROM SIGNALS` branch ignores WHERE and reads the LAST PARAM
  // as a limit. Without a dedicated branch, asking for signal 3 returned the
  // three newest signals and the route opened rows[0] — a DIFFERENT call than
  // the user clicked, in a way no test that used the shim could ever see.
  assert.match(db, /FROM SIGNALS'\) && cmd\.includes\('WHERE ID = \?'\)/);
  assert.match(db, /this\.signals\.filter\(s => Number\(s\.id\) === id\)/);
  // And the branch has to come BEFORE the catch-all, or it never runs.
  assert.ok(db.indexOf("cmd.includes('WHERE ID = ?')") < db.lastIndexOf("cmd.includes('FROM SIGNALS')"));
});

test('the shim honours an inline LIMIT rather than guessing from params', () => {
  // `LIMIT 12` with no bound parameters made params[params.length-1] undefined,
  // parseInt gave NaN, and the fallback silently served 50 rows where MySQL
  // would serve 12.
  assert.match(db, /const inline = cmd\.match\(\/LIMIT\\s\+\(\\d\+\)\/\);/);
});

// ── the panel ──────────────────────────────────────────────────────────────

const page = read('public', 'arena.html');

test('the age helper does not collide with the tape’s ago(iso)', () => {
  // Both are hoisted `function` declarations in the same IIFE, so the later
  // one silently wins. Under the shared name `ago`, this panel's DURATION was
  // parsed as a TIMESTAMP and every row said "20660d ago" — in English, in
  // every language, because the tape helper never went through T().
  const decls = page.match(/function ago\(/g) || [];
  assert.equal(decls.length, 1, 'two hoisted ago() declarations — the later shadows the earlier');
  assert.match(page, /function sigAgo\(ms\)/);
  assert.match(page, /sigAgo\(s\.age_ms\)/);
});

test('the drift is shown BEFORE the button, not explained after the fill', () => {
  const row = page.slice(page.indexOf('function sigRowHtml'), page.indexOf('async function loadSignals'));
  assert.ok(row.indexOf('sig_drift') < row.indexOf('data-sig='),
    'the user must see how far the market moved before they can click');
});

test('a failed signals read says "unknown", never renders as an empty stream', () => {
  // Same doctrine as the account panel: an empty list is a claim about the
  // engine, and we are not entitled to make it when we could not ask.
  assert.match(page, /arena\.sig_unknown/);
  assert.match(i18n.STRINGS['arena.sig_unknown'].en, /unknown, not empty/i);
});

test('a dropped exit is reported to the user, never silently swallowed', () => {
  // Discovering that your position has no stop is not something a user should
  // find out from the positions table.
  for (const k of ['arena.sig_dropped_tp', 'arena.sig_dropped_sl', 'arena.sig_dropped_both']) {
    assert.ok(i18n.STRINGS[k], `${k} missing`);
    assert.match(page, new RegExp(k.replace('.', '\\.')));
  }
});

test('every string on the signals panel exists in all 14 languages', () => {
  for (const k of ['arena.p_signals', 'arena.sig_body', 'arena.b_sig_open', 'arena.e_sig',
    'arena.sig_unknown', 'arena.sig_drift', 'arena.sig_drift_t', 'arena.sig_conf',
    'arena.sig_now', 'arena.sig_min', 'arena.sig_hr', 'arena.sig_b_stale',
    'arena.sig_b_open', 'arena.sig_b_dir', 'arena.sig_b_no', 'arena.sig_filled',
    'arena.sig_filled_drift', 'arena.sig_dropped_tp', 'arena.sig_dropped_sl',
    'arena.sig_dropped_both', 'arena.sig_failed', 'arena.th_tp', 'arena.th_sl']) {
    const e = i18n.STRINGS[k];
    assert.ok(e, `${k} missing from the dictionary`);
    for (const c of codes) assert.ok(String(e[c] || '').trim().length, `${k} is missing ${c}`);
  }
});

test('the "filled at the live mark" promise survives translation everywhere', () => {
  // This is the sentence that stops a reader believing they got the signal's
  // price. Matched by each language's own wording — a length check would call
  // the denser CJK translations truncated.
  const e = i18n.STRINGS['arena.sig_body'];
  const live = {
    en: /live mark/i, hi: /लाइव मार्क/, it: /prezzo live/i, es: /precio en vivo/i,
    zh: /即時標記價/, pt: /preço ao vivo/i, fr: /prix live/i, de: /Live-Kurs/,
    nl: /live koers/i, ja: /ライブのマーク価格/, ko: /실시간 마크 가격/,
    ru: /текущей цене/i, tr: /[Cc]anlı fiyat/, ar: /السعر الحي/,
  };
  for (const c of codes) {
    assert.match(String(e[c]), live[c],
      `arena.sig_body:${c} lost the "you are filled at the LIVE mark" promise`);
  }
});

test('the "dropped, not faked" promise survives translation everywhere', () => {
  const e = i18n.STRINGS['arena.sig_body'];
  const dropped = {
    en: /dropped, not faked/i, hi: /नक़ली नहीं/, it: /scartato, non falsificato/i,
    es: /descarta, no se falsea/i, zh: /捨棄，而不是造假/, pt: /descartado, não falsificado/i,
    fr: /abandonné, pas falsifié/i, de: /verworfen, nicht vorgetäuscht/i,
    nl: /weggelaten, niet nagebootst/i, ja: /捨てられ、偽造されません/,
    ko: /꾸며내지 않고 버립니다/, ru: /отбрасывается, а не подделывается/i,
    tr: /uydurulmaz, düşürülür/i, ar: /يُسقَط ولا يُزيَّف/,
  };
  for (const c of codes) {
    assert.match(String(e[c]), dropped[c],
      `arena.sig_body:${c} lost the "dropped, not faked" promise`);
  }
});

// ── one-tap from the dashboard signal stream ───────────────────────────────

test('the route accepts signal_key — the stream’s only public identifier', () => {
  // /api/signals (public) exposes signal_key, not row ids; it is the same
  // identifier /call/<key> verification uses. Both lookups must exist.
  assert.match(routeSrc, /FROM signals WHERE id = \?/);
  assert.match(routeSrc, /FROM signals WHERE signal_key = \?/);
  // Bounded like everywhere else the key is accepted.
  assert.match(routeSrc, /signal_key \|\| ''\)\.slice\(0, 128\)/);
});

test('the dashboard stream has the one-tap Arena button and its handler', () => {
  const dash = read('public', 'js', 'dashboard.js');
  assert.match(dash, /data-parena="\$\{esc\(s\.signal_key\)\}"/,
    'the button must carry the signal_key, not an id the public API lacks');
  // Resolution is read from the outcome LABEL now, not the amount: /api/signals
  // stopped publishing `pnl` (§4 — no dollars on an anonymous payload), and a
  // key that is simply absent would make `s.pnl == null` true for every
  // resolved signal and offer an open on all of them.
  assert.match(dash, /s\.outcome == null && s\.signal_key/,
    'a resolved signal must not offer an open');
  assert.match(dash, /signal_key: abtn\.getAttribute\('data-parena'\)/);
  // The refusals the route encodes translate; free-text errors pass through.
  assert.match(dash, /stale: \['arena\.sig_b_stale'/);
  assert.match(dash, /already_open: \['arena\.sig_b_open'/);
  // And the toast carries the same honesty payload the Arena panel shows.
  for (const k of ['arena.sig_filled', 'arena.sig_filled_drift', 'arena.sig_dropped_both']) {
    assert.ok(dash.includes(k), `the stream toast dropped ${k}`);
  }
});

test('the one-tap strings exist in all 14 languages', () => {
  for (const k of ['dd.b_arena', 'dd.arena_t', 'dd.arena_login', 'dd.arena_view']) {
    const e = i18n.STRINGS[k];
    assert.ok(e, `${k} missing from the dictionary`);
    for (const c of codes) assert.ok(String(e[c] || '').trim().length, `${k} is missing ${c}`);
  }
});

test('every slot survives every translation', () => {
  const slots = {
    'arena.sig_drift': ['p'], 'arena.sig_conf': ['p'], 'arena.sig_min': ['n'],
    'arena.sig_hr': ['n'], 'arena.sig_filled': ['s', 'd', 'e'],
    'arena.sig_filled_drift': ['p', 'm'],
  };
  for (const [k, names] of Object.entries(slots)) {
    for (const c of codes) {
      for (const n of names) {
        assert.ok(String(i18n.STRINGS[k][c]).includes('{' + n + '}'),
          `${k}:${c} dropped the {${n}} slot`);
      }
    }
  }
});
