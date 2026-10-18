'use strict';
/**
 * /privacy and /terms on the platform's own host.
 *
 * WHY THESE ARE HERE. Pi's Developer Portal will not list an app without a
 * privacy-policy URL and a terms-of-service URL (2026-10-17). The policy
 * existed, as a page of the marketing site (site/src/routes/privacy.tsx, built
 * into website/), and its canonical URL was https://www.humanoid-traders.com/
 * privacy. But www is this app, and this app had no /privacy: the canonical
 * URL of the one policy answered 404, as did /privacy on every other host.
 *
 * ONE SOURCE, TWO HOSTS. The documents are written once, in the site, where
 * site/test/privacy_truth.test.js and terms_truth.test.js check their claims
 * against the code. This route serves the ARTICLE of each built page, the
 * document itself, inside a plain shell. It does not rewrite the text, so the
 * two hosts cannot disagree, and a policy edit reaches www through the same
 * rebuild CI already requires.
 *
 * READ FROM app/content/site/, NOT website/. The web deploy ships `app/` and
 * nothing beside it (docs/DEPLOY_COMMANDS.md; the token record and the
 * lessons failed live the same way on 2026-10-07). The first version read
 * `../../website/` and answered every visitor with its 503 on the day it
 * shipped (2026-10-17). The copies are written by app/scripts/sync_content.js,
 * and app/test/the_website_carries_what_it_serves.test.js holds them
 * byte-identical to website/.
 *
 * WHY ONLY THE ARTICLE. The built page also carries the marketing site's
 * header and footer, whose links (/product, /risk, /changelog) are pages this
 * host does not serve, and the site's script bundle. The shell here links to
 * the two policies and nothing else: no script at all, no presale bar, no
 * door to the rest of the site. Pi's listing links these two URLs, and
 * nothing reachable from Pi may carry the presale (the operator's ruling,
 * 2026-10-16; see routes/piapp.js).
 *
 * FAIL CLOSED. A built page that is missing, has no single article, or whose
 * article carries a script, an inline handler or a style attribute (none of
 * which this page's policy would run) is a 503 that says the page is not
 * available here, never an empty 200 that reads as a policy with nothing in
 * it.
 */

const fs = require('fs');
const path = require('path');
const express = require('express');
const { frameAncestors } = require('./piapp');

const SITE_DIR = path.join(__dirname, '..', 'content', 'site');

/** The policies this host serves, by path. Each is a route of the site. */
const PAGES = ['privacy', 'terms'];

/** The site's stylesheet is content-hashed by vite; nothing else is served from its assets. */
const CSS_FILE = /^index\.[A-Za-z0-9_-]+\.css$/;

/** The shell's only links: the two policies, never the rest of the site. */
const NAV = [
  { href: '/privacy', label: 'Privacy' },
  { href: '/terms', label: 'Terms' },
];

class NotServable extends Error {}

/** Exactly one match of a /g pattern, or the page is not servable. */
function one(html, re, what) {
  const all = [...html.matchAll(re)];
  if (all.length !== 1) throw new NotServable(`${what}: found ${all.length}`);
  return all[0];
}

/**
 * Read one built page and keep what the shell needs. Throws NotServable for
 * anything this route will not put on the platform's host.
 */
function readPage(slug, { dir = SITE_DIR } = {}) {
  if (!PAGES.includes(slug)) throw new NotServable(`not a policy page: ${slug}`);
  let html;
  try {
    html = fs.readFileSync(path.join(dir, slug, 'index.html'), 'utf8');
  } catch (err) {
    throw new NotServable(`not built: ${err.code || err.name}`);
  }
  const article = one(html, /<article\b[^>]*>[\s\S]*?<\/article>/g, 'article')[0];
  if (/<script\b/i.test(article)) throw new NotServable('article carries a script');
  if (/\son[a-z]+\s*=/i.test(article)) throw new NotServable('article carries an inline handler');
  if (/\sstyle\s*=/i.test(article)) throw new NotServable('article carries a style attribute');
  const css = one(html, /<link rel="stylesheet"[^>]*\bhref="\/assets\/([^"]+)"/g, 'stylesheet')[1];
  if (!CSS_FILE.test(css)) throw new NotServable('unexpected stylesheet name');
  return {
    slug,
    title: one(html, /<title>([^<]*)<\/title>/g, 'title')[1],
    description: one(html, /<meta name="description" content="([^"]*)">/g, 'description')[1],
    canonical: one(html, /<link rel="canonical" href="(https:\/\/[^"]+)">/g, 'canonical')[1],
    css,
    article,
  };
}

