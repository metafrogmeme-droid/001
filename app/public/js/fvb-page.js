'use strict';
/**
 * Renders the published fair-value board. It does not recompute the blend.
 * A non-OK response is an error state. A missing price stays blank.
 */

(function () {
  var out = document.getElementById('fvb-out');
  var label = 'Public signals, not advice.';

  function text(value) {
    if (value === null || value === undefined || value === '') return 'unreadable';
    if (typeof value === 'number' && Number.isFinite(value)) return String(value);
    if (typeof value === 'boolean') return value ? 'yes' : 'no';
    return String(value);
  }

  function cell(row, key) {
    var td = document.createElement('td');
    td.textContent = text(row[key]);
    return td;
  }

  function showError(message) {
    out.replaceChildren();
    var p = document.createElement('p');
    p.className = 'fvb-err';
    p.textContent = message;
    out.appendChild(p);
  }

  function render(doc) {
    out.replaceChildren();
    var note = document.createElement('p');
    note.className = 'disc';
    note.textContent = (doc && doc.label) || label;
    out.appendChild(note);
    if (!doc || doc.available !== true || !Array.isArray(doc.rows) || doc.rows.length === 0) {
      var empty = document.createElement('p');
      if (doc && doc.reason === 'unreadable') {
        empty.textContent = 'Board unreadable.';
      } else {
        var line = 'Board not published. Nothing is listed here until a board file is published.';
        if (document.querySelector('.fvb-banner')) {
          line += ' The flag is off, so this view is an admin preview of that introduction.';
        }
        empty.textContent = line;
      }
      out.appendChild(empty);
      return;
    }
    var table = document.createElement('table');
    var head = document.createElement('tr');
    ['symbol', 'last trade', 'external', 'risk price', 'band', 'session', 'confirmed', 'w_ext'].forEach(function (name) {
      var th = document.createElement('th');
      th.textContent = name;
      head.appendChild(th);
    });
    table.appendChild(head);
    doc.rows.forEach(function (row) {
      var tr = document.createElement('tr');
      if (!row || row.confirmed !== true) tr.className = 'tone-muted';
      tr.appendChild(cell(row, 'symbol'));
      tr.appendChild(cell(row, 'last_trade'));
      tr.appendChild(cell(row, 'external'));
      tr.appendChild(cell(row, 'fv'));
      var band = document.createElement('td');
      var lo = row ? row.band_lo : null;
      var hi = row ? row.band_hi : null;
      band.textContent = (lo === null || lo === undefined || hi === null || hi === undefined)
        ? 'unreadable'
        : text(lo) + ' .. ' + text(hi);
      tr.appendChild(band);
      tr.appendChild(cell(row, 'session'));
      tr.appendChild(cell(row, 'confirmed'));
      tr.appendChild(cell(row, 'w_ext'));
      table.appendChild(tr);
    });
    out.appendChild(table);
  }

  var timer = (typeof AbortSignal !== 'undefined' && AbortSignal.timeout)
    ? AbortSignal.timeout(8000)
    : undefined;
  fetch('/api/fvb/board', { signal: timer, headers: { Accept: 'application/json' } })
    .then(function (res) {
      if (!res.ok) throw new Error('status ' + res.status);
      return res.json();
    })
    .then(render)
    .catch(function () {
      showError('The board could not be read.');
    });
})();
