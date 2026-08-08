/**
 * The Daily Duel card and record, as PURE functions.
 *
 * These exist as a module rather than inline in the page for the reason
 * CLAUDE.md gives: a surface with no seam has no assertion. Every claim this
 * feature makes to a player is made here — how a call is doing, what their
 * record is, and what colour to say it in — so every one of them can be driven
 * from a test with a planted state.
 *
 * THE RULE THIS FILE KEEPS. A duel has four distinct states and only two of
 * them are outcomes:
 *
 *   pending     the horizon has not arrived. Nothing is known yet.
 *   unresolved  the horizon passed and no price could be read. Still nothing.
 *   correct/wrong/push   an actual result.
 *
 * Collapsing pending or unresolved into a result is how a call that is merely
 * waiting starts reading as a loss. And because colour is a claim as loudly as
 * the number is, neither of them may be painted green or red: an unknown gets
 * a muted tone, always.
 */
(function (root, factory) {
  const api = factory();
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.RCDuel = api;
}(typeof self !== 'undefined' ? self : this, function () {
  'use strict';

  const IDENT = (k, en) => en;

  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({
      '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
    }[c]));
  }

  /**
   * How one of the caller's calls is doing.
   *
   * `tone` is the colour claim, and it is 'muted' for everything we do not
   * know. A green stripe beside a call whose price we never read would assert
   * a win that nothing in the record supports.
   */
  function callState(entry, t) {
    const tr = t || IDENT;
    const e = entry || {};
    if (e.pending) return { text: tr('duel.st_pending', 'Running'), tone: 'muted', known: false };
    if (e.unresolved || e.state === 'unresolved') {
      return {
        text: tr('duel.st_unresolved', 'Unresolved — no price'),
        tone: 'muted',
        known: false,
        note: tr('duel.st_unresolved_n', 'We could not read a settling price. This call is not counted either way.'),
      };
    }
    switch (e.state) {
      case 'correct':
        return e.beat_agent
          ? { text: tr('duel.st_beat', 'Correct — you beat the Claw'), tone: 'up', known: true }
          : { text: tr('duel.st_correct', 'Correct'), tone: 'up', known: true };
      case 'wrong':
        return { text: tr('duel.st_wrong', 'Wrong'), tone: 'down', known: true };
      case 'push':
        return {
          text: tr('duel.st_push', 'No result'),
          tone: 'muted',
          known: true,
          note: tr('duel.st_push_n', 'The market went nowhere, or you passed on one that moved. Counts for neither side.'),
        };
      default:
        return { text: tr('duel.st_pending', 'Running'), tone: 'muted', known: false };
    }
  }

  /**
   * The headline record.
   *
   * A null rate renders as a dash and a plain explanation, never as 0%. The
   * distinction the player needs is "you have not called enough yet" versus
   * "you called and were wrong", and one glyph decides which of those the page
   * says about them.
   */
  function accuracyLabel(acc, t) {
    const tr = t || IDENT;
    const a = acc || {};
    if (a.pct == null) {
      return {
        text: '—',
        tone: 'muted',
        sub: a.unresolved > 0 && !a.scored
          ? tr('duel.acc_unresolved', 'Nothing has settled yet')
          : tr('duel.acc_none', 'No calls have resolved yet'),
      };
    }
    return {
      text: a.pct + '%',
      tone: a.pct >= 50 ? 'up' : 'down',
      sub: tr('duel.acc_of', 'of {n} settled calls').replace('{n}', a.scored),
    };
  }

  /**
   * The marks total. A partial total travels WITH the count it could not
   * include: "7 marks · 2 still running" is true, and "7 marks" alone is a sum
   * over a set that quietly excluded two rows.
   */
  function marksLabel(marks, t) {
    const tr = t || IDENT;
    const m = marks || {};
    const n = Number(m.marks);
    const out = { text: Number.isFinite(n) ? String(n) : '—', tone: 'muted', sub: '' };
    if (m.pending > 0) {
      out.sub = tr('duel.marks_pending', '{n} still running').replace('{n}', m.pending);
    }
    return out;
  }

  /** What the agent said, or an honest statement that it said nothing. */
  function agentLabel(round, t) {
    const tr = t || IDENT;
    const r = round || {};
    if (!('agent_direction' in r)) {
      return { text: tr('duel.ag_hidden', 'Hidden until you call'), tone: 'muted', revealed: false };
    }
    if (r.agent_direction == null) {
      // A real answer, and a different one from "we are not telling you yet".
      return { text: tr('duel.ag_passed', 'The Claw passed'), tone: 'muted', revealed: true };
    }
    return {
      text: r.agent_direction === 'long'
        ? tr('duel.ag_long', 'The Claw called LONG')
        : tr('duel.ag_short', 'The Claw called SHORT'),
      tone: 'muted',
      revealed: true,
    };
  }

  /** One round on today's card. */
  function roundCard(round, t) {
    const tr = t || IDENT;
    const r = round || {};
    const agent = agentLabel(r, tr);
    const mine = r.my_call
      ? callState(Object.assign({}, r.my_call, r.my_result || {}), tr)
      : null;

    let body;
    if (mine) {
      const call = String(r.my_call.pick || '').toUpperCase();
      body = '<div class="d-called">'
        + '<span class="d-mycall">' + esc(tr('duel.you_called', 'You called') + ' ' + call) + '</span>'
        + '<span class="d-state tone-' + esc(mine.tone) + '">' + esc(mine.text) + '</span>'
        + (mine.note ? '<span class="d-note">' + esc(mine.note) + '</span>' : '')
        + '</div>';
    } else if (r.callable) {
      body = '<div class="d-actions" data-round="' + esc(r.id) + '">'
        + '<button class="d-btn" data-pick="long">' + esc(tr('duel.b_long', 'LONG')) + '</button>'
        + '<button class="d-btn" data-pick="short">' + esc(tr('duel.b_short', 'SHORT')) + '</button>'
        + '<button class="d-btn d-btn--pass" data-pick="pass">' + esc(tr('duel.b_pass', 'PASS')) + '</button>'
        + '</div>';
    } else {
      body = '<div class="d-note">' + esc(tr('duel.closed', 'This round has closed.')) + '</div>';
    }

    return '<article class="d-round">'
      + '<header class="d-round-h"><span class="d-sym">' + esc(r.symbol) + '</span>'
      + '<span class="d-agent">' + esc(agent.text) + '</span></header>'
      + body
      + '</article>';
  }

  return { esc, callState, accuracyLabel, marksLabel, agentLabel, roundCard };
}));
