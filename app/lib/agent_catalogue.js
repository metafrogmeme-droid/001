'use strict';
/**
 * The engine strategy-agent catalogue, read through the bot gateway with a
 * short cache — extracted from routes/copy.js so the follow flow and arena
 * attribution verify against the SAME source.
 *
 * `readable` distinguishes "the catalogue answered (possibly empty)" from
 * "we could not read it" — an absence claim ("unknown agent") is only honest
 * in the first case. An unconfigured gateway IS readable: this deployment
 * knowingly has no engine catalogue, so its emptiness is a fact, not a guess.
 * A stale cache still counts as readable — stale beats blind.
 */

const { getGateway, isConfigured } = require('./gateway');

const CAT_MS = 5 * 60 * 1000;   // the catalogue changes only on a deploy
let catCache = null;            // { at, agents }

async function loadCatalogueChecked() {
  // Cache first: a fresh cache is readable regardless of configuration (and
  // it is the test seam — _setCache plants a catalogue without a gateway).
  const now = Date.now();
  if (catCache && (now - catCache.at) < CAT_MS) return { agents: catCache.agents, readable: true };
  if (!isConfigured()) return { agents: [], readable: true };
  try {
    const r = await getGateway('/public/strategies', 15000);
    if (r.status >= 200 && r.status < 300 && r.data && Array.isArray(r.data.agents)) {
      catCache = { at: now, agents: r.data.agents };
      return { agents: catCache.agents, readable: true };
    }
  } catch (e) { /* fall through to stale-or-unreadable */ }
  if (catCache) return { agents: catCache.agents, readable: true };  // stale beats blind
  return { agents: [], readable: false };
}

async function loadCatalogue() {
  return (await loadCatalogueChecked()).agents;
}

function _resetCache() { catCache = null; }
/** Test seam: plant a catalogue so attribution paths are testable offline. */
function _setCache(agents) { catCache = { at: Date.now(), agents }; }

module.exports = { loadCatalogueChecked, loadCatalogue, _resetCache, _setCache };
