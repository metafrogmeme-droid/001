'use strict';
/**
 * The shipped mascot's clips are the ones the loader reaches for.
 *
 * `mascot3d.js` plays a clip called `idle` at rest and cross-fades to
 * `analyze` / `alert` / `execute` when the dashboard reacts. Those names were
 * the Viking agent's. The owl (2026-10-19) ships `Idle`, `Blink` and `Talk`,
 * capitalised, and none of the reactive three: read by exact name, every
 * reaction would have played nothing and the rest pose would have been
 * "whichever clip came first", with no test to say so. So the loader reads
 * clip names lower-cased and keeps an alias table from each reaction to a clip
 * the model has. This file holds the model and the table to each other: every
 * reaction resolves to a clip that EXISTS in the shipped file, and the rest
 * clip is there by name. Both arms: a table naming a clip the model lacks
 * fails here, as does a model that drops a clip the table names.
 */
const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

const PUB = path.join(__dirname, '..', 'public');
const SRC = fs.readFileSync(path.join(PUB, 'js', 'mascot3d.js'), 'utf8');

function glbJson(file) {
  const b = fs.readFileSync(file);
  assert.strictEqual(b.readUInt32LE(0), 0x46546c67, `${file} is not glTF-binary`);
  let off = 12;
  while (off < b.length) {
    const len = b.readUInt32LE(off);
    const type = b.readUInt32LE(off + 4);
    if (type === 0x4e4f534a) return JSON.parse(b.slice(off + 8, off + 8 + len).toString('utf8'));
    off += 12 + len - 4;
  }
  throw new Error(`${file} has no JSON chunk`);
}

function modelFile() {
  const m = SRC.match(/const MODEL_URL = '([^']+)'/);
  assert.ok(m, 'MODEL_URL is no longer a single quoted literal');
  return path.join(PUB, m[1].split('?')[0].replace(/^\//, ''));
}

/** The alias table as the source declares it: { reaction: clip }. */
function aliasTable() {
  const m = SRC.match(/const CLIP_ALIAS = \{([^}]*)\};/);
  assert.ok(m, 'CLIP_ALIAS is gone; a reaction the model has no clip for plays nothing');
  const table = {};
  for (const pair of m[1].matchAll(/(\w+):\s*'([^']+)'/g)) table[pair[1]] = pair[2];
  return table;
}

function clipNames() {
  return (glbJson(modelFile()).animations || []).map((a) => String(a.name || '').toLowerCase());
}

test('the shipped model has a rest clip the loader can name', () => {
  const names = clipNames();
  assert.ok(names.length >= 1, 'the model ships no animation at all');
  assert.ok(names.includes('idle'), `no idle clip among ${JSON.stringify(names)}`);
});

test('every reaction resolves to a clip the shipped model has', () => {
  const names = new Set(clipNames());
  const alias = aliasTable();
  for (const reaction of ['analyze', 'alert', 'execute']) {
    const played = names.has(reaction) ? reaction : alias[reaction];
    assert.ok(played && names.has(played),
      `react('${reaction}') plays ${played || 'nothing'}: the model has ${JSON.stringify([...names])}`);
  }
});

test('the alias table names only clips that exist, and the loader reads names lower-cased', () => {
  const names = new Set(clipNames());
  for (const [reaction, clip] of Object.entries(aliasTable())) {
    assert.ok(names.has(clip), `CLIP_ALIAS.${reaction} = '${clip}', which the model does not ship`);
  }
  assert.match(SRC, /actionByName\[String\(cl\.name \|\| ''\)\.toLowerCase\(\)\] = mixer\.clipAction\(cl\)/,
    'clip names are read by exact case again; the owl capitalises its clips');
  assert.match(SRC, /actionByName\[name\] \|\| actionByName\[CLIP_ALIAS\[name\]\]/,
    'fadeTo no longer falls back to the alias');
});

test('the table is consulted, not decorative: a reaction the model lacks by name needs it', () => {
  // The owl has none of the three reactive names, so without the aliases the
  // second test above could only pass on a model that happened to use them.
  const names = new Set(clipNames());
  const missing = ['analyze', 'alert', 'execute'].filter((r) => !names.has(r));
  assert.ok(missing.length > 0 || Object.keys(aliasTable()).length === 0,
    'every reaction exists by name now; the alias table can go, and this test with it');
});
