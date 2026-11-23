'use strict';
/**
 * The committed email previews (app/test/fixtures/email_previews/) are what
 * the templates render today, so the copy a reviewer read and approved is
 * the copy that ships. Rendered in memory and compared; nothing is written.
 *
 * When a wording or layout change is meant, re-render and commit:
 *
 *   node app/scripts/email_preview.js --out app/test/fixtures/email_previews
 */
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const preview = require('../scripts/email_preview');

const DIR = path.join(__dirname, 'fixtures', 'email_previews');
const RERENDER = 'node app/scripts/email_preview.js --out app/test/fixtures/email_previews';

test('the committed previews are what the templates render today', () => {
  const want = preview.files();
  assert.deepEqual(fs.readdirSync(DIR).sort(), Object.keys(want).sort(), `the preview set changed: ${RERENDER}`);
  for (const [name, body] of Object.entries(want)) {
    assert.equal(fs.readFileSync(path.join(DIR, name), 'utf8'), body, `${name} is stale: ${RERENDER}`);
  }
});

test('the previews carry placeholder tokens, never a real-looking secret', () => {
  for (const name of fs.readdirSync(DIR)) {
    const body = fs.readFileSync(path.join(DIR, name), 'utf8');
    for (const m of body.matchAll(/token=([^\s"&<]+)/g)) {
      assert.match(m[1], /^preview-[a-z-]+-token$/, `${name}: ${m[1]} is not a placeholder`);
    }
  }
});
