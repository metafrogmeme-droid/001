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
 *
 * THE CATEGORIES (2026-10-19). A round carries its `kind` and its `options`,
 * the picks the server takes on it, and this file renders from those: the
 * question in words, the buttons in the round's own order, the words a call
 * is reported in. No list of which words a category takes lives here.
 */
(function (root, factory) {
  const api = factory();
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.RCDuel = api;
}(typeof self !== 'undefined' ? self : this, function () {
  'use strict';

  const IDENT = (k, en) => en;
  /** The categories this renderer can put a question to (lib/duel.js KINDS). */
  const KNOWN_KINDS = ['direction', 'backfade', 'range', 'mover'];

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

  /**
   * The category's name, for a chip on the card. A category this page does
   * not know is named as such, never dressed as one it does.
   */
  function kindLabel(kind, t) {
    const tr = t || IDENT;
    switch (KNOWN_KINDS.indexOf(kind) >= 0 ? kind : null) {
      case 'direction': return tr('duel.k_direction', 'Direction');
      case 'backfade': return tr('duel.k_backfade', 'Back or fade');
      case 'range': return tr('duel.k_range', 'Range');
      case 'mover': return tr('duel.k_mover', 'Bigger mover');
      default: return tr('duel.k_unknown', 'Unknown round type');
    }
  }

  /** The base of an exchange symbol: 'ARBUSDT' -> 'ARB'. */
  function base(sym) {
    return String(sym == null ? '' : sym).replace(/(USDT|USDC|USD)$/i, '') || String(sym == null ? '' : sym);
  }

  /** The band a range round asks about, '±2%'; a dash when the round carries none. */
  function bandText(round) {
    const n = Number((round || {}).threshold_pct);
    return Number.isFinite(n) && n > 0 ? '±' + n + '%' : '—';
  }

  /**
   * The question a round asks, in words. Every category has one, and it is
   * rendered on every surface, so a player meeting a mover for the first
   * time is told what "first" means before they are offered it.
   */
  function questionText(round, t) {
    const tr = t || IDENT;
    const r = round || {};
    switch (r.kind == null ? 'direction' : r.kind) {
      case 'direction':
        return tr('duel.q_direction', 'Higher or lower in {h}h? The Claw’s own call is shown once you commit.')
          .replace('{h}', r.horizon_hours == null ? 24 : r.horizon_hours);
      case 'backfade': {
        const dir = r.agent_direction === 'long' ? tr('duel.b_long', 'LONG')
          : r.agent_direction === 'short' ? tr('duel.b_short', 'SHORT') : '—';
        return tr('duel.q_backfade', 'The Claw called {dir} on {sym}. Back it, or fade it?')
          .replace('{dir}', dir).replace('{sym}', base(r.symbol));
      }
      case 'range':
        return tr('duel.q_range', 'Does {sym} move more than {band} either way in {h}h?')
          .replace('{sym}', base(r.symbol)).replace('{band}', bandText(r))
          .replace('{h}', r.horizon_hours == null ? 24 : r.horizon_hours);
      case 'mover':
        return tr('duel.q_mover', '{a} or {b} — which moves more in {h}h, either way?')
          .replace('{a}', base(r.symbol)).replace('{b}', base(r.symbol_b))
          .replace('{h}', r.horizon_hours == null ? 24 : r.horizon_hours);
      default:
        return tr('duel.q_unknown', 'This round asks a question this page cannot show. Update the app.');
    }
  }

  /**
   * The word on a button, or in "You called …", for one pick on one round.
   * A mover's two sides are its symbols; everything else is a dictionary word.
   */
  function pickLabel(pick, round, t) {
    const tr = t || IDENT;
    const r = round || {};
    switch (pick) {
      case 'long': return tr('duel.b_long', 'LONG');
      case 'short': return tr('duel.b_short', 'SHORT');
      case 'pass': return tr('duel.b_pass', 'PASS');
      case 'back': return tr('duel.b_back', 'BACK THE CLAW');
      case 'fade': return tr('duel.b_fade', 'FADE THE CLAW');
      case 'break': return tr('duel.b_break', 'BREAKS {band}').replace('{band}', bandText(r));
      case 'hold': return tr('duel.b_hold', 'HOLDS {band}').replace('{band}', bandText(r));
      case 'first': return tr('duel.b_more', '{sym} MOVES MORE').replace('{sym}', base(r.symbol));
      case 'second': return tr('duel.b_more', '{sym} MOVES MORE').replace('{sym}', base(r.symbol_b));
      default: return String(pick == null ? '' : pick).toUpperCase();
    }
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

  /**
   * One round on today's card. The buttons are the round's OWN `options`, in
   * its order, so a surface never offers a word the server would refuse; a
   * round that names none (a category this page does not know) offers none.
   * The classic round shows the stance line (hidden, passed, or the call);
   * on a back/fade round the stance is in the question, and a range or a
   * mover round never had one.
   */
  function roundCard(round, t) {
    const tr = t || IDENT;
    const r = round || {};
    const kind = r.kind == null ? 'direction' : r.kind;
    const mine = r.my_call
      ? callState(Object.assign({}, r.my_call, r.my_result || {}), tr)
      : null;
    // A category this page can put a question to. A newer server's round of
    // another kind is not offered, whatever words it sends: a button under a
    // question the page cannot show is a call the player cannot have meant.
    const known = KNOWN_KINDS.indexOf(kind) >= 0;
    const options = !known ? []
      : Array.isArray(r.options) ? r.options : (kind === 'direction' ? ['long', 'short', 'pass'] : []);

    let body;
    if (mine) {
      body = '<div class="d-called">'
        + '<span class="d-mycall">' + esc(tr('duel.you_called', 'You called') + ' ' + pickLabel(r.my_call.pick, r, tr)) + '</span>'
        + '<span class="d-state tone-' + esc(mine.tone) + '">' + esc(mine.text) + '</span>'
        + (mine.note ? '<span class="d-note">' + esc(mine.note) + '</span>' : '')
        + '</div>';
    } else if (r.callable && options.length) {
      body = '<div class="d-actions" data-round="' + esc(r.id) + '">'
        + options.map((pick) => '<button class="d-btn' + (pick === 'pass' ? ' d-btn--pass' : '')
          + '" data-pick="' + esc(pick) + '">' + esc(pickLabel(pick, r, tr)) + '</button>').join('')
        + '</div>';
    } else if (r.callable) {
      body = '<div class="d-note">' + esc(tr('duel.q_unknown', 'This round asks a question this page cannot show. Update the app.')) + '</div>';
    } else {
      body = '<div class="d-note">' + esc(tr('duel.closed', 'This round has closed.')) + '</div>';
    }

    const title = kind === 'mover'
      ? esc(base(r.symbol)) + ' <span class="d-vs">' + esc(tr('duel.vs', 'vs')) + '</span> ' + esc(base(r.symbol_b))
      : esc(r.symbol);
    const stance = kind === 'direction'
      ? '<span class="d-agent">' + esc(agentLabel(r, tr).text) + '</span>' : '';
    return '<article class="d-round d-round--' + esc(kind) + '">'
      + '<header class="d-round-h"><span class="d-sym">' + title + '</span>'
      + '<span class="d-kind">' + esc(kindLabel(kind, tr)) + '</span></header>'
      + '<p class="d-q">' + esc(questionText(r, tr)) + '</p>'
      + stance
      + body
      + '</article>';
  }

  return { esc, base, KNOWN_KINDS, callState, accuracyLabel, marksLabel, agentLabel, kindLabel, questionText, pickLabel, bandText, roundCard };
}));
