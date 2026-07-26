'use strict';
// The language has to be known before any page script renders.
//
// i18n.js used to resolve the reader's language inside its DOMContentLoaded
// handler. dashboard.js renders synchronously while the document is still
// parsing — i.e. BEFORE that event — so every T() call in that first render
// read current === 'en' and painted English. apply() does not help: it rewrites
// attributes on markup that already exists, and there is no MutationObserver,
// so for a view the user never leaves and comes back to, the strings stayed
// English forever in an otherwise fully translated page.
//
// Resolving the language reads localStorage and navigator. It needs no DOM. So
// it now happens at parse time, and only the DOM work waits for the event.
//
// These tests boot the real module against a stub document that is still
// "loading", which is exactly the state dashboard.js runs in.

const test = require('node:test');
const assert = require('node:assert');
const path = require('node:path');

const MODULE = path.join(__dirname, '..', 'public', 'js', 'i18n.js');

/** Boot i18n.js as a browser would, with the document still parsing. */
function bootInDom({ stored, navLang }) {
  const listeners = {};
  const htmlAttrs = {};
  const docEl = {
    setAttribute: (k, v) => { htmlAttrs[k] = v; },
    getAttribute: (k) => htmlAttrs[k],
  };
  const doc = {
    readyState: 'loading',           // still parsing — the state that broke it
    documentElement: docEl,
    addEventListener: (ev, fn) => { (listeners[ev] = listeners[ev] || []).push(fn); },
    getElementById: () => null,
    querySelector: () => null,
    querySelectorAll: () => [],
    createElement: () => ({ setAttribute() {}, appendChild() {}, addEventListener() {} }),
  };
  const win = {};
  const saved = {
    window: global.window, document: global.document,
    navigator: Object.getOwnPropertyDescriptor(global, 'navigator'),
    localStorage: global.localStorage,
  };
  global.window = win;
  global.document = doc;
  // `navigator` is a getter-only global in Node 22 — plain assignment throws.
  Object.defineProperty(global, 'navigator', { value: { language: navLang }, configurable: true });
  global.localStorage = { getItem: () => stored, setItem() {} };
  delete require.cache[require.resolve(MODULE)];
  try {
    require(MODULE);
  } finally {
    delete require.cache[require.resolve(MODULE)];
    global.window = saved.window;
    global.document = saved.document;
    if (saved.navigator) Object.defineProperty(global, 'navigator', saved.navigator);
    global.localStorage = saved.localStorage;
  }
  // init() re-resolves the language, so the stub environment has to be live
  // while DOMContentLoaded fires — otherwise this harness, not the module,
  // would be what changed the answer.
  const fireDomReady = () => {
    const outer = {
      window: global.window, document: global.document,
      navigator: Object.getOwnPropertyDescriptor(global, 'navigator'),
      localStorage: global.localStorage,
    };
    global.window = win;
    global.document = doc;
    Object.defineProperty(global, 'navigator', { value: { language: navLang }, configurable: true });
    global.localStorage = { getItem: () => stored, setItem() {} };
    try {
      (listeners.DOMContentLoaded || []).forEach((fn) => fn());
    } finally {
      global.window = outer.window;
      global.document = outer.document;
      global.localStorage = outer.localStorage;
      if (outer.navigator) Object.defineProperty(global, 'navigator', outer.navigator);
    }
  };

  return { api: win.RCI18N, htmlAttrs, fireDomReady };
}

test('the language is known while the document is still parsing', () => {
  const { api } = bootInDom({ stored: 'ja', navLang: 'en-US' });
  assert.ok(api, 'the module published RCI18N');
  // No DOMContentLoaded has fired. A script rendering right now — which is what
  // dashboard.js does — must already see the reader's language.
  assert.equal(api.getLang(), 'ja',
    'a synchronous page script would render English: this is the bug');
});

test('a string translates correctly at that moment, not just later', () => {
  const { api } = bootInDom({ stored: 'ja', navLang: 'en-US' });
  assert.equal(api.translate('aria.jump_radar', api.getLang()), 'レーダーへ移動');
});

test('<html lang/dir> is set at parse time, so RTL never flashes LTR', () => {
  const { htmlAttrs } = bootInDom({ stored: 'ar', navLang: 'en-US' });
  assert.equal(htmlAttrs.lang, 'ar');
  assert.equal(htmlAttrs.dir, 'rtl',
    'the direction was applied only after DOMContentLoaded — an Arabic reader '
      + 'gets a left-to-right flash before it corrects');
});

test('an LTR language is marked ltr, not left unset', () => {
  const { htmlAttrs } = bootInDom({ stored: 'ja', navLang: 'en-US' });
  assert.equal(htmlAttrs.dir, 'ltr');
});

test('the browser language is still the fallback when nothing is stored', () => {
  const { api } = bootInDom({ stored: null, navLang: 'tr-TR' });
  assert.equal(api.getLang(), 'tr');
});

test('an unsupported language still lands on English', () => {
  const { api } = bootInDom({ stored: 'xx', navLang: 'zz-ZZ' });
  assert.equal(api.getLang(), 'en');
});

test('the deferred DOM pass still runs, and runs without throwing', () => {
  // Parse-time resolution must not have replaced the DOMContentLoaded work —
  // apply() and the switcher still need a document that exists.
  const { api, fireDomReady } = bootInDom({ stored: 'ja', navLang: 'en-US' });
  fireDomReady();
  assert.equal(api.getLang(), 'ja', 'the deferred pass must not reset the language');
});

test('a hostile localStorage cannot stop the page booting', () => {
  // Private-mode browsers throw on localStorage access. That must degrade to
  // the browser language, not take the whole page down at parse time.
  const saved = { window: global.window, document: global.document,
    navigator: Object.getOwnPropertyDescriptor(global, 'navigator'),
    localStorage: global.localStorage };
  global.window = {};
  global.document = {
    readyState: 'loading', documentElement: { setAttribute() {} },
    addEventListener() {}, getElementById: () => null,
    querySelector: () => null, querySelectorAll: () => [],
  };
  Object.defineProperty(global, 'navigator', { value: { language: 'de-DE' }, configurable: true });
  global.localStorage = { getItem() { throw new Error('denied'); } };
  delete require.cache[require.resolve(MODULE)];
  try {
    assert.doesNotThrow(() => require(MODULE));
    assert.equal(global.window.RCI18N.getLang(), 'de');
  } finally {
    delete require.cache[require.resolve(MODULE)];
    global.window = saved.window;
    global.document = saved.document;
    global.localStorage = saved.localStorage;
    if (saved.navigator) Object.defineProperty(global, 'navigator', saved.navigator);
  }
});
