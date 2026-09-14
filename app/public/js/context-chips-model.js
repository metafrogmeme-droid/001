/**
 * The context chip row's chips, as a PURE function.
 *
 * ONE read (/api/bot/sync/scan) feeds five subjects — the age of the bot's
 * last push, the venue it trades, the BTC regime, the macro calendar and the
 * entry gate — and every one of them can go missing in a way that looks
 * identical from the browser to a reading:
 *
 *   tick    — `received_at` is stamped by this site at ingest; `timestamp` is
 *             the bot's own build time in a non-ISO spelling
 *             (`%Y-%m-%d %H:%M UTC`, scan_skill.py). The topbar chip judges
 *             the stamp by age, and an unparseable stamp gives it a NaN age,
 *             which falls through to '● ENGINE OFFLINE' — a confident
 *             negative about the trading engine manufactured from a string.
 *   venue   — `features.venue` is built under a bare `except: pass`, so
 *             absent is "no live executor" OR "the probe raised". On a paper
 *             deployment it is absent on every scan forever.
 *   regime  — `regime` is ALWAYS present and seeded {label:'NEUTRAL', gate:0};
 *             `gate` (the BTC anchor price) is only written inside `if btc:`,
 *             so gate == 0 means BTC was never read and NEUTRAL is a DEFAULT,
 *             not a measurement. The Engine view prints it as one today.
 *   macro   — `state` is one word for four conditions: a real window, a
 *             crashed evaluation (`unreadable`), an exhausted schedule
 *             (`stale`) and an empty calendar (`has_events` false), whose
 *             NORMAL is "a confident all-clear from no data" by calendar.py's
 *             own docstring. The producer publishes all three flags now.
 *   gate    — `circuit_breaker.gate` is entry_gate's own answer, THREE
 *             values: `blocked` (a positive reading), `unknown` (some
 *             condition could not be read), and null when the gate could
 *             not be asked at all. A clear list beside `unknown: true` is
 *             consistent with a gate nobody fully read, and green is the
 *             word for the other one.
 *
 * So the rule here is not decoration: a chip is pushed only when its field
 * was READ, each subject that was not is NAMED with its own reason, a
 * subject that is absent by CONFIGURATION (the venue on a paper bot) is
 * omitted rather than named forever, and no chip in this row is green from a
 * field that could merely be absent.
 *
 * The model returns KEYS, never English. Every word it can emit is in `W`
 * (key → English fallback) and the renderer lists each one as a literal
 * `T('dd.…')` call, so the dictionary sweep sees every one of them.
 */
