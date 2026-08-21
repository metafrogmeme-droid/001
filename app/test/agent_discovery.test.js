'use strict';
// The agent surface had no front door.
//
// Probed on 2026-08-21 exactly as an outside agent would, against a running
// instance:
//
//   /.well-known/mcp.json         404
//   /.well-known/ai-plugin.json   404
//   /.well-known/agent.json       404
//   /api/tools                    404
//   /developers                   200  — 11.8KB of HTML, for a human
//
// 33 MCP tools, an ERC-8257 manifest, ERC-8004 identity cards and a paper
// Arena, and no programmatic path from the domain root to any of it. The
// manifest route is `/.well-known/ai-tool/:slug.json`, which requires knowing
// the slug is `runeclaw-intel` before you can ask for it.
//
// These tests drive the real routers over a real server, because the defect
// was never visible in the source — every individual route worked. It was the
// ABSENCE of one that broke adoption, and absence is only visible from outside.

const test = require('node:test');
const assert = require('node:assert');
const http = require('http');
const express = require('express');

const { TOOLS, WRITE_TOOLS, SERVER_INFO, PROTOCOL_VERSION } = require('../routes/mcp');

let server;
let base;

test.before(async () => {
  const app = express();
  // Mount order matters and is the point of one test below: discovery must
  // come first or tool8257's `:slug.json` pattern swallows `index.json`.
  app.use(require('../routes/discovery'));
  app.use(require('../routes/tool8257'));
  app.use('/mcp', require('../routes/mcp'));
  await new Promise((res) => { server = app.listen(0, '127.0.0.1', res); });
  base = `http://127.0.0.1:${server.address().port}`;
});

test.after(async () => {
  if (server) await new Promise((res) => server.close(res));
});

const getJson = async (p) => {
  const r = await fetch(base + p);
  return { status: r.status, body: r.status === 200 ? await r.json() : null };
};

test('an agent can discover the MCP endpoint from the domain alone', async () => {
  const { status, body } = await getJson('/.well-known/mcp.json');
  assert.strictEqual(status, 200);
  assert.ok(body.mcp && body.mcp.endpoint, 'no endpoint advertised');
  assert.match(body.mcp.endpoint, /\/mcp$/);
});

test('the endpoint it advertises actually answers JSON-RPC', async () => {
  // The whole journey, end to end: fetch the document, follow it, get tools.
  // A discovery doc that points somewhere dead is worse than none.
  const { body } = await getJson('/.well-known/mcp.json');
  const r = await fetch(body.mcp.endpoint, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ jsonrpc: '2.0', id: 1, method: 'tools/list' }),
  });
  const out = await r.json();
  assert.strictEqual(r.status, 200);
  assert.ok(Array.isArray(out.result.tools), 'tools/list did not answer');
  assert.strictEqual(out.result.tools.length,
    Object.keys(TOOLS).length + Object.keys(WRITE_TOOLS).length);
});

test('the counts are DERIVED from the registry, not written down', async () => {
  // The reason this document exists is partly that two hand-written counts had
  // rotted (INTEROP.md said 17 read-only tools when there were 30). A machine-
  // read document is the last place a maintained literal belongs.
  const { body } = await getJson('/.well-known/mcp.json');
  assert.strictEqual(body.tools.read_only, Object.keys(TOOLS).length);
  assert.strictEqual(body.tools.requires_key, Object.keys(WRITE_TOOLS).length);
  assert.strictEqual(body.tools.total, body.tools.read_only + body.tools.requires_key);
});

test('it states the same identity and protocol the server answers with', async () => {
  // A discovery doc advertising a protocol version the server does not speak
  // is worse than no discovery doc, so both come from one exported constant.
  const { body } = await getJson('/.well-known/mcp.json');
  assert.strictEqual(body.name, SERVER_INFO.name);
  assert.strictEqual(body.version, SERVER_INFO.version);
  assert.strictEqual(body.mcp.protocol_version, PROTOCOL_VERSION);
});

test('it does not claim a standard that does not exist', async () => {
  // MCP has no ratified .well-known discovery spec. Saying so is the same
  // discipline as an UNVERIFIED anchor: never claim more than is true.
  const { body } = await getJson('/.well-known/mcp.json');
  assert.match(body.note, /no ratified .well-known discovery standard/i);
});

