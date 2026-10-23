'use strict';

/**
 * The custodial-bot check on the website. Flag off is a 404 for the page and
 * the API. Flag on, the public document matches the Python catalog reader,
 * and a submission does not keep a Telegram id.
 */

const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { spawnSync } = require('node:child_process');
const express = require('express');

const ROOT = path.join(__dirname, '..', '..');
const check = require('../lib/botcheck');
const client = require('../public/js/botcheck.js');
const HTML = fs.readFileSync(path.join(__dirname, '..', 'content', 'botcheck.html'), 'utf8');
const CLIENT_SRC = fs.readFileSync(path.join(__dirname, '..', 'public', 'js', 'botcheck.js'), 'utf8');
const ROUTE_SRC = fs.readFileSync(path.join(__dirname, '..', 'routes', 'botcheck.js'), 'utf8');
const SUBMIT_SRC = fs.readFileSync(path.join(__dirname, '..', 'routes', 'botcheck_submit.js'), 'utf8');
const SERVER_SRC = fs.readFileSync(path.join(__dirname, '..', 'server.js'), 'utf8');
const CATALOG = fs.readFileSync(path.join(ROOT, 'bot', 'botcheck', 'catalog.json'), 'utf8');

const BANNED = /\b(?:safe|scam|verified|trusted)\b/i;
const MONEY = /\$|RCLAW|%|\bstake\b/i;

function python(code, args) {
  const proc = spawnSync('python3', ['-c', code].concat(args || []), {
    cwd: ROOT,
    encoding: 'utf8',
  });
  assert.equal(proc.status, 0, proc.stderr);
  return JSON.parse(proc.stdout);
}

test('the flag vocabulary matches the four on-words', () => {
  assert.equal(check.enabled({}), false);
  assert.equal(check.enabled({ BOTCHECK_ENABLED: '' }), false);
  for (const off of ['0', 'false', 'no', 'off', 'enabled', 'yes please']) {
    assert.equal(check.enabled({ BOTCHECK_ENABLED: off }), false, off);
  }
  for (const on of ['1', 'true', 'yes', 'on', 'ON', ' Yes ']) {
    assert.equal(check.enabled({ BOTCHECK_ENABLED: on }), true, on);
  }
});

test('the public document matches the Python reader', () => {
  const fromPython = python(
    'import json; from bot.botcheck import public_document; print(json.dumps(public_document()))'
  );
  assert.deepStrictEqual(check.publicDocument(), fromPython);
  const one = python(
    'import json,sys; from bot.botcheck import lookup; print(json.dumps(lookup(sys.argv[1])))',
    ['@StormTradeBot']
  );
  assert.deepStrictEqual(check.lookup('@StormTradeBot'), one);
  assert.equal(one.found, true);
  assert.equal(one.bot.summary.notice.includes('Art. 50'), true);
});

test('unknown stays unknown and a missing label is not invented', () => {
  const mevx = check.lookup('MEVX');
  assert.equal(mevx.found, true);
  for (const row of mevx.bot.signals) assert.equal(row.answer, 'unknown');
  assert.equal(client.answerClass('unknown'), 'ans muted');
  assert.equal(client.answerClass('yes'), 'ans');
  assert.equal(client.answerClass('no'), 'ans');
  assert.equal(client.httpsHref('http://example.com'), '');
  assert.equal(client.httpsHref('https://example.com/a b'), '');
  assert.equal(client.httpsHref('https://docs.example.com/a'), 'https://docs.example.com/a');
});

test('output, the catalog, the page, and the client avoid the forbidden words', () => {
  const doc = JSON.stringify(check.publicDocument());
  const blob = [CATALOG, HTML, CLIENT_SRC, ROUTE_SRC, SUBMIT_SRC, doc].join('\n');
  assert.equal(BANNED.test(blob), false);
  assert.equal(MONEY.test(blob), false);
  assert.equal(/<script(?![^>]*\bsrc=)/i.test(HTML), false);
  assert.equal(/\son[a-z]+=/i.test(HTML), false);
  assert.equal(HTML.includes('/styles.css?v=52'), true);
  assert.equal(HTML.includes('/js/botcheck.js?v=1'), true);
  assert.equal(ROUTE_SRC.includes('/styles.css?v=52'), true);
  assert.equal(ROUTE_SRC.includes('/js/botcheck.js?v=1'), true);
});

