/**
 * The metric cluster's cells — equity, day P&L, open count — as a PURE
 * function of the /api/portfolio payload.
 *
 * It reads the payload's PROVENANCE, not its flags. `stale` is the server's
 * word about the equity row; `live_unavailable` is about the venue;
 * `unconfigured` is about the deployment and is stamped `stale: false` over a
 * dbFallback payload — so a client that inferred "memory" from `stale` called
 * a seconds-old scan-cache balance a memory whenever the snapshot beside it
 * was old, and called months-old stored rows a reading of now on a site whose
 * gateway secret had been rotated away. routes/portfolio.js names the source
 * of each figure on every branch now, and this file maps a source to a state:
 *
 *   gateway, scan_cache  -> READ    a figure the bot answered just now
 *   sync_rows            -> SYNCED  the bot's last push, as this site stored it
 *   snapshot, db_rows    -> MEMORY  the last value this site stored, after a
 *                                   read that did not happen — the band fires
 *   never_stored         -> UNREAD  nothing was ever stored: a dash with the
 *                                   reason, never a zero
 *   unread               -> UNREAD  the source could not be read (the venue's
 *                                   balance, most often), with the reason
 *   absent               -> no cell at all. A labelled dash claims we looked.
 *
 * A payload that names no source is not read at all (`read: false`): the
 * caller throws, and renderPanel paints the error state rather than a row of
 * dashes that would claim we looked and found nothing.
 *
 * EXPOSURE is deliberately not a cell. There is no field — the bot's
 * `portfolio_exposure_pct` is declared "Reserved — not currently populated" —
 * and no honest derivation: `size_usd` is notional on the portfolio rows and
 * margin-or-notional on the positions rows, off by the leverage multiple.
 *
 * It does NOT read the mode. `readMode(pf)` in dashboard.js is the client's
 * one reading; its answer arrives as a parameter and is used only for the
 * line that says which BOOK the figures describe.
 *
 * Written clean of the honesty ratchet's four shapes: no `|| 0`, `?? 0`,
 * `Number(x) || 0`, and no `x >= 0 ? a : b` — a verdict colour is decided by
 * STATE here and painted by pnlClass in the renderer.
 */
