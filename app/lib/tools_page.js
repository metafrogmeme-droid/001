'use strict';
/**
 * The operator tools catalog. One card. The page is not under public/,
 * so express.static cannot serve it while the route is closed.
 *
 * The link to /hrc is present for an operator and absent otherwise.
 * "Coming soon" carries no date.
 */

const fs = require('node:fs');
const path = require('node:path');

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
    .replace('<!--STYLE-->', '<link rel="stylesheet" href="/styles.css?v=52">')
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

module.exports = { catalogFile, loadCatalog, renderPage, sendPage, cardHtml };
