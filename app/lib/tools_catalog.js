'use strict';
/**
 * The /tools cards. One file, app/data/tools.json, names every tool.
 * Whether a card is a link is decided here, from the flag that tool
 * names and from whether it has a route. The status field in the file
 * is the literal "runtime". It is not a verdict, and it is not read
 * when the card is drawn.
 *
 * A flag that is off is "Coming soon": no link and no date. A tool
 * with no route is "No web page": no link. Neither is described as live.
 */

const fs = require('node:fs');
const path = require('node:path');

const FILE = path.join(__dirname, '..', 'data', 'tools.json');
const PAGE = path.join(__dirname, '..', 'public', 'tools.html');

const BAR = Object.freeze([
  'all', 'ethereum', 'base', 'arbitrum', 'avalanche', 'bnb',
  'solana', 'hyperliquid', 'zksync', 'polygon',
]);

const FIXED = Object.freeze({
  all: 'All',
  ethereum: 'Ethereum',
  base: 'Base',
  arbitrum: 'Arbitrum',
  avalanche: 'Avalanche',
  bnb: 'BNB Chain',
  solana: 'Solana',
  hyperliquid: 'Hyperliquid',
  zksync: 'ZKsync Era',
  polygon: 'Polygon',
});

function fail(name) {
  const err = new Error(name);
  err.name = name;
  return err;
}

function loadCatalog() {
  let tools;
  try {
    tools = JSON.parse(fs.readFileSync(FILE, 'utf8'));
  } catch (err) {
    const wrapped = fail('CatalogUnreadable');
    wrapped.causeName = err && err.name ? err.name : 'Error';
    throw wrapped;
  }
  if (!Array.isArray(tools) || tools.length === 0) throw fail('CatalogUnreadable');
  for (const tool of tools) {
    if (!tool || tool.status !== 'runtime') throw fail('CatalogStatus');
    if (!Array.isArray(tool.chains) || tool.chains.length === 0) throw fail('CatalogUnreadable');
  }
  return tools;
}

/** The flag the tool names, read from that flag's own module. An unknown flag is off. */
function flagOn(name, env) {
  if (name === 'SAR_ORACLE_ENABLED') return require('./sar_oracle_flag').flag_on(env);
  if (name === 'RWA_PRECHECK_ENABLED') return require('./rwa_flag').enabled(env);
  if (name === 'HRC_ENABLED') return require('./hrc_flag').flag_on(env);
  if (name === 'SRB_ENABLED') return require('./srb_flag').flag_on(env);
  return false;
}

/**
 * Labels for chains the bar did not name in advance. Taken from the
 * module that owns the chain, so a renamed chain renames the chip.
 * An id with no owner throws. It is not given a guessed label.
 */
function extraLabels() {
  const lz = require('./lz_verifiers').chains();
  const evm = require('./evm_chains').CHAINS;
  const out = {};
  if (lz.optimism && lz.optimism.name) out.optimism = lz.optimism.name;
  if (evm[84532] && evm[84532].name) out['base-sepolia'] = evm[84532].name;
  return out;
}

function labelMap(tools) {
  const extras = extraLabels();
  const seen = new Set();
  for (const tool of tools) {
    for (const id of tool.chains) seen.add(id);
  }
  const extraIds = [...seen].filter((id) => BAR.indexOf(id) === -1).sort();
  const bar = BAR.concat(extraIds);
  const labels = {};
  for (const id of bar) {
    if (FIXED[id]) labels[id] = FIXED[id];
    else if (extras[id]) labels[id] = extras[id];
    else throw fail('UnknownChain');
  }
  for (const id of seen) {
    if (!labels[id]) throw fail('UnknownChain');
  }
  return { bar, labels };
}

function presentation(tool, env, view) {
  const src = env || process.env;
  // No public SaR dashboard. The operator, and only the operator, gets a
  // link to the admin feed once the flag is on. An admin preview passes
  // view.preview while the public flag is still off. Everyone else stays
  // "Coming soon", including an operator while the flag is off.
  if (tool && tool.id === 'sar-oracle') {
    const admin = view && view.operator === true;
    const preview = view && view.preview === true;
    if (admin && (flagOn('SAR_ORACLE_ENABLED', src) || preview)) {
      return { label: 'Admin view', href: '/api/sar-oracle/admin/packets' };
    }
    return { label: 'Coming soon', href: null };
  }
  const named = typeof tool.flag === 'string' && tool.flag.length > 0;
  if (named && !flagOn(tool.flag, src)) {
    return { label: 'Coming soon', href: null };
  }
  if (typeof tool.route !== 'string' || tool.route.length === 0) {
    return { label: 'No web page', href: null };
  }
  return { label: 'Open', href: tool.route };
}

function esc(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  }[c]));
}

function renderBar(tools) {
  const { bar, labels } = labelMap(tools);
  return bar.map((id) => {
    const pressed = id === 'all' ? 'true' : 'false';
    return `<button type="button" role="tab" data-chain="${esc(id)}" aria-pressed="${pressed}">${esc(labels[id])}</button>`;
  }).join('');
}

function renderCards(tools, env, view) {
  const { labels } = labelMap(tools);
  return tools.map((tool) => {
    const card = presentation(tool, env, view);
    const chips = tool.chains.map((id) => `<li>${esc(labels[id])}</li>`).join('');
    const attrs = `class="tool-card" data-tool="${esc(tool.id)}" data-chains="${esc(tool.chains.join(' '))}"`;
    const body = `<h2>${esc(tool.name)}</h2><p>${esc(tool.description)}</p><ul class="chain-chips">${chips}</ul><p class="tool-status">${esc(card.label)}</p>`;
    if (!card.href) return `<article ${attrs}>${body}</article>`;
    return `<a ${attrs} href="${esc(card.href)}">${body}</a>`;
  }).join('\n');
}

function renderFile(env, view) {
  const tools = loadCatalog();
  const html = fs.readFileSync(PAGE, 'utf8');
  if (!html.includes('<!--CHAIN_BAR-->') || !html.includes('<!--TOOL_CARDS-->')) {
    throw fail('CatalogUnreadable');
  }
  return html
    .replace('<!--CHAIN_BAR-->', renderBar(tools))
    .replace('<!--TOOL_CARDS-->', renderCards(tools, env, view));
}

module.exports = {
  FILE, BAR, FIXED, loadCatalog, flagOn, extraLabels, labelMap,
  presentation, renderBar, renderCards, renderFile,
};
