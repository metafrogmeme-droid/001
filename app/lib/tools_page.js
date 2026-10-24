'use strict';
/**
 * Two readers of the tools catalog.
 *
 * sendTools renders every card for the Recursion Breaker admin test.
 * loadCatalog / renderPage / sendPage are the operator Kink Cap card:
 * the link to /hrc is present for an operator and absent otherwise,
 * and "Coming soon" carries no date. That page is not under public/,
 * so express.static cannot serve it while the route is closed.
 */

const fs = require('node:fs');
const path = require('node:path');
const { optionalAuth } = require('../auth');
const operatorView = require('./operator_view');
const { loadCatalog: loadTools, present } = require('./tools_catalog');

function catalogFile() {
  return path.join(__dirname, '..', 'data', 'tools.json');
}

function templateFile() {
  return path.join(__dirname, '..', 'templates', 'tools.html');
}

function loadCatalog() {
  try {
    const payload = JSON.parse(fs.readFileSync(catalogFile(), 'utf8'));
    const tools = Array.isArray(payload) ? payload : (payload && payload.tools);
    if (!Array.isArray(tools)) return { readable: false, reason: 'unreadable' };
    const card = tools.find((tool) => tool && tool.id === 'kink-cap');
    if (!card) return { readable: false, reason: 'unreadable' };
    return { readable: true, tools: [card] };
  } catch (err) {
    return { readable: false, reason: 'unreadable' };
  }
}

function escapeText(value) {
  return String(value)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

function esc(value) {
  return String(value == null ? '' : value)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

function cardHtml(tool, operator) {
  const href = operator ? '/hrc' : null;
  const link = href
    ? '<p><a href="' + href + '">Open</a></p>'
    : '';
  const chains = Array.isArray(tool.chains) ? tool.chains.join(', ') : '';
  return '<article class="tool-card">'
    + '<h2>' + escapeText(tool.name || '') + '</h2>'
    + '<p class="tool-soon">Coming soon</p>'
    + '<p>' + escapeText(tool.description || '') + '</p>'
    + '<p class="tool-chains">' + escapeText(chains) + '</p>'
    + link
    + '</article>';
}

function renderPage(operator) {
  const catalog = loadCatalog();
  let cards = '<p>Unreadable.</p>';
  if (catalog.readable) {
    cards = catalog.tools.map((tool) => cardHtml(tool, operator)).join('');
  }
  const shell = fs.readFileSync(templateFile(), 'utf8');
  return shell
    .replace('<!--STYLE-->', '<link rel="stylesheet" href="/styles.css?v=53">')
    .replace('<!--CARDS-->', cards);
}

async function sendPage(req, res) {
  try {
    const { allow } = require('./hrc_flag');
    if (!(await allow(req, res))) return res.status(404).type('text/plain').send('Not found');
    res.setHeader('Cache-Control', 'no-cache');
    return res.type('html').send(renderPage(true));
  } catch (err) {
    const name = err && err.constructor ? err.constructor.name : 'Error';
    return res.status(500).type('text/plain').send(name);
  }
}

function render(cards) {
  const items = cards.map((card) => {
    const link = card.href
      ? `<p><a href="${esc(card.href)}">Admin view</a></p>`
      : '';
    return `<article class="panel">
      <h2>${esc(card.name)}</h2>
      <p>${esc(card.description)}</p>
      <p class="muted">${esc(card.chains.join(', '))}</p>
      <p><b>Coming soon</b></p>
      ${link}
    </article>`;
  }).join('\n');
  return `<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>RUNECLAW — Tools</title>
<link rel="canonical" href="https://www.humanoid-traders.com/tools">
<link rel="icon" type="image/svg+xml" href="/favicon.svg">
<link rel="stylesheet" href="/styles.css?v=53">
<style>
  .wrap { max-width: 720px; margin: 0 auto; padding: 32px 20px 64px; }
  h1 { font-family: var(--font-display); letter-spacing: .04em; }
  .panel { background: var(--surface); border: 1px solid var(--line); border-radius: 12px; padding: 20px; margin: 16px 0; }
  .muted { color: var(--text-2); }
</style>
</head>
<body>
<main class="wrap">
  <h1>Tools</h1>
  <p class="muted">What is not open yet stays unnamed.</p>
  ${items}
</main>
</body>
</html>`;
}

function sendTools(req, res, deps) {
  const auth = (deps && deps.optionalAuth) || optionalAuth;
  const ask = (deps && deps.isOperator) || operatorView.isOperator;
  auth(req, res, async () => {
    let admin = false;
    try {
      admin = await ask(req) === true;
    } catch (err) {
      admin = false;
    }
    res.setHeader('Cache-Control', 'no-store');
    let cards;
    try {
      cards = loadTools().map((card) => present(card, admin));
    } catch (err) {
      console.error('tools catalog failed');
      return res.status(500).type('text/plain').send('The tools list could not be read.');
    }
    return res.type('html').send(render(cards));
  });
}

module.exports = {
  catalogFile, loadCatalog, renderPage, sendPage, cardHtml,
  sendTools, render,
};
