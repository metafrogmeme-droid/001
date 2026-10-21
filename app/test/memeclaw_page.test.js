'use strict';
/**
 * /memeclaw, the page: what it serves and what it loads, that /doge and /pi
 * load nothing of it, the page's own helpers, and the operator's doors with
 * a real operator session.
 */
process.env.JWT_SECRET = process.env.JWT_SECRET || 'j'.repeat(64);
delete process.env.DATABASE_URL;

const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const express = require('express');

const { codeOnly } = require('./helpers/code_only');
const { pool } = require('../db');
const { findOrCreateOAuthUser, signToken } = require('../auth');
const pageRoute = require('../routes/memeclaw_page');
const page = require('../public/js/memeclaw.js');

const PUBLIC = path.join(__dirname, '..', 'public');
const PRESALE = /presale|\$?RCLAW|smithii|\/token\b/i;

async function serve(mount) {
  const app = express();
  app.use(express.json());
  mount(app);
  const server = http.createServer(app);
  await new Promise((r) => server.listen(0, '127.0.0.1', r));
  return { server, base: `http://127.0.0.1:${server.address().port}` };
}

const scriptsOf = (html) => [...html.matchAll(/<script src="([^"]+)"/g)].map((m) => m[1]);

test('the page is its own: its scripts, no presale, no token, no site navigation', async () => {
  const { server, base } = await serve((app) => app.use('/memeclaw', pageRoute));
  try {
    const r = await fetch(`${base}/memeclaw`);
    assert.equal(r.status, 200);
    const html = await r.text();
    assert.match(html, /<html lang="en">/);
    assert.match(html, /Practice and testnet only\./);
    assert.match(html, /data-mode="practice" aria-pressed="true"/, 'practice is the page\'s first mode: it needs no wallet');
    assert.match(html, /never launches, buys or sells these coins/);
    assert.doesNotMatch(html, PRESALE, 'the page names the sale or the token');
    const scripts = scriptsOf(html);
    assert.deepEqual(scripts.map((s) => s.split('?')[0]), ['/js/api-retry.js', '/js/evm-wallet.js', '/js/pi-receipt.js', '/js/memeclaw.js']);
    for (const src of scripts) {
      const file = path.join(PUBLIC, src.split('?')[0]);
      assert.ok(fs.existsSync(file), `${src} is loaded but not shipped`);
      assert.doesNotMatch(codeOnly(fs.readFileSync(file, 'utf8')), PRESALE, `${src} names the sale or the token`);
    }
    assert.ok(!/<script>/.test(html), 'no inline script: the site\'s CSP allows none on a router-built page');
    const links = [...html.matchAll(/href="(\/[^"]*)"/g)].map((m) => m[1]).filter((h) => !/\.(css|svg)/.test(h));
    assert.deepEqual(links.sort(), ['/privacy', '/terms'], 'a launch receipt is checked on the page, not on another site');
  } finally { server.close(); }
});

