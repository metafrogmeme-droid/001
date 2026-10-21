'use strict';
/**
 * The Pi Browser app in the reader's language.
 *
 * The app's sentences are written once, in English, where they are drawn
 * (`T('…')`), and app/scripts/build_pi_i18n.js collects them from exactly the
 * scripts the page loads. These tests hold the pieces together:
 *
 *   - every sentence is translated in every language, with its {placeholders},
 *     and no language keeps a sentence the app no longer says;
 *   - the committed chunks are the built chunks;
 *   - the collector sees every way the app draws text (a T on a variable is a
 *     sentence no table can hold, so each one is named here with its reason);
 *   - the server opens the page in the Pi Browser's language, loads that
 *     language's words before anything draws, and addresses each chunk by its
 *     content;
 *   - the runtime falls back to English, never to a key or a blank.
 *
 * The browser half (switching in About, the coach asked in the language on
 * screen) is the_pi_app_speaks_the_readers_language.smoke.test.js.
 */
const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');

const build = require('../scripts/build_pi_i18n.js');
const PiI18n = require('../public/js/pi-i18n.js');
const piapp = require('../routes/piapp');
const { codeOnly } = require('./helpers/code_only');

const PUB = path.join(__dirname, '..', 'public');

test('every sentence the Pi app draws is translated in every language, placeholders kept', () => {
  const out = build.build();
  assert.deepStrictEqual(out.problems, [], 'run node app/scripts/build_pi_i18n.js --check for the list');
  for (const code of PiI18n.CODES.filter((c) => c !== 'en')) {
    assert.deepStrictEqual(out.missing[code], [], `${code} lacks sentences`);
  }
  // Seventeen languages: the site's fourteen and three more.
  assert.strictEqual(PiI18n.CODES.length, 17);
  for (const c of ['vi', 'id', 'fil']) assert.ok(PiI18n.CODES.includes(c), c);
});

test('the committed chunks are the built chunks, one per language and no other', () => {
  const out = build.build();
  const dir = path.join(PUB, 'js', 'pi-i18n');
  const onDisk = fs.readdirSync(dir).sort();
  assert.deepStrictEqual(onDisk, Object.keys(out.chunks).map((c) => `${c}.js`).sort());
  for (const [code, text] of Object.entries(out.chunks)) {
    assert.strictEqual(fs.readFileSync(path.join(dir, `${code}.js`), 'utf8'), text,
      `public/js/pi-i18n/${code}.js is stale: run node app/scripts/build_pi_i18n.js`);
  }
});

/**
 * A copy of the translation sources in a temporary directory, with `edit`
 * applied to one language's pairs: build() reads it instead of the tree's.
 */
function doctored(code, edit) {
  const os = require('node:os');
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'pi-i18n-'));
  const src = path.join(__dirname, '..', 'i18n', 'pi');
  for (const f of fs.readdirSync(src)) fs.copyFileSync(path.join(src, f), path.join(dir, f));
  const file = path.join(dir, `${code}${build.SUFFIX}`);
  fs.writeFileSync(file, JSON.stringify(edit(JSON.parse(fs.readFileSync(file, 'utf8')))));
  return dir;
}

test('a placeholder dropped or misspelt in a translation is a problem the build names', () => {
  assert.deepStrictEqual(build.placeholders('Opened {sym} {dir} at {price}.'), ['{dir}', '{price}', '{sym}']);
  const en = 'Position closed: {what} at {price}.';
  const set = (text) => (pairs) => pairs.map((p) => (p[0] === en ? [en, text] : p));
  const dropped = build.build({ srcDir: doctored('es', set('Posición cerrada: {what}.')) });
  assert.ok(dropped.problems.some((x) => x.startsWith('es: "Position closed: {what} at {price}."')), dropped.problems.join('\n'));
  const misspelt = build.build({ srcDir: doctored('es', set('Posición cerrada: {what} a {pirce}.')) });
  assert.ok(misspelt.problems.some((x) => x.startsWith('es: "Position closed: {what} at {price}."')), misspelt.problems.join('\n'));
  // Both arms: the same sentence with both placeholders, reordered, is clean.
  const fine = build.build({ srcDir: doctored('es', set('A {price}: posición cerrada, {what}.')) });
  assert.deepStrictEqual(fine.problems, []);
});

