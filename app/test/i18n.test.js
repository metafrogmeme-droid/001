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
  // All three async states re-apply i18n: data, empty, AND error. The error
  // state matters most — it is the one a user hits when something is already
  // going wrong, and reverting to English there is the worst moment for it.
  assert.equal((appjs.match(/RCI18N\.apply\(el\)/g) || []).length, 3,
    'data, empty AND error states apply i18n');
  // Slice fail()'s body exactly (to its own closing brace) so the assertions
  // can't be satisfied by the data/empty branches further down.
  const from = appjs.indexOf('function fail(');
  assert.ok(from > 0, 'renderPanel defines fail()');
  const failFn = appjs.slice(from, appjs.indexOf('\n    }', from) + 6);
  assert.match(failFn, /RCI18N\.apply\(el\)/, 'the error state re-translates');
  assert.match(failFn, /data-i18n="dd\.retry"/, 'Retry re-translates on language switch');
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


test('the two Arena paragraphs keep their <b> emphasis in every language', () => {
  const codes = i18n.LANGS.map((l) => l.code);
  const tagsOf = (s) => (s.match(/<\/?(b|i|em|strong|code|br)\b/g) || []).sort().join(',');
  // Both are wired through data-i18n-html, so a dropped tag ships as literal
  // markup or as a sentence that quietly loses its emphasis.
  for (const k of ['arena.follow_body', 'arena.disc']) {
    assert.ok(i18n.STRINGS[k], `${k} missing`);
    for (const c of codes) {
      assert.equal(tagsOf(i18n.STRINGS[k][c]), tagsOf(i18n.STRINGS[k].en), `${k}:${c} lost its markup`);
    }
  }
  const fs = require('node:fs');
  const path = require('node:path');
  const arena = fs.readFileSync(path.join(__dirname, '..', 'public', 'arena.html'), 'utf8');
  assert.match(arena, /data-i18n-html="arena\.follow_body"/);
  assert.match(arena, /data-i18n-html="arena\.disc"/);
});

test('the landing account funnel is translated — log-in, reset, and the account card', () => {
  const fs = require('node:fs');
  const path = require('node:path');
  const index = fs.readFileSync(path.join(__dirname, '..', 'public', 'index.html'), 'utf8');
  const codes = i18n.LANGS.map((l) => l.code);
  // Every user passes through this. It was asking a Turkish or Korean visitor
  // to understand "Two-factor code", "Current password" and "Confirm new
  // password" in English, on a page otherwise fully translated.
  const wired = ['auth.tfa_code', 'auth.tfa_hint', 'auth.forgot', 'auth.forgot_body',
    'auth.send_reset', 'auth.back_login', 'acc.title', 'acc.logout', 'acc.link_tg',
    'acc.link_tg_body', 'acc.tg_step2', 'acc.gen_token', 'acc.link_social',
    'acc.verify_text', 'acc.resend_verify', 'acc.wallet', 'acc.wallet_body',
    'acc.wallet_link', 'acc.wallet_unlink', 'acc.pw_title', 'acc.pw_current',
    'acc.pw_new', 'acc.pw_confirm', 'acc.pw_update', 'acc.tfa', 'acc.tfa_enable',
    'acc.tfa_step1', 'acc.tfa_step2', 'acc.tfa_confirm', 'acc.tfa_disable', 'acc.open_dash'];
  for (const k of wired) {
    assert.ok(i18n.STRINGS[k], `${k} missing from the dictionary`);
    for (const c of codes) assert.ok(i18n.STRINGS[k][c], `${k} is missing ${c}`);
    assert.ok(index.includes(`data-i18n="${k}"`), `${k} is not wired into index.html`);
  }
  // The log-in step reuses keys that already existed and were simply never
  // wired — the same oversight as the Arena top nav.
  for (const k of ['auth.email', 'auth.password', 'auth.tab_login']) {
    assert.ok(index.includes(`data-i18n="${k}"`), `${k} exists but is still unwired`);
  }
  // Markup-bearing entries keep their tags in every language.
  const tagsOf = (s) => (s.match(/<\/?(b|i|em|strong|code|br)\b/g) || []).sort().join(',');
  for (const k of ['acc.tfa_on', 'acc.tg_step3']) {
    for (const c of codes) assert.equal(tagsOf(i18n.STRINGS[k][c]), tagsOf(i18n.STRINGS[k].en), `${k}:${c}`);
  }
  // The bot link survives translation — a step that loses it is unfollowable.
  for (const c of codes) assert.match(i18n.STRINGS['acc.tg_step1'][c], /t\.me\/HTRUNECLAW_bot/, `acc.tg_step1:${c}`);
});