test('/doge and /pi load nothing of Memeclaw, and Memeclaw sends nothing in the wallet door', () => {
  const doge = require('../routes/dogeapp');
  const pi = require('../routes/piapp');
  for (const list of [doge.SCRIPTS, pi.SCRIPTS]) {
    assert.ok(!list.some((s) => /memeclaw/.test(s)), `a Memeclaw script rode into ${list}`);
  }
  const door = codeOnly(fs.readFileSync(path.join(PUBLIC, 'js', 'evm-wallet.js'), 'utf8'));
  assert.ok(!/eth_sendTransaction|personal_sign/.test(door), 'the shared wallet door asks for accounts and chains only');
  const mine = codeOnly(fs.readFileSync(path.join(PUBLIC, 'js', 'memeclaw.js'), 'utf8'));
  assert.match(mine, /eth_sendTransaction/, 'Memeclaw\'s own script is where its sends live');
  assert.ok(!/6281971/.test(mine), 'the chain is the server\'s reading');
  assert.ok(!/\bencodeCall\b|keccak|AbiCoder|\babi\.|\bselector\(/i.test(mine), 'the page encodes no calldata: the server writes every transaction');
});

test('the page\'s amounts: parsed exactly, printed rounded down, never invented', () => {
  assert.equal(page.parseUnits('1.5'), '1500000000000000000');
  assert.equal(page.parseUnits('0.000000000000000001'), '1');
  assert.equal(page.parseUnits('40'), '40000000000000000000');
  assert.equal(page.parseUnits('1,000'), '1000000000000000000000');
  for (const bad of ['', '.', 'abc', '1.2.3', '-1', '1e5', '0.0000000000000000001']) {
    assert.strictEqual(page.parseUnits(bad), null, bad);
  }
  assert.equal(page.formatUnits('1500000000000000000', 4), '1.5');
  assert.equal(page.formatUnits('1999999999999999999', 2), '1.99', 'rounded down, never up');
  assert.equal(page.formatUnits('1000000000000000000000000000', 0), '1,000,000,000');
  assert.strictEqual(page.formatUnits(null), null, 'an unread amount stays unread');
  assert.strictEqual(page.pct(null), null);
  assert.equal(page.pct(1234), '12.34%');
  assert.equal(page.esc('<img src=x onerror=alert(1)>'), '&lt;img src=x onerror=alert(1)&gt;');
});

test('every reason the server can give has a sentence on the page', () => {
  const server = fs.readFileSync(path.join(__dirname, '..', 'lib', 'memeclaw.js'), 'utf8');
  const reasons = new Set([...codeOnly(server).matchAll(/reason: '([a-z_]+)'/g)].map((m) => m[1]));
  for (const r of ['symbol_reserved', 'name_reserved', 'presented_as_security', 'description_link', 'sold_out', 'not_held']) {
    assert.ok(reasons.has(r), `the server no longer gives ${r}`);
  }
  for (const r of reasons) {
    assert.doesNotMatch(page.refusal(r), /The server refused that/, `no sentence for ${r}`);
  }
  for (const r of ['not_deployed', 'factory_mismatch', 'factory_unknown', 'factory_misconfigured', 'no_testnet']) {
    assert.doesNotMatch(page.offSentence(r), /not open right now/, `no sentence for ${r}`);
  }
  assert.match(page.offSentence('factory_unknown'), /could not be read/, 'unreadable is said as unreadable');
  // Practice mode's refusals: the route sends each reason as it is, except an
  // unknown player, which it sends as practice_key.
  const practice = fs.readFileSync(path.join(__dirname, '..', 'lib', 'memeclaw_paper.js'), 'utf8');
  const paperReasons = new Set([...codeOnly(practice).matchAll(/PaperRefusal\('([a-z_]+)'/g)].map((m) => m[1]));
  for (const r of ['not_enough_doge', 'launches_today', 'refill_too_soon', 'slippage', 'unknown_player']) {
    assert.ok(paperReasons.has(r), `practice mode no longer gives ${r}`);
  }
  paperReasons.delete('unknown_player');
  paperReasons.add('practice_key');
  for (const r of paperReasons) {
    assert.doesNotMatch(page.refusal(r), /The server refused that/, `no sentence for the practice refusal ${r}`);
  }
});

test('the operator hides a coin and reads the deploy plan; anyone else is refused', async () => {
  const router = require('../routes/memeclaw');
  const store = require('../lib/memeclaw_store');
  const mk = async (admin) => {
    const user = await findOrCreateOAuthUser({ provider: 'wallet', providerId: `0x${(admin ? 'ad' : 'cd').repeat(20)}` });
    const row = pool.users.find((u) => u.id === user.id);
    if (admin) row.plan = 'admin';
    return signToken(user);
  };
  const op = await mk(true);
  const someone = await mk(false);
  pool.memeclawCoins = [];
  await store.recordCoin({ token: '0x' + '99'.repeat(20), chain_id: 6281971, factory: '0x' + 'fa'.repeat(20),
    creator: '0x' + 'a1'.repeat(20), name: 'Op Test', symbol: 'OPT', terms_hash: '0x' + '00'.repeat(32),
    tx_hash: '0x' + '01'.repeat(32), block_number: 1, launched_at: new Date(), supply: '1' });
  const { server, base } = await serve((app) => app.use('/api/memeclaw', router));
  const post = (p, token, body) => fetch(base + p, { method: 'POST', headers: { 'content-type': 'application/json', authorization: `Bearer ${token}` }, body: JSON.stringify(body) });
  try {
    let r = await post(`/api/memeclaw/coins/0x${'99'.repeat(20)}/hide`, someone, { reason: 'spam' });
    assert.equal(r.status, 403);
    r = await fetch(`${base}/api/memeclaw/deploy-plan`, { headers: { authorization: `Bearer ${someone}` } });
    assert.equal(r.status, 403);
    r = await post(`/api/memeclaw/coins/0x${'99'.repeat(20)}/hide`, op, { reason: 'spam' });
    assert.equal(r.status, 200);
    assert.ok((await store.coin('0x' + '99'.repeat(20))).hidden_at);
    r = await fetch(`${base}/api/memeclaw/deploy-plan`, { headers: { authorization: `Bearer ${op}` } });
    assert.equal(r.status, 200);
    const plan = await r.json();
    assert.equal(plan.data, require('../lib/memeclaw').FACTORY.bytecode, 'the deploy is the committed creation code, nothing else');
    assert.ok(!('to' in plan), 'a contract creation has no recipient');
    assert.deepEqual(plan.starters, JSON.parse(JSON.stringify(require('../lib/memeclaw').STARTER_COINS)),
      'the starter coins the operator\'s page offers are the server\'s one list');
    // The operator sees the hidden coin, with why; a visitor does not.
    const visitor = await (await fetch(`${base}/api/memeclaw/coins`)).json();
    assert.equal(visitor.coins.length, 0);
    const seen = await (await fetch(`${base}/api/memeclaw/coins`, { headers: { authorization: `Bearer ${op}` } })).json();
    assert.equal(seen.coins.length, 1);
    assert.equal(seen.coins[0].hidden, 'spam');
  } finally { server.close(); }
});

test('the starter coins pass every rule a launch is held to, and say they have no value', () => {
  const mc = require('../lib/memeclaw');
  assert.ok(mc.STARTER_COINS.length >= 1);
  for (const c of mc.STARTER_COINS) {
    assert.strictEqual(mc.pageProblem(c), null, `${c.symbol} would be refused`);
    assert.match(c.description, /no value/);
  }
  // The other arm: the same check refuses a starter that names the project.
  assert.ok(mc.pageProblem({ name: 'Runeclaw Coin', symbol: 'RCC', description: '' }));
});

test('the page floors a quote by its slippage and draws a launch receipt\'s facts from the sealed payload', () => {
  assert.equal(page.floorOf('1000000'), '990000');
  assert.equal(page.floorOf('99'), '98', 'rounded down');
  const facts = page.launchFacts({ kind: 'memeclaw_launch', symbol: 'CLAW', name: 'Much Claw', token: '0xt', creator: '0xc',
    terms_hash: '0xh', tx: '0xx', block_number: 7, launched_at: '2026-10-21T00:00:00.000Z' });
  assert.ok(facts.some(([k, v]) => k === 'Coin' && v === 'CLAW · Much Claw'));
  assert.ok(facts.some(([k, v]) => k === 'Launch transaction' && /0xx · block 7/.test(v)));
  assert.deepEqual(page.launchFacts({ kind: 'duel_round' }), [], 'another kind of receipt is not drawn as a launch');
});

test('the numbers the page tells a practice player are the practice library\'s own', () => {
  const paper = require('../lib/memeclaw_paper');
  const curve = require('../lib/memeclaw_curve');
  const src = codeOnly(fs.readFileSync(path.join(PUBLIC, 'js', 'memeclaw.js'), 'utf8'));
  const words = ['zero', 'one', 'two', 'three', 'four', 'five', 'six', 'seven', 'eight', 'nine', 'ten'];
  assert.match(src, new RegExp(`you start with ${page.formatUnits(paper.START_DOGE.toString(), 4)} practice DOGE`));
  assert.match(src, new RegExp(`less than ${page.formatUnits(paper.REFILL_BELOW.toString(), 4)}\\.`));
  assert.match(src, new RegExp(`That is ${words[paper.LAUNCHES_PER_DAY]} practice launches today`));
  assert.match(src, new RegExp(`${words[paper.LAUNCHES_PER_DAY].replace(/^./, (c) => c.toUpperCase())} practice launches a day`));
  // Buying out the whole supply from a fresh curve costs what the page says.
  const fresh = { dogeReserve: 0n, tokenReserve: curve.SUPPLY };
  assert.match(src, new RegExp(`whole supply costs ${page.formatUnits(curve.maxBuy(fresh).toString(), 4)} DOGE`));
  assert.match(src, new RegExp(`supply costs \\(${page.formatUnits(curve.maxBuy(fresh).toString(), 4)} DOGE\\)`));
});
