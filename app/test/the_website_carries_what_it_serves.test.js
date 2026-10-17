'use strict';
/**
 * The website reads only what a deploy of `app/` carries.
 *
 * THE FINDING (live, 2026-10-07). The web deploy ships `app/` and nothing
 * beside it. `GET /api/token` answered 503 `token_record_unreadable` with the
 * bare class `Error`, because `rclaw_token.js` read
 * `../../token/config/rclaw.mainnet.json`, a file the deploy never had; /token
 * painted "The token record could not be read" with no mint. `GET
 * /api/learn/lessons` answered `{"lessons": []}` for the same reason, and the
 * room showed no lessons as if there were none.
 *
 * The originals stay where the bot and the operator use them. The website
 * reads copies under `app/content/`, written by `app/scripts/sync_content.js`,
 * and this file holds the copies to the originals. A shelf that cannot be
 * read is now a 503 and a sentence, never an empty list.
 */
process.env.JWT_SECRET = 'j'.repeat(64);
delete process.env.DATABASE_URL;

const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const http = require('node:http');
const express = require('express');

const sync = require('../scripts/sync_content');

const APP = path.join(__dirname, '..');
const REPO = path.join(APP, '..');

function tmp(prefix) {
  return fs.mkdtempSync(path.join(os.tmpdir(), prefix));
}

// ── the copies are the originals ──────────────────────────────────────────

test('every copy under app/content is byte-identical to its original', () => {
  assert.deepStrictEqual(sync.drift(), [],
    'run `node app/scripts/sync_content.js` and commit app/content');
  const pairs = sync.plan();
  assert.ok(pairs.some(([src]) => src === 'token/config/rclaw.mainnet.json'));
  const lessonsInRepo = fs.readdirSync(path.join(REPO, 'docs', 'learn')).filter((f) => f.endsWith('.md'));
  assert.ok(lessonsInRepo.length >= 3);
  assert.strictEqual(pairs.filter(([src]) => src.startsWith('docs/learn/')).length, lessonsInRepo.length);
  const docsInRepo = fs.readdirSync(path.join(REPO, 'docs', 'gitbook')).filter((f) => /\.(md|png)$/.test(f));
  assert.ok(docsInRepo.includes('SUMMARY.md') && docsInRepo.some((f) => f.endsWith('.png')));
  assert.strictEqual(pairs.filter(([src]) => src.startsWith('docs/gitbook/')).length, docsInRepo.length);
  // The policies and the one stylesheet their built pages name.
  for (const slug of ['privacy', 'terms']) {
    assert.ok(pairs.some(([src, dst]) => src === `website/${slug}/index.html` && dst === `site/${slug}/index.html`), slug);
    const css = fs.readFileSync(path.join(REPO, 'website', slug, 'index.html'), 'utf8')
      .match(/<link rel="stylesheet"[^>]*href="\/assets\/([^"]+)"/)[1];
    assert.ok(pairs.some(([src]) => src === `website/assets/${css}`), `${slug} names ${css}, which is not copied`);
  }
  assert.ok(!pairs.some(([src]) => /^website\/assets\/.*\.js$/.test(src)), 'the site\'s script bundle is not the website\'s to serve');
});