test('the skip link parks off the TOP, so RTL pages do not scroll sideways', () => {
  const fs = require('node:fs');
  const path = require('node:path');
  const css = fs.readFileSync(path.join(__dirname, '..', 'public', 'styles.css'), 'utf8');
  // `left: -9999px` hides it fine in LTR, but an RTL document counts that
  // overflow: the Arabic landing page measured 10411px wide on a 412px phone
  // — the same failure mode that once dragged the fixed CTA off-screen.
  const rule = css.slice(css.indexOf('.skip-link {'), css.indexOf('}', css.indexOf('.skip-link {')));
  assert.ok(!/left:\s*-\d+px/.test(rule), 'the skip link is parked off the left again');
  assert.match(rule, /transform:\s*translateY\(-\d+%\)/, 'it should be parked off the top');
  assert.match(rule, /inset-inline-start/, 'it should use a direction-aware inset');
  assert.match(css, /\.skip-link:focus \{ transform: none; \}/, 'focus must still reveal it');
});

test('the landing footer translates — and half of it needed no new strings', () => {
  const fs = require('node:fs');
  const path = require('node:path');
  const index = fs.readFileSync(path.join(__dirname, '..', 'public', 'index.html'), 'utf8');
  const codes = i18n.LANGS.map((l) => l.code);
  // Ten of the twenty footer links already had nav.* keys from the landing and
  // dashboard navs and had simply never been wired here — the same oversight
  // as the Arena top nav and the log-in step. Reuse, don't re-author.
  const reused = ['nav.dashboard', 'nav.marketplace', 'nav.strengthmap', 'nav.track', 'nav.proof',
    'nav.guardian', 'nav.flight', 'nav.stress', 'nav.leaderboard', 'nav.letter', 'nav.docs'];
  const added = ['nav.roots', 'nav.sentinel', 'nav.firewall', 'nav.escape', 'nav.developers',
    'nav.status', 'nav.tg_bot', 'nav.community'];
  for (const k of reused.concat(added)) {
    assert.ok(i18n.STRINGS[k], `${k} missing from the dictionary`);
    for (const c of codes) assert.ok(i18n.STRINGS[k][c], `${k} is missing ${c}`);
    assert.ok(index.includes(`data-i18n="${k}"`), `${k} is not wired into index.html`);
  }
  // The public chat drawer is where anonymous visitors meet the assistant.
  assert.match(index, /data-i18n="chat\.label"/);
  assert.match(index, /data-i18n-attr="placeholder:chat\.ph"/);
  for (const c of codes) {
    assert.ok(i18n.STRINGS['chat.label'][c], `chat.label is missing ${c}`);
    assert.ok(i18n.STRINGS['chat.ph'][c], `chat.ph is missing ${c}`);
  }
  // RUNECLAW is a product name and stays itself wherever a language embeds it.
  for (const c of codes) assert.match(i18n.STRINGS['chat.label'][c], /RUNECLAW/, `chat.label:${c}`);
});


test('landing claims keep the links that back them up, in every language', () => {
  const codes = i18n.LANGS.map((l) => l.code);
  // "Every fill verifiable" and "the public track record" are claims whose
  // whole weight is the link. A translation that drops it leaves an unbacked
  // assertion — worse than the English sentence it replaced.
  for (const c of codes) {
    assert.match(i18n.STRINGS['lp.tb_fill'][c], /href="\/proof"/, `lp.tb_fill:${c} lost its evidence link`);
    assert.match(i18n.STRINGS['lp.replay_note'][c], /href="\/track"/, `lp.replay_note:${c} lost its evidence link`);
  }
  // §4 on the public landing surface: the marketing copy talks in percent and
  // ratio, and must not introduce a dollar figure in any language.
  for (const k of ['lp.why4_p', 'lp.board_p', 'lp.replay_p', 'lp.replay_return']) {
    for (const c of codes) {
      assert.ok(!/\$\s?\d|USD\s?\d/.test(i18n.STRINGS[k][c]), `${k}:${c} introduces a dollar figure`);
    }
  }
});

