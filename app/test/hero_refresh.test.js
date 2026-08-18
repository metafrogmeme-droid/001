'use strict';
/**
 * Hero — the headline now leads with the one claim the product can prove.
 *
 * It used to read "an AI trading engine you can talk to — and trust", under a
 * tagline of "Intelligent · Explainable · Guarded". Three adjectives carry no
 * information, and "trust" is the one thing a trading bot cannot ask for; every
 * competitor asks for it too, and a screenshot asks for it just as loudly.
 *
 * What replaced it is falsifiable: each call is hashed at decision time, and
 * the fields named in the copy are exactly the fields `app/lib/callseal.js`
 * puts in the sealed payload. That correspondence is pinned below, because the
 * failure mode of good copy is drift — the payload gains or loses a field and
 * the homepage keeps describing the old one. A claim that was true when it
 * shipped and is false a quarter later is worse than one that was never made.
 *
 * §4: capability copy, no dollar claims, no returns language.
 */
const test = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const path = require('path');

const i18n = fs.readFileSync(path.join(__dirname, '..', 'public', 'js', 'i18n.js'), 'utf8');
const index = fs.readFileSync(path.join(__dirname, '..', 'public', 'index.html'), 'utf8');
const callseal = fs.readFileSync(path.join(__dirname, '..', 'lib', 'callseal.js'), 'utf8');

/**
 * The payload builder `sealCall` ACTUALLY uses, found by reading sealCall
 * rather than by naming a function.
 *
 * Both checks below used to slice `canonicalPayload` by name. That was right
 * while it was the only builder, and became a stale guard the moment new seals
 * moved to `canonicalSignalPayload` (kind v4, which seals the reasoning too):
 * the tests would have gone on validating the homepage against a payload the
 * platform no longer produces, passing green the whole time. `v` in callseal.js
 * is a KIND discriminator — 1 signal, 2 arena_trade, 3 duel_pick, 4 signal_call
 * — so "the newest one" is not something a name can be trusted to track.
 *
 * v1 is deliberately still in the file and must stay byte-identical: it is the
 * string every historical seal was computed over. It is simply no longer what
 * the hero is describing.
 */
function activePayloadSource() {
  const seal = callseal.slice(callseal.indexOf('function sealCall'));
  const m = seal.match(/const seal_payload = (\w+)\(/);
  assert.ok(m, 'sealCall no longer builds its payload through a named function');
  const fn = 'function ' + m[1] + '(';
  const at = callseal.indexOf(fn);
  assert.ok(at > 0, `sealCall calls ${m[1]}(), which is not defined in callseal.js`);
  const end = callseal.indexOf('\n}', at);
  return callseal.slice(at, end);
}

test('the headline is the mechanism, not an adjective', () => {
  assert.match(i18n, /'hero\.h1':[^\n]*hashed/);
  assert.match(index, /Every call is hashed/);
  assert.doesNotMatch(index, /Intelligent · Explainable · Guarded/,
    'three adjectives, zero information — and it sat between the claim and the proof of it');
});

test('every decision field the hero names is really in the sealed payload', () => {
  // Derived from the CALLER so the copy cannot drift away from the code: if a
  // field leaves canonicalPayload, the sentence describing it fails here
  // rather than quietly becoming false on the homepage.
  const payload = activePayloadSource();
  for (const [word, field] of [['symbol', 'symbol'], ['direction', 'direction'],
                               ['entry', 'entry_price'], ['stop', 'stop_loss'],
                               ['target', 'take_profit'], ['confidence', 'confidence'],
                               ['pattern', 'pattern']]) {
    assert.ok(payload.includes(field + ':'),
      `the hero claims "${word}" is sealed, but canonicalPayload has no ${field}`);
    assert.match(index, new RegExp(word, 'i'),
      `${field} is sealed but the hero stopped saying so`);
  }
});

test('the hero does not claim an outcome is sealed with the call', () => {
  // The whole point is that the outcome attaches AFTERWARDS. If it ever entered
  // the decision payload the claim would collapse, so both halves are pinned.
  const payload = activePayloadSource();
  for (const banned of ['pnl', 'exit_price', 'outcome', 'result']) {
    assert.ok(!payload.includes(banned + ':'),
      `canonicalPayload gained ${banned} — "sealed before the outcome" is no longer true`);
  }
  assert.match(index, /outcome attaches to that sealed record afterwards/i);
});

test('the hero says whose keys a live trade runs on', () => {
  assert.match(index, /your own exchange keys/i);
  assert.doesNotMatch(index, /operator approval/i);
});

test('hero.h1 and hero.body stay translated across all locales', () => {
  for (const key of ["'hero.h1'", "'hero.body'"]) {
    const start = i18n.indexOf(key);
    assert.ok(start > 0, `${key} missing`);
    const block = i18n.slice(start, i18n.indexOf('\n', start));
    for (const loc of ['en:', 'hi:', 'it:', 'es:', 'zh:', 'pt:', 'fr:', 'de:',
                       'nl:', 'ja:', 'ko:', 'ru:', 'tr:', 'ar:']) {
      assert.ok(block.includes(loc), `${key} lost ${loc}`);
    }
  }
  assert.match(index, /i18n\.js\?v=\d+/);
});
