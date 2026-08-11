'use strict';
/**
 * One canonical host, on every page, and it is the product's own.
 *
 * Every page shipped its canonical, og:url and og:image pointing at the
 * hosting-provider hostname the site was born on. Both hosts serve identical
 * content — verified live, same /api/version on each — so search engines saw
 * the whole site twice and the link equity split between them, while every
 * share card and unfurl pointed at the deployment URL rather than the brand.
 *
 * This is metadata consumed ENTIRELY outside the server: a crawler's index, a
 * card unfurled in someone else's timeline. Nothing in a request can validate
 * it, which is the same reason `app/lib/public_origin.js` exists — and why it
 * cannot help here, since these are static files with no request to resolve
 * against. So the constant is pinned instead.
 *
 * The test is per-page rather than a repo-wide grep because the failure mode
 * is a NEW page copy-pasted from an old one: the grep passes on the 34 files
 * somebody remembered and says nothing about the 35th.
 */
const test = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const path = require('path');

const PUBLIC = path.join(__dirname, '..', 'public');
const HOST = 'https://www.humanoid-traders.com';
//: The hosting-provider hostname. Still a live origin and still the proxy
//: target in proxy.js — this is about which host gets INDEXED, not which one
//: answers.
const DEPLOY_HOST = 'pmvc58g2.mule.page';

const pages = fs.readdirSync(PUBLIC).filter((f) => f.endsWith('.html'));

test('there are pages to check at all', () => {
  assert.ok(pages.length > 20, `only found ${pages.length} pages — did the path move?`);
});

test('no page points its canonical or social metadata at the deployment host', () => {
  const offenders = [];
  for (const page of pages) {
    const html = fs.readFileSync(path.join(PUBLIC, page), 'utf8');
    for (const m of html.matchAll(/(canonical|og:url|og:image|twitter:image)[^>]*?(https?:\/\/[^"']+)/g)) {
      if (m[2].includes(DEPLOY_HOST)) offenders.push(`${page}: ${m[1]} -> ${m[2]}`);
    }
  }
  assert.deepStrictEqual(offenders, [],
    'these split indexing between two hosts serving identical content');
});

test('every canonical that exists uses exactly one host', () => {
  const hosts = new Set();
  for (const page of pages) {
    const html = fs.readFileSync(path.join(PUBLIC, page), 'utf8');
    const m = html.match(/<link rel="canonical" href="(https?:\/\/[^"/]+)/);
    if (m) hosts.add(m[1]);
  }
  assert.deepStrictEqual([...hosts], [HOST],
    `canonical hosts must agree; found ${[...hosts].join(', ')}`);
});

test('a canonical is absolute — a relative one silently self-canonicalises', () => {
  const bad = [];
  for (const page of pages) {
    const html = fs.readFileSync(path.join(PUBLIC, page), 'utf8');
    const m = html.match(/<link rel="canonical" href="([^"]*)"/);
    if (m && !/^https?:\/\//.test(m[1])) bad.push(`${page}: ${m[1]}`);
  }
  assert.deepStrictEqual(bad, [],
    'a relative canonical resolves against whichever host served it, which is '
    + 'the duplicate-content problem the tag exists to solve');
});

test('og:url and canonical agree per page', () => {
  const disagree = [];
  for (const page of pages) {
    const html = fs.readFileSync(path.join(PUBLIC, page), 'utf8');
    const c = html.match(/<link rel="canonical" href="([^"]+)"/);
    const o = html.match(/<meta property="og:url" content="([^"]+)"/);
    if (c && o && c[1] !== o[1]) disagree.push(`${page}: ${c[1]} vs ${o[1]}`);
  }
  assert.deepStrictEqual(disagree, [],
    'a share card that unfurls a different URL than the indexed one splits the '
    + 'same page in two again, one layer down');
});
