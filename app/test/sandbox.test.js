'use strict';
/**
 * The practice sandbox — and the three ways it could quietly become dishonest.
 *
 * A logged-out visitor used to meet "create a free account" and nothing to do.
 * Now they get live prices, the real Arena rules, and no signup. The feature is
 * easy; the boundaries are the part worth pinning.
 *
 *  1 · IT MUST NEVER BE SEALED OR RANKED. Every Arena trade is hashed at open
 *      and folds into that UTC day's Merkle root — the product's whole trust
 *      claim. Sandbox trades are anonymous, unauthenticated and disposable.
 *      Sealing them would put throwaway data in the ledger the claim rests on;
 *      not sealing them while still calling them Arena trades would create two
 *      classes of Arena trade, one of them quietly unprovable. Both damage the
 *      real thing, so the sandbox is a different thing that says so.
 *
 *  2 · IT MUST NOT REIMPLEMENT THE RULES. A practice mode that liquidates at a
 *      different price than the real one teaches a habit that costs money the
 *      first time it is used for real. It loads the same module the server
 *      runs — not a copy of it.
 *
 *  3 · AN UNREADABLE PRICE IS NOT A PRICE. No `|| 0`, no stale mark shown as
 *      live, no green stripe on an unknown return. This is the house rule in a
 *      surface built specifically to teach people how the numbers work.
 */

const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

const APP = path.join(__dirname, '..');
const read = (...p) => fs.readFileSync(path.join(APP, ...p), 'utf8');

const sb = read('public', 'js', 'sandbox.js');
const html = read('public', 'arena.html');
const i18n = read('public', 'js', 'i18n.js');
/** Comments quote the strings they forbid; strip them before scanning. */
const code = sb.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '');

// ── 1 · it is not the Arena, and cannot be mistaken for it ────────────────

test('nothing in the sandbox is sealed, keyed or ranked', () => {
  for (const banned of ['seal', 'trade_key', 'leaderboard', 'sha256', 'merkle']) {
    assert.ok(!new RegExp(banned, 'i').test(code),
      `${banned} in the sandbox would put throwaway data in the provable ledger`);
  }
});

