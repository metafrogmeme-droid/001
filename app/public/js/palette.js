/**
 * RUNECLAW command palette — the whole universe from anywhere. Ctrl+K (or
 * Cmd+K, or `/` outside an input) opens an overlay that jumps to any room,
 * and understands what you paste:
 *   - 0x + 40 hex        → X-ray the address's allowances
 *   - name.eth           → same, resolved by the page it lands on
 *   - base58 (32-44)     → the Solana side of the X-ray
 *   - a call/trade key   → open that sealed receipt
 * Self-contained (no deps), translated through RCI18N with English
 * fallbacks, aria-labelled, Esc closes, arrows + Enter navigate, and the
 * fade respects prefers-reduced-motion. Navigation only — the palette never
 * performs an action itself, it just takes you to the room that does.
 */
(function () {
  'use strict';
  if (window.__rcPalette) return;
  window.__rcPalette = true;

  var T = function (k, en) {
    try { return (window.RCI18N && RCI18N.translate(k, RCI18N.getLang())) || en; }
    catch (e) { return en; }
  };
  var reduce = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  // The rooms — labels ride existing nav.* keys, so the palette speaks all
  // 14 languages on day one.
  var ROOMS = [
    ['🏠', null, 'RUNECLAW', '/', 'home landing start'],
    ['⚔️', 'nav.dashboard', 'Dashboard', '/dashboard', 'app trade account'],
    ['🎪', 'nav.arena', 'Arena', '/arena', 'paper trading leaderboard season'],
    ['📖', 'nav.learn', 'Study Room', '/learn', 'diary lessons tutor quiz learn'],
    ['🎮', 'nav.command', 'Command Deck', '/command', 'achievements quests streaks'],
    ['🗿', 'nav.rune', 'Rune', '/rune', 'nft soulbound mint collection'],
    ['🛡️', 'nav.guardian', 'Guardian', '/guardian', 'safety suite modules'],
    ['🧱', 'nav.firewall', 'Firewall', '/firewall', 'scan sign xray calldata decode'],
    ['🩻', 'nav.approvals', 'Allowance X-ray', '/approvals', 'approvals revoke spender delegate'],
    ['🪂', 'nav.escape', 'Escape Agent', '/escape', 'exit unwind emergency plan'],
    ['🌀', 'nav.stress', 'Stress Lab', '/stress', 'twin crash shock liquidation'],
    ['📡', 'nav.sentinel', 'Risk Sentinel', '/sentinel', 'systemic crowding'],
    ['🛰️', 'nav.flight', 'Flight Recorder', '/flight', 'ledger decisions black box'],
    ['⛽', 'nav.gas', 'Live Gas', '/gas', 'gwei fees floor chains'],
    ['🔐', 'nav.proof', 'Proof of PnL', '/proof', 'verify performance sealed'],
    ['🌳', null, 'Daily seal roots', '/roots', 'merkle anchor base'],
    ['🧾', null, 'Verify everything', '/provable', 'provable contract quickstart'],
  ];

  var ADDR_RE = /^0x[0-9a-fA-F]{40}$/;
  var ENS_RE = /^[a-z0-9-]+(\.[a-z0-9-]+)*\.eth$/i;
  var SOL_RE = /^[1-9A-HJ-NP-Za-km-z]{32,44}$/;
  // Same charset the receipt routes accept (frame_meta KEY_RE) — longer than
  // an address so the two shapes never collide.
  var KEY_RE = /^[A-Za-z0-9:_.-]{4,128}$/;

  var wrap = null, input = null, list = null, items = [], sel = 0;

  function smartEntries(q) {
    var out = [];
    if (ADDR_RE.test(q) || ENS_RE.test(q) || (SOL_RE.test(q) && !q.startsWith('0x'))) {
      out.push(['🩻', null, T('pal.addr', 'X-ray this address — what can spend its tokens'),
        '/approvals?a=' + encodeURIComponent(q), '']);
    }
    if (!ADDR_RE.test(q) && !ENS_RE.test(q) && KEY_RE.test(q)
      && (q.indexOf(':') >= 0 || /^[a-z0-9]{8,}$/i.test(q)) && q.length >= 8) {
      out.push(['🔏', null, T('pal.call', 'Open this key as a sealed receipt'),
        '/call/' + encodeURIComponent(q), '']);
    }
    return out;
  }

  function render(q) {
    q = (q || '').trim();
    var ql = q.toLowerCase();
    items = smartEntries(q).concat(ROOMS.filter(function (r) {
      if (!ql) return true;
      var label = r[1] ? T(r[1], r[2]) : r[2];
      return (label + ' ' + r[2] + ' ' + r[4] + ' ' + r[3]).toLowerCase().indexOf(ql) >= 0;
    }));
    sel = 0;
    list.innerHTML = items.length ? items.map(function (r, i) {
      var label = r[1] ? T(r[1], r[2]) : r[2];
      return '<div class="rcp-it' + (i === sel ? ' on' : '') + '" data-i="' + i + '" role="option" aria-selected="' + (i === sel) + '">'
        + '<span class="rcp-e">' + r[0] + '</span><span>' + label.replace(/[<>&]/g, '') + '</span>'
        + '<span class="rcp-h">' + r[3].replace(/[<>&]/g, '') + '</span></div>';
    }).join('') : '<div class="rcp-empty">' + T('pal.empty', 'Nothing matches. Esc closes.') + '</div>';
  }

  function go(i) {
    var it = items[i];
    if (it) location.href = it[3];
  }

  function close() {
    if (wrap) { wrap.remove(); wrap = null; }
    document.removeEventListener('keydown', onNav, true);
  }

  function onNav(e) {
    if (e.key === 'Escape') { e.preventDefault(); close(); return; }
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      e.preventDefault();
      if (!items.length) return;
      sel = (sel + (e.key === 'ArrowDown' ? 1 : items.length - 1)) % items.length;
      var els = list.querySelectorAll('.rcp-it');
      els.forEach(function (el, i) {
        el.classList.toggle('on', i === sel);
        el.setAttribute('aria-selected', String(i === sel));
      });
      if (els[sel]) els[sel].scrollIntoView({ block: 'nearest' });
      return;
    }
    if (e.key === 'Enter') { e.preventDefault(); go(sel); }
  }

  function open() {
    if (wrap) return;
    wrap = document.createElement('div');
    wrap.id = 'rcPalette';
    wrap.setAttribute('role', 'dialog');
    wrap.setAttribute('aria-label', T('pal.aria', 'Command palette'));
    wrap.innerHTML = '<style>'
      + '#rcPalette{position:fixed;inset:0;z-index:9999;background:rgba(5,6,10,.72);display:flex;align-items:flex-start;justify-content:center;padding-top:12vh;'
      + (reduce ? '' : 'animation:rcpFade .15s ease-out;')
      + '}@keyframes rcpFade{from{opacity:0}}'
      + '#rcPalette .rcp-box{width:min(560px,92vw);background:var(--surface,#12141c);border:1px solid var(--line-2,#2a2e3d);border-radius:12px;overflow:hidden;box-shadow:0 18px 60px rgba(0,0,0,.5)}'
      + '#rcPalette input{width:100%;background:transparent;border:0;outline:0;color:var(--text-1,#e8eaf2);font:inherit;font-size:15px;padding:14px 16px;border-bottom:1px solid var(--line,#222636)}'
      + '#rcPalette .rcp-ls{max-height:46vh;overflow-y:auto;padding:6px}'
      + '#rcPalette .rcp-it{display:flex;gap:10px;align-items:center;padding:9px 10px;border-radius:8px;color:var(--text-2,#aab0c0);font-size:14px;cursor:pointer}'
      + '#rcPalette .rcp-it.on{background:var(--surface-2,#1a1d28);color:var(--text-1,#e8eaf2)}'
      + '#rcPalette .rcp-e{width:22px;text-align:center}'
      + '#rcPalette .rcp-h{margin-left:auto;font-size:11px;color:var(--text-3,#6b7183);font-family:monospace}'
      + '#rcPalette .rcp-empty{padding:14px;color:var(--text-3,#6b7183);font-size:13px}'
      + '</style>'
      + '<div class="rcp-box"><input type="text" spellcheck="false" autocomplete="off" '
      + 'placeholder="' + T('pal.ph', 'Type a room, an address, a call key…').replace(/"/g, '&quot;') + '">'
      + '<div class="rcp-ls" role="listbox"></div></div>';
    document.body.appendChild(wrap);
    input = wrap.querySelector('input');
    list = wrap.querySelector('.rcp-ls');
    render('');
    input.focus();
    input.addEventListener('input', function () { render(input.value); });
    list.addEventListener('click', function (e) {
      var it = e.target.closest('.rcp-it');
      if (it) go(Number(it.getAttribute('data-i')));
    });
    wrap.addEventListener('mousedown', function (e) { if (e.target === wrap) close(); });
    document.addEventListener('keydown', onNav, true);
  }

  document.addEventListener('keydown', function (e) {
    var inField = /^(INPUT|TEXTAREA|SELECT)$/.test((e.target && e.target.tagName) || '')
      || (e.target && e.target.isContentEditable);
    if ((e.key === 'k' || e.key === 'K') && (e.ctrlKey || e.metaKey)) {
      e.preventDefault();
      wrap ? close() : open();
    } else if (e.key === '/' && !inField && !wrap && !e.ctrlKey && !e.metaKey && !e.altKey) {
      e.preventDefault();
      open();
    }
  });
})();
