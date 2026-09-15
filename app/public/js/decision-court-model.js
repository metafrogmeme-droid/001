/**
 * THE DECISION COURT — one sealed decision, in full, as a PURE function of
 * `GET /api/guardian/flight/:decisionId`.
 *
 * WHY IT EXISTS. That route has been served since the Guardian slice: it
 * returns the whole record — idea, risk, macro, compliance, result,
 * explanation — PLUS the hash chain, scrubbed for anonymous callers. Grepped
 * across `app/public/`, it had ZERO readers; only a test called it. A dossier
 * endpoint served to nobody is the shape this repo already records one
 * granularity down (a value computed on every turn and delivered to nothing),
 * with the arrow pointed at a route.
 *
 * The decision LOG renders a row — event, thesis, gate, disposition, fill —
 * and stops there. The thesis is cut at 180 characters, every named risk
 * check beyond the count is dropped, `macro` and `compliance` are never shown
 * at all, and the chain is nowhere on the row. The chain is the half that
 * makes a record EVIDENCE rather than a claim, so the Court leads with it.
 *
 * ONE READING, TWO READERS. Every judgement this file needs — what a price of
 * 0 means, when a gate verdict is a verdict, the six fill states, the
 * direction vocabulary — already exists in `DecisionLogModel` and is read
 * from there. A second copy would agree on every fixture and diverge on the
 * first edit to either, which is what a second copy looks like from outside
 * until the day it bites.
 *
 * WHAT IS THREE-VALUED HERE, AND WHY EACH ONE HAD TO BE:
 *  * CONFIDENCE of 0 is an ABSENCE, not "no confidence". `scan_skill` seals
 *    `float(cp.get("confidence", 0) or 0)`, so an unread confidence arrives
 *    as 0 — and 0 is also what a headline reading "at 0% confidence" would
 *    assert the agent acted on. Same rule the sibling applies to prices, for
 *    the same reason: the producer's `or 0` is why.
 *  * A SECTION THE RECORD DOES NOT CARRY is NAMED as not on record. It is
 *    never rendered empty and never rendered as zeros — "no checks failed"
 *    over a risk block nobody sealed is the all-clear, and this card is read
 *    to decide whether to trust the engine.
 *  * THE CHAIN is three-valued too. A record whose `chain` is missing is not
 *    a record that failed verification; it is one this window did not carry
 *    the proof for, and the word says that rather than implying a break.
 *  * THE FILL keeps the log's six states, INCLUDING the pair that separate
 *    "the anonymous scrub dropped a number that was there" from "no number
 *    was ever recorded" — `fill_priced` is the server's marker and the only
 *    thing that can tell them apart.
 *  * A 404 is "not in the recent window", which is a fact about the WINDOW.
 *    It is not a failed read and must not be rendered as one; the caller
 *    passes it in, because only the caller saw the status.
 *
 * No colour is decided here beyond the class a sealed verdict earns, and
 * every word is a KEY the renderer resolves — a section rendering English on
 * a fourteen-language page is the shape the deck's own i18n guard exists for.
 */
