'use strict';
/**
 * Marketplace integrity — discovery you cannot game, counts you can trust.
 * The contract:
 * - the public list orders by FIRST publish date (created_at for legacy rows
 *   — a real date, never an invented one): a re-save buys nothing, and no
 *   publish/unpublish cycle earns a fresher position;
 * - legacy rows keep published_at NULL — back-filling one would fabricate a
 *   date the platform never observed;
 * - search/filter run server-side; a typo'd enum is a 400, never a silently
 *   dropped filter;
 * - follower counts are DISTINCT-account facts, decorating cards only when
 *   readable (omitted, never zeroed, on a failed read);
 * - a follow burns a finite slot, so unknown slugs are refused — but
 *   "unknown_agent" is an absence claim, made only when the catalogue
 *   actually answered; an unreadable catalogue refuses with 503 instead.
 */
process.env.JWT_SECRET = process.env.JWT_SECRET || 'j'.repeat(64);
delete process.env.DATABASE_URL;

const test = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const path = require('path');
const http = require('http');
const express = require('express');
const jwt = require('jsonwebtoken');
const { pool } = require('../db');
const store = require('../lib/user_strategies');
const { _resetCache } = require('../lib/follow_counts');

let server, base, token, uid;

function req(method, p, body, tok) {
  return new Promise((resolve, reject) => {
    const data = body ? JSON.stringify(body) : null;
    const r = http.request(`${base}${p}`, { method,
      headers: { 'Content-Type': 'application/json',
        ...(tok === null ? {} : { Authorization: `Bearer ${tok || token}` }),
        ...(data ? { 'Content-Length': Buffer.byteLength(data) } : {}) } }, (res) => {
      const chunks = [];
      res.on('data', (c) => chunks.push(c));
      res.on('end', () => resolve({ status: res.statusCode, body: JSON.parse(Buffer.concat(chunks) || '{}') }));
    });
    r.on('error', reject);
    if (data) r.write(data);
    r.end();
  });
}

test.before(async () => {
  const [u] = await pool.execute(
    'INSERT INTO users (email, password_hash, created_at) VALUES (?, ?, NOW())',
    ['integrity@test.dev', 'x'.repeat(60)]);
  uid = u.insertId;
  token = jwt.sign({ user_id: uid, email: 'integrity@test.dev' }, process.env.JWT_SECRET);
  const app = express();
  app.use(express.json());
  app.use('/api/strategies', require('../routes/user_strategies'));
  app.use('/api/public/user-strategies', require('../routes/public_user_strategies'));
  app.use('/api/copy', require('../routes/copy'));
  await new Promise((res) => { server = app.listen(0, '127.0.0.1', res); });
  base = `http://127.0.0.1:${server.address().port}`;
});
test.after(() => { if (server) server.close(); });

async function makePublished(name, extra, tok) {
  const c = await req('POST', '/api/strategies', {
    name, tagline: (extra && extra.tagline) || '', regime: (extra && extra.regime) || '',
    horizon: (extra && extra.horizon) || '',
    rules: [{ type: 'max_position_pct', value: 5 }] }, tok);
  assert.strictEqual(c.status, 200, JSON.stringify(c.body));
  const mine = await req('GET', '/api/strategies', null, tok);
  const row = mine.body.strategies.find((s) => s.slug === c.body.slug);
  const pub = await req('POST', `/api/strategies/${row.dbId}/publish`, null, tok);
  assert.strictEqual(pub.status, 200, 'publish: ' + JSON.stringify(pub.body));
  return { slug: c.body.slug, dbId: row.dbId };
}

test('ordering is by first publish — a re-save buys nothing', async () => {
  const a = await makePublished('Alpha Original');
  await new Promise((r) => setTimeout(r, 15));
  const b = await makePublished('Beta Later');
  // Re-save A (edit) — under updated_at ordering this would resurface it.
  await req('PUT', `/api/strategies/${a.dbId}`, {
    name: 'Alpha Original', tagline: 'edited', rules: [{ type: 'max_position_pct', value: 4 }] });
  const list = await req('GET', '/api/public/user-strategies', null, null);
  const idx = (slug) => list.body.agents.findIndex((x) => x.slug === slug);
  assert.ok(idx(b.slug) < idx(a.slug),
    'the later publish stays first; editing never resurfaces');
});

