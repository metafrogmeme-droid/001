'use strict';
/**
 * A fourteen-language landing page whose five suggestions were English.
 *
 * `chat.js` localises its chat drawer through `T(key, english)` — eighteen
 * calls, thirteen `dd.ct_*` keys, every language. Two lines above one of them
 * sat `CHIP_PROMPTS`: a plain array of English strings, rendered as the five
 * suggestion chips an anonymous visitor sees before they have typed anything.
 * A Chinese reader on the Chinese landing page was offered five questions in
 * English, in a drawer whose every other word was Chinese.
 *
 * A CHIP'S TEXT IS BOTH THE LABEL AND THE MESSAGE IT SENDS, which is the whole
 * reason the two lists are treated differently here.
 *
 *   * the PUBLIC five carry a key. Localising them is all upside: the visitor
 *     reads their language, sends it, and the model answers in it. The FAQ
 *     short-circuit correctly defers, because its triggers are English
 *     (bot/core/faq_kb.py) — so a starter question now costs a model call and
 *     buys a reply in the right language, which is the trade this makes
 *     deliberately. When no model is reachable the visitor gets the localised
 *     `chat_public_fallback`, which names the five English phrasings that the
 *     built-in answers still match.
 *   * the SIGNED-IN fourteen do not, and must not until label and payload are
 *     separated: "Backtest SOL" and "Long ETH" are parsed by a rule router
 *     that reads ENGLISH, so a translated chip would silently downgrade a
 *     skill dispatch to generic chat — a worse failure than an English label.
 *
 * The loop is DRIVEN below rather than grepped, because the property that
 * matters is one no scan can see: the click handler reads the button's CURRENT
 * text, so the label and the payload are the same string by construction and
 * stay that way across a language switch.
 */

const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

const SRC = path.join(__dirname, '..', 'public', 'js', 'chat.js');
const chat = fs.readFileSync(SRC, 'utf8');
const DICT = require('../public/js/i18n.js');
const LANGS = DICT.LANGS.map((l) => l.code);
const STRINGS = DICT.STRINGS;

/** The public chip descriptors, read out of the source as JS rather than text. */
function publicChips() {
  const start = chat.indexOf('const CHIP_PROMPTS = PUBLIC ? [');
  assert.ok(start > 0, 'CHIP_PROMPTS moved');
  const open = chat.indexOf('[', start);
  const close = chat.indexOf('] : [', open);
  assert.ok(close > open, 'the public branch moved');
  return new Function(`return ${chat.slice(open, close + 1)};`)();
}

const CHIPS = publicChips();

// ── the dictionary side ─────────────────────────────────────────────────────

test('every public chip carries a key', () => {
  assert.equal(CHIPS.length, 5);
  for (const c of CHIPS) {
    assert.ok(c && typeof c.key === 'string' && c.key,
      `a public chip is still a bare string: ${JSON.stringify(c)}`);
  }
});

test('every chip key exists in every language the site offers', () => {
  for (const c of CHIPS) {
    const row = STRINGS[c.key];
    assert.ok(row, `${c.key} is not in the dictionary`);
    for (const code of LANGS) {
      assert.ok(row[code] && String(row[code]).trim(),
        `${c.key} has no ${code}`);
    }
  }
});

test('every translation is actually translated', () => {
  for (const c of CHIPS) {
    const row = STRINGS[c.key];
    for (const code of LANGS) {
      if (code === 'en') continue;
      assert.notEqual(row[code], row.en, `${c.key}.${code} still reads English`);
    }
  }
});

test('the inline English fallback matches the dictionary', () => {
  // `T(key, en)` falls back to the second argument when the chunk has not
  // landed. If that string drifts from the dictionary's `en`, a reader with a
  // slow chunk sees different words than one without — and the payload, which
  // is this same text, changes with it.
  for (const c of CHIPS) {
    assert.equal(c.en, STRINGS[c.key].en, `${c.key}: fallback drifted`);
  }
});

// Whether the five English payloads still reach a built-in answer is pinned on
// the PYTHON side (tests/test_public_chips_reach_an_answer.py), where
// `faq_answer` can actually be called. The first draft of that check lived here
// and asserted `faq.includes(bare) || bare.split(' ').slice(-3).join(' ').length
// > 0` — whose right-hand side is true for every non-empty string, so the test
// could not fail. A guard that cannot fail is worse than no guard: it reads as
// coverage.

// ── the behaviour no scan can see ───────────────────────────────────────────

/** Run the real chip loop against a stub DOM, and return what it built. */
function renderChips({ lang }) {
  const start = chat.indexOf('CHIP_PROMPTS.forEach((p) => {');
  assert.ok(start > 0, 'the chip loop moved');
  const end = chat.indexOf('\n    });', start);
  const body = chat.slice(start, end + '\n    });'.length);

  const sent = [];
  const made = [];
  const document = {
    createElement() {
      const el = {
        attrs: {}, listeners: {}, textContent: '', type: '', className: '',
        setAttribute(k, v) { this.attrs[k] = v; },
        getAttribute(k) { return this.attrs[k]; },
        addEventListener(ev, fn) { this.listeners[ev] = fn; },
        click() { this.listeners.click(); },
      };
      made.push(el);
      return el;
    },
  };
  const chipsEl = { appendChild() {} };
  const T = (key, en) => (STRINGS[key] && STRINGS[key][lang]) || en;

  new Function('CHIP_PROMPTS', 'document', 'chipsEl', 'T', 'send', body)(
    CHIPS, document, chipsEl, T, (m) => sent.push(m));
  return { made, sent };
}

test('a Japanese visitor is offered the questions in Japanese', () => {
  const { made } = renderChips({ lang: 'ja' });
  assert.equal(made.length, 5);
  for (const el of made) {
    assert.ok(/[぀-ヿ一-鿿]/.test(el.textContent),
      `chip rendered without Japanese: ${el.textContent}`);
  }
});

test('what the chip SENDS is what the visitor READ', () => {
  for (const lang of LANGS) {
    const { made, sent } = renderChips({ lang });
    made.forEach((el) => el.click());
    assert.deepEqual(sent, made.map((el) => el.textContent),
      `${lang}: the message sent is not the label shown`);
  }
});

test('the label and the payload cannot drift across a language switch', () => {
  // RCI18N.apply() rewrites `[data-i18n]` text when the reader switches
  // language. The chips are built once, on open, so a payload captured at
  // build time would keep sending the OLD language's question under the NEW
  // language's label — the reader asks one thing and the model is asked
  // another. Reading at click time is what makes that impossible.
  const { made, sent } = renderChips({ lang: 'en' });
  for (const el of made) {
    assert.ok(el.getAttribute('data-i18n'), 'no data-i18n: apply() would skip it');
    el.textContent = STRINGS[el.getAttribute('data-i18n')].es;   // the switch
  }
  made.forEach((el) => el.click());
  assert.deepEqual(sent, made.map((el) => el.textContent));
  for (const m of sent) {
    assert.ok(!/^(What|How|Which)\b/.test(m), `English payload survived: ${m}`);
  }
});

// ── the decision on record ──────────────────────────────────────────────────

test('the signed-in chips are deliberately NOT localised', () => {
  const open = chat.indexOf('] : [');
  const close = chat.indexOf('];', open);
  const signedIn = new Function(`return [${chat.slice(open + 5, close)}];`)();
  assert.ok(signedIn.length > 5);
  for (const c of signedIn) {
    assert.equal(typeof c, 'string',
      'a signed-in chip grew a key: its text is also its payload, and the ' +
      'rule router parses English — separate label from payload first');
  }
});
