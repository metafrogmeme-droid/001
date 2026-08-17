'use strict';
/**
 * Every text token must clear WCAG AA against every surface it lands on.
 *
 * A dark theme makes this easy to get wrong and hard to notice: everything
 * looks "fine" on a good monitor in a dark room, and the failure shows up on a
 * phone in daylight for the people least likely to report it.
 *
 * MEASURED 2026-08-17, and the honest result was that the palette was almost
 * clean. Exactly one pair failed:
 *
 *     --down (#e5484d) on --surface-2   4.39:1   (AA needs 4.5)
 *     --down (#e5484d) on --surface-3   4.00:1
 *
 * which is the colour a LOSING position is written in, on exactly the card
 * surfaces positions are rendered on. #ec5a5f is the smallest shift that
 * clears every surface (4.62:1 on the darkest) and still reads as loss.
 *
 * WHY THIS IS COMPUTED FROM THE TOKENS rather than from a rendered page.
 * A browser audit of the live DOM reported three "failures" that were all
 * measurement artifacts — the skip link is translated off-screen until
 * focused, and the hero heading and primary button use gradients, for which
 * getComputedStyle returns a transparent backgroundColor. Chasing those would
 * have meant "fixing" things that were never broken. The tokens are the
 * contract; the render is downstream of them.
 *
 * The pairs below are the ones the site actually produces. Adding a surface or
 * a text colour without adding it here is the gap this cannot close on its own.
 */

const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

const css = fs.readFileSync(
  path.join(__dirname, '..', 'public', 'styles.css'), 'utf8');

/** Read a hex token out of the :root block. */
function token(name) {
  const m = css.match(new RegExp('--' + name + ':\\s*(#[0-9a-fA-F]{6})'));
  assert.ok(m, `--${name} is not defined as a hex value in styles.css`);
  return m[1];
}

const rgb = (hex) => [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16));

function luminance([r, g, b]) {
  const f = (v) => {
    v /= 255;
    return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4);
  };
  return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b);
}

function contrast(a, b) {
  const l1 = luminance(rgb(a));
  const l2 = luminance(rgb(b));
  return (Math.max(l1, l2) + 0.05) / (Math.min(l1, l2) + 0.05);
}

const SURFACES = ['bg', 'surface', 'surface-2', 'surface-3'];
//: Text tokens that carry MEANING and are read as prose or numbers. Decorative
//: tokens (--line, --glow) are excluded on purpose — they are not read.
const TEXT = ['text', 'text-2', 'text-3', 'gold', 'gold-bright', 'steel',
  'up', 'down', 'warn', 'info'];

test('every text token clears AA on every surface it can land on', () => {
  const failures = [];
  for (const t of TEXT) {
    for (const s of SURFACES) {
      const r = contrast(token(t), token(s));
      if (r < 4.5) failures.push(`--${t} on --${s}: ${r.toFixed(2)}:1`);
    }
  }
  assert.deepStrictEqual(failures, [],
    'these text/surface pairs are below the 4.5:1 AA floor:\n  '
    + failures.join('\n  '));
});

test('the loss colour specifically clears the darkest card', () => {
  // Pinned by name because this is the one that failed, and because a position
  // in the red is the single figure a user most needs to be able to read.
  const r = contrast(token('down'), token('surface-3'));
  assert.ok(r >= 4.5,
    `--down on --surface-3 is ${r.toFixed(2)}:1 — a losing position becomes `
    + 'hard to read on exactly the surface positions are rendered on');
});

test('gain and loss are within a stop of each other in contrast', () => {
  // If one is much brighter than the other, the eye reads it as louder, and
  // "colour is a claim" applies to emphasis as much as to hue: a red that
  // shouts next to a green that whispers editorialises the P&L.
  const up = contrast(token('up'), token('surface-2'));
  const down = contrast(token('down'), token('surface-2'));
  assert.ok(Math.abs(up - down) < 2.0,
    `--up is ${up.toFixed(2)}:1 and --down is ${down.toFixed(2)}:1 on `
    + '--surface-2; one state is being emphasised over the other by contrast '
    + 'alone');
});

test('button text clears AA against its own filled background', () => {
  // Filled buttons set an explicit foreground rather than inheriting, so they
  // are their own pairing and are missed by a token-vs-surface sweep.
  const pairs = [
    ['#14120a', token('gold'), '.btn--primary'],
    ['#04121e', token('gold'), '.mobile-cta'],
  ];
  for (const [fg, bg, what] of pairs) {
    const r = contrast(fg, bg);
    assert.ok(r >= 4.5, `${what}: ${fg} on ${bg} is ${r.toFixed(2)}:1`);
  }
});

test('the sweep is measuring something', () => {
  // A derived guard whose derivation silently stops matching passes
  // vacuously — this file would "pass" with an empty token list.
  assert.ok(TEXT.length >= 8 && SURFACES.length >= 4);
  for (const t of TEXT.concat(SURFACES)) assert.match(token(t), /^#[0-9a-f]{6}$/i);
  // And a pair that is SUPPOSED to fail must still fail, or the maths is wrong.
  assert.ok(contrast('#0a0b10', '#12141c') < 4.5,
    'two near-black surfaces should not pass AA against each other — the '
    + 'contrast calculation is broken');
});
