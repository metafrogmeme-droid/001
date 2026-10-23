'use strict';

function esc(s) {
  return String(s)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;');
}

function statusLabel(card) {
  if (card && card.status === 'coming_soon') return 'Coming soon';
  if (!card || card.status === null || card.status === undefined) return 'not read';
  return String(card.status);
}

async function load() {
  const host = document.getElementById('tools-list');
  if (!host) return;
  try {
    const res = await fetch('/api/tools', { headers: { Accept: 'application/json' } });
    if (!res.ok) {
      host.innerHTML = '<p class="tone-muted">The list could not be read.</p>';
      return;
    }
    const body = await res.json();
    const tools = body && Array.isArray(body.tools) ? body.tools : [];
    if (!tools.length) {
      host.innerHTML = '<p class="tone-muted">No tools are listed.</p>';
      return;
    }
    host.innerHTML = tools.map((card) => {
      const href = typeof card.href === 'string' ? card.href : '';
      const link = href ? '<p><a href="' + esc(href) + '">Open</a></p>' : '';
      return '<section class="card"><div class="status">' + esc(statusLabel(card)) + '</div>'
        + '<h2>' + esc(card.name || 'Untitled') + '</h2>'
        + '<p>' + esc(card.summary || '') + '</p>' + link + '</section>';
    }).join('');
  } catch (err) {
    host.innerHTML = '<p class="tone-muted">The list could not be read.</p>';
  }
}

load();
