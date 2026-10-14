'use strict';
/**
 * /docs serves docs/gitbook/, the folder the README links, page for page.
 *
 * The table of contents (SUMMARY.md) and the folder are held to each other:
 * a page in the folder that the table does not name is unreachable, and a
 * page the table names that is not there blanks the docs. Every link between
 * pages and every image resolves, so the website never prints a link that
 * leads nowhere. The renderer is escape-first and a link is printed only when
 * it goes somewhere the site can vouch for: each rule is driven both ways.
 * The page script fails in halves, and neither half is painted as "no docs".
 */
process.env.JWT_SECRET = 'j'.repeat(64);
delete process.env.DATABASE_URL;

const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const express = require('express');

const docs = require('../lib/docs_pages');
const page = require('../public/js/docs-page');

const APP = path.join(__dirname, '..');
const FOLDER = path.join(APP, '..', 'docs', 'gitbook');

function serve(router) {
  const app = express();
  app.use('/api/docs', router);
  const server = http.createServer(app);
  return new Promise((resolve) => server.listen(0, '127.0.0.1', () => resolve(server)));
}

function get(server, p) {
  return new Promise((resolve, reject) => {
    http.get({ host: '127.0.0.1', port: server.address().port, path: p }, (res) => {
      const chunks = [];
      res.on('data', (c) => chunks.push(c));
      res.on('end', () => {
        const buf = Buffer.concat(chunks);
        const type = String(res.headers['content-type'] || '');
        resolve({ status: res.statusCode, type, buf, body: type.includes('json') ? JSON.parse(buf.toString('utf8')) : null });
      });
    }).on('error', reject);
  });
}

