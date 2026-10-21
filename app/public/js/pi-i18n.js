/*
 * The Pi Browser app's words, in the reader's language.
 *
 * NOT THE SITE'S DICTIONARY. The site's i18n-core.js carries every string of
 * every page, the presale's among them, and nothing the Pi app loads may
 * (app/test/the_pi_app_is_the_arena_and_nothing_else.test.js). This is a
 * small table of the Pi app's own sentences, one chunk per language
 * (public/js/pi-i18n/<code>.js, built from app/i18n/pi/<code>.pairs.json by
 * app/scripts/build_pi_i18n.js).
 *
 * ENGLISH IS THE KEY. A sentence is written once, in English, where it is
 * used: `T('Position opened.')`, `T('{n} of {total} called', { n, total })`.
 * A language that has it shows its own; one that does not shows the English,
 * which is already on the screen's line in the code. A test reads every
 * T('…') in the Pi app's scripts and fails on a language that is missing one,
 * and on a table entry no script uses any more.
 *
 * THE LANGUAGE IS CHOSEN BY THE SERVER from the Pi Browser's own language
 * (routes/piapp.js, Accept-Language), which loads that chunk with the page;
 * About offers the others, loaded in place, because following a link would
 * end the in-memory session. Nothing is stored: a framed page keeps no
 * storage (arena-session.js).
 *
 * Shared modules (the session, the views, the Duel's renderer) call T only
 * when this module is on the page; the Farcaster Mini App, the DogeOS app and
 * the website load none of it and read the English in the code.
 */
(function (root, factory) {
  if (typeof module === 'object' && module.exports) module.exports = factory();
  else root.RCPiI18n = factory();
}(typeof self !== 'undefined' ? self : this, function () {
  'use strict';

  /** The site's fourteen, then the three Pi's largest communities read. */
  var LANGS = [
    { code: 'en', name: 'English' },
    { code: 'es', name: 'Español' },
    { code: 'zh', name: '繁體中文' },
    { code: 'pt', name: 'Português' },
    { code: 'fr', name: 'Français' },
    { code: 'de', name: 'Deutsch' },
    { code: 'nl', name: 'Nederlands' },
    { code: 'ja', name: '日本語' },
    { code: 'ko', name: '한국어' },
    { code: 'ru', name: 'Русский' },
    { code: 'tr', name: 'Türkçe' },
    { code: 'it', name: 'Italiano' },
    { code: 'hi', name: 'हिन्दी' },
    { code: 'ar', name: 'العربية', dir: 'rtl' },
    { code: 'vi', name: 'Tiếng Việt' },
    { code: 'id', name: 'Bahasa Indonesia' },
    { code: 'fil', name: 'Filipino' },
  ];
  var CODES = LANGS.map(function (l) { return l.code; });

  var tables = {};
  var current = 'en';
  var listeners = [];

  function known(code) { return CODES.indexOf(code) >= 0; }

  /**
   * The language a tag names, or null: 'pt-BR' is pt, 'zh-TW' is zh, 'in'
   * (Java's old tag for Indonesian) is id, 'tl' (Tagalog) is fil.
   */
  function normalize(tag) {
    var s = String(tag || '').trim().toLowerCase().replace('_', '-');
    if (!s) return null;
    var base = s.split('-')[0];
    if (base === 'in') base = 'id';
    if (base === 'tl') base = 'fil';
    return known(base) ? base : null;
  }

  /** The first language of an Accept-Language header this app speaks, or 'en'. */
  function fromAcceptLanguage(header) {
    var parts = String(header || '').split(',').map(function (p) {
      var bits = p.trim().split(';q=');
      var q = bits.length > 1 ? Number(bits[1]) : 1;
      return { tag: bits[0], q: isFinite(q) ? q : 0 };
    }).filter(function (p) { return p.tag; })
      .sort(function (a, b) { return b.q - a.q; });
    for (var i = 0; i < parts.length; i++) {
      var code = normalize(parts[i].tag);
      if (code) return code;
    }
    return 'en';
  }

  /** A chunk's pairs: [[english, translation], …]. */
  function add(code, pairs) {
    if (!known(code) || !Array.isArray(pairs)) return;
    var t = tables[code] = tables[code] || {};
    pairs.forEach(function (p) {
      if (Array.isArray(p) && typeof p[0] === 'string' && typeof p[1] === 'string' && p[1]) t[p[0]] = p[1];
    });
  }

  function fill(s, vars) {
    if (!vars) return s;
    return s.replace(/\{(\w+)\}/g, function (m, k) {
      return Object.prototype.hasOwnProperty.call(vars, k) && vars[k] != null ? String(vars[k]) : m;
    });
  }

  /** The sentence in the current language, its {placeholders} filled. */
  function t(en, vars) {
    var table = tables[current];
    var s = (table && Object.prototype.hasOwnProperty.call(table, en)) ? table[en] : en;
    return fill(s, vars);
  }

  function dirOf(code) {
    var l = LANGS.filter(function (x) { return x.code === code; })[0];
    return l && l.dir === 'rtl' ? 'rtl' : 'ltr';
  }

  /** Switch to a language whose chunk is loaded (English always is). */
  function use(code) {
    var next = known(code) && (code === 'en' || tables[code]) ? code : 'en';
    current = next;
    if (typeof document !== 'undefined' && document.documentElement) {
      document.documentElement.lang = next;
      document.documentElement.dir = dirOf(next);
    }
    listeners.forEach(function (fn) { try { fn(next); } catch (e) { /* a listener's own fault */ } });
    return next;
  }

  function onChange(fn) { if (typeof fn === 'function') listeners.push(fn); }

  return {
    LANGS: LANGS, CODES: CODES, add: add, t: t, use: use, onChange: onChange, fill: fill,
    normalize: normalize, fromAcceptLanguage: fromAcceptLanguage, dirOf: dirOf,
    lang: function () { return current; },
    has: function (code) { return code === 'en' || !!tables[code]; },
  };
}));