// One table-driven guard for every page that has been swept. Adding a page
// here is the whole ceremony — and a page that regrows English fails with the
// line number.
//
// The scan runs over the WHOLE document, not line by line. A line-based pass
// silently skips any text node that wraps across a newline, which is exactly
// how six multi-line <p> paragraphs on guardian.html stayed invisible to the
// first version of this test.
// Match against BOTH the original string and a diacritic-stripped copy.
//
// Stripping alone is wrong: NFD decomposes Japanese ブ into フ + combining
// dakuten and Hangul 익 into jamo, and \p{Diacritic} then deletes the dakuten
// outright — NFC cannot restore a mark that has been removed. So kana and
// Hangul must be matched in their ORIGINAL form, while Latin accents (Spanish
// "anónimos", Portuguese "à mão") need the stripped one. Searching the
// concatenation of both gets each script what it needs.
function searchable(s) {
  return s + ' \u0000 ' + s.normalize('NFD').replace(/\p{Diacritic}/gu, '').normalize('NFC');
}

const SWEPT_PAGES = {
  // Only proper nouns may go in an allowlist. Anything else belongs in the
  // dictionary — "we'd rather not translate it" is not a category.
  'arena.html': ['← RUNECLAW'],
  'index.html': ['RUNECLAW', 'RUNECLAW Guardian', 'GitHub', 'Sharpe'],
  'guardian.html': ['← RUNECLAW', 'RUNECLAW Guardian'],
  'developers.html': ['← RUNECLAW'],
  'stress.html': ['← RUNECLAW'],
  'provable.html': ['← RUNECLAW'],
  'escape.html': ['← RUNECLAW'],
  'leaderboard.html': ['← RUNECLAW', 'Sharpe'],
  // 'Bitget USDT-M perpetuals' is the API's own venue string, written over
  // this element on load — a data-i18n here would be clobbered every time.
  'track.html': ['RUNECLAW', 'Bitget USDT-M perpetuals'],
  'firewall.html': ['← RUNECLAW'],
  'intent.html': ['← RUNECLAW'],
  'flight.html': [],
  'sentinel.html': [],
  'roots.html': [],
  // 'AI Viking' is the agent's name; RUNECLAW its brand.
  'agent.html': ['RUNECLAW', 'AI Viking'],
  'agents.html': [],
  'letter.html': [],
  'proof.html': ['RUNECLAW'],
  'call.html': [],
};

