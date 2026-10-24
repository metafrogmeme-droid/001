'use strict';
/**
 * POST /api/guard/poison — the v0.1 lookalike warning.
 *
 * Mounted at /api/guard/poison, beside POST /api/guard/check, and only
 * answering when CLAW_GUARD_V01 is on. With the flag off the answer is 404,
 * the same as a path this server does not have.
 *
 * The body is the destination, the caller's own counterparty list, and
 * optionally the inbound transfers they already have. A public log read
 * runs only when they ask for one. Nothing is written: no seal, no row,
 * no log line that carries an address. The destination comes back only as
 * they sent it, inside the warning text the page builds; this response
 * does not contain a replacement address.
 *
 * Chains are lib/evm_chains.js, the same set the guard simulates on.
 */

const express = require('express');
const { optionalAuth } = require('../auth');
const { rateLimit, ipKey } = require('../lib/rate_limit');
const { CHAINS } = require('../lib/evm_chains');
const { flag_on_for } = require('../lib/admin_preview');
const poison = require('../public/js/address-poison.js');
const { readErc20Inbound } = require('../lib/poison_logs');

const router = express.Router();
router.use(optionalAuth);

const MAX_LIST = 200;

function chainOf(body) {
  const raw = body.chain_id == null ? 8453 : Number(body.chain_id);
  return CHAINS[raw] ? raw : null;
}

router.post('/', rateLimit({ name: 'guard_v01_poison', windowMs: 60000, max: 20, key: ipKey }), async (req, res) => {
  if (!(await flag_on_for(req, 'CLAW_GUARD_V01'))) return res.status(404).json({ error: 'Not found' });
  try {
    const body = req.body && typeof req.body === 'object' ? req.body : {};
    const chainId = chainOf(body);
    if (chainId === null) return res.status(400).json({ error: 'Unsupported chain' });
    const counterparties = Array.isArray(body.counterparties) ? body.counterparties : [];
    const inbound = Array.isArray(body.inbound) ? body.inbound : null;
    if (counterparties.length > MAX_LIST || (inbound && inbound.length > MAX_LIST)) {
      return res.status(400).json({ error: 'Too many addresses' });
    }
    const local = poison.checkAddressPoisoning({
      destination: body.destination,
      counterparties: counterparties,
      inbound: inbound,
    });
    if (!local.ok) return res.status(400).json({ error: 'destination must be an EVM address' });

    let merged = local;
    if (body.read_history === true) {
      const account = poison.canon(typeof body.account === 'string' ? body.account : '');
      const got = account
        ? await readErc20Inbound({ chainId: chainId, account: '0x' + account, destination: '0x' + poison.canon(body.destination) })
        : { read: false, reason: 'invalid' };
      if (!got.read) {
        merged = poison.mergePoison(local, { read: false });
      } else {
        const fromLogs = poison.checkAddressPoisoning({
          destination: body.destination,
          counterparties: [],
          inbound: got.inbound,
        });
        merged = poison.mergePoison(local, fromLogs);
      }
    }
    const result = merged.result;
    result.chain_id = chainId;
    res.set('Cache-Control', 'no-store').json(result);
  } catch (err) {
    console.error('[guard_v01] poison failed:', err && err.constructor ? err.constructor.name : 'Error');
    res.status(500).json({ error: 'The check could not run' });
  }
});

module.exports = router;