test('it tells an agent how to reach the key-gated tools', async () => {
  const { body } = await getJson('/.well-known/mcp.json');
  assert.match(body.authentication.arena_tools, /rcarena_/);
  // And what the key can NOT do — the scope limit is the reassuring half and
  // has to be stated, not implied.
  assert.match(body.authentication.arena_tools, /cannot see an exchange account/i);
  assert.match(body.authentication.read_only_tools, /none/i);
});

test('the ai-tool index lists the slug the manifest route requires', async () => {
  // Without this an agent must already know the slug is `runeclaw-intel`.
  const { status, body } = await getJson('/.well-known/ai-tool/index.json');
  assert.strictEqual(status, 200);
  assert.ok(Array.isArray(body.tools) && body.tools.length >= 1);
  assert.ok(body.tools[0].slug, 'no slug published');
});

test('every manifest URL the index publishes resolves', async () => {
  // The index is a promise; this is the taker. The first version of this route
  // was mounted AFTER tool8257, whose `:slug.json` pattern matched
  // `index.json` with slug="index" and 404'd it — so the discovery document
  // advertised its own index at a dead URL. Caught by fetching it, not by
  // reading it.
  const { body } = await getJson('/.well-known/ai-tool/index.json');
  for (const t of body.tools) {
    const r = await fetch(t.manifest);
    assert.strictEqual(r.status, 200, `${t.manifest} -> ${r.status}`);
    const manifest = await r.json();
    assert.strictEqual(manifest.name, t.slug);
  }
});

test('an unknown slug is still a 404', async () => {
  // The control. Mounting discovery first must not turn the manifest route
  // into a wildcard that answers for anything.
  const r = await fetch(base + '/.well-known/ai-tool/not-a-real-tool.json');
  assert.strictEqual(r.status, 404);
});

test('server.js mounts discovery BEFORE tool8257', () => {
  // THE TESTS ABOVE CANNOT SEE THIS, and that is why it is here.
  //
  // They build their own express app and choose their own mount order, so
  // they verify the behaviour in an arrangement they construct — not the one
  // that ships. Reordering server.js re-breaks the index (tool8257's
  // `:slug.json` swallows `index.json` and 404s it) and every assertion above
  // still passes. Found by mutating server.js and watching 10/10 stay green.
  //
  // Wiring is the narrow case where a source scan is the right tool: no unit
  // test can reach "these two lines are in this order in the composition
  // root".
  //
  // ONLY `//` COMMENTS ARE STRIPPED, and the block-comment strip that used to
  // be here is gone because it BROKE THE TEST. `/\*[\s\S]*?\*\//g` is
  // non-greedy but still spans from any stray `/*` — inside a string, a regex
  // literal, a URL — to the next `*/`, and server.js has one early enough that
  // the match swallowed the mount lines entirely. The test then failed on
  // correct code, reporting "server.js does not mount routes/discovery" about
  // a file that plainly does.
  //
  // A new shape of the trap CLAUDE.md opens with: not a comment quoting the
  // string it forbids, but a comment-stripper eating the code it was meant to
  // isolate. Matching the whole `app.use(require(...))` statement is what
  // makes stripping `//` prose sufficient here — the explanations above and in
  // server.js name the routes, but neither writes the call.
  const fs = require('node:fs');
  const path = require('node:path');
  const raw = fs.readFileSync(path.join(__dirname, '..', 'server.js'), 'utf8');
  const code = raw.split('\n').map((l) => l.replace(/\/\/.*$/, '')).join('\n');

  const discovery = code.indexOf("app.use(require('./routes/discovery'))");
  const tool8257 = code.indexOf("app.use(require('./routes/tool8257'))");
  assert.ok(discovery !== -1, 'server.js does not mount routes/discovery');
  assert.ok(tool8257 !== -1, 'server.js does not mount routes/tool8257');
  assert.ok(discovery < tool8257,
    'routes/discovery must be mounted BEFORE routes/tool8257, or '
    + '/.well-known/ai-tool/index.json is matched by :slug.json and 404s');
});

test('the interop links it publishes are absolute and self-consistent', async () => {
  const { body } = await getJson('/.well-known/mcp.json');
  for (const [key, url] of Object.entries(body.interop)) {
    assert.match(url, /^https?:\/\//, `${key} is not absolute: ${url}`);
  }
  assert.ok(body.interop.erc8257_manifest.endsWith('.json'));
  assert.match(body.documentation, /\/developers$/);
});