function untranslatedInSource(src, allowList) {
  // An element translated as HTML owns its subtree: the inner <span>/<a>/<i>
  // is replaced wholesale by the parent's entry and needs no key of its own.
  src = src.replace(/<(\w+)([^>]*data-i18n-html=[^>]*)>[\s\S]*?<\/\1>/g, (m, t, a) => `<${t}${a}></${t}>`);
  // Scripts build their strings through T(); comments ship to nobody.
  src = src.replace(/<(script|style)\b[^>]*>[\s\S]*?<\/\1>/g, (m) => m.replace(/[^\n]/g, ' '));
  src = src.replace(/<!--[\s\S]*?-->/g, (m) => m.replace(/[^\n]/g, ' '));
  // Entities are glyphs and escapes, not prose: &times; is a × on a close
  // button and must not read as the word "times".
  src = src.replace(/&[a-zA-Z]+;|&#\d+;/g, ' ');
  const allow = new Set(allowList || []);
  const re = /<(h1|h2|h3|h4|span|p|button|label|option|summary|th|legend|a|li|td)\b([^>]*)>([^<>{}`]*?[A-Za-z]{3,}[^<>{}`]*?)</g;
  const found = [];
  for (const m of src.matchAll(re)) {
    if (/data-i18n/.test(m[2])) continue;
    const text = m[3].replace(/\s+/g, ' ').trim();
    if (!text || allow.has(text)) continue;
    found.push(`${src.slice(0, m.index).split('\n').length}: <${m[1]}> ${text}`);
  }
  return found;
}

function untranslatedIn(file) {
  const fs = require('node:fs');
  const path = require('node:path');
  return untranslatedInSource(
    fs.readFileSync(path.join(__dirname, '..', 'public', file), 'utf8'), SWEPT_PAGES[file]);
}

for (const page of Object.keys(SWEPT_PAGES)) {
  test(`${page} has no untranslated static English left`, () => {
    const found = untranslatedIn(page);
    assert.deepEqual(found, [], `untranslated static text in ${page}:\n  ${found.join('\n  ')}`);
  });
}

test('the sweep guard actually catches multi-line copy', () => {
  // Self-check on a synthetic fixture rather than on a real page: pointing it
  // at "some page we haven't done yet" goes stale the moment that page is
  // swept, and a self-check that quietly stops checking is worse than none.
  //
  // The first version of this guard was line-based and would have passed the
  // wrapped paragraph below — that is exactly how six multi-line <p> blocks on
  // guardian.html stayed invisible.
  const fixture = [
    '<div>',
    '  <p data-i18n="ok.keyed">Keyed copy is invisible to the scan.</p>',
    '  <p>A wrapped sentence that keeps going',
    '  onto a second line before it closes.</p>',
    '  <button>&times;</button>',
    '  <span>BRANDNAME</span>',
    '</div>',
  ].join('\n');
  const found = untranslatedInSource(fixture, ['BRANDNAME']);
  assert.equal(found.length, 1, `expected exactly the wrapped paragraph, got ${JSON.stringify(found)}`);
  assert.match(found[0], /A wrapped sentence that keeps going onto a second line/,
    'the scanner must join wrapped text, not truncate at the newline');
  // …and the three exemptions really are exempt: keyed copy, an entity-only
  // glyph, and an allowlisted brand.
});

test('every swept page actually loads the localizer', () => {
  const fs = require('node:fs');
  const path = require('node:path');
  // guardian.html was fully marked up with data-i18n and still rendered
  // English in all twelve languages, because it never loaded i18n.js. Markup
  // without the script is markup that does nothing — and the scan guard above
  // cannot see the difference, so this is asserted separately.
  for (const page of Object.keys(SWEPT_PAGES)) {
    const src = fs.readFileSync(path.join(__dirname, '..', 'public', page), 'utf8');
    assert.match(src, /<script src="\/js\/i18n\.js\?v=\d+"><\/script>/, `${page} does not load i18n.js`);
  }
});

test('the developer page never translates an identifier', () => {
  const codes = i18n.LANGS.map((l) => l.code);
  // Endpoint paths, HTTP verbs, hash names and ERC numbers are identifiers.
  // A "translated" endpoint is a broken integration doc, so every locale must
  // carry them byte-identically.
  const literals = {
    'dv.mcp_p': ['<code>POST /mcp</code>'],
    'dv.mcp_note': ['JSON-RPC 2.0', '2025-03-26', '<code>readOnlyHint</code>'],
    'dv.tools_sub': ['<code>tools/list</code>'],
    'dv.ethos_p': ['<code>publish_hash = SHA-256(canonical)</code>', 'UTF-8'],
    'dv.reg_p': ['ERC-8257', 'ERC-8004', 'Base'],
  };
  for (const [k, needles] of Object.entries(literals)) {
    for (const c of codes) {
      const v = i18n.STRINGS[k][c];
      assert.ok(v, `${k} is missing ${c}`);
      for (const n of needles) assert.ok(v.includes(n), `${k}:${c} lost the identifier "${n}"`);
    }
  }
  // The two docs links must survive translation or the go-live notes are lost.
  for (const c of codes) {
    assert.match(i18n.STRINGS['dv.footer_p'][c], /docs\/ONCHAIN_GOLIVE\.md<\/a>/, `dv.footer_p:${c}`);
    assert.match(i18n.STRINGS['dv.footer_p'][c], /docs\/INTEROP\.md<\/a>/, `dv.footer_p:${c}`);
  }
  // §4: these two endpoint descriptions ARE the honesty guarantee of the API.
  // A translation that drops the clause misdescribes what the endpoint returns.
  for (const c of codes) {
    assert.ok(/dollar|dólar|dolar|金額|금액|долл|Dollar|مبالغ/i.test(i18n.STRINGS['dv.r2'][c]),
      `dv.r2:${c} dropped the "never dollar amounts" guarantee`);
    assert.ok(/percent|porcent|百分比|pourcent|Prozent|procent|パーセント|퍼센트|процент|yüzde|النسب/i.test(i18n.STRINGS['dv.r4'][c]),
      `dv.r4:${c} dropped the "percent-only" guarantee`);
  }
});

test('the Stress Lab keeps its honesty clauses in every language', () => {
  const codes = i18n.LANGS.map((l) => l.code);
  // This page simulates catastrophe on a made-up book. Its two paragraphs are
  // the only thing stopping a reader treating the output as a forecast of
  // their own account — so a softer translation would overstate the product.
  for (const c of codes) {
    const lede = i18n.STRINGS['sx.lede'][c];
    const disc = i18n.STRINGS['sx.disc'][c];
    assert.ok(lede && disc, `sx.lede/sx.disc missing ${c}`);
    // "never your real balance" / "not your real account"
    assert.ok(/real|reale|real|réel|echte|werkelijk|実|실제|реальн|gerçek|حقيقي|真實|真实/i.test(lede),
      `sx.lede:${c} lost the "never your real balance" clause`);
    // "not investment advice" survives in both paragraphs
    for (const [k, v] of [['sx.lede', lede], ['sx.disc', disc]]) {
      assert.ok(/advice|asesoram|conselho|aconselh|conseil|Anlageberatung|beleggingsadvies|投資建議|投資助言|투자 조언|инвестиционн|yatırım tavsiyesi|نصيحة/i.test(v),
        `${k}:${c} dropped "not investment advice"`);
    }
    // The <b>not</b> in sx.disc is the load-bearing word of the whole page.
    assert.ok(/<b>/.test(disc) && (disc.match(/<b>/g) || []).length >= 2,
      `sx.disc:${c} lost an emphasis the English relies on`);
    // The escape hatch to build a REAL envelope must stay reachable.
    assert.match(disc, /href="\/dashboard#agents"/, `sx.disc:${c} lost the app link`);
  }
});

test('the verification contract survives translation intact', () => {
  const codes = i18n.LANGS.map((l) => l.code);
  // Highest-stakes page on the site: a reader follows these words to reproduce
  // a hash. Blur "hash it verbatim, never re-serialize", drop "deduplicated,
  // sorted ascending", or loosen "promoted unchanged", and the reader gets a
  // mismatch and concludes OUR receipts are broken — when the instructions
  // were. Every code literal must therefore be byte-identical per locale.
  const literals = {
    'pv.c_v1_p': ['<code>seal_payload</code>'],
    'pv.s2_l1': ['<code>GET /api/call/&lt;key&gt;</code>', '<code>sig:…</code>', '<code>arena:…</code>'],
    'pv.s2_l2': ['<code>sha256(seal_payload)</code>', 'UTF-8'],
    'pv.s2_l3': ['<code>seal</code>', '<code>current</code>'],
    'pv.s3_l1': ['64'],
    'pv.s3_l2': ['<code>sha256(utf8(leftHex + rightHex))</code>'],
    'pv.s3_proof': ['<code>anchor.proof</code>', '<code>{pos, hash}</code>'],
    'pv.s3_p': ['href="/api/roots"', 'href="/roots"', 'Merkle'],
  };
  for (const [k, needles] of Object.entries(literals)) {
    for (const c of codes) {
      const v = i18n.STRINGS[k][c];
      assert.ok(v, `${k} is missing ${c}`);
      for (const n of needles) assert.ok(v.includes(n), `${k}:${c} lost the literal "${n}"`);
    }
  }
  // §4: the arena payload clause is a guarantee about what is published.
  for (const c of codes) {
    assert.ok(/virtu|仮想|가상|виртуальн|sanal|افتراض|虛擬/i.test(i18n.STRINGS['pv.c_v2_p'][c]),
      `pv.c_v2_p:${c} dropped the "virtual balances never appear" guarantee`);
  }
  // The honest-scope note bounds what a seal actually proves. It must keep its
  // <b> lead-in and its refusal to claim future performance.
  for (const c of codes) {
    const scope = i18n.STRINGS['pv.scope'][c];
    assert.match(scope, /<b>/, `pv.scope:${c} lost its emphasis`);
    assert.ok(/advice|asesoram|conselho|aconselh|conseil|Anlageberatung|beleggingsadvies|投資建議|投資助言|투자 조언|инвестиционн|yatırım tavsiyesi|نصيحة/i.test(scope),
      `pv.scope:${c} dropped "not investment advice"`);
  }
});

test('the Escape Agent never claims to act, in any language', () => {
  const codes = i18n.LANGS.map((l) => l.code);
  // This planner sequences an unwind of real money. Its two paragraphs are the
  // only thing telling a reader it does NOT act: "Planning only — it never
  // executes anything" and "it does not move funds, place orders or sign
  // anything — you execute each step yourself". A translation that softens
  // either one describes a different, far more dangerous product.
  //
  // escape.test.js asserts those clauses against the English markup; that keeps
  // working because data-i18n leaves the inline English in place as the
  // fallback. This covers the other eleven, which that test cannot see.
  for (const c of codes) {
    const lede = i18n.STRINGS['es.lede'][c];
    const disc = i18n.STRINGS['es.disc'][c];
    assert.ok(lede && disc, `es.lede/es.disc missing ${c}`);
    // "never executes" / "does not execute"
    assert.ok(/never|nunca|絕不|jamais|niemals|nooit|一切行いません|않습니다|никогда|asla|إطلاقًا|أبدًا/i.test(lede),
      `es.lede:${c} lost the "never executes anything" clause`);
    // the planner does not move funds / place orders / sign
    assert.ok(/sign|firma|assina|signe|signiert|onderteken|署名|서명|подписывает|imzalamaz|يوقّع|簽署/i.test(disc),
      `es.disc:${c} lost the "signs nothing" clause`);
    assert.ok(/advice|asesoram|conselho|aconselh|conseil|Anlageberatung|beleggingsadvies|投資建議|投資助言|투자 조언|инвестиционн|yatırım tavsiyesi|نصيحة/i.test(disc),
      `es.disc:${c} dropped "not investment advice"`);
    // the risks it explicitly does NOT model must stay listed
    assert.ok(/slippage|kayma|انزلاق|滑價|スリッページ|슬리피지|проскальзыван/i.test(disc),
      `es.disc:${c} dropped the un-modelled-risk list`);
    // both paragraphs keep the emphasis the English leans on
    assert.ok((lede.match(/<b>/g) || []).length >= 3, `es.lede:${c} lost emphasis`);
    assert.match(disc, /<b>/, `es.disc:${c} lost emphasis`);
  }
});

test('the public board never promises a dollar figure, in any language', () => {
  const codes = i18n.LANGS.map((l) => l.code);
  // §4 lives on this surface more directly than anywhere else: the board is
  // public, and lb.lede is what tells a reader what it will and will not show.
  for (const c of codes) {
    const lede = i18n.STRINGS['lb.lede'][c];
    assert.ok(lede, `lb.lede missing ${c}`);
    // EVERY text check goes through searchable(), including this one: Spanish
    // writes "dólares", and a raw-string match only passed while the accented
    // spelling happened to be listed by hand.
    const plain = searchable(lede);
    // "never a dollar figure" — the promise itself
    assert.ok(/dollar|dolar|金額|금액|долл|بالدولار/i.test(plain),
      `lb.lede:${c} dropped the "never a dollar figure" promise`);
    // anonymous handles, and the publish_hash that makes a row checkable
    assert.ok(/anon|匿名|익명|анонимн|مستعار/i.test(plain), `lb.lede:${c} lost "anonymous handles"`);
    assert.ok(lede.includes('<code>publish_hash</code>'), `lb.lede:${c} lost the publish_hash literal`);
    assert.match(lede, /href="\/proof"/, `lb.lede:${c} lost the verify-the-fills link`);
    assert.ok((lede.match(/<b>/g) || []).length >= 3, `lb.lede:${c} lost emphasis`);
  }
  // Opt-in and revocable is a consent statement, not decoration.
  for (const c of codes) {
    const note = searchable(i18n.STRINGS['lb.note'][c]);
    assert.ok(/opt|volunt|volont|任意|自願|자발|доброволь|gonull|vrijwillig|freiwillig|opcional|اختياري/i.test(note),
      `lb.note:${c} dropped the opt-in/revocable statement`);
  }
  // And the rendered page must actually carry no dollar sign in its markup.
  const fs = require('node:fs');
  const path = require('node:path');
  const html = fs.readFileSync(path.join(__dirname, '..', 'public', 'leaderboard.html'), 'utf8');
  const body = html.slice(0, html.indexOf('<script'));
  assert.ok(!/\$\s?\d/.test(body), 'a dollar figure appeared in the board markup');
});

test('the track record keeps its "nothing is hand-entered" claim everywhere', () => {
  const codes = i18n.LANGS.map((l) => l.code);
  // This page publishes the engine's own record. The only reason to believe
  // any figure on it is the claim that every number is aggregated from synced
  // closed trades and equity snapshots, with nothing typed in by a human.
  // A translation that loses that clause leaves the numbers unsupported.
  for (const c of codes) {
    const lede = searchable(i18n.STRINGS['tk.lede'][c]);
    assert.ok(lede, `tk.lede missing ${c}`);
    assert.ok(/hand|mano|mao|main|手|손|вручную|elle|يدوي/i.test(lede),
      `tk.lede:${c} dropped the "nothing is hand-entered" claim`);
    assert.ok(/automat|otomat|自動|자동|автомат|تلقائ/i.test(lede),
      `tk.lede:${c} dropped "aggregated automatically"`);
  }
});

test('the firewall never oversells itself, in any language', () => {
  const codes = i18n.LANGS.map((l) => l.code);
  const plain = searchable;
  // Someone pastes a signing request into this page. Two clauses carry the
  // whole product:
  //   fw.lede — the scan runs ENTIRELY in the browser and nothing leaves the
  //     page. Weaken that and a reader in that language reasonably believes
  //     they just uploaded a transaction they were about to sign.
  //   fw.disc — a clean result is NOT a guarantee and a flag is not a verdict.
  //     A heuristic scanner that reads as authoritative is worse than none.
  for (const c of codes) {
    const lede = plain(i18n.STRINGS['fw.lede'][c]);
    const disc = plain(i18n.STRINGS['fw.disc'][c]);
    assert.ok(lede && disc, `fw.lede/fw.disc missing ${c}`);
    assert.ok(/brows|navegador|navigateur|ブラウザ|브라우저|браузер|taray|متصفح|瀏覽器/i.test(lede),
      `fw.lede:${c} lost the "runs in your browser" promise`);
    assert.ok(/loca|loka|ローカル|로컬|локальн|yerel|محلي|本機/i.test(disc),
      `fw.disc:${c} lost "the scan is local"`);
    assert.ok(/heuristi|heuristisch|ヒューリスティック|휴리스틱|эвристи|sezgisel|استدلال|啟發式/i.test(disc),
      `fw.disc:${c} dropped the word "heuristic"`);
    assert.ok(/guarant|garant|保証|보장|гаранти|garanti|ضمان|保證/i.test(disc),
      `fw.disc:${c} dropped "a clean result is not a guarantee"`);
    // Both paragraphs keep the emphasis the English leans on.
    assert.ok((i18n.STRINGS['fw.lede'][c].match(/<b>/g) || []).length >= 3, `fw.lede:${c} lost emphasis`);
    assert.ok((i18n.STRINGS['fw.disc'][c].match(/<b>/g) || []).length >= 2, `fw.disc:${c} lost emphasis`);
  }
});

test('the Intent Compiler states all five of its limits in every language', () => {
  const codes = i18n.LANGS.map((l) => l.code);
  // in.disc is the densest safety paragraph in the product: five separate
  // limits, each of which a reader needs in order to understand what binding
  // an envelope would and would not do to their account.
  for (const c of codes) {
    const disc = searchable(i18n.STRINGS['in.disc'][c]);
    assert.ok(disc, `in.disc missing ${c}`);
    // 1. binds nothing / signs nothing / moves no funds
    assert.ok(/sign|firma|assina|signe|signiert|onderteken|署名|서명|подпис|imzala|وقع|簽署/i.test(disc),
      `in.disc:${c} lost "signs nothing"`);
    // 2. tighten-only — it can only NARROW the engine's caps
    assert.ok(/tighten|restri|estrech|收緊|狭め|締める|조이|ужесточ|daralt|مضي|verscharf|aanscherp|resserr/i.test(disc),
      `in.disc:${c} lost the tighten-only limit`);
    // 3. revocable at any time
    assert.ok(/revoc|revog|widerruf|intrekbaar|取り消|취소|отзыв|geri al|إلغاء|撤銷/i.test(disc),
      `in.disc:${c} lost "revocable at any time"`);
    // 4. §4 stated inside the product copy: a PUBLIC demo never shows a dollar
    //    figure; the real number is set privately in the app.
    assert.ok(/dollar|dolar|金額|금액|долл|بالدولار/i.test(disc),
      `in.disc:${c} lost the "never shows a dollar figure" clause`);
    // 5. not investment advice
    assert.ok(/advice|asesoram|conselho|aconselh|conseil|Anlageberatung|beleggingsadvies|投資建議|投資助言|투자 조언|инвестиционн|tavsiye|نصيحة/i.test(disc),
      `in.disc:${c} dropped "not investment advice"`);
    assert.ok((i18n.STRINGS['in.disc'][c].match(/<b>/g) || []).length >= 2, `in.disc:${c} lost emphasis`);
  }
  // The lede's whole argument is that a wish becomes a machine-checkable rule
  // with a named enforcer, so the four emphasised spans must all survive.
  for (const c of codes) {
    assert.ok((i18n.STRINGS['in.lede'][c].match(/<b>/g) || []).length >= 4, `in.lede:${c} lost emphasis`);
  }
});

test('a failed Arena load says UNKNOWN, never "you have none"', () => {
  const fs = require('node:fs');
  const path = require('node:path');
  const arena = fs.readFileSync(path.join(__dirname, '..', 'public', 'arena.html'), 'utf8');
  // Reproduced from an operator screenshot: when /api/arena/account 500s, the
  // positions and history panels stayed visible as bare column headers. A user
  // reads that as "my trades are gone". Filling them with the ordinary empty
  // state would be worse — it would ASSERT they have none, which we do not
  // know. The only honest word here is "unknown".
  assert.match(arena, /T\('arena\.d_unknown'/, 'the failure path must use the unknown copy');
  assert.match(arena, /\['posEmpty', 'histEmpty'\]\.forEach/, 'both tables must be told');
  for (const c of i18n.LANGS.map((l) => l.code)) {
    const v = i18n.STRINGS['arena.d_unknown'][c];
    assert.ok(v, `arena.d_unknown is missing ${c}`);
    // Assert POSITIVELY that it says "unknown". A leading-"no" heuristic is
    // wrong here: Spanish opens "No se pudo cargar" — a negated VERB ("could
    // not load"), not a claim of emptiness.
    assert.ok(/unknown|desconocid|desconhecid|inconnue|unbekannt|onbekend|不明|未知|알 수 없|неизвест|bilinmiyor|غير معروف/i.test(searchable(v)),
      `arena.d_unknown:${c} does not actually say "unknown"`);
    assert.notEqual(v, i18n.STRINGS['arena.e_pos'][c], `arena.d_unknown:${c} is just the empty state`);
  }
  // A retry that succeeds has to put the real copy back, or a working account
  // still reads "unknown" forever.
  assert.match(arena, /\$\('posEmpty'\)\.textContent = T\('arena\.e_pos'/);
  assert.match(arena, /\$\('histEmpty'\)\.textContent = T\('arena\.e_hist'/);
});

test('Arena route errors log a stack, not just a message', () => {
  const fs = require('node:fs');
  const path = require('node:path');
  const route = fs.readFileSync(path.join(__dirname, '..', 'routes', 'arena.js'), 'utf8');
  // The operator hit "Arena unavailable" in production. err.message alone
  // yields lines like "Cannot read properties of undefined" with no location,
  // so the log could not say which handler or which line failed.
  assert.ok(!/console\.error\('Arena [^']+', err\.message\)/.test(route),
    'an Arena handler still logs err.message without a stack');
  const withStack = (route.match(/console\.error\('Arena [^']+', err\.stack \|\| err\.message\)/g) || []).length;
  assert.ok(withStack >= 10, `expected every Arena handler to log a stack, found ${withStack}`);
});
