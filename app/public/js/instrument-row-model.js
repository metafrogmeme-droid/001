/**
 * ONE INSTRUMENT ROW, as a PURE function of one /api/positions row and two
 * reads of the public reference feed.
 *
 * The row answers four questions about one open position and every one has
 * THREE answers, not two:
 *   READ        the source answered and carried a usable value.
 *   UNREADABLE  the source was asked and the ask failed, or answered with a
 *               value nobody can use. We know we do not know.
 *   ABSENT      the source answered and the value genuinely is not there —
 *               this instrument is not on the reference feed, this position
 *               has no stop on record. A different fact and a different next
 *               step, so it gets a different sentence.
 *
 *  * DIRECTION IS THE SIGN OF EVERY NUMBER HERE, and it arrives raw: the live
 *    row publishes `getattr(pos, "direction", "")` unnormalised, and an adopted
 *    position's side is a venue-supplied string. `side()` shares dirChip's
 *    vocabulary (app.js: LONG/BUY, SHORT/SELL, everything else DECLINES) so
 *    one row cannot carry two opposite direction claims — a two-way test would
 *    have rendered 'BUY', 'UNKNOWN' and '' as a confident, coloured SHORT.
 *  * EACH ABSENCE HAS ITS OWN REASON, computed where the absence is decided.
 *    The move cell does not borrow the mark's sentence: an adopted position's
 *    entry is recorded as 0.0 and named in `adoption_unread`, so "entry unread
 *    + mark READ" is reachable, and a move cell blaming the feed beside a
 *    printed mark would be a false statement about a read that succeeded.
 *  * A FLAT SPARKLINE IS A MEASUREMENT. A line is drawn over closes that were
 *    read and differ; a window that was read and is genuinely flat is SAID in
 *    words; a window that could not be read draws nothing and says so.
 *  * R IS UNKNOWN, NOT ZERO, when there is no usable stop. The paper row
 *    builder passes `stop_loss` through raw, so 0.0 arrives for an absent
 *    stop, and abs(entry - 0) == entry makes r = move/entry — a different
 *    quantity in the right units. A stop AT entry has no risk distance and is
 *    the same absence, decided beside the stop read, before the mark is asked.
 *  * THE STOP CHIP READS THE LEVEL AS WELL AS THE FLAGS. The paper row emits
 *    `sl_order: 'manual', unprotected: false` unconditionally, so a paper
 *    position with no stop would otherwise wear "bot-managed" — the most
 *    reassuring label on the panel — beside "stop none on record".
 *  * NO DOLLAR EXPOSURE AND NO LEVERAGE. `size_usd` is notional on a paper
 *    row and margin-or-notional on a live one, and `leverage` defaults to 1.0
 *    for a field nobody read. Quantity is the one size figure with one meaning.
 *  * THE MARK IS NOT YOUR VENUE'S MARK: /api/market/tickers is Bitget USDT
 *    futures, a public reference price. The renderer says so under the rows.
 *
 * No colour is decided here. The renderer routes every class through
 * moveClass, which answers '' for a value nobody read. Written clean of the
 * honesty ratchet's four shapes: no `|| 0`, `?? 0`, `Number(x) || 0`, and no
 * bare `x >= 0 ? a : b` verdict.
 */
