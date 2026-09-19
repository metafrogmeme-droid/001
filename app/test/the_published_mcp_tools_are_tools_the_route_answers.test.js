'use strict';
/**
 * A tool name this product PUBLISHES to an agent developer is one `POST /mcp`
 * answers.
 *
 * `docs/gitbook/mcp-integration.md` is published on GitBook — `agent_card.json`
 * names it as the documentation — and it carried the status row
 *
 *     MCP tool adapter layer | Implemented -- bot/mcp/server.py, live over
 *     JSON-RPC at POST /mcp
 *
 * plus the sentence "`app/routes/mcp.js` mounts it at `POST /mcp`". The card's
 * own `mcp_tools` listed the same nine `runeclaw_*` names, and its
 * `interfaces_note` named both files as the MCP interface. Driven,
 * `app/routes/mcp.js` references neither that module nor any of the nine, and
 * each one answers `{"code":-32602,"message":"Unknown tool: runeclaw_scan"}`.
 *
 * THE GUARD THAT STOOD OVER IT COULD NOT SEE THAT.
 * `tests/test_mcp_doc_matches_the_code.py` calls itself THE CONTROL and
 * asserts three true things — the module exists, it builds a catalogue, and
 * `app.use('/mcp'` appears in `app/server.js`. The conjunction is false:
 * existence of both ends is not a connection between them, which is
 * `words_reach`'s "a door existing is not the door leading where the row says"
 * one process boundary over. Its sibling proved the doc's table and
 * `TOOL_CATALOGUE` agree exactly — they do, and neither is reachable.
 *
 * So the claim is checked where it can be measured: against the route. This is
 * the `web_reads_examples_reach_the_intercepts` rule — the sentence a surface
 * tells a caller to use is a claim about ANOTHER surface, so the other surface
 * checks it — with a process boundary in the middle instead of a regex.
 */
process.env.JWT_SECRET = 'j'.repeat(64);
delete process.env.DATABASE_URL;
delete process.env.BOT_GATEWAY_URL;
delete process.env.WEB_GATEWAY_SECRET;

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const express = require('express');

const ROOT = path.join(__dirname, '..', '..');
const DOC = path.join(ROOT, 'docs', 'gitbook', 'mcp-integration.md');
const CARD = path.join(ROOT, 'agent_card.json');
const ROUTE_SRC = fs.readFileSync(path.join(ROOT, 'app', 'routes', 'mcp.js'), 'utf8');

let server, base;

function rpc(msg) {
  return new Promise((resolve, reject) => {
    const payload = JSON.stringify(msg);
    const r = http.request(`${base}/mcp`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
    }, (res) => {
      let d = '';
      res.on('data', (c) => { d += c; });
      res.on('end', () => resolve({ status: res.statusCode, body: d ? JSON.parse(d) : null }));
    });
    r.on('error', reject);
    r.write(payload);
    r.end();
  });
}

test.before(async () => {
  const app = express();
  app.use(express.json({ limit: '1mb' }));
  app.use('/mcp', require('../routes/mcp'));
  await new Promise((res) => { server = app.listen(0, '127.0.0.1', res); });
  base = `http://127.0.0.1:${server.address().port}`;
});

test.after(() => { if (server) server.close(); });

/** The names the route answers, asked rather than read off its source. */
async function answered() {
  const r = await rpc({ jsonrpc: '2.0', id: 1, method: 'tools/list' });
  assert.equal(r.status, 200);
  const names = (r.body.result.tools || []).map((t) => t.name);
  assert.ok(names.length, 'tools/list answered no tools — nothing is measured');
  return new Set(names);
}

test('the agent card publishes tool names POST /mcp answers', async () => {
  const card = JSON.parse(fs.readFileSync(CARD, 'utf8'));
  const live = await answered();
  assert.ok(Array.isArray(card.mcp_tools) && card.mcp_tools.length,
    'agent_card.json publishes no mcp_tools — an empty list claims the agent '
    + 'exposes none, which is a reading nobody took');
  for (const name of card.mcp_tools) {
    assert.ok(live.has(name),
      `agent_card.json advertises MCP tool "${name}" and POST /mcp answers `
      + `"Unknown tool: ${name}". A card's mcp_tools is a claim to any agent `
      + 'that reads it. Re-render: node app/scripts/render_agent_card_tools.js');
  }
});

test('the card names only the surface that answers', () => {
  const card = JSON.parse(fs.readFileSync(CARD, 'utf8'));
  const note = String(card.interfaces_note || '');
  // The card's own voice, with what it QUOTES removed — the correction has to
  // name what it corrected. Here the retraction is a clause, not a quoted
  // span, so the claim is pinned by its SHAPE: `mcp:` must not present
  // bot/mcp/server.py as one of the interfaces.
  const mcpClause = (note.split(/mcp:/)[1] || '').split(/\.\s/)[0];
  assert.ok(mcpClause, 'interfaces_note no longer says what mcp: is');
  assert.ok(!/bot\/mcp\/server\.py/.test(mcpClause),
    'agent_card.json names bot/mcp/server.py as an MCP interface. It has no '
    + 'HTTP door, so that points an agent at nine tool names POST /mcp '
    + `answers Unknown tool for.\n  ${mcpClause}`);
  assert.ok(/app\/routes\/mcp\.js/.test(mcpClause),
    'interfaces_note no longer names the surface that does answer');
});

