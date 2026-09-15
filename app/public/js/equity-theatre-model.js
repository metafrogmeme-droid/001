/**
 * THE EQUITY & DRAWDOWN THEATRE — one read, two pictures, and every
 * "drawdown" on this site saying whose book it is.
 *
 * The CROSSFIRE deck pairs its equity curve with the cage that stops it —
 * "CAGE · NORMAL · 3 SLOTS · HALT 5%" printed under the curve, with the
 * account's own MAX SLOTS and HALT beside it. The pairing is the point: a
 * curve without the limit is a picture, and a limit without the curve is a
 * number. Measuring for that pairing found why RUNECLAW could not draw it.
 *
 * EIGHT QUANTITIES ON THIS SITE ARE CALLED "DRAWDOWN". Six are labelled
 * "Max drawdown" or "Max DD" in the same words, from six endpoints — and
 * they describe somebody else's record (three of them), the caller's own
 * account on two different bases, two simulations that traded nothing, and
 * the live gate:
 *
 *   /api/public/track-record   the AGENT's published record, percent
 *   /api/portfolio/intel       the CALLER's realized closes, DOLLARS
 *   /api/replay?stake=         a what-if on the agent's signals at your stake
 *   /api/lab/run               a BACKTEST of rules you configured, frozen data
 *   /api/reputation            the agent's reputation metrics
 *   /api/copy                  a COPY LEADER's record
 *   /api/trades/equity-curve   the CALLER's equity snapshots, % below peak
 *   circuit_breaker.backstop   the ENGINE's live gate — what halts trading
 *
 * Nothing on any of them said whose book or which basis. The eighth was
 * found by the guard written for the other seven: a measurement you
 * remember is not a measurement, and the backtest Lab's tile was the one
 * rendering no grep for the five had reached. CLAUDE.md already
 * records the two-quantity version — "a different quantity under the same
 * name, which is exactly what an implementer greps to and wires under 'halt
 * threshold' with every test green" — and the fix then reached the backstop
 * PAYLOAD and none of the labels. `DD_KINDS` is that vocabulary: one row per
 * quantity, naming the BOOK and the BASIS, and every renderer labels from it.
 *
 * ONE READ, TWO PICTURES. The equity curve and the underwater chart are two
 * views of one series, and the underwater half used to be mounted as a SIDE
 * EFFECT of the curve panel's loader, inside a catch that swallowed
 * everything. `#p-underwater` had three touchers in the tree — the markup and
 * two reads inside that function — so the drawdown chart had no failure state
 * of its own and no way to say the read failed. One reading answers both, and
 * a state that is not `read` is a state both halves show.
 *
 * THE SAMPLE IS STATED, the way the chart read's footnote states bars. The
 * route drops snapshots it cannot parse and returns only the CURRENT capital
 * segment, so "deepest drawdown 4.2%" over 6 snapshots and over 300 are
 * different claims and used to render identically.
 */
