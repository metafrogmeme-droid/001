'use strict';

/**
 * Lookalike warning for a destination the caller is about to use.
 *
 * The match is Claw Guard's isLookalike: the first 4 and the last 4 hex
 * characters, and not the same address. This warns. It does not block,
 * it does not rewrite the destination, and it does not pick a replacement.
 */
const poison = require('../public/js/address-poison');

function firstMatch(dest, rows) {
  const list = Array.isArray(rows) ? rows : [];
  let compared = 0;
  let match = null;
  for (const raw of list) {
    const body = poison.canon(raw);
    if (!body) continue;
    compared += 1;
    if (match == null && poison.isLookalike(dest, body)) match = body;
  }
  return { match: match, compared: compared };
}

function destinationWarning(destination, counterparties, own) {
  const dest = poison.canon(destination);
  if (!dest) return { ok: false, error: 'destination' };
  const party = firstMatch(dest, counterparties);
  const mine = firstMatch(dest, own);
  let level = 'ok';
  let reason = null;
  if (party.match) {
    level = 'warn';
    reason = 'prefix_suffix_match';
  } else if (mine.match) {
    level = 'warn';
    reason = 'own_prefix_suffix_match';
  }
  return {
    ok: true,
    result: {
      check: 'address_poisoning',
      level: level,
      reason: reason,
      lookalike_of: party.match ? '0x' + party.match : null,
      lookalike_of_own: mine.match ? '0x' + mine.match : null,
      compared: party.compared,
      own_compared: mine.compared,
    },
  };
}

module.exports = { destinationWarning };
