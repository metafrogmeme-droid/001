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

test('the Arena page has no untranslated static English left', () => {
  const fs = require('node:fs');
  const path = require('node:path');
  const src = fs.readFileSync(path.join(__dirname, '..', 'public', 'arena.html'), 'utf8');
  // Only the markup half — the script half builds strings through T().
  const cut = src.indexOf('<script>\n// Device-visible error trap');
  assert.ok(cut > 0, 'could not find the end of the markup');
  // Entities are glyphs, not prose — &times; must not read as the word "times".
  const markup = src.slice(0, cut).replace(/&[a-zA-Z]+;|&#\d+;/g, ' ');
  // A visible string in a text-bearing tag that carries no data-i18n renders
  // English in every language at once. The whole page heading translated while
  // the form under it did not, which is how this class of bug hides.
  const BRAND = ['← RUNECLAW'];                 // the product name is not translated
  const found = [];
  markup.split('\n').forEach((line, n) => {
    const re = /<(h1|h2|h3|span|p|button|label|option|summary|th|legend|a)\b([^>]*)>([^<>{}`]*[A-Za-z]{3,}[^<>{}`]*)</g;
    for (const m of line.matchAll(re)) {
      const attrs = m[2]; const text = m[3].trim();
      if (/data-i18n/.test(attrs) || !text) continue;
      if (BRAND.includes(text)) continue;
      found.push(`${n + 1}: <${m[1]}> ${text}`);
    }
  });
  assert.deepEqual(found, [], `untranslated static text in arena.html:\n  ${found.join('\n  ')}`);
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

test('the landing page has no untranslated static English left', () => {
  const fs = require('node:fs');
  const path = require('node:path');
  let src = fs.readFileSync(path.join(__dirname, '..', 'public', 'index.html'), 'utf8');
  // Elements translated as HTML own their subtree — the inner <span>/<a> there
  // is replaced wholesale by the parent's entry, so it needs no key of its own.
  src = src.replace(/<(\w+)([^>]*data-i18n-html=[^>]*)>[\s\S]*?<\/\1>/g, (m, t, a) => `<${t}${a}></${t}>`);
  // Scripts build their strings through T(); only markup is in scope here.
  src = src.replace(/<(script|style)\b[^>]*>[\s\S]*?<\/\1>/g, (m) => m.replace(/[^\n]/g, ' '));
  // HTML entities are glyphs and escapes, not prose: &times; is a × on a close
  // button, and its letters must not read as an untranslated word.
  src = src.replace(/&[a-zA-Z]+;|&#\d+;/g, ' ');
  // Proper nouns are not translated anywhere in the dictionary either — the
  // same rule that keeps Guardian and dApps as themselves in all twelve.
  const BRANDS = new Set(['RUNECLAW', 'RUNECLAW Guardian', 'GitHub', 'Sharpe']);
  const found = [];
  src.split('\n').forEach((line, n) => {
    const re = /<(h1|h2|h3|h4|span|p|button|label|option|summary|th|legend|a|li|td)\b([^>]*)>([^<>{}`]*[A-Za-z]{3,}[^<>{}`]*)</g;
    for (const m of line.matchAll(re)) {
      const text = m[3].trim();
      if (/data-i18n/.test(m[2]) || !text || BRANDS.has(text)) continue;
      found.push(`${n + 1}: <${m[1]}> ${text}`);
    }
  });
  assert.deepEqual(found, [], `untranslated static text in index.html:\n  ${found.join('\n  ')}`);
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