(function (root, factory) {
  const api = factory();
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.MetricClusterModel = api;
}(typeof self !== 'undefined' ? self : this, function () {
  'use strict';

  const DASH = '—';

  const W = {
    unread:    { key: 'dd.m_unread',    en: 'not read' },
    never:     { key: 'dd.m_never',     en: 'nothing stored for this account yet' },
    eqLive:    { key: 'dd.m_eq_live',   en: 'exchange balance unavailable' },
    dayBasis:  { key: 'dd.m_day_basis', en: 'realised today + all open' },
    // "just now" is NOT in this sentence: the age beside it is the bot's own
    // stamp (`updated_at`, minted when the payload was built), and a line
    // that said "just now" over a three-minute-old stamp would be two answers.
    srcBot:    { key: 'dd.m_src_bot',   en: 'read from the bot' },
    srcScan:   { key: 'dd.m_src_scan',  en: 'from the bot’s latest scan' },
    srcSync:   { key: 'dd.m_src_sync',  en: 'as last synced by the bot' },
    srcStored: { key: 'dd.m_src_stored', en: 'the last value this site stored' },
    band:      { key: 'dd.m_band',      en: 'Some figures below are the last values this site stored, not a reading of now — each one says which.' },
    bookNobot: { key: 'dd.m_book_nobot', en: 'simulated — no trading bot is configured on this deployment' },
    bookMixed: { key: 'dd.m_book_mixed', en: 'simulated paper book — not the live account the mode chip names' },
    bookSync:  { key: 'dd.m_book_sync',  en: 'the operator account’s synced book' },
    bookLive:  { key: 'dd.m_book_live',  en: 'live exchange account' },
    bookPaper: { key: 'dd.m_book_paper', en: 'paper book' },
    bookUnknown: { key: 'dd.m_book_unknown', en: 'book unknown — the mode could not be read' },
  };

  /** Every key this model can emit, for the translation guard. */
  const KEYS = Object.keys(W).map(function (k) { return W[k].key; });

  /** Finite number or null. Mirrors engine-card-model.js's num(): a coercion
   *  primitive, never a default. */
  function num(v) {
    if (v === null || v === undefined || typeof v === 'boolean') return null;
    const f = typeof v === 'number' ? v : parseFloat(v);
    return Number.isFinite(f) ? f : null;
  }

  function isoOrNull(v) {
    return (typeof v === 'string' && v.length && Number.isFinite(new Date(v).getTime())) ? v : null;
  }

  /** source word -> cell state. Unknown words are unread: a fifth spelling
   *  added later cannot arrive as a reading. */
  function stateOf(source) {
    if (source === 'gateway' || source === 'scan_cache') return 'read';
    if (source === 'sync_rows') return 'synced';
    if (source === 'snapshot' || source === 'db_rows') return 'memory';
    if (source === 'absent') return 'absent';
    return 'unread';
  }

  function srcWords(source) {
    if (source === 'gateway') return W.srcBot;
    if (source === 'scan_cache') return W.srcScan;
    if (source === 'sync_rows') return W.srcSync;
    if (source === 'snapshot' || source === 'db_rows') return W.srcStored;
    return null;
  }

  function cell(id, lead, fmt, state, value, src, why, asOf, verdict) {
    return { id: id, lead: lead, fmt: fmt, state: state, value: value,
             text: value === null ? DASH : null, src: src, why: why,
             as_of: asOf, verdict: verdict };
  }

  /**
   * @param {object|null} pf    the /api/portfolio payload
   * @param {string|null} mode  readMode(pf)'s answer: 'LIVE' | 'PAPER' | null
   * @returns {{read:boolean, band:object|null, book:object|null, cells:Array}}
   */
  function clusterCells(pf, mode) {
    if (!pf || typeof pf !== 'object') return { read: false, band: null, book: null, cells: [] };
    const prov = (pf.provenance && typeof pf.provenance === 'object') ? pf.provenance : null;
    if (!prov) return { read: false, band: null, book: null, cells: [] };
    const asOf = (pf.as_of && typeof pf.as_of === 'object') ? pf.as_of : {};
    const unconfigured = pf.unconfigured === true;
    const liveUnavailable = pf.live_unavailable === true;
    const cells = [];

    // ── Equity ──────────────────────────────────────────────────────────
    const eqState = stateOf(prov.equity);
    const eq = num(pf.equity);
    if (eqState === 'unread' || eq === null) {
      // Three reasons, each produced by a branch of the route: never stored,
      // the venue's balance unavailable, or a source that claims a reading
      // and carried no number. "No trading bot on this deployment" is NOT one
      // of them — on an unconfigured site the equity is a stored memory or
      // never stored, and the BOOK line below says there is no bot.
      let why = W.unread;
      if (prov.equity === 'never_stored') why = W.never;
      else if (liveUnavailable) why = W.eqLive;
      cells.push(cell('equity', true, 'money', 'unread', null, null, why, null, false));
    } else {
      // A balance is not a P&L: no verdict colour in any state, and a
      // measured $0.00 renders — an empty account is a measurement.
      cells.push(cell('equity', true, 'money', eqState, eq, srcWords(prov.equity), null,
                      isoOrNull(asOf.equity), false));
    }

    // ── Day P&L ─────────────────────────────────────────────────────────
    // Only the gateway tracks it. The operator sync path and a stored memory
    // do not carry it at all, so there is no cell — a dash would claim we
    // looked for something this payload never holds. Where it IS carried, a
    // null is a failed read of a figure the tracker always states.
    const dayState = stateOf(prov.daily_pnl);
    if (dayState !== 'absent') {
      const daily = num(pf.daily_pnl);
      if (dayState === 'unread' || daily === null) {
        cells.push(cell('daypnl', false, 'signed', 'unread', null, null, W.unread, null, false));
      } else {
        // The one figure that earns a colour, and only when it was READ:
        // a memory's colour would be a verdict about the past painted as now.
        cells.push(cell('daypnl', false, 'signed', dayState, daily, srcWords(prov.daily_pnl),
                        W.dayBasis, isoOrNull(asOf.daily_pnl), dayState === 'read'));
      }
    }

    // ── Open ────────────────────────────────────────────────────────────
    // An array that came back empty is a real, measured, empty book — from
    // the bot or from the sync. `[]` from a table nobody ever wrote to is
    // not, and the route says which (never_stored).
    const openState = stateOf(prov.open_positions);
    const rows = Array.isArray(pf.open_positions) ? pf.open_positions : null;
    if (openState === 'unread' || rows === null) {
      const why = prov.open_positions === 'never_stored' ? W.never : W.unread;
      cells.push(cell('open', false, 'count', 'unread', null, null, why, null, false));
    } else if (openState !== 'absent') {
      cells.push(cell('open', false, 'count', openState, rows.length, srcWords(prov.open_positions),
                      null, isoOrNull(asOf.open_positions), false));
    }

    // The band fires only when a MEMORY is on screen. A synced figure is the
    // bot's own last push and says so on its own line; an unread one says why.
    const band = cells.some(function (c) { return c.state === 'memory'; }) ? W.band : null;

    // Which BOOK the figures describe. MIXED is the caller's paper tracker on
    // a live deployment, and readMode maps it to LIVE for the chip — so this
    // line is the only place the reader is told the red chip is not about
    // these numbers.
    let book;
    if (unconfigured) book = W.bookNobot;
    else if (pf.mode === 'MIXED') book = W.bookMixed;
    else if (pf.source === 'sync') book = W.bookSync;
    else if (mode === 'LIVE') book = W.bookLive;
    else if (mode === 'PAPER') book = W.bookPaper;
    else book = W.bookUnknown;

    // READ means the payload named its sources. Every cell unread WITH its
    // reason ("nothing stored for this account yet", three times) is still a
    // reading — of an account this site never stored — and the reasons are
    // the answer; only a payload that names no source at all (`read: false`
    // above) leaves the renderer with nothing honest to print.
    return { read: true, band: band, book: book, cells: cells };
  }

  return { clusterCells: clusterCells, KEYS: KEYS, DASH: DASH };
}));