function listen(flag, opts) {
  const options = opts || {};
  const previous = process.env.BOTCHECK_ENABLED;
  const previousQueue = process.env.BOTCHECK_QUEUE_PATH;
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'botcheck-'));
  const queue = path.join(dir, 'review.jsonl');
  if (flag == null) delete process.env.BOTCHECK_ENABLED;
  else process.env.BOTCHECK_ENABLED = flag;
  process.env.BOTCHECK_QUEUE_PATH = queue;
  const app = express();
  // This harness only. Production decides req.ip from TRUSTED_PROXY.
  if (options.trustProxy) app.set('trust proxy', true);
  const route = require('../routes/botcheck');
  const submit = require('../routes/botcheck_submit');
  // Same order as server.js: the 1kb parser and its 413 answer run before
  // the global parser, and a parser error this answer does not take falls
  // through to a generic 500, which is what server.js does.
  app.use('/api/botcheck/submit', express.json({ limit: '1kb' }));
  app.use('/api/botcheck/submit', submit.rejectOversized);
  app.use(express.json({ limit: '1mb' }));
  app.get('/botcheck', route.page);
  app.use('/api/botcheck/submit', submit);
  app.use('/api/botcheck', route);
  app.use((err, req, res, next) => {
    res.status(500).json({ error: 'Internal server error' });
  });
  return new Promise((resolve) => {
    const server = app.listen(0, '127.0.0.1', () => {
      resolve({
        server: server,
        base: 'http://127.0.0.1:' + server.address().port,
        queue: queue,
        restore: function () {
          if (previous == null) delete process.env.BOTCHECK_ENABLED;
          else process.env.BOTCHECK_ENABLED = previous;
          if (previousQueue == null) delete process.env.BOTCHECK_QUEUE_PATH;
          else process.env.BOTCHECK_QUEUE_PATH = previousQueue;
        },
      });
    });
  });
}

async function stop(started) {
  await new Promise((resolve) => started.server.close(resolve));
  started.restore();
}

test('flag off is a 404 and writes nothing', async () => {
  const started = await listen(null);
  try {
    const page = await fetch(started.base + '/botcheck');
    assert.equal(page.status, 404);
    const list = await fetch(started.base + '/api/botcheck');
    assert.equal(list.status, 404);
    assert.deepStrictEqual(await list.json(), { error: 'not_found' });
    const posted = await fetch(started.base + '/api/botcheck/submit', {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ query: 'Fresh Bot', telegram_id: '9988776655' }),
    });
    assert.equal(posted.status, 404);
    assert.equal(fs.existsSync(started.queue), false);
  } finally {
    await stop(started);
  }
});

test('flag on lists, looks up, and queues without an id', async () => {
  const started = await listen('yes');
  try {
    const page = await fetch(started.base + '/botcheck');
    assert.equal(page.status, 200);
    assert.equal(page.headers.get('cache-control'), 'no-cache');
    const html = await page.text();
    assert.equal(html.includes('/js/botcheck.js?v=1'), true);
    const list = await fetch(started.base + '/api/botcheck');
    assert.equal(list.status, 200);
    assert.equal(list.headers.get('cache-control'), 'no-store');
    const doc = await list.json();
    assert.equal(doc.ok, true);
    assert.ok(doc.bots.length >= 20);
    const one = await fetch(started.base + '/api/botcheck?q=' + encodeURIComponent('freqtrade'));
    const body = await one.json();
    assert.equal(body.found, true);
    assert.equal(body.bot.summary.label, 'AI-written');
    const posted = await fetch(started.base + '/api/botcheck/submit', {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({
        query: 'Brand New Bot',
        note: 'a note',
        telegram_id: '9988776655',
        user_id: '9988776655',
      }),
    });
    assert.equal(posted.status, 200);
    const saved = await posted.json();
    assert.equal(saved.queued, true);
    const line = JSON.parse(fs.readFileSync(started.queue, 'utf8'));
    assert.deepStrictEqual(Object.keys(line).sort(), ['note', 'query', 'submitted_at', 'via']);
    assert.equal(line.query, 'Brand New Bot');
    assert.equal(line.via, 'web');
    assert.equal(JSON.stringify(line).includes('9988776655'), false);
    assert.equal(Object.prototype.hasOwnProperty.call(line, 'telegram_id'), false);
    const dup = await fetch(started.base + '/api/botcheck/submit', {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ query: 'totally safe' }),
    });
    const refused = await dup.json();
    assert.equal(refused.ok, false);
    assert.equal(JSON.stringify(refused).includes('safe'), false);
    assert.equal(fs.readFileSync(started.queue, 'utf8').trim().split('\n').length, 1);
  } finally {
    await stop(started);
  }
});

test('a name already curated is not appended', async () => {
  const started = await listen('1');
  try {
    const posted = await fetch(started.base + '/api/botcheck/submit', {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ query: 'OctoBot' }),
    });
    const body = await posted.json();
    assert.equal(body.queued, false);
    assert.equal(body.error, 'That name is already in the curated list. Nothing was added.');
    assert.equal(fs.existsSync(started.queue), false);
  } finally {
    await stop(started);
  }
});

