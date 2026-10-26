/**
 * Claw Watch page. Posts the fields to our own check route and shows the
 * unsigned transaction back. Nothing in this file signs, relays, or stores.
 */
(function () {
  'use strict';

  var form = document.getElementById('cw-form');
  var out = document.getElementById('cw-out');
  if (!form || !out) return;

  function text(el) {
    return el && typeof el.value === 'string' ? el.value.trim() : '';
  }

  function lines(raw) {
    if (!raw) return [];
    return raw.split(/\s+/).map(function (s) { return s.trim(); }).filter(Boolean);
  }

  function show(node) {
    out.replaceChildren(node);
  }

  function p(cls, sentence) {
    var el = document.createElement('p');
    if (cls) el.className = cls;
    el.textContent = sentence;
    return el;
  }

  function fieldsOf(tx) {
    if (!tx) return '';
    if (tx.kind === 'authorization') {
      return [
        'kind: authorization',
        'chain_id: ' + tx.chain_id,
        'address: ' + tx.address,
        "nonce: your wallet's current nonce",
        "signature: your wallet's, not ours",
      ].join('\n');
    }
    return [
      'kind: transaction',
      'to: ' + tx.to,
      'value: ' + tx.value,
      'chain_id: ' + tx.chain_id,
      'data: ' + tx.data,
    ].join('\n');
  }

  function paint(body) {
    var box = document.createElement('div');
    box.appendChild(p('', body && body.advisory ? body.advisory : 'Claw Watch is advisory only.'));
    if (body && body.banner) box.appendChild(p('muted', body.banner));
    var signals = body && body.signals ? body.signals : {};
    var names = ['exploit_list', 'approval_age_days', 'unlimited', 'delegate_family', 'claw_guard', 'lookalike', 'dust'];
    names.forEach(function (name) {
      var value = Object.prototype.hasOwnProperty.call(signals, name) ? signals[name] : 'unread';
      box.appendChild(p('muted', name.replace(/_/g, ' ') + ': ' + value));
    });
    if (body && body.alert === true) {
      box.appendChild(p('', 'Lookalike: the destination matches the first 4 and last 4 characters of a counterparty you listed.'));
    } else if (signals.dust === 'hide') {
      box.appendChild(p('muted', 'Dust is labelled and hidden. It is not an alert.'));
    }
    box.appendChild(p('muted', 'A cancel link applies once an on-chain module exists. None is installed, so there is no cancel transaction.'));
    if (body && body.tx) {
      box.appendChild(p('', 'The transaction below is unsigned. Your wallet signs it and submits it. It does not depend on this site staying online. Nothing here is pre-signed, and this site does not relay it.'));
      var pre = document.createElement('pre');
      pre.className = 'fields';
      pre.textContent = fieldsOf(body.tx);
      box.appendChild(pre);
      if (body.tx.kind === 'transaction' && body.tx.data) {
        var button = document.createElement('button');
        button.type = 'button';
        button.className = 'btn btn--primary btn--sm';
        button.textContent = 'Ask your wallet to sign this';
        button.addEventListener('click', function () { askWallet(body.tx, box); });
        box.appendChild(button);
      }
    }
    show(box);
  }

  function askWallet(tx, box) {
    var eth = window.ethereum;
    if (!eth || typeof eth.request !== 'function') {
      box.appendChild(p('muted', 'No wallet is attached in this browser. Copy the fields and submit them from any wallet.'));
      return;
    }
    var value = tx.value == null || tx.value === '' ? '0x0' : String(tx.value);
    eth.request({
      method: 'eth_sendTransaction',
      params: [{ to: tx.to, data: tx.data, value: value }],
    }).then(function () {
      box.appendChild(p('muted', 'The wallet accepted the transaction. This site did not sign it and did not relay it.'));
    }).catch(function () {
      box.appendChild(p('muted', 'The wallet did not submit it. The fields above are still the transaction.'));
    });
  }

  form.addEventListener('submit', function (ev) {
    ev.preventDefault();
    var value = text(document.getElementById('cw-value'));
    var payload = {
      chain_id: text(document.getElementById('cw-chain')),
      to: text(document.getElementById('cw-to')),
      data: text(document.getElementById('cw-data')),
      destination: text(document.getElementById('cw-dest')),
      counterparties: lines(text(document.getElementById('cw-known'))),
    };
    if (value !== '') payload.value = value;
    fetch('/api/drain-watch/check', {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify(payload),
    }).then(function (res) {
      return res.json().then(function (body) { return { status: res.status, body: body }; });
    }).then(function (got) {
      if (!got.body || typeof got.body !== 'object') {
        show(p('muted', 'The check could not be read.'));
        return;
      }
      if (got.status !== 200) {
        show(p('muted', got.body.error === 'refused'
          ? 'Nothing was stored. This page only reads a transaction.'
          : 'The check did not complete.'));
        return;
      }
      paint(got.body);
    }).catch(function () {
      show(p('muted', 'The check could not be read.'));
    });
  });
}());
