/**
 * THE DECISION LOG, as a PURE function of the two guardian reads.
 *
 * The engine's sealed trail — event → thesis → GATE → disposition → fill —
 * from the two endpoints that exist: /api/guardian/flight (the hash-chained
 * decision records) and /api/guardian/incidents (the gate blocks, recoveries
 * and flags). There is no order source on either surface, so the log renders
 * the engine's sealed DISPOSITION and labels it as that; a column headed
 * "order" over a payload that cannot say what is resting at the venue would
 * be a door painted on a wall.
 *
 * It is the OPERATOR AGENT's ledger, for every viewer: flight_cache is one
 * row and every emitter guards `if not user_id`. The footer says so.
 *
 * Every cell has three answers, and the absences are NAMED:
 *  * THE GATE has four. The recorder's own except branch seals the literal
 *    "UNKNOWN" when it could not read the risk object, and a record may carry
 *    no risk block at all; flightCard paints both in the colour of a
 *    rejection. Here "unread" and "none on record" are muted, and only a
 *    sealed APPROVED / REJECTED is a verdict.
 *  * THE FILL has six. A number is a number; a `pnl_usd` of null is the
 *    unpriced close the engine deliberately books; an ABSENT key is what the
 *    anonymous scrub leaves behind, and the server's `fill_priced` marker
 *    says whether a number was ever there — so "sign in to see it" is said
 *    only when signing in would show one, and an older server that sends no
 *    marker gets the sentence that promises nothing.
 *  * A PRICE the record holds as 0 is the absence, not a level: the engine
 *    writes `float(x or 0)` into the geometry it seals. P&L is exempt — a
 *    measured 0.00 is a break-even.
 *  * A record with no timestamp gets WORDS, never an age computed from a
 *    missing value (new Date(0) reads "56 y ago"), and it sinks to the bottom
 *    of the sort rather than being placed in a chronology it does not have.
 *  * An incident with no symbol is ABSENT and normal (a firewall verdict is
 *    not about a market); a decision with no symbol is a failed read. Two
 *    absences, two words.
 *  * THE INCIDENT STREAM is three-valued and the omission is NAMED: read
 *    (rows in the same timeline), read-and-empty (a quiet line), derived
 *    (an older bot: a loud line naming what is missing), unreadable (a loud
 *    line above the rows). Empty ledger AND unreadable incidents THROWS —
 *    omitting the only half that could have had content would turn OMIT into
 *    a confident negative.
 *  * THE LEDGER'S AGE is "last written", never a liveness claim: a push
 *    happens on a live confirm, rejection or close, so a quiet ledger and a
 *    quiet engine look identical from here.
 *
 * No colour is decided here beyond the class a sealed verdict earns; the
 * P&L's class comes from pnlClass in the renderer. Written clean of the
 * honesty ratchet's four shapes.
 */
