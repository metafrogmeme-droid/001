'use strict';
/**
 * Research Note 001 — the honesty contract for published research:
 * - a NEGATIVE result is stated plainly, in the title and the conclusions
 *   ("we tested it and it failed" is the product);
 * - method, sample sizes and caveats are in the note — a reader can weigh
 *   the evidence without trusting us;
 * - the scripts that produced the numbers ship in-repo and hit only public
 *   endpoints, so anyone can re-run the study;
 * - no dollar figures, no performance promises, the advice line present.
 */
const test = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const path = require('path');

const ROOT = path.join(__dirname, '..', '..');
const note = fs.readFileSync(path.join(ROOT, 'docs', 'research', 'rwa_session_gap.md'), 'utf8');

test('the negative result is the headline, never buried', () => {
  assert.match(note, /tested, and the data said no/);
  assert.match(note, /reports a \*\*negative result\*\*/);
  assert.match(note, /is not supported/);
  assert.match(note, /will not ship a strategy built on it/);
});

test('method and sample sizes are stated', () => {
  assert.match(note, /952 pooled observations/);
  assert.match(note, /30-minute candles/);
  assert.match(note, /09:30 ET/);
  assert.match(note, /Correlation of closed-hours return with open-session return: \+0\.026/);
  assert.match(note, /2,160 prints, 67% exactly zero/);
});

test('caveats and reproduction ship with the claim', () => {
  assert.match(note, /## Caveats/);
  assert.match(note, /One venue, ~6 months/);
  assert.match(note, /scripts\/research\/rwa_session_gap\.py/);
  assert.match(note, /scripts\/research\/rwa_funding\.py/);
  for (const f of ['rwa_session_gap.py', 'rwa_funding.py']) {
    assert.ok(fs.existsSync(path.join(ROOT, 'scripts', 'research', f)), `${f} ships in-repo`);
  }
});

test('§4 + honesty doctrine on the research surface', () => {
  assert.ok(!note.includes('$'), 'no dollar figures');
  assert.match(note, /not investment advice/);
  assert.match(note, /No performance is promised/);
  assert.match(note, /in either direction/);
  assert.ok(!/guarantee|sure thing|can'?t lose/i.test(note));
  // The scripts fetch public market data only — no keys, no auth headers.
  for (const f of ['rwa_session_gap.py', 'rwa_funding.py']) {
    const src = fs.readFileSync(path.join(ROOT, 'scripts', 'research', f), 'utf8');
    assert.ok(!/api[_-]?key|secret|passphrase|Authorization/i.test(src), `${f} needs no credentials`);
    assert.match(src, /api\.bitget\.com\/api\/v2\/mix\/market/, `${f} hits only public market endpoints`);
  }
});
