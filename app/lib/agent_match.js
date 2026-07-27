/**
 * Agent ↔ live-signal matcher for the Strategy-Agent "follow / paper-copy"
 * feature (Marketplace Phase 3).
 *
 * Marketplace agents are static catalogue entries defined by an explicit,
 * machine-readable gate set (their frozen-backtest scorecard `gates`). The
 * global signal stream is live. This module answers "which of the live signals
 * would THIS agent act on?" by applying the agent's gates to each signal.
 *
 * Honesty (§4): the live `signals` payload carries confidence, regime and
 * symbol — but NOT the intraday rsi / volume-spike readings an agent's finer
 * entry gates use. So we match only on the gates the signal actually exposes
 * (confidence, regime, symbol universe) and report exactly which gates were
 * applied, so the UI can say "coarse match — the engine applies the finer entry
 * filters live." We never fabricate a match on data we don't have, and this
 * never places a trade — it only surfaces candidates a human can paper-copy.
 */
'use strict';

// Normalise a symbol to its base ticker so an agent `symbols` allow-list of
// ["BTC","ETH"] matches a signal on "BTC/USDT" or "ETH/USDT:USDT".
function baseOf(sym) {
  const s = String(sym || '').toUpperCase().replace(/[:/].*$/, '').trim();
  // Signals arrive in both shapes — 'BTC/USDT' from the stream, 'BTCUSDT'
  // from exchange-style rows. The allow-list means BASE TICKERS, so a
  // trailing USDT quote is stripped either way (never to emptiness).
  return s.endsWith('USDT') && s.length > 4 ? s.slice(0, -4) : s;
}

// Project a COMMUNITY strategy's signal-checkable rules onto the same gate
// shape the engine agents publish, so one matcher serves both catalogues.
// Only rules the signal payload can actually answer become gates:
//   min_confidence → confidence_threshold, allowed_symbols → symbols,
//   blocked_symbols → blocked_symbols, direction → direction, and the
//   strategy's regime chip (when not 'any') → regime_filter.
// Everything else — size caps, drawdown halts, R:R floors, open-position
// counts — is the member's own risk config, NOT a signal filter; projecting
// it would fabricate a match criterion the data cannot support.
function rulesToGates(rules, regime) {
  const gates = {};
  for (const r of (Array.isArray(rules) ? rules : [])) {
    if (!r || !r.type) continue;
    if (r.type === 'min_confidence' && Number(r.value) > 0) {
      gates.confidence_threshold = Number(r.value);
    } else if (r.type === 'allowed_symbols' && Array.isArray(r.value) && r.value.length) {
      gates.symbols = r.value;
    } else if (r.type === 'blocked_symbols' && Array.isArray(r.value) && r.value.length) {
      gates.blocked_symbols = r.value;
    } else if (r.type === 'direction' && (r.value === 'long_only' || r.value === 'short_only')) {
      gates.direction = r.value;
    }
  }
  const rg = String(regime || '').toLowerCase();
  if (rg && rg !== 'any') gates.regime_filter = rg;
  return gates;
}

// Which of an agent's gates can actually be checked against a live signal?
// (confidence / regime / symbol are in the payload; rsi_max & volume_spike_min
// are not, so they're always reported as "applied live by the engine".)
function matchableGates(gates) {
  const g = gates || {};
  const applied = [];
  if (g.confidence_threshold != null && Number(g.confidence_threshold) > 0) applied.push('confidence');
  if (g.regime_filter) applied.push('regime');
  if (Array.isArray(g.symbols) && g.symbols.length) applied.push('symbols');
  if (Array.isArray(g.blocked_symbols) && g.blocked_symbols.length) applied.push('blocked');
  if (g.direction) applied.push('direction');
  return applied;
}

// Does the agent's gate set admit this live signal? Only the gates present in
// the signal payload are enforced; absent-data gates never cause a false match
// (they're neither passed nor failed here — they're applied live downstream).
function agentWouldTake(signal, gates) {
  if (!signal || !gates) return false;
  const conf = Number(signal.confidence);
  const thr = Number(gates.confidence_threshold);
  if (Number.isFinite(thr) && thr > 0) {
    if (!Number.isFinite(conf) || conf < thr) return false;
  }
  if (gates.regime_filter) {
    if (String(signal.regime || '').toUpperCase() !== String(gates.regime_filter).toUpperCase()) return false;
  }
  if (Array.isArray(gates.symbols) && gates.symbols.length) {
    const allow = new Set(gates.symbols.map(baseOf));
    if (!allow.has(baseOf(signal.symbol))) return false;
  }
  // Community-config gates (additive — engine gate sets never carry these,
  // so engine matching behaviour is byte-identical to before):
  if (Array.isArray(gates.blocked_symbols) && gates.blocked_symbols.length) {
    const deny = new Set(gates.blocked_symbols.map(baseOf));
    if (deny.has(baseOf(signal.symbol))) return false;
  }
  if (gates.direction) {
    const dir = String(signal.direction || '').toUpperCase();
    if (gates.direction === 'long_only' && dir !== 'LONG') return false;
    if (gates.direction === 'short_only' && dir !== 'SHORT') return false;
  }
  return true;
}

// For one agent + the live signal list, return the picks it would take (newest
// first, capped) plus which gates were applied. `agent.scorecard.gates` holds
// the gate set. Signals should be pre-filtered to the actionable set (OPEN).
function picksForAgent(agent, signals, limit = 8) {
  const gates = agent && agent.scorecard && agent.scorecard.gates;
  const applied = matchableGates(gates);
  const picks = [];
  for (const s of (signals || [])) {
    if (agentWouldTake(s, gates)) {
      picks.push(s);
      if (picks.length >= limit) break;
    }
  }
  return { id: agent.id, name: agent.name, icon: agent.icon || '🤖', matched_on: applied, picks };
}

module.exports = { baseOf, rulesToGates, matchableGates, agentWouldTake, picksForAgent };
