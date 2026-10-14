/**
 * WHAT BECAME OF A SIGNAL — the words the bot's outcome walk writes, and what
 * the signal panels may say about each.
 *
 * Both producers of the public stream pushed `status: NEW` and nothing ever
 * pushed a second row, so every signal read NEW for good and the stats panel
 * promised "outcomes appear once signals hit target or stop" over a path that
 * did not exist. The bot now walks hourly candles from each signal's
 * publication (bot/core/signal_outcomes.py) and re-sends the row with one of
 * eight words. Five of them are FINAL and only two carry an R, and a panel
 * that knew only WIN / LOSS would have read the other three as unresolved and
 * kept offering a Trade button on a call whose window had closed.
 *
 * A word this page does not know is shown as sent and is NOT actionable: a
 * newer bot's word is more likely to be a new way of ending than a new way of
 * staying open, and offering a trade on a call nobody can read is the loose
 * direction.
 *
 * It is a module rather than a closure in the panel for the reason
 * `dashboard_helpers_are_in_scope.test.js` exists: a renderer reachable only
 * through a DOM event is one no test can drive.
 */
(function (root, factory) {
  const api = factory();
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.SignalStatusModel = api;
}(typeof self !== 'undefined' ? self : this, function () {
  'use strict';

  const PENDING = ['NEW', 'OPEN'];
  const TERMINAL = ['TARGET', 'STOP', 'AMBIGUOUS', 'EXPIRED', 'NO_EXIT', 'UNSCORED'];

  // [chip class, label]. Only the two words that carry an R get a colour:
  // colour is a claim, and a call that was never filled or ended on a bar the
  // candles cannot order has made none.
  const CHIP = {
    NEW: ['', 'NEW'],
    OPEN: ['', 'ENTRY HIT'],
    TARGET: ['chip--up', '✓ TARGET'],
    STOP: ['chip--down', '✗ STOP'],
    AMBIGUOUS: ['', '? AMBIGUOUS'],
    EXPIRED: ['', 'NOT FILLED'],
    NO_EXIT: ['', 'NO EXIT'],
    UNSCORED: ['', 'UNSCORED'],
  };

  /** The word a row carries, upper-cased; an absent one is NEW. */
  function word(s) {
    const w = s && s.status != null && String(s.status).trim() !== ''
      ? String(s.status).trim().toUpperCase() : 'NEW';
    return w;
  }

  /** `{word, cls, label, final, known}` for one signal row. */
  function status(s) {
    const w = word(s);
    const c = CHIP[w];
    if (!c) return { word: w, cls: '', label: w, final: true, known: false };
    return { word: w, cls: c[0], label: c[1], final: TERMINAL.indexOf(w) >= 0, known: true };
  }

  /**
   * When this call stops being live, as epoch ms, or null when the row states
   * no window or one that does not read. The bot states it (`expires_at`,
   * bot/utils/website_sync.py::signal_expires_at): the signal's creation plus
   * the bot's own idea TTL, the time the bot itself would still confirm it.
   * The site cannot read the bot's config, and a second copy of that number
   * here would be a second answer about when a call is stale.
   */
  function liveUntil(s) {
    const v = s ? s.expires_at : null;
    if (v === null || v === undefined || v === '' || typeof v === 'boolean') return null;
    const t = v instanceof Date ? v.getTime() : new Date(v).getTime();
    return isFinite(t) ? t : null;
  }

  function nowMs(now) {
    if (now instanceof Date) return now.getTime();
    return typeof now === 'number' && isFinite(now) ? now : Date.now();
  }

  /**
   * Is the call inside the window the bot stated? Its last instant is still
   * inside, as it is for the bot, which drops an idea only once it is OLDER
   * than its TTL (engine `_expire_pending_ideas`). A row that states no
   * window is not live: the closed direction, which the copy sweep has read
   * since it began. ONE reading of "live" for every door that acts on a call: the
   * Signals stream's Trade and Paper buttons, the Arena's picker, its open
   * route and practice-follow, and the copy picks and their push.
   */
  function live(s, now) {
    const until = liveUntil(s);
    return until !== null && nowMs(now) <= until;
  }

  /**
   * Is this row a live call the server withheld from this viewer? A visitor
   * reads a live call's market and time, and its side and levels once it
   * resolves (app/lib/signal_access.js). Only the server's own marker says
   * so: a row that merely lacks a field is not locked.
   */
  function locked(s) {
    return !!s && s.locked === true;
  }

  /**
   * May a page offer to act on this signal? Only while the call is pending,
   * before any outcome label has arrived (an older server derives `outcome`
   * from the R and sends no status beside it), and inside its window. A
   * locked row carries no levels to act on.
   */
  function actionable(s, now) {
    if (!s || locked(s)) return false;
    const st = status(s);
    return !st.final && (s.outcome === null || s.outcome === undefined) && live(s, now);
  }

  /**
   * What is left of the window, for a countdown: `m:ss` while live (`0:00`
   * in its last second), `null` when the window has closed or none was
   * stated. Never a negative time.
   */
  function leftText(s, now) {
    if (!live(s, now)) return null;
    const left = Math.floor((liveUntil(s) - nowMs(now)) / 1000);
    const sec = left % 60;
    return Math.floor(left / 60) + ':' + (sec < 10 ? '0' : '') + sec;
  }

  /** A finite number, or null. */
  function num(v) {
    if (v === null || v === undefined || v === '' || typeof v === 'boolean') return null;
    const n = Number(v);
    return isFinite(n) ? n : null;
  }

  /** Is the call an element was rendered for still live? Its `data-live-until`. */
  function elementLive(el, now) {
    if (!el || typeof el.getAttribute !== 'function') return false;
    const v = el.getAttribute('data-live-until');
    return v !== null && v !== '' && live({ expires_at: Number(v) }, now);
  }

  /**
   * Bring every countdown and action under `root` up to `now`, and answer how
   * many have closed. An element carrying `data-live-until` (epoch ms) is
   * either a countdown, with a `data-live-word` holding `{t}` and a
   * `data-closed-word`, or an action (a Trade or Paper button), which is
   * hidden and disabled once the window closes. A page renders the actions
   * only for a live call; this keeps them from outliving the window on a page
   * left open, and the click handlers ask `elementLive` again.
   */
  function tick(root, now) {
    if (!root || typeof root.querySelectorAll !== 'function') return 0;
    const els = root.querySelectorAll('[data-live-until]');
    let closed = 0;
    for (let i = 0; i < els.length; i++) {
      const el = els[i];
      const v = el.getAttribute('data-live-until');
      const left = v === null || v === '' ? null : leftText({ expires_at: Number(v) }, now);
      const word = el.getAttribute('data-live-word');
      if (left !== null) {
        if (word) el.textContent = word.replace('{t}', left);
        continue;
      }
      closed += 1;
      if (word) {
        el.textContent = el.getAttribute('data-closed-word') || '';
      } else {
        el.hidden = true;
        el.disabled = true;
      }
    }
    return closed;
  }

  const OTHER = [
    ['NEW', 'waiting for the entry'],
    ['OPEN', 'entry hit, no exit yet'],
    ['EXPIRED', 'not filled'],
    ['AMBIGUOUS', 'ambiguous bar'],
    ['NO_EXIT', 'no exit in a week'],
    ['UNSCORED', 'unscored'],
  ];

  /**
   * The words besides TARGET and STOP, counted, as one line; printed only
   * where it bites. `null` counts are a read that failed and say so, never a
   * row of zeros.
   */
  function otherLine(byStatus) {
    if (byStatus === null || typeof byStatus !== 'object') {
      return 'The other outcomes could not be counted.';
    }
    const parts = [];
    for (const [w, label] of OTHER) {
      const n = num(byStatus[w]);
      if (n && n > 0) parts.push(n + ' ' + label);
    }
    const other = num(byStatus.other);
    if (other && other > 0) parts.push(other + ' in a word this page does not know');
    return parts.length ? parts.join(' · ') : '';
  }

  const BASIS = 'Each signal is walked on hourly candles from when it was published: '
    + 'reaching the target scores its own reward÷risk in R, the stop scores −1R. '
    + 'Gross of fees — a signal has no size. The win rate is over those two outcomes only.';

  /** The mean R as text, or a dash. */
  function avgR(v) {
    const n = num(v);
    if (n === null) return '—';
    return (n > 0 ? '+' : n < 0 ? '−' : '') + Math.abs(n).toFixed(2) + 'R';
  }

  /** Muted unless measured: a mean of 0 is flat, and flat has no colour. */
  function avgCls(v) {
    const n = num(v);
    if (n === null || n === 0) return '';
    return n > 0 ? 'pos' : 'neg';
  }

  /**
   * The stats panel, or null for the panel's empty state. `esc` is the
   * caller's escaper and is required: a renderer that silently stops escaping
   * publishes server text as markup.
   */
  function statsHtml(s, esc) {
    if (typeof esc !== 'function') throw new Error('statsHtml needs an escaper');
    if (!s || typeof s !== 'object') return null;
    const resolved = num(s.resolved) || 0;
    const other = otherLine(s.by_status === undefined ? {} : s.by_status);
    if (!resolved) {
      // Nothing reached its target or stop. Say what IS being tracked, when
      // anything is, rather than an empty state that reads as "no signals".
      if (!other) return null;
      return `<p class="muted small">None has reached its target or stop yet · ${esc(other)}</p>`
        + `<p class="muted small mt-1">${esc(BASIS)}</p>`;
    }
    const wins = num(s.wins) || 0;
    const losses = num(s.losses) || 0;
    const flat = num(s.flat) || 0;
    return `<div class="stat-row">
        <div class="stat"><div class="k">Resolved</div><div class="v">${resolved}</div></div>
        <div class="stat"><div class="k">Win rate</div><div class="v">${num(s.win_rate) !== null ? num(s.win_rate).toFixed(1) + '%' : '—'}</div></div>
        <div class="stat"><div class="k">Avg R (gross)</div><div class="v ${avgCls(s.avg_r)}">${esc(avgR(s.avg_r))}</div></div>
        <div class="stat"><div class="k">Target / Stop</div><div class="v">${wins} / ${losses}${flat ? ` <span class="muted small">/ ${flat} flat</span>` : ''}</div></div>
      </div>`
      + (other ? `<p class="muted small mt-1">${esc(other)}</p>` : '')
      + `<p class="muted small mt-1">${esc(BASIS)}</p>`;
  }

  /**
   * The outcome line of a signal's receipt (/call/<key>), from the word and
   * the time the route sends. The receipt carries no R -- it proves what was
   * called and when -- so the word IS the outcome. The page used to test a
   * `pnl` the route never sends, so every call read "<word> — not resolved
   * yet": a stop-out and a target alike, and a call that was never filled.
   * Colour only where the word carries an R, as everywhere else.
   */
  function receiptOutcome(o, esc) {
    if (typeof esc !== 'function') throw new Error('receiptOutcome needs an escaper');
    const st = status(o);
    const at = o && o.resolved_at ? ' · resolved ' + esc(String(o.resolved_at)) : '';
    if (!st.known) return esc(st.word) + ' — a word this page does not know' + at;
    if (!st.final) return esc(st.label) + ' — not resolved yet';
    const cls = st.cls === 'chip--up' ? 'up-c' : st.cls === 'chip--down' ? 'down-c' : '';
    return (cls ? '<b class="' + cls + '">' : '<b>') + esc(st.label) + '</b>' + at;
  }

  return {
    PENDING: PENDING, TERMINAL: TERMINAL, CHIP: CHIP,
    word: word, status: status, actionable: actionable, locked: locked,
    liveUntil: liveUntil, live: live, leftText: leftText,
    elementLive: elementLive, tick: tick,
    otherLine: otherLine, avgR: avgR, avgCls: avgCls, statsHtml: statsHtml, BASIS: BASIS,
    receiptOutcome: receiptOutcome,
  };
}));
