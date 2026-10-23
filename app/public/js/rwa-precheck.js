'use strict';
/**
 * /rwa — draw what POST /v1/rwa/precheck returned.
 *
 * The server decides the words and the tone. This file does not recolour a
 * number, and it does not treat a missing age as zero. Nothing is fetched
 * until the button is used: a 404 while the flag is off must not be a
 * console error on a page that merely opened.
 */
(function () {
  var TONES = { caution: true, muted: true, neutral: true };
  var REASONS = {
    unreadable: 'The chain could not be read. There is no finding under that.',
    rpc_unconfigured: 'No RPC URL is configured for this chain.',
    no_reader: 'This chain family has no reader.',
    unsupported_chain: 'That chain is not in this pre-check.',
    invalid_asset: 'The asset must be an EVM address.',
    invalid_account: 'The account must be an EVM address, or left blank.',
  };

  function el(tag, cls) {
    var node = document.createElement(tag);
    if (cls) node.className = cls;
    return node;
  }

  function text(parent, tag, cls, value) {
    var node = el(tag, cls);
    node.textContent = value == null ? '' : String(value);
    parent.appendChild(node);
    return node;
  }

  function paintError(out, message) {
    out.replaceChildren();
    text(out, 'p', 'rwa-err', message);
  }

  function finite(n) {
    return typeof n === 'number' && Number.isFinite(n);
  }

  function oracleLines(oracle) {
    if (!oracle || oracle.referenced !== true) {
      return ['No feed was reported by this contract.'];
    }
    var lines = [];
    if (oracle.update === 'no_update') lines.push('The feed reports no update. Age was not treated as zero.');
    else if (oracle.update === 'clock_skew') lines.push('The feed update is later than the block. Age was not computed.');
    else if (finite(oracle.age_seconds)) lines.push('Last update was ' + oracle.age_seconds + ' seconds before this block.');
    else lines.push('The age of the last update could not be read.');
    if (finite(oracle.heartbeat_seconds)) lines.push('Heartbeat ' + oracle.heartbeat_seconds + ' seconds.');
    else lines.push('Heartbeat was not readable.');
    if (oracle.stale === true) lines.push('Older than the heartbeat.');
    else if (oracle.stale === false) lines.push('Within the heartbeat. That comparison does not say the data is right.');
    else lines.push('Whether it is stale could not be decided.');
    return lines;
  }

  function paint(out, data) {
    out.replaceChildren();
    if (!data || data.read === false) {
      var reason = data && REASONS[data.reason];
      text(out, 'p', 'rwa-err', reason || 'The pre-check did not return a reading.');
      return;
    }
    if (data.token && typeof data.token.name === 'string' && data.token.name) {
      var title = data.token.name;
      if (typeof data.token.symbol === 'string' && data.token.symbol) title += ' (' + data.token.symbol + ')';
      text(out, 'p', 'rwa-k', title);
    }
    if (data.block && typeof data.block.number === 'string') {
      text(out, 'p', 'rwa-k', 'Block ' + data.block.number);
    }
    var flags = Array.isArray(data.flags) ? data.flags : [];
    flags.forEach(function (f) {
      var tone = f && TONES[f.tone] ? f.tone : 'muted';
      var row = el('div', 'flag tone-' + tone);
      elAppend(row, 'span', 'dot');
      text(row, 'div', 'wy', f && f.text ? f.text : '');
      out.appendChild(row);
    });
    var unread = Array.isArray(data.unreadable) ? data.unreadable : [];
    if (unread.length) {
      text(out, 'p', 'rwa-k', 'Not read');
      text(out, 'p', 'wy', unread.join(', ') + ' — no value was filled in for these.');
    }
    if (data.oracle) {
      text(out, 'p', 'rwa-k', 'Oracle');
      oracleLines(data.oracle).forEach(function (line) { text(out, 'p', 'wy', line); });
    }
    if (data.receipt && typeof data.receipt.verify === 'string' && data.receipt.verify.indexOf('/call/') === 0) {
      var p = el('p', 'wy');
      var a = el('a');
      a.href = data.receipt.verify;
      a.textContent = 'Receipt ' + (data.receipt.scan_key || '');
      p.appendChild(a);
      out.appendChild(p);
    }
    if (typeof data.note === 'string' && data.note) text(out, 'p', 'disc', data.note);
  }

  function elAppend(parent, tag, cls) {
    var node = el(tag, cls);
    parent.appendChild(node);
    return node;
  }

  async function run() {
    var out = document.getElementById('rwa-out');
    if (!out) return;
    var chain = document.getElementById('rwa-chain');
    var asset = document.getElementById('rwa-asset');
    var account = document.getElementById('rwa-account');
    var body = {
      chain: chain ? chain.value : '',
      asset: asset ? asset.value.trim() : '',
    };
    var acct = account ? account.value.trim() : '';
    if (acct) body.account = acct;
    var res;
    try {
      res = await fetch('/v1/rwa/precheck', {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify(body),
        signal: AbortSignal.timeout(60000),
      });
    } catch (err) {
      paintError(out, 'The pre-check did not answer.');
      return;
    }
    if (res.status === 404) {
      paintError(out, 'This pre-check is not enabled.');
      return;
    }
    var data = null;
    try { data = await res.json(); } catch (err) { data = null; }
    if (!data || typeof data !== 'object') {
      paintError(out, 'The pre-check answer could not be read.');
      return;
    }
    if (!res.ok) {
      paintError(out, typeof data.error === 'string' ? data.error : 'The pre-check was refused.');
      return;
    }
    paint(out, data);
  }

  var go = document.getElementById('rwa-go');
  if (go) go.addEventListener('click', function () { run(); });
})();
