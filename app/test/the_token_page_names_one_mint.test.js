'use strict';
/**
 * /token paints the one $RCLAW record and invents nothing, and carries no presale.
 *
 * The token was minted on Solana mainnet on 2026-09-30. The facts live in one
 * file, token/config/rclaw.mainnet.json, which the bot's /rclaw card reads;
 * the website reads its copy in app/content/ (the web deploy ships app/
 * alone), so the two surfaces cannot name two mints. Three values for every
 * field a chain can leave empty: a null authority is "none (revoked)", and a
 * record that did not arrive paints NO address — a guessed mint is worse than
 * none.
 *
 * NO PRESALE. The owner took the presale off the website and the bot on
 * 22 October 2026. The original record may still hold a `presale` block (the
 * token tooling's account of the Smithii sale); the website's copy is written
 * without it (app/scripts/sync_content.js, websiteRecord), /api/token drops
 * one anyway, and no page, string or image of the site names a sale. The
 * bot's half is tests/test_the_token_card_reads_the_one_record.py.
 */

const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const express = require('express');

const rclaw = require('../lib/rclaw_token');
const page = require('../public/js/token-page');
const sync = require('../scripts/sync_content');

const MINT = 'rupKpYsgk6em6xx4V9E4oGN9Bvo9FQWQd71qBK2CaNe';
const APP = path.join(__dirname, '..');
const PUB = path.join(APP, 'public');
const ORIGINAL = path.join(APP, '..', 'token', 'config', 'rclaw.mainnet.json');
const record = () => JSON.parse(JSON.stringify(rclaw.publicRecord(rclaw.readRecord())));
/** A sale, in every language the site speaks: the words a reader would see. */
const SALE_WORDS = /presale|pre-sale|preventa|prévente|pré-venda|prevendita|預售|预售|プレセール|프리세일|пресейл|ön satış|प्रीसेल|البيع المسبق|tools\.smithii\.io\/launch/i;
/** A sale block of the shape the token tooling's record holds. */
const SALE = { status: 'announced', price: '30,000.3 RCLAW per SOL', sale_url: 'https://tools.smithii.io/launch/solana/RUNECLAW' };

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

test('the record is the website\'s copy of the file the bot reads, without the sale', () => {
  // The web deploy ships app/ alone, so the website reads its copy
  // (app/scripts/sync_content.js); reading ../../token/ answered 503 live.
  assert.strictEqual(rclaw.RECORD_PATH, path.join(APP, 'content', 'rclaw.mainnet.json'));
  assert.ok(fs.readFileSync(rclaw.RECORD_PATH).equals(sync.websiteRecord()), 'run `node app/scripts/sync_content.js`');
  const r = rclaw.readRecord();
  assert.strictEqual(r.mint, MINT);
  assert.strictEqual(r.token_program, 'TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA');
  assert.strictEqual(r.mint_authority, null);
  assert.strictEqual(r.freeze_authority, null);
  assert.ok(!('presale' in r), 'the website\'s copy carries a presale block');
  // Editor notes (`_comment`) name the token tooling's own paths (token/presale/…) and are never
  // served; what the site serves is the record without them.
  assert.ok(!SALE_WORDS.test(JSON.stringify(record())), 'what the website serves names a sale');
  // Both arms: everything else in the original arrives, field for field.
  const original = JSON.parse(fs.readFileSync(ORIGINAL, 'utf8'));
  for (const k of Object.keys(original)) {
    if (k === 'presale' || k === 'smithii_audits') continue;
    assert.deepStrictEqual(r[k], original[k], k);
  }
});

test('the copy keeps only the audits of the program that holds the locks', () => {
  const vesting = JSON.parse(fs.readFileSync(path.join(APP, '..', 'token', 'presale', 'locks.plan.json'), 'utf8')).tool.program;
  const original = JSON.parse(fs.readFileSync(ORIGINAL, 'utf8')).smithii_audits.items;
  const kept = rclaw.readRecord().smithii_audits.items;
  assert.ok(kept.length >= 1, 'the vesting program\'s audit was dropped too');
  assert.ok(kept.every((it) => it.program === vesting), 'an audit of another program is on the website');
  assert.deepStrictEqual(kept, original.filter((it) => it.program === vesting));
  assert.ok(original.some((it) => it.program !== vesting), 'the fixture must hold an audit to drop, or the filter is untested');
});

