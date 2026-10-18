'use strict';
/**
 * /privacy and /terms answer on www, from the site's build, and carry nothing
 * else.
 *
 * Pi's Developer Portal will not list the app without both URLs (2026-10-17),
 * and until this route both were 404 on every host: the policy's canonical
 * URL named www, and www is this app. routes/legal.js serves the article of
 * each built page (website/<slug>/index.html) in a plain shell. These tests
 * hold four promises, each from what the server sends:
 *
 *   The document is the site's, byte for byte. The text is written once, in
 *   site/, where the truth tests check it against the code.
 *
 *   No script, no presale, no door. Pi's listing links these pages, and
 *   nothing reachable from Pi may carry the presale (routes/piapp.js). The
 *   only own-origin links are the two policies, the stylesheet and the icon.
 *
 *   Unreadable is never an empty policy. A missing build, or an article the
 *   page's policy would not run as written, is a 503 with a sentence.
 *
 *   The stylesheet and nothing else. The site's script bundle stays off this
 *   host, and so does any path outside the assets folder.
 */
const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const http = require('node:http');
const express = require('express');
const { codeOnly } = require('./helpers/code_only');

const APP = path.join(__dirname, '..');
const WEBSITE = path.join(APP, '..', 'website');
const legal = require('../routes/legal');

async function serve(router, p) {
  const app = express();
  app.use(express.static(path.join(APP, 'public')));
  app.use(router);
  const server = http.createServer(app);
  await new Promise((r) => server.listen(0, '127.0.0.1', r));
  try {
    const r = await fetch(`http://127.0.0.1:${server.address().port}${p}`, { redirect: 'manual' });
    return { status: r.status, headers: r.headers, text: await r.text() };
  } finally {
    server.close();
  }
}

const directive = (csp, name) => (csp.split(';').map((d) => d.trim()).find((d) => d.startsWith(name + ' ')) || '');
const built = (slug) => fs.readFileSync(path.join(WEBSITE, slug, 'index.html'), 'utf8');
const articleOf = (html) => html.match(/<article\b[^>]*>[\s\S]*?<\/article>/)[0];
const PRESALE = /presale|\$?RCLAW|smithii|\/token\b/i;

for (const slug of legal.PAGES) {
  test(`/${slug} is the site's built article, byte for byte`, async () => {
    const r = await serve(legal, '/' + slug);
    assert.strictEqual(r.status, 200);
    assert.match(r.headers.get('content-type'), /^text\/html/);
    assert.ok(r.text.includes(articleOf(built(slug))), `/${slug} is not the built document`);
    assert.strictEqual((r.text.match(/<article\b/g) || []).length, 1);
    // The built page's own title and canonical, so a crawler sees one document.
    assert.ok(r.text.includes(built(slug).match(/<title>[^<]*<\/title>/)[0]));
    assert.ok(r.text.includes(built(slug).match(/<link rel="canonical"[^>]*>/)[0]));
  });

  test(`/${slug} keeps its contact address out of Cloudflare's email rewriting`, async () => {
    // Live on 2026-10-18 Cloudflare's Email Address Obfuscation turned the
    // address into "[email protected]" plus a decoder script this page's CSP
    // refuses. Its email_off markers, around the whole article, leave it be.
    const r = await serve(legal, '/' + slug);
    const article = articleOf(built(slug));
    assert.ok(r.text.includes('<!--email_off-->' + article + '<!--/email_off-->'),
      `/${slug}'s article is not inside the email_off markers`);
    assert.match(article, /info@humanoid-traders\.com/, 'the fixture names no address to protect');
    assert.strictEqual((r.text.match(/<!--email_off-->/g) || []).length, 1);
    assert.strictEqual((r.text.match(/<!--\/email_off-->/g) || []).length, 1);
  });

  test(`/${slug} runs no script, carries no presale, and links only to the policies`, async () => {
    const r = await serve(legal, '/' + slug);
    assert.doesNotMatch(r.text, /<script\b/i);
    assert.doesNotMatch(r.text, /\son[a-z]+\s*=/i, 'an inline handler');
    assert.doesNotMatch(r.text, PRESALE, `/${slug} names the sale or the token page`);
    const own = [...r.text.matchAll(/(?:href|src)="(\/[^"]*)"/g)].map((m) => m[1]);
    const allowed = (u) => u === '/privacy' || u === '/terms' || u === '/favicon.svg'
      || /^\/assets\/index\.[A-Za-z0-9_-]+\.css$/.test(u);
    assert.deepStrictEqual(own.filter((u) => !allowed(u)), [],
      `/${slug} opens a door to the rest of the site`);
    assert.ok(own.includes('/privacy') && own.includes('/terms'), 'the two policies link each other');
  });

  test(`/${slug}: no inline script or style may run, framable by Pi's hosts only`, async () => {
    const r = await serve(legal, '/' + slug);
    const csp = r.headers.get('content-security-policy');
    assert.strictEqual(directive(csp, 'default-src'), "default-src 'none'");
    assert.strictEqual(directive(csp, 'style-src'), "style-src 'self'");
    assert.strictEqual(directive(csp, 'script-src'), '', 'no script source at all');
    assert.doesNotMatch(csp, /unsafe-inline|unsafe-eval/);
    assert.doesNotMatch(r.text, /\sstyle\s*=/i, 'a style attribute this policy would drop');
    const { frameAncestors } = require('../routes/piapp');
    assert.strictEqual(directive(csp, 'frame-ancestors'), 'frame-ancestors ' + frameAncestors());
    assert.strictEqual(r.headers.get('x-frame-options'), null);
    assert.match(r.headers.get('cache-control'), /no-cache/);
  });
}