test('the lookup module is the read and the submit module is the write', () => {
  assert.equal(/\.(?:post|put|delete|patch)\s*\(/.test(ROUTE_SRC), false);
  assert.equal(/\.get\s*\(/.test(SUBMIT_SRC), false);
  assert.match(SUBMIT_SRC, /name:\s*'botcheck\.submit'/);
  assert.match(SUBMIT_SRC, /max:\s*5/);
  assert.match(SUBMIT_SRC, /key:\s*ipKey/);
});

test('the 1kb parser is mounted before the global parser', () => {
  const cap = SERVER_SRC.indexOf("app.use('/api/botcheck/submit', express.json({ limit: '1kb' }))");
  const answer = SERVER_SRC.indexOf("require('./routes/botcheck_submit').rejectOversized");
  const wide = SERVER_SRC.indexOf("app.use(express.json({ limit: '1mb' }))");
  const sink = SERVER_SRC.indexOf("res.status(500).json({ error: 'Internal server error' })");
  const submitMount = SERVER_SRC.lastIndexOf("app.use('/api/botcheck/submit', require('./routes/botcheck_submit'))");
  const lookupMount = SERVER_SRC.indexOf("app.use('/api/botcheck', require('./routes/botcheck'))");
  assert.ok(cap > -1, 'the 1kb parser is not mounted');
  assert.ok(cap < wide, 'the 1kb parser must run before the global parser');
  assert.ok(answer > cap && answer < wide, 'the 413 answer must sit with the 1kb parser');
  assert.ok(answer < sink, 'the 413 answer must stand before the generic 500');
  assert.ok(submitMount > wide, 'the submit router must be mounted');
  assert.ok(lookupMount > submitMount, 'the lookup mount must not swallow the submit path');
});

function postAs(started, body, ip, raw) {
  const payload = raw != null ? raw : JSON.stringify(body);
  return fetch(started.base + '/api/botcheck/submit', {
    method: 'POST',
    headers: {
      'content-type': 'application/json',
      'x-forwarded-for': ip,
    },
    body: payload,
  });
}

test('the submit route limits each client IP on its own', async () => {
  const started = await listen('on', { trustProxy: true });
  try {
    for (let i = 1; i <= 5; i += 1) {
      const posted = await postAs(started, { query: 'Bucket A' + i }, '203.0.113.10');
      assert.equal(posted.status, 200, 'write ' + i);
    }
    const blocked = await postAs(started, { query: 'Bucket A6' }, '203.0.113.10');
    assert.equal(blocked.status, 429);
    assert.equal(blocked.headers.get('retry-after'), '60');
    const lines = fs.readFileSync(started.queue, 'utf8');
    assert.equal(lines.includes('Bucket A6'), false);
    assert.equal(lines.trim().split('\n').length, 5);
    const other = await postAs(started, { query: 'Bucket B1' }, '203.0.113.11');
    assert.equal(other.status, 200);
    const saved = await other.json();
    assert.equal(saved.queued, true);
    assert.equal(fs.readFileSync(started.queue, 'utf8').includes('Bucket B1'), true);
  } finally {
    await stop(started);
  }
});

test('a body over 1kb is refused and a short one is still queued', async () => {
  const started = await listen('yes', { trustProxy: true });
  try {
    const tooLong = JSON.stringify({ query: 'Cap Probe', note: 'n'.repeat(1100) });
    assert.ok(Buffer.byteLength(tooLong) > 1024);
    const refused = await postAs(started, null, '203.0.113.20', tooLong);
    assert.equal(refused.status, 413);
    const body = await refused.json();
    assert.equal(body.ok, false);
    assert.equal(JSON.stringify(body).includes('n'.repeat(20)), false);
    assert.equal(fs.existsSync(started.queue), false);
    const posted = await postAs(started, { query: 'Cap Ok Bot', note: 'hello' }, '203.0.113.21');
    assert.equal(posted.status, 200);
    const saved = await posted.json();
    assert.equal(saved.queued, true);
    const line = JSON.parse(fs.readFileSync(started.queue, 'utf8'));
    assert.equal(line.query, 'Cap Ok Bot');
    assert.equal(line.note, 'hello');
  } finally {
    await stop(started);
  }
});

test('a numeric id in the query or the note is refused and not stored', async () => {
  const started = await listen('true', { trustProxy: true });
  try {
    const queryId = '5550012345';
    const byQuery = await postAs(started, { query: queryId, note: 'a public page' }, '203.0.113.30');
    assert.equal(byQuery.status, 400);
    const queryBody = await byQuery.json();
    assert.equal(queryBody.queued, false);
    assert.equal(queryBody.error, 'Send a bot name, not a numeric id. Nothing was stored.');
    assert.equal(JSON.stringify(queryBody).includes(queryId), false);
    assert.equal(fs.existsSync(started.queue), false);
    const noteId = '5550098765';
    const byNote = await postAs(started, { query: 'Noted Bot', note: 'ref ' + noteId }, '203.0.113.31');
    assert.equal(byNote.status, 400);
    const noteBody = await byNote.json();
    assert.equal(noteBody.queued, false);
    assert.equal(noteBody.error, 'Send a bot name, not a numeric id. Nothing was stored.');
    assert.equal(JSON.stringify(noteBody).includes(noteId), false);
    assert.equal(fs.existsSync(started.queue), false);
  } finally {
    await stop(started);
  }
});
