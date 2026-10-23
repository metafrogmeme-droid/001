/*
 * Scam or Safe? Daily. One decoded signing request, called safe or risky.
 *
 * READ ONLY. The buttons record a call. They do not sign, connect a wallet,
 * build a transaction, or link anywhere. Addresses render masked, with
 * copying and selecting turned off. Amounts render blanked. The explanation
 * is shown only once the server has sealed the call, and only with the
 * server's AI label. A reveal that arrives without that label is withheld.
 *
 * The Pi app mounts create() as a tab when this script was included. The
 * website mounts #scam-root on its own. Neither path keeps the session token.
 */
(function (root, factory) {
  if (typeof module === 'object' && module.exports) module.exports = factory();
  else root.RCScamOrSafe = factory();
}(typeof self !== 'undefined' ? self : this, function () {
  'use strict';

  var AI_LABEL = 'AI-written explanation';
  var MADE_UP = 'Made-up training example, not a check of any real contract or address.';

  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }

  function isMasked(v) {
    return /^0xEXAMPLE\u2026[0-9]{2}$/.test(String(v || ''));
  }

  function isAmount(k) {
    return /^amount$/i.test(String(k || ''));
  }

  /** Stop a copy or cut that starts in a masked address or a blanked amount. */
  function guardCopy(e) {
    var t = e && e.target;
    var el = t && t.nodeType === 3 ? t.parentElement : t;
    if (!el || !el.closest || !el.closest('.sos-mask, .sos-amt')) return false;
    if (typeof e.preventDefault === 'function') e.preventDefault();
    if (e.clipboardData && typeof e.clipboardData.setData === 'function') {
      try { e.clipboardData.setData('text/plain', ''); } catch (err) { /* already blocked */ }
    }
    return true;
  }

  function guardSelect(e) {
    var t = e && e.target;
    var el = t && t.nodeType === 3 ? t.parentElement : t;
    if (!el || !el.closest || !el.closest('.sos-mask, .sos-amt')) return false;
    if (typeof e.preventDefault === 'function') e.preventDefault();
    return true;
  }

  function arm(rootEl) {
    if (!rootEl || rootEl.__sosArmed) return;
    rootEl.__sosArmed = true;
    rootEl.addEventListener('copy', guardCopy);
    rootEl.addEventListener('cut', guardCopy);
    rootEl.addEventListener('selectstart', guardSelect);
  }

  function styleBlock() {
    return '<style>'
      + '.sos-card{border:1px solid rgba(255,255,255,.14);border-radius:12px;padding:16px;margin:12px 0}'
      + '.sos-k{display:block;font-size:11px;letter-spacing:.08em;text-transform:uppercase;opacity:.65;margin-top:8px}'
      + '.sos-v{font-family:ui-monospace,monospace;font-size:15px}'
      + '.sos-mask,.sos-amt{user-select:none;-webkit-user-select:none;-webkit-touch-callout:none}'
      + '.sos-amt{letter-spacing:.18em}'
      + '.sos-actions{display:flex;gap:8px;margin-top:14px}'
      + '.sos-btn{flex:1;padding:12px 8px;border-radius:10px;border:1px solid rgba(255,255,255,.28);background:transparent;color:inherit;font:inherit;cursor:pointer}'
      + '.sos-btn[disabled]{opacity:.45;cursor:default}'
      + '.sos-ai{font-size:12px;font-weight:700;letter-spacing:.04em;text-transform:uppercase;margin-top:14px}'
      + '.sos-note{font-size:13px;opacity:.75;margin-top:8px}'
      + '.sos-q{font-size:18px;margin:0 0 8px}'
      + '</style>';
  }

  function fieldHtml(field) {
    var k = field && field.k;
    var v = field && field.v;
    if (/^0x/i.test(String(v || '')) && !isMasked(v)) v = '[withheld]';
    var cls = 'sos-v';
    if (isAmount(k)) cls += ' sos-amt';
    else if (isMasked(v)) cls += ' sos-mask';
    return '<div class="sos-field"><span class="sos-k">' + esc(k) + '</span>'
      + '<span class="' + cls + '">' + esc(v) + '</span></div>';
  }

  function rightText(right) {
    if (!right || typeof right.pct !== 'number' || typeof right.total !== 'number') {
      return 'The share of right calls could not be read.';
    }
    var line = right.pct + '% of ' + right.total + ' players called it right.';
    if (typeof right.unreadable === 'number' && right.unreadable > 0) {
      line += ' ' + right.unreadable + ' sealed calls could not be read and are not in that percent.';
    }
    return line;
  }

  function streakText(streak) {
    if (!streak || typeof streak.current !== 'number') return 'Your streak could not be read.';
    var unit = streak.current === 1 ? 'day' : 'days';
    var best = typeof streak.best === 'number' ? streak.best : null;
    var line = 'Streak: ' + streak.current + ' ' + unit;
    if (best != null) line += ' (best ' + best + ')';
    line += '. A streak is consecutive UTC days you called it right.';
    return line;
  }

  function rankText(rank) {
    if (!rank || typeof rank.place !== 'number' || typeof rank.players !== 'number') {
      return 'Your place on the streak board could not be read.';
    }
    return 'Streak board: place ' + rank.place + ' of ' + rank.players + '.';
  }

  /**
   * One screen. `view.phase` is loading, unread, off, closed, pick, or reveal.
   * The reveal phase prints the explanation only when `ai_label` is present.
   */
  function render(view) {
    var v = view || {};
    var head = styleBlock() + '<section class="sos"><h2 class="sos-q">Scam or Safe?</h2>';
    var foot = '<p class="sos-note">Read only. Nothing here can be signed, and a masked account cannot be copied out.</p></section>';
    if (v.phase === 'loading') return head + '<p class="sos-note">Loading today’s request…</p>' + foot;
    if (v.phase === 'off') return head + '<p class="sos-note">This game is not open.</p>' + foot;
    if (v.phase === 'unread') {
      return head + '<p class="sos-note">Today’s request could not be read. Nothing was sealed.</p>' + foot;
    }
    if (v.phase === 'denied') {
      return head + '<p class="sos-note">Sign in to call today’s request. The request is the same for everyone this UTC day.</p>' + foot;
    }
    var puzzle = v.puzzle;
    if (!puzzle || !Array.isArray(puzzle.fields)) {
      return head + '<p class="sos-note">Today’s request could not be read. Nothing was sealed.</p>' + foot;
    }
    var fields = puzzle.fields.map(fieldHtml).join('');
    var card = '<div class="sos-card"><span class="sos-k">' + esc(puzzle.chain_label || puzzle.chain || '')
      + ' · ' + esc(puzzle.method || '') + '</span>' + fields + '</div>';
    if (v.phase === 'reveal') {
      var called = v.pick === 'safe' || v.pick === 'risky'
        ? '<p>You called this request ' + esc(v.pick) + '.</p>' : '';
      var verdict = v.verdict === 'safe' || v.verdict === 'risky'
        ? '<p>Verdict: ' + esc(v.verdict) + '.</p>' : '<p>The verdict could not be read, so it is not shown.</p>';
      var match = v.correct === true ? '<p>That matches the verdict.</p>'
        : v.correct === false ? '<p>That does not match the verdict.</p>'
          : '<p>Whether it matched could not be read.</p>';
      var explain = v.ai_label
        ? '<p class="sos-ai">' + esc(v.ai_label) + '</p><p>' + esc(v.explanation || '') + '</p>'
          + (v.ai_note ? '<p class="sos-note">' + esc(v.ai_note) + '</p>' : '')
        : '<p class="sos-note">The explanation is withheld until it carries its AI label.</p>';
      var madeUp = '<p class="sos-note">' + esc(MADE_UP) + '</p>';
      return head + card + called + verdict + match + madeUp + explain
        + '<p>' + esc(rightText(v.right)) + '</p>'
        + '<p>' + esc(streakText(v.streak)) + '</p>'
        + '<p>' + esc(rankText(v.rank)) + '</p>'
        + '<p class="sos-note">Sealed. This call cannot be changed.</p>' + foot;
    }
    var closed = v.phase === 'closed';
    var buttons = closed ? '' : '<div class="sos-actions">'
      + '<button type="button" class="sos-btn" data-sos="safe">Safe</button>'
      + '<button type="button" class="sos-btn" data-sos="risky">Risky</button>'
      + '</div>';
    var ask = closed
      ? '<p class="sos-note">Today’s request has closed. Nothing new can be sealed.</p>'
      : '<p>Call it safe or risky. You confirm nothing else: the call is sealed, then the explanation is shown, and it cannot be changed.</p>';
    return head + card + ask + buttons + foot;
  }

  function viewFromBody(body, status) {
    if (status === 404) return { phase: 'off' };
    if (status === 401) return { phase: 'denied' };
    if (!body || body.ok === false || !body.puzzle) return { phase: 'unread' };
    if (body.sealed) {
      return {
        phase: 'reveal',
        puzzle: body.puzzle,
        pick: body.pick,
        verdict: body.verdict,
        correct: body.correct,
        explanation: body.explanation,
        ai_label: body.ai_label,
        ai_note: body.ai_note,
        right: body.right,
        streak: body.streak,
        rank: body.rank,
      };
    }
    return { phase: 'pick', puzzle: body.puzzle, puzzle_id: body.puzzle.id };
  }

  function create() {
    var view = { phase: 'loading' };
    var ctx = null;
    var busy = false;

    function draw() { if (ctx) ctx.render(); }

    function load() {
      if (!ctx) return Promise.resolve();
      return ctx.api('/api/scam-or-safe/today').then(function (r) {
        view = viewFromBody(r && r.body, r && r.status);
        draw();
      }, function () {
        view = { phase: 'unread' };
        draw();
      });
    }

    function signedIn(c) {
      ctx = c;
      load();
    }

    function pick(which) {
      if (busy || !ctx || view.phase !== 'pick') return;
      busy = true;
      var puzzleId = view.puzzle_id;
      view = { phase: 'loading' };
      draw();
      ctx.api('/api/scam-or-safe/pick', {
        method: 'POST',
        body: { pick: which, puzzle_id: puzzleId },
      }).then(function (r) {
        busy = false;
        if (r && r.body && r.body.error === 'moved') return load();
        view = viewFromBody(r && r.body, r && r.status);
        if (view.phase === 'pick') view = { phase: 'unread' };
        draw();
      }, function () {
        busy = false;
        view = { phase: 'unread' };
        draw();
      });
    }

    function click(target) {
      var btn = target && target.closest ? target.closest('.sos [data-sos]') : null;
      if (!btn) return false;
      pick(btn.getAttribute('data-sos'));
      return true;
    }

    return {
      place: 'main',
      tab: 'scam',
      label: 'Scam or Safe',
      icon: '\uD83D\uDD0D',
      badge: function () { return view.phase === 'pick' ? 1 : 0; },
      html: function () { return render(view); },
      click: click,
      signedIn: signedIn,
      reload: load,
      mounted: arm,
    };
  }

  function authHeaders() {
    // The session helper every other page uses (app.js). A cookie session
    // sends no Bearer header; the cookie travels on credentials: 'same-origin'.
    // Reading localStorage here would decide "signed out" for that session.
    var rc = typeof window !== 'undefined' ? window.RC : null;
    if (rc && typeof rc.authHeaders === 'function') return rc.authHeaders();
    return {};
  }

  function mount(el) {
    if (!el) return;
    arm(el);
    function paint(view) { el.innerHTML = render(view); }
    function api(url, opts) {
      var o = opts || {};
      var headers = authHeaders();
      if (o.body) headers['Content-Type'] = 'application/json';
      return fetch(url, {
        method: o.method || 'GET',
        credentials: 'same-origin',
        headers: headers,
        body: o.body ? JSON.stringify(o.body) : undefined,
      }).then(function (r) {
        return r.json().then(function (body) { return { status: r.status, body: body }; }, function () {
          return { status: r.status, body: null };
        });
      });
    }
    paint({ phase: 'loading' });
    var currentId = null;
    function apply(res) {
      var view = viewFromBody(res && res.body, res && res.status);
      if (view.phase === 'pick') currentId = view.puzzle_id;
      paint(view);
    }
    api('/api/scam-or-safe/today').then(apply, function () { paint({ phase: 'unread' }); });
    el.addEventListener('click', function (e) {
      var btn = e.target && e.target.closest ? e.target.closest('[data-sos]') : null;
      if (!btn) return;
      var which = btn.getAttribute('data-sos');
      paint({ phase: 'loading' });
      api('/api/scam-or-safe/pick', { method: 'POST', body: { pick: which, puzzle_id: currentId } })
        .then(apply, function () { paint({ phase: 'unread' }); });
    });
  }

  function boot() {
    if (typeof document === 'undefined') return;
    var el = document.getElementById('scam-root');
    if (el) mount(el);
  }
  if (typeof document !== 'undefined') {
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot);
    else boot();
  }

  return {
    create: create, mount: mount, render: render, guardCopy: guardCopy, guardSelect: guardSelect,
    viewFromBody: viewFromBody, rightText: rightText, streakText: streakText, rankText: rankText,
    AI_LABEL: AI_LABEL, MADE_UP: MADE_UP,
  };
}));
