'use strict';
/**
 * The DogeOS app (GET /doge) is the duel, the arena and the proof, and
 * nothing else.
 *
 * Three promises, each held from what the server actually sends or the page
 * actually runs:
 *
 *   No token, no presale. The Pi app's ruling applies: the page is built in
 *   its router from the Mini App's parts, and nothing it loads may say
 *   presale, $RCLAW, Smithii or link to /token. A hackathon judge lands on the
 *   game and the proof.
 *
 *   One reading of the chain. The DogeOS testnet's id, RPC and explorer live
 *   in lib/root_anchor.js's registry and reach the page through
 *   /api/roots/chains. No script the page loads spells the chain id itself,
 *   so the wallet is asked for the chain the anchors are verified on.
 *
 *   The proof block says what it knows. A feed that could not be read is
 *   said to be unreadable, never "no roots yet"; the anchoring button appears
 *   only for a day with no anchor on the DogeOS testnet; a click anywhere
 *   else is not a click on it.
 */
const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const express = require('express');
const { codeOnly } = require('./helpers/code_only');

const APP = path.join(__dirname, '..');
const PUBLIC = path.join(APP, 'public');
const PRESALE = /presale|\$?RCLAW|smithii|\/token\b/i;

async function get(p) {
  const app = express();
  app.use('/doge', require('../routes/dogeapp'));
  const server = await new Promise((r) => { const s = app.listen(0, '127.0.0.1', () => r(s)); });
  try {
    return await new Promise((resolve, reject) => {
      http.get(`http://127.0.0.1:${server.address().port}${p}`, (res) => {
        let body = '';
        res.on('data', (c) => { body += c; });
        res.on('end', () => resolve({ status: res.statusCode, headers: res.headers, body }));
      }).on('error', reject);
    });
  } finally { server.close(); }
}

function scriptsOf(html) {
  return [...html.matchAll(/<script src="([^"]+)"/g)].map((m) => m[1]);
}

test('the page is served, English, with the duel, the proof and the app', async () => {
  const r = await get('/doge');
  assert.strictEqual(r.status, 200);
  assert.match(r.headers['content-type'], /text\/html/);
  assert.match(r.body, /<html lang="en">/);
  const s = scriptsOf(r.body);
  // pi-receipt.js: the Duel block's receipts check themselves in place here too.
  for (const need of ['/js/arena-session.js', '/js/duel.js', '/js/pi-receipt.js', '/js/pi-duel.js', '/js/anchor_cell.js', '/js/doge-proof.js', '/js/evm-wallet.js', '/js/doge-app.js']) {
    assert.ok(s.some((x) => x.startsWith(need + '?v=')), `${need} is not loaded`);
  }
  assert.strictEqual(s[s.length - 1].split('?')[0], '/js/doge-app.js', 'the app starts the session last, with every block loaded');
});

test('every script the page loads exists, and none carries the presale or the token', async () => {
  const r = await get('/doge');
  assert.doesNotMatch(r.body, PRESALE, 'the page itself names the sale or the token');
  const s = scriptsOf(r.body);
  assert.ok(s.length >= 6);
  for (const src of s) {
    const file = path.join(PUBLIC, src.split('?')[0]);
    assert.ok(fs.existsSync(file), `${src} is loaded but not shipped`);
    assert.doesNotMatch(codeOnly(fs.readFileSync(file, 'utf8')), PRESALE, `${src} names the sale or the token`);
  }
  assert.ok(!s.some((x) => /presale|i18n-core|\/app\.js|nav/.test(x)), `a site-wide script rode in: ${s}`);
});

test('the chain is the registry\'s reading: no page script spells the DogeOS chain id', () => {
  const lib = fs.readFileSync(path.join(APP, 'lib', 'root_anchor.js'), 'utf8');
  assert.match(lib, /chain_id: 6281971/, 'the registry no longer names the DogeOS testnet');
  for (const f of fs.readdirSync(path.join(PUBLIC, 'js')).filter((x) => x.endsWith('.js'))) {
    const src = codeOnly(fs.readFileSync(path.join(PUBLIC, 'js', f), 'utf8'));
    assert.ok(!/6281971/.test(src), `${f} carries the chain id as a literal; it must read /api/roots/chains`);
  }
  // The sign-in is doge-app.js and the wallet door it loads (evm-wallet.js,
  // shared with /memeclaw): read both, and ask the page that it loads both.
  const app = ['doge-app.js', 'evm-wallet.js']
    .map((f) => codeOnly(fs.readFileSync(path.join(PUBLIC, 'js', f), 'utf8'))).join('\n');
  assert.match(app, /\/api\/roots\/chains/);
  assert.match(app, /wallet_switchEthereumChain/);
  assert.match(app, /wallet_addEthereumChain/);
  assert.match(app, /personal_sign/);
  assert.match(app, /\/api\/auth\/wallet\/nonce/);
  assert.match(app, /\/api\/auth\/wallet\/verify/);
  assert.ok(!/eth_sendTransaction/.test(app), 'sign-in sends no transaction');
});

