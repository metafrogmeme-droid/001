'use strict';

/**
 * Every browser fetch needs a deadline, for the same reason every other one
 * this week did.
 *
 * fetchJSON in app.js has always had one — an AbortController armed at the
 * start of the call. But six call sites went straight to `fetch()` and
 * skipped it, and `fetch` has no default timeout at all. A request that never
 * returns leaves the page waiting forever, with the UI stuck on whatever it
 * said before the call:
 *
 *   - guardian-console: "Reading the market…", permanently
 *   - strengthmap boot: the empty-state placeholder, permanently
 *   - dashboard share:  the Share button does nothing, permanently — and the
 *                       comment beside it says "card is optional", which is
 *                       exactly the guarantee the code did not provide
 *
 * Each of these already had an honest failure branch. They simply could not
 * REACH it, which is the same shape as the engine's hung analyze phase: a
 * handler that only runs if the thing can finish.
 *
 * Second defect, in the venue lookup. It read:
 *
 *     const d = r.ok ? await r.json() : null;
 *     const vs = (d && d.venues) || [];
 *     ... .join('') || 'No venues found.'
 *
 * A non-OK status became an empty list, and an empty list rendered
 * "No venues found." — a claim ABOUT THE MARKET manufactured from a 503. The
 * not-ready gate in server.js returns exactly that status while the schema is
 * unmigrated, so this was reachable on any database blip. Unreadable is never
 * zero; it now throws into the catch that already said the true thing.
 */

const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

const JS_DIR = path.join(__dirname, '..', 'public', 'js');
const read = (f) => fs.readFileSync(path.join(JS_DIR, f), 'utf8');

/** Every `fetch(` call plus the ~4 lines of options that follow it. */
function fetchCalls(src) {
  const out = [];
  const re = /\bfetch\s*\(/g;
  let m;
  while ((m = re.exec(src))) {
    // Take to the end of the 5th line from the call — long enough to include
    // a multi-line options object, short enough not to swallow the next call.
    const tail = src.slice(m.index);
    out.push(tail.split('\n').slice(0, 6).join('\n'));
  }
  return out;
}

const FILES = ['app.js', 'dashboard.js', 'guardian-console.js', 'strengthmap.js',
               'i18n.js', 'mascot3d.js', 'theater.js'];

// ── the sweep ─────────────────────────────────────────────────────────────

test('no browser fetch is left without a deadline', () => {
  const naked = [];
  for (const f of FILES) {
    for (const call of fetchCalls(read(f))) {
      // Two acceptable forms: an explicit AbortSignal.timeout, or the
      // controller fetchJSON arms for itself.
      if (/AbortSignal\.timeout\(/.test(call)) continue;
      if (/signal:\s*ctrl\.signal/.test(call)) continue;
      naked.push(`${f}: ${call.split('\n')[0].trim()}`);
    }
  }
  assert.deepEqual(naked, [],
    'fetch has no default timeout — an unbounded one waits forever');
});

test('the caps are seconds, not minutes', () => {
  // A cap longer than a person will wait is a cap that never helps them.
  for (const f of FILES) {
    for (const m of read(f).matchAll(/AbortSignal\.timeout\((\d+)\)/g)) {
      const ms = Number(m[1]);
      assert.ok(ms >= 3000, `${f}: ${ms}ms would cut requests that would succeed`);
      assert.ok(ms <= 20000, `${f}: ${ms}ms is longer than anyone waits`);
    }
  }
});

test('every bounded call lands in a branch that already existed', () => {
  // An AbortSignal.timeout rejects with a TimeoutError, so each call must sit
  // inside a try/catch or carry a .catch — otherwise the deadline converts a
  // hang into an unhandled rejection, which is not an improvement.
  const cases = [
    ['guardian-console.js', 'sentinelView(null)'],
    ['strengthmap.js', 'Venue lookup unavailable.'],
    ['strengthmap.js', 'The market feed is unavailable right now.'],
    ['dashboard.js', 'card is optional'],
    ['mascot3d.js', 'catch (e) { return false; }'],
    ['i18n.js', '.catch(function () {});'],
  ];
  for (const [f, needle] of cases) {
    assert.ok(read(f).includes(needle), `${f} lost its honest failure branch: ${needle}`);
  }
});

// ── unreadable is never zero ──────────────────────────────────────────────

/**
 * Source with comments removed.
 *
 * Three separate tests today failed on their own explanation: a comment that
 * NAMES the string it forbids, or quotes the bug it fixed, is indistinguishable
 * from the code doing it when you scan raw text. Scan code; read prose
 * separately if you need to.
 */
function codeOnly(src) {
  return src.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '');
}

test('an unreadable venue lookup does not claim there are no venues', () => {
  const src = codeOnly(read('strengthmap.js'));
  const block = src.slice(src.indexOf("fetch('/api/market/venues/"),
                          src.indexOf('function closePanel'));
  assert.match(block, /if \(!r\.ok\) throw new Error\('venues'\)/,
    'a 503 from the not-ready gate must not render as an empty market');
  // The honest message stays, and the zero-claim can now only come from a
  // response we actually read.
  assert.ok(block.includes('Venue lookup unavailable.'));
  assert.ok(block.includes('No venues found.'));
  assert.ok(block.indexOf('if (!r.ok) throw') < block.indexOf('No venues found.'),
    'the guard must precede the branch it protects');
});

test('the ternary that swallowed the status is gone', () => {
  const src = codeOnly(read('strengthmap.js'));
  assert.ok(!/r\.ok \? await r\.json\(\) : null/.test(src),
    'r.ok ? json : null turns "we could not read" into "there is nothing"');
});

// ── the established helper is still the default ───────────────────────────

test('every changed bundle is served under a new cache-buster', () => {
  // A deadline that stays in the cached bundle bounds nothing. The `assets`
  // digest added in #974 makes a forgotten bump VISIBLE; it does not make the
  // browser fetch the new file.
  const floors = {
    'dashboard.js': 128, 'i18n.js': 105, 'mascot3d.js': 4,
    'strengthmap.js': 4, 'guardian-console.js': 3,
  };
  const pub = path.join(__dirname, '..', 'public');
  for (const page of fs.readdirSync(pub).filter(f => f.endsWith('.html'))) {
    const html = fs.readFileSync(path.join(pub, page), 'utf8');
    for (const [file, min] of Object.entries(floors)) {
      const re = new RegExp(file.replace('.', '\\.') + '\\?v=(\\d+)', 'g');
      for (const m of html.matchAll(re)) {
        assert.ok(Number(m[1]) >= min,
          `${page} serves ${file} v${m[1]}, below the fixed build (v${min})`);
      }
    }
  }
});

test('fetchJSON remains the bounded path everything else should use', () => {
  const app = read('app.js');
  assert.match(app, /const ctrl = new AbortController\(\);/);
  assert.match(app, /setTimeout\(\(\) => ctrl\.abort\(\), timeoutMs\)/);
  assert.match(app, /timeoutMs = 10000/, 'with a sane default');
  assert.match(app, /clearTimeout\(timer\)/, 'and no timer left behind');
});
