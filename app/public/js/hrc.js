'use strict';
/**
 * Draw GET /api/hrc. Numbers are text. A missing number is the word
 * unreadable. Nothing on this page is coloured as profit or loss.
 */
(function () {
  var out = document.getElementById('hrc-out');

  function text(parent, tag, cls, value) {
    var node = document.createElement(tag);
    if (cls) node.className = cls;
    node.textContent = value == null ? '' : String(value);
    parent.appendChild(node);
    return node;
  }

  function line(parent, label, value) {
    var row = document.createElement('p');
    row.className = 'hrc-row';
    text(row, 'span', 'hrc-k', label);
    var shown = (value == null || value === '') ? 'unreadable' : String(value);
    text(row, 'span', 'hrc-num', shown);
    parent.appendChild(row);
  }

  function paint(packet) {
    if (!out) return;
    out.replaceChildren();
    if (!packet || packet.readable !== true) {
      var why = packet && typeof packet.reason === 'string' && packet.reason
        ? packet.reason
        : 'unreadable';
      text(out, 'p', 'hrc-note', 'Unreadable (' + why + ').');
      return;
    }
    if (packet.synthetic === true) {
      text(out, 'p', 'hrc-note', 'Testnet readings are synthetic.');
    } else if (packet.synthetic === false && packet.network === 'mainnet') {
      text(out, 'p', 'hrc-note', 'Mainnet read.');
    }
    line(out, 'Network', packet.network);
    line(out, 'Source', packet.source);
    line(out, 'Utilization', packet.utilization);
    line(out, 'Borrow APY', packet.borrow_apy);
    line(out, 'Points below the kink', packet.points_to_kink);
    line(out, 'Points below the funding crossover', packet.points_to_crossover);
    line(out, 'Borrow cap used', packet.borrow_cap_ratio);
    line(out, 'Trailing mean', packet.trailing_mean);
    line(out, 'Premium fraction', packet.premium);
    if (packet.cap_hit === true) line(out, 'Cap-hit', 'yes');
    else if (packet.cap_hit === false) line(out, 'Cap-hit', 'no');
    else line(out, 'Cap-hit', null);
    var replays = packet.replays;
    if (replays && typeof replays === 'object') {
      ['scenario_a', 'scenario_b'].forEach(function (name) {
        var row = replays[name];
        if (!row || row.readable !== true) {
          line(out, name, null);
          return;
        }
        line(out, name, 'utilization ' + row.utilization + ' borrow ' + row.borrow_apy);
      });
    }
  }

  function run() {
    fetch('/api/hrc', { credentials: 'same-origin', signal: AbortSignal.timeout(8000) })
      .then(function (r) {
        if (!r.ok) throw new Error('status');
        return r.json();
      })
      .then(paint)
      .catch(function () {
        paint({ readable: false, reason: 'unreadable' });
      });
  }

  run();
}());
