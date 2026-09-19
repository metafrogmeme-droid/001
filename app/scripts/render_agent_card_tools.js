#!/usr/bin/env node
'use strict';
/**
 * Render `agent_card.json`'s `mcp_tools` from the tools `POST /mcp` answers.
 *
 * The card's `mcp_tools` field is a claim to any agent that reads the card:
 * *these are the MCP tools I expose*. It carried nine `runeclaw_*` names from
 * `bot/mcp/server.py`'s in-process catalogue, and driven, every one of them
 * answers `Unknown tool` at `POST /mcp` — the route serves its own registry,
 * built on the libraries behind the public site, and names neither that module
 * nor any of the nine.
 *
 * WHY THIS IS RENDERED AND NOT TYPED. A hand-written list of the route's tools
 * is the `/setllm` ten-of-eleven shape: the tool added tomorrow is the one
 * missing from it, and the card is a discovery artifact nobody re-reads. The
 * source of truth is the route's own registry; this writes it into the card,
 * the card is committed, and
 * `app/test/the_published_mcp_tools_are_tools_the_route_answers.test.js`
 * renders it again and compares — the `scripts/render_secret_shapes.py` rule,
 * with the runtimes the other way round.
 *
 * Committed rather than generated at boot, for the reason that precedent
 * gives: `app/` and the repo root are different deploy targets, and a card
 * that had to run a script to say what it exposes would be empty on the box
 * where the script is missing.
 *
 *     node app/scripts/render_agent_card_tools.js           # write the card
 *     node app/scripts/render_agent_card_tools.js --check   # exit 1 if stale
 */
const fs = require('fs');
const path = require('path');

const CARD = path.join(__dirname, '..', '..', 'agent_card.json');

/** Every tool name `POST /mcp` answers, sorted, read from the route itself. */
function routeToolNames() {
  const mcp = require('../routes/mcp');
  const all = { ...(mcp.TOOLS || {}), ...(mcp.WRITE_TOOLS || {}) };
  const names = Object.keys(all).sort();
  if (!names.length) {
    // An unreadable registry is not an empty one. Writing `[]` here would
    // publish "this agent exposes no MCP tools" from a failed read, on the
    // one field whose job is to say what it exposes.
    throw new Error('read no tools from app/routes/mcp.js — refusing to '
      + 'publish an empty mcp_tools, which would claim the agent exposes none');
  }
  return names;
}

function render(cardText, names) {
  const card = JSON.parse(cardText);
  card.mcp_tools = names;
  // The card is read by hand as often as by machine, so it says where the
  // names came from and how to get the live answer.
  card.mcp_tools_note = 'Rendered from app/routes/mcp.js by '
    + 'app/scripts/render_agent_card_tools.js — the tools POST /mcp answers. '
    + 'Ask the server `tools/list` for the descriptions and input schemas; '
    + 'this list is the build it was rendered from. bot/mcp/server.py is an '
    + 'in-process adapter over the bot skill registry with no HTTP door, so '
    + 'its runeclaw_* names are not callable here.';
  return JSON.stringify(card, null, 2) + '\n';
}

function main() {
  const check = process.argv.includes('--check');
  const before = fs.readFileSync(CARD, 'utf8');
  const after = render(before, routeToolNames());
  if (before === after) {
    console.log('agent_card.json mcp_tools: up to date');
    return 0;
  }
  if (check) {
    console.error('agent_card.json mcp_tools is STALE — run '
      + 'node app/scripts/render_agent_card_tools.js');
    return 1;
  }
  fs.writeFileSync(CARD, after);
  console.log(`agent_card.json mcp_tools: rewritten`);
  return 0;
}

if (require.main === module) process.exit(main());
module.exports = { routeToolNames, render, CARD };
