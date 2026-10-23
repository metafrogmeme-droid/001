'use strict';
/**
 * The public tools card. Coming soon, no date, no issuer, no score.
 * An admin gets a link to the admin view. The flag does not publish it.
 */

const fs = require('node:fs');
const path = require('node:path');

const LABELS = { ethereum: 'Ethereum', base: 'Base' };

function loadCatalog() {
  const raw = JSON.parse(fs.readFileSync(path.join(__dirname, '..', 'data', 'tools.json'), 'utf8'));
  if (!Array.isArray(raw) || raw.length === 0) {
    throw new Error('tools catalog is empty');
  }
  for (const card of raw) {
    if (card.status !== 'runtime') throw new Error('tools status');
    if (!Array.isArray(card.chains) || card.chains.length === 0) throw new Error('tools chains');
    for (const chain of card.chains) {
      if (!LABELS[chain]) throw new Error('UnknownChain');
    }
  }
  return raw;
}

function present(card, admin) {
  return {
    id: card.id,
    name: card.name,
    description: card.description,
    chains: card.chains.map((chain) => LABELS[chain]),
    status_label: 'Coming soon',
    href: admin === true ? card.route : null,
    date: null,
  };
}

module.exports = { loadCatalog, present, LABELS };
