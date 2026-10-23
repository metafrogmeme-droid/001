/**
 * The lookalike warning on the ask-before-sign page.
 *
 * Loaded only when Claw Guard v0.1 is on (the /guard response includes this
 * script). It does not change the address field, it does not disable Check,
 * and a warning is a yellow note beside the verdict. An "ok" is muted: no
 * lookalike in the list that was given is not a claim that the address is safe.
 */
(function (root, factory) {
  if (typeof module === 'object' && module.exports) module.exports = factory();
  else root.RCGuardV01 = factory();
}(typeof self !== 'undefined' ? self : this, function () {
  'use strict';

  var P = typeof module === 'object' && module.exports
    ? require('./address-poison.js')
    : (typeof window !== 'undefined' ? window.RCAddressPoison : null);

  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }

  function dustSentence(dust, scope) {
    if (dust === 'not_checked') return 'Dust history was not checked.';
    if (dust === 'unread') return 'Dust history could not be read, so this is not a finding that none was sent.';
    if (dust === 'logs_clear') {
      return 'No ERC-20 transfer from this address was in the public logs that were read. Those logs do not include a native transfer.';
    }
    if (dust === 'none' && scope === 'client_list') return 'No zero-value or dust transfer from this address was in the list you gave.';
    if (dust === 'flagged') return 'A zero-value or dust transfer from this address was in the last 30 days.';
    return 'Dust history was not a complete reading.';
  }

  /**
   * The warning as HTML. `result` is the poison route's JSON. The destination
   * the person typed is `typed`; it is shown, not replaced.
   */
  function sameAsListed(typed, listed) {
    if (!P || !Array.isArray(listed)) return false;
    var dest = P.canon(typed || '');
    if (!dest) return false;
    for (var i = 0; i < listed.length; i++) {
      if (P.canon(listed[i]) === dest) return true;
    }
    return false;
  }

  function renderWarning(result, typed, listed) {
    if (!result || result.check !== 'address_poisoning') {
      return '<section class="panel g-v01"><p class="s g-err">The lookalike check could not run. Nothing was decided about this address, and it was not changed.</p></section>';
    }
    var dust = dustSentence(result.dust, result.dust_scope);
    var hold = 'The address you typed was not changed, and this does not block the send.';
    if (result.level === 'warn' && result.reason === 'prefix_suffix_match' && result.lookalike_of && P) {
      var pair = P.highlightPair(typed || '', result.lookalike_of);
      return '<section class="panel g-v01" data-poison="warn">'
        + '<div class="verdict v-caution"><span class="ico">⚠️</span><div class="t">This address resembles a past counterparty.</div></div>'
        + '<p class="s">It matches the first 4 and the last 4 characters of an address you listed, and it is not that address. The characters that differ are highlighted, so the two can be compared. The address in the field is still the one you typed.</p>'
        + '<p class="s"><span class="n">You typed</span><br><code class="poison-addr">' + pair.destination + '</code></p>'
        + '<p class="s"><span class="n">A past counterparty</span><br><code class="poison-addr">' + pair.lookalike + '</code></p>'
        + '<p class="s n">' + esc(dust) + '</p>'
        + '<p class="s n">' + esc(hold) + '</p></section>';
    }
    if (result.level === 'warn' && result.reason === 'dust_sender') {
      return '<section class="panel g-v01" data-poison="warn">'
        + '<div class="verdict v-caution"><span class="ico">⚠️</span><div class="t">This address has sent a dust transfer.</div></div>'
        + '<p class="s">A zero-value or dust transfer was received from this address in the last 30 days. That is a common way a lookalike is planted in a history.</p>'
        + '<p class="s"><code class="poison-addr">' + esc(typed || '') + '</code></p>'
        + '<p class="s n">' + esc(hold) + '</p></section>';
    }
    var compared = Number.isFinite(result.compared) ? result.compared : null;
    var lead = compared === 0
      ? 'No valid past counterparty was listed, so this was not a lookalike check.'
      : sameAsListed(typed, listed)
        ? 'This is the same address as a past counterparty you listed, so it is not a lookalike.'
        : 'This address does not match the first 4 and last 4 characters of a past counterparty you listed.';
    return '<section class="panel g-v01" data-poison="ok">'
      + '<p class="s n">' + esc(lead) + ' ' + esc(dust) + ' ' + esc(hold) + '</p></section>';
  }

  function listOf(text) {
    return String(text || '').split(/[\s,]+/).map(function (s) { return s.trim(); }).filter(Boolean);
  }

  function install(doc) {
    var d = doc || (typeof document !== 'undefined' ? document : null);
    if (!d || !d.getElementById || !d.getElementById('g-out') || d.getElementById('g-v01-panel')) return;
    if (!d.getElementById('g-v01-style') && d.head) {
      var style = d.createElement('style');
      style.id = 'g-v01-style';
      style.textContent = '.poison-diff{background:#e6c34b;color:#1a1400;padding:0 1px;border-radius:2px}'
        + '.poison-addr{word-break:break-all;font-size:var(--fs-xs)}';
      d.head.appendChild(style);
    }
    var panel = d.createElement('section');
    panel.className = 'panel';
    panel.id = 'g-v01-panel';
    panel.innerHTML = '<h3 class="g-h">Past counterparties</h3>'
      + '<p class="s n">Optional. One address per line, from your own history. They are sent with the check and not stored.</p>'
      + '<textarea id="g-v01-cps" class="g-in" spellcheck="false" autocomplete="off" aria-label="Past counterparties, one address per line"></textarea>'
      + '<label class="s n"><input id="g-v01-hist" type="checkbox"> Also read public ERC-20 logs on this chain for dust from the destination. Native transfers are not in those logs. Nothing is stored.</label>';
    d.getElementById('g-out').parentNode.insertBefore(panel, d.getElementById('g-out'));
  }

  function onVerdict(ctx) {
    if (typeof document === 'undefined') return;
    var c = ctx || {};
    var prev = document.getElementById('g-v01-out');
    if (prev) prev.remove();
    if (!c.out || c.mode !== 'tx') return;
    var fields = c.fields || {};
    var dest = fields.to || '';
    var box = typeof document !== 'undefined' ? document.getElementById('g-v01-cps') : null;
    var hist = typeof document !== 'undefined' ? document.getElementById('g-v01-hist') : null;
    var holder = document.createElement('div');
    holder.id = 'g-v01-out';
    c.out.insertAdjacentElement('afterend', holder);
    if (!dest) {
      holder.innerHTML = renderWarning(null, dest);
      return;
    }
    var body = {
      destination: dest,
      chain_id: Number(fields.chain) || 8453,
      counterparties: listOf(box ? box.value : ''),
      read_history: !!(hist && hist.checked),
    };
    if (body.read_history && fields.from) body.account = fields.from;
    var old = document.getElementById('g-v01-out');
    if (old && old !== holder) old.remove();
    fetch('/api/guard/poison', {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify(body),
    }).then(function (r) { return r.ok ? r.json() : null; })
      .catch(function () { return null; })
      .then(function (res) {
        var typed = (document.getElementById('g-to') || {}).value;
        if (typed != null && typed !== dest) return;
        holder.innerHTML = renderWarning(res, dest, listOf(box ? box.value : ''));
      });
  }

  return { renderWarning: renderWarning, listOf: listOf, install: install, onVerdict: onVerdict, dustSentence: dustSentence };
}));

(function () {
  'use strict';
  if (typeof document === 'undefined' || !document.getElementById) return;
  if (!window.RCGuardV01 || !document.getElementById('g-go')) return;
  window.RCGuardV01.install(document);
}());
