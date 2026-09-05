/**
 * Every number in facts.ts STATS is re-derived from the file it cites.
 *
 * `source` being required by the type stops an uncited figure from compiling.
 * It does not stop a cited figure from ROTTING: the venue table grows, the
 * language list gains an entry, and the homepage keeps printing last month's
 * count beside a citation that still looks authoritative. That is the
 * $72,669 shape at a slower speed. So each stat here is counted from its
 * source by the same rule a reader would apply, and the test fails the day
 * the code moves and the page does not.
 */
import test from 'node:test'
import assert from 'node:assert'
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const REPO = path.join(path.dirname(fileURLToPath(import.meta.url)), '..', '..');
const read = (rel) => fs.readFileSync(path.join(REPO, rel), 'utf8');

function stats() {
  const src = read('site/src/facts.ts').replace(/\/\*[\s\S]*?\*\//g, ' ').replace(/\/\/[^\n]*/g, ' ');
  const arr = src.match(/STATS[^=]*=\s*\[([\s\S]*?)\n\]/);
  assert.ok(arr, 'STATS is gone from facts.ts');
  const out = {};
  for (const m of arr[1].matchAll(/\{[^{}]*\}/g)) {
    const value = m[0].match(/value:\s*'([^']*)'/);
    const label = m[0].match(/label:\s*'([^']*)'/);
    if (value && label) out[label[1]] = value[1];
  }
  return out;
}

const STATS = stats();

test('venue adapters: the _VENUES table in bot/core/venues.py', () => {
  const py = read('bot/core/venues.py');
  const table = py.match(/_VENUES[^=]*=\s*\{([\s\S]*?)\n\}/);
  assert.ok(table, '_VENUES table not found');
  const n = [...table[1].matchAll(/^\s+"[a-z]+":/gm)].length;
  assert.ok(n > 0);
  assert.equal(STATS['venue adapters'], String(n));
});

test('Guardian surfaces: the page routes in app/server.js', () => {
  const js = read('app/server.js');
  const routes = ['flight', 'stress', 'sentinel', 'firewall', 'escape', 'intent']
    .filter((p) => js.includes(`app.get('/${p}'`));
  assert.equal(STATS['Guardian surfaces'], String(routes.length));
});

test('interface languages: the LANGS table in app/public/js/i18n.js', () => {
  const js = read('app/public/js/i18n.js');
  const n = [...js.matchAll(/\{ code: '[a-z]{2}', name: /g)].length;
  assert.ok(n > 0);
  assert.equal(STATS['interface languages'], String(n));
});

test('chat languages: _CHAT_LANG_NAMES in bot/utils/i18n.py', () => {
  const py = read('bot/utils/i18n.py');
  const table = py.match(/_CHAT_LANG_NAMES\s*=\s*\{([\s\S]*?)\n\}/);
  assert.ok(table, '_CHAT_LANG_NAMES not found');
  // Several keys share a line, so count keys, not lines.
  const n = [...table[1].matchAll(/"[a-z]{2,3}":\s*"/g)].length;
  assert.ok(n > 0);
  assert.equal(STATS['chat languages'], String(n));
});

test('every stat on the page is one this file re-derives', () => {
  assert.deepStrictEqual(Object.keys(STATS).sort(), [
    'Guardian surfaces', 'chat languages', 'interface languages', 'venue adapters',
  ], 'a stat was added to facts.ts without a derivation here');
});
