'use strict';
/**
 * Two things a review of the home page (2026-10-19) found missing or crossed:
 * a safety line beside the Telegram door, and a link preview whose sentence
 * was not the page's own. Both are copy, so both are pinned.
 */
const test = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const path = require('path');

const html = fs.readFileSync(path.join(__dirname, '..', 'public', 'index.html'), 'utf8');
const meta = (sel) => {
  const m = html.match(new RegExp('<meta ' + sel + ' content="([^"]*)">'));
  assert.ok(m, sel);
  return m[1];
};

test('the Telegram steps are followed by the safety line, which names the one bot, in every language', () => {
  const i = html.indexOf('data-i18n-html="acc.tg_step3"');
  const j = html.indexOf('data-i18n="acc.safety"');
  assert.ok(i > 0 && j > i && j - i < 400, 'the safety line sits right after the link steps');
  const line = html.slice(j, html.indexOf('</p>', j));
  assert.match(line, /never message you first/);
  assert.match(line, /seed phrase/);
  assert.match(line, /@HTRUNECLAW_bot/);
  const i18n = require('../public/js/i18n.js');
  for (const l of i18n.LANGS) {
    const v = i18n.STRINGS['acc.safety'][l.code];
    assert.ok(v && v.includes('@HTRUNECLAW_bot'), `${l.code} does not name the bot`);
  }
});

test('the link preview says what the page says', () => {
  const description = meta('name="description"');
  assert.equal(meta('property="og:description"'), description);
  const first = description.split('. ')[0];
  assert.ok(meta('name="twitter:description"').startsWith(first), 'the X card opens with the same sentence');
});
