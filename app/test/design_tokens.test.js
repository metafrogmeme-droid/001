'use strict';
/**
 * A design pass is exactly when the honesty tokens get quietly recoloured.
 *
 * `--up`, `--down`, `--warn`, `--info` and their `-dim` variants are not
 * decoration. Colour is a claim here: a green accent says "in profit" as
 * loudly as the number beside it, and `-dim` exists so an UNKNOWN value has
 * something muted to wear. A visual refresh that gives "unknown" a confident
 * colour turns every panel that renders it into a lie, silently, on every
 * page at once — and it would look like an improvement in review.
 *
 * So the semantic set is pinned, and the surface pass is held to black and
 * white at low alpha: depth is a shadow, not a hue.
 */

const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

const css = fs.readFileSync(
  path.join(__dirname, '..', 'public', 'styles.css'), 'utf8');

/** The appended surface pass — everything after its banner. */
const LAYER = css.slice(css.indexOf('Surface & depth pass.'));

const SEMANTIC = ['--up', '--down', '--warn', '--info',
  '--up-dim', '--down-dim', '--warn-dim', '--info-dim'];

test('every semantic colour is defined exactly once', () => {
  for (const tok of SEMANTIC) {
    const defs = css.split(new RegExp(`\\${tok}\\s*:`)).length - 1;
    assert.strictEqual(defs, 1, `${tok} is defined ${defs}× — a redefinition `
      + 'later in the file silently wins and changes what a colour claims');
  }
});

test('an unknown state still has a muted colour to wear', () => {
  // The -dim variants are what "we could not read this" renders as. Without
  // them the only options are a verdict colour or nothing.
  for (const tok of ['--up-dim', '--down-dim', '--warn-dim', '--info-dim']) {
    assert.ok(css.includes(`${tok}:`), `${tok} is gone — unknown has no colour`);
    const val = css.slice(css.indexOf(`${tok}:`)).split(';')[0];
    assert.match(val, /rgba\([^)]*0?\.\d+\s*\)/,
      `${tok} must stay translucent — a solid dim is just another verdict`);
  }
});

test('the surface pass introduces no hue of its own', () => {
  // Depth is black or white at low alpha. A coloured shadow or a new accent
  // here would start a second palette that --gold no longer controls.
  const colours = LAYER.match(/rgba?\(([^)]*)\)/g) || [];
  for (const c of colours) {
    const nums = c.replace(/rgba?\(|\)/g, '').split(',').map((x) => x.trim());
    const [r, g, b] = nums;
    const neutral = (r === g && g === b);
    const brand = (r === '63' && g === '182' && b === '255');   // --gold, exactly
    assert.ok(neutral || brand,
      `${c} is a new hue in the surface pass — depth must be neutral, and an `
      + 'accent must come from --gold so the site still re-themes from it');
  }
  assert.ok(!/#[0-9a-fA-F]{3,8}\b/.test(LAYER), 'no hex literals in the surface pass');
});

test('the accent is still the single re-theming point', () => {
  const root = css.slice(css.indexOf(':root {'), css.indexOf('}', css.indexOf(':root {')));
  assert.match(root, /--gold:\s*#3fb6ff/, 'the brand accent moved without notice');
  assert.match(css, /re-themes/, 'the comment promising a one-value re-theme is gone');
});

test('motion added by the pass is covered by reduced-motion', () => {
  // The global block zeroes every duration. A transition written with a
  // literal time instead of a token would escape it.
  const transitions = LAYER.match(/transition:[^;]+;/g) || [];
  assert.ok(transitions.length > 0, 'the pass lost its transitions');
  for (const t of transitions) {
    assert.ok(!/\d+m?s/.test(t.replace(/var\([^)]*\)/g, '')),
      `${t.trim()} hard-codes a duration and will ignore prefers-reduced-motion`);
  }
});

test('the focus ring is visible against the near-black ground', () => {
  // The browser default outline over #0a0b10 is close to invisible, which is
  // an accessibility failure that a dark redesign usually introduces.
  assert.match(css, /--ring:\s*0 0 0 2px var\(--bg\), 0 0 0 4px var\(--gold\)/);
  assert.match(LAYER, /:focus-visible[^{]*\{[^}]*box-shadow: var\(--ring\)/);
});

test('every page ships the bumped stylesheet', () => {
  const pub = path.join(__dirname, '..', 'public');
  const versions = new Set();
  for (const f of fs.readdirSync(pub).filter((x) => x.endsWith('.html'))) {
    const m = fs.readFileSync(path.join(pub, f), 'utf8').match(/styles\.css\?v=(\d+)/);
    if (m) versions.add(m[1]);
  }
  assert.strictEqual(versions.size, 1,
    `styles.css ships at ${[...versions].join(', ')} — a page left behind keeps `
    + 'the old sheet and renders the old design against the new markup');
});
