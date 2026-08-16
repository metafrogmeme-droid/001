'use strict';
/**
 * The Arena over MCP — and the exact size of a stolen key.
 *
 * This is the first WRITE surface on a server that was public, unauthenticated
 * and read-only by design. So the tests worth writing are not "an agent can
 * open a position" — that is one test — but the boundary around it:
 *
 *   · a write tool must be unreachable from the PUBLIC, UNAUTHENTICATED
 *     /api/tool/invoke dispatcher, which calls tool.handler directly. That is
 *     why the write tools live in their own registry instead of in TOOLS.
 *   · a key must not authenticate anything but the paper Arena.
 *   · a key must not be a session — authMiddleware must refuse it.
 *   · the MCP annotations must say a write is a write, because clients use
 *     readOnlyHint to decide what to auto-approve without asking a human.
 *
 * The blast radius this pins: somebody can lose your VIRTUAL money and put bad
 * trades on your public paper record. Never real funds, never an exchange
 * credential, never another user's row.
 */

process.env.JWT_SECRET = 'j'.repeat(64);
delete process.env.DATABASE_URL;

const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const { codeOnly } = require('./helpers/code_only');

const APP = path.join(__dirname, '..');
const mcp = require(path.join(APP, 'routes', 'mcp.js'));
const keys = require(path.join(APP, 'lib', 'arena_keys.js'));

const WRITES = ['arena_open', 'arena_close', 'arena_my_positions'];

// ── the registries are separate, and that is the safety argument ──────────

test('no write tool is reachable from the public unauthenticated dispatcher', () => {
  // routes/tool8257.js does `require('./mcp').TOOLS[name].handler(args)` with
  // NO auth. A write tool in that registry would be callable with curl.
  for (const w of WRITES) {
    assert.ok(!(w in mcp.TOOLS),
      `${w} is in TOOLS — /api/tool/invoke would run it unauthenticated`);
    assert.ok(w in mcp.WRITE_TOOLS, `${w} belongs in WRITE_TOOLS`);
  }
});

test('tool8257 reads only the read-only registry', () => {
  const src = fs.readFileSync(path.join(APP, 'routes', 'tool8257.js'), 'utf8');
  const code = src.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '');
  assert.ok(!/WRITE_TOOLS/.test(code),
    'the public invoke endpoint must never see the write registry');
  assert.match(code, /require\('\.\/mcp'\)\.TOOLS/);
});

test('every write tool demands a key', () => {
  for (const w of WRITES) {
    assert.strictEqual(mcp.WRITE_TOOLS[w].requiresKey, true, w);
  }
});

test('no read tool accidentally became a write', () => {
  for (const [name, t] of Object.entries(mcp.TOOLS)) {
    assert.ok(!t.requiresKey, `${name} is in the public registry but wants a key`);
  }
});

// ── a key is not a session ────────────────────────────────────────────────

test('an Arena key is rejected by the normal auth middleware', () => {
  // If authMiddleware ever accepted one, the key would reach every JWT-authed
  // route in the app — live trading, exchange credentials, the lot.
  const { authMiddleware } = require(path.join(APP, 'auth.js'));
  const key = keys.PREFIX + 'A'.repeat(43);
  let status = null;
  const res = {
    status(c) { status = c; return this; },
    json() { return this; },
    setHeader() {},
  };
  let nexted = false;
  authMiddleware({ headers: { authorization: `Bearer ${key}` }, cookies: {} },
    res, () => { nexted = true; });
  assert.strictEqual(nexted, false, 'an Arena key must never pass as a session');
  assert.strictEqual(status, 401);
});

test('the key format is recognisable and strictly checked', () => {
  assert.ok(keys.looksLikeKey(keys.PREFIX + 'a'.repeat(43)));
  for (const bad of ['', null, undefined, 'rcarena_', 'rcarena_short',
    'Bearer rcarena_' + 'a'.repeat(43), keys.PREFIX + 'a'.repeat(42),
    keys.PREFIX + 'a'.repeat(44), keys.PREFIX + '!'.repeat(43),
    'jwt.token.here', 'a'.repeat(43)]) {
    assert.strictEqual(keys.looksLikeKey(bad), false, JSON.stringify(bad));
  }
});

