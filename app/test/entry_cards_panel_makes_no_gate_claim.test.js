'use strict';
/**
 * THE SETUPS PANEL SAID THE RISK GATE WAS WORKING OVER A PUSH THAT RAN NO SCAN.
 *
 * Its empty state was one option on one `renderPanel` call:
 *
 *     { empty: { icon: 'icon-target',
 *                text: 'No qualifying setups in the last scan
 *                       — the gate is doing its job.' } }
 *
 * DRIVEN, rather than scanned, because the claim is what the panel SAYS for a
 * given payload, and the old defect was invisible from the source: `cards =
 * scan?.entry_cards || []` reads an omitted block, an empty list and a wiped
 * list identically, so no reading of that line can tell them apart.
 *
 * The body is a module-level seam (`ecPanelHtml`) for that reason: a renderer
 * reachable only through a renderPanel callback inside a 6k-line function is a
 * renderer no test can run.
 */
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const { codeOnly } = require(path.join(__dirname, 'helpers', 'code_only.js'));

const DASH = path.join(__dirname, '..', 'public', 'js', 'dashboard.js');
const RAW = fs.readFileSync(DASH, 'utf8');
const MODEL = path.join(__dirname, '..', 'public', 'js', 'entry-cards-model.js');

/** The renderer block, executed with the helpers it reads. */
function renderer({ model = require(MODEL), words = {} } = {}) {
  const a = RAW.indexOf('// ── the entry cards: renderer start ─');
  const b = RAW.indexOf('// ── the entry cards: renderer end ─');
  assert.ok(a > 0 && b > a,
    'the entry-cards renderer lost its markers; this harness slices between them');
  const ctx = {
    window: { EntryCardsModel: model },
    // The page's own helpers, in the shapes the renderer reads them.
    esc: (v) => String(v == null ? '' : v)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;'),
    T: (key, en) => (Object.prototype.hasOwnProperty.call(words, key) ? words[key] : en),
    dirChip: (d) => `<span class="chip">${d || '?'}</span>`,
    fmtPrice: (n) => (isFinite(n) ? String(n) : '—'),
    out: null,
  };
  vm.runInNewContext(
    RAW.slice(a, b) + '\nout = { ecPanelHtml: ecPanelHtml, ecAgo: ecAgo, ecWords: ecWords };',
    ctx, { timeout: 5000 });
  return ctx.out;
}

function reading(over) {
  return { results: 40, above_floor: 3, considered: 3, cards: 1,
           no_atr: 2, no_direction: 0, floor: 0.4, shown_max: 8, ...over };
}

const CARD = { symbol: 'BTC', direction: 'LONG', entry: '62970',
               stop_loss: '62750', tp1: '63300', rr: '1.5', trigger: 'RSI 55' };
const AT = '2026-09-26T12:00:00.000Z';
const NOW = Date.parse('2026-09-26T12:30:00.000Z');

// ── the sentence the panel no longer says ─────────────────────────────────

test('no payload makes the panel claim the gate screened anything out', () => {
  const { ecPanelHtml } = renderer();
  const payloads = [
    {},                                                               // no scan
    { entry_cards: [] },                                              // older bot
    { entry_cards: [], entry_cards_read: reading({ results: 0, above_floor: 0 }) },
    { entry_cards: [], entry_cards_read: reading({ above_floor: 0, cards: 0 }) },
    { entry_cards: [], entry_cards_read: reading({ cards: 0, no_atr: 3 }) },
    { entry_cards: [CARD], entry_cards_read: reading({ above_floor: 1, cards: 1,
                                                       no_atr: 0 }), scan_at: AT },
  ];
  for (const scan of payloads) {
    const html = ecPanelHtml(scan, NOW);
    assert.equal(/the gate is doing its job/.test(html), false, JSON.stringify(scan));
    assert.equal(/No qualifying setups/.test(html), false, JSON.stringify(scan));
  }
});

test('a cycle summary that wiped nothing says no scan is on record', () => {
  const { ecPanelHtml } = renderer();
  const html = ecPanelHtml({ circuit_breaker: { equity: 141.22 } }, NOW);
  assert.match(html, /No scan on record yet/);
  assert.match(html, /class="muted"/, 'an absence is muted, never green');
});

test('a failed read is named as one, with both counts', () => {
  const { ecPanelHtml } = renderer();
  const html = ecPanelHtml({ entry_cards: [],
    entry_cards_read: reading({ above_floor: 4, cards: 0, no_atr: 3,
                                no_direction: 1, considered: 4 }) }, NOW);
  assert.match(html, /3 with no readable volatility/);
  assert.match(html, /1 with no readable direction/);
  assert.match(html, /failed read, not a gate/);
});

test('below the floor names the scanner\'s own floor and its value', () => {
  const { ecPanelHtml } = renderer();
  const html = ecPanelHtml({ entry_cards: [],
    entry_cards_read: reading({ above_floor: 0, cards: 0, no_atr: 0,
                                considered: 0 }) }, NOW);
  assert.match(html, /read 40 symbol\(s\)/);
  assert.match(html, /none scored above 0\.4/);
  assert.match(html, /scanner/);
});

// ── the cards, and what the panel says about them ─────────────────────────

test('cards render with the age of the scan they came from', () => {
  const { ecPanelHtml } = renderer();
  const html = ecPanelHtml({ entry_cards: [CARD], scan_at: AT,
    entry_cards_read: reading({ above_floor: 1, cards: 1, no_atr: 0,
                                considered: 1 }) }, NOW);
  assert.match(html, /<b>BTC<\/b>/);
  assert.match(html, /From the scan at/);
  assert.equal(/prices have moved/.test(html), false, 'a fresh scan is not old');
});

