'use strict';
/**
 * The /token page shows the tokenomics card, and only the guarded one.
 *
 * The card is docs/assets/presale/rclaw_tokenomics_1000x1000.png: the figures
 * it draws are written into the PNG (a tEXt chunk named `runeclaw-tokenomics`)
 * and token/presale/sale_page_images.test.mjs holds them to the roadmap, the
 * sale config and the locks plan. The web deploy ships app/ alone, so the site
 * serves a copy from app/public. A copy is a second answer; these tests make it
 * the same answer: the same bytes, and a description, in every language, that
 * carries exactly the figures the card draws.
 */

const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

const ROOT = path.join(__dirname, '..', '..');
const ORIGINAL = path.join(ROOT, 'docs', 'assets', 'presale', 'rclaw_tokenomics_1000x1000.png');
const SERVED = path.join(__dirname, '..', 'public', 'rclaw_tokenomics_1000x1000.png');
const PAGE = fs.readFileSync(path.join(__dirname, '..', 'public', 'token.html'), 'utf8');
const { STRINGS } = require('../public/js/i18n.js');

/** The figures the card was drawn with, read from its own tEXt chunk. */
function drawnFigures(file) {
  const b = fs.readFileSync(file);
  for (let i = 8; i < b.length;) {
    const n = b.readUInt32BE(i);
    const type = b.toString('latin1', i + 4, i + 8);
    if (type === 'tEXt') {
      const body = b.subarray(i + 8, i + 8 + n);
      const nul = body.indexOf(0);
      if (body.toString('latin1', 0, nul) === 'runeclaw-tokenomics') {
        return JSON.parse(body.toString('latin1', nul + 1));
      }
    }
    i += 12 + n;
  }
  throw new Error('no runeclaw-tokenomics chunk');
}

/** Every number in a sentence, with thousands separators of any locale folded away. */
function numbersIn(text) {
  return [...String(text).matchAll(/\d{1,3}(?:[,.   ]\d{3})+(?!\d)|\d+/g)]
    .map((m) => Number(m[0].replace(/[,.   ]/g, '')));
}

const decode = (s) => s.replace(/&amp;/g, '&').replace(/&lt;/g, '<').replace(/&gt;/g, '>').replace(/&quot;/g, '"');

test('the site serves the guarded card itself, byte for byte', () => {
  assert.ok(fs.existsSync(SERVED), 'app/public has no tokenomics card');
  assert.ok(fs.readFileSync(SERVED).equals(fs.readFileSync(ORIGINAL)),
    'the served card differs from docs/assets/presale/: redraw there, then copy it here');
});

test('the token page shows it in its own section, outside the record-driven part', () => {
  const sec = PAGE.match(/<section class="tok-card tok-tokenomics"[\s\S]*?<\/section>/);
  assert.ok(sec, 'no tokenomics section on /token');
  const html = sec[0];
  assert.match(html, /data-i18n="tok\.tokenomics_h"/);
  const img = html.match(/<img\s[^>]*>/);
  assert.ok(img, 'no image in the section');
  assert.match(img[0], /src="\/rclaw_tokenomics_1000x1000\.png\?v=\d+"/);
  assert.match(img[0], /width="1000" height="1000"/, 'the size reserves the space before the image loads');
  assert.match(img[0], /data-i18n-attr="alt:tok\.tokenomics_alt"/);
  const href = html.match(/<a href="([^"]+)"/)[1];
  assert.strictEqual(href, img[0].match(/src="([^"]+)"/)[1], 'the full-size link opens the same image');
  // Static, so a record that cannot be read does not take the card with it.
  assert.ok(PAGE.indexOf('id="tok-root"') < PAGE.indexOf('<section class="tok-card tok-tokenomics"'),
    'the card sits after the record-driven part');
  assert.ok(!/tok-tokenomics/.test(fs.readFileSync(path.join(__dirname, '..', 'public', 'js', 'token-page.js'), 'utf8')),
    'the renderer replaces #tok-root only; the card must not depend on it');
  // The markup's English alt is the dictionary's English alt.
  const alt = decode(img[0].match(/\salt="([^"]*)"/)[1]);
  assert.strictEqual(alt, STRINGS['tok.tokenomics_alt'].en);
});

test('in every language, the description carries exactly the figures the card draws', () => {
  const drawn = drawnFigures(SERVED);
  const want = [drawn.supply, ...drawn.buckets.map((b) => Math.round(b.pct))].sort((a, b) => a - b);
  const langs = Object.keys(STRINGS['tok.lede']);
  assert.ok(langs.length >= 14, `${langs.length} languages`);
  for (const key of ['tok.tokenomics_h', 'tok.tokenomics_note', 'tok.tokenomics_alt']) {
    assert.deepStrictEqual(Object.keys(STRINGS[key]).sort(), langs.slice().sort(), `${key} is missing a language`);
  }
  for (const lang of langs) {
    const got = numbersIn(STRINGS['tok.tokenomics_alt'][lang]).sort((a, b) => a - b);
    assert.deepStrictEqual(got, want, `${lang}: the description's figures are not the card's`);
  }
});
