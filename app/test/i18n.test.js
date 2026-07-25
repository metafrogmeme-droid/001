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

test('every dashboard view has a nav key — a new view cannot ship untranslated', () => {
  const fs = require('node:fs');
  const path = require('node:path');
  const dash = fs.readFileSync(path.join(__dirname, '..', 'public', 'js', 'dashboard.js'), 'utf8');
  // The rail emits data-i18n="nav.<view id>" for EVERY entry, so a view whose
  // id has no dictionary key renders English in all languages — silently, on
  // the most-used surface in the product. Seven did.
  assert.match(dash, /data-i18n="nav\.\$\{v\.id\}"/, 'the rail must key nav items by view id');
  const start = dash.indexOf('const VIEWS = [');
  assert.ok(start > 0, 'VIEWS array not found');
  const block = dash.slice(start, dash.indexOf('];', start));
  const ids = [...block.matchAll(/id: '(\w+)'/g)].map((m) => m[1]);
  assert.ok(ids.length >= 23, `expected the full rail, found ${ids.length}`);
  const codes = i18n.LANGS.map((l) => l.code);
  for (const id of ids) {
    const e = i18n.STRINGS['nav.' + id];
    assert.ok(e, `view "${id}" has no nav.${id} key — it will render English everywhere`);
    for (const c of codes) assert.ok(e[c], `nav.${id} is missing ${c}`);
  }
});

test('no key is defined twice — a duplicate silently discards a translation', () => {
  const fs = require('node:fs');
  const path = require('node:path');
  const src = fs.readFileSync(path.join(__dirname, '..', 'public', 'js', 'i18n.js'), 'utf8');
  // In a JS object literal the LATER definition wins, so a duplicated key
  // means one full row of translations is dead code that no edit can reach.
  // nav.guardian and nav.leaderboard were each defined twice.
  const defs = [...src.matchAll(/^\s+'([\w.]+)': \{/gm)].map((m) => m[1]);
  const seen = new Map();
  for (const k of defs) seen.set(k, (seen.get(k) || 0) + 1);
  const dup = [...seen].filter(([, n]) => n > 1).map(([k]) => k);
  assert.deepEqual(dup, [], `duplicated key(s): ${dup.join(', ')}`);
  assert.equal(defs.length, Object.keys(i18n.STRINGS).length, 'parsed defs should match the object');
});

test('every data-i18n reference in the shipped pages resolves to a real key', () => {
  const fs = require('node:fs');
  const path = require('node:path');
  const pub = path.join(__dirname, '..', 'public');
  // A key referenced in markup but absent from the dictionary is invisible:
  // the inline English simply never gets replaced, in every language at once.
  // nav.menu on the landing hamburger was exactly that.
  const dangling = [];
  const walk = (d) => {
    for (const f of fs.readdirSync(d)) {
      const p = path.join(d, f);
      if (fs.statSync(p).isDirectory()) { if (f !== 'node_modules') walk(p); continue; }
      if (!/\.html$/.test(f)) continue;
      const src = fs.readFileSync(p, 'utf8');
      for (const m of src.matchAll(/data-i18n(?:-html)?="([\w.]+)"/g)) {
        if (!i18n.STRINGS[m[1]]) dangling.push(`${f}:${m[1]}`);
      }
    }
  };
  walk(pub);
  assert.deepEqual(dangling, [], `data-i18n keys with no dictionary entry: ${dangling.join(', ')}`);
});

test('Guardian is a product name, spelled the same way in every language', () => {
  // It appears untranslated in hero.explore_guardian and sec.guardian_cta in
  // all eleven languages; the nav link used to translate it in five of them,
  // so the same product had two names on one page.
  for (const c of i18n.LANGS.map((l) => l.code)) {
    assert.equal(i18n.STRINGS['nav.guardian'][c], 'Guardian', `nav.guardian:${c} renames the product`);
    assert.match(i18n.STRINGS['hero.explore_guardian'][c], /Guardian/, `hero.explore_guardian:${c}`);
    assert.match(i18n.STRINGS['sec.guardian_cta'][c], /Guardian/, `sec.guardian_cta:${c}`);
  }
  // The landing "Marketplace" link and the dashboard "Agents" view are
  // different destinations and must not share a key again.
  assert.equal(i18n.STRINGS['nav.marketplace'].en, 'Marketplace');
  assert.equal(i18n.STRINGS['nav.agents'].en, 'Agents');
});

test('the Arena order ticket is translated — every field the user touches', () => {
  const fs = require('node:fs');
  const path = require('node:path');
  const arena = fs.readFileSync(path.join(__dirname, '..', 'public', 'arena.html'), 'utf8');
  const codes = i18n.LANGS.map((l) => l.code);
  // The page heading translated while the form under it did not, so a
  // non-English user read their own language right up to the fields where a
  // misunderstanding costs money. Every one of these must carry a key.
  const wired = ['arena.f_symbol', 'arena.f_direction', 'arena.b_long', 'arena.b_short',
    'arena.f_margin', 'arena.f_leverage', 'arena.f_tp', 'arena.f_sl', 'arena.f_optional',
    'arena.f_margin_sig', 'arena.th_side', 'arena.th_lev', 'arena.th_entry', 'arena.th_mark',
    'arena.th_pnl', 'arena.th_tpsl', 'arena.th_liq', 'arena.th_exit', 'arena.th_how',
    'arena.e_pos', 'arena.e_hist'];
  for (const k of wired) {
    assert.ok(i18n.STRINGS[k], `${k} missing from the dictionary`);
    for (const c of codes) assert.ok(i18n.STRINGS[k][c], `${k} is missing ${c}`);
    assert.ok(arena.includes(`data-i18n="${k}"`), `${k} is not wired into arena.html`);
  }
  // The two placeholders go through the attribute path, not textContent.
  assert.match(arena, /data-i18n-attr="placeholder:arena\.ph_tp"/);
  assert.match(arena, /data-i18n-attr="placeholder:arena\.ph_sl"/);
  for (const c of codes) {
    assert.ok(i18n.STRINGS['arena.ph_tp'][c], `arena.ph_tp is missing ${c}`);
    assert.ok(i18n.STRINGS['arena.ph_sl'][c], `arena.ph_sl is missing ${c}`);
  }
});

test('the follow button toggles through T() — a static attribute would be clobbered', () => {
  const fs = require('node:fs');
  const path = require('node:path');
  const arena = fs.readFileSync(path.join(__dirname, '..', 'public', 'arena.html'), 'utf8');
  // JS rewrites this label on every paint, so it cannot rely on data-i18n:
  // the attribute is applied once at load and then overwritten.
  assert.match(arena, /T\('arena\.b_unfollow', 'Stop following'\)/);
  assert.match(arena, /T\('arena\.b_follow', 'Start following'\)/);
  assert.ok(!/textContent = following \? 'Stop following'/.test(arena),
    'the toggle is still writing a bare English literal');
  for (const c of i18n.LANGS.map((l) => l.code)) {
    assert.ok(i18n.STRINGS['arena.b_unfollow'][c], `arena.b_unfollow is missing ${c}`);
  }
});