test('the drift check names a missing copy, a changed copy and a stray', () => {
  const repo = tmp('repo-');
  fs.mkdirSync(path.join(repo, 'token', 'config'), { recursive: true });
  fs.mkdirSync(path.join(repo, 'docs', 'learn'), { recursive: true });
  fs.mkdirSync(path.join(repo, 'docs', 'gitbook'), { recursive: true });
  fs.writeFileSync(path.join(repo, 'token', 'config', 'rclaw.mainnet.json'), '{"a":1}\n');
  fs.writeFileSync(path.join(repo, 'docs', 'learn', '01-x.md'), '# X\n');
  fs.writeFileSync(path.join(repo, 'docs', 'gitbook', 'SUMMARY.md'), '* [X](README.md)\n');
  fs.writeFileSync(path.join(repo, 'docs', 'gitbook', 'notes.txt'), 'not served\n');
  for (const d of ['privacy', 'terms', 'assets']) fs.mkdirSync(path.join(repo, 'website', d), { recursive: true });
  fs.writeFileSync(path.join(repo, 'website', 'privacy', 'index.html'), '<article>p</article>\n');
  fs.writeFileSync(path.join(repo, 'website', 'terms', 'index.html'), '<article>t</article>\n');
  fs.writeFileSync(path.join(repo, 'website', 'assets', 'index.abc.css'), 'a{}\n');
  fs.writeFileSync(path.join(repo, 'website', 'assets', 'index.abc.js'), 'x()\n');
  const content = tmp('content-');

  const before = sync.drift({ repo, content });
  assert.strictEqual(before.length, 6, before.join('\n'));
  assert.ok(before.every((l) => l.startsWith('missing ')));

  sync.write({ repo, content });
  assert.deepStrictEqual(sync.drift({ repo, content }), []);

  fs.writeFileSync(path.join(content, 'rclaw.mainnet.json'), '{"a":2}\n');
  fs.writeFileSync(path.join(content, 'learn', '99-stray.md'), '# Stray\n');
  fs.writeFileSync(path.join(content, 'docs', 'gone.md'), '# Gone\n');
  assert.ok(!fs.existsSync(path.join(content, 'docs', 'notes.txt')), 'only .md and .png are copied');
  assert.ok(!fs.existsSync(path.join(content, 'site', 'assets', 'index.abc.js')), 'only the stylesheet is copied');
  const after = sync.drift({ repo, content });
  assert.ok(after.some((l) => /rclaw\.mainnet\.json differs/.test(l)), after.join('\n'));
  assert.ok(after.some((l) => /99-stray\.md has no original in docs\/learn/.test(l)), after.join('\n'));
  assert.ok(after.some((l) => /docs\/gone\.md has no original in docs\/gitbook/.test(l)), after.join('\n'));

  sync.write({ repo, content });
  assert.deepStrictEqual(sync.drift({ repo, content }), [], 'write removes the stray too');
});

// ── the deploy shape that failed: app/ with nothing beside it ─────────────

function appOnly({ withContent }) {
  const root = tmp('app-only-');
  const app = path.join(root, 'app');
  fs.mkdirSync(path.join(app, 'lib'), { recursive: true });
  for (const f of ['rclaw_token.js', 'learn_lessons.js', 'docs_pages.js']) {
    fs.copyFileSync(path.join(APP, 'lib', f), path.join(app, 'lib', f));
  }
  if (withContent) fs.cpSync(path.join(APP, 'content'), path.join(app, 'content'), { recursive: true });
  return {
    rclaw: require(path.join(app, 'lib', 'rclaw_token.js')),
    lessons: require(path.join(app, 'lib', 'learn_lessons.js')),
    docs: require(path.join(app, 'lib', 'docs_pages.js')),
  };
}

test('a deploy of app/ alone reads the token record, the lessons and the docs', () => {
  const { rclaw, lessons, docs } = appOnly({ withContent: true });
  assert.strictEqual(docs.listPages()[0].slug, 'introduction');
  assert.strictEqual(rclaw.readRecord().mint, 'rupKpYsgk6em6xx4V9E4oGN9Bvo9FQWQd71qBK2CaNe');
  const n = fs.readdirSync(path.join(REPO, 'docs', 'learn')).filter((f) => f.endsWith('.md')).length;
  assert.strictEqual(lessons.listLessons().length, n);
});

test('without its content, the same deploy refuses: no record, no shelf, no empty list', () => {
  const { rclaw, lessons, docs } = appOnly({ withContent: false });
  assert.throws(() => docs.listPages(), (e) => e.code === 'ENOENT');
  assert.throws(() => docs.listPages(), (e) => e.code === 'ENOENT', 'a failure is not cached');
  assert.throws(() => rclaw.readRecord(), (e) => e.code === 'ENOENT' && e.name === 'Error',
    'the live 503 named the class Error: this is that read');
  assert.throws(() => lessons.listLessons(), (e) => e.code === 'ENOENT');
  assert.throws(() => lessons.listLessons(), (e) => e.code === 'ENOENT', 'a failure is not cached');
});