test('the plaintext key is never stored', () => {
  const src = fs.readFileSync(path.join(APP, 'lib', 'arena_keys.js'), 'utf8');
  const code = src.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '');
  // The INSERT must bind the hash, never the raw key.
  assert.match(code, /INSERT INTO arena_api_keys[\s\S]*?hash\(key\)/);
  assert.ok(!/console\.(log|error|warn)\([^)]*\braw\b/.test(code),
    'a key must never reach a log');
  assert.ok(!/SELECT[^']*key_hash[^']*'\s*\+/.test(code), 'no string-built SQL');
});

test('bearerFrom parses only a well-formed header, and never throws', () => {
  assert.strictEqual(keys.bearerFrom({ headers: { authorization: 'Bearer abc' } }), 'abc');
  for (const h of [undefined, '', 'abc', 'Basic abc', 'Bearer', 'Bearer  ']) {
    const got = keys.bearerFrom({ headers: { authorization: h } });
    assert.ok(got === null || typeof got === 'string', JSON.stringify(h));
  }
  assert.strictEqual(keys.bearerFrom(undefined), null);
  assert.strictEqual(keys.bearerFrom({}), null);
});

// ── the annotations tell a client the truth ───────────────────────────────

test('the write tools are annotated as writes', () => {
  const src = fs.readFileSync(path.join(APP, 'routes', 'mcp.js'), 'utf8');
  const code = src.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '');
  assert.match(code, /readOnlyHint:\s*false/,
    'a client uses readOnlyHint to auto-approve; a write must not claim true');
  assert.ok(!/annotations:\s*\{\s*readOnlyHint:\s*true,\s*openWorldHint:\s*false\s*\}\s*\)\)/.test(code),
    'the hardcoded all-tools-are-reads annotation is gone');
});

// ── the write tools ride the SAME path the browser does ───────────────────

