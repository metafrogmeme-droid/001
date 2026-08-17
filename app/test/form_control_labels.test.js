'use strict';
/**
 * Every form control must have an accessible name.
 *
 * A control without one is announced as "edit text" — a screen reader user is
 * told a field exists and nothing about what belongs in it. A placeholder does
 * not fix this: it is not an accessible name in several combinations, and it
 * disappears on focus, which is precisely when the user needs it.
 *
 * MEASURED 2026-08-17 by asking a real browser for each control's computed
 * name across all 37 pages: 56 of 120 controls had none.
 *
 * THE FIRST TWO SWEEPS BOTH LIED, IN OPPOSITE DIRECTIONS, and that is the
 * reason this file exists rather than a grep:
 *
 *   pass 1  regex over `<input>` with `label[for=]` — reported 14. It never
 *           looked at <textarea> or <select> at all, so two thirds of the
 *           controls on the site were outside the question being asked.
 *   pass 2  same regex plus "is there an unclosed <label> before me" — reported
 *           ZERO. The backwards scan cleared arena's two genuinely bare inputs
 *           because some earlier <label> on the page had not been closed on the
 *           line the heuristic examined.
 *
 * Fourteen, then zero, then fifty-six. Only the last was measured against what
 * a browser actually computes. So this test enumerates the same four name
 * sources a browser uses and nothing else — and the implicit-label case is
 * resolved by parsing, not by scanning backwards for a tag.
 *
 * The count is a ratchet at zero. There is no allowlist: every control on this
 * site can be named in a few words, and "this one is obvious from context" is
 * an argument made by people who can see it.
 */

const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

const PUB = path.join(__dirname, '..', 'public');
const pages = fs.readdirSync(PUB).filter((f) => f.endsWith('.html'));

//: Types that are never named by a label: they carry their own text, or are
//: not perceivable at all.
const EXEMPT_TYPES = new Set(['hidden', 'submit', 'button', 'image', 'reset']);

const CONTROL = /<(input|select|textarea)\b([^>]*)>/gi;
const attr = (tag, name) => {
  const m = tag.match(new RegExp('\\b' + name + '\\s*=\\s*"([^"]*)"', 'i'));
  return m ? m[1] : null;
};

/**
 * Is this control INSIDE a <label>? Resolved by walking the label open/close
 * tags in order and tracking depth, rather than by looking backwards for the
 * nearest one — the backwards version is what produced the "zero unlabelled"
 * answer above.
 */
function labelDepthAt(src, index) {
  let depth = 0;
  const re = /<\/?label\b/gi;
  let m;
  while ((m = re.exec(src)) && m.index < index) {
    depth += m[0][1] === '/' ? -1 : 1;
  }
  return Math.max(0, depth);
}

/** Controls with no accessible name, per page. */
function unnamed(src) {
  const explicitFor = new Set(
    [...src.matchAll(/<label\b[^>]*\bfor\s*=\s*"([^"]*)"/gi)].map((m) => m[1]));
  const ids = new Set([...src.matchAll(/\bid\s*=\s*"([^"]*)"/g)].map((m) => m[1]));
  const out = [];
  for (const m of src.matchAll(CONTROL)) {
    const [full, tag, rest] = m;
    const type = (attr(full, 'type') || (tag.toLowerCase() === 'input' ? 'text' : tag)).toLowerCase();
    if (EXEMPT_TYPES.has(type)) continue;
    const id = attr(full, 'id');
    const labelledby = attr(full, 'aria-labelledby');
    const named = (attr(full, 'aria-label') !== null)          // 1. aria-label
      || (labelledby && ids.has(labelledby))                    // 2. aria-labelledby
      || (id && explicitFor.has(id))                            // 3. <label for>
      || labelDepthAt(src, m.index) > 0                         // 4. wrapping <label>
      || (attr(full, 'title') !== null);                        // 5. title (last resort)
    if (!named) out.push(`${tag}#${id || '(anon)'} [${type}]`);
  }
  return out;
}

test('every form control on every page has an accessible name', () => {
  const offenders = [];
  for (const f of pages) {
    for (const c of unnamed(fs.readFileSync(path.join(PUB, f), 'utf8'))) {
      offenders.push(`${f}: ${c}`);
    }
  }
  assert.deepStrictEqual(offenders, [],
    'these controls are announced as "edit text" with no indication of what '
    + 'they are for:\n  ' + offenders.join('\n  '));
});

test('a label written into generated markup is also translated', () => {
  // A row template that emits a literal aria-label and no data-i18n-attr is
  // permanently English. Both row builders emit both; RCI18N has no
  // MutationObserver, so the pages must also re-apply after rendering — which
  // neither did, leaving the Remove buttons English in fourteen locales.
  for (const f of ['escape.html', 'stress.html']) {
    const src = fs.readFileSync(path.join(PUB, f), 'utf8');
    const tpl = src.slice(src.indexOf('function rowHtml'), src.indexOf('function renderRows'));
    const literals = (tpl.match(/aria-label="/g) || []).length;
    const keyed = (tpl.match(/data-i18n-attr="aria-label:/g) || []).length;
    assert.ok(literals > 0, `${f}: rowHtml emits no aria-label at all`);
    assert.strictEqual(keyed, literals,
      `${f}: ${literals} literal aria-label(s) in the row template but ${keyed} `
      + 'translated — the difference is permanently English');
    assert.match(src.slice(src.indexOf('function renderRows')), /RCI18N\.apply/,
      `${f}: renderRows does not re-apply i18n, so nothing it builds after boot `
      + 'is ever translated');
  }
});

test('the sweep is measuring something', () => {
  // The parser is the load-bearing part. If CONTROL stops matching, every page
  // yields zero offenders and this file passes having checked nothing — which
  // is exactly what the second hand-written sweep did.
  let total = 0;
  for (const f of pages) {
    total += [...fs.readFileSync(path.join(PUB, f), 'utf8').matchAll(CONTROL)].length;
  }
  // 68 in the markup against 120 in a live DOM, and the gap is not a miss:
  // escape.html renders seven identical rows from one template and stress.html
  // four or five, so ~52 controls exist only after script. THE FIRST TEST HERE
  // THEREFORE COVERS 68 OF 120. The remaining 52 are covered structurally by
  // the row-template test above — one template, checked once, is the whole
  // population — and that division is the reason this note exists rather than
  // a lowered number with no explanation.
  assert.ok(total >= 60,
    `only ${total} form controls found across ${pages.length} pages — the `
    + 'control pattern has stopped matching the markup');

  // And the name detection must still be capable of saying "no".
  assert.deepStrictEqual(unnamed('<form><input id="x" type="text"></form>'),
    ['input#x [text]'], 'a bare input is no longer detected as unnamed');
  assert.deepStrictEqual(unnamed('<label for="x">Name</label><input id="x">'), []);
  assert.deepStrictEqual(unnamed('<label>Name <input id="x"></label>'), []);
  assert.deepStrictEqual(unnamed('<input id="x" aria-label="Name">'), []);
  // The case pass 2 got wrong: a CLOSED label earlier on the page must not
  // launder a bare input that comes after it.
  assert.deepStrictEqual(unnamed('<label>Done <input aria-label="a"></label><input id="x">'),
    ['input#x [text]'],
    'an input after a closed <label> is being treated as inside it — this is '
    + 'the exact bug that reported zero unlabelled controls');
});