/** Markdown with fenced blocks and code spans removed: what the renderer reads links from. */
function prose(src) {
  return src.replace(/^```[\s\S]*?^```\s*$/gm, '').replace(/`[^`\n]*`/g, '');
}

// ── the table and the folder ──────────────────────────────────────────────

test('the table of contents names every page in the folder, once, and every page it names is there', () => {
  const pages = docs.parseSummary(fs.readFileSync(path.join(FOLDER, 'SUMMARY.md'), 'utf8'));
  const named = pages.map((p) => p.file).sort();
  const inFolder = fs.readdirSync(FOLDER).filter((f) => f.endsWith('.md') && f !== 'SUMMARY.md').sort();
  assert.deepStrictEqual(named, inFolder);
  assert.strictEqual(pages[0].file, 'README.md', 'the folder\'s README is the front page, as GitHub shows it');
});

test('a table that names nothing, a page twice or a path out of the folder is unreadable, not empty', () => {
  assert.throws(() => docs.parseSummary('# Table of contents\n'), /names no page/);
  assert.throws(() => docs.parseSummary('* [A](faq.md)\n* [B](faq.md)\n'), /twice/);
  assert.throws(() => docs.parseSummary('* [A](../SECRETS.md)\n'), /not a page/);
  assert.throws(() => docs.parseSummary('* [A](sub/faq.md)\n'), /not a page/);
  assert.deepStrictEqual(docs.parseSummary('* [Intro](README.md)\n- [FAQ](faq.md)\n'),
    [{ slug: 'introduction', file: 'README.md', title: 'Intro' }, { slug: 'faq', file: 'faq.md', title: 'FAQ' }]);
});

test('every link between pages and every image in the docs resolves on the website', () => {
  const all = docs.listPages();
  const slugs = new Set(all.map((p) => p.slug));
  for (const p of all) {
    const src = prose(fs.readFileSync(path.join(FOLDER, p.file), 'utf8'));
    const mdLinks = (src.match(/(?<!!)\[[^\]]+\]\((?![a-z]+:)[^)\s]*\.md(#[^)\s]*)?\)/g) || []).length;
    const internal = p.html.match(/<a href="\/docs\/[^"]*"/g) || [];
    assert.strictEqual(internal.length, mdLinks, `${p.file}: ${mdLinks} links to pages, ${internal.length} printed`);
    for (const a of internal) {
      const slug = /\/docs\/([a-z0-9-]+)/.exec(a)[1];
      assert.ok(slugs.has(slug), `${p.file} links /docs/${slug}, which is no page`);
    }
    const images = (src.match(/!\[[^\]]*\]\(([^)\s]+)\)/g) || []).length;
    const printed = p.html.match(/<img src="\/api\/docs\/assets\/([^"]+)"/g) || [];
    assert.strictEqual(printed.length, images, `${p.file}: ${images} images, ${printed.length} printed`);
    for (const img of printed) {
      const name = /assets\/([^"]+)"/.exec(img)[1];
      assert.ok(docs.assetPath(name), `${p.file} shows ${name}, which the folder does not carry`);
    }
    assert.strictEqual(p.source, docs.SOURCE + p.file);
  }
  assert.ok(all.some((p) => /<img /.test(p.html)), 'the fixture has an image to check');
});

// ── the routes ────────────────────────────────────────────────────────────

test('the routes list the table in order, serve each page and its images, and 404 the rest', async () => {
  const server = await serve(require('../routes/docs'));
  try {
    const list = await get(server, '/api/docs/pages');
    assert.strictEqual(list.status, 200);
    const want = docs.parseSummary(fs.readFileSync(path.join(FOLDER, 'SUMMARY.md'), 'utf8'));
    assert.deepStrictEqual(list.body.pages, want.map(({ slug, title }) => ({ slug, title })));
    assert.strictEqual(list.body.folder, page.FOLDER, 'the route and the page link the same folder');
    for (const p of list.body.pages) {
      const one = await get(server, '/api/docs/pages/' + p.slug);
      assert.strictEqual(one.status, 200, p.slug);
      assert.ok(one.body.html.length > 100 && one.body.title === p.title, p.slug);
    }
    for (const bad of ['nope', 'README', 'SUMMARY', '..%2FSUMMARY', '%2E%2E']) {
      assert.strictEqual((await get(server, '/api/docs/pages/' + bad)).status, 404, bad);
    }
    const png = fs.readdirSync(FOLDER).find((f) => f.endsWith('.png'));
    const img = await get(server, '/api/docs/assets/' + png);
    assert.strictEqual(img.status, 200);
    assert.match(img.type, /^image\/png/);
    assert.ok(img.buf.equals(fs.readFileSync(path.join(FOLDER, png))));
    for (const bad of ['SUMMARY.md', 'README.md', 'nope.png', '..%2F..%2Fpackage.json', '%2E%2E%2Fdocs_pages.js']) {
      assert.strictEqual((await get(server, '/api/docs/assets/' + bad)).status, 404, bad);
    }
  } finally { server.close(); }
});

test('an unreadable folder is a 503 naming the class, on the list, a page and an image', async () => {
  const libPath = require.resolve('../lib/docs_pages');
  const routePath = require.resolve('../routes/docs');
  const real = require.cache[libPath];
  const boom = () => { throw Object.assign(new Error('ENOENT: no such file or directory'), { code: 'ENOENT' }); };
  delete require.cache[routePath];
  require.cache[libPath] = { id: libPath, filename: libPath, loaded: true,
    exports: { ...docs, listPages: boom, getPage: boom, assetPath: boom } };
  let router;
  try { router = require('../routes/docs'); } finally {
    require.cache[libPath] = real;
    delete require.cache[routePath];
  }
  const server = await serve(router);
  try {
    for (const p of ['/api/docs/pages', '/api/docs/pages/faq', '/api/docs/assets/x.png']) {
      const r = await get(server, p);
      assert.strictEqual(r.status, 503, p);
      assert.deepStrictEqual(r.body, { error: 'docs_unreadable', exception: 'Error' }, p);
    }
  } finally { server.close(); }
});

// ── the renderer ──────────────────────────────────────────────────────────

const ctx = { pagesByFile: new Map([['faq.md', 'faq'], ['README.md', 'introduction']]), assets: new Set(['shot.png']) };
const md = (s) => docs.renderMd(s, ctx);

test('markup in a doc is text, wherever it sits', () => {
  for (const src of ['<script>alert(1)</script>', '| <img src=x onerror=alert(1)> |\n|---|\n| a |',
    '> <iframe src="https://x">', '- <b onclick="x()">x</b>', '```\n<script>x</script>\n```',
    '[x](https://a.b/" onmouseover="alert(1))', '![x" onerror="alert(1)](shot.png)']) {
    const html = md(src);
    assert.ok(!/<(script|img src=x|iframe|b onclick)/.test(html), html);
    assert.ok(!/" on[a-z]+="/.test(html), html);
  }
  assert.ok(md('<script>').includes('&lt;script&gt;'));
});

test('a link is printed only where the site can vouch for it', () => {
  assert.strictEqual(md('[A](https://a.b/c?d=1&e=2)'),
    '<p><a href="https://a.b/c?d=1&amp;e=2" target="_blank" rel="noopener noreferrer">A</a></p>');
  assert.strictEqual(md('[FAQ](faq.md)'), '<p><a href="/docs/faq">FAQ</a></p>');
  assert.strictEqual(md('[FAQ](./faq.md#who-runs-it)'), '<p><a href="/docs/faq#who-runs-it">FAQ</a></p>');
  assert.strictEqual(md('[Home](README.md)'), '<p><a href="/docs/introduction">Home</a></p>');
  assert.strictEqual(md('[Part](#part-two)'), '<p><a href="#part-two">Part</a></p>');
  for (const url of ['javascript:void0', 'http://a.b', 'missing.md', '../TOKEN_ROADMAP.md', 'data:text/html,x', '//evil.io']) {
    assert.strictEqual(md(`[A **b**](${url})`), '<p>A <strong>b</strong></p>', url);
  }
  assert.strictEqual(md('![Shot](shot.png)'),
    '<p><img src="/api/docs/assets/shot.png" alt="Shot" loading="lazy" decoding="async"></p>');
  for (const url of ['https://evil.io/x.png', 'other.png', '../shot.png']) {
    assert.strictEqual(md(`![Shot](${url})`), '<p>Shot</p>', url);
  }
});

test('code is never read as markdown; emphasis and links outside it are', () => {
  assert.strictEqual(md('`**x** [a](faq.md)` and **y**'),
    '<p><code>**x** [a](faq.md)</code> and <strong>y</strong></p>');
  assert.strictEqual(md('```bash\n**x** [a](faq.md)\n```'),
    '<pre><code class="lang-bash">**x** [a](faq.md)</code></pre>');
  assert.strictEqual(md('a *b* c'), '<p>a <em>b</em> c</p>');
  assert.strictEqual(md('2 * 3 * 4'), '<p>2 * 3 * 4</p>');
  assert.strictEqual(md('[*b*](https://x.y/a*b*)'),
    '<p><a href="https://x.y/a*b*" target="_blank" rel="noopener noreferrer"><em>b</em></a></p>');
});

test('blocks: headings, lists, tables, quotes and rules', () => {
  assert.strictEqual(md('# One\n## Two\n### Two'),
    '<h2 id="one">One</h2>\n<h3 id="two">Two</h3>\n<h4 id="two-1">Two</h4>');
  assert.strictEqual(md('3. c\n4. d'), '<ol start="3"><li>c</li><li>d</li></ol>');
  assert.strictEqual(md('1. a\n2. b:\n - x\n - y\n3. c'),
    '<ol><li>a</li><li>b:<ul><li>x</li><li>y</li></ul></li><li>c</li></ol>');
  assert.strictEqual(md('- a\n  continues\n- [x] done\n- [ ] open'),
    '<ul><li>a continues</li><li><span class="md-task" role="img" aria-label="done">☑</span> done</li>'
    + '<li><span class="md-task" role="img" aria-label="not done">☐</span> open</li></ul>');
  assert.strictEqual(md('| A | B |\n|:--|--:|\n| `a|b` | **c** |\n| d |'),
    '<div class="md-table"><table><thead><tr><th>A</th><th class="al-r">B</th></tr></thead><tbody>'
    + '<tr><td><code>a|b</code></td><td class="al-r"><strong>c</strong></td></tr>'
    + '<tr><td>d</td><td class="al-r"></td></tr></tbody></table></div>');
  assert.strictEqual(md('> one\n> two\n>\n> - x'), '<blockquote><p>one two</p>\n<ul><li>x</li></ul></blockquote>');
  assert.strictEqual(md('a\n\n---\n\nb'), '<p>a</p>\n<hr>\n<p>b</p>');
  assert.strictEqual(md('```\nunclosed'), '<pre><code>unclosed</code></pre>');
});