// ── the proof block, driven ───────────────────────────────────────────────

const proof = require('../public/js/doge-proof.js');

function fakeCtx(answers) {
  const calls = [];
  return {
    calls,
    api(p, o) {
      calls.push({ path: p, opts: o || {} });
      const key = p.split('?')[0];
      const a = answers[key] || answers[p];
      if (typeof a === 'function') return Promise.resolve(a(p, o));
      if (!a) return Promise.resolve({ ok: false, status: 404, body: null });
      return Promise.resolve(a);
    },
    render() { this.renders = (this.renders || 0) + 1; },
    toast() {},
  };
}

const DAY = '2026-10-18';
const ROOT = 'f'.repeat(64);
const CHAINS = [
  { key: 'base', chain_id: 8453, name: 'Base', explorer_tx: 'https://basescan.org/tx/' },
  { key: 'dogeos-testnet', chain_id: 6281971, name: 'DogeOS testnet', explorer_tx: 'https://dogeos-testnet.l2scan.co/tx/' },
];

test('before it has read anything the block says so; an unreadable feed is said to be unreadable', async () => {
  const b = proof.create();
  assert.match(b.html(), /Reading the daily roots/);
  const ctx = fakeCtx({ '/api/roots': { ok: false, status: 503, body: null } });
  b.signedIn(ctx);
  await b.reload();
  assert.match(b.html(), /could not be read: HTTP 503/);
  assert.doesNotMatch(b.html(), /No completed day/, 'an outage must not read as "no roots yet"');
});

test('an empty feed is the honest sentence, not an outage', async () => {
  const b = proof.create();
  b.signedIn(fakeCtx({ '/api/roots': { ok: true, status: 200, body: { chains: CHAINS, roots: [] } } }));
  await b.reload();
  assert.match(b.html(), /No completed day has sealed calls yet/);
});

test('a day without a DogeOS anchor offers the button; a day with one does not', async () => {
  const roots = [
    { day: DAY, root: ROOT, seal_count: 12, anchors: [{ chain: 'base', tx: '0x' + 'a'.repeat(64) }] },
    { day: '2026-10-17', root: 'e'.repeat(64), seal_count: 3,
      anchors: [{ chain: 'dogeos-testnet', tx: '0x' + 'b'.repeat(64) }] },
  ];
  const b = proof.create();
  const ctx = fakeCtx({
    '/api/roots': { ok: true, status: 200, body: { chains: CHAINS, roots } },
    '/api/roots/verify/2026-10-18': { ok: true, status: 200, body: { status: 'verified', block_time: '2026-10-19T00:10:00Z' } },
    '/api/roots/verify/2026-10-17': { ok: true, status: 200, body: { status: 'verified', block_time: '2026-10-18T00:05:00Z' } },
  });
  b.signedIn(ctx);
  await b.reload();
  const html = b.html();
  assert.match(html, /data-anchor-day="2026-10-18"/, 'the Base-only day offers anchoring on DogeOS');
  assert.doesNotMatch(html, /data-anchor-day="2026-10-17"/, 'the day already on DogeOS offers nothing');
  assert.match(html, /verified on DogeOS testnet/);
  assert.match(html, /verified on Base/);
  assert.match(html, /dogeos-testnet\.l2scan\.co\/tx\/0xb/);
  // one verify per (day, chain), each naming its chain
  // signedIn reloads once on its own and the test reloaded once more; the
  // set of (day, chain) pairs asked is the claim, not the count.
  const verifies = ctx.calls.filter((c) => c.path.startsWith('/api/roots/verify/'));
  assert.deepStrictEqual([...new Set(verifies.map((c) => c.path))].sort(), [
    '/api/roots/verify/2026-10-17?chain=dogeos-testnet',
    '/api/roots/verify/2026-10-18?chain=base',
  ]);
});

test('a click anywhere but the button is not a click on it', () => {
  const b = proof.create();
  const notIt = { target: { closest: () => null } };
  assert.strictEqual(b.click(notIt), false);
});

test('the older Base-only column is read as a Base anchor', () => {
  assert.deepStrictEqual(proof.anchorsOf({ anchor_tx: '0x1' }), [{ chain: 'base', tx: '0x1' }]);
  assert.deepStrictEqual(proof.anchorsOf({ anchors: [], anchor_tx: null }), []);
  assert.deepStrictEqual(proof.anchorsOf({ anchors: [{ chain: 'dogeos-testnet', tx: '0x2' }], anchor_tx: '0x1' }),
    [{ chain: 'dogeos-testnet', tx: '0x2' }], 'a list is the record; the column is only the fallback');
});

test('the polling budget stays under the submit route\'s rate limit', () => {
  // POST /api/roots/anchor allows six a minute per user; a poll faster than
  // that would spend the budget on "not yet mined" and lock the person out of
  // their own submission.
  assert.ok(proof.POLL_MS >= 10000, `${proof.POLL_MS} ms between polls is more than six a minute`);
  assert.ok(proof.POLL_TRIES * proof.POLL_MS <= 180000, 'the wait is bounded');
});
