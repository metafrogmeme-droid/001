/*
 * Ask the Claw: the coach tab of the Pi Browser app.
 *
 * A chat with the owl, through our own /api/pi/coach and the session's token.
 * The server reads the person's facts (their practice trades, today's Duel
 * card, the card they asked "Why?" about), counts the day's allowance and
 * hands the question to the bot's coach, which answers under the Pi app's
 * rules and has its reply checked before it is sent (bot/web/pi_coach.py).
 * This page shows the answer and the allowance, and nothing else decides
 * anything here.
 *
 * THE CONVERSATION IS IN MEMORY, like the session: no storage on a framable
 * page (arena-session.js). Closing the app clears it.
 *
 * THE REPLY IS HTML FROM OUR BOT, AND STILL NOT TRUSTED AS HTML. It is
 * escaped whole, and only bare <b> <strong> <i> <em> <code> <br> (no
 * attributes) are put back. No link can be drawn, whatever the reply holds.
 *
 * THREE STATES FOR THE ALLOWANCE. Read ("12 of 20 left today"), used up (when
 * it resets, and Coach Plus when it is offered), and unread (said to be, and
 * the question can still be asked: the server counts, not this page).
 *
 * THE AI DISCLOSURE is the server's (lib/coach_ai_disclosure.js). This page
 * draws it only when the response carries that notice, and it marks a reply
 * only when the response says the reply was generated. A missing field is
 * not drawn as off and is not drawn as on.
 */
