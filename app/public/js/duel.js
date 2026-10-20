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
  const KNOWN_KINDS = ['direction', 'backfade', 'range', 'mover', 'sports'];

  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({
      '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
    }[c]));
  }

  /**
   * Time to a horizon, in words: "21h 10m", "40m", or "any moment" once it
   * has passed; null when the horizon cannot be read, so the caller can say
   * "Running" instead of inventing a time.
   */
  function timeLeftText(resolvesAt, now, t) {
    const tr = t || IDENT;
    const at = Date.parse(resolvesAt || '');
    if (!Number.isFinite(at)) return null;
    const ms = at - (now == null ? Date.now() : new Date(now).getTime());
    if (ms <= 0) return tr('duel.t_soon', 'any moment');
    const mins = Math.ceil(ms / 60000);
    const h = Math.floor(mins / 60);
    const m = mins % 60;
    if (h === 0) return tr('duel.t_m', '{m}m').replace('{m}', m);
    return tr('duel.t_hm', '{h}h {m}m').replace('{h}', h).replace('{m}', m);
  }

  /** A glyph for the category chip. Decoration, not a claim: no colour here. */
  function kindIcon(kind) {
    switch (kind) {
      case 'direction': return '\u2195';   // ↕
      case 'backfade': return '\u2694';    // ⚔
      case 'range': return '\u2194';       // ↔
      case 'mover': return '\u26A1';       // ⚡
      case 'sports': return '\u26BD';      // ⚽
      default: return '?';
    }
  }

  /** A glyph before a button's word, for the picks that have a natural one. */
  function pickIcon(pick) {
    switch (pick) {
      case 'long': return '\u25B2';    // ▲
      case 'short': return '\u25BC';   // ▼
      case 'pass': return '\u2013';    // –
      case 'back': return '\u2713';    // ✓
      case 'fade': return '\u2715';    // ✕
      case 'break': return '\u21C5';   // ⇅
      case 'hold': return '\u21D4';    // ⇔
      default: return '';
    }
  }

  /**
   * How one of the caller's calls is doing.
   *
   * `tone` is the colour claim, and it is 'muted' for everything we do not
   * know. A green stripe beside a call whose price we never read would assert
   * a win that nothing in the record supports.
   */
  function callState(entry, t, now) {
    const tr = t || IDENT;
    const e = entry || {};
    if (e.pending) {
      // A football call is settled on the final score when the feed reports
      // it, not at a clock time: a countdown would name a moment nobody set.
      if (e.kind === 'sports') return { text: tr('duel.st_fulltime', 'Settles after full time'), tone: 'muted', known: false };
      // A running call says WHEN it settles where the horizon can be read;
      // a horizon that cannot be read is "Running", never a made-up time.
      const left = timeLeftText(e.resolves_at, now, tr);
      return {
        text: left ? tr('duel.st_settles', 'Settles in {t}').replace('{t}', left) : tr('duel.st_pending', 'Running'),
        tone: 'muted',
        known: false,
      };
    }
    if (e.unresolved || e.state === 'unresolved') {
      return {
        text: tr('duel.st_unresolved', 'Unresolved — no price'),
        tone: 'muted',
        known: false,
        note: tr('duel.st_unresolved_n', 'We could not read a settling price. This call is not counted either way.'),
      };
    }
    // A second source disagreed with the settle price past the tolerance:
    // the verdict stands (the venue's close settles), and the note says a
    // reader may want to check the receipt. Only a true `disputed` says so;
    // false is agreement and null is "not checked", and neither is a note.
    const dispute = e.disputed === true
      ? ' \u00b7 ' + tr('duel.st_disputed', 'disputed') : '';
    const disputeNote = e.disputed === true
      ? tr('duel.st_disputed_n', 'A second price source disagreed with the settling price. The venue’s close settles; the receipt shows both.') : null;
    const withDispute = (r) => {
      if (!dispute) return r;
      const out = Object.assign({}, r, { text: r.text + dispute, disputed: true });
      out.note = r.note ? r.note + ' ' + disputeNote : disputeNote;
      return out;
    };
    switch (e.state) {
      case 'correct':
        return withDispute(e.beat_agent
          ? { text: tr('duel.st_beat', 'Correct — you beat the Claw'), tone: 'up', known: true }
          : { text: tr('duel.st_correct', 'Correct'), tone: 'up', known: true });
      case 'wrong':
        return withDispute({ text: tr('duel.st_wrong', 'Wrong'), tone: 'down', known: true });
      case 'push':
        return withDispute({
          text: tr('duel.st_push', 'No result'),
          tone: 'muted',
          known: true,
          note: tr('duel.st_push_n', 'The market went nowhere, or you passed on one that moved. Counts for neither side.'),
        });
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
    // `how` is what a mark is, beside the number: a reader who sees
    // "Accuracy 60% of 5" next to "Marks 4" cannot reconcile the two without
    // the rule. `sub` stays what is outstanding, and empty when nothing is.
    const out = { text: Number.isFinite(n) ? String(n) : '—', tone: 'muted', sub: '',
      how: tr('duel.marks_how', '1 per correct call, 2 for beating the Claw') };
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
      case 'sports': return tr('duel.k_sports', 'Football');
      default: return tr('duel.k_unknown', 'Unknown round type');
    }
  }

  /** The base of an exchange symbol: 'ARBUSDT' -> 'ARB'. */
  function base(sym) {
    return String(sym == null ? '' : sym).replace(/(USDT|USDC|USD)$/i, '') || String(sym == null ? '' : sym);
  }

  /**
   * A kick-off in the reader's own clock, with the zone it is read in and the
   * day when it is not today: "21:00 CEST", "Sat 21:00 CEST". A fixture's
   * kick-off is a fixed fact from the feed; a bare "21:00" with no zone read
   * as if it might be moving. A dash when unreadable.
   */
  function kickoffText(match, now) {
    const t = Date.parse((match || {}).kickoff || '');
    if (!Number.isFinite(t)) return '—';
    const d = new Date(t);
    const ref = new Date(now == null ? Date.now() : now);
    const pad = (n) => (n < 10 ? '0' : '') + n;
    let out = pad(d.getHours()) + ':' + pad(d.getMinutes());
    try {
      const parts = new Intl.DateTimeFormat('en-US', { timeZoneName: 'short' }).formatToParts(d);
      const zone = parts.find((p) => p.type === 'timeZoneName');
      if (zone && zone.value) out += ' ' + zone.value;
    } catch (e) { /* no zone name available: the time still stands */ }
    const sameDay = d.getFullYear() === ref.getFullYear() && d.getMonth() === ref.getMonth() && d.getDate() === ref.getDate();
    if (!sameDay) out = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'][d.getDay()] + ' ' + out;
    return out;
  }

  /** A team's short name, its name, or a dash: never a blank button. */
  function teamText(match, side) {
    const m = match || {};
    return String(m[side + '_short'] || m[side] || '—');
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
  function questionText(round, t, now) {
    const tr = t || IDENT;
    const r = round || {};
    switch (r.kind == null ? 'direction' : r.kind) {
      case 'direction':
        return tr('duel.q_direction', 'Higher or lower in {h}h?')
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
      case 'sports': {
        const m = r.match || {};
        return tr('duel.q_sports', '{home} vs {away} — who wins? {comp} · kick-off {time}')
          .replace('{home}', teamText(m, 'home')).replace('{away}', teamText(m, 'away'))
          .replace('{comp}', String(m.competition || '—')).replace('{time}', kickoffText(m, now));
      }
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
      case 'home': return teamText(r.match, 'home').toUpperCase();
      case 'away': return teamText(r.match, 'away').toUpperCase();
      case 'draw': return tr('duel.b_draw', 'DRAW');
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
  /** The first eight characters of a seal, the way a reader quotes one. */
  function shortSeal(seal) {
    return /^[0-9a-f]{64}$/.test(String(seal || '')) ? String(seal).slice(0, 8) + '…' : null;
  }

  /**
   * One receipt mention: the kind's word and the seal's first characters.
   * `links` only on the website: the Pi app links nowhere on the site, so
   * there the hash is quoted and the receipt is looked up on the web.
   */
  function receiptHtml(key, seal, text, links) {
    const short = shortSeal(seal);
    if (!short) return '';
    const label = esc(text.replace('{h}', short));
    return links
      ? '<a class="d-rc" href="/call/' + esc(key) + ':' + esc(seal) + '" title="' + esc(seal) + '">' + label + '</a>'
      : '<span class="d-rc" title="' + esc(seal) + '">' + label + '</span>';
  }

  /** +$1.2M / -$340K / +$12: a market figure at the size a card line has room for. */
  function compactUsd(v) {
    // `Number(null)` is 0: absence is refused before the coercion, or a
    // flow nobody read prints as +$0.
    if (v === null || v === undefined || v === '') return '';
    var n = Number(v);
    if (!isFinite(n)) return '';
    var a = Math.abs(n), sign = n < 0 ? '-' : '+';
    var body = a >= 1e9 ? (a / 1e9).toFixed(1) + 'B' : a >= 1e6 ? (a / 1e6).toFixed(1) + 'M'
      : a >= 1e3 ? (a / 1e3).toFixed(0) + 'K' : a.toFixed(0);
    return sign + '$' + body;
  }

  /**
   * The smart-money context line, when the card carries one. A read value
   * only: the line is omitted, never "0", when nothing was read, and it is
   * a line beside the question, never a tint on the buttons.
   */
  function smartMoneyText(sm, tr) {
    if (!sm || sm.read !== true) return '';
    var flow = compactUsd(sm.net_flow_usd);
    if (!flow) return '';
    var dir = Number(sm.net_flow_usd) >= 0 ? tr('duel.sm_in', 'in') : tr('duel.sm_out', 'out');
    return tr('duel.smart_money', 'Smart money 24h: {flow} {dir}', { flow: flow, dir: dir })
      + ' \u00b7 ' + (sm.provider || 'Nansen');
  }
  function smartMoneyHtml(r, tr) {
    var a = smartMoneyText(r && r.smart_money, tr);
    var b = smartMoneyText(r && r.smart_money_b, tr);
    var parts = [];
    if (a) parts.push((b && r.symbol ? base(r.symbol) + ': ' : '') + a);
    if (b) parts.push((r.symbol_b ? base(r.symbol_b) + ': ' : '') + b);
    return parts.length ? '<p class="d-ctx">' + parts.map(esc).join('<br>') + '</p>' : '';
  }

  function roundCard(round, t, now, opts) {
    const tr = t || IDENT;
    const links = Boolean(opts && opts.links);
    const r = round || {};
    const kind = r.kind == null ? 'direction' : r.kind;
    const mine = r.my_call
      ? callState(Object.assign({ kind }, r.my_call, r.my_result || {}), tr, now)
      : null;
    // A category this page can put a question to. A newer server's round of
    // another kind is not offered, whatever words it sends: a button under a
    // question the page cannot show is a call the player cannot have meant.
    const known = KNOWN_KINDS.indexOf(kind) >= 0;
    const options = !known ? []
      : Array.isArray(r.options) ? r.options : (kind === 'direction' ? ['long', 'short', 'pass'] : []);

    const pill = (cls, k, v) => '<span class="d-pill ' + cls + '"><small class="d-pill-k">' + esc(k) + '</small><b>' + esc(v) + '</b></span>';
    const clawWord = (rr) => (rr.agent_direction === 'long' ? tr('duel.b_long', 'LONG')
      : rr.agent_direction === 'short' ? tr('duel.b_short', 'SHORT') : tr('duel.b_pass', 'PASS'));

    let body;
    let status;
    let is;
    if (mine) {
      is = 'called';
      status = '<span class="d-status tone-' + esc(mine.tone) + '">' + esc(mine.text) + '</span>';
      // A football call shows the final score beside its result; a score is
      // printed only when one was read (my_call.score), never as 0–0.
      const sc = r.my_call.score;
      const scoreNote = kind === 'sports' && sc && sc.home != null && sc.away != null
        ? '<span class="d-note">' + esc(tr('duel.final_score', 'Final score {h}–{a}')
          .replace('{h}', sc.home).replace('{a}', sc.away)) + '</span>' : '';
      // The Claw's own word sits beside yours where there was one to reveal:
      // a classic round after the call, a back/fade round always.
      const claw = (kind === 'direction' || kind === 'backfade') && ('agent_direction' in r)
        ? pill('d-pill--claw', tr('duel.claw', 'Claw'), clawWord(r)) : '';
      // Provable Calls: the call's receipt, the settlement's once there is
      // one, and the round's own seal, dealt before anyone called.
      const receipts = [
        receiptHtml('duel', r.my_call.seal, tr('duel.rc_call', 'Receipt {h}'), links),
        receiptHtml('settle', r.my_call.settle_seal, tr('duel.rc_settle', 'Settlement {h}'), links),
        receiptHtml('round', r.seal, tr('duel.rc_round', 'Dealt sealed {h}'), links),
      ].filter(Boolean);
      body = '<div class="d-called">'
        + '<div class="d-pills">' + pill('d-pill--you', tr('duel.you', 'You'), pickLabel(r.my_call.pick, r, tr)) + claw + '</div>'
        + (mine.note ? '<span class="d-note">' + esc(mine.note) + '</span>' : '')
        + scoreNote
        + (receipts.length ? '<div class="d-receipts">' + receipts.join('') + '</div>' : '')
        + '</div>';
    } else if (r.callable && options.length) {
      is = 'open';
      status = '<span class="d-status tone-muted">' + esc(tr('duel.st_open', 'Open')) + '</span>';
      body = '<div class="d-actions" data-round="' + esc(r.id) + '">'
        + options.map((pick) => {
          const icon = pickIcon(pick);
          return '<button class="d-btn d-btn--' + esc(pick) + (pick === 'pass' ? ' d-btn--pass' : '')
            + '" data-pick="' + esc(pick) + '">' + (icon ? '<span class="d-bi">' + icon + '</span>' : '')
            + esc(pickLabel(pick, r, tr)) + '</button>';
        }).join('')
        + '</div>';
    } else if (r.callable) {
      is = 'unknown';
      status = '';
      body = '<div class="d-note">' + esc(tr('duel.q_unknown', 'This round asks a question this page cannot show. Update the app.')) + '</div>';
    } else {
      is = 'closed';
      status = '<span class="d-status tone-muted">' + esc(tr('duel.st_closed', 'Closed')) + '</span>';
      body = '<div class="d-note">' + esc(kind === 'sports'
        ? tr('duel.closed_kickoff', 'Kick-off has passed — calls are closed.')
        : tr('duel.closed', 'This round has closed.')) + '</div>';
    }

    const title = kind === 'mover'
      ? esc(base(r.symbol)) + ' <span class="d-vs">' + esc(tr('duel.vs', 'vs')) + '</span> ' + esc(base(r.symbol_b))
      : kind === 'sports'
        ? esc(teamText(r.match, 'home')) + ' <span class="d-vs">' + esc(tr('duel.vs', 'vs')) + '</span> ' + esc(teamText(r.match, 'away'))
        : esc(r.symbol);
    // The stance line stays on an uncalled classic round: it is the promise
    // the question used to carry ("shown once you commit"), now beside the
    // Claw's name where the pill will appear.
    // On an open classic round the commitment sits beside the hidden stance:
    // the hash was fixed when the card was dealt, and opens once you call.
    const dealt = kind === 'direction' && !mine ? receiptHtml('round', r.seal, tr('duel.rc_round', 'Dealt sealed {h}'), links) : '';
    const stance = kind === 'direction' && !mine
      ? '<span class="d-agent"><small class="d-pill-k">' + esc(tr('duel.claw', 'Claw')) + '</small>' + esc(agentLabel(r, tr).text)
        + (dealt ? ' <span class="d-receipts d-receipts--inline">' + dealt + '</span>' : '') + '</span>' : '';
    return '<article class="d-round d-round--' + esc(kind) + ' d-round--' + esc(is) + '">'
      + '<header class="d-round-h">'
      + '<span class="d-kind d-kind--' + esc(known ? kind : 'unknown') + '"><span class="d-icon">' + kindIcon(known ? kind : null) + '</span>' + esc(kindLabel(kind, tr)) + '</span>'
      + status
      + '</header>'
      + '<h3 class="d-sym">' + title + '</h3>'
      + '<p class="d-q">' + esc(questionText(r, tr, now)) + '</p>'
      + smartMoneyHtml(r, tr)
      + stance
      + body
      + '</article>';
  }

  return { esc, base, KNOWN_KINDS, callState, timeLeftText, kindIcon, pickIcon, shortSeal, receiptHtml, accuracyLabel, marksLabel, agentLabel, kindLabel, questionText, pickLabel, bandText, kickoffText, teamText, roundCard, compactUsd, smartMoneyText, smartMoneyHtml };
}));