(function (root, factory) {
  const api = factory(typeof require === 'function' && typeof module !== 'undefined'
    ? require('./decision-log-model.js')
    : (typeof self !== 'undefined' ? self.DecisionLogModel : null));
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.DecisionCourtModel = api;
}(typeof self !== 'undefined' ? self : this, function (LOG) {
  'use strict';

  const W = {
    // Three words the SIBLING already owns. The Court says exactly what the
    // log row says about a missing time, symbol or direction, so it says it
    // with the same string — a second key carrying the same sentence is two
    // answers the moment one of them is reworded.
    noTime:      LOG.W.noTime,
    noSym:       LOG.W.noSym,
    noDir:       LOG.W.noDir,
    noConf:      { key: 'dd.dc_no_conf',      en: 'not on record' },
    bookPaper:   { key: 'dd.dc_book_paper',   en: 'paper' },
    bookLive:    { key: 'dd.dc_book_live',    en: 'live' },

    tChain:      { key: 'dd.dc_t_chain',      en: 'Chain' },
    tThesis:     { key: 'dd.dc_t_thesis',     en: 'Thesis' },
    tPlan:       { key: 'dd.dc_t_plan',       en: 'Plan' },
    tSetup:      { key: 'dd.dc_t_setup',      en: 'Setup' },
    tEvidence:   { key: 'dd.dc_t_evidence',   en: 'Evidence' },
    tGate:       { key: 'dd.dc_t_gate',       en: 'Risk verdict' },
    tMacro:      { key: 'dd.dc_t_macro',      en: 'Macro' },
    tCompliance: { key: 'dd.dc_t_compliance', en: 'Compliance' },
    tDecided:    { key: 'dd.dc_t_decided',    en: 'Decided by' },
    tExecution:  { key: 'dd.dc_t_execution',  en: 'Execution' },
    tRaw:        { key: 'dd.dc_t_raw',        en: 'Sealed record' },

    lSeq:        { key: 'dd.dc_l_seq',        en: 'sequence' },
    lEntry:      { key: 'dd.dc_l_entry',      en: 'entry hash' },
    lPrev:       { key: 'dd.dc_l_prev',       en: 'previous hash' },
    lEntryPx:    { key: 'dd.dc_l_entry_px',   en: 'entry' },
    lStop:       { key: 'dd.dc_l_stop',       en: 'stop' },
    lTarget:     { key: 'dd.dc_l_target',     en: 'target' },
    lRR:         { key: 'dd.dc_l_rr',         en: 'reward : risk' },
    lStrategy:   { key: 'dd.dc_l_strategy',   en: 'strategy' },
    lSignal:     { key: 'dd.dc_l_signal',     en: 'signal' },
    lVoters:     { key: 'dd.dc_l_voters',     en: 'top voters' },
    lFor:        { key: 'dd.dc_l_for',        en: 'for' },
    lAgainst:    { key: 'dd.dc_l_against',    en: 'against' },
    lReason:     { key: 'dd.dc_l_reason',     en: 'reason' },
    lFailedChk:  { key: 'dd.dc_l_failed_chk', en: 'failed checks' },
    lModel:      { key: 'dd.dc_l_model',      en: 'model' },
    lVersion:    { key: 'dd.dc_l_version',    en: 'analysis version' },
    lPrompt:     { key: 'dd.dc_l_prompt',     en: 'prompt hash' },
    lThin:       { key: 'dd.dc_l_thin',       en: 'data was thin' },
    lDisp:       { key: 'dd.dc_l_disp',       en: 'disposition' },
    lFill:       { key: 'dd.dc_l_fill',       en: 'fill' },
    lExitPx:     { key: 'dd.dc_l_exit_px',    en: 'exit' },
    lCloseWhy:   { key: 'dd.dc_l_close_why',  en: 'closed because' },
    lWhen:       { key: 'dd.dc_l_when',       en: 'when' },
    lSym:        { key: 'dd.dc_l_sym',        en: 'symbol' },
    lSide:       { key: 'dd.dc_l_side',       en: 'direction' },
    lConf:       { key: 'dd.dc_l_conf',       en: 'confidence' },
    lBook:       { key: 'dd.dc_l_book',       en: 'book' },

    // The modal's own chrome. It lives HERE, not in the renderer, because a
    // key the renderer spells and the model does not is a second vocabulary
    // — the split the risk-backstop panel's guard exists to forbid.
    title:       { key: 'dd.dc_title',        en: 'Decision Court' },
    sub:         { key: 'dd.dc_sub',          en: 'The full sealed record behind this decision — nothing added, nothing inferred.' },
    close:       { key: 'dd.dc_close',        en: 'Close' },
    unread:      { key: 'dd.dc_unread',       en: 'This decision could not be read. Nothing here is a statement about what the agent decided — the record was not retrieved.' },

    noChain:     { key: 'dd.dc_no_chain',     en: 'This record arrived without its chain link — the window did not carry the proof, which is not the same as a broken chain.' },
    noThesis:    { key: 'dd.dc_no_thesis',    en: 'No thesis on record. The agent sealed a decision without prose reasoning; nothing here stands in for it.' },
    noPlan:      { key: 'dd.dc_no_plan',      en: 'No levels on record — the seal carries no entry, stop or target for this decision.' },
    noSetup:     { key: 'dd.dc_no_setup',     en: 'No setup on record.' },
    noEvidence:  { key: 'dd.dc_no_evidence',  en: 'No per-factor evidence on record.' },
    noGate:      { key: 'dd.dc_no_gate',      en: 'No risk block on record. This is not a pass: nothing about the gate can be read from this record.' },
    gateUnread:  { key: 'dd.dc_gate_unread',  en: 'The gate verdict was sealed as unknown — the recorder could not read the risk object at seal time. This is not a pass.' },
    noChecks:    { key: 'dd.dc_no_checks',    en: 'The verdict is sealed; the individual checks behind it are not on this record.' },
    noMacro:     { key: 'dd.dc_no_macro',     en: 'No macro block on record.' },
    noCompl:     { key: 'dd.dc_no_compl',     en: 'No compliance block on record.' },
    noDecided:   { key: 'dd.dc_no_decided',   en: 'No provenance on record — the seal does not say which model or analysis version decided this.' },
    noExec:      { key: 'dd.dc_no_exec',      en: 'Nothing was sent to a venue for this decision, so there is no execution to report.' },
    nothingShown: { key: 'dd.dc_nothing',     en: 'On record, but nothing in it is a value this card can show.' },
    notFound:    { key: 'dd.dc_not_found',    en: 'This decision is no longer in the recent window the bot publishes. The sealed record still exists in the chain; it is just not in this view.' },
    anonNote:    { key: 'dd.dc_anon',         en: 'Anonymous view — percent, ratio and count only. Dollar amounts are withheld here, not missing from the record.' },
  };
  // Only the keys this model INTRODUCES — the three borrowed from the log
  // model are that model's to declare, and counting them twice would make
  // each file look like it owns the other's vocabulary.
  const KEYS = Object.keys(W).map(function (k) { return W[k].key; })
    .filter(function (k) { return k.indexOf('dd.dc_') === 0; });

  /** A non-empty trimmed string, or ''. */
  function text(v) {
    return (typeof v === 'string' && v.trim()) ? v.trim() : '';
  }

  /** A finite number, or null. Unlike a price, 0 survives — callers that must
   *  reject it say so at their own site, because the reason differs per
   *  field and a shared "non-zero" would bury it. */
  function num(v) {
    return (typeof v === 'number' && isFinite(v)) ? v : null;
  }

  /** CONFIDENCE. A finite number in (0, 1], or null.
   *  0 is rejected because `scan_skill` seals `... or 0` for an unread one,
   *  and "acted at 0% confidence" is a claim no record here can support.
   *  Out of range is not a measurement either. */
  function confidence(v) {
    const n = num(v);
    if (n === null || n <= 0 || n > 1) return null;
    return n;
  }

  /** Strings from an array, trimmed and non-empty, capped. */
  function strings(v, cap) {
    if (!Array.isArray(v)) return [];
    const out = [];
    for (let i = 0; i < v.length && out.length < cap; i += 1) {
      const s = text(v[i]);
      if (s) out.push(s);
    }
    return out;
  }

  /** A row whose value is a plain scalar the card can print, or null.
   *  Objects and arrays of objects are DROPPED rather than stringified: a
   *  row reading "[object Object]" claims a value was shown. */
  function scalar(v) {
    if (typeof v === 'string') return text(v) || null;
    if (typeof v === 'number') return isFinite(v) ? String(v) : null;
    if (typeof v === 'boolean') return v ? 'yes' : 'no';
    if (Array.isArray(v)) {
      const s = strings(v, 8);
      return s.length ? s.join(' · ') : null;
    }
    return null;
  }

  /** An opaque sealed block (macro, compliance) as label/value rows.
   *  Deliberately GENERIC: their shapes are not pinned anywhere a client can
   *  read, and inventing a schema for them here would be a second answer
   *  about what they contain. Keys are printed as the record spells them. */
  function blockRows(obj, cap) {
    if (obj === null || obj === undefined || typeof obj !== 'object' || Array.isArray(obj)) return null;
    const rows = [];
    const keys = Object.keys(obj);
    for (let i = 0; i < keys.length && rows.length < cap; i += 1) {
      const val = scalar(obj[keys[i]]);
      if (val !== null) rows.push({ label: keys[i], value: val });
    }
    return rows;
  }

  function section(id, title, rows, emptyWord) {
    if (!rows || !rows.length) return { id: id, title: title, word: emptyWord, rows: null };
    return { id: id, title: title, word: null, rows: rows };
  }

  /**
   * THE READING. `payload` is the route's body ({record, chain, disclosure}).
   * `status` is the HTTP status the caller saw, because a 404 here means
   * "outside the published window" and only the caller knows it happened.
   *
   * Returns null for nothing to show; throws for a body that did not parse
   * into a record, because an empty dossier over an unreadable 200 would
   * assert the chain holds nothing for this decision.
   */
  function decisionCourt(payload, status) {
    if (status === 404) return { notFound: true, word: W.notFound };
    if (payload === null || payload === undefined || typeof payload !== 'object') {
      const e = new Error('decision court: no body');
      e.status = 0;
      throw e;
    }
    const rec = payload.record;
    if (rec === null || rec === undefined || typeof rec !== 'object') {
      const e = new Error('decision court: body carried no record');
      e.status = 0;
      throw e;
    }
    const anonymous = !!text(payload.disclosure);
    const idea = (rec.idea && typeof rec.idea === 'object' && !Array.isArray(rec.idea)) ? rec.idea : {};
    const risk = (rec.risk && typeof rec.risk === 'object' && !Array.isArray(rec.risk)) ? rec.risk : null;
    const ms = LOG.time(rec.timestamp);
    const rawSym = text(rec.symbol);
    const sym = rawSym.replace(':USDT', '').replace('/USDT', '');
    const dir = LOG.side(idea.direction);
    const conf = confidence(idea.confidence);

    // ── the strip ──
    const head = {
      when: ms, whenWord: ms === null ? W.noTime : null, timestamp: ms === null ? null : rec.timestamp,
      sym: sym || null, symWord: sym ? null : W.noSym,
      side: dir, sideWord: dir ? null : W.noDir,
      conf: conf, confWord: conf === null ? W.noConf : null,
      book: rec.is_paper === false ? 'live' : 'paper',
      bookWord: rec.is_paper === false ? W.bookLive : W.bookPaper,
      action: LOG.disposition(rec),
    };

    // ── the chain: RUNECLAW's own half, and the reason this is evidence ──
    const ch = (rec.chain && typeof rec.chain === 'object') ? rec.chain : {};
    const seq = num(ch.sequence);
    const entryHash = text(ch.entry_hash);
    const prevHash = text(ch.prev_hash);
    const chainRows = [];
    if (seq !== null) chainRows.push({ label: W.lSeq, value: String(seq), mono: true });
    if (entryHash) chainRows.push({ label: W.lEntry, value: entryHash, mono: true });
    if (prevHash) chainRows.push({ label: W.lPrev, value: prevHash, mono: true });

    // ── the plan: a sealed 0 is the absence, per the sibling's `price` ──
    const planRows = [];
    const entry = LOG.price(idea.entry);
    const stop = LOG.price(idea.sl);
    const target = LOG.price(idea.tp);
    const rr = num(idea.rr);
    if (entry !== null) planRows.push({ label: W.lEntryPx, value: entry, price: true });
    if (stop !== null) planRows.push({ label: W.lStop, value: stop, price: true });
    if (target !== null) planRows.push({ label: W.lTarget, value: target, price: true });
    if (rr !== null && rr > 0) planRows.push({ label: W.lRR, value: String(rr) });

    // ── setup ──
    const setupRows = [];
    const strat = text(idea.strategy_type);
    const sig = text(idea.signal_type);
    const voters = (Array.isArray(idea.votes) ? idea.votes : [])
      .filter(function (v) { return v && typeof v === 'object'; })
      .map(function (v) { return text(v.name); })
      .filter(function (s) { return !!s; })
      .slice(0, 5);
    if (strat) setupRows.push({ label: W.lStrategy, value: strat });
    if (sig) setupRows.push({ label: W.lSignal, value: sig });
    if (voters.length) setupRows.push({ label: W.lVoters, value: voters.join(' · ') });

    // ── evidence ──
    const ex = (idea.explain && typeof idea.explain === 'object') ? idea.explain : {};
    const bull = strings(ex.top_bullish, 5);
    const bear = strings(ex.top_bearish, 5);
    const evidenceRows = [];
    if (bull.length) evidenceRows.push({ label: W.lFor, value: bull.join('; ') });
    if (bear.length) evidenceRows.push({ label: W.lAgainst, value: bear.join('; ') });

    // ── the gate: the sibling's verdict, with the checks this card can show ──
    const gate = LOG.gate(rec);
    const gateRows = [];
    let gateWord = null;
    if (risk === null) {
      gateWord = W.noGate;
    } else {
      const why = LOG.checks(risk);
      if (why.names.length) gateRows.push({ label: W.lFailedChk, value: why.names.join(' · '), bad: true });
      if (why.reason) gateRows.push({ label: W.lReason, value: why.reason });
      if (!gateRows.length) gateWord = gate.state === 'unread' ? W.gateUnread : W.noChecks;
    }

    // ── provenance ──
    const prov = (idea.provenance && typeof idea.provenance === 'object') ? idea.provenance : {};
    const decidedRows = [];
    const model = text(prov.model_provider);
    const ver = text(prov.analysis_version);
    const ph = text(prov.prompt_hash);
    if (model) decidedRows.push({ label: W.lModel, value: model });
    if (ver) decidedRows.push({ label: W.lVersion, value: ver });
    if (ph) decidedRows.push({ label: W.lPrompt, value: ph, mono: true });
    if (prov.data_thin === true) decidedRows.push({ label: W.lThin, value: 'yes', bad: true });

    // ── execution: the disposition and the six-state fill, both the log's ──
    const fill = LOG.fill(rec, anonymous);
    const execRows = [];
    if (head.action.state !== 'unread' || fill.state !== 'omitted') {
      execRows.push({ label: W.lDisp, chip: head.action });
    }
    if (fill.state !== 'omitted') {
      execRows.push({ label: W.lFill, fill: fill });
      if (fill.price !== null) execRows.push({ label: W.lExitPx, value: fill.price, price: true });
      if (fill.reason) execRows.push({ label: W.lCloseWhy, value: fill.reason });
    }

    let raw = '';
    try {
      raw = JSON.stringify(rec, null, 2);
    } catch (err) {
      raw = '';
    }

    return {
      notFound: false,
      anonymous: anonymous,
      anonWord: anonymous ? W.anonNote : null,
      id: text(rec.decision_id) || null,
      head: head,
      gate: gate,
      sections: [
        section('chain', W.tChain, chainRows, W.noChain),
        section('thesis', W.tThesis,
          text(idea.reasoning) ? [{ prose: text(idea.reasoning) }] : null, W.noThesis),
        section('plan', W.tPlan, planRows, W.noPlan),
        section('setup', W.tSetup, setupRows, W.noSetup),
        section('evidence', W.tEvidence, evidenceRows, W.noEvidence),
        { id: 'gate', title: W.tGate, word: gateWord, rows: gateRows.length ? gateRows : null },
        section('macro', W.tMacro, blockRows(rec.macro, 12),
          rec.macro === null || rec.macro === undefined ? W.noMacro : W.nothingShown),
        section('compliance', W.tCompliance, blockRows(rec.compliance, 12),
          rec.compliance === null || rec.compliance === undefined ? W.noCompl : W.nothingShown),
        section('decided', W.tDecided, decidedRows, W.noDecided),
        section('execution', W.tExecution, execRows, W.noExec),
      ],
      raw: raw,
    };
  }

  return { decisionCourt: decisionCourt, confidence: confidence, blockRows: blockRows,
    scalar: scalar, W: W, KEYS: KEYS };
}));
