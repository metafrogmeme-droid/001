'use strict';
/**
 * An icon that does not resolve renders as NOTHING. No error, no console
 * warning, no layout shift worth noticing — `<svg><use href="#icon-typo">` is
 * simply blank, and it is blank in exactly the same way on the developer's
 * machine as in production, so nobody finds out from looking.
 *
 * There are two ways to get there and both are one keystroke wide:
 *
 *   1. Reference a symbol that is not in the sprite — a typo, or an icon
 *      renamed in js/icons.js while a call site kept the old id.
 *   2. Put a `<use>` on a page that never loads js/icons.js. The sprite is
 *      injected by that script; without it the symbol table is empty and
 *      EVERY icon on the page is blank, including the brand mark.
 *
 * Written 2026-08-17, when 25 emoji across the landing page were replaced with
 * sprite icons and 18 new symbols were added in one pass. Nothing about that
 * change is visible in a test run, and "I looked at the page" only covers the
 * sections that were scrolled to and the branches that were rendered — the
 * live feed's eight icons only appear once the engine pushes an event, and the
 * marketplace fallback icon only when an agent has no icon of its own.
 *
 * Both checks are derived from the files. Neither can be satisfied by a list
 * that somebody remembered to update.
 */

const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

const PUB = path.join(__dirname, '..', 'public');
const SPRITE_SRC = fs.readFileSync(path.join(PUB, 'js', 'icons.js'), 'utf8');

