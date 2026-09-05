'use strict';
/**
 * The dictionary ships per language, and the shipped files are the built files.
 *
 * Every page loaded public/js/i18n.js whole: 1.9 MB of JavaScript, fourteen
 * languages of strings, before the first paint, for a reader who needs one of
 * them — or none, since the English is already in the markup. The build
 * (scripts/build_i18n.js) writes a core with the runtime and the English
 * fallback, plus one chunk per language; a page loads the core and, for a
 * non-English reader, exactly one chunk.
 *
 * Three things have to stay true for that to be an improvement rather than a
 * regression, and each is pinned here from the outside:
 *
 *   1. what is committed is what the source builds — the marketing site's
 *      "committed site is the built site" rule, because a chunk that lags the
 *      source ships stale strings under a fresh-looking version;
 *   2. the chunk is in place BEFORE the next script runs at parse time — the
 *      guarantee test/i18n_boot_order.test.js pins for the monolith, which a
 *      lazily-loaded dictionary would quietly break for every synchronous
 *      renderer;
 *   3. a chunk that never arrives leaves English on the page, never blank.
 */
const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

const builder = require('../scripts/build_i18n');
const PUB = path.join(__dirname, '..', 'public');
const SOURCE = require(builder.SRC);                 // every key, every language
const SOURCE_TEXT = fs.readFileSync(builder.SRC, 'utf8');
const built = builder.build();
const outputs = builder.outputs();
const NON_EN = SOURCE.LANGS.map((l) => l.code).filter((c) => c !== 'en');

/** Run a chunk the way the browser would: it assigns into window.RCI18N_DICTS. */
function runChunk(code, win) {
  new Function('window', outputs[`js/i18n/${code}.js`])(win);   // eslint-disable-line no-new-func
  return win.RCI18N_DICTS[code];
}

/** Boot the CORE as a browser would, document still parsing, with a write spy. */
function bootCore({ stored, navLang, readyState = 'loading' }) {
  const written = [];
  const appended = [];
  const listeners = {};
  const htmlAttrs = {};
  const doc = {
    readyState,
    documentElement: { setAttribute: (k, v) => { htmlAttrs[k] = v; }, getAttribute: (k) => htmlAttrs[k] },
    head: { appendChild: (el) => appended.push(el) },
    write: (s) => written.push(s),
    addEventListener: (ev, fn) => { (listeners[ev] = listeners[ev] || []).push(fn); },
    getElementById: () => null,
    querySelector: () => null,
    querySelectorAll: () => [],
    createElement: () => ({ setAttribute() {}, appendChild() {}, addEventListener() {} }),
    cookie: '',
  };
  const win = {};
  const saved = {
    window: global.window, document: global.document,
    navigator: Object.getOwnPropertyDescriptor(global, 'navigator'),
    localStorage: global.localStorage,
  };
  const enter = () => {
    global.window = win;
    global.document = doc;
    Object.defineProperty(global, 'navigator', { value: { language: navLang }, configurable: true });
    global.localStorage = { getItem: () => stored, setItem() {} };
  };
  const leave = () => {
    global.window = saved.window;
    global.document = saved.document;
    if (saved.navigator) Object.defineProperty(global, 'navigator', saved.navigator);
    global.localStorage = saved.localStorage;
  };
  enter();
  delete require.cache[require.resolve(builder.CORE)];
  try { require(builder.CORE); } finally { delete require.cache[require.resolve(builder.CORE)]; leave(); }
  const inDom = (fn) => { enter(); try { return fn(); } finally { leave(); } };
  return {
    api: win.RCI18N, win, doc, written, appended, htmlAttrs, inDom,
    fireDomReady: () => inDom(() => (listeners.DOMContentLoaded || []).forEach((fn) => fn())),
  };
}

// ── 1. committed == built ───────────────────────────────────────────────────

