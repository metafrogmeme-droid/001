'use strict';
/**
 * Reads the cover status route and paints the hold notice.
 * A failed read, a non-hold payload, or a missing chain list is unreadable.
 * It is not rendered as an offer.
 *
 * Not offered to customers. Licence review pending. Do not enable in production.
 */
(function () {
  var root = document.querySelector('[data-cover]');
  var out = document.getElementById('cover-status');
  if (!root || !out) return;
  var kind = root.getAttribute('data-cover');
  var HOLD = 'Not offered to customers. Licence review pending. Do not enable in production.';
  var path = kind === 'adl' ? '/api/cover/adl' : (kind === 'stopgap' ? '/api/cover/stopgap' : '');

  function unread() {
    out.textContent = 'The status could not be read.';
  }

  if (!path) {
    unread();
    return;
  }

  fetch(path, { headers: { Accept: 'application/json' } })
    .then(function (res) {
      if (!res.ok) {
        unread();
        return null;
      }
      return res.json();
    })
    .then(function (body) {
      if (body == null) return;
      if (body.offered !== false || !Array.isArray(body.chains) || body.notice !== HOLD) {
        unread();
        return;
      }
      out.textContent = '';
      var notice = document.createElement('p');
      notice.textContent = body.notice;
      out.appendChild(notice);
      var list = document.createElement('ul');
      for (var i = 0; i < body.chains.length; i += 1) {
        var item = document.createElement('li');
        item.textContent = String(body.chains[i]);
        list.appendChild(item);
      }
      out.appendChild(list);
    })
    .catch(function () {
      unread();
    });
}());
