'use strict';
/**
 * The domain is humanoid-traders.com. The product is RUNECLAW. Until now those
 * two names were never connected anywhere a visitor could see.
 *
 * Measured 2026-08-17, before the fix: the string "Humanoid Traders" appeared
 * on the landing page exactly ZERO times in visible copy — only inside URLs
 * (the GitBook docs link, the GitHub org). So somebody who typed the domain,
 * or clicked a link to it, arrived at a page headed by a name they had never
 * heard of, with nothing on it saying the two were the same people. A review
 * of the site called this out as a "branding disconnect" and it is the plainest
 * kind: the site does not state who it belongs to.
 *
 * Humanoid Traders is the identity; RUNECLAW is its command core. The site now
 * says so, and this file keeps it said.
 *
 * WHY EACH LIST BELOW IS DERIVED RATHER THAN ENUMERATED. A hardcoded list of
 * pages passes forever while a page added tomorrow ships without the tag — the
 * test would be describing the site as it was on the day it was written. So the
 * sweep asks the FILES which pages make each claim, and then requires all of
 * them to make it consistently. The non-vacuity test at the bottom is the other
 * half of that bargain: a derivation that silently stops matching anything
 * passes just as green as one that matches everything.
 */

const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

const PUB = path.join(__dirname, '..', 'public');
const ORG = 'Humanoid Traders';
const PRODUCT = 'RUNECLAW';

const pages = fs.readdirSync(PUB).filter((f) => f.endsWith('.html')).sort();
const read = (f) => fs.readFileSync(path.join(PUB, f), 'utf8');
const html = Object.fromEntries(pages.map((f) => [f, read(f)]));

//: A page that ships an OpenGraph block is a page somebody can share into a
//: chat app, and the unfurl card is often the ONLY thing a person sees before
//: deciding whether to click. og:site_name is the field that names the
//: organisation on that card.
const withOg = pages.filter((f) => html[f].includes('property="og:'));
//: A footer is where a site states who it belongs to. Only some pages carry
//: one; the rule is about the ones that do.
const withFooter = pages.filter((f) => html[f].includes('<footer'));

test('every page that unfurls names the organisation on the card', () => {
  const missing = withOg.filter((f) => !html[f].includes('og:site_name'));
  assert.deepStrictEqual(missing, [],
    'these pages declare OpenGraph tags but no og:site_name, so a link to '
    + 'them unfurls with no organisation on the card:\n  ' + missing.join('\n  '));
});

test('og:site_name says the organisation, not the product', () => {
  // The distinction is the entire point: RUNECLAW in this field would restate
  // the product name that og:title already carries and connect nothing.
  for (const f of withOg) {
    const m = html[f].match(/property="og:site_name" content="([^"]*)"/);
    assert.ok(m, `${f} has no og:site_name`);
    assert.strictEqual(m[1], ORG,
      `${f} names "${m[1]}" as the site; it should be the organisation, ` + ORG);
  }
});

test('the landing page title carries both names', () => {
  const title = html['index.html'].match(/<title>([^<]*)<\/title>/)[1];
  assert.ok(title.includes(PRODUCT),
    `the landing <title> must still lead with the product: ${title}`);
  assert.ok(title.includes(ORG),
    'the landing <title> is the first thing a search result and a browser tab '
    + `show, and it does not mention ${ORG}: ${title}`);
});

test('every footer states the relationship in visible copy', () => {
  // Visible copy, not a meta tag: a person reading the page has to be able to
  // learn this. The og: fields above are for machines and link previews.
  assert.ok(withFooter.length > 0, 'no page has a footer — the sweep found nothing');
  for (const f of withFooter) {
    assert.ok(html[f].includes('class="org-line'),
      `${f} has a footer but no .org-line, so the page never says who it belongs to`);
    assert.ok(html[f].includes('data-i18n="lp.org_line"'),
      `${f}'s org line is not translated — it would stay English in 13 locales`);
    assert.ok(html[f].includes(ORG) && html[f].includes(PRODUCT),
      `${f}'s footer copy must name both ${ORG} and ${PRODUCT}`);
  }
});

test('the org line is translated into every locale, naming both parties', () => {
  const i18n = fs.readFileSync(path.join(PUB, 'js', 'i18n.js'), 'utf8');
  const m = i18n.match(/'lp\.org_line':\s*\{([^}]*)\}/);
  assert.ok(m, 'lp.org_line is missing from i18n.js');
  const body = m[1];
  const LOCALES = ['en', 'hi', 'it', 'es', 'zh', 'pt', 'fr', 'de', 'nl', 'ja',
    'ko', 'ru', 'tr', 'ar'];
  for (const loc of LOCALES) {
    assert.ok(new RegExp('\\b' + loc + ':').test(body),
      'lp.org_line missing locale ' + loc);
  }
  // Both proper nouns stay in Latin script in every translation — they are
  // names, not words, and a reader who arrived from the domain has to be able
  // to match the string they typed against the string on the page.
  const perLocale = body.split(/,\s*(?=[a-z]{2}:)/);
  assert.ok(perLocale.length >= LOCALES.length,
    `lp.org_line split into ${perLocale.length} locale strings, expected `
    + `at least ${LOCALES.length} — the split heuristic stopped matching`);
  for (const s of perLocale) {
    assert.ok(s.includes(ORG),
      `a translation of lp.org_line does not carry "${ORG}" verbatim: ${s}`);
    assert.ok(s.includes(PRODUCT),
      `a translation of lp.org_line does not carry "${PRODUCT}" verbatim: ${s}`);
  }
});

test('the sweep is measuring something', () => {
  // Every assertion above iterates a list built by grepping the public
  // directory. If a rename or a restructure made those greps stop matching,
  // every loop would run zero times and this file would pass having checked
  // nothing — the exact shape of failure the repo's rules are written against.
  assert.ok(pages.length >= 30,
    `only ${pages.length} html pages found in public/ — the sweep is not `
    + 'seeing the site');
  assert.ok(withOg.length >= 20,
    `only ${withOg.length} pages declare OpenGraph — expected most of the `
    + 'public pages to');
  assert.ok(withFooter.length >= 2,
    `only ${withFooter.length} page(s) have a footer — if a footer was `
    + 'renamed, the visible-copy rule above is guarding nothing');
});
