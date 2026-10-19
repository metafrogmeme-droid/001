'use strict';
/**
 * The landing page's replay theater fetches the candles behind ONE recorded
 * trade. The history stores the engine's symbol, `CRV/USDT:USDT`; the theater
 * used to strip its punctuation and ask the relay for `CRVUSDTUSDT`, which
 * Bitget does not know, so the relay answered 502 and every perp replay said
 * its price path was unavailable (seen live on 2026-10-19). The symbol rule is
 * exposed before the page check, so it can be driven here without a stage.
 */
const test = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');
const { codeOnly } = require('./helpers/code_only');

const SRC = fs.readFileSync(path.join(__dirname, '..', 'public', 'js', 'theater.js'), 'utf8');

function load() {
  const window = {};
  vm.runInNewContext(SRC, { window, document: { getElementById: () => null } });
  return window.RCTheater;
}

test('a recorded trade asks the candles route for the feed contract, never a doubled quote leg', () => {
  const T = load();
  assert.ok(T && typeof T.contractSym === 'function', 'theater.js exposes its symbol rule');
  assert.equal(T.contractSym('CRV/USDT:USDT'), 'CRVUSDT');
  assert.equal(T.contractSym('NATGAS/USDT:USDT'), 'NATGASUSDT');
  assert.equal(T.contractSym('BTCUSDT'), 'BTCUSDT', 'an already-contract symbol gains no second leg');
  assert.equal(T.contractSym('eth/usdt'), 'ETHUSDT');
  assert.equal(T.contractSym(''), '');
  assert.equal(T.contractSym(null), '');
  assert.equal(T.contractSym('/USDT'), '', 'nothing usable is an empty answer, not "USDT"');
});

test('the candle fetch goes through that rule', () => {
  assert.match(codeOnly(SRC), /const sym = contractSym\(trade\.symbol\);/);
  assert.doesNotMatch(codeOnly(SRC), /replace\(\/\[\^A-Z0-9\]\/gi, ''\)\.toUpperCase\(\)/, 'the punctuation strip is back');
});