test('the sandbox never writes to the server', () => {
  // A read of the public price feed is the only network call it may make.
  const calls = [...code.matchAll(/fetch\(\s*'([^']+)'/g)].map((m) => m[1]);
  assert.deepStrictEqual(calls, ['/api/market/tickers'],
    `the sandbox called ${calls.join(', ')} — it must only read public prices`);
  for (const verb of ['method:', 'POST', 'PUT', 'DELETE', 'PATCH']) {
    assert.ok(!code.includes(verb), `${verb} means it is writing somewhere`);
  }
});

test('the panel states its limits where they will be read', () => {
  const panel = html.slice(html.indexOf('id="sandboxPanel"'),
    html.indexOf('</section>', html.indexOf('id="sandboxPanel"')));
  assert.match(panel, /data-i18n-html="sb\.badge"/, 'the badge must carry its markup');
  const badge = i18n.split('\n').find((l) => l.includes("'sb.badge':"));
  assert.ok(badge, 'sb.badge exists');
  for (const claim of ['not sealed', 'not ranked', 'this browser only']) {
    assert.ok(badge.includes(claim), `the badge must say "${claim}"`);
  }
  const lede = i18n.split('\n').find((l) => l.includes("'sb.lede':"));
  assert.match(lede, /nothing is written to a server/);
});

test('it is never called an Arena trade', () => {
  const panel = html.slice(html.indexOf('id="sandboxPanel"'),
    html.indexOf('</section>', html.indexOf('id="sandboxPanel"')));
  assert.ok(!/Arena (trade|position)/i.test(panel),
    'the panel calls a sandbox position an Arena one — that is the conflation '
    + 'this whole design exists to prevent');
});

// ── 2 · one implementation of the rules ───────────────────────────────────

test('the sandbox uses the engine, and defines no rule of its own', () => {
  assert.match(code, /var E = window\.ArenaEngine/);
  for (const fn of ['validateOpen', 'posPnl', 'isLiquidated', 'equity', 'liqPrice']) {
    assert.ok(code.includes(`E.${fn}`), `${fn} must come from the engine`);
  }
  // The giveaway that somebody reimplemented one: the constants.
  for (const constant of ['0.005', 'MMR =', 'MAX_LEVERAGE =', 'START_BALANCE =']) {
    assert.ok(!code.includes(constant),
      `${constant} is a rule copied out of the engine — there must be one copy`);
  }
});

test('the engine really is the module the server runs', () => {
  const shim = read('lib', 'arena.js');
  assert.match(shim, /require\('\.\.\/public\/js\/arena_engine\.js'\)/,
    'the server stopped loading the same file the browser does');
  assert.strictEqual(
    require('../lib/arena.js').liqPrice,
    require('../public/js/arena_engine.js').liqPrice,
    'the two halves are running different functions');
});

test('the engine loads before the sandbox that needs it', () => {
  const e = html.indexOf('arena_engine.js');
  const s = html.indexOf('sandbox.js');
  assert.ok(e > -1 && s > -1 && e < s, 'the sandbox would find no engine');
});

test('a missing engine is reported, not worked around', () => {
  assert.match(code, /if \(!E\) \{[\s\S]*?sb\.e_engine[\s\S]*?return;/,
    'without the rules it must refuse, not improvise');
  // The guard sits BELOW the helpers it calls. It did not, once: T is a `var`
  // function expression, so the failure message threw a TypeError instead of
  // reporting the failure — on the one path nobody clicks through by hand.
  assert.ok(code.indexOf('var T = function') < code.indexOf('var E = window.ArenaEngine'),
    'the engine guard runs before T() exists and will throw instead of report');
});

// ── 3 · an unreadable price is not a price ────────────────────────────────

test('a failed price read never becomes a number', () => {
  for (const banned of ['|| 0', '|| 0,', 'marks = {}']) {
    assert.ok(!code.includes(banned),
      `"${banned}" turns an unreadable price into a measurement`);
  }
  assert.match(code, /marks = null/, 'unread prices are null, not empty');
  assert.match(code, /catch \(function|\.catch\(function \(\) \{ marks = null; \}\)/,
    'a rejected fetch must clear the marks, not leave the last ones looking live');
});

test('an unknown return is muted, never coloured', () => {
  // Colour is a claim: a green stripe says "in profit" as loudly as a number.
  assert.match(code, /r === null \? 'muted' : \(r >= 0 \? 'up' : 'down'\)/,
    'the unknown case must get the muted class, not a direction');
  assert.match(code, /eq === null \? 'muted' : ''/);
});

test('equity is omitted while prices are unread, not shown as zero', () => {
  assert.match(code, /var eq = marks \? E\.equity\([^)]*\) : null;/);
  assert.match(code, /eq === null \? '—'/, 'a dash, not 0.00');
});

test('liquidation is not judged without a price', () => {
  const settle = code.slice(code.indexOf('function settle()'),
    code.indexOf('function pct('));
  assert.match(settle, /if \(!marks\) return;/,
    'liquidating on an unread price would close a position on a number nobody read');
  assert.match(settle, /if \(px === null\) continue;/,
    'one unreadable symbol must not liquidate the others');
});

test('opening and closing refuse without a live price', () => {
  assert.match(code, /if \(px === null\) \{ setMsg\('sb\.e_price'/);
  assert.match(code, /if \(px === null\) \{ setMsg\('sb\.e_close'/);
});

test('the stale notice appears exactly when prices are unread', () => {
  assert.match(code, /stale\.hidden = !!marks/);
  const line = i18n.split('\n').find((l) => l.includes("'sb.stale':"));
  assert.ok(line && /not shown/.test(line) && /estimated/i.test(line));
});

// ── housekeeping ──────────────────────────────────────────────────────────

test('it only appears for logged-out visitors, and only on this page', () => {
  assert.match(code, /if \(!host\) return;/, 'no-op on every other page');
  assert.match(html, /id="sandboxPanel"[^>]*hidden/, 'ships hidden');
  // The test used to stop at the two lines above, while sandbox.js revealed
  // the panel unconditionally — so a signed-in visitor would have seen the
  // practice panel sitting beside the real Arena, the two disagreeing about
  // what a position is. The NAME of a test is not one of its assertions.
  assert.match(code, /if \(window\.RC && RC\.LOGGED_IN\) return;/,
    'a signed-in visitor must not be shown the sandbox');
  assert.ok(code.indexOf('RC.LOGGED_IN') < code.indexOf('host.hidden = false'),
    'the panel is revealed before the logged-in check runs');
});

test('no dollar figure reaches the panel', () => {
  const panel = html.slice(html.indexOf('id="sandboxPanel"'),
    html.indexOf('</section>', html.indexOf('id="sandboxPanel"')));
  assert.ok(!/\$\s?\d/.test(panel));
});

test('every sandbox string ships all fourteen locales', () => {
  const keys = [...html.matchAll(/data-i18n(?:-html)?="(sb\.[a-z_]+)"/g)].map((m) => m[1]);
  assert.ok(keys.length >= 12, `expected the panel's labels, found ${keys.length}`);
  const runtime = ['sb.unknown', 'sb.none', 'sb.opened', 'sb.e_price', 'sb.e_close',
    'sb.e_engine', 'sb.b_close'];
  for (const key of [...new Set([...keys, ...runtime])]) {
    const line = i18n.split('\n').find((l) => l.includes(`'${key}':`));
    assert.ok(line, `i18n defines ${key}`);
    for (const loc of ['en:', 'hi:', 'it:', 'es:', 'zh:', 'pt:', 'fr:', 'de:',
      'nl:', 'ja:', 'ko:', 'ru:', 'tr:', 'ar:']) {
      assert.ok(line.includes(loc), `${key} is missing ${loc}`);
    }
  }
});
