'use strict';
/**
 * Two MCP tools served dollars on the unauthenticated surface.
 *
 * `/mcp` takes no token — the `router.use` above the tool registry is a 64 KB
 * body cap, not auth — and `/api/tool/invoke` exposes the same registry. §4:
 * public surfaces carry percent / ratio / count only.
 *
 * get_weekly_letter called `letters.getLetter(...)`, the PRIVATE composer.
 * Its own body emits, verbatim:
 *
 *     `Net PnL <b>${money(net)}</b> across ${trades.length} closes`
 *     best: ${symbol} ${money(best.pnl)}   worst: ${symbol} ${money(worst.pnl)}
 *
 * `composePublicLetter` exists for exactly this surface — it emits "Equity
 * moved +X% on the week" — and the tool one block down already uses
 * `getPublicLetter`. This one reached for the private composer instead.
 *
 * get_showcase_trade returned the raw `trades` row, `size_usd` and dollar
 * `pnl` included. The route it mirrors converts to `pnl_pct` under the
 * comment "prices are public market facts; the sizes and dollar PnL are not".
 *
 *     THE PUBLIC VARIANT ALREADY EXISTED IN BOTH CASES. THE TOOL SIMPLY DID
 *     NOT CALL IT.
 *
 * WHY THIS IS A SOURCE TEST AND NOT A RESPONSE TEST
 *
 * The in-memory test DB has no trades, so BOTH letters come back without a
 * dollar in them and a response-level assertion passes on the broken code.
 * The leak only appears with real rows. So the check is: which composer does
 * the tool call, and does the showcase tool project the row. Verified against
 * the composers' own emit strings, which is where the difference actually
 * lives.
 */

const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

const { codeOnly } = require('./helpers/code_only');

const MCP = codeOnly(
  fs.readFileSync(path.join(__dirname, '..', 'routes', 'mcp.js'), 'utf8'));
const LETTER = codeOnly(
  fs.readFileSync(path.join(__dirname, '..', 'lib', 'letter.js'), 'utf8'));

function toolBody(name) {
  const i = MCP.indexOf(`${name}: {`);
  assert.ok(i > 0, `tool ${name} not found`);
  return MCP.slice(i, i + 2200);
}

test('/mcp is still the unauthenticated surface this is about', () => {
  // If it ever gains auth, these constraints can be revisited — but the
  // reason must not silently stop applying.
  assert.ok(!MCP.includes('authMiddleware'));
  assert.ok(/rateLimit\(/.test(MCP));
});

test('the private letter composer really does emit dollars', () => {
  // The premise. If this stops being true the tests below are pointless.
  const i = LETTER.indexOf('function composeLetter');
  const j = LETTER.indexOf('function composePublicLetter');
  assert.ok(i > 0 && j > i);
  const priv = LETTER.slice(i, j);
  assert.ok(/Net PnL <b>\$\{money\(net\)\}/.test(priv),
    'the private composer no longer emits Net PnL in dollars');
  assert.ok(/money\(best\.pnl\)/.test(priv));
});

test('the public letter composer emits percent, not dollars', () => {
  const j = LETTER.indexOf('function composePublicLetter');
  const pub = LETTER.slice(j, j + 6000);
  assert.ok(/Equity moved <b>\$\{pct/.test(pub), 'public letter lost its percent');
  assert.ok(!/money\(net\)/.test(pub), 'the public composer emits dollars');
});

test('get_weekly_letter uses the PUBLIC composer', () => {
  const body = toolBody('get_weekly_letter');
  assert.ok(/getPublicLetter\(/.test(body),
    'the weekly letter tool serves the private, dollar-bearing letter');
  assert.ok(!/getLetter\(/.test(body.replace(/getPublicLetter\(/g, '')),
    'it still reaches for the private composer');
});

test('get_showcase_trade projects the row instead of returning it', () => {
  const body = toolBody('get_showcase_trade');
  assert.ok(/return \{ trade: \{/.test(body),
    'the raw DB row is returned, size_usd and pnl included');
  assert.ok(/pnl_pct:/.test(body), 'the percent must replace the dollars');
});

test('the showcase projection carries no dollar field', () => {
  const body = toolBody('get_showcase_trade');
  const i = body.indexOf('return { trade: {');
  const proj = body.slice(i, i + 700);
  for (const banned of ['size_usd:', 'pnl:', 'fees:', 'notional']) {
    assert.ok(!proj.includes(banned), `${banned} on an unauthenticated tool`);
  }
  // Prices ARE public market facts — they must survive.
  assert.ok(proj.includes('entry_price') && proj.includes('exit_price'));
});

test('an unmeasurable percent is absent, not zero', () => {
  // A missing or zero size has no honest percentage. Rendering 0% would
  // state a fact the row does not carry.
  const body = toolBody('get_showcase_trade');
  const i = body.indexOf('pnl_pct:');
  const expr = body.slice(i, i + 260);
  assert.ok(expr.includes(': null'), 'an unmeasurable percent must be null');
  assert.ok(/_size > 0/.test(expr), 'a zero size must not divide');
});

test('a scratch is its own outcome', () => {
  // Folding flat into loss would misreport a canceled/expired fill, which
  // the bot does produce.
  const body = toolBody('get_showcase_trade');
  assert.ok(body.includes("'flat'"));
});

test('the dollar figure is still used to PICK the trade', () => {
  // Selecting the largest absolute move is correct and needs the dollars —
  // they simply must not leave. Removing the selection would change which
  // trade is showcased, which is not what this fix is for.
  const body = toolBody('get_showcase_trade');
  assert.ok(/Math\.abs\(parseFloat\(b\.pnl\)\)/.test(body));
});
