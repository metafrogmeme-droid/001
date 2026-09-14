'use strict';
/**
 * Four producers answered "is the bot trading a real account?" and gave three
 * answers. `lib/live_mode.js` is the one reading now, and this guard does two
 * things: DRIVES that reading, and pins that every producer actually reaches
 * it -- which is the one thing a source scan is for (CLAUDE.md, "Writing tests
 * that scan source": a guard being reached at every call site).
 *
 * The scan strips comments first. This file's own docstring quotes the
 * forbidden shape, and so do the comments left beside each producer's call.
 */
const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

const { readLiveMode, modeWord } = require('../lib/live_mode');
const { codeOnly } = require('./helpers/code_only');

const APP = path.join(__dirname, '..');

// ── the reading ────────────────────────────────────────────────────────────

test('a boolean is a reading', () => {
  assert.strictEqual(readLiveMode({ live_mode: true }), true);
  assert.strictEqual(readLiveMode({ live_mode: false }), false);
});

test('anything that is not a boolean is not a reading, and unknown is the safe direction', () => {
  // `!!cb.live_mode` read every one of these as a mode: 'true' and 1 as
  // LIVE, 'PAPER', 0, null and absent as PAPER. None of them is a reading of
  // the field the producer actually sends.
  for (const junk of ['true', 'false', 1, 0, 'LIVE', 'PAPER', null, undefined]) {
    assert.strictEqual(readLiveMode({ live_mode: junk }), null, `live_mode=${JSON.stringify(junk)}`);
  }
  assert.strictEqual(readLiveMode({}), null, 'an absent key is unread');
});

test('an absent or malformed circuit_breaker is unread, never PAPER', () => {
  for (const cb of [null, undefined, 'x', 42, true, []]) {
    // `[]` is an object with no live_mode -- unread. Everything else is not
    // even a payload.
    assert.strictEqual(readLiveMode(cb), null, `cb=${JSON.stringify(cb)}`);
  }
});

test('the word follows the reading and null stays null', () => {
  assert.strictEqual(modeWord(true), 'LIVE');
  assert.strictEqual(modeWord(false), 'PAPER');
  assert.strictEqual(modeWord(null), null);
  assert.strictEqual(modeWord(undefined), null);
  // Truthiness is not a reading. 1 and 'LIVE' are not `true`.
  assert.strictEqual(modeWord(1), null);
  assert.strictEqual(modeWord('LIVE'), null);
});

test('composed, the six failed reads and the real PAPER reading are told apart', () => {
  const unread = [null, {}, { live_mode: undefined }, { live_mode: 'PAPER' }];
  for (const cb of unread) assert.strictEqual(modeWord(readLiveMode(cb)), null);
  assert.strictEqual(modeWord(readLiveMode({ live_mode: false })), 'PAPER');
  assert.strictEqual(modeWord(readLiveMode({ live_mode: true })), 'LIVE');
});

// ── every producer reaches it ──────────────────────────────────────────────

const PRODUCERS = ['routes/portfolio.js', 'routes/sync.js', 'routes/track.js'];

test('every producer of a mode word requires the one reading', () => {
  for (const rel of PRODUCERS) {
    const src = codeOnly(fs.readFileSync(path.join(APP, rel), 'utf8'));
    assert.match(src, /require\('\.\.\/lib\/live_mode'\)/, `${rel} does not import lib/live_mode`);
    assert.match(src, /modeWord\(/, `${rel} imports the reading and never calls it`);
  }
});

test('no producer re-derives the word from the raw field', () => {
  // The three spellings that existed: the loose ternary, the strict-but-local
  // ternary, and the bare boolean read into a variable. Any of them coming
  // back is a second answer.
  const bad = [
    /live_mode\s*\?\s*'LIVE'/,
    /typeof\s+cb\.live_mode\s*===\s*'boolean'\)\s*(live|mode)\s*=/,
    /=\s*!!\s*cb\.live_mode/,
  ];
  for (const rel of PRODUCERS) {
    const src = codeOnly(fs.readFileSync(path.join(APP, rel), 'utf8'));
    for (const re of bad) assert.doesNotMatch(src, re, `${rel} re-derives the mode: ${re}`);
  }
});

test('the reading is reached by every route that stamps a mode on a portfolio payload', () => {
  // The sweep that found sync.js: every `mode:` key written into a payload
  // object in routes/ must be fed by modeWord, the unconfigured-deployment
  // PAPER in portfolio.js being the one deliberate exception (there is no bot
  // to have an account on, so PAPER there is the deployment's own state).
  const routes = fs.readdirSync(path.join(APP, 'routes')).filter((f) => f.endsWith('.js'));
  const offenders = [];
  for (const f of routes) {
    const src = codeOnly(fs.readFileSync(path.join(APP, 'routes', f), 'utf8'));
    for (const m of src.matchAll(/\bmode:\s*([^,\n]+)/g)) {
      const rhs = m[1].trim();
      // Only the TRADING mode is this claim. `alerts.js` stamps a schedule
      // mode ('recurring' | 'once') and `credentials.js` a storage mode
      // ('sealed'); the first draft of this scan flagged both. Not every match
      // is a defect, including in a new test.
      if (/^modeWord\(/.test(rhs)) continue;
      if (!/LIVE|PAPER|live_mode/.test(rhs)) continue;
      if (/^'PAPER'/.test(rhs) && /unconfigured: true/.test(src.slice(m.index, m.index + 120))) continue;
      if (/^mode\b/.test(rhs)) continue;          // `mode,` shorthand fed above
      offenders.push(`${f}: mode: ${rhs}`);
    }
  }
  assert.deepStrictEqual(offenders, [], 'a mode stamped without the reading');
});
