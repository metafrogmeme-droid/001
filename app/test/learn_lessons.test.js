'use strict';
/**
 * Study Room lessons — the contract under test:
 * - Lessons are repo-shipped markdown (docs/learn/*.md): slug from filename,
 *   title from the first heading, order from the file order.
 * - Rendering is ESCAPE-FIRST: lesson source can never inject markup beyond
 *   the small allowed vocabulary.
 * - Reading is public; progress is per-account, upsert-safe, and the first
 *   done_at stands.
 * - The starter lessons teach only what the platform actually does, and
 *   every one ends on the education-not-advice line.
 */
process.env.JWT_SECRET = 'j'.repeat(64);
delete process.env.DATABASE_URL;

const test = require('node:test');
const assert = require('node:assert');
const http = require('node:http');
const fs = require('node:fs');
const path = require('node:path');
const express = require('express');
const jwt = require('jsonwebtoken');
const { pool } = require('../db');
const lessons = require('../lib/learn_lessons');

let server, base, tok;

function req(method, p, tokIn) {
  return new Promise((resolve, reject) => {
    const r = http.request(`${base}${p}`, {
      method,
      headers: tokIn ? { Authorization: `Bearer ${tokIn}` } : {},
    }, (res) => {
      let d = '';
      res.on('data', (c) => d += c);
      res.on('end', () => resolve({ status: res.statusCode, data: d ? JSON.parse(d) : {} }));
    });
    r.on('error', reject);
    r.end();
  });
}

test.before(async () => {
  await pool.execute('INSERT INTO users (email, password_hash) VALUES (?, ?)',
    ['lessons@test.io', 'x'.repeat(60)]);
  const [u] = await pool.execute('SELECT * FROM users WHERE email = ?', ['lessons@test.io']);
  tok = jwt.sign({ user_id: u[0].id, email: u[0].email }, process.env.JWT_SECRET);
  const app = express();
  app.use(express.json());
  app.use('/api/learn', require('../routes/learn'));
  await new Promise((res) => { server = app.listen(0, '127.0.0.1', res); });
  base = `http://127.0.0.1:${server.address().port}`;
});

test.after(() => { if (server) server.close(); });

test('the starter lessons exist, ordered, titled from their headings', () => {
  const all = lessons.listLessons();
  assert.ok(all.length >= 3, 'the room opens with at least three lessons');
  assert.deepEqual(all.slice(0, 3).map((l) => l.slug),
    ['stops-and-risk', 'exit-discipline', 'bridges-gas-dust']);
  assert.match(all[0].title, /Stops and risk/);
  for (const l of all) {
    assert.match(l.html, /Education, not investment advice|not investment advice/i,
      `${l.slug} must end on the education-not-advice line`);
  }
});

test('escape-first rendering: lesson markdown can never smuggle markup', () => {
  const html = lessons.renderMd('# T\n\nA <script>alert(1)</script> **bold** *i* line.\n\n- item <img src=x>\n\n    code <b>\n');
  assert.ok(!html.includes('<script>'), 'raw tags are escaped before any transform');
  assert.ok(html.includes('&lt;script&gt;'));
  assert.ok(html.includes('<b>bold</b>'));
  assert.ok(html.includes('<i>i</i>'));
  assert.ok(html.includes('<li>item &lt;img src=x&gt;</li>'));
  assert.ok(html.includes('<pre><code>code &lt;b&gt;</code></pre>'));
  assert.ok(html.includes('<h2>T</h2>'), 'lesson # maps to h2 — the page owns h1');
});

test('public reads: list and content need no token; unknown slugs 404', async () => {
  const list = await req('GET', '/api/learn/lessons');
  assert.equal(list.status, 200);
  assert.ok(list.data.lessons.length >= 3);
  assert.ok(!('html' in list.data.lessons[0]), 'the list is light — content ships per lesson');
  assert.match(list.data.note, /English for now/);
  const one = await req('GET', '/api/learn/lessons/stops-and-risk');
  assert.equal(one.status, 200);
  assert.match(one.data.html, /<h2>/);
  const miss = await req('GET', '/api/learn/lessons/nope');
  assert.equal(miss.status, 404);
});

test('progress: authed, upsert-safe, first done_at stands, unknown slug refused', async () => {
  const anon = await req('POST', '/api/learn/lessons/stops-and-risk/done');
  assert.equal(anon.status, 401);
  const anonList = await req('GET', '/api/learn/progress');
  assert.equal(anonList.status, 401);

  const d1 = await req('POST', '/api/learn/lessons/stops-and-risk/done', tok);
  assert.equal(d1.status, 200);
  const d2 = await req('POST', '/api/learn/lessons/stops-and-risk/done', tok);
  assert.equal(d2.status, 200, 'marking twice is a no-op, never a duplicate-key 500');
  const p = await req('GET', '/api/learn/progress', tok);
  assert.deepEqual(p.data.done, ['stops-and-risk']);
  const bad = await req('POST', '/api/learn/lessons/nope/done', tok);
  assert.equal(bad.status, 404);
});

test('the page wires lessons: list, reader, progress, mark-as-read — chrome through T()', () => {
  const page = fs.readFileSync(path.join(__dirname, '..', 'public', 'learn.html'), 'utf8');
  assert.match(page, /\/api\/learn\/lessons/);
  assert.match(page, /ln\.lessons_note/, 'the English-content note is stated, not hidden');
  assert.match(page, /ln\.mark_read/);
  assert.match(page, /escape-first renderer/, 'the one trusted-html surface is documented in place');
  const i18n = require('../public/js/i18n.js');
  for (const k of ['ln.lessons_h', 'ln.lessons_note', 'ln.progress', 'ln.mark_read', 'ln.back']) {
    const en = String(i18n.STRINGS[k].en || '');
    const slots = [...en.matchAll(/\{(\w+)\}/g)].map((m) => m[0]);
    for (const l of i18n.LANGS) {
      const s = String(i18n.STRINGS[k][l.code] || '');
      assert.ok(s.trim().length, `${k} missing ${l.code}`);
      for (const slot of slots) assert.ok(s.includes(slot), `${k} lost ${slot} in ${l.code}`);
    }
  }
});