test('a bad record throws; the real one does not, and a record with no sale is whole', () => {
  const dir = fs.mkdtempSync(path.join(require('node:os').tmpdir(), 'rclaw-'));
  const file = path.join(dir, 'r.json');
  const good = rclaw.readRecord();
  for (const [body, name] of [
    ['[]', 'TokenRecordInvalid'],
    [JSON.stringify({ ...good, mint: 'not-an-address' }), 'TokenRecordInvalid'],
    [JSON.stringify({ ...good, mint: '0'.repeat(44) }), 'TokenRecordInvalid'],
    [JSON.stringify({ ...good, explorer: undefined }), 'TokenRecordInvalid'],
    ['{not json', 'SyntaxError'],
  ]) {
    fs.writeFileSync(file, body);
    assert.throws(() => rclaw.readRecord(file), (e) => e.name === name, body.slice(0, 40));
  }
  fs.writeFileSync(file, JSON.stringify(good));
  assert.strictEqual(rclaw.readRecord(file).mint, MINT);
  assert.ok(!rclaw.REQUIRED_KEYS.includes('presale'), 'a record is not whole without a sale');
  assert.throws(() => rclaw.readRecord(path.join(dir, 'absent.json')), (e) => e.code === 'ENOENT');
});

// ── the route ────────────────────────────────────────────────────────────

test('GET /api/token serves the record without its editor notes or a sale', async () => {
  await serve(async (port) => {
    const res = await get(port);
    assert.strictEqual(res.status, 200);
    const t = res.body.token;
    assert.strictEqual(t.mint, MINT);
    assert.ok(!('basis' in t) && !('_comment' in t), 'editor notes stay in the file');
    assert.ok(!('presale' in t), 'a presale block on the public route');
    assert.ok(!SALE_WORDS.test(JSON.stringify(res.body)), 'the public route names a sale');
    // §4: no dollar figure on a public payload.
    assert.ok(!/\$\s?\d/.test(JSON.stringify(res.body)), 'a dollar amount on a public route');
  });
});

test('a copy that still carried a sale block would not serve it', async () => {
  const real = rclaw.readRecord;
  rclaw.readRecord = () => ({ ...real(), presale: { ...SALE, _comment: 'note' } });
  try {
    await serve(async (port) => {
      const res = await get(port);
      assert.strictEqual(res.status, 200);
      assert.strictEqual(res.body.token.mint, MINT, 'the rest of the record is served');
      assert.ok(!('presale' in res.body.token));
    });
  } finally { rclaw.readRecord = real; }
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
  // Anchored to the token link itself: the locks section links Solscan's account pages, which is
  // not a link to a mint.
  assert.ok(!html.includes('solscan.io/token/'), 'a link to another mint beside this address');
  assert.ok(!html.includes('View on Solscan'), 'the explorer row is not painted');
  assert.ok(html.includes(MINT));
});

test('a supply that is not digits is unreadable, never a number', () => {
  assert.strictEqual(page.supplyText('1000000000'), '1,000,000,000');
  for (const v of ['1e9', '', null, '-5', '1,000']) assert.strictEqual(page.supplyText(v), null, String(v));
  const r = record(); r.supply_tokens = '1e9';
  assert.ok(page.render(r).includes('unreadable'));
});


test('the page renders no sale, even from a record that holds one', () => {
  const html = page.render({ ...record(), presale: SALE });
  assert.ok(html.includes(MINT), 'the page rendered the token');
  assert.ok(!SALE_WORDS.test(html), 'the page names a sale');
  assert.ok(!html.includes('tok-presale') && !html.includes('per SOL'), 'a sale block was painted');
  for (const k of Object.keys(page)) assert.ok(!/presale|sale/i.test(k), `token-page.js still exports ${k}`);
});

// ── the doors ────────────────────────────────────────────────────────────

test('no page loads the sale bar, and the bar is gone', () => {
  assert.ok(!fs.existsSync(path.join(PUB, 'js', 'presale.js')), 'presale.js is still served');
  const pages = fs.readdirSync(PUB).filter((f) => f.endsWith('.html'));
  assert.ok(pages.length > 30, 'the scan read the pages');
  for (const f of pages) {
    const html = fs.readFileSync(path.join(PUB, f), 'utf8');
    assert.ok(!html.includes('presale.js'), `${f} loads the sale bar`);
  }
  const manifest = JSON.parse(fs.readFileSync(path.join(APP, 'test', 'asset_versions.json'), 'utf8'));
  assert.ok(!('js/presale.js' in manifest), 'the manifest still versions the bar');
});

