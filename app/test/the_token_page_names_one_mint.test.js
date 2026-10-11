'use strict';
/**
 * /token paints the one $RCLAW record and invents nothing.
 *
 * The token was minted on Solana mainnet on 2026-09-30. The facts live in one
 * file, token/config/rclaw.mainnet.json, which the bot's /rclaw card reads;
 * the website reads its byte-identical copy in app/content/ (the web deploy
 * ships app/ alone), so the two surfaces cannot name two mints. Three values for every
 * field a chain can leave empty: a null authority is "none (revoked)", a null
 * presale term is "not announced yet", and a record that did not arrive paints
 * NO address — a guessed mint is worse than none. The presale was announced on
 * 2026-10-10: its terms are sentences token/presale/the_record_states_the_sale
 * .test.mjs holds to the sale config, and its sale link is printed only when
 * it is https on smithii.io (tests/fixtures/sale_url_cases.json, which the
 * bot's /rclaw card is held to as well).
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
const SALE_URL_CASES = JSON.parse(fs.readFileSync(
  path.join(APP, '..', 'tests', 'fixtures', 'sale_url_cases.json'), 'utf8'));
const TERM_KEYS = page.TERMS.map(([k]) => k);
const esc = (t) => String(t).replace(/[&<>"']/g, (c) => (
  { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]);
const count = (hay, needle) => hay.split(needle).length - 1;

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

test('the record is the website\'s copy of the file the bot reads, byte for byte', () => {
  // The web deploy ships app/ alone, so the website reads its copy
  // (app/scripts/sync_content.js); reading ../../token/ answered 503 live.
  assert.strictEqual(rclaw.RECORD_PATH, path.join(APP, 'content', 'rclaw.mainnet.json'));
  assert.ok(fs.readFileSync(rclaw.RECORD_PATH).equals(
    fs.readFileSync(path.join(APP, '..', 'token', 'config', 'rclaw.mainnet.json'))),
  'run `node app/scripts/sync_content.js`');
  const r = rclaw.readRecord();
  assert.strictEqual(r.mint, MINT);
  assert.strictEqual(r.token_program, 'TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA');
  assert.strictEqual(r.mint_authority, null);
  assert.strictEqual(r.freeze_authority, null);
  assert.strictEqual(r.presale.status, 'announced');
  for (const k of TERM_KEYS) {
    assert.ok(typeof r.presale[k] === 'string' && r.presale[k].trim(), k);
  }
  assert.strictEqual(r.presale.sale_url, null, 'no sale link before Create');
});

test('the page prints every term the record announces, and no other', () => {
  // Both ways: a term added to the record and not to TERMS would go unprinted,
  // and a TERMS key the record lacks would read "unreadable" to every visitor.
  const announced = Object.keys(record().presale).filter((k) => k !== 'status' && k !== 'sale_url');
  // The bot's TERMS is held to the same record by tests/test_the_token_card_reads_the_one_record.py.
  assert.deepStrictEqual(announced, TERM_KEYS);
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
    // §4: no dollar figure on a public payload. The presale price is a SOL rate.
    assert.match(t.presale.price, / per SOL$/);
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
  const blank = { status: 'coming_soon', sale_url: null };
  for (const k of TERM_KEYS) blank[k] = null;
  const html = page.presaleHtml(blank, 'RCLAW');
  assert.ok(html.includes('Coming soon'));
  assert.strictEqual(count(html, 'not announced yet'), TERM_KEYS.length);
  assert.strictEqual(count(html, esc(page.SALE_LINK_NONE)), 1);
  assert.ok(html.includes(esc(page.BODY_SOON)) && !html.includes(esc(page.BODY_ANNOUNCED)));
  assert.ok(!/\$\s?\d/.test(html));
});

test('the announced presale prints each term as written', () => {
  const presale = record().presale;
  const html = page.presaleHtml(presale, 'RCLAW');
  assert.ok(html.includes('<span class="tok-status">Announced</span>'));
  for (const k of TERM_KEYS.filter((key) => key !== 'allocation_tokens')) {
    assert.ok(html.includes(`<b>${esc(presale[k])}</b>`), k);
  }
  assert.ok(html.includes('<dt>For sale</dt><dd><b>150,000,000 RCLAW</b></dd>'));
  assert.ok(html.includes('<dt>Price</dt><dd><b>30,000.3 RCLAW per SOL</b></dd>'));
  assert.ok(!html.includes('not announced yet'));
  assert.strictEqual(count(html, esc(page.SALE_LINK_NONE)), 1);
  assert.ok(html.includes(esc(page.BODY_ANNOUNCED)) && !html.includes(esc(page.BODY_SOON)));
  assert.ok(!/\$\s?\d/.test(html));
  // one term withdrawn reads "not announced yet" beside the others as written
  const partial = page.presaleHtml({ ...presale, price: null }, 'RCLAW');
  assert.strictEqual(count(partial, 'not announced yet'), 1);
  assert.ok(partial.includes(`<b>${esc(presale.venue)}</b>`));
});

test('a term missing from the record is unreadable, not "not announced"', () => {
  assert.ok(page.presaleCell({}, 'date').includes('unreadable'));
  assert.ok(page.presaleCell({ date: null }, 'date').includes('not announced yet'));
  const noStatus = page.presaleHtml({ date: null, price: null, venue: null });
  assert.ok(noStatus.includes('unreadable') && !noStatus.includes('Coming soon'));
  // a status this page does not know is named, with no prose that would claim either state
  const odd = page.presaleHtml({ ...record().presale, status: 'paused_for_review' });
  assert.ok(odd.includes('paused for review'));
  assert.ok(!odd.includes(esc(page.BODY_SOON)) && !odd.includes(esc(page.BODY_ANNOUNCED)));
  for (const k of [...TERM_KEYS, 'sale_url']) {
    const p = record().presale;
    delete p[k];
    assert.strictEqual(count(page.presaleHtml(p, 'RCLAW'), '>unreadable<'), 1, k);
  }
  assert.strictEqual(count(page.presaleHtml(record().presale, 'RCLAW'), '>unreadable<'), 0);
});

test('a token count that is not digits is unreadable, never a number', () => {
  for (const raw of ['1.5e8', '150,000,000', '-1', '', 42.5]) {
    assert.ok(page.allocationCell({ allocation_tokens: raw }, 'RCLAW').includes('unreadable'), String(raw));
  }
  assert.strictEqual(page.allocationCell({ allocation_tokens: '150000000' }, 'RCLAW'), '<b>150,000,000 RCLAW</b>');
  assert.ok(page.allocationCell({ allocation_tokens: null }, 'RCLAW').includes('not announced yet'));
});

test('a sale link on smithii.io is a link; anything else is unreadable and not printed', () => {
  for (const url of SALE_URL_CASES.accept) {
    const cell = page.saleLinkCell({ sale_url: url });
    assert.ok(cell.startsWith(`<a href="${esc(url)}" target="_blank" rel="noopener noreferrer">`), url);
  }
  for (const url of [...SALE_URL_CASES.refuse, 42, ['https://smithii.io'], { href: 'https://smithii.io' }]) {
    const cell = page.saleLinkCell({ sale_url: url });
    assert.ok(cell.includes('unreadable') && !cell.includes('<a '), JSON.stringify(url));
    if (typeof url === 'string' && url.trim()) {
      assert.ok(!cell.includes(url.trim()) && !cell.includes(esc(url.trim())), url);
    }
  }
  assert.ok(!page.isSaleUrl('https://tools.smithii.io/' + 'a'.repeat(300)), 'an overlong link');
  assert.ok(page.saleLinkCell({}).includes('unreadable'));
  assert.ok(page.saleLinkCell({ sale_url: null }).includes(esc(page.SALE_LINK_NONE)));
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

test('the landing page announces the token, says what the record says, and links /token', () => {
  const landing = fs.readFileSync(path.join(APP, 'public', 'index.html'), 'utf8');
  assert.match(landing, /<a class="hero-token" href="\/token">/);
  // The strip's one claim is the presale's status, so it is held to the record:
  // "coming soon" beside an announced sale, or "announced" before one, is a second answer.
  const STRIP = { coming_soon: 'presale coming soon', announced: 'presale announced' };
  const said = STRIP[rclaw.readRecord().presale.status];
  assert.ok(said, 'a record status the strip has no words for');
  const en = `$RCLAW is live on Solana — ${said}`;
  assert.ok(landing.includes(`data-i18n="home.token_live">${en}<`), 'the markup');
  const i18n = require('../public/js/i18n.js');
  assert.strictEqual(i18n.translate('home.token_live', 'en'), en, 'the English string the other languages translate');
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

// ── the link preview ─────────────────────────────────────────────────────

/** The JPEG's size and its runeclaw-presale-og comment, read from the bytes. */
function jpegFacts(buf) {
  assert.ok(buf[0] === 0xff && buf[1] === 0xd8, 'not a JPEG');
  let i = 2;
  let facts = null;
  let size = null;
  while (i + 4 <= buf.length && buf[i] === 0xff) {
    const marker = buf[i + 1];
    const len = buf.readUInt16BE(i + 2);
    const body = buf.subarray(i + 4, i + 2 + len);
    if (marker === 0xfe && body.subarray(0, 20).toString('latin1') === 'runeclaw-presale-og\0') {
      facts = JSON.parse(body.subarray(20).toString('utf8'));
    }
    if (marker >= 0xc0 && marker <= 0xc2) size = { height: body.readUInt16BE(1), width: body.readUInt16BE(3) };
    if (marker === 0xda) break;
    i += 2 + len;
  }
  return { facts, size };
}

