'use strict';
/**
 * Recursion Breaker stays admin-only. The public card is coming soon,
 * with no date and no issuer. A thin reading is not a zero.
 */
process.env.JWT_SECRET = process.env.JWT_SECRET || 'j'.repeat(64);
delete process.env.DATABASE_URL;

const test = require('node:test');
const assert = require('node:assert');
const express = require('express');
const { flag_on } = require('../lib/rxb_flag');
const { loadCatalog, present } = require('../lib/tools_catalog');
const { sendTools } = require('../lib/tools_page');
const { sendRxb } = require('../lib/rxb_page');
const { readNode } = require('../lib/rxb_read');

function passAuth(req, res, next) { next(); }

async function listen(app) {
  return new Promise((resolve) => {
    const server = app.listen(0, '127.0.0.1', () => resolve(server));
  });
}

test('the flag is only 1 or true', () => {
  assert.equal(flag_on({ RXB_ENABLED: undefined }), false);
  assert.equal(flag_on({ RXB_ENABLED: 'yes' }), false);
  assert.equal(flag_on({ RXB_ENABLED: 'on' }), false);
  assert.equal(flag_on({ RXB_ENABLED: ' TRUE ' }), true);
  assert.equal(flag_on({ RXB_ENABLED: '1' }), true);
});

test('the public card is coming soon with no date and no issuer', () => {
  const card = loadCatalog()[0];
  const pub = present(card, false);
  const admin = present(card, true);
  assert.equal(pub.status_label, 'Coming soon');
  assert.equal(pub.href, null);
  assert.equal(pub.date, null);
  assert.equal(admin.href, '/rxb');
  assert.equal(admin.date, null);
  const blob = JSON.stringify(pub).toLowerCase();
  for (const word of ['xusd', 'deusd', 'elixir', 'stream', 'cbf', 'uf']) {
    assert.ok(!blob.includes(word), word);
  }
});

test('the tools page hides the admin link and the reading stays 404', async () => {
  const app = express();
  app.get('/tools', (req, res) => sendTools(req, res, {
    optionalAuth: passAuth,
    isOperator: async () => false,
  }));
  app.get('/rxb', (req, res) => sendRxb(req, res, {
    optionalAuth: passAuth,
    isOperator: async () => false,
    readPage: () => { throw new Error('public must not load the reading'); },
  }));
  const server = await listen(app);
  try {
    const tools = await fetch(`http://127.0.0.1:${server.address().port}/tools`);
    assert.equal(tools.status, 200);
    assert.match(tools.headers.get('cache-control'), /no-store/);
    const html = await tools.text();
    assert.match(html, /Coming soon/);
    assert.ok(!html.includes('/rxb'));
    assert.ok(!/xUSD|deUSD|Elixir|Stream|CBF|UF/.test(html));
    assert.ok(!/20\d\d-\d\d-\d\d|Q[1-4]/.test(html));
    const rxb = await fetch(`http://127.0.0.1:${server.address().port}/rxb`);
    assert.equal(rxb.status, 404);
    process.env.RXB_ENABLED = 'true';
    const still = await fetch(`http://127.0.0.1:${server.address().port}/rxb`);
    assert.equal(still.status, 404);
  } finally {
    delete process.env.RXB_ENABLED;
    server.close();
  }
});

test('an admin can read while the flag is off, in ratios', async () => {
  delete process.env.RXB_ENABLED;
  const app = express();
  app.get('/rxb', (req, res) => sendRxb(req, res, {
    optionalAuth: passAuth,
    isOperator: async () => true,
  }));
  app.get('/tools', (req, res) => sendTools(req, res, {
    optionalAuth: passAuth,
    isOperator: async () => true,
  }));
  const server = await listen(app);
  try {
    const page = await fetch(`http://127.0.0.1:${server.address().port}/rxb`);
    assert.equal(page.status, 200);
    assert.match(page.headers.get('cache-control'), /no-store/);
    const html = await page.text();
    assert.match(html, /setIsSentinel/);
    assert.match(html, /not an all-clear/);
    assert.match(html, /36\/53/);
    assert.ok(!html.includes('$'));
    const tools = await fetch(`http://127.0.0.1:${server.address().port}/tools`);
    const card = await tools.text();
    assert.match(card, /href="\/rxb"/);
    assert.match(card, /Coming soon/);
  } finally {
    server.close();
  }
});

test('the node reading is the solver and it does not invent a zero', () => {
  const view = readNode('xUSD');
  assert.equal(view.cbf, '36/53');
  assert.equal(view.price.price_wad, null);
  assert.equal(view.tripped, true);
  const missing = readNode('sdeUSD');
  assert.equal(missing.readable, false);
  assert.equal(missing.cbf, null);
  const text = JSON.stringify(view);
  assert.ok(!/"usd"|\$/.test(text));
});
