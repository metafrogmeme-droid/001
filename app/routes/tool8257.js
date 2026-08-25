'use strict';
/**
 * ERC-8257 tool surface:
 *   GET  /.well-known/ai-tool/runeclaw-intel.json  — the canonical manifest
 *   POST /api/tool/invoke                          — the tool endpoint
 *   GET  /api/tool/registration-plan               — operator dry-run plan
 *
 * The invoke endpoint is a thin dispatcher over the SAME read-only TOOLS
 * registry the /mcp server exposes — one source of truth, so what the
 * on-chain manifest advertises is exactly what /mcp serves. Public data
 * only; nothing here can see an account or place an order.
 */

const express = require('express');
const { rateLimit, ipKey } = require('../lib/rate_limit');
const t8257 = require('../lib/tool8257');
const chain = require('../lib/tool8257_chain');

const { safeErrorText } = require('../lib/safe_error');

const router = express.Router();
const limited = rateLimit({ windowMs: 60_000, max: 60, key: ipKey, message: 'rate_limited' });

function mcpTools() {
  // Late require avoids a require-cycle at module load (mcp.js is standalone).
  return require('./mcp').TOOLS;
}

router.get('/.well-known/ai-tool/:slug.json', limited, (req, res) => {
  if (req.params.slug !== t8257.TOOL_SLUG) {
    return res.status(404).json({ error: 'unknown tool' });
  }
  const manifest = t8257.buildManifest({ tools: mcpTools() });
  res.json(manifest);
});

router.get('/api/tool/registration-plan', limited, (req, res) => {
  res.json(t8257.buildRegistrationPlan({ tools: mcpTools() }));
});

/**
 * GET /api/tool/registration — is the registration REAL? Ask the chain.
 *
 * `registrationCheck` compares our computed manifest hash against
 * REGISTERED_MANIFEST_HASH, an environment variable we set ourselves. Both
 * sides of that comparison are ours: it proves the operator typed the hash
 * they meant to, and nothing about whether a transaction was ever sent.
 *
 * This is the public self-audit — the sibling of /api/roots/verify/:day, and
 * deliberately the same shape: anyone can ask the server to re-check its own
 * claim, and the server reports exactly what the chain said, `unknown`
 * included. Nothing here is cached, because a cached `unknown` freezes a
 * transient RPC failure into a fact.
 */
router.get('/api/tool/registration', limited, async (req, res) => {
  try {
    const plan = t8257.buildRegistrationPlan({ tools: mcpTools() });
    const tx = chain.registeredTx();
    const chain_id = chain.registeredChainId();

    // No transaction recorded is its own answer, and NOT a failure. It is the
    // honest state of a registration that has been planned and not yet sent —
    // which is where this has sat since the plan was written.
    if (!tx) {
      return res.json({
        status: 'not_submitted',
        detail: 'No registration transaction is recorded. The plan is ready; nothing has been sent.',
        manifest_hash: plan.manifest_hash || null,
        registry: plan.registry,
        recommended_chain_id: plan.recommended_chain_id,
        hash_check: plan.registration_check || null,
      });
    }

    const v = await chain.verifyRegistration(
      tx, { registry: plan.registry, calldata: plan.calldata }, chain_id);

    res.json({
      status: v.status,           // verified | mismatch | unknown
      tx, chain_id,
      registry: plan.registry,
      manifest_hash: plan.manifest_hash || null,
      block_time: v.block_time || null,
      from: v.from || null,
      reason: v.reason || null,
      hash_check: plan.registration_check || null,
    });
  } catch (err) {
    console.error('Tool registration status error:', err.stack || err.message);
    // 503, not a verdict shaped like one: an error here means we could not
    // ask, and answering "not registered" would be a claim from no evidence.
    res.status(503).json({ error: 'registration_status_unavailable' });
  }
});

router.post('/api/tool/invoke', limited, express.json({ limit: '64kb' }), async (req, res) => {
  const body = req.body || {};
  const name = String(body.tool || '');
  const TOOLS = mcpTools();
  const tool = TOOLS[name];
  if (!tool) {
    return res.status(400).json({
      error: `Unknown tool: ${name || '(missing)'}`,
      tools: Object.keys(TOOLS),
    });
  }
  const argErr = require('./mcp').validateArgs(tool.inputSchema, body.args || {});
  if (argErr) return res.status(400).json({ error: argErr });
  try {
    const result = await tool.handler(body.args || {});
    res.json({ tool: name, result });
  } catch (e) {
    // F-15: this endpoint is PUBLIC and unauthenticated, and these handlers
    // do pool.execute and getGateway calls — a database error carries
    // connection and schema detail, a gateway error carries the internal URL
    // it tried. Truncating to 200 chars bounded the SIZE of a leak, not
    // whether one happened. Scrubbed rather than blanked: tools throw real
    // validation errors ("text is required") a caller needs to see.
    console.error('Tool invoke error:', name, e.stack || e.message);
    res.status(502).json({
      tool: name,
      error: `Tool failed: ${safeErrorText(e)}`,
    });
  }
});

module.exports = router;
