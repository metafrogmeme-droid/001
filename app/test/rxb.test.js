'use strict';
/**
 * Recursion Breaker is a public signal while RXB_ENABLED is on.
 * While the flag is off, the public gets 404 and an admin can still read.
 * Scores are signals, not verdicts, and the public page has no dollar amount.
 */
process.env.JWT_SECRET = process.env.JWT_SECRET || 'j'.repeat(64);
delete process.env.DATABASE_URL;

const test = require('node:test');
const assert = require('node:assert');
const express = require('express');
const { flag_on } = require('../lib/rxb_flag');
const catalog = require('../lib/tools_catalog');
const { sendTools } = require('../lib/tools_page');
const { sendRxb } = require('../lib/rxb_page');
const { readNode } = require('../lib/rxb_read');
const rxbRoutes = require('../routes/rxb');

function passAuth(req, res, next) { next(); }

async function listen(app) {
  return new Promise((resolve) => {
    const server = app.listen(0, '127.0.0.1', () => resolve(server));
  });
}

function appFor(operator) {
  const app = express();
  app.set('rxb', { isOperator: async () => operator });
  app.get('/tools', (req, res) => sendTools(req, res, {
    optionalAuth: passAuth,
    isOperator: async () => operator,
  }));
  app.get('/rxb', (req, res) => sendRxb(req, res, {
    optionalAuth: passAuth,
    isOperator: async () => operator,
  }));
  app.use('/v1/node', rxbRoutes);
  return app;
}

test('the flag is only 1 or true', () => {
  assert.equal(flag_on({ RXB_ENABLED: undefined }), false);
  assert.equal(flag_on({ RXB_ENABLED: 'yes' }), false);
  assert.equal(flag_on({ RXB_ENABLED: 'on' }), false);
  assert.equal(flag_on({ RXB_ENABLED: ' TRUE ' }), true);
  assert.equal(flag_on({ RXB_ENABLED: '1' }), true);
});

test('the card stays closed until the flag and never names an issuer', () => {
  const card = catalog.loadCatalog().find((row) => row.id === 'recursion-breaker');
  const pub = catalog.present(card, false);
  const admin = catalog.present(card, true);
  assert.equal(pub.status_label, 'Coming soon');
  assert.equal(pub.href, null);
  assert.equal(pub.date, null);
  assert.equal(admin.href, '/rxb');
  assert.equal(admin.date, null);
  assert.deepStrictEqual(catalog.presentation(card, {}), { label: 'Coming soon', href: null });
  assert.deepStrictEqual(
    catalog.presentation(card, { RXB_ENABLED: '1' }),
    { label: 'Open', href: '/rxb' },
  );
  const blob = JSON.stringify(pub).toLowerCase();
  for (const word of ['xusd', 'deusd', 'elixir', 'stream', 'cbf', 'uf']) {
    assert.ok(!blob.includes(word), word);
  }
});

test('the flag off hides the reading from the public and an admin can test it', async () => {
  delete process.env.RXB_ENABLED;
  const closed = await listen(appFor(false));
  const admin = await listen(appFor(true));
  try {
    const tools = await fetch(`http://127.0.0.1:${closed.address().port}/tools`);
    assert.equal(tools.status, 200);
    const html = await tools.text();
    assert.match(html, /Coming soon/);
    assert.ok(!html.includes('href="/rxb"'));
    assert.ok(!/xUSD|deUSD|Elixir|Stream|CBF|UF/.test(html));
    assert.ok(!/20\d\d-\d\d-\d\d|Q[1-4]/.test(html));
    const rxb = await fetch(`http://127.0.0.1:${closed.address().port}/rxb`);
    assert.equal(rxb.status, 404);
    const node = await fetch(`http://127.0.0.1:${closed.address().port}/v1/node/xUSD`);
    assert.equal(node.status, 404);
    const page = await fetch(`http://127.0.0.1:${admin.address().port}/rxb`);
    assert.equal(page.status, 200);
    assert.match(page.headers.get('cache-control'), /no-store/);
    const body = await page.text();
    assert.match(body, /Admin test/);
    assert.match(body, /public gets 404/);
    assert.match(body, /setIsSentinel/);
    assert.match(body, /not an all-clear/);
    assert.match(body, /36\/53/);
    assert.match(body, /2025-10-29/);
    assert.match(body, /6 days before 2025-11-04/);
    assert.match(body, /USD3 history starts 2025-11-20/);
    assert.ok(!body.includes('$'));
    const card = await (await fetch(`http://127.0.0.1:${admin.address().port}/tools`)).text();
    assert.match(card, /href="\/rxb"/);
    assert.match(card, /Coming soon/);
  } finally {
    closed.close();
    admin.close();
  }
});

test('the flag on publishes a signal and still refuses a dollar amount', async () => {
  process.env.RXB_ENABLED = 'true';
  const server = await listen(appFor(false));
  try {
    const page = await fetch(`http://127.0.0.1:${server.address().port}/rxb`);
    assert.equal(page.status, 200);
    assert.match(page.headers.get('cache-control'), /no-store/);
    const html = await page.text();
    assert.match(html, /Public signal, not a verdict/);
    assert.match(html, /not an all-clear/);
    assert.match(html, /36\/53/);
    assert.match(html, /<td>xUSD<\/td>[\s\S]*?<td>Fired<\/td>[\s\S]*?<td>34\/265<\/td>[\s\S]*?<td>36\/53<\/td>[\s\S]*?<td>0\/1<\/td>\s*<td><\/td>/);
    assert.match(html, /no_lookthrough_inputs/);
    assert.match(html, /unsigned/);
    assert.match(html, /not activated/);
    assert.ok(!html.includes('setIsSentinel'));
    assert.ok(!html.includes('$'));
    assert.ok(!/\b(USDC|dollars)\b/.test(html));
    assert.equal(html.includes('color: var(--green)'), false);
    const node = await fetch(`http://127.0.0.1:${server.address().port}/v1/node/xUSD`);
    assert.equal(node.status, 200);
    const view = await node.json();
    assert.equal(view.kind, 'signal');
    assert.equal(view.cbf, '34/265');
    const text = JSON.stringify(view);
    assert.ok(!/"usd"|\$/.test(text));
    assert.ok(!/\b(USDC|dollars)\b/.test(text));
  } finally {
    delete process.env.RXB_ENABLED;
    server.close();
  }
});

test('the node reading is the solver and it does not invent a zero', () => {
  const view = readNode('xUSD');
  assert.equal(view.cbf, '34/265');
  assert.equal(view.lts, '36/53');
  assert.equal(view.state, 'Fired');
  assert.equal(view.price.price_wad, null);
  assert.equal(view.tripped, true);
  const wrapped = readNode('sdeUSD');
  assert.equal(wrapped.readable, true);
  assert.equal(wrapped.cbf, '13600000/20923077');
  assert.equal(wrapped.state, 'Fired');
  const missing = readNode('USD3');
  assert.equal(missing.readable, false);
  assert.equal(missing.cbf, null);
  const text = JSON.stringify(view);
  assert.ok(!/"usd"|\$/.test(text));
});
