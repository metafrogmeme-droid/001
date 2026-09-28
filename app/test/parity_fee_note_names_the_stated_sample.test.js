'use strict';
// The parity panel's "Fees vs model" tile prints a dash whenever the bot
// withheld the ratio, and the note beside it has to say WHY. The ratio is
// read over the closes whose round trip the VENUE stated (`fees_stated`),
// never over a commission the bot estimated at its own configured rates --
// on the 2026-09-28 record 182 of 211 closes carried such an estimate and
// the card printed "0.46x (better than model)": the model compared with
// itself. The renderer sits inline in a 6k-line function, so this is a scan
// of the panel's block, stated as one; the Python suite drives the
// producer's count and the sentence's words.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('fs');
const path = require('path');
const { codeOnly } = require('./helpers/code_only.js');

const DASH = codeOnly(fs.readFileSync(
  path.join(__dirname, '..', 'public', 'js', 'dashboard.js'), 'utf8'));

function parityBlock() {
  const i = DASH.indexOf("renderPanel(C('eparity')");
  assert.ok(i > 0, 'parity panel not found');
  return DASH.slice(i, DASH.indexOf("renderPanel(C('", i + 40));
}

test('a withheld ratio names the venue-stated sample, not the closes carrying any commission', () => {
  const block = parityBlock();
  assert.match(block, /p\.fees_stated != null/, 'the note reads the stated count');
  assert.match(block, /round trip stated by the venue on \$\{p\.fees_stated\} of \$\{p\.trades\} closes — fee ratio withheld/);
});

test('an older bot that sends no stated count keeps the reason it can give', () => {
  const block = parityBlock();
  const i = block.indexOf('p.fees_stated == null');
  assert.ok(i > 0, 'the fallback is keyed on the count being ABSENT');
  assert.match(block.slice(i), /fee record on \$\{p\.fees_read\} of \$\{p\.trades\} closes — fee ratio withheld/);
  // The fallback is the older bot's sentence and never the new bot's: a
  // stated count of 0 is a reading, and must not fall through to it.
  assert.ok(block.indexOf('p.fees_stated != null') < i, 'the stated sentence is asked first');
});