test('a stale scan warns, and the warning is a class not a colour literal', () => {
  const { ecPanelHtml } = renderer();
  const M = require(MODEL);
  const html = ecPanelHtml({ entry_cards: [CARD], scan_at: AT,
    entry_cards_read: reading({ above_floor: 1, cards: 1, no_atr: 0,
                                considered: 1 }) },
    Date.parse(AT) + M.STALE_MS + 1000);
  assert.match(html, /class="warn small mt-2"/);
  assert.match(html, /prices have moved since/);
  // The model hands over a DURATION in ms; the page renders it. A raw
  // 10800001 on the card is the model's unit reaching a reader.
  assert.match(html, /3h ago/);
  assert.equal(/1080\d{4} ago/.test(html), false, 'raw milliseconds on the card');
});

test('a card field carrying markup is escaped', () => {
  // The symbol comes off the wire, and a DEXScreener-style name is
  // attacker-controlled text. Driveable for the first time now the renderer is
  // a seam.
  const { ecPanelHtml } = renderer();
  const html = ecPanelHtml({
    entry_cards: [{ ...CARD, symbol: '<img src=x onerror=1>',
                    trigger: '<b>boom</b>' }],
    scan_at: AT,
    entry_cards_read: reading({ above_floor: 1, cards: 1, no_atr: 0,
                                considered: 1 }),
  }, NOW);
  assert.equal(/<img src=x/.test(html), false, 'a symbol reached the page as markup');
  assert.equal(/<b>boom<\/b>/.test(html), false, 'a trigger reached the page as markup');
  assert.match(html, /&lt;img src=x/);
});

test('the duration is rendered by the page, never by the model', () => {
  const { ecAgo } = renderer();
  assert.equal(ecAgo(60 * 1000), '1m');
  assert.equal(ecAgo(89 * 60 * 1000), '89m');
  assert.equal(ecAgo(3 * 3600 * 1000), '3h');
  assert.equal(ecAgo(3 * 86400 * 1000), '3d');
  assert.equal(ecAgo(0), '1m', 'never "0m ago" for a moment that has passed');
  assert.equal(ecAgo(-5), '1m', 'a negative duration never renders as one');
});

test('the footer still says where the gate runs', () => {
  const { ecPanelHtml } = renderer();
  const html = ecPanelHtml({ entry_cards: [CARD], scan_at: AT,
    entry_cards_read: reading({ above_floor: 1, cards: 1, no_atr: 0,
                                considered: 1 }) }, NOW);
  assert.match(html, /Confirmations run through its risk gate/);
});

// ── what the panel must not do ────────────────────────────────────────────

test('a missing model throws rather than saying anything about the market', () => {
  const { ecPanelHtml } = renderer({ model: null });
  assert.throws(() => ecPanelHtml({ entry_cards: [CARD] }, NOW),
    /entry-cards-model\.js did not load/);
});

test('the renderer spells no sentence and no key of its own', () => {
  const s = codeOnly(RAW);
  const a = s.indexOf('function ecPanelHtml(');
  const b = s.indexOf('function paintChartRead(');
  assert.ok(a > 0 && b > a, 'boundary anchors are code, not comments');
  const block = s.slice(a, b);
  // Every key comes from the model's own table, resolved by `ecWords`.
  assert.equal(/T\('dd\./.test(block), false, 'a key spelled here is a second vocabulary');
  assert.equal(/qualifying|gate is doing|No setups/.test(block), false);
});

test('the panel carries no empty-state option any more', () => {
  const s = codeOnly(RAW);
  const i = s.indexOf("renderPanel(C('ecards')");
  assert.ok(i > 0);
  const call = s.slice(i, s.indexOf("renderPanel(C('eshadow')", i));
  assert.equal(/empty:/.test(call), false,
    '"there are no setups" is a sentence the producer states, not an icon');
  assert.match(call, /ecPanelHtml\(scan, Date\.now\(\)\)/);
});

test('every key the model can emit resolves in all fourteen languages', () => {
  const M = require(MODEL);
  const i18n = require(path.join(__dirname, '..', 'public', 'js', 'i18n.js'));
  const LANGS = i18n.LANGS.map((l) => l.code);
  assert.ok(LANGS.length >= 14, `expected fourteen languages, saw ${LANGS.length}`);
  for (const key of M.KEYS) {
    const row = i18n.STRINGS[key];
    assert.ok(row, `${key} is in no dictionary`);
    for (const lang of LANGS) {
      // Read STRINGS directly: `translate()` falls back to English, so a guard
      // that resolves through it cannot see a missing translation.
      assert.ok(typeof row[lang] === 'string' && row[lang].trim(),
        `${key} has no ${lang}`);
    }
  }
});

test('every slot the model fills is present in every translation', () => {
  const M = require(MODEL);
  const i18n = require(path.join(__dirname, '..', 'public', 'js', 'i18n.js'));
  const LANGS = i18n.LANGS.map((l) => l.code);
  for (const name of Object.keys(M.W)) {
    const { key, en } = M.W[name];
    const slots = (en.match(/\{\w+\}/g) || []).sort();
    for (const lang of LANGS) {
      const got = (i18n.STRINGS[key][lang].match(/\{\w+\}/g) || []).sort();
      assert.deepEqual(got, slots,
        `${key} [${lang}] does not carry the same slots as the English`);
    }
  }
});

test('the model is loaded before the bundle that reads it', () => {
  const html = fs.readFileSync(
    path.join(__dirname, '..', 'public', 'dashboard.html'), 'utf8');
  const model = html.indexOf('entry-cards-model.js');
  const dash = html.indexOf('<script src="/js/dashboard.js');
  assert.ok(model > 0, 'the model is not loaded by the page at all');
  assert.ok(model < dash, 'the bundle would throw on a model loaded after it');
});
