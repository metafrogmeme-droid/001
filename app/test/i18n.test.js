'use strict';
/**
 * Web i18n engine (public/js/i18n.js) — pure-logic + dictionary-integrity tests.
 * Runs the module under Node (dual-mode export); the DOM apply/switcher paths
 * are browser-only and not exercised here.
 */
const test = require('node:test');
const assert = require('node:assert');
const i18n = require('../public/js/i18n');

test('normalize strips region subtags and lowercases', () => {
  assert.equal(i18n.normalize('pt-BR'), 'pt');
  assert.equal(i18n.normalize('ZH_TW'), 'zh');
  assert.equal(i18n.normalize('EN'), 'en');
  assert.equal(i18n.normalize(''), '');
  assert.equal(i18n.normalize(null), '');
});

test('resolveLang: saved choice > browser > English', () => {
  assert.equal(i18n.resolveLang('es', 'fr-FR'), 'es');        // saved wins
  assert.equal(i18n.resolveLang(null, 'fr-FR'), 'fr');        // browser fallback
  assert.equal(i18n.resolveLang('xx', 'yy'), 'en');           // unknown -> en
  assert.equal(i18n.resolveLang(null, 'pt-BR'), 'pt');        // region normalized
});

test('translate returns the language string, falls back to English, null on miss', () => {
  assert.equal(i18n.translate('nav.dashboard', 'es'), 'Panel');
  assert.equal(i18n.translate('nav.dashboard', 'zz'), 'Dashboard');   // fallback en
  assert.equal(i18n.translate('does.not.exist', 'es'), null);
});

test('every dictionary key defines all offered languages (no silent gaps)', () => {
  const codes = i18n.LANGS.map((l) => l.code);
  const missing = [];
  for (const [key, entry] of Object.entries(i18n.STRINGS)) {
    for (const c of codes) {
      if (typeof entry[c] !== 'string' || !entry[c].length) missing.push(`${key}:${c}`);
    }
  }
  assert.deepEqual(missing, [], 'missing translations: ' + missing.join(', '));
});

test('every offered language has a non-empty display name', () => {
  for (const l of i18n.LANGS) {
    assert.ok(l.code && l.name, `lang ${JSON.stringify(l)} needs code+name`);
  }
});