(function (root, factory) {
  const api = factory();
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.EquityTheatreModel = api;
}(typeof self !== 'undefined' ? self : this, function () {
  'use strict';

  /**
   * THE VOCABULARY. `short` is the qualifier printed BESIDE the figure — it
   * has to be visible text and not a title attribute, because a title does
   * not render on touch and this repo already records that lesson. `long` is
   * the caption sentence for a panel that has room for one.
   */
  const DD_KINDS = {
    agentRecord: {
      short: { key: 'dd.et_k_agent_s', en: 'the agent’s record' },
      long: { key: 'dd.et_k_agent_l', en: 'Deepest fall from peak on the agent’s published track record — not your account.' },
    },
    yourClosed: {
      short: { key: 'dd.et_k_closed_s', en: 'your closed trades' },
      long: { key: 'dd.et_k_closed_l', en: 'Deepest fall from peak on the running total of your CLOSED trades, in dollars — it does not include open positions.' },
    },
    replayWhatIf: {
      short: { key: 'dd.et_k_replay_s', en: 'a what-if at your stake' },
      long: { key: 'dd.et_k_replay_l', en: 'Deepest fall in a replay of the agent’s signals at the stake you chose — nothing here was traded.' },
    },
    backtestLab: {
      short: { key: 'dd.et_k_lab_s', en: 'this backtest' },
      long: { key: 'dd.et_k_lab_l', en: 'Deepest fall in a backtest of the rules you configured, over frozen historical data — nothing here was traded.' },
    },
    agentReputation: {
      short: { key: 'dd.et_k_rep_s', en: 'the agent’s record' },
      long: { key: 'dd.et_k_rep_l', en: 'Deepest fall from peak on the agent’s reputation record — not your account.' },
    },
    copyLeader: {
      short: { key: 'dd.et_k_copy_s', en: 'this leader’s record' },
      long: { key: 'dd.et_k_copy_l', en: 'Deepest fall from peak on this leader’s record — not your account.' },
    },
    yourEquity: {
      short: { key: 'dd.et_k_equity_s', en: 'your equity snapshots' },
      long: { key: 'dd.et_k_equity_l', en: 'Deepest fall below the running peak of your recorded equity — a history, not the limit the engine enforces.' },
    },
    engineGate: {
      short: { key: 'dd.et_k_gate_s', en: 'the engine’s live gate' },
      long: { key: 'dd.et_k_gate_l', en: 'What the breaker measures and halts on right now — a different quantity from any record above.' },
    },
  };

  const W = {
    unread: { key: 'dd.et_unread', en: 'Your equity history could not be read.' },
    none: { key: 'dd.et_none', en: 'No equity snapshots recorded yet.' },
    thin: { key: 'dd.et_thin', en: 'One snapshot on record — a curve needs at least two.' },
    snaps: { key: 'dd.et_snaps', en: '{n} snapshots' },
    segments: { key: 'dd.et_segments', en: 'capital basis changed {n} time(s) — this shows the current period only' },
    deepest: { key: 'dd.et_deepest', en: 'deepest {n}% below peak' },
    flat: { key: 'dd.et_flat', en: 'no fall below peak on record' },
    // The pairing the video's cage row makes, and the sentence that keeps the
    // two apart. Without it a reader takes the history for the limit.
    notTheGate: { key: 'dd.et_not_gate', en: 'This is your recorded history. The limit the engine halts on is a separate reading.' },
  };

  const KEYS = Object.keys(W).map(function (k) { return W[k].key; })
    .concat(Object.keys(DD_KINDS).reduce(function (acc, k) {
      return acc.concat([DD_KINDS[k].short.key, DD_KINDS[k].long.key]);
    }, []));

  /** A finite number, or null. A numeric STRING is junk, not a value. */
  function num(v) {
    if (typeof v === 'number') return isFinite(v) ? v : null;
    return null;
  }

  /** The label for a drawdown figure. An unknown kind RAISES rather than
   *  printing a bare number: a figure with no book named is the defect. */
  function kind(name) {
    const k = DD_KINDS[name];
    if (!k) throw new Error('equity theatre: no such drawdown kind: ' + String(name));
    return k;
  }

  /**
   * THE UNDERWATER SERIES — percent below the running peak, and the deepest
   * point, over equities already parsed.
   *
   * `deepest` is `null` when nothing could be measured, and `0` only when a
   * real series never fell below its peak: a flat record is a measurement and
   * an unreadable one is not, which is the distinction the old caption could
   * not make (it printed "No meaningful drawdown yet" for both).
   */
  function underwater(equities) {
    const pts = (equities || []).filter(usable).map(parseNum);
    if (pts.length < 2) return { points: pts, series: [], deepest: null };
    let peak = -Infinity;
    let worst = 0;
    const series = [];
    for (let i = 0; i < pts.length; i++) {
      if (pts[i] > peak) peak = pts[i];
      const pct = ((pts[i] - peak) / peak) * 100;
      series.push(pct);
      if (pct < worst) worst = pct;
    }
    return { points: pts, series: series, deepest: worst };
  }

  /**
   * THE SAMPLE — what the route answered, what parsed, and how many capital
   * segments it dropped. `/api/trades/equity-curve` returns the CURRENT
   * segment only and counts the others, so a curve is never drawn across a
   * deposit; the count says so rather than leaving the reader to assume the
   * whole history is on screen.
   */
  function sample(answered, parsed, capitalEvents) {
    const a = num(answered);
    const p = num(parsed);
    if (a === null || p === null || a < 0 || p < 0) return null;
    const ce = num(capitalEvents);
    return {
      answered: a,
      parsed: p,
      dropped: Math.max(0, a - p),
      segmentsDropped: ce === null || ce < 0 ? 0 : Math.floor(ce),
    };
  }

  /**
   * THE WHOLE READING, for both charts.
   *
   * `payload === null` is the failed read; a payload with no `snapshots`
   * array is an older server or a junk body and is ALSO unread, never an
   * empty history — "nothing recorded yet" is a claim about the account.
   */
  function theatre(payload) {
    if (payload === null || payload === undefined || typeof payload !== 'object') {
      return { state: 'unread', word: W.unread, sample: null, curve: [], uw: null, kind: DD_KINDS.yourEquity };
    }
    const snaps = payload.snapshots;
    if (!Array.isArray(snaps)) {
      return { state: 'unread', word: W.unread, sample: null, curve: [], uw: null, kind: DD_KINDS.yourEquity };
    }
    const rows = snaps.filter(function (s) { return s && usable(s.equity); });
    const equities = rows.map(function (s) { return s.equity; });
    const s = sample(snaps.length, rows.length, payload.capital_events);
    if (!rows.length) {
      return { state: 'none', word: W.none, sample: s, curve: [], uw: null, kind: DD_KINDS.yourEquity };
    }
    if (rows.length < 2) {
      return { state: 'thin', word: W.thin, sample: s, curve: rows, uw: null, kind: DD_KINDS.yourEquity };
    }
    return {
      state: 'read',
      word: null,
      sample: s,
      curve: rows,
      uw: underwater(equities),
      kind: DD_KINDS.yourEquity,
    };
  }

  /**
   * ONE predicate for what counts as an equity reading, so the footnote's
   * count and the chart's points cannot disagree — `theatre` filters rows
   * with it and `underwater` maps with it.
   *
   * Positive, because the series divides by the RUNNING PEAK and a peak of
   * zero has no percent below it. `/api/trades/equity-curve` already drops
   * these at the route (`isFinite(p.equity) && p.equity > 0`); agreeing with
   * it is what keeps `sample.parsed` equal to `uw.points.length`.
   */
  function usable(v) {
    const n = parseNum(v);
    return n !== null && n > 0;
  }

  /** The route sends `equity` as a string on some drivers and a number on
   *  others; both are real. Anything else is not a reading. */
  function parseNum(v) {
    if (typeof v === 'number') return isFinite(v) ? v : null;
    if (typeof v === 'string' && v.trim() !== '') {
      const n = Number(v);
      return isFinite(n) ? n : null;
    }
    return null;
  }

  /**
   * THE FOOTNOTE PARTS — the sample, then the deepest point, then the
   * segments that were dropped. Each is printed only when it is a reading:
   * a "0 segments dropped" row trains the reader to stop reading the line.
   */
  function footnote(read) {
    if (!read || read.state !== 'read' || !read.sample) return [];
    const parts = [{ word: W.snaps, n: read.sample.parsed }];
    const d = read.uw ? read.uw.deepest : null;
    if (d === null) {
      // nothing to say about depth — the sample line already said the count
    } else if (d < 0) {
      parts.push({ word: W.deepest, n: Math.abs(d).toFixed(1) });
    } else {
      parts.push({ word: W.flat, n: null });
    }
    if (read.sample.segmentsDropped > 0) {
      parts.push({ word: W.segments, n: read.sample.segmentsDropped });
    }
    return parts;
  }

  return {
    theatre: theatre, underwater: underwater, sample: sample, footnote: footnote,
    kind: kind, num: num, parseNum: parseNum, usable: usable,
    DD_KINDS: DD_KINDS, W: W, KEYS: KEYS,
  };
}));
