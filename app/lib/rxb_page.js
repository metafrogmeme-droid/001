'use strict';

const { optionalAuth } = require('../auth');
const { readPage } = require('./rxb_read');
const { access } = require('./rxb_access');

function esc(value) {
  return String(value == null ? '' : value)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

function coverageLine(back) {
  const v2 = back && back.v2;
  if (!v2 || v2.readable !== true) return '';
  const parts = [];
  if (v2.fired_on && v2.lead_days != null && v2.disclosure) {
    parts.push(`Coverage fired on ${v2.fired_on}, ${v2.lead_days} days before ${v2.disclosure}.`);
  }
  const covered = v2.usd3;
  if (covered && covered.covered === true) {
    const start = covered.history_starts || 'unreadable';
    const outcome = covered.fired_on == null ? 'did not fire' : `fired on ${covered.fired_on}`;
    parts.push(`USD3 history starts ${start} and ${outcome}.`);
  }
  if (v2.euler && v2.euler.readable === false) parts.push('Euler v2 was unreadable.');
  return parts.length ? ` ${parts.join(' ')}` : '';
}

function cell(value) {
  if (value === null || value === undefined) return 'unreadable';
  return String(value);
}

function render(model, audience) {
  const back = model && model.backtest ? model.backtest : {};
  const nodes = model && Array.isArray(model.nodes) ? model.nodes : [];
  const flag = model && (model.flag_on === true || model.flag_on === false)
    ? (model.flag_on ? 'on' : 'off')
    : 'unreadable';
  const publicView = audience !== 'admin_test';
  const doc = !publicView && model && model.onboarding && model.onboarding.readable === true
    ? model.onboarding.text
    : '';
  const rows = nodes.map((row) => `<tr>
    <td>${esc(cell(row.token))}</td>
    <td>${esc(cell(row.state))}</td>
    <td>${esc(cell(row.cbf))}</td>
    <td>${esc(cell(row.lts))}</td>
    <td>${esc(cell(row.uf))}</td>
    <td>${esc(cell(row.reason))}</td>
  </tr>`).join('\n');
  const crossing = back.shows_crossing_before_2025_10_28 === false && back.all_clear === false
    ? `No crossing is shown before 2025-10-28. The earlier windows are not an all-clear.${coverageLine(back)}`
    : (back.readable === false
      ? 'The backtest could not be read.'
      : 'See the onboarding note for how to read this result.');
  const banner = publicView
    ? 'Public signal, not a verdict. Ratios only. Flag: ' + flag + '.'
    : 'Admin test. The flag is off, so the public gets 404. Ratios only. Flag: ' + flag + '.';
  const note = doc ? `<article class="panel"><pre>${esc(doc)}</pre></article>` : '';
  return `<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>RUNECLAW — Recursion Breaker</title>
<link rel="canonical" href="https://www.humanoid-traders.com/rxb">
<meta name="robots" content="noindex">
<link rel="icon" type="image/svg+xml" href="/favicon.svg">
<link rel="stylesheet" href="/styles.css?v=52">
<style>
  .wrap { max-width: 820px; margin: 0 auto; padding: 32px 20px 64px; }
  h1 { font-family: var(--font-display); letter-spacing: .04em; }
  .panel { background: var(--surface); border: 1px solid var(--line); border-radius: 12px; padding: 20px; margin: 16px 0; }
  pre { white-space: pre-wrap; font: inherit; color: var(--text-2); }
  table { width: 100%; border-collapse: collapse; }
  td, th { text-align: left; padding: 8px 6px; border-bottom: 1px solid var(--line); font-family: var(--font-data); font-size: 14px; }
  .muted { color: var(--text-2); }
  .signal { color: var(--text-2); }
</style>
</head>
<body>
<main class="wrap">
  <h1>Recursion Breaker</h1>
  <p class="signal">${esc(banner)}</p>
  <p>${esc(crossing)}</p>
  <p class="muted">Mainnet signing is refused. A decrease prepared here is unsigned, and it is not activated.</p>
  <form method="get" action="/rxb">
    <label for="token">Token</label>
    <input id="token" name="token" maxlength="32" autocomplete="off">
    <button type="submit">Read</button>
  </form>
  <div class="panel">
    <table>
      <thead><tr><th>Token</th><th>Signal</th><th>CBF</th><th>LTS</th><th>UF</th><th>Note</th></tr></thead>
      <tbody>${rows}</tbody>
    </table>
  </div>
  ${note}
</main>
</body>
</html>`;
}

function sendRxb(req, res, deps) {
  const auth = (deps && deps.optionalAuth) || optionalAuth;
  const load = (deps && deps.readPage) || readPage;
  auth(req, res, async () => {
    const gate = await access(req, deps);
    if (!gate.ok) return res.status(404).type('text/plain').send('Not found');
    res.setHeader('Cache-Control', 'no-store');
    return res.type('html').send(render(load(), gate.audience));
  });
}

module.exports = { sendRxb, render };
