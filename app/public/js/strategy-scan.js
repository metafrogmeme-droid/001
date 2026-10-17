/**
 * "Scan now" on a strategy's page: a rule the live bot does not run, read by
 * the bot on its markets' last closed 1h bars (bot/core/strategy_scan.py).
 *
 * One source, so a failed read paints an error state with the reason the bot
 * named (an exception class, never a message) and never an empty board. A
 * market the bot could not read is listed apart, never scored. Sides are
 * chips without colour: a side is not a profit. Nothing here places anything.
 */
(function (root, factory) {
  const api = factory();
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.StrategyScan = api;
}(typeof self !== 'undefined' ? self : this, function () {
  'use strict';

  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }
  // A percent with its sign, or "unread". `scale` turns a ratio into percent.
  function pctText(v, scale) {
    if (typeof v !== 'number' || !isFinite(v)) return 'unread';
    const x = v * (scale || 1);
    return (x > 0 ? '+' : '') + x.toFixed(2) + '%';
  }
  function sideText(s) { return s ? String(s).toLowerCase() : 'no side'; }

  function classHtml(c) {
    const head = '<div style="margin-top:8px"><b>' + esc(c.name === 'all' ? '' : c.name + ' — ')
      + esc(c.leader) + '</b>: ' + esc(sideText(c.side))
      + ' <span class="muted" style="font-size:11px">(' + esc(c.regime) + ')</span></div>';
    const top = (c.board || []).slice(0, 5).map(function (r) {
      return '<div class="rec-row" style="font-size:12px">' + esc(r.symbol)
        + ' <span class="chip" style="font-size:10px">' + esc(sideText(r.side)) + '</span> '
        + esc(String(r.points)) + '/' + esc(String(r.out_of))
        + (r.skip ? ' <span class="muted">— ' + esc(r.skip) + '</span>' : '') + '</div>';
    }).join('');
    const plan = c.plan
      ? '<div style="font-size:12px">Would rest a ' + (c.plan.side === 'LONG' ? 'buy' : 'sell')
        + ' limit on <b>' + esc(c.plan.symbol) + '</b> at ' + esc(String(c.plan.limit))
        + ', stop ' + esc(String(c.plan.stop)) + ', first target ' + esc(String(c.plan.target)) + '</div>'
      : '';
    return head + top + plan;
  }

  function rowHtml(rule, r) {
    let cells = '<b>' + esc(r.symbol) + '</b> ';
    if (rule === 'momentum_trend') {
      cells += 'momentum ' + esc(pctText(r.momentum_pct)) + ' <span class="muted">— ' + esc(r.reason) + '</span>';
    } else if (rule === 'daily_trend') {
      cells += (r.score == null ? esc(r.reason)
        : 'trend score ' + esc(Number(r.score).toFixed(2)) + ', weight '
          + (r.weight_pct == null ? 'unread' : esc(Number(r.weight_pct).toFixed(1)) + '%'));
    } else if (rule === 'vol_rotation') {
      cells += 'momentum ' + esc(pctText(r.momentum, 100)) + ', '
        + (r.above_average == null ? 'its average unread' : (r.above_average ? 'above' : 'not above') + ' its average')
        + ', ' + (r.qualifies ? 'qualifies' : 'does not qualify');
    } else {
      cells += '<span class="chip" style="font-size:10px">' + esc(sideText(r.side)) + '</span>';
    }
    return '<div class="rec-row" style="font-size:12px;padding:3px 0">' + cells + '</div>';
  }

  // A board the server withheld from a visitor (app/lib/signal_access.js):
  // which markets it read, and the sentence saying the reading is a
  // member's. Never an empty board, which would read as "no side anywhere".
  function lockedBody(b) {
    const read = (b.read || []).map(function (s) { return esc(s); }).join(', ');
    return (read ? '<p style="font-size:12px">Read: ' + read + '</p>' : '')
      + '<p style="margin-top:8px"><b>' + esc(b.verdict) + '</b></p>'
      + '<p><a class="btn btn--sm" href="/">Sign in to see the trade</a></p>';
  }

  // The board the bot returned, as markup.
  function boardHtml(b) {
    const unreadLocked = (b.unread || []).length
      ? '<p class="muted" style="font-size:11px">Unread: '
        + b.unread.map(function (u) { return esc(u.symbol) + ' (' + esc(u.why) + ')'; }).join(', ') + '.</p>'
      : '';
    if (b.locked === true) {
      return '<p class="prov">At the ' + esc(b.bar_close) + ' close.</p>' + lockedBody(b) + unreadLocked
        + '<p class="prov">Nothing is placed or queued: the live bot does not run this rule.</p>';
    }
    const body = b.rule === 'limit_entry'
      ? (b.classes || []).map(classHtml).join('')
      : (b.rows || []).map(function (r) { return rowHtml(b.rule, r); }).join('');
    const unread = (b.unread || []).length
      ? '<p class="muted" style="font-size:11px">Unread, so not on the board: '
        + b.unread.map(function (u) { return esc(u.symbol) + ' (' + esc(u.why) + ')'; }).join(', ') + '.</p>'
      : '';
    return '<p class="prov">At the ' + esc(b.bar_close) + ' close.</p>' + body
      + '<p style="margin-top:8px"><b>' + esc(b.verdict) + '</b></p>' + unread
      + '<p class="prov">Nothing is placed or queued: the live bot does not run this rule.</p>';
  }

  // The section, only for a card that names a scan door.
  function blockHtml(a) {
    if (!a || !a.scan) return '';
    return '<div class="sc" id="scanBox" style="margin-top:14px">'
      + '<div class="k" style="font-size:10px;text-transform:uppercase;letter-spacing:.03em;color:var(--text-3);margin-bottom:8px">Scan now</div>'
      + '<p class="prov">Runs this rule on its markets’ last closed 1h bars and shows what it would do. '
      + 'Nothing is placed. In the bot: <code>' + esc(a.scan) + '</code></p>'
      + '<button class="btn btn--sm" id="scanBtn" type="button">Scan now</button>'
      + '<div id="scanOut" aria-live="polite"></div></div>';
  }

  // Read one board into `out` (anything with innerHTML). The id rides as a
  // query parameter. A failed read says why and shows no board.
  function run(id, out, fetchFn) {
    out.innerHTML = '<p class="muted" style="font-size:12px">Reading the last closed bars…</p>';
    return fetchFn('/api/public/strategies/scan?id=' + encodeURIComponent(id),
      { headers: { Accept: 'application/json' } })
      .then(function (r) {
        return r.json().catch(function () { return null; }).then(function (d) {
          if (!r.ok || !d || !d.board) throw new Error(d && d.detail ? d.detail : 'HTTP ' + r.status);
          return d.board;
        });
      })
      .then(function (b) { out.innerHTML = boardHtml(b); })
      .catch(function (e) {
        out.innerHTML = '<p class="muted" style="font-size:12px">The scan could not be read ('
          + esc(e && e.message ? e.message : 'no answer') + '). No board is shown rather than a guess.</p>';
      });
  }

  return { boardHtml: boardHtml, blockHtml: blockHtml, run: run, pctText: pctText };
}));
