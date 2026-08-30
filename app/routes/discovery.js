'use strict';
/**
 * The front door for agents.
 *
 * THE PROBLEM THIS SOLVES. RUNECLAW's agent surface is substantial — 33 MCP
 * tools, an ERC-8257 manifest, ERC-8004 identity cards, a paper Arena an agent
 * can trade in — and until this file existed there was no programmatic way to
 * FIND any of it. Probed as an outside agent would, on 2026-08-21:
 *
 *   /.well-known/mcp.json         404
 *   /.well-known/ai-plugin.json   404
 *   /.well-known/agent.json       404
 *   /api/tools                    404
 *   /developers                   200  — 11.8KB of HTML, for a human
 *
 * The ERC-8257 manifest is served at `/.well-known/ai-tool/:slug.json`, which
 * requires knowing the slug is `runeclaw-intel` before you can ask. The only
 * machine-readable index of the tools was a JSON-RPC POST to an endpoint you
 * had to know about already.
 *
 * So a human who lands on /developers can work it out, and an agent — the
 * stated audience — could not. A surface nothing can discover is close to a
 * surface that does not work, which is the same lesson as a module nothing
 * imports.
 *
 * WHAT IS AND IS NOT CLAIMED HERE. MCP has no ratified `.well-known`
 * discovery standard. This document does not pretend otherwise: it is
 * RUNECLAW's own index, served at the path clients most commonly probe, and it
 * says so in its own `note` field. Claiming conformance to a spec that does
 * not exist would be the documentation equivalent of a green tick over an
 * unread value.
 *
 * EVERY COUNT IS DERIVED. `tools.total`, `read_only` and `requires_key` are
 * computed from the live registry `/mcp` serves, never written down. This file
 * exists partly BECAUSE two hand-written counts had rotted — docs/INTEROP.md
 * said "17 read-only tools" when there were 30, and ci.yml's comment claimed
 * advisory numbers the gate itself contradicts. A discovery document is read
 * by machines that will act on it, so it is the last place a stale literal
 * belongs.
 */

const express = require('express');
const { rateLimit, ipKey } = require('../lib/rate_limit');
const t8257 = require('../lib/tool8257');

const router = express.Router();
const limited = rateLimit({ windowMs: 60_000, max: 60, key: ipKey, message: 'rate_limited' });

function mcp() {
  // Late require: mcp.js is standalone and requiring it at module load would
  // create a cycle, the same reason routes/tool8257.js defers its own.
  return require('./mcp');
}

/** Absolute origin for this request, so the document is usable as fetched. */
function origin(req) {
  const configured = String(process.env.APP_BASE_URL || '').trim().replace(/\/+$/, '');
  if (configured) return configured;
  // X-Forwarded-* is honoured by express when `trust proxy` is set; req.protocol
  // and req.get('host') already account for it there.
  return `${req.protocol}://${req.get('host')}`;
}

function toolCounts() {
  const m = mcp();
  const readOnly = Object.keys(m.TOOLS || {}).length;
  const requiresKey = Object.keys(m.WRITE_TOOLS || {}).length;
  return { total: readOnly + requiresKey, read_only: readOnly, requires_key: requiresKey };
}

/**
 * The Farcaster Mini App manifest.
 *
 * Sits beside the other discovery documents because it is the same kind of
 * thing: a file a client reads BEFORE it has any relationship with us, to
 * decide what we are. The difference is that this one carries a claim only the
 * Farcaster account owner can make — see lib/farcaster_manifest.js — so an
 * unsigned manifest omits `accountAssociation` entirely rather than shipping a
 * placeholder that would read as configured.
 *
 * 503 rather than a partial 200 when a REQUIRED field cannot be built. A
 * manifest missing `iconUrl` is not a smaller manifest; it is one Warpcast
 * rejects with an error the operator never sees, and answering 200 would put
 * that failure on the far side of a wall.
 */
router.get('/.well-known/farcaster.json', limited, (req, res) => {
  const fc = require('../lib/farcaster_manifest');
  const doc = fc.manifest(req);
  if (!doc) {
    const st = fc.status(req);
    return res.status(503).json({
      error: 'manifest_not_ready',
      not_ready_reasons: st.not_ready_reasons,
      problems: st.problems,
    });
  }
  res.set('Cache-Control', 'public, max-age=300');
  return res.json(doc);
});

/**
 * Whether the Mini App is publishable, and what a signed one still needs.
 *
 * Separate from the manifest because the manifest cannot say "I am unsigned" —
 * it says it by an ABSENT key, which is correct for the consumer and useless
 * for the operator. This is the surface that answers "why is Warpcast not
 * accepting it", in the same shape /api/tool/registration-plan already uses.
 */
router.get('/api/farcaster/status', limited, (req, res) => {
  res.json(require('../lib/farcaster_manifest').status(req));
});

router.get('/.well-known/mcp.json', limited, (req, res) => {
  const m = mcp();
  const base = origin(req);
  const counts = toolCounts();
  res.json({
    name: m.SERVER_INFO.name,
    version: m.SERVER_INFO.version,
    description:
      'RUNECLAW trading intelligence and agent-safety tools. Read-only tools '
      + 'serve data the public site already publishes, including a '
      + 're-derivable Proof-of-PnL track record. Arena tools trade PAPER '
      + 'positions with virtual money at real market prices.',
    mcp: {
      endpoint: `${base}/mcp`,
      transport: 'http',
      method: 'POST',
      protocol_version: m.PROTOCOL_VERSION,
      note: 'Stateless JSON-RPC 2.0 over a single POST. There is no SSE stream; '
        + 'GET and DELETE on /mcp return 405 by design.',
    },
    authentication: {
      read_only_tools: 'none — no key, no account, no signup',
      arena_tools:
        'Authorization: Bearer rcarena_… — mint one from a RUNECLAW account '
        + '(Arena → agent keys). The key reaches the paper Arena and nothing '
        + 'else: it cannot see an exchange account, move real funds, or place '
        + 'a live order.',
    },
    tools: counts,
    interop: {
      erc8257_manifest: `${base}/.well-known/ai-tool/${t8257.TOOL_SLUG}.json`,
      erc8257_index: `${base}/.well-known/ai-tool/index.json`,
      erc8257_invoke: `${base}/api/tool/invoke`,
      erc8004_agent_card: `${base}/agent/{address}`,
    },
    documentation: `${base}/developers`,
    note:
      'MCP has no ratified .well-known discovery standard. This document is '
      + "RUNECLAW's own index, served at the path clients commonly probe; its "
      + 'shape may change. The endpoints it points at are stable.',
  });
});

/**
 * The index the ERC-8257 manifest route needed and did not have.
 *
 * `/.well-known/ai-tool/:slug.json` 404s for any slug but one, and nothing
 * published which slug that is. Listing them completes the surface we already
 * implement rather than inventing a new one.
 */
router.get('/.well-known/ai-tool/index.json', limited, (req, res) => {
  const base = origin(req);
  res.json({
    tools: [
      {
        slug: t8257.TOOL_SLUG,
        manifest: `${base}/.well-known/ai-tool/${t8257.TOOL_SLUG}.json`,
        registry: t8257.TOOL_REGISTRY_ADDRESS || undefined,
      },
    ],
  });
});

module.exports = router;
