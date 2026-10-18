'use strict';
/**
 * The Deep Scan panels' empty state used to say the pattern read "appears
 * after the engine's next deep scan". The engine runs no deep scan on its
 * own: a block reaches the website only when someone runs /deepscan in
 * Telegram or "deep scan" in AI Chat, and the web keeps it six hours
 * (DEEPSCAN_TTL_MS). A page that promises an event nothing schedules waits
 * forever (2026-10-18). The empty state now names the doors that run one.
 *
 * Both arms: no empty state promises an engine run, and every deep-scan
 * empty state names a door (the chat phrase and the Telegram command).
 */
const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const { codeOnly } = require('./helpers/code_only');

const CODE = codeOnly(fs.readFileSync(path.join(__dirname, '..', 'public', 'js', 'dashboard.js'), 'utf8'));
const SYNC = codeOnly(fs.readFileSync(path.join(__dirname, '..', 'routes', 'sync.js'), 'utf8'));

function deepScanEmptyStates() {
  // Every renderPanel empty state attached to a loader that reads `deepscan`.
  const out = [];
  const re = /empty:\s*\{\s*icon:\s*'[\w-]+',\s*text:\s*'((?:[^'\\]|\\.)*)'\s*\}/g;
  let m;
  while ((m = re.exec(CODE)) !== null) {
    const before = CODE.slice(Math.max(0, m.index - 1500), m.index);
    const loaderStart = before.lastIndexOf('renderPanel(');
    if (loaderStart >= 0 && /deepscan/.test(before.slice(loaderStart))) out.push(m[1]);
  }
  return out;
}

test('the scan finds the deep-scan panels', () => {
  assert.ok(deepScanEmptyStates().length >= 3, 'the matcher drifted: fewer than three deep-scan panels found');
});

test('no deep-scan empty state promises a run the engine never makes', () => {
  for (const t of deepScanEmptyStates()) {
    assert.doesNotMatch(t, /engine\\?'s next deep scan/i, t);
  }
});

test('every deep-scan empty state names the doors that run one, and the window it is kept', () => {
  const hours = /DEEPSCAN_TTL_MS\s*=\s*(\d+)\s*\*\s*60\s*\*\s*60\s*\*\s*1000/.exec(SYNC);
  assert.ok(hours, 'DEEPSCAN_TTL_MS is no longer hours * 60 * 60 * 1000 — re-read the window');
  for (const t of deepScanEmptyStates()) {
    assert.match(t, /\/deepscan/, t);
    assert.match(t, /deep scan\\?" in AI Chat/, t);
    assert.ok(t.includes(`last ${hours[1]} hours`), `the window it names is not the web's TTL: ${t}`);
  }
});
