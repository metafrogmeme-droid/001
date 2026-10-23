'use strict';
/**
 * Claw Guard — check a transaction or a signature BEFORE signing it.
 *
 * POST /api/guard/check with one of:
 *   { request: 'tx', tx: { from?, to?, data, value? }, simulate?, chain_id? }
 *   { request: 'typed_data', typed_data, chain_id? }
 *   { request: 'authorization', authorization, chain_id? }
 *
 * It decodes what the request SAYS (txray-model.js, sigray-model.js), asks
 * Base what a transaction DOES when it names its sender (lib/tx_simulate.js),
 * and answers one plain-words verdict (guard-model.js). Nothing here signs,
 * sends or holds a key, and the request is never stored.
 *
 * A SIGNED-IN CALLER GETS A RECEIPT. The verdict is sealed (v10,
 * lib/scan_seal.js sealGuardIfSignedIn) against a sha256 of the exact input,
 * folded into the day's root, and opens at /call/<scan_key>: proof of what
 * the caller was told before signing, not that it was right. An anonymous
 * caller gets the same verdict and nothing is stored.
 *
 * WHERE THE INPUT GOES. A simulation sends the transaction and the sender's
 * address to Base's public RPC endpoints (the set lib/nft.js CHAINS holds),
 * which is what any wallet's own RPC sees before it signs. Nothing else
 * leaves this server.
 */

const express = require('express');
const { optionalAuth } = require('../auth');
const { rateLimit, ipKey } = require('../lib/rate_limit');
const TX = require('../public/js/txray-model.js');
const SR = require('../public/js/sigray-model.js');
const G = require('../public/js/guard-model.js');
const { simulate } = require('../lib/tx_simulate');
const { CHAINS } = require('../lib/evm_chains');

const router = express.Router();
router.use(optionalAuth);

/** The most input one check reads: a long multicall or a large typed-data batch fits. */
const MAX_INPUT = 100000;

function chainOf(body) {
  const raw = body.chain_id == null ? 8453 : Number(body.chain_id);
  return CHAINS[raw] ? raw : null;
}

/** The exact text the seal commits to: the caller's request, canonically. */
function inputText(request, body) {
  if (request === 'tx') return JSON.stringify(body.tx || {});
  const v = request === 'typed_data' ? body.typed_data : body.authorization;
  return typeof v === 'string' ? v : JSON.stringify(v == null ? null : v);
}

router.post('/check', rateLimit({ name: 'guard_check', windowMs: 60000, max: 20, key: ipKey }), async (req, res) => {
  try {
    const body = req.body && typeof req.body === 'object' ? req.body : {};
    const request = String(body.request || '');
    if (!['tx', 'typed_data', 'authorization'].includes(request)) {
      return res.status(400).json({ error: 'request must be tx, typed_data or authorization' });
    }
    const chainId = chainOf(body);
    if (chainId === null) return res.status(400).json({ error: 'Unsupported chain' });
    const input = inputText(request, body);
    if (input.length > MAX_INPUT) return res.status(413).json({ error: 'Request too large' });

    let decoded;
    let simulation = null;
    if (request === 'tx') {
      const tx = body.tx && typeof body.tx === 'object' ? body.tx : {};
      decoded = TX.decodeTx({ data: tx.data, value: tx.value == null ? '0' : String(tx.value) });
      // A simulation answers "what does this do to THIS account": it runs
      // only when the caller named the sender and the destination.
      if (body.simulate !== false && tx.from && tx.to) {
        simulation = await simulate(tx, { chainId });
      }
    } else if (request === 'typed_data') {
      decoded = SR.decodeTypedData(body.typed_data, { chainId });
    } else {
      decoded = SR.decodeAuthorization(body.authorization, { chainId });
    }
    const verdict = G.verdict(decoded, simulation);
    const userId = req.user && req.user.user_id;
    const rc = verdict.level === 'invalid'
      ? { sealed: false, reason: 'invalid' }
      : await require('../lib/scan_seal').sealGuardIfSignedIn({
        request, input, verdict, decoded, simulation: simulation && { chain_id: chainId, ...simulation }, userId });
    res.set('Cache-Control', 'no-store').json({
      request,
      chain_id: chainId,
      ...verdict,
      simulation: simulation === null ? null
        : simulation.ok ? { ran: true, status: simulation.status, block: simulation.block, other_events: simulation.other_events }
          : { ran: false, reason: simulation.reason },
      receipt: rc.sealed
        ? { scan_key: rc.scan_key, seal: rc.seal, sealed_at: rc.sealed_at, verify: '/call/' + rc.scan_key }
        : null,
      note: rc.sealed
        ? 'Your request was NOT stored; this verdict was sealed against a sha256 of it, so you can prove later what you were told before signing. The receipt proves what we SAID, not that it was right.'
        : 'Nothing was stored. A verdict is a reading with reasons, not a guarantee.',
    });
  } catch (err) {
    console.error('[guard] check failed:', err.constructor.name);
    res.status(500).json({ error: 'The check could not run' });
  }
});

module.exports = router;