test('the shell is styled by the site\'s stylesheet: each class it uses is defined there', async () => {
  const r = await serve(legal, '/privacy');
  const shell = r.text.replace(/<article\b[\s\S]*?<\/article>/, '');
  const classes = new Set([...shell.matchAll(/class="([^"]+)"/g)].flatMap((m) => m[1].split(/\s+/)));
  assert.ok(classes.size >= 8, `read ${classes.size} classes; the parse is wrong`);
  const css = fs.readFileSync(path.join(WEBSITE, 'assets', legal.readPage('privacy').css), 'utf8');
  const esc = (c) => c.replace(/[:.[\]()/]/g, (ch) => '\\' + ch);
  const missing = [...classes].filter((c) => !css.includes('.' + esc(c)));
  assert.deepStrictEqual(missing, [], 'the site\'s build does not emit these; the shell renders unstyled');
});

test('the stylesheet is served by its hashed name, immutable; the scripts and other paths are not', async () => {
  const name = legal.readPage('terms').css;
  const css = await serve(legal, '/assets/' + name);
  assert.strictEqual(css.status, 200);
  assert.match(css.headers.get('content-type'), /^text\/css/);
  assert.match(css.headers.get('cache-control'), /immutable/);
  const js = fs.readdirSync(path.join(WEBSITE, 'assets')).find((f) => f.endsWith('.js'));
  assert.ok(js, 'the fixture needs the site bundle to prove it is refused');
  assert.strictEqual((await serve(legal, '/assets/' + js)).status, 404, 'the site\'s script reached www');
  assert.strictEqual((await serve(legal, '/assets/index.nothere.css')).status, 404);
  assert.strictEqual((await serve(legal, '/assets/..%2Fprivacy%2Findex.html')).status, 404);
});

// ── unreadable is never an empty policy ───────────────────────────────────

function fixture(files) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'legal-'));
  for (const [rel, body] of Object.entries(files)) {
    fs.mkdirSync(path.dirname(path.join(dir, rel)), { recursive: true });
    fs.writeFileSync(path.join(dir, rel), body);
  }
  return dir;
}

const GOOD = (article) => `<!DOCTYPE html><html><head><title>Privacy — RUNECLAW</title>
<meta name="description" content="d">
<link rel="canonical" href="https://www.humanoid-traders.com/privacy">
<link rel="stylesheet" crossorigin href="/assets/index.abc.css"></head>
<body><div id="root"><main>${article}</main></div></body></html>`;

test('a page this host cannot serve as written is a 503 with a sentence, never an empty 200', async () => {
  const cases = {
    'not built': {},
    'a script in the article': { 'privacy/index.html': GOOD('<article><script>x()</script></article>') },
    'an inline handler': { 'privacy/index.html': GOOD('<article><p onclick="x()">a</p></article>') },
    'a style attribute': { 'privacy/index.html': GOOD('<article><h1 style="color:red">a</h1></article>') },
    'two articles': { 'privacy/index.html': GOOD('<article>a</article><article>b</article>') },
  };
  const logged = [];
  const orig = console.error;
  console.error = (...a) => logged.push(a.join(' '));
  try {
    for (const [why, files] of Object.entries(cases)) {
      const r = await serve(legal.create({ dir: fixture(files) }), '/privacy');
      assert.strictEqual(r.status, 503, why);
      assert.match(r.text, /not available on this server/, why);
      assert.match(r.headers.get('cache-control'), /no-store/, why);
    }
  } finally {
    console.error = orig;
  }
  assert.strictEqual(logged.length, Object.keys(cases).length, 'each refusal is logged with its reason');
  // And the other arm: the same fixture with a clean article is served.
  const ok = await serve(legal.create({ dir: fixture({ 'privacy/index.html': GOOD('<article><p>a</p></article>') }) }), '/privacy');
  assert.strictEqual(ok.status, 200);
  assert.match(ok.text, /<article><p>a<\/article>|<article><p>a<\/p><\/article>/);
});

test('server.js mounts the policies, and no static file shadows them', () => {
  const server = codeOnly(fs.readFileSync(path.join(APP, 'server.js'), 'utf8'));
  assert.match(server, /app\.use\(require\('\.\/routes\/legal'\)\)/);
  // express.static mounts first: a public/privacy.html would answer instead
  // of the built policy, and drift from it.
  for (const f of ['privacy.html', 'terms.html', 'privacy', 'terms', 'assets']) {
    assert.ok(!fs.existsSync(path.join(APP, 'public', f)), `public/${f} would shadow the route`);
  }
  assert.ok(fs.existsSync(path.join(APP, 'public', 'favicon.svg')), 'the shell names /favicon.svg');
});
