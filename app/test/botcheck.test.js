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
  const blob = [CATALOG, HTML, CLIENT_SRC, ROUTE_SRC, doc].join('\n');
  assert.equal(BANNED.test(blob), false);
  assert.equal(MONEY.test(blob), false);
  assert.equal(/<script(?![^>]*\bsrc=)/i.test(HTML), false);
  assert.equal(/\son[a-z]+=/i.test(HTML), false);
  assert.equal(HTML.includes('/styles.css?v=52'), true);
  assert.equal(HTML.includes('/js/botcheck.js?v=1'), true);
  assert.equal(ROUTE_SRC.includes('/styles.css?v=52'), true);
  assert.equal(ROUTE_SRC.includes('/js/botcheck.js?v=1'), true);
});

function listen(flag) {
  const previous = process.env.BOTCHECK_ENABLED;
  const previousQueue = process.env.BOTCHECK_QUEUE_PATH;
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'botcheck-'));
  const queue = path.join(dir, 'review.jsonl');
  if (flag == null) delete process.env.BOTCHECK_ENABLED;
  else process.env.BOTCHECK_ENABLED = flag;
  process.env.BOTCHECK_QUEUE_PATH = queue;
  const app = express();
  app.use(express.json());
  const route = require('../routes/botcheck');
  app.get('/botcheck', route.page);
  app.use('/api/botcheck', route);
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
