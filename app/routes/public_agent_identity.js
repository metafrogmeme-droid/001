'use strict';
/**
 * PUBLIC agent identity — GET /api/public/agent-identity/:slug.
 *
 * The claim itself, plus everything a stranger needs to check it without
 * trusting this server:
 *
 *   sha256(utf8(seal_payload)) === seal        the claim is intact
 *   seal ∈ day D's committed leaf set          via the Merkle proof returned here
 *   D's root is calldata in a Base tx          anchor_tx, if the day is anchored
 *   → the claim existed by that block's time
 *
 * §4-safe by construction: a claim is a name, a timestamp and a hash. There is
 * no amount on this surface and no user identity — `lib/agents.bySlug` never
 * selects the owner column.
 *
 * The chain walk itself is lib/agent_chain.js, where its branches can be
 * driven; this file is the HTTP shell around it.
 */

const express = require('express');
const { rateLimit, ipKey } = require('../lib/rate_limit');
const agents = require('../lib/agents');
const { chainFor, SETTLED } = require('../lib/agent_chain');

const router = express.Router();
router.use(rateLimit({ windowMs: 60000, max: 30, key: ipKey }));

const CACHE_MS = 60 * 1000;
const cache = new Map();   // slug -> { at, data }

/**
 * GET /api/public/agent-identity — every claimed agent, newest first.
 *
 * The index `/a/:slug` shipped without. A page reachable only by already
 * knowing its URL is present-but-not-reached, and from the outside that is
 * indistinguishable from broken.
 *
 * `count` is sent beside `agents` deliberately: an empty list is a MEASUREMENT
 * — nobody has claimed an identity yet — and the page has to be able to say
 * that rather than render blank, which reads as a failed load.
 */
router.get('/', async (req, res) => {
  try {
    const agentList = await agents.listClaimed(100);
    res.json({ agents: agentList, count: agentList.length });
  } catch (err) {
    console.error('Agent index error:', err.stack || err.message);
    // 503, never `{agents: []}`. An unreadable table rendered as an empty
    // directory is the exact defect this whole surface is built against.
    res.status(503).json({ error: 'The agent directory could not be read' });
  }
});

router.get('/:slug', async (req, res) => {
  const slug = String(req.params.slug || '').toLowerCase();
  try {
    const now = Date.now();
    const hit = cache.get(slug);
    if (hit && now - hit.at < CACHE_MS) return res.json(hit.data);

    const agent = await agents.bySlug(slug);
    if (!agent) return res.status(404).json({ error: 'No such agent' });

    const data = {
      slug: agent.slug,
      display_name: agent.display_name,
      claimed_at: agent.claimed_at,
      seal: agent.seal,
      seal_payload: agent.seal_payload,
      verify: {
        seal_algorithm: 'sha256 over the UTF-8 bytes of seal_payload, verbatim',
        chain: await chainFor(agent),
      },
      // Stated rather than implied: the claim proves a NAME was registered on a
      // date. It does not prove who operates the agent — that needs a signature
      // from a key the agent controls, which this version does not carry.
      proves: 'this slug was claimed on this date, and the record has not been altered since',
      does_not_prove: 'who operates this agent — no key signature is bound to this claim yet',
    };
    // Cached only when the chain is a settled answer. Caching `unknown` would
    // freeze a transient roots-table failure into a minute of stated fact.
    if (SETTLED.has(data.verify.chain.status)) cache.set(slug, { at: now, data });
    res.json(data);
  } catch (err) {
    console.error('Agent identity error:', err.stack || err.message);
    res.status(503).json({ error: 'The agent identity could not be read' });
  }
});

module.exports = router;
