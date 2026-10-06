'use strict';
/**
 * /token paints the one $RCLAW record and invents nothing.
 *
 * The token was minted on Solana mainnet on 2026-09-30. The facts live in one
 * file, token/config/rclaw.mainnet.json, which the bot's /rclaw card reads as
 * well, so the two surfaces cannot name two mints. Three values for every
 * field a chain can leave empty: a null authority is "none (revoked)", a null
 * presale term is "not announced yet", and a record that did not arrive paints
 * NO address — a guessed mint is worse than none.
 */

const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const express = require('express');

const rclaw = require('../lib/rclaw_token');
const page = require('../public/js/token-page');

const MINT = 'rupKpYsgk6em6xx4V9E4oGN9Bvo9FQWQd71qBK2CaNe';
const APP = path.join(__dirname, '..');
const record = () => JSON.parse(JSON.stringify(rclaw.publicRecord(rclaw.readRecord())));

function serve(fn) {
  const app = express();
  app.use('/api/token', require('../routes/token'));
  const server = http.createServer(app);
  return new Promise((resolve) => server.listen(0, '127.0.0.1', resolve)).then(async () => {
    try { return await fn(server.address().port); } finally { server.close(); }
  });
}
function get(port) {
  return new Promise((resolve, reject) => {
    http.get({ port, path: '/api/token', host: '127.0.0.1' }, (res) => {
      let b = ''; res.on('data', (c) => { b += c; });
      res.on('end', () => resolve({ status: res.statusCode, body: JSON.parse(b) }));
    }).on('error', reject);
  });
}

// ── the record ───────────────────────────────────────────────────────────

test('the record is the file the bot reads', () => {
  assert.strictEqual(rclaw.RECORD_PATH,
    path.join(APP, '..', 'token', 'config', 'rclaw.mainnet.json'));
  const r = rclaw.readRecord();
  assert.strictEqual(r.mint, MINT);
  assert.strictEqual(r.token_program, 'TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA');
  assert.strictEqual(r.mint_authority, null);
  assert.strictEqual(r.freeze_authority, null);
  assert.strictEqual(r.presale.status, 'coming_soon');
  for (const k of ['date', 'price', 'venue']) assert.strictEqual(r.presale[k], null, k);
});

test('a bad record throws; the real one does not', () => {
  const dir = fs.mkdtempSync(path.join(require('node:os').tmpdir(), 'rclaw-'));
  const file = path.join(dir, 'r.json');
  const good = rclaw.readRecord();
  for (const [body, name] of [
    ['[]', 'TokenRecordInvalid'],
    [JSON.stringify({ ...good, mint: 'not-an-address' }), 'TokenRecordInvalid'],
    [JSON.stringify({ ...good, mint: '0'.repeat(44) }), 'TokenRecordInvalid'],
    [JSON.stringify({ ...good, presale: { date: null } }), 'TokenRecordInvalid'],
    ['{not json', 'SyntaxError'],
  ]) {
    fs.writeFileSync(file, body);
    assert.throws(() => rclaw.readRecord(file), (e) => e.name === name, body.slice(0, 40));
  }
  fs.writeFileSync(file, JSON.stringify(good));
  assert.strictEqual(rclaw.readRecord(file).mint, MINT);
  assert.throws(() => rclaw.readRecord(path.join(dir, 'absent.json')), (e) => e.code === 'ENOENT');
});

// ── the route ────────────────────────────────────────────────────────────

test('GET /api/token serves the record without its editor notes', async () => {
  await serve(async (port) => {
    const res = await get(port);
    assert.strictEqual(res.status, 200);
    const t = res.body.token;
    assert.strictEqual(t.mint, MINT);
    assert.ok(!('basis' in t) && !('_comment' in t), 'editor notes stay in the file');
    assert.ok(!('_comment' in t.presale));
    // §4: no dollar figure on a public payload. The presale price is null.
    assert.ok(!/\$\s?\d/.test(JSON.stringify(res.body)), 'a dollar amount on a public route');
  });
});

test('an unreadable record is a 503 naming the class, with no address', async () => {
  const real = rclaw.readRecord;
  rclaw.readRecord = () => { const e = new Error('/srv/secret/path'); e.name = 'SyntaxError'; throw e; };
  try {
    await serve(async (port) => {
      const res = await get(port);
      assert.strictEqual(res.status, 503);
      assert.deepStrictEqual(res.body, { error: 'token_record_unreadable', exception: 'SyntaxError' });
    });
  } finally { rclaw.readRecord = real; }
  // and the same route answers 200 once the read succeeds again
  await serve(async (port) => assert.strictEqual((await get(port)).status, 200));
});

