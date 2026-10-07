'use strict';
/**
 * The Engine view's regime panel reads the regime the context row reads.
 *
 * The producer seeds `regime: {label: 'NEUTRAL', gate: 0}` and writes `gate`
 * (the BTC anchor price) only when it read BTC, so a zero anchor means the
 * label is the constructor's default. The context row already said so; the
 * Engine view's regime panel printed the seed as a measured NEUTRAL beside
 * "BTC anchor $0", on every cycle summary push. Both read
 * `ContextChipsModel.regimeReading` now.
 */
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const CX = require('../public/js/context-chips-model.js');
const { codeOnly } = require('./helpers/code_only');

const FIXED = () => ({ cls: 'chip--up', text: 'LIVE' });

test('the seed is not a reading', () => {
  const rr = CX.regimeReading({ regime: { label: 'NEUTRAL', gate: 0 } });
  assert.equal(rr.read, false);
  assert.equal(rr.whyKey, 'dd.ctx_w_regime_default');
  assert.equal(rr.anchor, null);
});

test('a read NEUTRAL is a reading', () => {
  const rr = CX.regimeReading({ regime: { label: 'NEUTRAL', gate: 63000 } });
  assert.deepEqual(rr, { read: true, vKey: 'dd.ctx_reg_neutral', cls: '', label: 'NEUTRAL', anchor: 63000 });
});

for (const [name, scan, why] of [
  ['no regime', {}, 'dd.ctx_w_regime'],
  ['no label', { regime: { gate: 63000 } }, 'dd.ctx_w_regime'],
  ['an unknown word', { regime: { label: 'SIDEWAYS', gate: 63000 } }, 'dd.ctx_w_regime_word'],
  ['an empty-string anchor', { regime: { label: 'BULLISH', gate: '' } }, 'dd.ctx_w_regime_default'],
  ['a boolean anchor', { regime: { label: 'BULLISH', gate: true } }, 'dd.ctx_w_regime_default'],
  ['a negative anchor', { regime: { label: 'BULLISH', gate: -1 } }, 'dd.ctx_w_regime_default'],
  ['no scan', null, 'dd.ctx_w_regime'],
]) {
  test(`${name} is unread with its own reason`, () => {
    const rr = CX.regimeReading(scan);
    assert.equal(rr.read, false);
    assert.equal(rr.whyKey, why);
  });
}

test('the context row agrees with the reading on every case', () => {
  const cases = [
    { regime: { label: 'NEUTRAL', gate: 0 } },
    { regime: { label: 'BULLISH', gate: 63000 } },
    { regime: { label: 'bearish', gate: 63000 } },
    { regime: { label: 'SIDEWAYS', gate: 63000 } },
    {},
  ];
  for (const scan of cases) {
    const rr = CX.regimeReading(scan);
    const out = CX.contextChips({ ...scan, received_at: new Date(0).toISOString() }, 1000, FIXED);
    const chip = out.chips.find((c) => c.subject === 'regime');
    const miss = out.unread.find((u) => u.subject === 'regime');
    if (rr.read) {
      assert.ok(chip && !miss, JSON.stringify(scan));
      assert.equal(chip.vKey, rr.vKey);
      assert.equal(chip.cls, rr.cls);
    } else {
      assert.ok(miss && !chip, JSON.stringify(scan));
      assert.equal(miss.whyKey, rr.whyKey);
    }
  }
});

// ── the Engine view's panel, driven ──────────────────────────────────────

const DASH = fs.readFileSync(path.join(__dirname, '..', 'public', 'js', 'dashboard.js'), 'utf8');
const START = "renderPanel(C('eregime'), ";
const END = ', { empty: OFFLINE });';

function panelCallback() {
  const code = codeOnly(DASH);
  const i = code.indexOf(START);
  assert.ok(i >= 0, 'the regime panel is gone');
  const j = code.indexOf(END, i);
  assert.ok(j > i, 'the regime panel has no end marker');
  return code.slice(i + START.length, j);
}

function render(scan) {
  const ctx = {
    scan, self: { ContextChipsModel: CX },
    T: (k, en) => en, esc: (s) => String(s).replace(/</g, '&lt;'),
    fmtPrice: (n) => `$${n}`, sanitizeBotHtml: (s) => String(s),
  };
  vm.createContext(ctx);
  const fn = vm.runInContext(`(${panelCallback()})`, ctx);
  return fn();
}

test('the panel says the seed was not read, and prints no $0 anchor', async () => {
  const html = await render({ regime: { label: 'NEUTRAL', gate: 0 }, key_call: 'No scan data available.' });
  assert.ok(html.includes('NOT REPORTED'), html);
  assert.ok(html.includes('BTC was not read'), html);
  assert.ok(!html.includes('$0'), 'an unread anchor printed as a price');
  assert.ok(!/◆ NEUTRAL/.test(html), 'the seed printed as a measured NEUTRAL');
});

test('the panel prints a read regime and its anchor', async () => {
  const html = await render({ regime: { label: 'BULLISH', gate: 63000 } });
  assert.ok(html.includes('▲ BULLISH'), html);
  assert.ok(html.includes('chip--up'), html);
  assert.ok(html.includes('$63000'), html);
});

test('a word the page does not know is named, not coloured', async () => {
  const html = await render({ regime: { label: 'SIDEWAYS', gate: 63000 } });
  assert.ok(html.includes('regime word this page does not know'), html);
  assert.ok(!html.includes('chip--up') && !html.includes('chip--down'), html);
});

test('no scan is the empty state', async () => {
  assert.equal(await render(null), null);
  assert.equal(await render(undefined), null);
});

test('a pushed scan with no regime says so, beside the key call it carried', async () => {
  const html = await render({ timestamp: '2026-10-06T12:00:00Z', key_call: 'Range day, wait for the break.' });
  assert.ok(html.includes('NOT REPORTED'), html);
  assert.ok(html.includes('the scan carried no regime'), html);
  assert.ok(html.includes('Range day, wait for the break.'), html);
});

test('every reason the reading can give has a literal T() call in the panel', () => {
  const block = panelCallback();
  for (const k of ['dd.ctx_w_regime', 'dd.ctx_w_regime_default', 'dd.ctx_w_regime_word',
    'dd.ctx_reg_bull', 'dd.ctx_reg_bear', 'dd.ctx_reg_neutral', 'dd.ctx_unread']) {
    assert.ok(block.includes(`T('${k}'`), k);
  }
  assert.ok(!/regime\.gate|reg\.gate/.test(block), 'the panel reads the anchor itself');
});
