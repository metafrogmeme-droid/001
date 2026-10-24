'use strict';

const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

const catalog = require('../lib/tools_catalog');
const page = require('../public/js/tools-page');

const HTML = fs.readFileSync(path.join(__dirname, '..', 'public', 'tools.html'), 'utf8');

function panicChains() {
  const src = fs.readFileSync(path.join(__dirname, '..', '..', 'bot', 'approval_hygiene.py'), 'utf8');
  const slugs = { 1: 'ethereum', 8453: 'base', 43114: 'avalanche', 42161: 'arbitrum' };
  const body = src.slice(src.indexOf('CHAINS'), src.indexOf('STALE_AFTER'));
  return [...body.matchAll(/\((\d+),/g)].map((m) => {
    const id = Number(m[1]);
    if (!Object.prototype.hasOwnProperty.call(slugs, id)) {
      const err = new Error('UnknownChain');
      err.name = 'UnknownChain';
      throw err;
    }
    return slugs[id];
  });
}

function walletKeys() {
  const src = fs.readFileSync(path.join(__dirname, '..', 'lib', 'wallet.js'), 'utf8');
  const start = src.indexOf('const CHAINS = [');
  const block = src.slice(start, src.indexOf('];', start));
  return [...block.matchAll(/key:\s*'([a-z0-9-]+)'/g)].map((m) => m[1]);
}

function card(id, chains) {
  return {
    getAttribute(name) {
      if (name === 'data-tool') return id;
      if (name === 'data-chains') return chains.join(' ');
      return null;
    },
  };
}

test('the catalog chains are the modules they name', () => {
  const tools = catalog.loadCatalog();
  const byId = Object.fromEntries(tools.map((t) => [t.id, t.chains]));
  assert.deepStrictEqual(byId['sar-oracle'], require('../lib/sar/chains').COVERS);
  assert.deepStrictEqual(byId['panic-button'], panicChains());
  assert.deepStrictEqual(byId['rwa-precheck'], require('../lib/rwa_chains').CHAINS.map((c) => c.id));
  assert.deepStrictEqual(byId['risk-scanner'], Object.keys(require('../lib/lz_verifiers').chains()));
  const evm = require('../lib/evm_chains').CHAINS;
  assert.deepStrictEqual(byId['claw-guard'], ['base', 'base-sepolia']);
  assert.equal(evm[8453].name, 'Base');
  assert.equal(evm[84532].name, 'Base Sepolia');
  const allowances = fs.readFileSync(path.join(__dirname, '..', 'routes', 'allowances.js'), 'utf8');
  assert.match(allowances, /chain === 'solana'/);
  assert.deepStrictEqual(byId['allowance-xray'], walletKeys().concat(['solana']));
  for (const tool of tools) assert.equal(tool.status, 'runtime');
});

test('the SaR card stays coming soon with the flag on or off', () => {
  const sar = catalog.loadCatalog().find((t) => t.id === 'sar-oracle');
  for (const env of [{}, { SAR_ORACLE_ENABLED: '1' }, { SAR_ORACLE_ENABLED: 'true' }]) {
    const view = catalog.presentation(sar, env);
    assert.deepStrictEqual(view, { label: 'Coming soon', href: null });
  }
  const panic = catalog.loadCatalog().find((t) => t.id === 'panic-button');
  assert.deepStrictEqual(catalog.presentation(panic, {}), { label: 'No web page', href: null });
});

test('the restitution card is coming soon with no date until its flag is on', () => {
  const tool = catalog.loadCatalog().find((t) => t.id === 'srb');
  assert.equal(tool.chains.includes('solana'), true);
  assert.deepStrictEqual(catalog.presentation(tool, {}), { label: 'Coming soon', href: null });
  assert.deepStrictEqual(
    catalog.presentation(tool, { SRB_ENABLED: '1' }),
    { label: 'Open', href: '/srb' },
  );
  const rendered = catalog.renderFile({});
  const at = rendered.indexOf('data-tool="srb"');
  const start = rendered.lastIndexOf('<', at);
  const end = rendered.indexOf('</article>', at);
  const cardHtml = rendered.slice(start, end);
  assert.equal(cardHtml.startsWith('<article'), true);
  assert.match(cardHtml, /Coming soon/);
  assert.equal(cardHtml.includes('href='), false);
  assert.equal(/\b20\d{2}\b/.test(cardHtml), false);
});

test('chips follow the bar, then extras from the modules that name them', () => {
  const tools = catalog.loadCatalog();
  const { bar, labels } = catalog.labelMap(tools);
  assert.deepStrictEqual(bar.slice(0, 10), [
    'all', 'ethereum', 'base', 'arbitrum', 'avalanche', 'bnb',
    'solana', 'hyperliquid', 'zksync', 'polygon',
  ]);
  assert.deepStrictEqual(bar.slice(10), ['base-sepolia', 'optimism']);
  assert.equal(labels.optimism, require('../lib/lz_verifiers').chains().optimism.name);
  assert.equal(labels['base-sepolia'], require('../lib/evm_chains').CHAINS[84532].name);
  assert.throws(() => catalog.labelMap([{ chains: ['not-a-chain'] }]), (err) => err.name === 'UnknownChain');
});

test('the filter keeps the cards the chain names', () => {
  const cards = [
    card('sar-oracle', ['hyperliquid', 'base', 'solana']),
    card('panic-button', ['ethereum', 'base']),
  ];
  assert.deepStrictEqual([...page.visibleIds(cards, 'all')], ['sar-oracle', 'panic-button']);
  assert.deepStrictEqual([...page.visibleIds(cards, 'hyperliquid')], ['sar-oracle']);
  assert.deepStrictEqual([...page.visibleIds(cards, 'ethereum')], ['panic-button']);
  assert.equal(page.visibleIds(cards, 'polygon').size, 0);
});

test('the page carries no dollar key and no live SaR link', () => {
  assert.equal(HTML.includes('_usd_'), false);
  assert.equal(HTML.includes('$'), false);
  assert.match(HTML, /Public signals, not advice\. Written with AI help\./);
  assert.match(HTML, /No tool on this chain is in the catalog\./);
  assert.equal(HTML.includes('href="/tools"'), false);
  for (const env of [{ SAR_ORACLE_ENABLED: '1' }, {}]) {
    const rendered = catalog.renderFile(env);
    assert.equal(rendered.includes('_usd_'), false);
    assert.equal(rendered.includes('$'), false);
    const at = rendered.indexOf('data-tool="sar-oracle"');
    const cardHtml = rendered.slice(at - 40, rendered.indexOf('data-tool="panic-button"'));
    assert.match(cardHtml, /<article /);
    assert.equal(cardHtml.includes('href='), false);
    assert.match(cardHtml, /Coming soon/);
    assert.equal(cardHtml.includes('Live'), false);
  }
});
