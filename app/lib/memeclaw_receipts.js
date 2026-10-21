'use strict';
/**
 * The receipt of a Memeclaw launch (Provable Calls v8, docs/PROVABLE_CALLS_SPEC.md
 * §2.7), addressed `launch:<seal>` and served by GET /api/call/:key.
 *
 * A launch is public on the chain, so the receipt is open to anyone: the
 * sealed payload, the day's Merkle proof, and the coin's row as it is now,
 * which the receipt page compares field by field with what was sealed.
 */
const { anchorFor } = require('./seal_roots');
const { chainInfo } = require('./root_anchor');
const store = require('./memeclaw_store');
const mchain = require('./memeclaw_chain');

const KEY = /^launch:([0-9a-f]{64})$/;

async function anchorOf(seal, sealedAt) {
  try { return await anchorFor(seal, sealedAt); } catch (e) { return null; }
}

/** { code, body } for a `launch:` key, or null when the key is not one. */
async function lookupLaunch(key) {
  const m = KEY.exec(String(key || ''));
  if (!m) return null;
  const row = await store.coinBySeal(m[1]);
  if (!row || !row.seal) {
    return { code: 404, body: { error: 'No sealed Memeclaw launch with that seal. A launch is sealed once this site has read it from the chain, and only when its terms were planned on the page.' } };
  }
  const chain = chainInfo(mchain.CHAIN_KEY);
  return { code: 200, body: {
    kind: 'memeclaw_launch',
    seal: row.seal,
    seal_payload: row.seal_payload,
    sealed_at: row.sealed_at,
    anchor: await anchorOf(row.seal, row.sealed_at),
    // The row as it is now, under the names the payload uses, for the drift check.
    current: { token: row.token, creator: row.creator, name: row.name, symbol: row.symbol,
      description: row.description || '', terms_hash: row.terms_hash, tx: row.tx_hash },
    explorer_tx: chain ? chain.explorer_tx : null,
    proves: 'the terms this coin launched under, as the page wrote them before the wallet was asked: their SHA-256 is the terms hash the factory recorded in its Launched event, at the block the chain dated it.',
    does_not_prove: 'anything about the coin since its launch, or that it is worth anything. It is a testnet coin.',
    verify: 'sha256 over the UTF-8 bytes of seal_payload, verbatim, is `seal`; the terms hash is in the launch transaction\'s Launched event on the DogeOS testnet explorer.',
  } };
}

module.exports = { lookupLaunch, KEY };