(function (root, factory) {
  const api = factory();
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.ContextChipsModel = api;
}(typeof self !== 'undefined' ? self : this, function () {
  'use strict';

  // Every key the model can emit, with its English fallback.
  const W = {
    'dd.ctx_tick': 'TICK',
    'dd.ctx_venue': 'VENUE',
    'dd.ctx_regime': 'REGIME',
    'dd.ctx_macro': 'MACRO',
    'dd.ctx_gate': 'GATE',
    'dd.ctx_unread': 'NOT REPORTED',
    'dd.ctx_tick_live': 'LIVE',
    'dd.ctx_tick_stale': 'STALE',
    'dd.ctx_tick_offline': 'OFFLINE',
    'dd.ctx_reg_bull': 'BULLISH',
    'dd.ctx_reg_bear': 'BEARISH',
    'dd.ctx_reg_neutral': 'NEUTRAL',
    'dd.ctx_mac_normal': 'Normal',
    'dd.ctx_mac_pre': 'Pre-event caution',
    'dd.ctx_mac_post': 'Post-event volatility',
    'dd.ctx_mac_blackout': 'Blackout',
    'dd.ctx_gate_clear': 'CLEAR',
    'dd.ctx_gate_blocked': 'BLOCKED',
    'dd.ctx_gate_noblock': 'no block reported',
    'dd.ctx_u_tick': 'tick',
    'dd.ctx_u_venue': 'venue',
    'dd.ctx_u_regime': 'regime',
    'dd.ctx_u_macro': 'macro',
    'dd.ctx_u_gate': 'gate',
    'dd.ctx_w_tick': 'the scan carries no readable time stamp.',
    'dd.ctx_w_venue': 'the scan named no venue.',
    'dd.ctx_w_regime': 'the scan carried no regime.',
    'dd.ctx_w_regime_default': 'BTC was not read, so the regime is the scan’s default rather than a reading.',
    'dd.ctx_w_regime_word': 'the scan carried a regime word this page does not know.',
    'dd.ctx_w_macro': 'the scan carried no calendar reading.',
    'dd.ctx_w_macro_failed': 'the calendar evaluation failed — nothing was measured.',
    'dd.ctx_w_macro_exhausted': 'every scheduled event is in the past; the calendar needs regenerating.',
    'dd.ctx_w_macro_empty': 'no calendar is loaded, so its Normal is not a reading.',
    'dd.ctx_w_macro_unstated': 'the scan did not say whether a calendar was loaded, so its Normal is not a reading.',
    'dd.ctx_w_gate': 'the scan carried no gate reading.',
    'dd.ctx_w_gate_unasked': 'the engine could not read its own entry gate.',
    'dd.ctx_w_gate_shape': 'the gate reading was malformed.',
    'dd.ctx_n_tick': 'Last scan push reached this site {when}.',
    'dd.ctx_n_macro': 'The calendar reports {state}. That is the calendar’s own reading, not what the risk gate did with it.',
    'dd.ctx_n_gate_blocked': 'Entries are blocked: {reasons}.',
    'dd.ctx_n_gate_blocked_nr': 'Entries are blocked; the scan named no reason.',
    'dd.ctx_n_gate_partial': 'No block was reported, but not every gate condition could be read — this is not an all-clear.',
  };
  const KEYS = Object.keys(W);

  // A number that was MEASURED. `Number('')` is 0 AND is finite — the trap
  // pnlClass records — so an empty string is rejected before the coercion,
  // and a boolean is rejected because `Number(true)` is 1.
  function num(v) {
    if (v == null || typeof v === 'boolean') return null;
    if (typeof v === 'string' && v.trim() === '') return null;
    const n = Number(v);
    return isFinite(n) ? n : null;
  }
  function str(v) { return (typeof v === 'string' && v.trim()) ? v.trim() : null; }
  function obj(v) { return (v && typeof v === 'object' && !Array.isArray(v)) ? v : null; }
  function titleCase(s) {
    return s.replace(/_/g, ' ').toLowerCase().replace(/^\S/, (c) => c.toUpperCase());
  }

  /**
   * The bot's own stamp is `2026-09-14 11:55 UTC`, which is not ISO 8601, and
   * `Date.parse` of a non-ISO string is implementation-defined: V8 reads it,
   * another engine may answer NaN, and the chip would exist in one browser
   * and not the other from one payload. Spelled into ISO before parsing, so
   * the verdict does not depend on the engine. Anything else is parsed as
   * is, and a stamp that does not parse is not a stamp.
   *   → ISO string, or null
   */
  function stamp(v) {
    const s = str(v);
    if (s === null) return null;
    const m = s.match(/^(\d{4}-\d{2}-\d{2}) (\d{2}:\d{2})(?::(\d{2}))? UTC$/);
    const iso = m ? `${m[1]}T${m[2]}:${m[3] || '00'}Z` : s;
    const t = Date.parse(iso);
    return isFinite(t) ? new Date(t).toISOString() : null;
  }

  const TICK_BY_CLS = { 'chip--up': 'dd.ctx_tick_live', 'chip--warn': 'dd.ctx_tick_stale', 'chip--offline': 'dd.ctx_tick_offline' };
  const REGIME = { BULLISH: ['dd.ctx_reg_bull', 'chip--up'], BEARISH: ['dd.ctx_reg_bear', 'chip--down'], NEUTRAL: ['dd.ctx_reg_neutral', ''] };
  const MACRO = { NORMAL: 'dd.ctx_mac_normal', PRE_EVENT_CAUTION: 'dd.ctx_mac_pre', POST_EVENT_VOLATILITY: 'dd.ctx_mac_post', BLACKOUT: 'dd.ctx_mac_blackout' };

  /**
   * @param {object|null} scan     the /scan payload's `scan` object
   * @param {number} nowMs         injected, so the verdict is testable
   * @param {function} engineChipState  EngineStatusModel.engineChipState —
   *        the topbar's own verdict on the stamp's age. Reused, never
   *        re-derived: a second copy of a threshold is a second answer, and
   *        the row and the topbar would then disagree about one engine.
   * @returns {{chips, unread, omitted, notes, at}|null}  null when there is no scan
   *   chips   [{subject, kKey, vKey, v, cls}]   vKey null → `v` is a producer
   *                                             literal (a venue name)
   *   unread  [{subject, nameKey, whyKey}]      named in the NOT REPORTED chip,
   *                                             each with its own reason
   *   omitted [{subject, why}]                  absent by configuration; not named
   *   notes   [{key, vars}]                     visible lines under the row
   *   at      ISO string of the stamp that was read, or null
   */
  function contextChips(scan, nowMs, engineChipState) {
    const s = obj(scan);
    if (s === null) return null;
    if (typeof engineChipState !== 'function') {
      // A verdict nobody can compute is a render we could not do, not a
      // subject the scan failed to carry: the loader throws on it.
      throw new TypeError('contextChips needs the engine-status verdict');
    }
    const chips = [];
    const unread = [];
    const omitted = [];
    const notes = [];
    const miss = (subject, whyKey) => unread.push({ subject, nameKey: 'dd.ctx_u_' + subject, whyKey });

    // ── TICK ── the PARSE is checked first (see `stamp`), then the topbar's
    // model judges the age. Its class is the state; its words are keyed here
    // so the row translates, and a class this map does not know keeps the
    // model's own text with no colour rather than guessing a state for it.
    const at = stamp(s.received_at) || stamp(s.timestamp);
    if (at === null) {
      miss('tick', 'dd.ctx_w_tick');
    } else {
      const v = engineChipState(at, true, nowMs) || {};
      const vKey = TICK_BY_CLS[v.cls] || null;
      chips.push({ subject: 'tick', kKey: 'dd.ctx_tick', vKey, v: vKey ? W[vKey] : String(v.text || ''), cls: vKey ? v.cls : '' });
      notes.push({ key: 'dd.ctx_n_tick', vars: { at } });
    }

    // ── VENUE ── a name is a reading, not a verdict: no colour. Absent on a
    // bot that says `live_mode: false` is absent by configuration — there is
    // no live executor to be bound to a venue — and naming it on every scan
    // forever trains the reader to stop reading the list. Absent beside
    // `live_mode: true`, or with no live_mode at all, is not reported.
    const cb = obj(s.circuit_breaker);
    const feat = obj(s.features);
    const ven = feat === null ? null : obj(feat.venue);
    const vname = ven === null ? null : (str(ven.name) || str(ven.id));
    if (vname !== null) {
      chips.push({ subject: 'venue', kKey: 'dd.ctx_venue', vKey: null, v: vname.toUpperCase(), cls: '' });
    } else if (cb !== null && cb.live_mode === false) {
      omitted.push({ subject: 'venue', why: 'paper' });
    } else {
      miss('venue', 'dd.ctx_w_venue');
    }

    // ── REGIME ── `gate` is the BTC anchor price and the only evidence in the
    // payload that BTC was read at all. Without it the label is the
    // constructor's default. A read NEUTRAL is a measurement and renders.
    const reg = obj(s.regime);
    const label = reg === null ? null : str(reg.label);
    const anchor = reg === null ? null : num(reg.gate);
    if (reg === null || label === null) {
      miss('regime', 'dd.ctx_w_regime');
    } else if (anchor === null || anchor <= 0) {
      miss('regime', 'dd.ctx_w_regime_default');
    } else if (!REGIME[label.toUpperCase()]) {
      miss('regime', 'dd.ctx_w_regime_word');
    } else {
      const [vKey, cls] = REGIME[label.toUpperCase()];
      chips.push({ subject: 'regime', kKey: 'dd.ctx_regime', vKey, v: W[vKey], cls });
    }

    // ── MACRO ── the producer's three flags each name a condition the state
    // word alone hides, and NORMAL is a reading only when a calendar was
    // loaded: an empty one evaluates NORMAL too. A non-normal state is the
    // CALENDAR's reading, said so in the note, because the risk gate sizes
    // off its own provider and nothing in the payload connects the two.
    const mac = obj(s.macro);
    const mstate = mac === null ? null : str(mac.state);
    if (mac === null || mstate === null) {
      miss('macro', 'dd.ctx_w_macro');
    } else if (mac.unreadable === true) {
      miss('macro', 'dd.ctx_w_macro_failed');
    } else if (mac.stale === true) {
      miss('macro', 'dd.ctx_w_macro_exhausted');
    } else if (mac.has_events === false) {
      miss('macro', 'dd.ctx_w_macro_empty');
    } else if (mstate.toUpperCase() === 'NORMAL' && mac.has_events !== true) {
      miss('macro', 'dd.ctx_w_macro_unstated');
    } else {
      const up = mstate.toUpperCase();
      const vKey = MACRO[up] || null;
      const word = vKey ? W[vKey] : titleCase(mstate);
      const normal = up === 'NORMAL';
      chips.push({ subject: 'macro', kKey: 'dd.ctx_macro', vKey, v: word, cls: normal ? '' : 'chip--warn' });
      if (!normal) notes.push({ key: 'dd.ctx_n_macro', vars: { state: word, stateKey: vKey } });
    }

    // ── GATE ── entry_gate's own three values. BLOCKED is a positive reading
    // and carries the engine's category reasons in the note (they are
    // category-only on this payload by the producer's rule). CLEAR is green
    // only when `unknown` is false — every condition read clear. A clear
    // list beside `unknown: true` is "no block reported", uncoloured, with
    // the caveat in visible text: a title attribute does not render on touch.
    if (cb === null || cb.gate === undefined) {
      miss('gate', 'dd.ctx_w_gate');
    } else if (cb.gate === null) {
      miss('gate', 'dd.ctx_w_gate_unasked');
    } else {
      const g = obj(cb.gate);
      if (g === null || typeof g.blocked !== 'boolean' || typeof g.unknown !== 'boolean') {
        miss('gate', 'dd.ctx_w_gate_shape');
      } else if (g.blocked) {
        const reasons = (Array.isArray(g.reasons) ? g.reasons : []).map(str).filter((r) => r !== null);
        chips.push({ subject: 'gate', kKey: 'dd.ctx_gate', vKey: 'dd.ctx_gate_blocked', v: W['dd.ctx_gate_blocked'], cls: 'chip--down' });
        notes.push(reasons.length
          ? { key: 'dd.ctx_n_gate_blocked', vars: { reasons: reasons.join(' · ') } }
          : { key: 'dd.ctx_n_gate_blocked_nr', vars: {} });
      } else if (g.unknown) {
        chips.push({ subject: 'gate', kKey: 'dd.ctx_gate', vKey: 'dd.ctx_gate_noblock', v: W['dd.ctx_gate_noblock'], cls: '' });
        notes.push({ key: 'dd.ctx_n_gate_partial', vars: {} });
      } else {
        chips.push({ subject: 'gate', kKey: 'dd.ctx_gate', vKey: 'dd.ctx_gate_clear', v: W['dd.ctx_gate_clear'], cls: 'chip--up' });
      }
    }

    // NAMED, not counted, and never silent. A row whose chips simply
    // disappear is the "announces itself and then says nothing" failure the
    // guard/omit table warns about. One chip, muted, listing the subjects;
    // the reason for each is a visible line, not a tooltip.
    if (unread.length) {
      chips.push({ subject: 'unread', kKey: 'dd.ctx_unread', vKey: null, v: null, cls: 'chip--offline' });
    }
    return { chips, unread, omitted, notes, at };
  }

  return { contextChips, stamp, W, KEYS };
}));