test('an empty shelf that did read is an empty list, not a refusal', () => {
  const { lessons } = appOnly({ withContent: true });
  const dir = lessons.LESSONS_DIR;
  for (const f of fs.readdirSync(dir)) fs.unlinkSync(path.join(dir, f));
  lessons.resetCache();
  assert.deepStrictEqual(lessons.listLessons(), []);
});

// ── the policies, the same deploy shape (live, 2026-10-17) ─────────────────

/**
 * /privacy and /terms first read the built site from `../../website/`, and
 * the live host answered both with the route's own 503 the day they shipped:
 * the deploy had `app/` and nothing beside it, which this file's header had
 * recorded ten days earlier. The route reads `app/content/site/` now.
 */
async function getText(router, p) {
  const app = express();
  app.use(router);
  const server = http.createServer(app);
  await new Promise((r) => server.listen(0, '127.0.0.1', r));
  try {
    const r = await fetch(`http://127.0.0.1:${server.address().port}${p}`);
    return { status: r.status, text: await r.text() };
  } finally { server.close(); }
}

test('the policies are read from inside app/, one copied folder per page', () => {
  const legal = require('../routes/legal');
  const rel = path.relative(APP, legal.SITE_DIR);
  assert.ok(!rel.startsWith('..') && !path.isAbsolute(rel),
    `routes/legal.js reads ${legal.SITE_DIR}, which a deploy of app/ does not carry`);
  assert.strictEqual(legal.SITE_DIR, path.join(sync.CONTENT, 'site'));
  for (const slug of legal.PAGES) {
    assert.ok(sync.MIRRORS.some(([from, to]) => from === `website/${slug}` && to === `site/${slug}`),
      `/${slug} is served, but sync_content.js does not copy its page`);
  }
});

test('a deploy of app/ alone serves /privacy and /terms; without the copies it refuses', async () => {
  const legal = require('../routes/legal');
  const root = tmp('app-only-');
  fs.cpSync(path.join(APP, 'content'), path.join(root, 'app', 'content'), { recursive: true });
  const served = legal.create({ dir: path.join(root, 'app', 'content', 'site') });
  for (const slug of legal.PAGES) {
    const r = await getText(served, '/' + slug);
    assert.strictEqual(r.status, 200, slug);
    assert.match(r.text, /<article\b/, slug);
  }
  const css = legal.readPage('privacy', { dir: path.join(root, 'app', 'content', 'site') }).css;
  assert.strictEqual((await getText(served, '/assets/' + css)).status, 200);

  const bare = tmp('app-bare-');
  const orig = console.error;
  console.error = () => {};
  try {
    const r = await getText(legal.create({ dir: path.join(bare, 'app', 'content', 'site') }), '/privacy');
    assert.strictEqual(r.status, 503);
    assert.match(r.text, /not available on this server/);
  } finally { console.error = orig; }
});

// ── the routes say "unreadable" ───────────────────────────────────────────

function withLessonsStub(stub, mount) {
  const libPath = require.resolve('../lib/learn_lessons');
  const real = require.cache[libPath];
  const routePath = require.resolve(mount);
  delete require.cache[routePath];
  require.cache[libPath] = { id: libPath, filename: libPath, loaded: true, exports: stub };
  try {
    return require(mount);
  } finally {
    if (real) require.cache[libPath] = real; else delete require.cache[libPath];
    delete require.cache[routePath];
  }
}

function serve(prefix, router) {
  const app = express();
  app.use(express.json());
  app.use(prefix, router);
  const server = http.createServer(app);
  return new Promise((resolve) => server.listen(0, '127.0.0.1', () => resolve(server)));
}