/** Every symbol the sprite actually defines. */
const DEFINED = new Set(
  [...SPRITE_SRC.matchAll(/<symbol id="([^"]+)"/g)].map((m) => m[1]));

const htmlPages = fs.readdirSync(PUB).filter((f) => f.endsWith('.html'));
const jsBundles = fs.readdirSync(path.join(PUB, 'js')).filter((f) => f.endsWith('.js'));
const readPage = (f) => fs.readFileSync(path.join(PUB, f), 'utf8');
const readJs = (f) => fs.readFileSync(path.join(PUB, 'js', f), 'utf8');

/**
 * Every `<use href="#…">` in a source file, split by how the id is formed:
 *
 *   exact     `href="#icon-shield"`              — one named symbol
 *   prefix    `href="#icon-arrow-' + dir + '"`   — a FAMILY of symbols
 *   dynamic   `href="#' + ICONS[type]`           — id supplied entirely by a
 *                                                  variable; see dynamicIdsIn
 *
 * THE PREFIX CASE IS WHY THIS IS NOT A ONE-LINE REGEX, and it was found the
 * first time this file was pointed at real markup: matching greedily reported
 * `#icon-arrow-` as a missing symbol (it is half an id, nothing declares it)
 * AND left `icon-arrow-up` / `icon-arrow-down` looking unreferenced, so the
 * same construct failed two different tests in opposite directions. A checker
 * that manufactures the accusation it exists to prevent is worse than none.
 */
function usesIn(src) {
  const exact = [];
  const prefixes = [];
  const re = /<use\s+href=\\?["']#([A-Za-z0-9_-]*)(\\?["']\s*\+|\$\{)?/g;
  for (const m of src.matchAll(re)) {
    if (m[2]) { if (m[1]) prefixes.push(m[1]); }   // '' => fully dynamic
    else if (m[1]) exact.push(m[1]);
  }
  return { exact, prefixes };
}

/**
 * Does this file build a `<use>` target at RUNTIME rather than writing it out?
 *   '<use href="#' + ICONS[ev.event_type] + '">'      (concatenation)
 *   `<use href="#${icon}">`                            (template literal)
 */
const hasDynamicUse = (src) =>
  /<use\s+href=\\?["']#(?:\\?["']\s*\+|\$\{)/.test(src);

/**
 * The string table feeding a dynamic `<use>` — the literal id cannot be read
 * off the tag, so the `'icon-…'` strings in the same file stand in for it.
 * Without this, turning a literal call site into a lookup would quietly leave
 * the sprite unchecked there.
 *
 * ONLY FOR FILES THAT ACTUALLY BUILD ONE. Applied everywhere it fired on
 * `strategy.html`, which has a CSS class named `.icon-emoji` — a class, not a
 * symbol, and no `<use>` on the page at all. A heuristic that reports working
 * code teaches its reader to skim past it, which costs more than the coverage
 * is worth.
 */
const dynamicIdsIn = (src) => (hasDynamicUse(src)
  ? [...src.matchAll(/['"](icon-[a-z0-9-]+)['"]/g)].map((m) => m[1])
  : []);

/**
 * The sprite file is where symbols are DEFINED, so scanning it for references
 * matches its own `<symbol id=>` table and the `<use href="#icon-name">` in
 * its docstring. CLAUDE.md: strip comments first — a comment quoting the thing
 * it documents is indistinguishable from code doing it.
 */
const SPRITE_FILE = 'icons.js';

test('every referenced symbol exists in the sprite', () => {
  const bad = [];
  const check = (where, src) => {
    const { exact, prefixes } = usesIn(src);
    for (const id of exact.concat(dynamicIdsIn(src))) {
      if (!DEFINED.has(id)) bad.push(`${where} -> #${id}`);
    }
    // A prefix has to resolve to at least one symbol; `#icon-arow-` + dir is
    // a typo that would otherwise draw nothing for every value of dir.
    for (const pre of prefixes) {
      if (![...DEFINED].some((id) => id.startsWith(pre))) {
        bad.push(`${where} -> #${pre}* (no symbol starts with it)`);
      }
    }
  };
  for (const f of htmlPages) check(f, readPage(f));
  for (const f of jsBundles) {
    if (f === SPRITE_FILE) continue;          // defines them; see above
    check('js/' + f, readJs(f));
  }
  assert.deepStrictEqual(bad, [],
    'these references resolve to no symbol, so they render as empty space:\n  '
    + bad.join('\n  '));
});

test('every page that renders a <use> also loads the sprite', () => {
  // Including via the scripts it pulls in: dashboard.js emits `<use>` markup,
  // so a page loading dashboard.js needs the sprite even if its own HTML has
  // no icon in it.
  const offenders = [];
  for (const f of htmlPages) {
    const src = readPage(f);
    const loadsSprite = /\/js\/icons\.js/.test(src);
    if (loadsSprite) continue;
    const u = usesIn(src);
    if (u.exact.length || u.prefixes.length) { offenders.push(`${f}: markup uses <use>`); continue; }
    const scripts = [...src.matchAll(/src="\/js\/([A-Za-z0-9_.-]+\.js)/g)].map((m) => m[1]);
    for (const s of scripts) {
      if (!fs.existsSync(path.join(PUB, 'js', s))) continue;
      const su = usesIn(readJs(s));
      if (su.exact.length || su.prefixes.length) {
        offenders.push(`${f}: loads js/${s}, which renders <use>`);
        break;
      }
    }
  }
  assert.deepStrictEqual(offenders, [],
    'these pages render icon references with no sprite in the document, so '
    + 'every icon on them is blank:\n  ' + offenders.join('\n  '));
});

test('the sprite carries no symbol nobody references', () => {
  // The known_failures.txt rule again: an unused symbol is dead weight served
  // to every visitor, and a symbol kept "just in case" is the one that drifts
  // out of style with the rest.
  const referenced = new Set();
  const mark = (src) => {
    const { exact, prefixes } = usesIn(src);
    exact.forEach((i) => referenced.add(i));
    dynamicIdsIn(src).forEach((i) => referenced.add(i));
    // Every member of a referenced family counts as used — `icon-arrow-up`
    // and `icon-arrow-down` are both reachable from one `icon-arrow-` site.
    for (const pre of prefixes) {
      for (const id of DEFINED) if (id.startsWith(pre)) referenced.add(id);
    }
  };
  for (const f of htmlPages) mark(readPage(f));
  for (const f of jsBundles) { if (f !== SPRITE_FILE) mark(readJs(f)); }
  const dead = [...DEFINED].filter((id) => !referenced.has(id));
  assert.deepStrictEqual(dead, [],
    'symbols defined but never used — delete them in the commit that made '
    + 'them unused:\n  ' + dead.join('\n  '));
});

test('the sweep is measuring something', () => {
  assert.ok(DEFINED.size >= 30,
    `the sprite parsed to ${DEFINED.size} symbols — the <symbol id=> match `
    + 'has stopped finding them');
  assert.ok(DEFINED.has('brand-mark'), 'brand-mark is the one icon on every page');
  // And a reference that SHOULD fail must fail, or the checks above are noise.
  assert.ok(!DEFINED.has('icon-does-not-exist'));
  const total = htmlPages.reduce((n, f) => n + usesIn(readPage(f)).exact.length, 0);
  assert.ok(total >= 20,
    `only ${total} <use> references found across ${htmlPages.length} pages — `
    + 'the reference scan is not seeing the markup it checks');
});