(function (root, factory) {
  if (typeof module === 'object' && module.exports) module.exports = factory();
  else root.RCPiCoach = factory();
}(typeof self !== 'undefined' ? self : this, function () {
  'use strict';

  var MAX_TEXT = 1000;
  /** The sentences in lib/coach_ai_disclosure.js. A test holds them equal. */
  var NOTICE = 'You are talking to the Coach, an AI. It is not a person.';
  var MARK = 'AI-generated';
  /** The admin-preview banner (lib/admin_preview.js BANNER). */
  var PREVIEW = 'Admin preview. Not public.';
  /** The intents of the coach's fixed answers (bot/web/user_gateway.py, the Pi coach turn). */
  var FIXED_INTENTS = ['pi_coach_token', 'pi_coach_no_feed', 'pi_coach_withheld', 'pi_coach_unavailable'];
  /** The starter questions in the language on screen; a chip asks what it shows. */
  function starters() {
    return [
      T('What is a stop-loss?'),
      T('How does the Daily Duel work?'),
      T('How do I check a receipt?'),
      T('Why did my last trade end the way it did?'),
    ];
  }

  /** The Pi app's translation (pi-i18n.js), the English where it is not loaded. */
  function T(en, vars) {
    var I = typeof window !== 'undefined' && window.RCPiI18n;
    if (I) return I.t(en, vars);
    return !vars ? en : en.replace(/\{(\w+)\}/g, function (m, k) { return vars[k] != null ? String(vars[k]) : m; });
  }

  /** The language the reader has on screen, sent with a question so the
   *  coach answers in it; English is the default and is not sent. */
  function lang() {
    var I = typeof window !== 'undefined' && window.RCPiI18n;
    var code = I && typeof I.lang === 'function' ? I.lang() : 'en';
    return code && code !== 'en' ? code : null;
  }

  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }

  /** The bot's reply, escaped whole, with only bare inline tags restored. */
  function safeReply(html) {
    return esc(html).replace(/&lt;(\/?)(b|strong|i|em|code)&gt;/gi, '<$1$2>')
      .replace(/&lt;br\s*\/?&gt;/gi, '<br>');
  }

  function utcTime(iso) {
    var d = new Date(iso);
    if (isNaN(d.getTime())) return null;
    return (d.getUTCHours() < 10 ? '0' : '') + d.getUTCHours() + ':' + (d.getUTCMinutes() < 10 ? '0' : '') + d.getUTCMinutes() + ' UTC';
  }

  /**
   * opts.buyPlus(ctx) -> Promise of { ok, text }: the Pi payment for Coach
   * Plus, run by the host that owns the Pi SDK (pi-arena.js). Absent, no
   * purchase is offered.
   */
  function create(opts) {
    var o = opts || {};
    var ctx = null;
    var state = null;     // null: not read; { enabled, reason?, limit, left, resets_at, plus }
    var readFailed = false;
    var log = [];         // [{ who: 'me' | 'claw' | 'note', text?, html? }]
    var busy = false;
    var typed = '';
    var scrollEnd = false;
    var plusNote = null;  // [kind, text]
    var aiView = null;    // the server's ai_disclosure, or null when it sent none

    function takeDisclosure(body) {
      var d = body && body.ai_disclosure;
      aiView = d && d.notice === NOTICE ? d : null;
    }

    function read() {
      if (!ctx) return Promise.resolve();
      return ctx.api('/api/pi/coach').then(function (r) {
        if (r.ok && r.body && typeof r.body.enabled === 'boolean') {
          state = r.body;
          readFailed = false;
          takeDisclosure(r.body);
        } else readFailed = true;
      }, function () { readFailed = true; }).then(function () { if (ctx) ctx.render(); });
    }

    function signedIn(c) { ctx = c; return read(); }

    function allowanceHtml() {
      if (readFailed) return '<p class="p-coach-left">' + esc(T('How many questions are left could not be read just now.')) + '</p>';
      if (!state || !state.enabled || typeof state.left !== 'number') return '';
      var plus = state.plus || {};
      var until = plus.until ? ' · ' + T('Coach Plus until {day}', { day: new Date(plus.until).toISOString().slice(0, 10) }) : '';
      return '<p class="p-coach-left">' + esc(T('{n} of {total} questions left today', { n: state.left, total: state.limit }) + until) + '</p>';
    }

    function usedUpHtml() {
      if (!state || state.left !== 0) return '';
      var at = state.resets_at ? utcTime(state.resets_at) : null;
      var plus = state.plus || {};
      var offer = plus.offered && !plus.until && typeof o.buyPlus === 'function'
        ? '<button type="button" class="p-coach-plus"' + (busy ? ' disabled' : '') + '>'
          + esc(T('Coach Plus: {n} questions a day for {days} days, {amount} π', { n: plus.daily, days: plus.offered.days, amount: plus.offered.amount })) + '</button>'
          + '<p class="m-open-note">' + esc(T('A payment in Pi, through the Pi Browser. It adds coach questions and nothing else.')) + '</p>'
        : '';
      return '<div class="p-coach-out"><p>' + esc(at ? T('You have used today’s questions. More at {time}.', { time: at })
        : T('You have used today’s questions.')) + '</p>' + offer + '</div>';
    }

    function disclosureHtml() {
      if (!aiView || aiView.notice !== NOTICE) return '';
      var banner = aiView.preview === true
        ? '<p class="admin-preview" role="note">' + esc(PREVIEW) + '</p>'
        : '';
      return banner + '<p class="p-coach-ai" role="note">'
        + esc(T('You are talking to the Coach, an AI. It is not a person.')) + '</p>';
    }

    function logHtml() {
      if (!log.length) {
        return '<div class="p-coach-start"><p class="p-coach-hi">'
          + esc(T('Hi, I am the Claw. Ask me about the Duel, a signal, your practice trades or any trading word.'))
          + '</p><div class="p-coach-chips">'
          + starters().map(function (q) { return '<button type="button" class="p-coach-chip">' + esc(q) + '</button>'; }).join('')
          + '</div></div>';
      }
      return '<div class="p-coach-log" aria-live="polite">' + log.map(function (m) {
        if (m.who === 'me') return '<div class="p-coach-msg p-coach-msg--me">' + esc(m.text) + '</div>';
        if (m.who === 'claw') {
          var gen = m.generated === true;
          return '<div class="p-coach-msg p-coach-msg--claw"' + (gen ? ' data-ai-generated="true"' : '') + '>'
            + (gen ? '<p class="p-coach-ai-mark">' + esc(T('AI-generated')) + '</p>' : '')
            + (typeof m.html === 'string' ? safeReply(m.html) : esc(m.text)) + '</div>';
        }
        return '<div class="p-coach-msg p-coach-msg--note">' + esc(m.text) + '</div>';
      }).join('') + (busy ? '<div class="p-coach-msg p-coach-msg--claw p-coach-typing">' + esc(T('Thinking…')) + '</div>' : '') + '</div>';
    }

    function html() {
      var M = window.RCMiniView;
      var off = state && state.enabled === false;
      var form = off ? M.actionNoteHtml('note', state.reason === 'pi_account_required'
        ? T('The coach needs a Pi sign-in.') : T('The coach is not available right now.'))
        : '<div class="p-coach-form"><label class="m-field"><span class="m-label">' + esc(T('Your question')) + '</span>'
          + '<textarea class="p-coach-in" rows="2" maxlength="' + MAX_TEXT + '" placeholder="' + esc(T('e.g. what does leverage do?')) + '">'
          + esc(typed) + '</textarea></label>'
          + '<button type="button" class="p-coach-send"' + (busy || (state && state.left === 0) ? ' disabled' : '') + '>' + esc(T('Ask')) + '</button></div>';
      var disclosed = aiView && aiView.notice === NOTICE;
      return '<section class="m-open p-coach"' + (disclosed ? ' data-ai-system="coach"' : '') + '><div class="p-sig-head"><h2>' + esc(T('Ask the Claw')) + '</h2></div>'
        + disclosureHtml()
        + allowanceHtml() + logHtml() + usedUpHtml()
        + (plusNote ? M.actionNoteHtml(plusNote[0], plusNote[1]) : '')
        + form
        + '<p class="m-open-note">' + esc(T('Education and practice, not advice. Your question and a summary of your practice trades and Duel calls go to an AI provider to answer it.'))
        + '</p></section>';
    }

    function say(who, text) { log.push({ who: who, text: text }); }

    function ask(text, about, c) {
      var cx = c || ctx;
      var q = String(text || '').trim();
      if (!cx || busy || !q) return;
      if (q.length > MAX_TEXT) q = q.slice(0, MAX_TEXT);
      log.push({ who: 'me', text: q });
      typed = '';
      busy = true;
      scrollEnd = true;
      cx.render();
      var body = { text: q };
      if (about) body.about = about;
      var l = lang();
      if (l) body.lang = l;
      return cx.api('/api/pi/coach', { method: 'POST', body: body }).then(function (r) {
        busy = false;
        var b = r.body || {};
        if (r.ok && typeof b.reply_html === 'string') {
          takeDisclosure(b);
          // A fixed answer (its intent names which) is one of the coach's four
          // sentences, which the Pi table translates (build_pi_i18n.js reads
          // them from bot/web/pi_coach.py); it is drawn as text, in the
          // language on screen. A model's answer is drawn as the bot sent it.
          // generated is only the server's true: a missing field is not a mark.
          if (FIXED_INTENTS.indexOf(b.intent) >= 0) log.push({ who: 'claw', text: T(b.reply_html) });
          else log.push({ who: 'claw', html: b.reply_html, generated: b.ai_generated === true });
          if (typeof b.left === 'number') state = Object.assign({}, state || {}, { enabled: true, limit: b.limit, left: b.left, resets_at: b.resets_at, plus: b.plus });
        } else if (r.status === 429) {
          state = Object.assign({}, state || {}, { enabled: true, limit: b.limit, left: 0, resets_at: b.resets_at, plus: b.plus });
          say('note', T('That question was not asked: today’s questions are used.'));
        } else if (r.status === 403) {
          say('note', T('The coach needs a Pi sign-in.'));
        } else if (r.status === 400) {
          say('note', b.error === 'text_too_long' ? T('That question could not be asked: it is too long.')
            : T('That question could not be asked: it was empty.'));
        } else {
          say('note', T('The coach could not answer just now. Your question was not counted.'));
        }
        scrollEnd = true;
        cx.render();
      }, function () {
        busy = false;
        say('note', T('The coach could not be reached. Your question was not counted.'));
        scrollEnd = true;
        cx.render();
      });
    }

    function buy(c) {
      if (busy || typeof o.buyPlus !== 'function') return;
      busy = true;
      plusNote = ['note', T('Preparing Coach Plus…')];
      c.render();
      Promise.resolve().then(function () { return o.buyPlus(c); }).then(function (out) {
        busy = false;
        plusNote = [out && out.ok ? 'ok' : 'warn', (out && out.text) || T('Coach Plus did not go through. Nothing was taken.')];
        return read();
      }, function () {
        busy = false;
        plusNote = ['warn', T('Coach Plus did not go through. Nothing was taken.')];
        c.render();
      });
    }

    function click(target, c) {
      var chip = target.closest('.p-coach-chip');
      if (chip) { ask(chip.textContent, null, c); return true; }
      if (target.closest('.p-coach-send')) {
        var el = document.querySelector('.p-coach-in');
        ask(el ? el.value : typed, null, c);
        return true;
      }
      if (target.closest('.p-coach-plus')) { buy(c); return true; }
      return false;
    }

    function mounted(rootEl) {
      var el = rootEl.querySelector('.p-coach-in');
      if (el) el.addEventListener('input', function () { typed = el.value; });
      if (scrollEnd) {
        scrollEnd = false;
        var logEl = rootEl.querySelector('.p-coach-log');
        var last = logEl && logEl.lastElementChild;
        if (last && last.scrollIntoView) last.scrollIntoView({ block: 'end' });
      }
    }

    return { place: 'main', tab: 'coach', label: 'Coach', icon: '🦉', html: html, click: click,
      signedIn: signedIn, mounted: mounted, reload: read, ask: ask };
  }

  return { create: create, safeReply: safeReply, starters: starters, MAX_TEXT: MAX_TEXT, FIXED_INTENTS: FIXED_INTENTS,
    NOTICE: NOTICE, MARK: MARK, PREVIEW: PREVIEW };
}));
