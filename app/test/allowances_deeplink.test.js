'use strict';
/**
 * The X-ray comes to you. /approvals?a=0x…&c=chain prefills and auto-runs
 * (address re-validated by the same regex before anything fires), the
 * dashboard's linked-wallet panel builds that link with its known address,
 * and the Escape Agent's topnav carries the exit-hygiene pointer.
 */
const test = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const path = require('path');

const read = (...p) => fs.readFileSync(path.join(__dirname, '..', 'public', ...p), 'utf8');

test('the deep link validates before it runs', () => {
  const page = read('approvals.html');
  const deep = page.slice(page.indexOf('Deep link:'));
  assert.match(deep, /URLSearchParams\(location\.search\)/);
  assert.match(deep, /\/\^0x\[0-9a-fA-F\]\{40\}\$\/\.test\(qa\)/,
    'the query address passes the same regex as manual input');
  assert.match(deep, /CHAINS\.indexOf\(qc\) >= 0/, 'unknown chains are ignored');
});

test('the dashboard wallet panel links the X-ray with its own address', () => {
  const dash = read('js', 'dashboard.js');
  assert.match(dash, /\/approvals\?a=\$\{encodeURIComponent\(d\.address\)\}/);
  assert.match(dash, /dd\.w_xray/);
  const html = read('dashboard.html');
  const m = html.match(/dashboard\.js\?v=(\d+)/);
  assert.ok(m && Number(m[1]) >= 124, 'dashboard buster bumped');
});

test('the Escape Agent points at exit hygiene', () => {
  assert.match(read('escape.html'), /href="\/approvals" data-i18n="nav\.approvals"/);
});

test('dd.w_xray ships all 14 locales', () => {
  const line = read('js', 'i18n.js').split('\n').find((l) => l.includes("'dd.w_xray':"));
  assert.ok(line);
  for (const loc of ['en:', 'hi:', 'it:', 'es:', 'zh:', 'pt:', 'fr:', 'de:',
    'nl:', 'ja:', 'ko:', 'ru:', 'tr:', 'ar:']) {
    assert.ok(line.includes(loc), `dd.w_xray has ${loc}`);
  }
  const m = read('approvals.html').match(/i18n\.js\?v=(\d+)/);
  assert.ok(m && Number(m[1]) >= 88, 'i18n buster >= 88');
});