(function (root, factory) {
  const api = factory();
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.DecisionLogModel = api;
}(typeof self !== 'undefined' ? self : this, function () {
  'use strict';

  const W = {
    noTime:     { key: 'dd.dl_no_time',     en: 'time not on record' },
    noSym:      { key: 'dd.dl_no_sym',      en: 'symbol unread' },
    noMarket:   { key: 'dd.dl_no_market',   en: 'no market' },
    noDir:      { key: 'dd.dl_no_dir',      en: 'direction unread' },
    noThesis:   { key: 'dd.dl_no_thesis',   en: 'no thesis on record' },
    noDetail:   { key: 'dd.dl_no_detail',   en: 'no detail on record' },
    gatePass:   { key: 'dd.dl_gate_pass',   en: 'gate passed' },
    gateBlock:  { key: 'dd.dl_gate_block',  en: 'gate blocked' },
    gateUnread: { key: 'dd.dl_gate_unread', en: 'gate verdict unread' },
    gateNone:   { key: 'dd.dl_gate_none',   en: 'no gate on record' },
    nFailed:    { key: 'dd.dl_nfailed',     en: '{n} check(s) failed' },
    dExec:      { key: 'dd.dl_d_exec',      en: 'sent to venue' },
    dFail:      { key: 'dd.dl_d_fail',      en: 'venue rejected the order' },
    dRej:       { key: 'dd.dl_d_rej',       en: 'stopped on re-check' },
    dUnread:    { key: 'dd.dl_d_unread',    en: 'disposition not on record' },
    open:       { key: 'dd.dl_open',        en: 'open — no close on record' },
    pnlUnrec:   { key: 'dd.dl_pnl_unrec',   en: 'closed · P&L not recorded' },
    hidden:     { key: 'dd.dl_hidden',      en: 'closed · amount hidden on the anonymous view — sign in to see it' },
    unshown:    { key: 'dd.dl_unshown',     en: 'closed · amount not shown on the anonymous view' },
    kBlock:     { key: 'dd.dl_k_block',     en: 'blocked' },
    kRec:       { key: 'dd.dl_k_rec',       en: 'recovered' },
    kFlag:      { key: 'dd.dl_k_flag',      en: 'flagged' },
    kUnread:    { key: 'dd.dl_k_unread',    en: 'kind not on record' },
    incUnread:  { key: 'dd.dl_inc_unread',  en: 'Gate blocks could not be read — this log shows sealed decisions only and is incomplete.' },
    incDerived: { key: 'dd.dl_inc_derived', en: 'Only risk-gate rejections are shown — this bot has not yet sent its full incident stream, so firewall, sentinel and escape events are missing from the log below.' },
    incNone:    { key: 'dd.dl_inc_none',    en: 'No gate blocks in this window — the incident ledger was read and is empty.' },
    scope:      { key: 'dd.dl_scope',       en: 'The operator agent’s sealed ledger — the same record for every viewer, not your own account.' },
    written:    { key: 'dd.dl_written',     en: 'Ledger last written {when}.' },
    writtenWhy: { key: 'dd.dl_written_why', en: 'That is when the bot last pushed, not when it last thought: a push happens on a live confirm, rejection or close, so a quiet ledger and a quiet engine look the same from here.' },
    thesisCut:  { key: 'dd.dl_thesis_cut',  en: 'shortened — the full thesis is in the tooltip' },
  };
  const KEYS = Object.keys(W).map(function (k) { return W[k].key; });

  /** A finite, non-zero NUMBER, or null. Prices are sealed as floats; a
   *  numeric string there is junk, and 0 is the engine's `float(x or 0)`. */
  function price(v) {
    if (typeof v !== 'number' || !isFinite(v) || v === 0) return null;
    return v;
  }

  /** A P&L is a finite NUMBER or null; 0 stays, because a break-even is
   *  measured. */
  function pnl(v) {
    if (typeof v !== 'number' || !isFinite(v)) return null;
    return v;
  }

  /** Milliseconds a timestamp string carries, or null. */
  function time(ts) {
    if (typeof ts !== 'string' || !ts.trim()) return null;
    const ms = new Date(ts).getTime();
    return isFinite(ms) ? ms : null;
  }

  /** 'long' | 'short' | null — the same vocabulary the instrument row and
   *  dirChip read (LONG/BUY, SHORT/SELL), pinned against them by the guard. */
  function side(direction) {
    const d = String(direction === null || direction === undefined ? '' : direction).trim().toUpperCase();
    if (d === 'LONG' || d === 'BUY') return 'long';
    if (d === 'SHORT' || d === 'SELL') return 'short';
    return null;
  }

  function text(v) {
    return (typeof v === 'string' && v.trim()) ? v.trim() : '';
  }

  /** The named checks a gate reading carries. `passed`/`failed` are COUNTS
   *  the seal may not carry at all (the recorder's except branch returns
   *  `{verdict}` alone): an absent count is null, never 0, because "0 checks
   *  failed" is the all-clear. */
  function checks(risk) {
    const names = Array.isArray(risk.checks_failed)
      ? risk.checks_failed.filter(function (c) { return typeof c === 'string' && c.trim(); }) : [];
    const failed = (typeof risk.failed === 'number' && isFinite(risk.failed) && risk.failed > 0) ? risk.failed : null;
    return { names: names, reason: text(risk.reason), failed: failed };
  }

  /**
   * THE GATE CELL — four verdict states and a fifth for a word this model
   * does not know. state: 'pass' | 'block' | 'unread' | 'none' | 'other'.
   */
  function gate(rec) {
    const risk = rec && rec.risk;
    if (risk === null || risk === undefined || typeof risk !== 'object') {
      return { state: 'none', cls: 'chip dl-unread', word: W.gateNone, literal: null, why: null };
    }
    const v = risk.verdict === null || risk.verdict === undefined ? '' : String(risk.verdict).trim().toUpperCase();
    const rejected = String(rec.outcome === null || rec.outcome === undefined ? '' : rec.outcome).indexOf('REJECTED') === 0;
    if (v === 'APPROVED') return { state: 'pass', cls: 'chip chip--up', word: W.gatePass, literal: null, why: checks(risk) };
    if (v === 'REJECTED' || rejected) return { state: 'block', cls: 'chip chip--down', word: W.gateBlock, literal: null, why: checks(risk) };
    if (v === '' || v === 'UNKNOWN') return { state: 'unread', cls: 'chip dl-unread', word: W.gateUnread, literal: null, why: null };
    // A word this model does not know is not a verdict it may colour.
    return { state: 'other', cls: 'chip dl-unread', word: null, literal: v, why: checks(risk) };
  }

  /** The engine's sealed disposition — what it DID with the decision, and
   *  nothing about what is resting at the venue. */
  function disposition(rec) {
    const o = String(rec && rec.outcome !== null && rec.outcome !== undefined ? rec.outcome : '').trim();
    if (o === 'EXECUTED_LIVE') return { state: 'exec', cls: 'chip chip--up', word: W.dExec, literal: null };
    if (o === 'EXECUTION_FAILED') return { state: 'fail', cls: 'chip chip--warn', word: W.dFail, literal: null };
    if (o === 'REJECTED_ON_RECHECK') return { state: 'rej', cls: 'chip chip--down', word: W.dRej, literal: null };
    if (!o) return { state: 'unread', cls: 'chip dl-unread', word: W.dUnread, literal: null };
    return { state: 'other', cls: 'chip dl-unread', word: null, literal: o };
  }

  /**
   * THE FILL CELL. state: 'omitted' | 'open' | 'pnl' | 'unpriced' | 'hidden' | 'unshown'.
   *   omitted   no result and never executed: there was never a fill to report
   *   open      executed live, no close on record
   *   pnl       a number (the renderer colours it through pnlClass)
   *   unpriced  the engine recorded no P&L for this close (pnl_usd null, or
   *             the anonymous scrub dropped a key the server says held none)
   *   hidden    the scrub dropped a number the server says was there
   *   unshown   the scrub dropped the key and the server did not say which
   */
  function fill(rec, anonymous) {
    const res = rec && rec.result;
    const out = String(rec && rec.outcome !== null && rec.outcome !== undefined ? rec.outcome : '');
    if (res === null || res === undefined || typeof res !== 'object') {
      return out === 'EXECUTED_LIVE'
        ? { state: 'open', word: W.open, pnl: null, price: null, reason: '' }
        : { state: 'omitted', word: null, pnl: null, price: null, reason: '' };
    }
    const px = price(res.exit_price);
    const reason = text(res.close_reason);
    if (Object.prototype.hasOwnProperty.call(res, 'pnl_usd')) {
      const p = pnl(res.pnl_usd);
      if (p !== null) return { state: 'pnl', word: null, pnl: p, price: px, reason: reason };
      return { state: 'unpriced', word: W.pnlUnrec, pnl: null, price: px, reason: reason };
    }
    if (anonymous) {
      if (res.fill_priced === true) return { state: 'hidden', word: W.hidden, pnl: null, price: px, reason: reason };
      if (res.fill_priced === false) return { state: 'unpriced', word: W.pnlUnrec, pnl: null, price: px, reason: reason };
      return { state: 'unshown', word: W.unshown, pnl: null, price: px, reason: reason };
    }
    return { state: 'unpriced', word: W.pnlUnrec, pnl: null, price: px, reason: reason };
  }

  const THESIS_MAX = 180;

  function decisionRow(rec, anonymous) {
    const r = rec || {};
    const ms = time(r.timestamp);
    const rawSym = text(r.symbol);
    const sym = rawSym.replace(':USDT', '').replace('/USDT', '');
    const idea = (r.idea && typeof r.idea === 'object') ? r.idea : {};
    const thesis = text(idea.reasoning);
    const seqOk = r.chain && typeof r.chain.sequence === 'number' && isFinite(r.chain.sequence);
    return {
      kind: 'decision', ms: ms, when: ms === null ? W.noTime : null, timestamp: ms === null ? null : r.timestamp,
      sym: sym || null, symWord: sym ? null : W.noSym,
      side: side(idea.direction), sideWord: side(idea.direction) ? null : W.noDir,
      thesis: thesis || null, thesisWord: thesis ? null : W.noThesis, thesisCut: thesis.length > THESIS_MAX,
      gate: gate(r), disposition: disposition(r), fill: fill(r, anonymous),
      seq: seqOk ? r.chain.sequence : null,
    };
  }

  function incidentRow(inc) {
    const i = inc || {};
    const ms = time(i.ts);
    const rawSym = text(i.symbol);
    const sym = rawSym.replace(':USDT', '').replace('/USDT', '');
    const kind = i.kind === 'block' ? { cls: 'chip chip--down', word: W.kBlock, literal: null }
      : i.kind === 'recovery' ? { cls: 'chip chip--info', word: W.kRec, literal: null }
      : i.kind === 'flag' ? { cls: 'chip chip--warn', word: W.kFlag, literal: null }
      : { cls: 'chip dl-unread', word: W.kUnread, literal: null };
    const detail = text(i.detail);
    const seqOk = i.chain && typeof i.chain.sequence === 'number' && isFinite(i.chain.sequence);
    return {
      kind: 'incident', ms: ms, when: ms === null ? W.noTime : null, timestamp: ms === null ? null : i.ts,
      sym: sym || null, symWord: sym ? null : W.noMarket,
      detail: detail || null, detailWord: detail ? null : W.noDetail,
      chip: kind, category: text(i.category),
      seq: seqOk ? i.chain.sequence : null,
    };
  }

  /**
   * Classify the two fetch results. `fr` is the flight read (GUARDED: the
   * loader has already run mustRead on it); `ir` the incidents read, or null
   * when the fetch itself rejected.
   *   A 200 whose body did not parse is NOT a reading: fetchJSON answers
   *   {ok:true, data:null} for it and mustRead hands the null through, which
   *   the empty state would then render as "nothing sealed yet" — a confident
   *   negative about the safety ledger off a body nobody could read (a proxy
   *   page, an SPA fallback). It throws. A 404 keeps mustRead's empty doctrine.
   *   The incident stream is a reading only when it answered 200 with an
   *   object; anything else is null and the log NAMES the omission.
   */
  function reading(fr, ir) {
    if (fr && fr.ok && (fr.data === null || fr.data === undefined || typeof fr.data !== 'object')) {
      const e = new Error('decision log: 200 with an unreadable body');
      e.status = 0;
      throw e;
    }
    const flight = (fr && fr.ok) ? fr.data : null;
    const incidents = (ir && ir.ok && ir.data && typeof ir.data === 'object') ? ir.data : null;
    return { flight: flight, incidents: incidents };
  }

  /**
   * The whole log. Returns null for a READ empty ledger with a READ incident
   * stream (renderPanel's empty state); THROWS for an empty ledger beside an
   * unreadable incident stream.
   */
  function decisionLog(flight, incidents) {
    if (!flight || typeof flight !== 'object') return null;
    const recs = Array.isArray(flight.records) ? flight.records : [];
    const anonymous = !!flight.disclosure;
    const notes = [];
    let blocks = [];
    if (incidents === null || incidents === undefined) {
      notes.push({ loud: true, word: W.incUnread });
    } else {
      blocks = Array.isArray(incidents.incidents) ? incidents.incidents : [];
      if (incidents.derived === true) notes.push({ loud: true, word: W.incDerived });
      else if (!blocks.length) notes.push({ loud: false, word: W.incNone });
    }
    if (!recs.length && (incidents === null || incidents === undefined)) {
      const e = new Error('decision log: ledger empty and incident stream unreadable');
      e.status = 0;
      throw e;
    }
    const rows = recs.map(function (r) { return decisionRow(r, anonymous); })
      .concat(blocks.map(incidentRow));
    // Newest first; undated rows sink. Total comparator, so the sort is stable.
    rows.sort(function (a, b) {
      const ka = a.ms === null ? -Infinity : a.ms, kb = b.ms === null ? -Infinity : b.ms;
      if (ka === kb) return 0;
      return kb > ka ? 1 : -1;
    });
    if (!rows.length) return null;
    const at = time(flight.updated_at);
    return {
      anonymous: anonymous, notes: notes, rows: rows,
      footer: { scope: W.scope, written: W.written, when: at === null ? null : flight.updated_at, whenWord: at === null ? W.noTime : null, why: W.writtenWhy },
    };
  }

  return { decisionLog: decisionLog, reading: reading, decisionRow: decisionRow, incidentRow: incidentRow,
    gate: gate, disposition: disposition, fill: fill, checks: checks, price: price, pnl: pnl, time: time, side: side,
    THESIS_MAX: THESIS_MAX, W: W, KEYS: KEYS };
}));
