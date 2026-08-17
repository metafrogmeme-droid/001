'use strict';
/**
 * The practice sandbox — paper trading with no account, in this browser only.
 *
 * A logged-out visitor previously met "create a free account" and nothing to
 * try. This is the thing to try: real live prices, the real Arena rules, no
 * signup, no server write.
 *
 * IT IS NOT THE ARENA, AND IT SAYS SO IN EVERY STATE.
 *
 * Every Arena trade is sealed at open and folds into that UTC day's Merkle
 * root — that is the product's whole trust claim. A sandbox trade is
 * anonymous, unauthenticated and disposable. Sealing those would put
 * throwaway data into the ledger the claim rests on; NOT sealing them while
 * still calling them Arena trades would create two classes of Arena trade,
 * one of which is quietly unprovable. Either move damages the thing that makes
 * the real Arena worth anything.
 *
 * So this is a different thing with a different name, and the panel says
 * "not sealed · not ranked · this browser only" wherever it can be read.
 *
 * THE RULES ARE NOT REIMPLEMENTED. `arena_engine.js` is the same module the
 * server runs — margin floors, leverage caps, the open limit, isolated-margin
 * PnL, the liquidation price. A practice mode that liquidates at a different
 * price than the real one teaches a habit that costs money the first time it
 * is used for real, so there is no second copy of that arithmetic.
 */