function get(server, p, tok) {
  return new Promise((resolve, reject) => {
    http.get({ host: '127.0.0.1', port: server.address().port, path: p,
      headers: tok ? { Authorization: `Bearer ${tok}` } : {} }, (res) => {
      let b = ''; res.on('data', (c) => { b += c; });
      res.on('end', () => resolve({ status: res.statusCode, body: b ? JSON.parse(b) : {} }));
    }).on('error', reject);
  });
}

const enoent = () => Object.assign(new Error('ENOENT: no such file or directory'), { code: 'ENOENT' });
const unreadShelf = {
  listLessons: () => { throw enoent(); },
  getLesson: () => { throw enoent(); },
  renderMd: (s) => s, resetCache: () => {}, LESSONS_DIR: '/nowhere',
};

test('an unreadable shelf is a 503 naming the class, on the list and on a lesson', async () => {
  const server = await serve('/api/learn', withLessonsStub(unreadShelf, '../routes/learn'));
  try {
    for (const p of ['/api/learn/lessons', '/api/learn/lessons/stops-and-risk']) {
      const r = await get(server, p);
      assert.strictEqual(r.status, 503, p);
      assert.deepStrictEqual(r.body, { error: 'lessons_unreadable', exception: 'Error' }, p);
    }
  } finally { server.close(); }
});

test('a readable shelf lists its lessons and serves one', async () => {
  const server = await serve('/api/learn', require('../routes/learn'));
  try {
    const r = await get(server, '/api/learn/lessons');
    assert.strictEqual(r.status, 200);
    assert.ok(r.body.lessons.length >= 3);
    const one = await get(server, '/api/learn/lessons/' + r.body.lessons[0].slug);
    assert.strictEqual(one.status, 200);
    assert.ok(one.body.html);
  } finally { server.close(); }
});

test('the command deck says the lesson total was not read, and grants no scholar', async () => {
  const jwt = require('jsonwebtoken');
  const { pool } = require('../db');
  await pool.execute('INSERT INTO users (email, password_hash) VALUES (?, ?)',
    ['shelf@test.io', 'x'.repeat(60)]);
  const [u] = await pool.execute('SELECT * FROM users WHERE email = ?', ['shelf@test.io']);
  const tok = jwt.sign({ user_id: u[0].id, email: u[0].email }, process.env.JWT_SECRET);

  const unread = await serve('/api/command', withLessonsStub(unreadShelf, '../routes/command'));
  try {
    const r = await get(unread, '/api/command', tok);
    assert.strictEqual(r.status, 200);
    assert.strictEqual(r.body.study.lessons_total, null);
    const by = Object.fromEntries(r.body.achievements.map((a) => [a.id, a.unlocked]));
    assert.strictEqual(by.scholar, false);
  } finally { unread.close(); }

  const read = await serve('/api/command', require('../routes/command'));
  try {
    const r = await get(read, '/api/command', tok);
    assert.ok(r.body.study.lessons_total >= 3);
  } finally { read.close(); }
});

// ── the pages paint the refusal ───────────────────────────────────────────

test('the Study Room paints the unread sentence instead of an empty shelf', () => {
  const src = fs.readFileSync(path.join(APP, 'public', 'learn.html'), 'utf8');
  const start = src.indexOf('async function loadLessons()');
  assert.ok(start > 0);
  const body = src.slice(start, src.indexOf('loadLessons();', start));
  assert.match(body, /if \(!resp\.ok \|\| !r \|\| !Array\.isArray\(r\.lessons\)\)/);
  assert.match(body, /ln\.lessons_unread/);
  assert.doesNotMatch(body, /\(r && r\.lessons\) \|\| \[\]/, 'the empty-list fallback is gone');
});

test('the command deck prints a dash for a total it did not read', () => {
  const src = fs.readFileSync(path.join(APP, 'public', 'command.html'), 'utf8');
  assert.match(src, /d\.study\.lessons_total == null \? '—' : d\.study\.lessons_total/);
});