test('the Arena tools call the shared open/close functions, not their own SQL', () => {
  // A second door into the Arena would be a second copy of the season rules,
  // the armed envelope check and the seal — and the copy nobody watches is
  // the one that stops being fail-closed.
  const src = fs.readFileSync(path.join(APP, 'routes', 'mcp.js'), 'utf8');
  const block = src.slice(src.indexOf('const WRITE_TOOLS'), src.indexOf('function allTools'));
  assert.match(block, /openForUser\(ctx\.userId/);
  assert.match(block, /closeForUser\(ctx\.userId/);
  assert.ok(!/INSERT INTO|UPDATE |DELETE FROM/i.test(block),
    'the MCP tools must not write the Arena tables directly');
});

test('the shared path is exported for exactly that reason', () => {
  const arena = require(path.join(APP, 'routes', 'arena.js'));
  for (const fn of ['openForUser', 'closeForUser', 'loadPositions', 'loadAccount']) {
    assert.strictEqual(typeof arena[fn], 'function', fn);
  }
});

test('the HTTP routes are thin wrappers over the same functions', () => {
  const src = fs.readFileSync(path.join(APP, 'routes', 'arena.js'), 'utf8');
  assert.match(src, /router\.post\('\/open'[\s\S]{0,200}openForUser\(req\.user\.user_id/);
  assert.match(src, /router\.post\('\/close'[\s\S]{0,200}closeForUser\(req\.user\.user_id/);
});

// ── a tool call with no key refuses, and says how to fix it ───────────────

test('a keyless write call is refused with an actionable message', async () => {
  const rpc = { jsonrpc: '2.0', id: 1, method: 'tools/call',
    params: { name: 'arena_open', arguments: { symbol: 'BTCUSDT', direction: 'LONG', margin: 100, leverage: 2 } } };
  const res = await postMcp(rpc, {});           // no Authorization header
  const text = res.result.content[0].text;
  assert.strictEqual(res.result.isError, true);
  assert.match(text, /Arena key/);
  assert.match(text, /Authorization: Bearer/);
});

test('a malformed key is refused exactly like an absent one', async () => {
  const rpc = { jsonrpc: '2.0', id: 1, method: 'tools/call',
    params: { name: 'arena_my_positions', arguments: {} } };
  const a = await postMcp(rpc, {});
  const b = await postMcp(rpc, { authorization: 'Bearer not-a-key' });
  assert.strictEqual(a.result.content[0].text, b.result.content[0].text,
    'a caller must not be able to tell "revoked" from "never existed"');
});

// ── §4 on the payload, driven rather than scanned ─────────────────────────

test('no Arena tool can emit an account-money field, whatever the row holds', () => {
  // The repo-wide source scan (public_no_dollars) catches the spelling. This
  // catches the SHAPE: the handlers allowlist their output, so a virtual
  // dollar figure appearing in an arena row cannot reach an agent even if
  // openForUser/closeForUser grow a new field tomorrow.
  const BANNED = ['pnl', 'pnl_usd', 'net_pnl_usd', 'realized_pnl',
    'unrealized_pnl', 'equity', 'equity_usd', 'balance', 'balance_usd',
    'margin', 'margin_usd', 'notional', 'notional_usd'];
  const src = fs.readFileSync(path.join(APP, 'routes', 'mcp.js'), 'utf8');
  const block = src.slice(src.indexOf('const WRITE_TOOLS'), src.indexOf('function allTools'));
  // Spreading a shared function's return is the shape that leaks: it carries
  // whatever that function decides to include, forever.
  assert.ok(!/\.\.\.r\.body/.test(block),
    'allowlist the fields; spreading r.body leaks whatever it gains later');
  // codeOnly, not a hand-rolled block-comment strip. My first version stripped
  // only /* */ and then failed on my own LINE comment explaining why `margin:`
  // is avoided — a comment quoting the string it forbids is indistinguishable
  // from the code doing it, which CLAUDE.md records as having caused four
  // false failures before this one.
  const code = codeOnly(block);
  for (const f of BANNED) {
    assert.ok(!new RegExp(`\\b${f}:`).test(code),
      `${f} must not be emitted by an Arena tool`);
  }
});

test('the agent speaks percent, not virtual dollars', () => {
  // Every account starts on the identical stake, so a percent means the same
  // thing for every competitor and a virtual dollar figure does not.
  const t = mcp.WRITE_TOOLS.arena_open;
  assert.ok('stake_pct' in t.inputSchema.properties);
  assert.ok(!('margin' in t.inputSchema.properties));
  assert.ok(t.inputSchema.required.includes('stake_pct'));
});

test('a non-positive stake is refused before it reaches the Arena', async () => {
  for (const bad of [0, -1, 'abc', NaN, Infinity]) {
    await assert.rejects(
      () => mcp.WRITE_TOOLS.arena_open.handler(
        { symbol: 'BTCUSDT', direction: 'LONG', stake_pct: bad, leverage: 2 },
        { userId: 1 }),
      /positive percent/, String(bad));
  }
});

// ── harness ───────────────────────────────────────────────────────────────

const http = require('node:http');
const express = require('express');
let server, base;

test.before(async () => {
  const app = express();
  app.use('/mcp', require(path.join(APP, 'routes', 'mcp.js')));
  await new Promise((r) => { server = app.listen(0, '127.0.0.1', r); });
  base = `http://127.0.0.1:${server.address().port}`;
});
test.after(() => { if (server) server.close(); });

function postMcp(body, headers) {
  return new Promise((resolve, reject) => {
    const payload = JSON.stringify(body);
    const r = http.request(`${base}/mcp`, {
      method: 'POST',
      headers: Object.assign({ 'Content-Type': 'application/json' }, headers || {}),
    }, (res) => {
      let d = '';
      res.on('data', (c) => { d += c; });
      res.on('end', () => { try { resolve(JSON.parse(d)); } catch (e) { reject(e); } });
    });
    r.on('error', reject);
    r.write(payload);
    r.end();
  });
}