(function () {
  var host = document.getElementById('sandboxPanel');
  if (!host) return;                       // not on this page
  // Logged-out only. A signed-in visitor already has the real Arena on this
  // page — showing a practice panel beside it invites exactly the confusion
  // the naming works to avoid, and the two would sit there disagreeing about
  // what a position is.
  if (window.RC && RC.LOGGED_IN) return;

  var KEY = 'rc_sandbox_v1';
  // Same shape as arena.html's T(): translate by key, with the inline English
  // as a never-blank fallback. `RC.t` does not exist — inventing a helper name
  // would have thrown at the first refusal message, i.e. only on the error
  // path, which is the path nobody exercises by hand.
  var T = function (k, fb) {
    try { return (window.RCI18N && RCI18N.translate(k, RCI18N.getLang())) || fb; }
    catch (e) { return fb; }
  };
  var $ = function (id) { return document.getElementById(id); };
  var esc = function (s) { return (window.RC && RC.esc) ? RC.esc(s) : String(s); };

  // The engine check sits BELOW the helpers on purpose. It was above them, and
  // its own failure message called T() — a `var` function expression, still
  // undefined that early. The panel would have thrown a TypeError instead of
  // reporting the problem, on the one path nobody clicks through by hand.
  var E = window.ArenaEngine;
  if (!E) {
    // Do NOT render an empty or half-working panel: a sandbox that silently
    // drops the rules is worse than no sandbox, because it teaches the wrong
    // ones and looks fine doing it.
    host.hidden = false;
    setMsg('sb.e_engine', 'The practice engine could not load. Reload the page.');
    return;
  }

  var marks = null;      // null means UNREAD — never an empty object
  var state = load();

  function load() {
    try {
      var raw = localStorage.getItem(KEY);
      var s = raw ? JSON.parse(raw) : null;
      if (!s || typeof s.balance !== 'number' || !Array.isArray(s.positions)) throw 0;
      return s;
    } catch (e) {
      return { balance: E.START_BALANCE, positions: [], closed: [], nextId: 1 };
    }
  }
  function save() {
    try { localStorage.setItem(KEY, JSON.stringify(state)); } catch (e) { /* private mode */ }
  }
  function setMsg(key, fb) {
    var el = $('sbMsg'); if (el) el.textContent = T(key, fb);
  }

  // ── prices ──────────────────────────────────────────────────────────────
  // A failed read leaves `marks` null and the panel says the price is
  // unavailable. It must never fall back to the last mark silently, and it
  // must never render 0 — an unreadable price is not a price of zero, and a
  // stale one shown as live is a lie about when it was true.
  function refreshMarks() {
    return fetch('/api/market/tickers', { cache: 'no-store' })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (d) {
        var rows = (d && d.data) || [];
        if (!rows.length) { marks = null; return; }
        var m = {};
        rows.forEach(function (t) {
          var sym = String(t.symbol || '').replace(/USDT.*$/, '');
          var px = Number(t.lastPr != null ? t.lastPr : t.last);
          if (sym && isFinite(px) && px > 0) m[sym] = px;
        });
        marks = Object.keys(m).length ? m : null;
      })
      .catch(function () { marks = null; });
  }
  var markOf = function (sym) {
    return marks && Object.prototype.hasOwnProperty.call(marks, sym) ? marks[sym] : null;
  };

  // ── actions ─────────────────────────────────────────────────────────────
  function open(sym, dir, margin, lev) {
    var px = markOf(sym);
    if (px === null) { setMsg('sb.e_price', 'No live price for that symbol right now — nothing was opened.'); return; }
    // The server's own validator, not a copy of it.
    var v = E.validateOpen({ symbol: sym, direction: dir, margin: margin, leverage: lev },
      state.balance, state.positions.length);
    if (!v.ok) { setMsg('arena.e_' + v.code, v.error); return; }
    state.positions.push({
      id: state.nextId++, symbol: sym, direction: dir,
      entry: px, margin: v.margin, leverage: v.leverage, opened_at: Date.now(),
    });
    state.balance = Math.round((state.balance - v.margin) * 100) / 100;
    save(); setMsg('sb.opened', 'Opened. It lives in this browser and nowhere else.'); render();
  }

  function close(id) {
    var i = state.positions.findIndex(function (p) { return p.id === id; });
    if (i < 0) return;
    var p = state.positions[i];
    var px = markOf(p.symbol);
    if (px === null) { setMsg('sb.e_close', 'No live price to close at — the position is untouched.'); return; }
    var pnl = E.posPnl(p, px);
    state.positions.splice(i, 1);
    state.balance = Math.round((state.balance + p.margin + pnl) * 100) / 100;
    state.closed.unshift({ symbol: p.symbol, direction: p.direction, entry: p.entry,
      exit: px, margin: p.margin, leverage: p.leverage, pnl: pnl, at: Date.now() });
    state.closed = state.closed.slice(0, 20);
    save(); render();
  }

  function reset() {
    state = { balance: E.START_BALANCE, positions: [], closed: [], nextId: 1 };
    save(); render();
  }

  // ── liquidation, on the same rule the Arena uses ─────────────────────────
  function settle() {
    if (!marks) return;                    // cannot judge without prices
    for (var i = state.positions.length - 1; i >= 0; i--) {
      var p = state.positions[i];
      var px = markOf(p.symbol);
      if (px === null) continue;           // this one is unreadable; leave it
      if (E.isLiquidated(p, px)) {
        state.positions.splice(i, 1);
        state.closed.unshift({ symbol: p.symbol, direction: p.direction, entry: p.entry,
          exit: px, margin: p.margin, leverage: p.leverage, pnl: -p.margin,
          at: Date.now(), liquidated: true });
      }
    }
    state.closed = state.closed.slice(0, 20);
    save();
  }

  // ── render ──────────────────────────────────────────────────────────────
  function pct(n) { return (n >= 0 ? '+' : '') + n.toFixed(2) + '%'; }

  function render() {
    settle();
    var eq = marks ? E.equity(state.balance, state.positions, marks) : null;

    // Equity is unknown while prices are unread. It renders as a dash with a
    // muted class — never 0.00%, and never a green stripe. Colour is a claim.
    var eqEl = $('sbEquity'), retEl = $('sbReturn');
    if (eqEl) {
      eqEl.textContent = eq === null ? '—' : eq.toFixed(2);
      eqEl.className = eq === null ? 'muted' : '';
    }
    if (retEl) {
      var r = eq === null ? null : E.returnPct(eq);
      retEl.textContent = r === null ? T('sb.unknown', 'price unavailable') : pct(r);
      retEl.className = r === null ? 'muted' : (r >= 0 ? 'up' : 'down');
    }

    var rows = state.positions.map(function (p) {
      var px = markOf(p.symbol);
      var pnl = px === null ? null : E.posPnl(p, px);
      var pnlPct = pnl === null ? null : (pnl / p.margin) * 100;
      var liq = E.liqPrice(p);
      return '<tr><td>' + esc(p.symbol) + '</td>'
        + '<td>' + esc(p.direction) + ' ' + p.leverage + '×</td>'
        + '<td class="r">' + p.entry + '</td>'
        + '<td class="r">' + (px === null ? '<span class="muted">—</span>' : px) + '</td>'
        + '<td class="r">' + (liq === null ? '<span class="muted">—</span>' : liq.toFixed(4)) + '</td>'
        + '<td class="r ' + (pnlPct === null ? 'muted' : (pnlPct >= 0 ? 'up' : 'down')) + '">'
        + (pnlPct === null ? T('sb.unknown', 'price unavailable') : pct(pnlPct)) + '</td>'
        + '<td><button class="btn btn--sm" data-sb-close="' + p.id + '" '
        + 'data-i18n="sb.b_close">Close</button></td></tr>';
    }).join('');

    var body = $('sbRows');
    if (body) {
      body.innerHTML = rows || '<tr><td colspan="7" class="muted" data-i18n="sb.none">'
        + T('sb.none', 'No open practice positions yet.') + '</td></tr>';
    }
    var stale = $('sbStale');
    if (stale) stale.hidden = !!marks;      // shown only while prices are unread
    try { if (window.RCI18N) RCI18N.apply(host, RCI18N.getLang()); } catch (e) { /* labels keep their inline English */ }
  }

  // ── wire ────────────────────────────────────────────────────────────────
  host.hidden = false;
  host.addEventListener('click', function (ev) {
    var b = ev.target.closest('[data-sb-close]');
    if (b) { close(Number(b.getAttribute('data-sb-close'))); return; }
    if (ev.target.id === 'sbReset') reset();
    if (ev.target.id === 'sbOpen') {
      open(String($('sbSym').value || '').trim().toUpperCase(),
        $('sbDir').value, Number($('sbMargin').value), Number($('sbLev').value));
    }
  });

  refreshMarks().then(render);
  setInterval(function () { refreshMarks().then(render); }, 15000);
}());
