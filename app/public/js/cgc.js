'use strict';
/**
 * Draw GET /api/cgc/status. The page inserts a banner only when the
 * response carries one. A missing or unexpected body is an unread
 * sentence, never a zero and never a console error.
 */
(function () {
  var out = document.getElementById('cgc-out');
  var banner = document.getElementById('cgc-banner');
  if (!out) return;

  function unread(text) {
    var p = document.createElement('p');
    p.className = 'muted';
    p.textContent = text || 'The conservation-brake status could not be read.';
    out.appendChild(p);
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

  function ratio(num, den) {
    if (!Number.isInteger(num) || !Number.isInteger(den) || den === 0) return null;
    return String(num) + '/' + String(den);
  }

  function chainCard(row) {
    var card = document.createElement('div');
    card.className = 'chain';
    var title = document.createElement('h2');
    title.textContent = row && typeof row.name === 'string' && row.name ? row.name : 'A chain';
    card.appendChild(title);
    if (!row || !row.windows) {
      line(card, 'Its windows could not be read.', true);
      return card;
    }
    var bits = [];
    if (typeof row.mode === 'string') bits.push(row.mode);
    if (row.chain_id === null) bits.push('no EVM chain id');
    else if (Number.isInteger(row.chain_id)) bits.push('chain ' + row.chain_id);
    else bits.push('chain id could not be read');
    if (row.brake === false) bits.push('no on-chain brake in v0');
    else if (row.brake === true) bits.push('brake configured');
    else bits.push('whether a brake is deployed could not be read');
    line(card, bits.join(' · '), true);
    var list = document.createElement('ul');
    list.className = 'facts';
    var labels = [
      ['w_brake', 'W_brake'],
      ['w_prov', 'W_prov'],
      ['w_final', 'W_final'],
      ['w_final_max', 'W_final_max'],
    ];
    for (var i = 0; i < labels.length; i++) {
      var li = document.createElement('li');
      li.textContent = labels[i][1] + ' ' + row.windows[labels[i][0]] + 's';
      list.appendChild(li);
    }
    card.appendChild(list);
    return card;
  }

  function render(body) {
    if (banner) {
      if (typeof body.banner === 'string' && body.banner) {
        banner.textContent = body.banner;
        banner.hidden = false;
      } else {
        banner.textContent = '';
        banner.hidden = true;
      }
    }
    if (body.unreadable === true) {
      unread(typeof body.note === 'string' ? body.note : 'The chain list could not be read.');
      return;
    }
    if (body.server_signs === false) line(out, 'This server does not sign.');
    else if (body.server_signs === true) line(out, 'Signing was reported on. This page still has no signing control.', true);
    else line(out, 'Whether this server signs could not be read.', true);

    var wrap = body.wrapper;
    if (wrap && wrap.symbol === 'gwrsETH' && wrap.testnet_only === true) {
      line(out, 'gwrsETH is testnet only.');
    } else {
      line(out, 'The wrapper reading could not be read.', true);
    }

    heading(out, 'Chains');
    if (!Array.isArray(body.chains)) {
      unread('The chain list could not be read.');
      return;
    }
    if (body.chains.length === 0) {
      line(out, 'No chains were listed.', true);
    } else {
      var panel = document.createElement('section');
      panel.className = 'panel';
      for (var i = 0; i < body.chains.length; i++) panel.appendChild(chainCard(body.chains[i]));
      out.appendChild(panel);
    }

    heading(out, 'Inflow factor');
    if (!Array.isArray(body.inflow)) {
      line(out, 'The inflow factors could not be read.', true);
    } else {
      var shown = [];
      var broken = false;
      for (var j = 0; j < body.inflow.length; j++) {
        var row = body.inflow[j];
        var text = ratio(row && row.num, row && row.den);
        if (!row || !Number.isInteger(row.floor) || text === null) { broken = true; break; }
        shown.push('floor ' + row.floor + ' ' + text);
      }
      if (broken) line(out, 'The inflow factors could not be read.', true);
      else line(out, shown.join(' · '));
    }
    if (typeof body.inflow_unread === 'string') line(out, body.inflow_unread, true);

    heading(out, 'Kelp scenario');
    var kelp = body.kelp;
    if (!kelp || kelp.kind !== 'scenario') {
      line(out, 'The scenario was not in this reading.', true);
    } else {
      var counts = [
        ['forged release', kelp.forged_release_units],
        ['destination nonce', kelp.dest_nonce],
        ['source outbound', kelp.source_outbound_nonce],
        ['supplied', kelp.supplied_units],
        ['first window', kelp.first_window_units],
      ];
      var parts = [];
      var miss = false;
      for (var k = 0; k < counts.length; k++) {
        if (!Number.isInteger(counts[k][1])) { miss = true; break; }
        parts.push(counts[k][0] + ' ' + counts[k][1]);
      }
      if (miss) line(out, 'The scenario counts could not be read.', true);
      else line(out, parts.join(' · '));
      if (typeof kelp.note === 'string') line(out, kelp.note, true);
    }

    heading(out, 'Proof kinds');
    var kinds = body.proof_kinds;
    if (!kinds || !Array.isArray(kinds.accepted) || !Array.isArray(kinds.rejected)) {
      line(out, 'The proof kinds could not be read.', true);
    } else {
      line(out, 'Accepted: ' + kinds.accepted.join(', '));
      line(out, 'Refused: ' + kinds.rejected.join(', '), true);
    }
  }

  function showUnread() {
    unread('The conservation-brake status could not be read.');
  }

  var statusOk = function (body) {
    if (!body || typeof body !== 'object') return false;
    if (body.unreadable === true) return true;
    return Array.isArray(body.chains);
  };

  var opts = {};
  try { opts.signal = AbortSignal.timeout(8000); } catch (e) { /* no timer on this document */ }
  fetch('/api/cgc/status', opts)
    .then(function (res) {
      if (!res.ok) throw new Error('status');
      return res.json();
    })
    .then(function (body) {
      if (!statusOk(body)) { showUnread(); return; }
      render(body);
    })
    .catch(function () { showUnread(); });
}());