test('the card is the rendered list, not a second one kept in step by hand', () => {
  const { render, routeToolNames } = require('../scripts/render_agent_card_tools');
  const onDisk = fs.readFileSync(CARD, 'utf8');
  assert.equal(render(onDisk, routeToolNames()), onDisk,
    'agent_card.json is stale against app/routes/mcp.js — run '
    + 'node app/scripts/render_agent_card_tools.js');
});

test('the renderer refuses to publish an empty list from a failed read', () => {
  const { render } = require('../scripts/render_agent_card_tools');
  // `render` takes the names; the REFUSAL lives in `routeToolNames`, so drive
  // that path by giving the route nothing to read. An unreadable registry is
  // not an empty one: `mcp_tools: []` would publish "exposes no MCP tools"
  // from a read that failed, on the one field that says what it exposes.
  const mod = require('../routes/mcp');
  const realTools = mod.TOOLS;
  const realWrites = mod.WRITE_TOOLS;
  try {
    mod.TOOLS = {};
    mod.WRITE_TOOLS = {};
    const { routeToolNames } = require('../scripts/render_agent_card_tools');
    assert.throws(() => routeToolNames(), /refusing to publish an empty/,
      'a failed read must not render as an agent that exposes nothing');
  } finally {
    mod.TOOLS = realTools;
    mod.WRITE_TOOLS = realWrites;
  }
  assert.equal(typeof render, 'function');
});

test('the published doc names no tool POST /mcp does not answer', async () => {
  const doc = fs.readFileSync(DOC, 'utf8');
  const live = await answered();
  // THE ADAPTER'S OWN TABLE IS EXEMPT AS A WHOLE, not by the `runeclaw_`
  // prefix. Its rows have two columns — the MCP name and the SKILL name it
  // dispatches to — so a prefix exemption let `scan_market`, `check_risk` and
  // `run_backtest` through and the first draft of this test accused the doc
  // of naming three tools the route does not answer. They are not tools it
  // claims; they are the registry's own names, in a section that says in as
  // many words that none of it is callable at POST /mcp. A line carrying a
  // `runeclaw_` cell is that table.
  const lines = doc.split('\n').filter((l) => !/runeclaw_/.test(l));
  const spelled = new Set([...lines.join('\n').matchAll(/`([a-z][a-z0-9_]{4,})`/g)].map((m) => m[1]));
  const looksLikeALiveTool = (n) => live.has(n)
    || /^(get|scan|run|verify|compile|stress|plan|research|ask|xray|arena)_/.test(n);
  for (const name of spelled) {
    if (!looksLikeALiveTool(name)) continue;          // prose, not a tool name
    assert.ok(live.has(name),
      `mcp-integration.md spells \`${name}\` as a tool and POST /mcp answers `
      + `"Unknown tool: ${name}"`);
  }
});

test('the doc does not present the runeclaw_* names as callable over HTTP', () => {
  const doc = fs.readFileSync(DOC, 'utf8');
  // BOUNDED TO THE STATUS TABLE, because the claim lived in a table ROW and a
  // sentence-anywhere scan cannot tell a claim from its own retraction. The
  // first draft forbade the literal "`app/routes/mcp.js` mounts it" and then
  // failed on the paragraph this slice added to say that sentence was WRONG —
  // "a comment that quotes the string it forbids is indistinguishable from
  // the code doing it", from the author's side, which CLAUDE.md records and
  // which happened here anyway.
  const rows = doc.split('\n').filter((l) => /^\s*\|/.test(l));
  for (const row of rows) {
    if (!/bot\/mcp\/server\.py/.test(row)) continue;
    assert.ok(!/POST\s+`?\/mcp/.test(row),
      'a status row presents bot/mcp/server.py as answering at POST /mcp. It '
      + 'does not: app/routes/mcp.js names neither that module nor any '
      + `runeclaw_* tool, and each of the nine answers Unknown tool.\n  ${row}`);
    assert.ok(!/\*\*Implemented\*\*/.test(row) || /not served/i.test(row),
      `a status row calls the unserved adapter Implemented without saying it `
      + `has no door.\n  ${row}`);
  }
  // And the page must SAY which surface answers, or a reader lands on the
  // runeclaw_* table with nothing telling them it is not the one.
  assert.ok(/Which surface answers your call/.test(doc),
    'the page no longer distinguishes the two MCP pieces, which is the state '
    + 'it was in when the status row was false');
  assert.ok(/not callable there/i.test(doc),
    'the page no longer says the runeclaw_* names are not callable at POST /mcp');
});

test('the route really is the one with no reference to the adapter', () => {
  // THE CONTROL, and it runs the other way round: if app/routes/mcp.js ever
  // DOES mount the Python catalogue, the sentences forbidden above become
  // true and this guard should be revisited rather than silently keeping a
  // true sentence off the page.
  assert.ok(!/bot\/mcp|bot\.mcp/.test(ROUTE_SRC),
    'app/routes/mcp.js now references bot/mcp — the doc may say so again, and '
    + 'this guard needs rewriting rather than working around');
  assert.ok(!/runeclaw_[a-z]/.test(ROUTE_SRC),
    'app/routes/mcp.js now names a runeclaw_* tool');
});