test('the committed core and chunks are what the source builds, byte for byte', () => {
  const stale = [];
  for (const [rel, text] of Object.entries(outputs)) {
    const file = path.join(PUB, rel);
    if (!fs.existsSync(file) || fs.readFileSync(file, 'utf8') !== text) stale.push(rel);
  }
  assert.deepEqual(stale, [], 'run `node app/scripts/build_i18n.js` and commit the result');
  const extra = fs.readdirSync(path.join(PUB, 'js', 'i18n')).filter((f) => !(`js/i18n/${f}` in outputs));
  assert.deepEqual(extra, [], 'a chunk nobody builds any more is a chunk nobody updates');
});

test('the build is deterministic', () => {
  assert.deepEqual(builder.outputs(), outputs);
});

test('each chunk carries exactly its language; the core carries English and nothing else', () => {
  const win = {};
  for (const code of NON_EN) {
    const dict = runChunk(code, win);
    let expected = 0;
    for (const [key, entry] of Object.entries(SOURCE.STRINGS)) {
      if (entry[code] != null) { expected += 1; assert.equal(dict[key], entry[code], `${code}:${key}`); }
      else assert.ok(!(key in dict), `${code}:${key} is in the chunk but not in the source`);
    }
    assert.equal(Object.keys(dict).length, expected, `${code} chunk has extra keys`);
  }
  const { api } = bootCore({ stored: 'en', navLang: 'en-US' });
  for (const [key, entry] of Object.entries(SOURCE.STRINGS)) {
    assert.deepEqual(api.STRINGS[key], { en: entry.en }, key);
  }
  assert.equal(Object.keys(api.STRINGS).length, Object.keys(SOURCE.STRINGS).length);
});

test('the payload a page loads is a fraction of what it loaded', () => {
  const total = SOURCE_TEXT.length;
  assert.ok(outputs['js/i18n-core.js'].length < total * 0.2,
    `core is ${outputs['js/i18n-core.js'].length} bytes of ${total}`);
  for (const code of NON_EN) {
    assert.ok(outputs[`js/i18n/${code}.js`].length < total * 0.15, `${code} chunk is not a fraction`);
  }
});

// ── 2. parse-time boot order, for the core ──────────────────────────────────

test('a Japanese reader gets one blocking chunk tag at parse time, versioned by content', () => {
  const { api, written } = bootCore({ stored: 'ja', navLang: 'en-US' });
  assert.equal(api.getLang(), 'ja', 'the language is known while the document is still parsing');
  assert.deepEqual(written, [`<script src="/js/i18n/ja.js?v=${built.versions.ja}"></script>`]);
  assert.match(built.versions.ja, /^[0-9a-f]{8}$/);
});

test('an English reader loads no chunk at all', () => {
  const { written, appended } = bootCore({ stored: null, navLang: 'en-GB' });
  assert.deepEqual(written, []);
  assert.deepEqual(appended, []);
});

test('the chunk version moves when its strings move', () => {
  const a = builder.build().versions.es;
  const text = outputs['js/i18n/es.js'];
  assert.notEqual(require('node:crypto').createHash('sha256').update(text + ' ').digest('hex').slice(0, 8), a);
  assert.equal(require('node:crypto').createHash('sha256').update(text).digest('hex').slice(0, 8), a);
});

test('before the chunk runs the core answers in English; after, in the reader\'s language', () => {
  const { api, win, inDom } = bootCore({ stored: 'ja', navLang: 'en-US' });
  const key = 'aria.jump_radar';
  assert.equal(api.translate(key, 'ja'), SOURCE.STRINGS[key].en, 'unloaded is English, never blank');
  runChunk('ja', win);                                    // what the written tag does
  assert.equal(api.translate(key, 'ja'), SOURCE.STRINGS[key].ja);
  assert.equal(api.translate(key, 'en'), SOURCE.STRINGS[key].en);
  inDom(() => assert.doesNotThrow(() => api.apply(global.document, 'ja')));
});