test('a sentence the app no longer says, or one a language lacks, is a problem the build names', () => {
  const stale = build.build({ srcDir: doctored('vi', (pairs) => pairs.concat([['A sentence nobody draws', 'Một câu']])) });
  assert.ok(stale.problems.some((x) => /vi\.pairs\.json: "A sentence nobody draws" is no longer in the Pi app/.test(x)), stale.problems.join('\n'));
  const gone = build.build({ srcDir: doctored('vi', (pairs) => pairs.filter((p) => p[0] !== 'Season standings')) });
  assert.deepStrictEqual(gone.missing.vi, ['Season standings']);
  assert.ok(gone.problems.includes('vi: 1 sentence(s) not translated'));
});

test('a Duel sentence borrows the site\'s translation only while the site\'s English is the same sentence', () => {
  // 'Settles in {t}' is the renderer's (duel.st_settles). Taken out of the
  // Spanish file, it is borrowed from the site, if the site's English matches.
  const srcDir = doctored('es', (pairs) => pairs.filter((p) => p[0] !== 'Settles in {t}'));
  const site = require('../public/js/i18n.js').STRINGS;
  const same = build.build({ srcDir, site: { ...site, 'duel.st_settles': { en: 'Settles in {t}', es: 'Se decide en {t}' } } });
  assert.deepStrictEqual(same.missing.es, []);
  assert.match(same.chunks.es, /\["Settles in \{t\}","Se decide en \{t\}"\]/);
  const other = build.build({ srcDir, site: { ...site, 'duel.st_settles': { en: 'Settles {t} from now', es: 'Se decide en {t}' } } });
  assert.deepStrictEqual(other.missing.es, ['Settles in {t}']);
});

