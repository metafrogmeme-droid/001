'use strict';
// The same rule as the dashboard, applied to the standalone pages: a failed
// read is never rendered as a statement about what exists.
//
// Two pages got this wrong in ways that mattered more than a panel:
//   roots.html    — the provenance ledger. A non-ok answer resolved to null and
//                   rendered "No completed day has sealed calls yet ... Honest
//                   ledgers start empty" on the one page whose job is proving
//                   the seal ledger is NOT empty.
//   strategy.html — a non-ok answer rendered "No strategy named X", telling
//                   someone following a shared link that their strategy does
//                   not exist, when we simply could not look.

const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

const pub = (f) => fs.readFileSync(path.join(__dirname, '..', 'public', f), 'utf8');

test('roots.html routes a non-ok answer to the unavailable path', () => {
  const src = pub('roots.html');
  assert.doesNotMatch(src, /r\.ok \? r\.json\(\) : null/,
    'a non-ok answer must not resolve to null — that renders the empty-ledger copy');
  assert.match(src, /if \(!r\.ok\) throw new Error\('HTTP ' \+ r\.status\);/,
    'it throws so the existing catch reports the feed as unavailable');
  // The empty-ledger copy must still exist — it is correct when the read WORKED
  // and there genuinely are no roots yet.
  assert.match(src, /No completed day has sealed calls yet/,
    'the genuine-empty copy stays for the genuine-empty case');
});

test('strategy.html only claims a strategy is absent when the server said 404', () => {
  const src = pub('strategy.html');
  assert.match(src, /if \(r\.status === 404\) return null;/,
    '404 is the only answer that earns notFound()');
  assert.match(src, /if \(!r\.ok\) throw new Error\('HTTP ' \+ r\.status\);/,
    'every other failure reports the catalogue as unavailable');
  assert.match(src, /function unavailable\(\)/, 'the unavailable path is shared, not duplicated');
  assert.doesNotMatch(src, /\.catch\(function \(\) \{ notFound\(\); \}\)/,
    'a network failure is not evidence that a strategy does not exist');
});

test('the soft-failure shape is not rendered as emptiness either', () => {
  // idle-yield and cross-yield fail SOFT inside a 200: {available:false}. A
  // wallet-less user comes back {available:true, wallet_linked:false}, so
  // available:false is a failure and must reach the error state.
  const dash = pub('js/dashboard.js');
  assert.match(dash, /if \(!d \|\| d\.available === false\) throw new Error\('idle-yield scanner unavailable'\)/);
  assert.match(dash, /if \(!d \|\| d\.available === false\) throw new Error\('cross-yield planner unavailable'\)/);
  assert.doesNotMatch(dash, /d\.available === false\) return null/,
    'a soft failure must not fall through to the empty state');
});

test('web3 empty states describe emptiness and offer the next step', () => {
  const dash = pub('js/dashboard.js');
  // The old copy hedged about reachability, which is now the ERROR state's job.
  assert.doesNotMatch(dash, /is reachable\.'/,
    'empty states no longer hedge about whether a source was reachable');
  // The connectable surfaces point at a real, working deep link.
  const ctas = [...dash.matchAll(/href: '#account\/awallet'/g)];
  assert.ok(ctas.length >= 4, `expected the wallet CTA on the connectable panels, found ${ctas.length}`);

  const i18n = require('../public/js/i18n');
  const keys = ['dd.cta_link_wallet', 'dd.e_networth', 'dd.e_holdings', 'dd.e_wallet',
    'dd.e_idle', 'dd.e_idle_nowallet', 'dd.e_cross_nowallet', 'dd.e_dex', 'dd.e_meme', 'dd.e_flow'];
  for (const k of keys) {
    assert.ok(i18n.STRINGS[k], `${k} is in the dictionary`);
    for (const l of i18n.LANGS) assert.ok(i18n.STRINGS[k][l.code], `${k} missing ${l.code}`);
  }
});