/**
 * The shell. Title, description and canonical are the built page's, already
 * escaped by its build. Its classes are the site's, so the site's stylesheet
 * styles it; a test checks the stylesheet defines each one.
 */
function render(p) {
  const nav = NAV.map((n) => {
    const here = n.href === '/' + p.slug;
    return `<a href="${n.href}" class="${here ? 'text-ink' : 'text-ink-2 hover:text-ink'}"`
      + `${here ? ' aria-current="page"' : ''}>${n.label}</a>`;
  }).join('');
  return `<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>${p.title}</title>
<meta name="description" content="${p.description}">
<link rel="canonical" href="${p.canonical}">
<meta name="theme-color" content="#0a0b10">
<link rel="icon" type="image/svg+xml" href="/favicon.svg">
<link rel="stylesheet" href="/assets/${p.css}">
</head>
<body>
<header class="border-b border-line">
<nav class="mx-auto flex max-w-3xl items-center gap-6 px-5 py-3.5 text-sm" aria-label="Policies">
<span class="font-bold text-ink">RUNECLAW</span>${nav}
</nav>
</header>
<main id="main">${p.article}</main>
</body>
</html>`;
}

/** No script may run, no style may be inline, and nothing may be framed except by Pi's hosts. */
function legalCsp(env = process.env) {
  return [
    "default-src 'none'",
    "style-src 'self'",
    "img-src 'self' data:",
    "base-uri 'none'",
    "form-action 'none'",
    'frame-ancestors ' + frameAncestors(env),
  ].join('; ');
}

function sendPage(slug, opts) {
  return (req, res) => {
    let page;
    try {
      page = readPage(slug, opts);
    } catch (err) {
      if (!(err instanceof NotServable)) throw err;
      console.error(`legal page /${slug} not served: ${err.message}`);
      res.setHeader('Cache-Control', 'no-store');
      return res.status(503).type('text/plain')
        .send(`The ${slug} page is not available on this server right now.`);
    }
    res.removeHeader('X-Frame-Options');
    res.setHeader('Content-Security-Policy', legalCsp());
    res.setHeader('Cache-Control', 'no-cache');
    return res.type('html').send(render(page));
  };
}

/** The site's stylesheet, by its hashed name, and only that: the site's scripts stay off this host. */
function sendCss({ dir = SITE_DIR } = {}) {
  const assets = path.join(dir, 'assets');
  return (req, res, next) => {
    const name = req.params.file;
    if (!CSS_FILE.test(name)) return next();
    res.sendFile(name, {
      root: assets,
      dotfiles: 'deny',
      headers: { 'Cache-Control': 'public, max-age=31536000, immutable' },
    }, (err) => { if (err) next(); });
  };
}

/** `dir` is the built site; tests point it at a fixture. */
function create(opts = {}) {
  const r = express.Router();
  for (const slug of PAGES) r.get('/' + slug, sendPage(slug, opts));
  r.get('/assets/:file', sendCss(opts));
  return r;
}

module.exports = create();
module.exports.create = create;
module.exports.PAGES = PAGES;
module.exports.SITE_DIR = SITE_DIR;
module.exports.NAV = NAV;
module.exports.readPage = readPage;
module.exports.render = render;
module.exports.legalCsp = legalCsp;
module.exports.NotServable = NotServable;