test('a publish/unpublish cycle keeps the FIRST publish date', async () => {
  const a = await makePublished('Cycle Test');
  const [before] = await pool.execute(
    'SELECT * FROM user_strategies WHERE id = ? AND user_id = ?', [a.dbId, uid]);
  const stamp1 = before[0].published_at;
  assert.ok(stamp1, 'publish stamped published_at');
  await new Promise((r) => setTimeout(r, 15));
  await req('POST', `/api/strategies/${a.dbId}/unpublish`);
  await req('POST', `/api/strategies/${a.dbId}/publish`);
  const [after] = await pool.execute(
    'SELECT * FROM user_strategies WHERE id = ? AND user_id = ?', [a.dbId, uid]);
  assert.strictEqual(String(after[0].published_at), String(stamp1),
    'COALESCE keeps the first date — cycling earns nothing');
});

test('legacy rows are never back-filled; the DDL and ALTER say NULL', () => {
  const db = fs.readFileSync(path.join(__dirname, '..', 'db.js'), 'utf8');
  assert.match(db, /published_at TIMESTAMP NULL DEFAULT NULL/);
  assert.match(db, /ADD COLUMN published_at TIMESTAMP NULL DEFAULT NULL/);
  assert.match(db, /inventing a publish date they never had would be a fabricated number/);
  const lib = fs.readFileSync(path.join(__dirname, '..', 'lib', 'user_strategies.js'), 'utf8');
  assert.match(lib, /COALESCE\(published_at, created_at\) DESC/,
    'legacy rows sort by created_at — a real date');
});

test('server-side search and validated enum filters', async () => {
  await makePublished('Quantum Fader', { tagline: 'fade the edges', regime: 'range', horizon: 'intraday' });
  await makePublished('Trend Walker', { regime: 'trend_up', horizon: 'swing' });
  const q = await req('GET', '/api/public/user-strategies?q=quantum', null, null);
  assert.strictEqual(q.body.agents.length, 1);
  assert.strictEqual(q.body.agents[0].name, 'Quantum Fader');
  const rg = await req('GET', '/api/public/user-strategies?regime=range', null, null);
  assert.ok(rg.body.agents.every((x) => x.regime === 'range'));
  assert.ok(rg.body.agents.some((x) => x.name === 'Quantum Fader'));
  const bad = await req('GET', '/api/public/user-strategies?regime=moonphase', null, null);
  assert.strictEqual(bad.status, 400, 'a typo is a 400, never a silently dropped filter');
  assert.match(bad.body.error, /moonphase/);
});

test('follower counts are distinct-account facts on public cards', async () => {
  // The main account is at the 5-public cap (a real product limit the earlier
  // tests exercised into place) — the counted strategy belongs to user 2.
  const [u2] = await pool.execute(
    'INSERT INTO users (email, password_hash, created_at) VALUES (?, ?, NOW())',
    ['integrity2@test.dev', 'x'.repeat(60)]);
  const tok2 = jwt.sign({ user_id: u2.insertId, email: 'integrity2@test.dev' }, process.env.JWT_SECRET);
  const a = await makePublished('Counted Strategy', null, tok2);
  // Two distinct accounts follow; one follows twice (idempotent — still 1).
  const f1 = await req('POST', '/api/copy/follow', { agent_id: a.slug });
  assert.strictEqual(f1.status, 200, JSON.stringify(f1.body) + ' slug=' + a.slug);
  const f2 = await req('POST', '/api/copy/follow', { agent_id: a.slug });
  assert.strictEqual(f2.status, 200, JSON.stringify(f2.body));
  const f3 = await req('POST', '/api/copy/follow', { agent_id: a.slug }, tok2);
  assert.strictEqual(f3.status, 200, 'u2 follow: ' + JSON.stringify(f3.body));
  _resetCache();
  const list = await req('GET', '/api/public/user-strategies?q=counted', null, null);
  assert.strictEqual(list.body.agents[0].followers, 2, 'distinct accounts, not raw rows');
});

