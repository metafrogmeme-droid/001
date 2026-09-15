/**
 * THE CHART READ — what a candle chart may CLAIM, and what it must say about
 * its own sample.
 *
 * The CROSSFIRE focus room puts a footnote under its chart —
 * "BITGET PUBLIC MIX CANDLES · 15M · 96 BARS · REAL ONLY" — and that line is
 * the whole difference between a read and an assertion: a chart drawn over 6
 * bars is visually indistinguishable from one drawn over 200, and so is every
 * verdict computed from it. RUNECLAW already had the focus room (the symbol
 * modal); what it did not have was the footnote, and measuring for one found
 * three things.
 *
 * ONE: the same four chips (VWAP · structure · BOS · CHoCH) were built TWICE
 * in dashboard.js — the Markets view and the symbol modal — with the same
 * bodies and DIFFERENT sample gates. Driven over identical candles:
 *
 *      6 bars | Markets: VWAP below -0.15%  | Modal: (none)
 *     10 bars | Markets: VWAP above +0.05%  | Modal: (none)
 *     14 bars | Markets: VWAP below -0.13%  | Modal: (none)
 *     15 bars | both agree
 *
 * One symbol, one timeframe, two answers. Both copies were re-spelling floors
 * that `vwap()` and `structure()` already own (`< 5` and `< 15`, each in its
 * own first line), which is exactly how they came to disagree — a second copy
 * of a threshold is a second answer. So NO floor is spelled here either: this
 * model CALLS them and reads null.
 *
 * TWO: the Markets copy wrote its chips inside `if (rows && rows.length)`, so
 * a failed read never cleared the box and the PREVIOUS symbol's "VWAP above
 * +0.31% · structure bullish · BOS ↑" stayed on screen beside the new
 * symbol's error panel — a confident directional verdict about asset B
 * assembled from asset A's candles. The modal copy cleared first. Fixing that
 * at each call site is seventeen chances to forget, so `chips()` answers an
 * EMPTY list for an unreadable sample and the renderer always writes: the
 * `_fmt_price(None)` rule, guarded at the boundary.
 *
 * THREE: `structure()` answers `ranging / bos:false / choch:false` when its
 * swing detector found fewer than two swings per side — the constructor's
 * defaults, returned before BOS or CHoCH is computed. Driven, a 40-bar
 * MONOTONE RAMP takes that branch: the strongest trend there is, reported as
 * "ranging", beside a chart that is visibly a straight line up. That is
 * unreadable rendered as a measurement, on a directional read. `structure()`
 * now says whether its verdict was measured — set where the knowledge is,
 * never re-derived here — and an unmeasured one gets words, not a verdict.
 *
 * What this model does NOT do: re-implement any of the maths. `chartread.js`
 * mirrors the engine's own session-VWAP and fractal-structure rules so the
 * website never invents a different read, and that stays the one source.
 */