test('the presale link preview prints what the record says', () => {
  const html = fs.readFileSync(path.join(APP, 'public', 'token.html'), 'utf8');
  const og = html.match(/<meta property="og:image" content="https:\/\/www\.humanoid-traders\.com\/([^"]+)">/);
  const tw = html.match(/<meta name="twitter:image" content="https:\/\/www\.humanoid-traders\.com\/([^"]+)">/);
  assert.ok(og && tw && og[1] === tw[1], 'og:image and twitter:image name one file on the canonical host');
  const { facts, size } = jpegFacts(fs.readFileSync(path.join(APP, 'public', og[1])));
  assert.ok(facts, `${og[1]} carries no runeclaw-presale-og record: it was re-saved, or is not the drawn card`);
  assert.deepStrictEqual(size, { width: 1200, height: 630 });
  assert.match(html, /<meta property="og:image:width" content="1200">/);
  assert.match(html, /<meta property="og:image:height" content="630">/);
  const p = rclaw.readRecord().presale;
  assert.strictEqual(p.status, 'announced', 'a presale card on a page whose record has not announced one');
  assert.ok(p.date.startsWith(`${facts.window},`), `the card says ${facts.window}; the record ${p.date}`);
  assert.strictEqual(p.price, `${facts.rate} RCLAW per SOL`);
  assert.ok(p.hard_cap.startsWith(`${facts.hardCapSol} SOL `), `the card says ${facts.hardCapSol}; the record ${p.hard_cap}`);
  assert.ok(facts.footer.includes('No refunds') && p.refunds === 'None');
  assert.ok(facts.footer.includes('no buyer vesting') && /No vesting for buyers/.test(p.claim));
  assert.ok(html.includes(`<link rel="canonical" href="https://www.${facts.link}">`), 'the card names this page');
});
