'use strict';
/**
 * Draw the committed QCH reading. A number is printed only beside the
 * word estimate, the method, and the data source. One failed panel stays
 * unread and leaves the other panel in place.
 */
(function (root, factory) {
  var api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  if (typeof document !== 'undefined') api.boot();
}(typeof window !== 'undefined' ? window : null, function () {
  function scoreLine(row) {
    if (!row || row.label !== 'estimate' || row.estimate !== true) return null;
    if (typeof row.method !== 'string' || row.method.length === 0) return null;
    if (typeof row.data_source !== 'string' || row.data_source.length === 0) return null;
    if (!Number.isInteger(row.qes_bps)) return null;
    return String(row.qes_bps) + ' bps, estimate. ' + row.method + ' Source: ' + row.data_source;
  }

  function policyLine(body) {
    if (!body || body.label !== 'estimate' || body.estimate !== true) return null;
    if (typeof body.method !== 'string' || body.method.length === 0) return null;
    if (typeof body.data_source !== 'string' || body.data_source.length === 0) return null;
    var decision = body.decision;
    if (!decision || typeof decision.action !== 'string') return null;
    var haircut = 'Haircut unread.';
    if (Number.isInteger(decision.haircut_bps)) haircut = 'Haircut ' + String(decision.haircut_bps) + ' bps.';
    var liquidate = 'Whether a liquidation is scheduled could not be read.';
    if (decision.liquidate === false) liquidate = 'No liquidation.';
    return decision.action + '. ' + haircut + ' ' + liquidate + ' Estimate. ' + body.method
      + ' Source: ' + body.data_source;
  }

  function countLine(chain, sats) {
    if (typeof chain !== 'string' || chain.length === 0) return null;
    if (!Number.isInteger(sats) || sats < 0) return null;
    return chain + ' ' + String(sats) + ' sats';
  }

  function line(parent, text, muted) {
    var p = document.createElement('p');
    if (muted) p.className = 'muted';
    p.textContent = text;
    parent.appendChild(p);
  }

  function heading(parent, text) {
    var h = document.createElement('p');
    h.className = 'k';
    h.textContent = text;
    parent.appendChild(h);
  }

  function clear(node) {
    while (node.firstChild) node.removeChild(node.firstChild);
  }

  function showBanner(body) {
    var banner = document.getElementById('qch-banner');
    if (!banner) return;
    if (body && typeof body.banner === 'string' && body.banner) {
      banner.textContent = body.banner;
      banner.hidden = false;
    }
  }

  function renderIssuer(parent, row) {
    var card = document.createElement('div');
    card.className = 'issuer';
    var title = document.createElement('h2');
    title.textContent = row && typeof row.asset === 'string' && row.asset ? row.asset : 'An issuer';
    card.appendChild(title);
    var shown = scoreLine(row);
    if (shown) line(card, shown);
    else line(card, 'The exposure score could not be read.', true);
    if (row && Array.isArray(row.supply_by_chain)) {
      var listed = [];
      var broken = false;
      for (var i = 0; i < row.supply_by_chain.length; i++) {
        var item = row.supply_by_chain[i];
        var text = item ? countLine(item.chain, item.sats) : null;
        if (!text) { broken = true; break; }
        listed.push(text);
      }
      if (broken) line(card, 'A chain supply count could not be read.', true);
      else if (listed.length) line(card, listed.join(' · '), true);
    }
    parent.appendChild(card);
  }

  function renderScore(body) {
    var out = document.getElementById('qch-score');
    if (!out) return;
    clear(out);
    showBanner(body);
    heading(out, 'Issuers');
    if (!body || body.readable !== true || !Array.isArray(body.issuers)) {
      line(out, 'The custody reading could not be read.', true);
      return;
    }
    if (body.signs === false) line(out, 'This server does not sign.', true);
    else line(out, 'Whether this server signs could not be read.', true);
    for (var i = 0; i < body.issuers.length; i++) renderIssuer(out, body.issuers[i]);
    if (Array.isArray(body.scenarios)) mountCases(body.scenarios);
  }

  function renderPolicy(body) {
    var out = document.getElementById('qch-policy');
    if (!out) return;
    var existing = document.getElementById('qch-decision');
    if (existing) existing.remove();
    var kids = Array.prototype.slice.call(out.childNodes);
    for (var n = 0; n < kids.length; n++) {
      var node = kids[n];
      if (node.id === 'qch-cases' || node.id === 'qch-decision') continue;
      if (node.className === 'k') continue;
      out.removeChild(node);
    }
    var slot = document.createElement('div');
    slot.id = 'qch-decision';
    var shown = policyLine(body);
    if (shown) line(slot, shown);
    else line(slot, 'The policy case could not be read.', true);
    out.appendChild(slot);
    showBanner(body);
  }

  function mountCases(names) {
    var out = document.getElementById('qch-policy');
    if (!out || document.getElementById('qch-cases')) return;
    var title = document.createElement('p');
    title.className = 'k';
    title.textContent = 'Policy';
    out.insertBefore(title, out.firstChild);
    var bar = document.createElement('div');
    bar.id = 'qch-cases';
    bar.className = 'cases';
    for (var i = 0; i < names.length; i++) {
      var button = document.createElement('button');
      button.type = 'button';
      button.textContent = names[i];
      button.addEventListener('click', function (ev) {
        loadPolicy(ev.currentTarget.textContent);
      });
      bar.appendChild(button);
    }
    var after = title.nextSibling;
    if (after) out.insertBefore(bar, after);
    else out.appendChild(bar);
  }

  function fetchJson(url) {
    var opts = {};
    try { opts.signal = AbortSignal.timeout(8000); } catch (e) { /* no timer here */ }
    return fetch(url, opts).then(function (res) {
      if (!res.ok) throw new Error('status');
      return res.json();
    });
  }

  function loadPolicy(scenario) {
    var query = typeof scenario === 'string' && scenario ? scenario : '';
    return fetchJson('/api/qch/policy?scenario=' + encodeURIComponent(query))
      .then(function (body) { renderPolicy(body); })
      .catch(function () { renderPolicy(null); });
  }

  function boot() {
    var score = document.getElementById('qch-score');
    var policy = document.getElementById('qch-policy');
    if (score) line(score, 'Reading the custody snapshot.', true);
    if (policy) line(policy, 'Reading the policy case.', true);
    fetchJson('/api/qch/score')
      .then(function (body) { renderScore(body); })
      .catch(function () { renderScore(null); });
    loadPolicy('clear');
  }

  return { scoreLine: scoreLine, policyLine: policyLine, countLine: countLine, boot: boot };
}));