test('the collector reads the page\'s own scripts, and finds every kind of sentence in them', () => {
  const files = build.scriptFiles().map((f) => path.relative(PUB, f).replace(/\\/g, '/'));
  // The list is the page's (routes/piapp.js SCRIPTS), not a second copy.
  const page = piapp.SCRIPTS.map((s) => s.split('?')[0].replace(/^\//, ''))
    .filter((s) => s.startsWith('js/') && !s.startsWith('js/pi-i18n'));
  assert.deepStrictEqual(files, page);
  const said = build.sentences();
  const from = (en) => [...(said.get(en) || { files: [] }).files].map((f) => path.basename(f));
  // A T literal in each module the app draws with.
  assert.ok(from('Position opened.').includes('miniapp-view.js'));
  assert.ok(from('Updated {time}').includes('arena-session.js'));
  assert.ok(from('{n} trades').includes('embed-arena-view.js'));
  assert.ok(from('Daily Duel').includes('pi-duel.js'));
  assert.ok(from('Verified in your browser').includes('pi-receipt.js'));
  assert.ok(from('Ask the Claw').includes('pi-coach.js'));
  assert.ok(from('Why? Ask the Claw').includes('pi-signals.js'));
  assert.ok(from('Season standings').includes('pi-leaders.js'));
  assert.ok(from('Beat the Claw').includes('pi-arena.js'));
  assert.ok(from('entry').includes('embed-row.js'));
  // A tab's label, which the session draws through T.
  assert.ok(from('Leaders').includes('pi-leaders.js'));
  assert.ok(from('About').includes('pi-arena.js'));
  // The Duel renderer's words, with the site key they borrow from.
  assert.ok(from('Settles in {t}').includes('duel.js'));
  assert.ok(said.get('Settles in {t}').keys.has('duel.st_settles'));
  // Comments are not sentences: pi-i18n.js's header quotes T('Position
  // opened.') and is not collected (it is not in the list), and a comment in
  // a collected file is blanked first.
  assert.ok(!said.has('English sentence'));
});

/**
 * A T( whose first argument is not a literal draws a sentence the collector
 * cannot see, so no language has it. Each one is named with what supplies its
 * English, and a named site that is gone fails too.
 */
const DYNAMIC_T = {
  'arena-session.js': ['T(x.label)', 'a tab block\'s `label:` literal, collected from the pi-*.js blocks'],
};

test('every T in the Pi app is called on a literal, except the named ones', () => {
  const found = {};
  for (const file of build.scriptFiles()) {
    const src = codeOnly(fs.readFileSync(file, 'utf8'));
    const re = /(?<![\w$.])T\(\s*([^'\s)][^)]*)\)/g;
    let m;
    while ((m = re.exec(src))) {
      // The helpers' own definitions and pass-throughs are not draws.
      const line = src.slice(src.lastIndexOf('\n', m.index) + 1, src.indexOf('\n', m.index));
      if (/function T\(|function TR\(|return T\(en\)/.test(line)) continue;
      (found[path.basename(file)] = found[path.basename(file)] || []).push('T(' + m[1].trim() + ')');
    }
  }
  const want = Object.fromEntries(Object.entries(DYNAMIC_T).map(([f, [call]]) => [f, [call]]));
  assert.deepStrictEqual(found, want);
});

test('the duel renderer is asked in English on the Pi page, so the table holds its words', () => {
  const src = codeOnly(fs.readFileSync(path.join(PUB, 'js', 'pi-duel.js'), 'utf8'));
  assert.match(src, /function TR\(key, en\) \{ return T\(en\); \}/);
  // Every renderer call that draws words passes TR.
  assert.match(src, /D\.roundCard\([^;]*, TR, undefined,/);
  assert.match(src, /D\.accuracyLabel\(me\.accuracy, TR\)/);
  assert.match(src, /D\.marksLabel\(me\.marks, TR\)/);
  assert.match(src, /D\.pickLabel\(r\.pick, r, TR\)/);
});

// ── the server ────────────────────────────────────────────────────────────

function req(query, header) {
  return { query: query || {}, get: (h) => (String(h).toLowerCase() === 'accept-language' ? header : undefined) };
}

test('the page opens in the Pi Browser\'s language, ?lang first, English otherwise', () => {
  const all = piapp._chunks();
  assert.strictEqual(piapp._pickLang(req({}, 'es-ES,es;q=0.9,en;q=0.8'), all), 'es');
  assert.strictEqual(piapp._pickLang(req({}, 'pt-BR'), all), 'pt');
  assert.strictEqual(piapp._pickLang(req({}, 'zh-TW,zh;q=0.9'), all), 'zh');
  assert.strictEqual(piapp._pickLang(req({}, 'in-ID'), all), 'id', 'Java\'s old tag for Indonesian');
  assert.strictEqual(piapp._pickLang(req({}, 'tl-PH'), all), 'fil', 'Tagalog is Filipino');
  assert.strictEqual(piapp._pickLang(req({}, 'vi-VN,vi;q=0.9'), all), 'vi');
  // q-order, not header order.
  assert.strictEqual(piapp._pickLang(req({}, 'sw;q=0.9,fr;q=0.8,de;q=0.95'), all), 'de');
  // A language this app does not speak is English, not an error.
  assert.strictEqual(piapp._pickLang(req({}, 'sw-KE'), all), 'en');
  assert.strictEqual(piapp._pickLang(req({}, ''), all), 'en');
  assert.strictEqual(piapp._pickLang(req({}, undefined), all), 'en');
  // q=0 is "not this one".
  assert.strictEqual(piapp._pickLang(req({}, 'ja;q=0,ko'), all), 'ko');
  // ?lang beats the header; a junk ?lang does not.
  assert.strictEqual(piapp._pickLang(req({ lang: 'ar' }, 'es'), all), 'ar');
  assert.strictEqual(piapp._pickLang(req({ lang: '<script>' }, 'es'), all), 'es');
  assert.strictEqual(piapp._pickLang(req({ lang: ['es', 'fr'] }, 'de'), all), 'de');
  // A language whose chunk is not built is not offered, whatever asks for it.
  const some = { es: all.es };
  assert.strictEqual(piapp._pickLang(req({ lang: 'fr' }, 'fr'), some), 'en');
  assert.strictEqual(piapp._pickLang(req({}, 'fr,es;q=0.5'), some), 'es');
});

test('each chunk is addressed by its own content', () => {
  const all = piapp._chunks();
  assert.deepStrictEqual(Object.keys(all).sort(), PiI18n.CODES.filter((c) => c !== 'en').sort());
  for (const [code, url] of Object.entries(all)) {
    const sha = crypto.createHash('sha256').update(fs.readFileSync(path.join(PUB, 'js', 'pi-i18n', `${code}.js`))).digest('hex').slice(0, 8);
    assert.strictEqual(url, `/js/pi-i18n/${code}.js?v=${sha}`);
  }
});

test('the page loads the language\'s words right after the module that holds them, before anything draws', () => {
  const all = piapp._chunks();
  const scripts = (html) => [...html.matchAll(/<script src="([^"]+)"/g)].map((m) => m[1]);
  const es = piapp._page('es', all);
  assert.match(es, /<html lang="es" dir="ltr">/);
  const list = scripts(es);
  const at = list.findIndex((x) => x.startsWith('/js/pi-i18n.js?v='));
  assert.ok(at >= 0, 'pi-i18n.js is not loaded');
  assert.strictEqual(list[at + 1], all.es);
  assert.ok(list.findIndex((x) => x.startsWith('/js/miniapp-view.js?')) > at + 1);
  assert.ok(list.findIndex((s) => s.startsWith('/js/pi-arena.js')) > at + 1);
  // Arabic is right to left.
  assert.match(piapp._page('ar', all), /<html lang="ar" dir="rtl">/);
  // English loads the module and no chunk.
  const en = scripts(piapp._page('en', all));
  assert.ok(en.some((x) => x.startsWith('/js/pi-i18n.js?v=')));
  assert.ok(!en.some((s) => s.startsWith('/js/pi-i18n/')));
  // A language with no chunk is English, never a script that 404s.
  assert.match(piapp._page('fr', { es: all.es }), /<html lang="en" dir="ltr">/);
  // The chunk addresses About uses to switch in place, in a meta the page parses.
  const meta = es.match(/<meta name="pi-i18n" content="([^"]*)">/);
  assert.ok(meta, 'no pi-i18n meta');
  const decoded = meta[1].replace(/&quot;/g, '"').replace(/&lt;/g, '<').replace(/&gt;/g, '>').replace(/&amp;/g, '&');
  assert.deepStrictEqual(JSON.parse(decoded), all);
});

test('GET /pi answers in the header\'s language and says the page varies by it', async () => {
  const express = require('express');
  const http = require('node:http');
  const app = express();
  app.use('/pi', require('../routes/piapp'));
  const server = http.createServer(app);
  await new Promise((r) => server.listen(0, '127.0.0.1', r));
  try {
    const base = `http://127.0.0.1:${server.address().port}`;
    const vi = await fetch(`${base}/pi/`, { headers: { 'accept-language': 'vi-VN,vi;q=0.9' } });
    assert.strictEqual(vi.status, 200);
    assert.match(vi.headers.get('vary') || '', /Accept-Language/i);
    assert.match(await vi.text(), /<html lang="vi" dir="ltr">/);
    const en = await fetch(`${base}/pi/?lang=xx`, { headers: { 'accept-language': 'sw' } });
    assert.match(await en.text(), /<html lang="en" dir="ltr">/);
  } finally {
    server.close();
  }
});

// ── the runtime ───────────────────────────────────────────────────────────

test('the runtime: English when a language lacks a sentence, placeholders filled, nothing blank', () => {
  // A fresh module, so this test's table is its own.
  delete require.cache[require.resolve('../public/js/pi-i18n.js')];
  const I = require('../public/js/pi-i18n.js');
  assert.strictEqual(I.t('Position opened.'), 'Position opened.');
  assert.strictEqual(I.t('min {n} {unit}', { n: 10, unit: 'credits' }), 'min 10 credits');
  // A missing value leaves its placeholder, never "undefined".
  assert.strictEqual(I.t('min {n} {unit}', { n: 10 }), 'min 10 {unit}');
  I.add('es', [['Position opened.', 'Posición abierta.'], ['Empty', ''], ['bad']]);
  // A language whose chunk is not loaded stays English.
  assert.strictEqual(I.use('fr'), 'en');
  assert.strictEqual(I.t('Position opened.'), 'Position opened.');
  assert.strictEqual(I.use('es'), 'es');
  assert.strictEqual(I.t('Position opened.'), 'Posición abierta.');
  // A blank translation is not added: the English stands.
  assert.strictEqual(I.t('Empty'), 'Empty');
  assert.strictEqual(I.t('Not in the table'), 'Not in the table');
  // Unknown codes fall back to English.
  assert.strictEqual(I.use('xx'), 'en');
  assert.strictEqual(I.dirOf('ar'), 'rtl');
  assert.strictEqual(I.dirOf('es'), 'ltr');
  assert.strictEqual(I.fromAcceptLanguage('fil-PH'), 'fil');
  delete require.cache[require.resolve('../public/js/pi-i18n.js')];
});

test('the chunks are pairs, not "name": value lines a secret scanner keys on', () => {
  const src = fs.readFileSync(path.join(PUB, 'js', 'pi-i18n', 'es.js'), 'utf8');
  for (const line of src.split('\n')) {
    if (!line.startsWith('    [')) continue;
    assert.match(line, /^ {4}\[".*",".*"\],$/, line);
  }
  for (const code of PiI18n.CODES.filter((c) => c !== 'en')) {
    const raw = JSON.parse(fs.readFileSync(path.join(build.SRC_DIR, `${code}${build.SUFFIX}`), 'utf8'));
    assert.ok(Array.isArray(raw) && raw.every((p) => Array.isArray(p) && p.length === 2), code);
  }
});

/**
 * A source git ignores is a translation only this checkout has. Indonesian's
 * was `id.json`, the name of a Solana keypair that .gitignore denies
 * everywhere; every test passed here on the untracked copy, and CI, which
 * checks out only what was committed, had no Indonesian at all. So git is
 * asked directly: no translation source may be ignored.
 */
test('git keeps every translation source: none is ignored', () => {
  const { spawnSync } = require('node:child_process');
  const repo = path.join(__dirname, '..', '..');
  const files = PiI18n.CODES.filter((c) => c !== 'en').map((c) => path.relative(repo, path.join(build.SRC_DIR, `${c}${build.SUFFIX}`)));
  const r = spawnSync('git', ['check-ignore', '--no-index', ...files], { cwd: repo, encoding: 'utf8' });
  // 1: nothing matched an ignore rule. 0: something did (listed on stdout).
  assert.strictEqual(r.status, 1, `ignored by git: ${r.stdout || r.stderr}`);
  // Both arms: the same question about the old name is answered "ignored".
  const old = spawnSync('git', ['check-ignore', '--no-index', 'app/i18n/pi/id.json'], { cwd: repo, encoding: 'utf8' });
  assert.strictEqual(old.status, 0, 'the keypair rule no longer covers id.json; this test is asking the wrong question');
});
