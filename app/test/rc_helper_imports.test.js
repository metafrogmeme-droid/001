'use strict';
// Every RC helper dashboard.js calls must actually be imported from it.
//
// app.js exports its helpers on window.RC; dashboard.js destructures the ones
// it uses in a single statement at the top. Adding an export without adding it
// to that list produces a ReferenceError at the CALL site — which, inside a
// renderPanel loader, is caught and painted as "Couldn't load this panel."
// The panel then fails permanently and looks exactly like a dead endpoint.
// That shipped once (mustRead). This test makes the class impossible.

const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

const APP = fs.readFileSync(path.join(__dirname, '..', 'public', 'js', 'app.js'), 'utf8');
const DASH = fs.readFileSync(path.join(__dirname, '..', 'public', 'js', 'dashboard.js'), 'utf8');
// Every file that destructures window.RC is subject to the same trap.
const CONSUMERS = ['dashboard.js', 'chat.js'].map((f) => ({
  name: f, src: fs.readFileSync(path.join(__dirname, '..', 'public', 'js', f), 'utf8'),
}));

function rcExports() {
  const m = APP.match(/window\.RC = \{([\s\S]*?)\};/);
  assert.ok(m, 'app.js defines window.RC');
  return m[1].split(',').map((s) => s.trim()).filter((s) => /^[A-Za-z_$][\w$]*$/.test(s));
}

function importsOf(src, name) {
  const m = src.match(/const \{([\s\S]*?)\} = (?:window\.)?RC;/);
  assert.ok(m, `${name} destructures RC`);
  return m[1].split(',').map((s) => s.trim()).filter(Boolean);
}
function dashImports() { return importsOf(DASH, 'dashboard.js'); }

test('every RC helper its consumers call is destructured from RC', () => {
  const exported = new Set(rcExports());
  const missing = [];
  for (const { name: file, src: DASH } of CONSUMERS) {
  const imported = new Set(importsOf(DASH, file));
  for (const name of exported) {
    if (imported.has(name)) continue;
    // Called bare somewhere in dashboard.js? Then it must be imported.
    // `RC.name` / `window.RC.name` are qualified and always fine.
    const bare = new RegExp(`(^|[^.\\w$])${name}\\s*\\(`, 'm');
    const hits = DASH.split('\n')
      .map((line, i) => ({ line, n: i + 1 }))
      .filter(({ line }) => bare.test(line) && !new RegExp(`(RC|window\\.RC)\\.${name}`).test(line)
        && !/^\s*(\/\/|\*)/.test(line));
    if (hits.length) missing.push(`${name} (first use: ${file}:${hits[0].n})`);
  }
  }
  assert.deepStrictEqual(missing, [],
    'these are called bare but never imported — they throw ReferenceError at runtime:\n  '
      + missing.join('\n  '));
});

test('mustRead specifically is imported (the one that shipped broken)', () => {
  assert.ok(dashImports().includes('mustRead'),
    'mustRead is called at 65+ sites; without the import every one of them '
      + 'throws ReferenceError and its panel shows a permanent error state');
  assert.ok(rcExports().includes('mustRead'), 'and app.js still exports it');
});