(function (root, factory) {
  const api = factory();
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.InstrumentRowModel = api;
}(typeof self !== 'undefined' ? self : this, function () {
  'use strict';

  const DASH = '—';

  const W = {
    sparkUnread:  { key: 'dd.ir_spark_unread',  en: 'price history not read' },
    sparkAbsent:  { key: 'dd.ir_spark_absent',  en: 'not on the reference feed' },
    sparkThin:    { key: 'dd.ir_spark_thin',    en: 'too few closes to draw' },
    sparkFlat:    { key: 'dd.ir_spark_flat',    en: 'unchanged across {n} closes' },
    markUnread:   { key: 'dd.ir_mark_unread_t', en: 'The reference price feed could not be read for this instrument. A website read, not a statement about your position.' },
    markAbsent:   { key: 'dd.ir_mark_absent_t', en: 'This instrument is not on the reference price feed, so no mark could be looked up for it.' },
    moveNoEntry:  { key: 'dd.ir_move_noentry_t', en: 'No entry price on record for this position, so the move from entry cannot be measured.' },
    moveNoDir:    { key: 'dd.ir_move_nodir_t',  en: 'The position’s direction could not be read, so the move has no sign.' },
    rNoStop:      { key: 'dd.ir_r_nostop',      en: 'R unknown — no stop on record for this position. It is not 0R.' },
    rNoEntry:     { key: 'dd.ir_r_noentry',     en: 'R unknown — no entry price on record for this position.' },
    rNoMark:      { key: 'dd.ir_r_nomark',      en: 'R unknown — the mark could not be read, so there is nothing to measure against the stop.' },
    rNoDir:       { key: 'dd.ir_r_nodir',       en: 'R unknown — the position’s direction could not be read.' },
    markUnknown:  { key: 'dd.ir_mark_unknown',  en: 'mark —' },
    moveUnknown:  { key: 'dd.ir_move_unknown',  en: 'move —' },
    rUnknown:     { key: 'dd.ir_r_unknown',     en: 'R —' },
    stopUnknown:  { key: 'dd.ir_stop_unknown',  en: '🛑 stop unknown' },
    stopUnprot:   { key: 'dd.ir_stop_unprot',   en: '⚠️ unprotected' },
    stopExch:     { key: 'dd.ir_stop_exch',     en: '🛡️ on exchange' },
    stopManaged:  { key: 'dd.ir_stop_managed',  en: '🤖 bot-managed' },
    stopNone:     { key: 'dd.ir_stop_none',     en: 'no stop on record' },
    entry:        { key: 'dd.ir_entry',         en: 'entry' },
    stop:         { key: 'dd.ir_stop',          en: 'stop' },
    target:       { key: 'dd.ir_target',        en: 'target' },
    noneOnRecord: { key: 'dd.ir_none_on_record', en: 'none on record' },
    dirNone:      { key: 'dd.ir_dir_none',      en: '— no direction' },
    footLive:     { key: 'dd.ir_foot_live',     en: 'Mark and price history are the public reference feed (Bitget USDT futures), not your venue. R is measured against the stop on record.' },
    footPaper:    { key: 'dd.ir_foot_paper',    en: 'Simulated paper book. Mark and price history are the public reference feed; R is measured against the stop on record.' },
    bookUnread:   { key: 'dd.ir_book_unread',   en: 'Your positions could not be read for this account — the bot has no executor attached to it. Nothing here is a statement about your book.' },
  };

  /** Every key this model can emit, for the translation guard. */
  const KEYS = Object.keys(W).map(function (k) { return W[k].key; });

  /** A finite positive price, or null. Never a default, never a zero. */
  function price(v) {
    if (v === null || v === undefined || typeof v === 'boolean') return null;
    if (typeof v === 'string' && v.trim() === '') return null;
    const n = Number(v);
    if (!isFinite(n)) return null;
    return n > 0 ? n : null;
  }

  /** A finite, non-zero quantity, or null. */
  function quantity(v) {
    if (v === null || v === undefined || typeof v === 'boolean') return null;
    if (typeof v === 'string' && v.trim() === '') return null;
    const n = Number(v);
    return isFinite(n) && n !== 0 ? n : null;
  }

  /**
   * 'long' | 'short' | null — dirChip's vocabulary (app.js), and nothing
   * else. null is the third answer, and the numbers below take it as their
   * own reason rather than falling through to a side.
   */
  function side(direction) {
    const d = String(direction === null || direction === undefined ? '' : direction).trim().toUpperCase();
    if (d === 'LONG' || d === 'BUY') return 'long';
    if (d === 'SHORT' || d === 'SELL') return 'short';
    return null;
  }

  /** The direction chip's class and words for a side. The muted chip is a
   *  DECLINE, not a third side. */
  function sideChip(s) {
    if (s === 'long') return { cls: 'chip chip--up', text: '▲ LONG', word: null };
    if (s === 'short') return { cls: 'chip chip--down', text: '▼ SHORT', word: null };
    return { cls: 'chip muted', text: null, word: W.dirNone };
  }

  /**
   * The reference feed's symbol for a position, or '' when there is none.
   * A URL is a surface: /api/market/* validates ^[A-Z0-9]{1,20}$, so the
   * slash cannot ride there. Only a USDT-quoted pair maps onto the feed at
   * all — answering base+'USDT' for BTC/USDC would price a DIFFERENT
   * instrument, a confident wrong number rather than a missing one.
   */
  function refFeedSymbol(symbol) {
    const parts = String(symbol === null || symbol === undefined ? '' : symbol).toUpperCase().split('/');
    if (parts.length < 2) return '';
    const base = parts[0].replace(/[^A-Z0-9]/g, '');
    const quote = parts[1].split(':')[0].replace(/[^A-Z0-9]/g, '');
    if (!base || quote !== 'USDT') return '';
    const sym = base + 'USDT';
    return /^[A-Z0-9]{1,20}$/.test(sym) ? sym : '';
  }

  /**
   * The mark for one instrument, from a CLASSIFIED tickers read:
   *   tickers = { state: 'read', map: {SYM: ticker} } | { state: 'unreadable' }
   * A feed nobody could fetch is UNREADABLE. A feed that answered and does
   * not carry this symbol is ABSENT (a fresh listing, a pair quoted in
   * something else, a venue the feed does not cover). A feed that carried
   * the symbol with an unusable price is unreadable too — it was meant to be
   * there. A position with no reference symbol at all is absent.
   */
  function markFrom(tickers, refSym) {
    if (!refSym) return { state: 'absent', value: null };
    if (!tickers || tickers.state !== 'read' || !tickers.map || typeof tickers.map !== 'object') {
      return { state: 'unreadable', value: null };
    }
    if (!Object.prototype.hasOwnProperty.call(tickers.map, refSym)) return { state: 'absent', value: null };
    const t = tickers.map[refSym];
    const p = price(t && (t.lastPr !== undefined ? t.lastPr : t.last));
    return p === null ? { state: 'unreadable', value: null } : { state: 'read', value: p };
  }

  /** Closes, oldest-first, from a Bitget candle payload (rows of
   *  [ts, open, high, low, close, ...]). Returns [] when the rows held no
   *  usable close — the CALLER decides what that means, because this
   *  function cannot tell a read that failed from one that found nothing. */
  function closes(rows) {
    return (Array.isArray(rows) ? rows : [])
      .map(function (r) { return { t: Number(r && r[0]), c: Number(r && r[4]) }; })
      .filter(function (p) { return isFinite(p.t) && isFinite(p.c) && p.c > 0; })
      .sort(function (a, b) { return a.t - b.t; })
      .map(function (p) { return p.c; });
  }

  /**
   * The sparkline's reading from a CLASSIFIED candles read:
   *   bars = { state: 'read', rows } | { state: 'unreadable' } | { state: 'absent' }
   * A 200 whose body is not an array is UNREADABLE — the same rule the mark
   * applies — never "not on the feed". Only a position with no reference
   * symbol, or a feed that answered with no candles at all, is absent.
   */
  function sparkFrom(bars) {
    if (!bars || bars.state === 'unreadable') return { state: 'unreadable', closes: [] };
    if (bars.state === 'absent') return { state: 'absent', closes: [] };
    if (bars.state !== 'read' || !Array.isArray(bars.rows)) return { state: 'unreadable', closes: [] };
    const cs = closes(bars.rows);
    if (cs.length === 0) return { state: 'absent', closes: [] };
    if (cs.length === 1) return { state: 'thin', closes: cs };
    const lo = Math.min.apply(null, cs), hi = Math.max.apply(null, cs);
    if (!(hi - lo > 0)) return { state: 'flat', closes: cs };
    return { state: 'read', closes: cs };
  }

  /** Polyline points for closes that were READ and differ; null otherwise.
   *  null means NO LINE IS DRAWN — the renderer never calls this for any
   *  other spark state, and a caller that does gets nothing to draw. */
  function sparkGeometry(closeList, W_, H_) {
    const cs = Array.isArray(closeList) ? closeList : [];
    if (cs.length < 2) return null;
    const lo = Math.min.apply(null, cs), hi = Math.max.apply(null, cs);
    if (!isFinite(lo) || !isFinite(hi) || !(hi - lo > 0)) return null;
    const span = hi - lo;
    const stepX = W_ / (cs.length - 1);
    const pts = cs.map(function (c, i) {
      const x = Math.round(i * stepX * 10) / 10;
      const y = Math.round(((hi - c) / span * (H_ - 2) + 1) * 10) / 10;
      return x + ',' + y;
    }).join(' ');
    return { points: pts, n: cs.length };
  }

  /**
   * Percent the price has moved IN THE POSITION'S OWN DIRECTION. Basis-free:
   * no leverage, no margin, no size — a fact about two prices.
   * { pct: number, why: null } | { pct: null, why: 'no_entry'|'no_direction'|'no_mark' }
   */
  function movePct(entry, mark, s) {
    const e = price(entry);
    if (e === null) return { pct: null, why: 'no_entry' };
    if (s !== 'long' && s !== 'short') return { pct: null, why: 'no_direction' };
    const m = price(mark);
    if (m === null) return { pct: null, why: 'no_mark' };
    const raw = (m - e) / e * 100;
    return { pct: Math.round((s === 'long' ? raw : -raw) * 100) / 100, why: null };
  }

  /**
   * R as of the mark, against the stop on record. The absences are NAMED,
   * and decided in the order the facts arrive: the entry, the direction, the
   * stop (a missing level OR a level at entry — no risk distance is the same
   * absence, and it is a fact about the stop, not about the mark), the mark.
   * { r: number, why: null } | { r: null, why: 'no_entry'|'no_direction'|'no_stop'|'no_mark' }
   */
  function rNow(entry, stop, mark, s) {
    const e = price(entry);
    if (e === null) return { r: null, why: 'no_entry' };
    if (s !== 'long' && s !== 'short') return { r: null, why: 'no_direction' };
    const st = price(stop);
    if (st === null) return { r: null, why: 'no_stop' };
    const risk = Math.abs(e - st);
    if (!(risk > 0)) return { r: null, why: 'no_stop' };
    const m = price(mark);
    if (m === null) return { r: null, why: 'no_mark' };
    const move = s === 'long' ? (m - e) : (e - m);
    return { r: Math.round(move / risk * 100) / 100, why: null };
  }

  /**
   * Five stop states. Order matters: `unprotected` is THREE-valued and null
   * is falsy, so the unread case is tested first or it lands on the most
   * reassuring label; then the venue's own alarm; then the LEVEL — no stop
   * on record is a fact the flags cannot override, because the paper row
   * builder stamps 'manual' + false unconditionally; then the two verdicts.
   */
  function stopState(pos) {
    const p = pos || {};
    if (p.sl_unknown || p.unprotected === null || p.unprotected === undefined) return 'unknown';
    if (p.unprotected === true) return 'unprotected';
    if (price(p.stop_loss) === null) return 'no_stop';
    if (p.sl_order === 'exchange') return 'exchange';
    return 'managed';
  }

  function stopChip(state) {
    if (state === 'unknown') return { cls: 'chip chip--down', word: W.stopUnknown };
    if (state === 'unprotected') return { cls: 'chip chip--down', word: W.stopUnprot };
    if (state === 'exchange') return { cls: 'chip chip--up', word: W.stopExch };
    if (state === 'no_stop') return { cls: 'chip muted', word: W.stopNone };
    return { cls: 'chip', word: W.stopManaged };
  }

  /**
   * @param pos      one row of /api/positions `positions[]`
   * @param tickers  the classified tickers read (see markFrom)
   * @param bars     the classified candles read for THIS instrument (see sparkFrom)
   * The two reads arrive ALREADY CLASSIFIED: the loader knows whether its
   * fetch threw, 502'd, or answered a map this symbol is not in, and this
   * function cannot recover any of that from a value.
   */
  function instrumentRow(pos, tickers, bars) {
    const p = pos || {};
    const symbol = String(p.symbol === null || p.symbol === undefined ? '' : p.symbol);
    const refSym = refFeedSymbol(symbol);
    const s = side(p.direction);
    const mark = markFrom(tickers, refSym);
    const markWhy = mark.state === 'read' ? null : (mark.state === 'absent' ? W.markAbsent : W.markUnread);
    const mv = movePct(p.entry_price, mark.value, s);
    const moveWhy = mv.why === 'no_entry' ? W.moveNoEntry
      : mv.why === 'no_direction' ? W.moveNoDir
      : mv.why === 'no_mark' ? markWhy : null;
    const r = rNow(p.entry_price, p.stop_loss, mark.value, s);
    const rWhy = r.why === 'no_entry' ? W.rNoEntry
      : r.why === 'no_direction' ? W.rNoDir
      : r.why === 'no_stop' ? W.rNoStop
      : r.why === 'no_mark' ? W.rNoMark : null;
    // No reference symbol, no candles to have read: absent at the boundary,
    // whatever a caller handed in for a symbol that cannot be on the feed.
    const sp = refSym ? sparkFrom(bars) : { state: 'absent', closes: [] };
    const sparkWord = sp.state === 'unreadable' ? W.sparkUnread
      : sp.state === 'absent' ? W.sparkAbsent
      : sp.state === 'thin' ? W.sparkThin
      : sp.state === 'flat' ? W.sparkFlat : null;
    const st = stopState(p);
    return {
      symbol: symbol,
      base: String(p.pair || symbol.split('/')[0] || ''),
      refSym: refSym,
      side: s,
      chip: sideChip(s),
      entry: price(p.entry_price),
      stop: price(p.stop_loss),
      target: price(p.take_profit),
      quantity: quantity(p.quantity),
      mark: { state: mark.state, value: mark.value, why: markWhy, label: W.markUnknown },
      move: { pct: mv.pct, why: moveWhy, label: W.moveUnknown },
      r: { value: r.r, why: rWhy, label: W.rUnknown },
      spark: { state: sp.state, closes: sp.closes, word: sparkWord, n: sp.closes.length },
      stop_state: st,
      stopChip: stopChip(st),
    };
  }

  /** Which sentence sits under the rows, for the book the payload names. A
   *  paper book has no venue at all; the row's mark is still the reference
   *  feed and says so. */
  function footer(live) {
    return live === true ? W.footLive : W.footPaper;
  }

  return {
    instrumentRow: instrumentRow, side: side, sideChip: sideChip, price: price, quantity: quantity,
    refFeedSymbol: refFeedSymbol, markFrom: markFrom, closes: closes, sparkFrom: sparkFrom,
    sparkGeometry: sparkGeometry, movePct: movePct, rNow: rNow, stopState: stopState,
    stopChip: stopChip, footer: footer, W: W, KEYS: KEYS, DASH: DASH,
  };
}));