(function (root, factory) {
  const api = factory();
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.ChartReadModel = api;
}(typeof self !== 'undefined' ? self : this, function () {
  'use strict';

  const W = {
    // — the chips —
    vwapAbove:  { key: 'dd.cr_vwap_above',  en: 'VWAP above' },
    vwapBelow:  { key: 'dd.cr_vwap_below',  en: 'VWAP below' },
    vwapAt:     { key: 'dd.cr_vwap_at',     en: 'at VWAP' },
    stBull:     { key: 'dd.cr_st_bull',     en: 'structure bullish' },
    stBear:     { key: 'dd.cr_st_bear',     en: 'structure bearish' },
    stRange:    { key: 'dd.cr_st_range',    en: 'structure ranging' },
    stUnread:   { key: 'dd.cr_st_unread',   en: 'structure unreadable — no swings found' },
    bosUp:      { key: 'dd.cr_bos_up',      en: 'BOS up' },
    bosDown:    { key: 'dd.cr_bos_down',    en: 'BOS down' },
    chochUp:    { key: 'dd.cr_choch_up',    en: 'CHoCH up' },
    chochDown:  { key: 'dd.cr_choch_down',  en: 'CHoCH down' },
    // — why a reading is absent —
    thin:       { key: 'dd.cr_thin',        en: 'too few bars to read — {n} on record' },
    noBars:     { key: 'dd.cr_no_bars',     en: 'the venue answered no candles for this pair' },
    unread:     { key: 'dd.cr_unread',      en: 'the candles could not be read' },
    // — the footnote —
    bars:       { key: 'dd.cr_bars',        en: '{n} bars' },
    dropped:    { key: 'dd.cr_dropped',     en: '{n} unreadable row(s) dropped' },
    deduped:    { key: 'dd.cr_deduped',     en: '{n} repeated timestamp(s) dropped' },
    formulas:   { key: 'dd.cr_formulas',    en: 'engine formulas' },
    // A fact about a DIFFERENT read — the modal's levels and waves come from
    // the 4h insight call and do not follow the timeframe buttons. It lives
    // in this table rather than at the call site so the renderer spells no
    // key of its own; the caller asks for it with a flag.
    levels4h:   { key: 'dd.cr_levels_4h',   en: 'levels & waves from the 4h read' },
    swingsFrom: { key: 'dd.cr_swings_from', en: 'read from {n} swing(s) each side' },
    sessionVw:  { key: 'dd.cr_session_vw',  en: 'session VWAP' },
    windowVw:   { key: 'dd.cr_window_vw',   en: 'full-window VWAP — this session traded no volume' },
  };

  const KEYS = Object.keys(W).map(function (k) { return W[k].key; });

  /** A finite number, or null. A numeric STRING is junk, not a value. */
  function num(v) {
    return (typeof v === 'number' && isFinite(v)) ? v : null;
  }

  /**
   * THE SAMPLE — three counts, and the gaps between them.
   *
   * `answered` is what the venue sent, `parsed` what survived
   * `parseCandles` (it drops rows shorter than five fields and rows whose
   * OHLC will not parse, silently), and `drawn` what actually reached the
   * chart (the TV path additionally drops repeated timestamps at the live
   * edge — "Bitget can echo a candle twice"). Three numbers that were never
   * the same and were never stated.
   *
   * `drawn` is optional: the SVG path draws every parsed bar, so a caller
   * with no separate draw count passes none and `drawn` is `parsed`.
   */
  function sample(answered, parsed, drawn) {
    const a = num(answered), p = num(parsed);
    if (a === null || p === null || a < 0 || p < 0) return null;
    const d = num(drawn) === null ? p : Math.max(0, num(drawn));
    return {
      answered: a,
      parsed: p,
      drawn: d,
      dropped: Math.max(0, a - p),
      deduped: Math.max(0, p - d),
    };
  }

  /**
   * THE CHIPS, as data — never markup, and never a colour the renderer chose.
   *
   * `read` is the chartread module (injected, so this stays pure and the
   * guard can drive it). A missing module is not an empty read: the caller
   * gets `null` and renders the unread sentence, because chips absent because
   * a script failed to load look exactly like chips absent because the market
   * is quiet.
   *
   * Every item carries `bars`, so a verdict off 6 bars and one off 200 stop
   * rendering identically.
   */
  function chips(parsed, read) {
    if (!read || typeof read.vwap !== 'function' || typeof read.structure !== 'function') return null;
    const bars = (parsed && parsed.length) || 0;
    const items = [];

    // NO floor is spelled here. vwap() answers null under 5 bars and
    // structure() under 15, each in its own first line; re-stating either is
    // the second copy that made the two call sites disagree.
    const vw = read.vwap(parsed);
    if (vw) {
      const d = num(vw.dist_pct);
      if (d !== null) {
        // A price exactly AT the VWAP is neither above nor below it, and a
        // green "above +0.00%" is a claim the number does not make.
        const word = d > 0 ? W.vwapAbove : d < 0 ? W.vwapBelow : W.vwapAt;
        items.push({
          kind: 'vwap',
          word: word,
          pct: d === 0 ? null : d,
          cls: d > 0 ? 'chip chip--up' : d < 0 ? 'chip chip--down' : 'chip',
          bars: bars,
        });
      }
    }

    const st = read.structure(parsed);
    if (st) {
      // `measured` is structure()'s OWN answer about whether it found swings
      // to read. Re-deriving it from st.swings here would be the second copy
      // again, one function further out.
      if (st.measured === false) {
        items.push({ kind: 'structure', word: W.stUnread, cls: 'chip dl-unread', bars: bars, measured: false });
      } else {
        const s = String(st.structure || '');
        items.push({
          kind: 'structure',
          word: s === 'bullish' ? W.stBull : s === 'bearish' ? W.stBear : W.stRange,
          cls: s === 'bullish' ? 'chip chip--up' : s === 'bearish' ? 'chip chip--down' : 'chip',
          bars: bars,
          measured: true,
          swings: swingCount(st),
        });
        // BOS and CHoCH are reported only over a structure that was measured.
        // Under the unmeasured branch both are the constructor's `false`,
        // returned before either is computed — "no break of structure" said
        // without looking.
        if (st.bos) {
          items.push({ kind: 'bos', word: st.bos_dir > 0 ? W.bosUp : W.bosDown,
            cls: st.bos_dir > 0 ? 'chip chip--up' : 'chip chip--down', bars: bars });
        }
        if (st.choch) {
          items.push({ kind: 'choch', word: st.choch_dir > 0 ? W.chochUp : W.chochDown,
            cls: st.choch_dir > 0 ? 'chip chip--up' : 'chip chip--down', bars: bars });
        }
      }
    }
    return items;
  }

  /** The smaller of the two swing sides structure() read, or null. */
  function swingCount(st) {
    const sw = st && st.swings;
    const h = sw && sw.highs ? sw.highs.length : null;
    const l = sw && sw.lows ? sw.lows.length : null;
    if (h === null || l === null) return null;
    return Math.min(h, l);
  }

  /**
   * THE FOOTNOTE — what this chart is a picture OF.
   *
   * Three states, because "we could not ask", "the venue answered nothing"
   * and "here is what was drawn" are three different facts and only the last
   * one is a chart. `rows === null` is the failed read; an empty array is the
   * venue's own empty answer.
   */
  function provenance(opts) {
    const o = opts || {};
    const s = o.sample || null;
    const src = { venue: String(o.venue || '') || null, gran: String(o.gran || '') || null };
    if (o.rows === null || o.rows === undefined) {
      return { state: 'unread', word: W.unread, src: src, sample: null, parts: [] };
    }
    if (!s || s.answered === 0) {
      return { state: 'none', word: W.noBars, src: src, sample: s, parts: [] };
    }
    const parts = [{ word: W.bars, n: s.drawn }];
    // Only ever printed when something really was dropped: a permanent
    // "0 dropped" row trains the reader to stop reading the line.
    if (s.dropped > 0) parts.push({ word: W.dropped, n: s.dropped });
    if (s.deduped > 0) parts.push({ word: W.deduped, n: s.deduped });
    return { state: 'read', word: null, src: src, sample: s, parts: parts };
  }

  /**
   * THE WHOLE READ, for a caller that has candles and wants both halves.
   *
   * `rows === null` means the fetch failed; `[]` means the venue answered
   * with no candles. Neither is a chart, and neither may leave a previous
   * symbol's verdict on screen — so both answer an EMPTY chip list rather
   * than no answer at all.
   */
  function chartRead(rows, read, opts) {
    const o = opts || {};
    // No module is no read, whatever the venue sent. Counting rows nobody
    // could parse as "40 bars" would put a confident footnote over an empty
    // verdict list — two answers about one read, which is the defect this
    // model exists to end rather than to commit.
    const usable = !!(read && typeof read.parseCandles === 'function');
    const parsed = (rows && usable) ? read.parseCandles(rows) : [];
    const s = (rows && usable) ? sample(rows.length, parsed.length, o.drawn) : null;
    const prov = provenance({ rows: usable ? rows : null, sample: s, venue: o.venue, gran: o.gran });
    if (prov.state !== 'read') return { provenance: prov, items: [], parsed: parsed, thin: null };
    const items = chips(parsed, read);
    if (items === null) return { provenance: prov, items: [], parsed: parsed, thin: W.unread };
    // Nothing to say is not the same as nothing to read: under every floor
    // the module owns, the sample itself is the answer.
    return {
      provenance: prov,
      items: items,
      parsed: parsed,
      thin: items.length ? null : W.thin,
      thinN: items.length ? null : parsed.length,
    };
  }

  return {
    chartRead: chartRead, chips: chips, sample: sample, provenance: provenance,
    swingCount: swingCount, W: W, KEYS: KEYS,
  };
}));
