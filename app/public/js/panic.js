/**
 * Panic Button page. Posts an address, paints the ranked list, and links
 * each unsigned revoke into the user's wallet. Stores nothing. Does not
 * sign and does not broadcast.
 */
(function () {
  'use strict';

  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }

  function short(a) {
    var s = String(a || '');
    if (s.length < 12) return s;
    return s.slice(0, 6) + '…' + s.slice(-4);
  }

  function textOf(res) {
    return res.text();
  }

  var out = document.getElementById('out');
  var form = document.getElementById('form');
  var input = document.getElementById('addr');
  var queue = [];
  var recorded = [];

  function showUnavailable() {
    var main = document.querySelector('.pn-wrap');
    if (main) main.innerHTML = '<p>not available yet</p>';
  }

  function who(item) {
    if (item.kind === 'delegation_7702') return 'delegation ' + short(item.spender);
    return 'token ' + short(item.token) + ' · spender ' + short(item.spender);
  }

  function when(item) {
    return item.updated ? 'updated ' + item.updated : 'updated time could not be read';
  }

  function expiry(item) {
    if (item.kind !== 'permit2') return '';
    return item.permit2_expires
      ? ' · Permit2 expires ' + item.permit2_expires
      : ' · Permit2 expiry could not be read';
  }

  function paint(body) {
    var html = '';
    html += '<p class="note">' + esc(body.read_line || 'These are the approvals we could read.') + '</p>';
    if (body.read_failed && body.message) {
      html += '<p class="empty">' + esc(body.message) + '</p>';
    }
    var items = Array.isArray(body.items) ? body.items : [];
    if (!body.read_failed && items.length === 0) {
      html += '<p class="empty">No open approval was found on the chains that could be read.</p>';
    }
    items.forEach(function (item, i) {
      var link = item.open_url && item.tx
        ? '<a class="btn btn--sm" href="' + esc(item.open_url) + '">Open in your wallet</a>'
        : '<p class="meta">No revoke was prepared for this item.</p>';
      html += '<article class="item rank' + esc(item.rank) + '">'
        + '<div class="hd">' + (i + 1) + '. ' + esc(item.chain) + ' · ' + esc(who(item)) + '</div>'
        + '<div class="why">' + esc(item.why) + esc(expiry(item)) + '</div>'
        + '<div class="meta">' + esc(when(item)) + '</div>'
        + link
        + '</article>';
    });
    var unread = Array.isArray(body.unread_chains) ? body.unread_chains : [];
    if (unread.length) {
      html += '<p class="note">Could not read: ' + esc(unread.join(', ')) + '.</p>';
    }
    if (body.exploit_list_read === false) {
      html += '<p class="note">The public exploit list could not be read, so spenders on it are not included.</p>';
    }
    if (items.length > 1) {
      html += '<p class="queue"><button class="btn btn--sm" id="all" type="button">Revoke all, one at a time</button></p>';
      html += '<div id="queue" class="queue" hidden></div>';
    }
    out.innerHTML = html;
    queue = items.filter(function (item) { return item && item.open_url && item.tx; });
    recorded = [];
    var all = document.getElementById('all');
    if (all) all.addEventListener('click', openQueue);
  }

  function openQueue() {
    var box = document.getElementById('queue');
    if (!box) return;
    var html = '<p class="note">Sign these in your wallet, one after another. Each link is the same revoke as above.</p>';
    queue.forEach(function (item, i) {
      html += '<p class="' + (i === 0 ? 'next' : '') + '" data-step="' + i + '">'
        + (i + 1) + '. <a href="' + esc(item.open_url) + '">Open in your wallet</a></p>';
    });
    html += '<form id="hashform" class="row">'
      + '<label for="txhash">Transaction hash</label>'
      + '<input type="text" id="txhash" autocomplete="off" spellcheck="false" placeholder="0x…">'
      + '<button class="btn btn--sm" type="submit">Record this hash</button>'
      + '</form><div id="receipt"></div>';
    box.hidden = false;
    box.innerHTML = html;
    document.getElementById('hashform').addEventListener('submit', onHash);
  }

  function onHash(ev) {
    ev.preventDefault();
    var field = document.getElementById('txhash');
    var hash = String(field.value || '').trim();
    if (!/^0x[0-9a-fA-F]{64}$/.test(hash)) {
      document.getElementById('receipt').textContent = 'That is not a transaction hash.';
      return;
    }
    recorded.push(hash.toLowerCase());
    field.value = '';
    var steps = document.querySelectorAll('#queue [data-step]');
    var n = recorded.length;
    for (var i = 0; i < steps.length; i++) {
      steps[i].className = i === n ? 'next' : '';
    }
    fetch('/api/panic/receipt', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ hashes: recorded }),
    }).then(function (res) { return res.json().then(function (j) { return { ok: res.ok, j: j }; }); })
      .then(function (r) {
        var slot = document.getElementById('receipt');
        if (!r.ok || !r.j || !r.j.cleaned_at) {
          slot.textContent = 'The receipt was not sealed.';
          return;
        }
        slot.textContent = 'Cleaned at ' + r.j.cleaned_at + '. Hashes recorded: ' + r.j.hashes.length + '.';
      })
      .catch(function () {
        document.getElementById('receipt').textContent = 'The receipt was not sealed.';
      });
  }

  form.addEventListener('submit', function (ev) {
    ev.preventDefault();
    var address = String(input.value || '').trim();
    out.textContent = 'Reading the chains.';
    fetch('/api/panic', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ address: address }),
    }).then(function (res) {
      var ctype = res.headers.get('content-type') || '';
      if (ctype.indexOf('application/json') === -1) return textOf(res).then(function (t) { return { text: t }; });
      return res.json().then(function (j) { return { status: res.status, json: j }; });
    }).then(function (r) {
      if (r.text && r.text.indexOf('not available yet') !== -1) {
        showUnavailable();
        return;
      }
      if (r.json && r.status === 400) {
        input.value = '';
        out.textContent = r.json.message || 'Nothing was stored.';
        return;
      }
      if (!r.json) {
        out.textContent = 'The approval lists could not be read just now, so there is no list. This is not a clean result.';
        return;
      }
      paint(r.json);
    }).catch(function () {
      out.textContent = 'The approval lists could not be read just now, so there is no list. This is not a clean result.';
    });
  });
}());