test('a follow of an unknown slug is refused — the slot is not burned', async () => {
  const r = await req('POST', '/api/copy/follow', { agent_id: 'never-existed-abc' });
  assert.strictEqual(r.status, 404);
  assert.strictEqual(r.body.error, 'unknown_agent');
  // and the honesty split is pinned in source: absence claims need a readable
  // catalogue; unreadable refuses 503 instead of guessing.
  //
  // `stale beats blind` was asserted here and is a COMMENT. Dropped rather
  // than replaced: the two lines above it are code, and they are the property
  // — a 404 is only reachable when the catalogue was readable, and an
  // unreadable one has its own refusal code. The comment explains why; it does
  // not enforce anything, and pinning it made a reworded sentence look like a
  // regression in the gate.
  const src = fs.readFileSync(path.join(__dirname, '..', 'routes', 'copy.js'), 'utf8');
  assert.match(src, /catalogue_unreadable/);
  assert.match(src, /if \(cat\.readable\) return res\.status\(404\)/);
});

test('counts decorate, never fabricate: omitted on unreadable, engine cards too', async () => {
  // THE LOAD-BEARING LINE WAS MATCHED WITH ITS COMMENT ATTACHED:
  //
  //     assert.match(fc, /return null; {3}\/\/ unreadable ≠ zero/)
  //
  // Three literal spaces and a comment. Reformatting that line broke the test
  // on unchanged behaviour, and — the direction that costs — `return 0;` with
  // the comment left in place would have been just as red for the wrong
  // reason, teaching the next person to loosen the pattern rather than look.
  //
  // It is a pure function with a cache reset exported. Drive it.
  const { pool } = require('../db');
  const counts = require('../lib/follow_counts');
  const real = pool.execute.bind(pool);
  counts._resetCache();
  pool.execute = async (sql, params) => {
    if (/FROM copy_subscriptions/i.test(sql)) throw new Error('injected');
    return real(sql, params);
  };
  let unreadable;
  try { unreadable = await counts.followerCounts(); } finally { pool.execute = real; }
  counts._resetCache();

  assert.strictEqual(unreadable, null,
    'an unreadable follower count came back as a value — `{}` or `0` here puts '
    + '"0 followers" on every card in the marketplace, which is a measurement '
    + 'nobody took');

  // And the decoration is genuinely omitted rather than zeroed downstream.
  const cards = [{ slug: 'a' }, { slug: 'b' }];
  const real2 = pool.execute.bind(pool);
  counts._resetCache();
  pool.execute = async (sql, params) => {
    if (/FROM copy_subscriptions/i.test(sql)) throw new Error('injected');
    return real2(sql, params);
  };
  try { await counts.decorate(cards); } finally { pool.execute = real2; }
  counts._resetCache();
  for (const c of cards) {
    assert.ok(!('followers' in c),
      'a card carries a followers key when the count could not be read');
  }

  const ps = fs.readFileSync(path.join(__dirname, '..', 'routes', 'public_strategies.js'), 'utf8');
  assert.match(ps, /decorate\(r\.data\.agents\)/, 'engine catalogue cards carry counts too');
});

test('published community strategies join the sitemap', async () => {
  const server_ = fs.readFileSync(path.join(__dirname, '..', 'server.js'), 'utf8');
  assert.match(server_, /agents = agents\.concat\(await require\('\.\/lib\/user_strategies'\)\.listPublic\(\)\)/);
  const { buildSitemap } = require('../lib/sitemap');
  const cards = await store.listPublic();
  const xml = buildSitemap('https://x.dev', cards);
  assert.ok(cards.length > 0);
  assert.ok(xml.includes(`/agents/${cards[0].slug}`), 'community slug in the sitemap');
});

test('the UI shows counts as facts (never a sort) and i18n carries the keys', () => {
  const agents = fs.readFileSync(path.join(__dirname, '..', 'public', 'agents.html'), 'utf8');
  assert.match(agents, /followersLine\(a\.followers\)/);
  assert.match(agents, /A count is a fact, not a ranking/);
  const dash = fs.readFileSync(path.join(__dirname, '..', 'public', 'js', 'dashboard.js'), 'utf8');
  assert.match(dash, /a\.followers \?/);
  const i18n = fs.readFileSync(path.join(__dirname, '..', 'public', 'js', 'i18n.js'), 'utf8');
  assert.ok(i18n.includes("'mk.followers'"));
  assert.ok(i18n.includes("'mk.following_w'"));
});