test('arena dynamic strings: dictionary-backed with intact {x} placeholders', () => {
  const fs = require('node:fs');
  const path = require('node:path');
  // Every weekly quest key has a dictionary entry, so the panel translates
  // by key with the server's English name as fallback. Two consecutive weeks
  // cover the full 6-quest rotation (3 per week, offset by the ISO week).
  const streaks = require('../lib/arena_streaks');
  const QUEST_POOL = [...new Set([
    ...streaks.weeklyQuests([], new Date('2026-07-20T00:00:00Z')).map((q) => q.key),
    ...streaks.weeklyQuests([], new Date('2026-07-27T00:00:00Z')).map((q) => q.key),
  ])];
  assert.equal(QUEST_POOL.length, 6, 'both rotation halves sampled');
  for (const k of QUEST_POOL) {
    assert.ok(i18n.STRINGS['arena.q_' + k], `arena.q_${k} missing from the dictionary`);
  }
  // Placeholder slots survive translation in every language (a dropped {n}
  // would render a literal hole in the UI).
  const codes = i18n.LANGS.map((l) => l.code);
  for (const [key, entry] of Object.entries(i18n.STRINGS)) {
    const slots = (entry.en.match(/\{\w+\}/g) || []).sort().join(',');
    if (!slots) continue;
    for (const c of codes) {
      assert.equal((entry[c].match(/\{\w+\}/g) || []).sort().join(','), slots,
        `${key}:${c} placeholder mismatch`);
    }
  }
  // The arena wires dynamics through the shared dictionary.
  const arena = fs.readFileSync(path.join(__dirname, '..', 'public', 'arena.html'), 'utf8');
  assert.match(arena, /function T\(key, en\)/);
  assert.match(arena, /function fill\(tpl, map\)/);
  assert.match(arena, /T\('arena\.d_tape_pulse'/);
  assert.match(arena, /T\('arena\.q_' \+ q\.key, q\.name\)/);
  assert.match(arena, /T\('arena\.d_chart_note'/);
  assert.match(arena, /reasonLabel\(t\.reason\)/);
});

test('dashboard panels: dictionary-backed titles, async panels re-apply on land', () => {
  const fs = require('node:fs');
  const path = require('node:path');
  const dash = fs.readFileSync(path.join(__dirname, '..', 'public', 'js', 'dashboard.js'), 'utf8');
  // Every dp.* key referenced in dashboard.js exists in the dictionary.
  const used = [...new Set([...dash.matchAll(/data-i18n="(dp\.[\w.]+)"/g)].map((m) => m[1]))];
  assert.ok(used.length >= 100, `expected the full sweep, found ${used.length} dp.* uses`);
  for (const k of used) assert.ok(i18n.STRINGS[k], `${k} missing from the dictionary`);
  // renderPanel translates async content the moment it lands (both states).
  const appjs = fs.readFileSync(path.join(__dirname, '..', 'public', 'js', 'app.js'), 'utf8');
  assert.equal((appjs.match(/RCI18N\.apply\(el\)/g) || []).length, 2, 'data AND empty states apply i18n');
});

test('markup never dies in translation: data-i18n vs data-i18n-html', () => {
  const fs = require('node:fs');
  const path = require('node:path');
  const pub = path.join(__dirname, '..', 'public');
  // 1) A data-i18n element sets textContent — so English markup inside one is
  // either dropped (translated) or printed as literal tags. Those must use
  // data-i18n-html instead.
  const offenders = [];
  for (const f of fs.readdirSync(pub).filter((x) => x.endsWith('.html'))) {
    const src = fs.readFileSync(path.join(pub, f), 'utf8');
    for (const m of src.matchAll(/<(\w+)[^>]*\sdata-i18n="([\w.]+)"[^>]*>/g)) {
      const end = src.indexOf(`</${m[1]}>`, m.index + m[0].length);
      if (end < 0) continue;
      const inner = src.slice(m.index + m[0].length, end);
      if (/<(b|i|em|strong|code|a|br)\b/.test(inner)) offenders.push(`${f}:${m[2]}`);
    }
  }
  assert.deepEqual(offenders, [], 'these need data-i18n-html: ' + offenders.join(', '));
  // 2) When a key IS html, every language must carry the same tags as English —
  // a translation that quietly drops the <b> loses the emphasis for everyone
  // reading in that language.
  const codes = i18n.LANGS.map((l) => l.code);
  const tagsOf = (s) => (s.match(/<\/?(b|i|em|strong|code|br)\b/g) || []).sort().join(',');
  const htmlKeys = new Set();
  for (const f of fs.readdirSync(pub).filter((x) => x.endsWith('.html'))) {
    const src = fs.readFileSync(path.join(pub, f), 'utf8');
    for (const m of src.matchAll(/data-i18n-html="([\w.]+)"/g)) htmlKeys.add(m[1]);
  }
  assert.ok(htmlKeys.size > 0, 'the html-aware path should actually be in use');
  for (const k of htmlKeys) {
    const e = i18n.STRINGS[k];
    if (!e) continue;   // markup-only key with no dictionary entry is fine
    for (const c of codes) {
      assert.equal(tagsOf(e[c]), tagsOf(e.en), `${k}:${c} lost/changed markup vs English`);
    }
  }
});

test('dashboard dynamic strings: dictionary-backed with English fallback', () => {
  const fs = require('node:fs');
  const path = require('node:path');
  const dash = fs.readFileSync(path.join(__dirname, '..', 'public', 'js', 'dashboard.js'), 'utf8');
  // The helper degrades to the inline English rather than to an empty panel.
  assert.match(dash, /const T = \(key, en\) =>/);
  assert.match(dash, /RCI18N\.translate\(key, RCI18N\.getLang\(\)\)\) \|\| en/);
  // Every dd.* key used in dashboard.js exists in the dictionary...
  const used = [...new Set([...dash.matchAll(/T\('(dd\.[\w.]+)'/g)].map((m) => m[1]))];
  assert.ok(used.length >= 16, `expected the dynamic sweep, found ${used.length}`);
  for (const k of used) assert.ok(i18n.STRINGS[k], `${k} missing from the dictionary`);
  // ...and no error/CTA/empty string is left as a bare literal on those paths.
  assert.ok(!/errorText: '/.test(dash), 'an errorText is still hardcoded');
  assert.ok(!/label: 'Link Telegram'/.test(dash), 'a CTA label is still hardcoded');
});