// ── the page script ───────────────────────────────────────────────────────

const PAGES = [{ slug: 'introduction', title: 'Introduction' }, { slug: 'faq', title: 'FAQ <b>' }];

test('the page reads its slug from the path, and only a slug', () => {
  assert.strictEqual(page.slugFromPath('/docs'), '');
  assert.strictEqual(page.slugFromPath('/docs/'), '');
  assert.strictEqual(page.slugFromPath('/docs/faq'), 'faq');
  assert.strictEqual(page.slugFromPath('/docs/faq/'), 'faq');
  for (const p of ['/docs/FAQ', '/docs/..%2Fx', '/docs/a/b', '/docsx', '/', '/docs/%3Cb%3E']) {
    assert.strictEqual(page.slugFromPath(p), null, p);
  }
});

test('the contents: listed and escaped when read; a sentence and the GitHub folder when not, never an empty list', () => {
  const toc = page.tocHtml(page.readablePages({ pages: PAGES }), 'faq');
  assert.ok(toc.startsWith('<ol dir="ltr" lang="en">'), 'the titles are English');
  assert.ok(toc.includes('<a href="/docs/faq" aria-current="page">FAQ &lt;b&gt;</a>'));
  assert.ok(toc.includes('<a href="/docs/introduction">Introduction</a>'));
  for (const body of [null, {}, { pages: [] }, { pages: [{ slug: 'x' }] }, { pages: [{ slug: '../x', title: 'X' }] }]) {
    assert.strictEqual(page.readablePages(body), null, JSON.stringify(body));
  }
  const unread = page.tocHtml(null, 'faq');
  assert.ok(unread.includes(page.TOC_UNREAD) && unread.includes('href="' + page.FOLDER + '"'));
  assert.ok(!unread.includes('<ol'));
});