test('an unknown key is still null, and a key the chunk lacks falls back to English', () => {
  const { api, win } = bootCore({ stored: 'ja', navLang: 'en-US' });
  runChunk('ja', win);
  assert.equal(api.translate('no.such.key', 'ja'), null);
  win.RCI18N_DICTS.ja = { 'nav.track': 'x' };              // a chunk with one key
  assert.equal(api.translate('nav.track', 'ja'), 'x');
  assert.equal(api.translate('nav.proof', 'ja'), SOURCE.STRINGS['nav.proof'].en);
});

test('switching language after parsing appends an async script and does not write into a finished document', () => {
  const { api, written, appended, inDom } = bootCore({ stored: 'en', navLang: 'en-US', readyState: 'complete' });
  inDom(() => api.setLang('es', { persistServer: false }));
  assert.equal(api.getLang(), 'es');
  assert.deepEqual(written, [], 'document.write after parsing would replace the page');
  assert.equal(appended.length, 1);
  assert.equal(appended[0].src, `/js/i18n/es.js?v=${built.versions.es}`);
  // The callbacks run in the page, where `document` exists — as the browser would call them.
  inDom(() => assert.doesNotThrow(() => appended[0].onload()));    // re-applies once it lands
  inDom(() => assert.doesNotThrow(() => appended[0].onerror()));   // a failed chunk leaves English
});

test('the same language is never fetched twice', () => {
  const { api, appended, inDom, win } = bootCore({ stored: 'en', navLang: 'en-US', readyState: 'complete' });
  inDom(() => api.setLang('es', { persistServer: false }));
  runChunk('es', win);
  inDom(() => api.setLang('en', { persistServer: false }));
  inDom(() => api.setLang('es', { persistServer: false }));
  assert.equal(appended.length, 1);
});

test('the deferred DOM pass still runs on the core', () => {
  const { api, fireDomReady } = bootCore({ stored: 'ja', navLang: 'en-US' });
  fireDomReady();
  assert.equal(api.getLang(), 'ja');
});

// ── 3. the pages and the source ─────────────────────────────────────────────

test('every page loads the core and no page loads the whole dictionary', () => {
  const pages = fs.readdirSync(PUB).filter((f) => f.endsWith('.html'));
  const whole = pages.filter((f) => /\/js\/i18n\.js(\?|")/.test(fs.readFileSync(path.join(PUB, f), 'utf8')));
  assert.deepEqual(whole, [], 'these pages still load 1.9 MB of dictionary');
  const core = pages.filter((f) => /\/js\/i18n-core\.js\?v=\d+/.test(fs.readFileSync(path.join(PUB, f), 'utf8')));
  assert.ok(core.length >= 30, `only ${core.length} pages load the core`);
  for (const f of core) {
    const src = fs.readFileSync(path.join(PUB, f), 'utf8');
    assert.doesNotMatch(src, /i18n-core\.js\?v=\d+"[^>]*\b(defer|async)\b/,
      `${f} defers the core — a synchronous renderer would then paint English`);
  }
});

test('the source keeps the loader switched off, so the tests boot the monolith they always did', () => {
  assert.ok(SOURCE_TEXT.includes(builder.SPLIT_LINE));
  assert.ok(SOURCE_TEXT.includes(builder.CHUNKS_LINE));
  assert.ok(outputs['js/i18n-core.js'].includes('var SPLIT = true;'));
  assert.ok(!outputs['js/i18n-core.js'].includes(builder.SPLIT_LINE));
});

test('the runtime in the core is the runtime in the source', () => {
  // Everything after the dictionary is copied verbatim; a fix to the source
  // runtime that did not reach the core would be a split brain.
  const tail = (text) => text.slice(text.indexOf('  function normalize('));
  const coreTail = tail(outputs['js/i18n-core.js']).replace('var SPLIT = true;', builder.SPLIT_LINE)
    .replace(/  var CHUNKS = \{[^\n]*\};/, builder.CHUNKS_LINE);
  assert.equal(coreTail, tail(SOURCE_TEXT));
});
