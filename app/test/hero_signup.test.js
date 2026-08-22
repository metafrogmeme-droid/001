// The IA audit found the register form buried at landing position #7. The hero
// now carries an email-capture above the fold that hands off to the real form
// (no auth-logic duplication). These lock the surfacing + the safe hand-off.
const { test } = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

const read = (p) => fs.readFileSync(path.join(__dirname, '..', 'public', p), 'utf8');

test('hero has an email-capture form above the fold', () => {
  const html = read('index.html');
  const hero = html.slice(html.indexOf('<header class="hero">'), html.indexOf('</header>'));
  assert.match(hero, /id="heroSignup"/, 'hero signup form missing');
  const input = hero.match(/<input[^>]*id="hero-email"[^>]*>/);
  assert.ok(input, 'hero email input missing');
  assert.match(input[0], /type="email"/, 'hero email input must be type=email');
});

test('hero hand-off reuses the real register form, not a duplicate auth path', () => {
  const html = read('index.html');
  // It prefills the real form field and defers to the existing register flow.
  assert.match(html, /getElementById\('reg-email'\)/, 'must prefill the real reg-email');
  assert.match(html, /switchTab\('register'\)/, 'must switch to the register tab');
  // It must NOT re-implement the account-creation POST itself.
  //
  // THE WINDOW USED TO RUN FROM THE FORM MARKUP TO A COMMENT — from
  // `id="heroSignup"` (line 194, the <form>) all the way to
  // `indexOf('Landing mind-stream')` (line ~1790). Fifteen hundred lines of
  // unrelated page, bounded at one end by prose.
  //
  // THAT BOUNDARY WAS SHAPING THE PRODUCTION CODE. index.html carried a
  // comment above the /api/call/latest block reading "It sits below the
  // mind-stream marker because hero_signup.test.js slices from id=heroSignup
  // to this comment and forbids a fetch( inside that window". A real feature
  // was positioned on the page to stay outside a test's comment-delimited
  // span. The test was not measuring the hero handler; it was measuring
  // distance, and the codebase was arranging itself around the measurement.
  //
  // The window is now the hero IIFE itself — from the line that looks the form
  // up to the `})();` that closes it. Nothing else can wander in, and nothing
  // else has to stay out.
  const at = html.indexOf("getElementById('heroSignup')");
  assert.ok(at > 0, 'the hero handler no longer looks the form up');
  const rest = html.slice(at);
  const close = rest.indexOf('})();');
  assert.ok(close > 0, 'the hero handler is no longer a self-contained IIFE');
  const handler = rest.slice(0, close);
  assert.ok(handler.includes('addEventListener'), 'the hero handler window is empty');
  assert.doesNotMatch(handler, /fetch\(/, 'hero handler must not POST directly');
});

test('hero.free_note exists in all six languages', () => {
  const i18n = read('js/i18n.js');
  const line = i18n.split('\n').find((l) => l.includes("'hero.free_note'"));
  assert.ok(line, 'hero.free_note missing');
  for (const lang of ['en', 'es', 'zh', 'pt', 'fr', 'ar']) {
    assert.match(line, new RegExp(`${lang}:\\s*'`), `hero.free_note missing ${lang}`);
  }
});