// ── the render ───────────────────────────────────────────────────────────

test('the page names the mint, the supply and the revoked authorities', () => {
  const html = page.render(record());
  assert.ok(html.includes(`<code id="tok-mint">${MINT}</code>`));
  assert.ok(html.includes(`data-mint="${MINT}"`), 'the copy button copies this mint');
  assert.ok(html.includes(`href="https://solscan.io/token/${MINT}"`));
  assert.ok(html.includes('<b>1,000,000,000 RCLAW</b>'));
  assert.strictEqual((html.match(/none \(revoked\)/g) || []).length, 2);
  assert.ok(html.includes('2026-09-30 UTC'));
});

test('an unannounced presale reads "not announced yet" for every term', () => {
  const html = page.presaleHtml(record().presale);
  assert.ok(html.includes('Coming soon'));
  assert.strictEqual((html.match(/not announced yet/g) || []).length, 3);
  assert.ok(!/\$\s?\d/.test(html));
  // the announced arm: a term is printed as written
  const announced = page.presaleHtml({ status: 'coming_soon', date: '2026-11-01', price: null, venue: 'Metaplex Genesis' });
  assert.ok(announced.includes('<b>2026-11-01</b>') && announced.includes('<b>Metaplex Genesis</b>'));
  assert.strictEqual((announced.match(/not announced yet/g) || []).length, 1);
});

test('a term missing from the record is unreadable, not "not announced"', () => {
  assert.ok(page.presaleCell({}, 'date').includes('unreadable'));
  assert.ok(page.presaleCell({ date: null }, 'date').includes('not announced yet'));
  const noStatus = page.presaleHtml({ date: null, price: null, venue: null });
  assert.ok(noStatus.includes('unreadable') && !noStatus.includes('Coming soon'));
});

test('a record that did not arrive paints no address at all', () => {
  for (const bad of [null, undefined, {}, { mint: '' }, { mint: '0'.repeat(44) }, { mint: 'l'.repeat(44) }, { mint: 42 }, 'str']) {
    const html = page.render(bad);
    assert.strictEqual(html, page.fault(), JSON.stringify(bad));
    assert.ok(!/[1-9A-HJ-NP-Za-km-z]{32,44}/.test(html), 'an address stood in');
  }
});

test('an authority of an unknown shape is unreadable; a held one is named', () => {
  assert.ok(page.authorityHtml(null).includes('none (revoked)'));
  assert.ok(page.authorityHtml('EEoMVamYkEvZEDXe7cyMGCLg5BUGNAuvSXC1w2LWELVy').includes('held by'));
  for (const v of [undefined, '', 0, false]) assert.ok(page.authorityHtml(v).includes('unreadable'), String(v));
});

test('an explorer link that does not name this mint is not rendered', () => {
  const r = record();
  r.explorer = 'https://solscan.io/token/So11111111111111111111111111111111111111112';
  const html = page.render(r);
  assert.ok(!html.includes('solscan.io'), 'a link to another mint beside this address');
  assert.ok(html.includes(MINT));
});

test('a supply that is not digits is unreadable, never a number', () => {
  assert.strictEqual(page.supplyText('1000000000'), '1,000,000,000');
  for (const v of ['1e9', '', null, '-5', '1,000']) assert.strictEqual(page.supplyText(v), null, String(v));
  const r = record(); r.supply_tokens = '1e9';
  assert.ok(page.render(r).includes('unreadable'));
});

// ── the doors ────────────────────────────────────────────────────────────

test('the landing page announces the token and links /token', () => {
  const landing = fs.readFileSync(path.join(APP, 'public', 'index.html'), 'utf8');
  assert.match(landing, /<a class="hero-token" href="\/token">/);
  assert.match(landing, /data-i18n="home\.token_live">\$RCLAW is live on Solana — presale coming soon</);
  assert.match(fs.readFileSync(path.join(APP, 'server.js'), 'utf8'),
    /app\.get\('\/token', [^\n]*'token\.html'\)/);
});

test('the page points at the bot command that exists', () => {
  const catalog = fs.readFileSync(path.join(APP, '..', 'bot', 'skills', 'command_catalog.py'), 'utf8');
  assert.match(catalog, /\("rclaw", "the \$RCLAW token/);
  const i18n = require('../public/js/i18n.js');
  for (const l of i18n.LANGS) {
    assert.ok(i18n.translate('tok.bot_hint', l.code).endsWith('/rclaw'), l.code);
  }
});
