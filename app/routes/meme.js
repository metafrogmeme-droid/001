/**
 * POST /api/meme/swap/build — preflight a meme buy and return an UNSIGNED
 * transaction for the user to review and sign in their own wallet.
 *
 * THIS LAYER NEVER SIGNS AND NEVER HOLDS A KEY, and there is no code path here
 * that could: it forwards a public key to the bot gateway and relays back an
 * opaque base64 blob. The approval happens in Phantom/Backpack, on the user's
 * machine, against a key this process has never seen.
 *
 * A 200 IS NOT PERMISSION TO TRADE. The payload carries `build.signable`, and
 * it is False unless the operator has explicitly named mainnet. That is not a
 * timidity setting — Jupiter v6 quotes mainnet only, so the transaction is a
 * mainnet transaction under every configured mode, and `simulate` can only
 * honestly mean "review it, do not sign it". The browser fail-closes on the
 * flag being absent (see public/js/swap-sign-model.js); this route does not
 * re-derive it, because two independent derivations of a safety claim is how
 * they come to disagree.
 *
 * Private per-user surface, so dollar figures are allowed (§4).
 */

'use strict';

const express = require('express');
const { authMiddleware } = require('../auth');
const { rateLimit, userKey } = require('../lib/rate_limit');
const { resolveBotIdentity } = require('../lib/identity');
const gateway = require('../lib/gateway');

const router = express.Router();
router.use(authMiddleware);

// Base58, 32-44 chars — the same shape for a mint and for a wallet, because a
// Solana address is a Solana address. Checked here so a typo fails fast rather
// than becoming a transaction bound to an account nobody controls.
const SOL_ADDR_RE = /^[1-9A-HJ-NP-Za-km-z]{32,44}$/;

// A build costs an upstream quote AND a transaction build, so it is rate
// limited harder than a read.
const buildLimit = rateLimit({ windowMs: 60000, max: 8, key: userKey });

/**
 * Check a build request. `{ ok: true, value }` or `{ ok: false, error, detail }`.
 *
 * Pulled out of the handler so it can be DRIVEN rather than grepped: these are
 * the refusals that keep a typo from becoming a transaction bound to an account
 * nobody controls, and "the regex appears in the file" does not test that.
 *
 * Note `Number(b.size_usd)` rather than `parseFloat`: parseFloat('25abc') is 25,
 * and a size that was partly garbage should be refused, not silently truncated
 * to whatever prefix happened to parse.
 */
function validateBuildRequest(b) {
  const body = b || {};
  const mint = String(body.mint == null ? '' : body.mint).trim();
  if (!SOL_ADDR_RE.test(mint)) {
    return { ok: false, error: 'bad_mint',
      detail: 'Not a Solana mint (base58, 32-44 chars). Nothing was checked.' };
  }
  const wallet = String(body.user_public_key == null ? '' : body.user_public_key).trim();
  if (!SOL_ADDR_RE.test(wallet)) {
    return { ok: false, error: 'bad_wallet',
      detail: 'Connect a Solana wallet first — RUNECLAW signs nothing and '
        + 'needs the wallet that will.' };
  }
  const size = Number(body.size_usd);
  if (!Number.isFinite(size) || size <= 0) {
    return { ok: false, error: 'bad_size',
      detail: 'Size must be a positive number of USD.' };
  }
  return { ok: true, value: { mint: mint, user_public_key: wallet, size_usd: size } };
}

router.post('/swap/build', buildLimit, async (req, res) => {
  try {
    if (!gateway.isConfigured()) {
      return res.status(503).json({ error: 'Meme swap not configured' });
    }
    const check = validateBuildRequest(req.body);
    if (!check.ok) {
      return res.status(400).json({ error: check.error, detail: check.detail });
    }
    const { mint, user_public_key: wallet, size_usd: size } = check.value;

    const who = await resolveBotIdentity(req);
    const r = await gateway.postGateway('/meme/swap/build', {
      telegram_id: who.id,
      name: who.email || '',
      mint,
      user_public_key: wallet,
      size_usd: size,
    }, 25000);
    return gateway.relay(res, r);
  } catch (err) {
    // Loud in the log, honest to the caller. A failed build rendered as an
    // empty-but-successful response is the shape this repo spends most of its
    // guard tests preventing, and here it would read as "no route found" —
    // a market fact — when the truth is that we never asked.
    console.error('[meme] swap build failed:', (err && err.stack) || err);
    return res.status(503).json({
      error: 'build_unavailable',
      detail: 'Could not reach the planner — nothing was built or signed.',
    });
  }
});

module.exports = router;
module.exports.validateBuildRequest = validateBuildRequest;
