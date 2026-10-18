#!/usr/bin/env node
'use strict';
/**
 * Copy the repository files the website serves into app/content/.
 *
 *   node app/scripts/sync_content.js           # write the copies
 *   node app/scripts/sync_content.js --check   # exit 1 if any copy differs
 *
 * THE FINDING. The web deploy ships `app/` and nothing beside it. Two readers
 * reached past it: the $RCLAW record (`../../token/config/rclaw.mainnet.json`)
 * and the Study Room lessons (`../../docs/learn/`). On the live site the
 * first answered 503 `token_record_unreadable` with the bare class `Error`
 * (the file was not there), so /token painted "The token record could not be
 * read" with no mint; the second answered `{"lessons": []}`, a room with no
 * lessons in it.
 *
 * The originals stay where they are: the bot reads the record from
 * `token/config/` (`bot/token/record.py`), and the operator writes lessons in
 * `docs/learn/`. These are copies, written by this script and nothing else,
 * and `app/test/the_website_carries_what_it_serves.test.js` fails when one
 * differs from its original, so a record edited for the presale and not
 * synced fails CI instead of showing two mints.
 *
 * The documentation at /docs is the same shape: the README and the agent card
 * link `docs/gitbook/` on GitHub, and the website serves its copy under
 * `app/content/docs/` (`app/lib/docs_pages.js`).
 *
 * So are /privacy and /terms (`app/routes/legal.js`): the marketing site
 * builds them into `website/`, and the website serves each built page's
 * article, and the site's stylesheet, from `app/content/site/`. The route
 * first read `../../website/`, and answered 503 on the live host the day it
 * shipped (2026-10-17). After a site rebuild, run this script too.
 */

const fs = require('node:fs');
const path = require('node:path');

const REPO = path.join(__dirname, '..', '..');
const CONTENT = path.join(__dirname, '..', 'content');

/**
 * The folders the website mirrors: [folder in the repo, folder under
 * app/content, the file names it copies]. The copy of each is exact: a file
 * the original no longer has is a stray.
 */
const MIRRORS = [
  ['docs/learn', 'learn', /\.md$/],
  ['docs/gitbook', 'docs', /\.(md|png)$/],
  // The built policy pages, one folder per page, as routes/legal.js reads them.
  ['website/privacy', 'site/privacy', /^index\.html$/],
  ['website/terms', 'site/terms', /^index\.html$/],
  // The site's stylesheet only: its script bundle is not served by the website.
  ['website/assets', 'site/assets', /\.css$/],
];

/** [source relative to the repo, copy relative to app/content]. One reading. */
function plan({ repo = REPO } = {}) {
  const pairs = [['token/config/rclaw.mainnet.json', 'rclaw.mainnet.json']];
  for (const [from, to, keep] of MIRRORS) {
    for (const f of fs.readdirSync(path.join(repo, from)).filter((n) => keep.test(n)).sort()) {
      pairs.push([path.posix.join(from, f), path.posix.join(to, f)]);
    }
  }
  return pairs;
}

/** Copies that differ from, or are missing beside, their originals, plus strays. */
function drift({ repo = REPO, content = CONTENT } = {}) {
  const pairs = plan({ repo });
  const out = [];
  for (const [src, dst] of pairs) {
    const want = fs.readFileSync(path.join(repo, src));
    let have = null;
    try { have = fs.readFileSync(path.join(content, dst)); } catch (e) { have = null; }
    if (have === null) out.push(`missing app/content/${dst} (copy of ${src})`);
    else if (!have.equals(want)) out.push(`app/content/${dst} differs from ${src}`);
  }
  const expected = new Set(pairs.map(([, dst]) => dst));
  for (const [from, to] of MIRRORS) {
    let present = [];
    try { present = fs.readdirSync(path.join(content, to)).map((f) => path.posix.join(to, f)); } catch (e) { present = []; }
    for (const rel of present) {
      if (!expected.has(rel)) out.push(`app/content/${rel} has no original in ${from}`);
    }
  }
  return out;
}

function write({ repo = REPO, content = CONTENT } = {}) {
  const pairs = plan({ repo });
  const expected = new Set(pairs.map(([, dst]) => dst));
  for (const [, to] of MIRRORS) {
    fs.mkdirSync(path.join(content, to), { recursive: true });
    for (const f of fs.readdirSync(path.join(content, to))) {
      if (!expected.has(path.posix.join(to, f))) fs.unlinkSync(path.join(content, to, f));
    }
  }
  for (const [src, dst] of pairs) {
    fs.copyFileSync(path.join(repo, src), path.join(content, dst));
  }
  return pairs.length;
}

if (require.main === module) {
  if (process.argv.includes('--check')) {
    const d = drift();
    if (d.length) {
      console.error('app/content is out of date — run `node app/scripts/sync_content.js`:\n  '
        + d.join('\n  '));
      process.exit(1);
    }
    console.log('app/content matches its originals');
  } else {
    console.log(`copied ${write()} file(s) into app/content`);
  }
}

module.exports = { plan, drift, write, MIRRORS, CONTENT, REPO };