test('the page: the body when read, "no such page" on a 404, unreadable with the GitHub folder otherwise', () => {
  const ok = { status: 200, body: { slug: 'faq', title: 'FAQ', html: '<h2>FAQ</h2>', source: docs.SOURCE + 'faq.md' } };
  const html = page.pageHtml(ok, PAGES);
  assert.ok(html.startsWith('<div class="doc-body" dir="ltr" lang="en"><h2>FAQ</h2></div>'), 'English keeps its direction on a right-to-left page');
  assert.ok(html.includes('href="' + docs.SOURCE + 'faq.md"'));
  assert.ok(html.includes('href="/docs/introduction" rel="prev"'), 'the pager follows the table');
  assert.ok(!html.includes('rel="next"'), 'the last page has no next');
  const missing = page.pageHtml({ status: 404, body: { error: 'no_such_page' } }, PAGES);
  assert.ok(missing.includes(page.NO_PAGE) && !missing.includes('doc-body'));
  for (const res of [{ status: 503, body: { error: 'docs_unreadable' } }, { status: 0, body: null },
    { status: 200, body: { ...ok.body, source: 'https://evil.io/faq.md' } }, { status: 200, body: { ...ok.body, html: null } }]) {
    const out = page.pageHtml(res, PAGES);
    assert.ok(out.includes(page.PAGE_UNREAD) && out.includes('href="' + page.FOLDER + '"'), JSON.stringify(res));
    assert.ok(!out.includes('doc-body'), JSON.stringify(res));
  }
});

test('the website links its own docs page, and the page is served and mapped', () => {
  for (const f of ['index.html', 'explore.html']) {
    const html = fs.readFileSync(path.join(APP, 'public', f), 'utf8');
    const links = html.match(/<a [^>]*data-i18n="nav\.docs"[^>]*>/g) || [];
    assert.ok(links.length >= 2, f);
    for (const a of links) assert.ok(a.startsWith('<a href="/docs" '), `${f}: ${a}`);
  }
  const server = fs.readFileSync(path.join(APP, 'server.js'), 'utf8');
  assert.ok(server.includes("app.use('/api/docs', require('./routes/docs'))"));
  assert.ok(server.includes("app.get(['/docs', '/docs/:slug'], (req, res) => {"));
  assert.ok(server.includes("require('./lib/docs_pages').pageShell(req.params.slug)"));
  const { STATIC_PATHS } = require('../lib/sitemap');
  assert.ok(STATIC_PATHS.some((s) => s.path === '/docs'));
});

test('each docs address is titled and canonical as itself; an address with no page is a 404; an outage is not', () => {
  const plain = fs.readFileSync(path.join(APP, 'public', 'docs.html'), 'utf8');
  assert.deepStrictEqual(docs.pageShell(''), { status: 200, html: plain });
  assert.deepStrictEqual(docs.pageShell(undefined), { status: 200, html: plain });
  const faq = docs.listPages().find((p) => p.slug === 'faq');
  const one = docs.pageShell('faq');
  assert.strictEqual(one.status, 200);
  const canon = /<link rel="canonical" href="([^"]+)">/.exec(plain)[1];
  for (const tag of ['<link rel="canonical" href="' + canon + '/faq">', '<meta property="og:url" content="' + canon + '/faq">',
    '<title>' + faq.title + ' — RUNECLAW docs</title>', '<meta property="og:title" content="' + faq.title + ' — RUNECLAW docs">',
    '<meta name="twitter:title" content="' + faq.title + ' — RUNECLAW docs">']) {
    assert.strictEqual(one.html.split(tag).length - 1, 1, tag);
  }
  assert.ok(!one.html.includes('href="' + canon + '"'), 'no second canonical');
  assert.strictEqual(docs.pageShell('nope').status, 404);
  assert.strictEqual(docs.pageShell('../x').status, 404);
  const nowhere = { dir: path.join(APP, 'content', 'no-such-folder') };
  assert.throws(() => docs.listPages(nowhere), (e) => e.code === 'ENOENT');
  assert.deepStrictEqual(docs.pageShell('faq', nowhere), { status: 200, html: plain }, 'unreadable is not a 404');
});
