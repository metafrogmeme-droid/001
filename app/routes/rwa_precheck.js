'use strict';
/**
 * POST /v1/rwa/precheck and GET /v1/rwa/chains.
 *
 * A read-only check of one tokenized-asset contract. Nothing is signed and
 * no key is held. The chain list is lib/rwa_chains.js. With
 * RWA_PRECHECK_ENABLED off, both routes are 404 — the same answer as a
 * path this server does not have.
 *
 * A signed-in caller gets a v11 receipt (lib/scan_seal.js sealRwaIfSignedIn).
 * The request address is hashed, not stored. An anonymous caller gets the
 * same reading and nothing is stored. Failing to seal does not fail the check.
 */

const express = require('express');
const { optionalAuth } = require('../auth');
const { rateLimit, ipKey } = require('../lib/rate_limit');
const { flag_on_for } = require('../lib/admin_preview');
const { CHAINS, publicChain } = require('../lib/rwa_chains');
const { precheck, canonicalInput, project, DISCLAIMER } = require('../lib/rwa_precheck');

const router = express.Router();
router.use(optionalAuth);

router.use(async (req, res, next) => {
  try {
    if (!(await flag_on_for(req, 'RWA_PRECHECK_ENABLED'))) {
      return res.status(404).json({ error: 'Not found' });
    }
    return next();
  } catch (e) {
    return res.status(500).json({ error: 'Not found' });
  }
});

router.get('/chains', (req, res) => {
  res.set('Cache-Control', 'no-store').json({
    chains: CHAINS.map((c) => publicChain(c, process.env)),
    disclaimer: DISCLAIMER,
  });
});

const REFUSAL = {
  unsupported_chain: 'That chain is not in this pre-check.',
  invalid_asset: 'The asset must be an EVM address.',
  invalid_account: 'The account must be an EVM address, or left blank.',
};

router.post('/precheck', rateLimit({ name: 'rwa_precheck', windowMs: 60000, max: 20, key: ipKey }), async (req, res) => {
  try {
    const body = req.body && typeof req.body === 'object' ? req.body : {};
    const reading = await precheck({
      chain: body.chain,
      asset: body.asset,
      account: body.account,
      env: process.env,
    });
    if (reading.read === false && REFUSAL[reading.reason]) {
      return res.status(400).json({ error: REFUSAL[reading.reason], disclaimer: DISCLAIMER });
    }
    const userId = req.user && req.user.user_id;
    const rc = await require('../lib/scan_seal').sealRwaIfSignedIn({
      input: canonicalInput(
        reading.chain ? reading.chain.id : '',
        reading.asset || '',
        reading.account || null,
      ),
      projection: project(reading),
      userId,
    });
    res.set('Cache-Control', 'no-store').json({
      ...reading,
      receipt: rc.sealed
        ? { scan_key: rc.scan_key, seal: rc.seal, sealed_at: rc.sealed_at, verify: '/call/' + rc.scan_key }
        : null,
      note: rc.sealed
        ? 'The asset address was not stored. This receipt seals a sha256 of it and the flags above. It proves what this pre-check said, not that the asset is sound.'
        : 'Nothing was stored. This is an automated pre-check, not an audit.',
    });
  } catch (err) {
    console.error('[rwa] precheck failed:', err && err.constructor ? err.constructor.name : 'Error');
    res.status(500).json({ error: 'The check could not run' });
  }
});

module.exports = router;
