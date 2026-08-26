/*
 * The arena board: reading the payloads, and rendering what was actually read.
 *
 * A COMPETITION SURFACE IS THE MOST DANGEROUS THING IN THIS REPO TO BUILD.
 * CLAUDE.md's own list of what went wrong is almost entirely leaderboards and
 * track records — win rates, "12 (7W/4L)", an edge-metrics panel whose comment
 * promised "nothing is invented". Every shape in that table is reachable from
 * here: a rank, a return, a win rate, a count of trades, a countdown.
 *
 * So the whole module is built on one distinction the payloads already make
 * and a naive renderer would erase:
 *
 *   ranked_total: 0   nobody has opted in yet. A MEASUREMENT. Say so plainly.
 *   a failed read     we do not know who is competing. NOT the same sentence,
 *                     and the caller paints an error rather than a board.
 *   win_rate_pct null the API's own way of saying "no resolved trades to rate".
 *                     Never 0% — that reads as "lost every one".
 *
 * The readers THROW on a shape they do not recognise rather than returning [],
 * for the reason embed-read.js was written: the signals board announced "No
 * open signals right now" on every load for its entire life because a misread
 * envelope produced an empty array that looked exactly like a real answer.
 */
(function (root, factory) {
  if (typeof module === 'object' && module.exports) module.exports = factory();
  else root.RCArenaView = factory();
}(typeof self !== 'undefined' ? self : this, function () {
  'use strict';

  function UnreadablePayload(what) {
    const e = new Error('unreadable ' + what);
    e.name = 'UnreadablePayload';
    return e;
  }

  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }

  /** A finite number, or null. Never a fallback zero. */
  function num(v) {
    if (v === null || v === undefined || v === '') return null;
    var n = Number(v);
    return isFinite(n) ? n : null;
  }

  // ── readers ─────────────────────────────────────────────────────────────

  /**
   * `{rows:[...], ranked_total}` from /api/arena/leaderboard or /season.
   *
   * An ABSENT `rows` key throws; an EMPTY `rows` array is returned as-is. The
   * two are different facts — "we could not read the board" and "the board is
   * empty" — and this is the exact seam where they get confused.
   */
  function readBoard(j) {
    if (!j || typeof j !== 'object') throw UnreadablePayload('board');
    if (!Array.isArray(j.rows)) throw UnreadablePayload('board rows');
    return j.rows;
  }

  /**
   * The season, or null.
   *
   * `{season: null}` is what the API sends when no season has been authored —
   * a real answer, not a failure. A payload with no `season` KEY AT ALL is a
   * shape we do not understand and throws.
   */
  function readSeason(j) {
    if (!j || typeof j !== 'object') throw UnreadablePayload('season');
    if (!('season' in j)) throw UnreadablePayload('season key');
    return j.season || null;
  }

  /** `{rows:[...]}` from /api/arena/tape. */
  function readTape(j) {
    if (!j || typeof j !== 'object') throw UnreadablePayload('tape');
    if (!Array.isArray(j.rows)) throw UnreadablePayload('tape rows');
    return j.rows;
  }

  // ── formatters, each with an honest absent case ──────────────────────────

  /**
   * A percent return, signed. Null renders as an em dash, NEVER 0.00%.
   *
   * `+0.00%` beside a green stripe for an unreadable return is named in
   * CLAUDE.md as one of the three original sins of this codebase.
   */
  function pct(v, opts) {
    var n = num(v);
    if (n === null) return '—';
    var dp = (opts && typeof opts.dp === 'number') ? opts.dp : 2;
    var s = Math.abs(n).toFixed(dp);
    return (n > 0 ? '+' : n < 0 ? '−' : '') + s + '%';
  }

  /**
   * The direction class for a return.
   *
   * COLOUR IS A CLAIM. A return we could not read gets the muted class, not
   * the green one — `(x || 0) >= 0` is true for a missing number and would
   * paint every unreadable row as a winner.
   */
  function toneFor(v) {
    var n = num(v);
    if (n === null) return 'a-flat';
    if (n > 0) return 'a-up';
    if (n < 0) return 'a-down';
    return 'a-even';      // a real, measured break-even. Not the same as null.
  }

  /**
   * A win rate. The API sends null when there is nothing to rate.
   *
   * "0%" for a trader with no resolved trades reads as "lost every one", which
   * is the `losses = total - wins` shape from CLAUDE.md's table wearing a
   * percent sign.
   */
  function winRate(v) {
    var n = num(v);
    return n === null ? '—' : Math.round(n * 10) / 10 + '%';
  }

  /** A whole count, or an em dash. `0` is a real count and prints as 0. */
  function count(v) {
    var n = num(v);
    return n === null ? '—' : String(Math.round(n));
  }

  /**
   * "6d left", "4h left", "ends soon" — or NULL when it cannot be computed.
   *
   * Null, not "0d left". A countdown of zero on a live competition says it is
   * finishing right now, which is a specific and alarming claim to manufacture
   * from an unparseable date.
   */
  function remaining(endsAt, nowMs) {
    if (endsAt === null || endsAt === undefined || endsAt === '') return null;
    var t = new Date(endsAt).getTime();
    if (!isFinite(t)) return null;
    var now = typeof nowMs === 'number' ? nowMs : Date.now();
    var secs = Math.floor((t - now) / 1000);
    if (secs <= 0) return null;                  // over. The status says so.
    var days = Math.floor(secs / 86400);
    if (days >= 2) return days + 'd left';
    var hrs = Math.floor(secs / 3600);
    if (hrs >= 1) return hrs + 'h left';
    return 'ends soon';
  }

  // ── renderers ───────────────────────────────────────────────────────────

  /**
   * The season header.
   *
   * Three states, because there are three facts: a season is running, a season
   * exists but is not running, or none has been authored. The last one is NOT
   * an error and NOT an empty competition — it is a product that has not
   * started one yet.
   */
  function seasonHtml(season, opts) {
    var o = opts || {};
    if (!season) {
      return '<header class="a-season a-season--none">'
        + '<h1>The Arena</h1>'
        + '<p class="a-sub">No season is running. Standings below are all-time.</p>'
        + '</header>';
    }
    var status = String(season.status || '').toLowerCase();
    var left = status === 'live' ? remaining(season.ends_at, o.nowMs) : null;
    var badge = status === 'live' ? 'live'
      : status === 'upcoming' ? 'starts soon'
        : status === 'ended' ? 'ended' : null;

    return '<header class="a-season">'
      + '<h1>' + esc(season.name || 'The Arena') + '</h1>'
      + '<p class="a-sub">'
      + (badge ? '<span class="a-badge a-badge--' + esc(status) + '">' + esc(badge) + '</span>' : '')
      // The countdown is omitted when unreadable rather than shown as zero.
      + (left ? '<span class="a-left">' + esc(left) + '</span>' : '')
      + '</p></header>';
  }

  /**
   * One standings row.
   *
   * `return_pct` IS coloured, and that is deliberate rather than inconsistent
   * with the signals board. There, a green row would have claimed a trade was
   * winning — something that board does not know. Here the return is the
   * measurement itself: it was computed from closed trades against a uniform
   * stake. Colouring a real measurement is honest; colouring an absent one is
   * not, which is what `toneFor(null)` exists to prevent.
   */
  function standingRow(r) {
    var row = r || {};
    var tone = toneFor(row.return_pct);
    return '<li class="a-row">'
      + '<span class="a-rank">' + esc(count(row.rank)) + '</span>'
      + '<span class="a-handle">' + esc(row.handle || '—') + '</span>'
      + '<span class="a-trades">' + esc(count(row.trades)) + ' trades</span>'
      + '<span class="a-ret ' + tone + '">' + esc(pct(row.return_pct)) + '</span>'
      + '</li>';
  }

  /**
   * The standings list, or an honest statement about why there isn't one.
   *
   * `rows` EMPTY is a measurement — the readers throw on an unreadable payload,
   * so nothing here is standing in for a failed read. What it is NOT is a count
   * of participants, and this function used to print "No one has joined this
   * season yet" on it.
   *
   * Nothing joins a season. The board is derived from trades CLOSED inside the
   * window, and seasonRanking then drops every user with no opt-in handle. So
   * an empty board has two unrelated causes:
   *
   *   closes_in_window 0    nobody has closed a trade since the season opened
   *   ranked_total     0    people have, and none of them shows a public handle
   *
   * Printing "no one has joined" over the second one tells a member who joined,
   * traded and closed that their account did not register. Printing it over the
   * first tells someone who joined an hour ago the same thing. The distinction
   * is the one this module's header is about, and the leaderboard endpoint has
   * carried `ranked_total` for it all along.
   *
   * @param meta optional `{ closes_in_window, ranked_total }`. ABSENT is its own
   *             case: the board is empty and we were not told why, so the copy
   *             states the board is empty and claims nothing about people.
   */
  function standingsHtml(rows, meta) {
    if (rows && rows.length) {
      return '<ol class="a-list">' + rows.map(standingRow).join('') + '</ol>';
    }
    var closes = meta ? num(meta.closes_in_window) : null;
    var ranked = meta ? num(meta.ranked_total) : null;
    if (closes === 0) {
      return '<p class="a-empty">No trades have closed in this season yet.</p>';
    }
    if (closes > 0 && ranked === 0) {
      return '<p class="a-empty">' + esc(count(closes)) + ' trade'
        + (closes === 1 ? '' : 's') + ' closed this season, but nobody has '
        + 'chosen a public handle yet — the board only shows opted-in names.</p>';
    }
    return '<p class="a-empty">Nobody is on the board yet.</p>';
  }

  /** One line of the live tape. */
  function tapeRow(t) {
    var row = t || {};
    var dir = String(row.direction || '').toUpperCase();
    var dirCls = dir === 'LONG' ? 'a-long' : dir === 'SHORT' ? 'a-short' : 'a-flat';
    return '<li class="a-tick">'
      + '<span class="a-tick-dir ' + dirCls + '">' + esc(dir || '—') + '</span>'
      + '<span class="a-tick-sym">' + esc(row.symbol || '—') + '</span>'
      + '<span class="a-tick-who">' + esc(row.handle || '—') + '</span>'
      + '<span class="a-tick-pct ' + toneFor(row.pct) + '">' + esc(pct(row.pct, { dp: 1 })) + '</span>'
      + '</li>';
  }

  function tapeHtml(rows) {
    if (!rows || !rows.length) return '';    // no tape is not worth a sentence
    return '<section class="a-tape"><h2>Latest closes</h2>'
      + '<ul class="a-ticks">' + rows.slice(0, 8).map(tapeRow).join('') + '</ul></section>';
  }

  /**
   * The cast text for sharing the board.
   *
   * §4 applies hardest here — it becomes a public post. Percent and count only,
   * and every part omitted when unreadable. A cast reading "leading with +0.00%"
   * assembled from an absent return would be published under the reader's name.
   */
  function shareText(season, rows, opts) {
    var o = opts || {};
    var top = (rows && rows.length) ? rows[0] : null;
    var bits = [];
    var name = season && season.name ? season.name : null;
    bits.push(name ? 'RUNECLAW Arena · ' + name : 'RUNECLAW Arena');

    if (top && top.handle) {
      var ret = num(top.return_pct);
      bits.push(ret === null
        ? top.handle + ' leads'
        : top.handle + ' leads at ' + pct(top.return_pct));
    }
    var n = num(o.rankedTotal);
    if (n !== null && n > 0) bits.push(n + (n === 1 ? ' trader' : ' traders'));

    var line = bits.join(' · ');
    return o.suffix ? line + '\n\n' + o.suffix : line;
  }

  return {
    readBoard: readBoard,
    readSeason: readSeason,
    readTape: readTape,
    seasonHtml: seasonHtml,
    standingsHtml: standingsHtml,
    standingRow: standingRow,
    tapeHtml: tapeHtml,
    shareText: shareText,
    pct: pct,
    winRate: winRate,
    count: count,
    toneFor: toneFor,
    remaining: remaining,
  };
}));