test('the landing page carries no token strip, and the token is still one menu link away', () => {
  const landing = fs.readFileSync(path.join(PUB, 'index.html'), 'utf8');
  assert.ok(!landing.includes('hero-token'), 'the hero still carries the token strip');
  assert.ok(!landing.includes('token-page.js'), 'the landing page loads the token page\'s script for nothing');
  assert.ok(!SALE_WORDS.test(landing), 'the landing page names a sale');
  assert.match(landing, /<a href="\/token" data-i18n="nav.token">/);
  assert.match(fs.readFileSync(path.join(APP, 'server.js'), 'utf8'),
    /app\.get\('\/token', [^\n]*'token\.html'\)/);
});

test('/token names no sale: its text, its link preview and its images', () => {
  const html = fs.readFileSync(path.join(PUB, 'token.html'), 'utf8');
  assert.ok(!SALE_WORDS.test(html), 'token.html names a sale');
  assert.ok(!/tokenomics/i.test(html), 'the tokenomics card (with its presale slice) is still on the page');
  assert.match(html, /<meta property="og:image" content="https:\/\/www\.humanoid-traders\.com\/og_image_1200x630\.jpg">/);
  assert.match(html, /<meta name="twitter:image" content="https:\/\/www\.humanoid-traders\.com\/og_image_1200x630\.jpg">/);
  for (const f of ['rclaw_presale_og_1200x630.jpg', 'rclaw_tokenomics_1000x1000.png']) {
    assert.ok(!fs.existsSync(path.join(PUB, f)), `${f} is still served`);
  }
  assert.ok(fs.existsSync(path.join(PUB, 'og_image_1200x630.jpg')), 'the preview names an image the site has');
});

test('no string in any language names a sale', () => {
  const { STRINGS, LANGS } = require('../public/js/i18n.js');
  let read = 0;
  for (const [key, byLang] of Object.entries(STRINGS)) {
    assert.ok(!/^(bar\.presale|tok\.presale|home\.token_)/.test(key), `${key} is still in the dictionary`);
    for (const [lang, text] of Object.entries(byLang)) {
      read += 1;
      assert.ok(!SALE_WORDS.test(text), `${key} [${lang}] names a sale: ${text.slice(0, 80)}`);
    }
  }
  assert.ok(read > 1000 && LANGS.length === 14, 'the scan read the dictionary');
  // The three sentences that stayed say what the page shows, in every language.
  for (const l of LANGS) {
    for (const k of ['tok.lede', 'tok.not_offer', 'tok.audits_body']) assert.ok(STRINGS[k][l.code], `${k} has no ${l.code}`);
    assert.ok(STRINGS['tok.audits_body'][l.code].includes('2025-02-03'), `${l.code}: the vesting program's deploy date`);
    assert.ok(!STRINGS['tok.audits_body'][l.code].includes('2024-06-29'), `${l.code}: the sale program's deploy date`);
  }
  assert.strictEqual(STRINGS['tok.audits_body'].en, page.AUDITS_BODY);
});

test('the docs the site serves name no sale', () => {
  const dir = path.join(APP, 'content', 'docs');
  const docs = fs.readdirSync(dir).filter((f) => f.endsWith('.md'));
  assert.ok(docs.includes('token-roadmap.md') && docs.length > 5, 'the scan read the docs');
  for (const f of docs) {
    assert.ok(!SALE_WORDS.test(fs.readFileSync(path.join(dir, f), 'utf8')), `/docs/${f} names a sale`);
  }
});

test('the page points at the bot command that exists', () => {
  const catalog = fs.readFileSync(path.join(APP, '..', 'bot', 'skills', 'command_catalog.py'), 'utf8');
  assert.match(catalog, /\("rclaw", "the \$RCLAW token/);
  const i18n = require('../public/js/i18n.js');
  for (const l of i18n.LANGS) {
    assert.ok(i18n.translate('tok.bot_hint', l.code).endsWith('/rclaw'), l.code);
  }
});
