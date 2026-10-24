'use strict';
/**
 * Chain filter for the tools catalog. The cards are already in the
 * document. This script shows the ones whose chains include the
 * selected id, and it does not fetch a reading.
 */
(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  if (root) root.ToolsPage = api;
  if (typeof document !== 'undefined' && document.querySelector) {
    const start = () => api.bind(document);
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start);
    else start();
  }
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  function chainList(card) {
    const raw = card && card.getAttribute ? card.getAttribute('data-chains') : (card && card.chains);
    return String(raw || '').split(/\s+/).filter(Boolean);
  }

  function cardId(card) {
    if (card && card.getAttribute) return card.getAttribute('data-tool');
    return card && card.id;
  }

  function visibleIds(cards, chain) {
    const ids = new Set();
    const list = cards || [];
    for (let i = 0; i < list.length; i += 1) {
      const card = list[i];
      const chains = chainList(card);
      if (chain === 'all' || chains.indexOf(chain) !== -1) ids.add(cardId(card));
    }
    return ids;
  }

  function apply(scope, chain) {
    const host = scope || document;
    const cards = host.querySelectorAll('[data-tool]');
    const ids = visibleIds(cards, chain);
    for (let i = 0; i < cards.length; i += 1) {
      cards[i].hidden = !ids.has(cards[i].getAttribute('data-tool'));
    }
    const empty = host.querySelector('[data-empty]');
    if (empty) empty.hidden = ids.size !== 0;
    const tabs = host.querySelectorAll('[data-chain]');
    for (let i = 0; i < tabs.length; i += 1) {
      const on = tabs[i].getAttribute('data-chain') === chain;
      tabs[i].setAttribute('aria-pressed', on ? 'true' : 'false');
    }
    return ids;
  }

  function bind(scope) {
    const host = scope || document;
    const bar = host.querySelector('[data-chain-bar]');
    if (!bar || bar.dataset.bound === '1') return;
    bar.dataset.bound = '1';
    bar.addEventListener('click', (ev) => {
      const target = ev.target;
      const btn = target && target.closest ? target.closest('[data-chain]') : null;
      if (!btn || !bar.contains(btn)) return;
      apply(host, btn.getAttribute('data-chain'));
    });
  }

  return { visibleIds, apply, bind };
});
