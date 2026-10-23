'use strict';

const { optionalAuth } = require('../auth');
const operatorView = require('./operator_view');
const { loadCatalog, present } = require('./tools_catalog');

function esc(value) {
  return String(value == null ? '' : value)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
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
<link rel="stylesheet" href="/styles.css?v=52">
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
      cards = loadCatalog().map((card) => present(card, admin));
    } catch (err) {
      console.error('tools catalog failed');
      return res.status(500).type('text/plain').send('The tools list could not be read.');
    }
    return res.type('html').send(render(cards));
  });
}

module.exports = { sendTools, render };
