/**
 * THE ENTRY CARDS — what an EMPTY setups panel may claim, and why it is empty.
 *
 * The panel's empty state read:
 *
 *     "No qualifying setups in the last scan — the gate is doing its job."
 *
 * That sentence was false three ways, and the third is the expensive one.
 *
 * ONE: IT FIRED WHEN NO SCAN HAD RUN. The bot's autonomous cycle pushes a
 * SUMMARY every cycle — `_push_scan_summary_to_website` calls
 * `_build_scan_payload([], engine)` for the circuit-breaker block and the
 * regime, and its own docstring says the rest "would stay placeholder". Driven,
 * that payload carried `entry_cards: []` and `symbols: {}`, and the ingest
 * replaces the stored scan WHOLESALE (only the deep-scan block was carried
 * forward), so the last manual `/scan`'s cards were wiped within a cycle and
 * the panel reported the absence as a risk control working.
 *
 * TWO: THE GATE WAS NEVER CONSULTED. `entry_cards` are filtered by
 * `r["score"] >= SETUP_SCORE_FLOOR` — the SCANNER's own score — and the risk
 * gate runs at confirm time, which the panel's own footer already says
 * ("Confirmations run through its risk gate"). Attributing the absence to the
 * gate names a control that never ran.
 *
 * THREE: A FAILED READ RENDERED AS THE GATE WORKING. The card loop skips a
 * candidate whose ATR it could not read, and before `record_atr` kept
 * significant digits every sub-cent asset recorded `0.0` — so a universe of
 * cheap assets produced zero cards and the panel called that discipline. It
 * also skips a candidate whose direction it could not read, which is the same
 * shape one field over.
 *
 * So the producer now says WHY (`entry_cards_read`: what was scanned, what
 * cleared the floor, what was shown, and the two read failures), the ingest
 * carries the blocks forward with the age of the scan that produced them, and
 * this model turns that into one of six facts. None of them is a claim about
 * the gate.
 *
 * What it does NOT do: derive a sentence the producer already states, or pick a
 * colour. An absence is muted; a read failure is a warning; neither is green.
 */
(function (root, factory) {
  const api = factory();
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.EntryCardsModel = api;
}(typeof self !== 'undefined' ? self : this, function () {
  'use strict';

  /** How old a scan's cards may be before the panel says they are old.
   *  ONE bound, and it is this page's own: the "Last scan" row already calls a
   *  scan stale past three hours, and a second threshold would be a second
   *  answer about when a setup is out of date. */
  const STALE_MS = 3 * 3600 * 1000;

  const W = {
    // — why there is nothing to show —
    noScan:     { key: 'dd.ec_no_scan',     en: 'No scan on record yet — the engine pushes its cycle summary without one.' },
    nothingRead:{ key: 'dd.ec_nothing_read',en: 'The last scan read no symbols, so it found no candidates.' },
    belowFloor: { key: 'dd.ec_below_floor', en: 'The last scan read {results} symbol(s) and none scored above {floor} — that is the scanner’s own floor, not the risk gate.' },
    unpriced:   { key: 'dd.ec_unpriced',    en: '{above} candidate(s) cleared the scanner’s floor and none could be priced: {noAtr} with no readable volatility, {noDir} with no readable direction. That is a failed read, not a gate.' },
    unknown:    { key: 'dd.ec_unknown',     en: 'Why there are no setups is not on record — this bot build does not report it.' },
    // — what the cards are —
    fromScan:   { key: 'dd.ec_from_scan',   en: 'From the scan at {at}.' },
    old:        { key: 'dd.ec_old',         en: 'From the scan at {at}, {ago} ago — prices have moved since.' },
    undated:    { key: 'dd.ec_undated',     en: 'The scan these came from is not dated.' },
    partial:    { key: 'dd.ec_partial',     en: 'Showing {cards} of {above} candidate(s) above the floor.' },
    dropped:    { key: 'dd.ec_dropped',     en: '{n} could not be priced and is not shown.' },
    footer:     { key: 'dd.ec_footer',      en: 'The engine’s own candidates — not personal advice. Confirmations run through its risk gate.' },
  };

  const KEYS = Object.keys(W).map(function (k) { return W[k].key; });

  /** A finite number, or null. A numeric STRING is junk, not a value. */
  function num(v) {
    return (typeof v === 'number' && isFinite(v)) ? v : null;
  }

  /** The reading block, or null when the payload carries none (an older bot). */
  function reading(scan) {
    const r = scan && scan.entry_cards_read;
    return (r && typeof r === 'object' && !Array.isArray(r)) ? r : null;
  }

  /** The cards, or null when the payload carries no list at all. */
  function cardList(scan) {
    const c = scan && scan.entry_cards;
    return Array.isArray(c) ? c : null;
  }

  /**
   * SIX FACTS, and not one of them names the gate.
   *
   * `cards`          setups to show.
   * `no_scan`        the reading says this push ran none and none is stored.
   * `nothing_read`   a scan ran and read no symbols at all.
   * `below_floor`    it read symbols and none cleared the scanner's floor.
   * `unpriced`       candidates cleared the floor and none could be priced.
   * `unknown`        no reading on the payload: an older bot build.
   *
   * The order matters: a payload can carry cards AND a reading whose counts
   * describe the scan they came from, so the cards win. A reading with no cards
   * is the empty case, and which empty case it is comes from the counts.
   */
  function state(scan) {
    const cards = cardList(scan);
    if (cards && cards.length) return 'cards';
    const r = reading(scan);
    // Neither block on record: this build pushes both together, so a payload
    // with neither has only ever carried cycle summaries.
    if (!r && !cards) return 'no_scan';
    // A list with no reading is an OLDER BOT, which always sent `entry_cards`
    // and never sent why. Its empty list cannot be told from a scan that ran
    // and found nothing, so the panel says exactly that rather than guessing.
    if (!r) return 'unknown';
    const results = num(r.results);
    const above = num(r.above_floor);
    if (results === null || above === null) return 'unknown';
    if (results === 0) return 'nothing_read';
    if (above === 0) return 'below_floor';
    return 'unpriced';
  }

  /** The words for the empty state, as {key, en, params} or null for `cards`. */
  function why(scan) {
    const st = state(scan);
    if (st === 'cards') return null;
    const r = reading(scan) || {};
    if (st === 'no_scan') return { w: W.noScan, params: {} };
    if (st === 'nothing_read') return { w: W.nothingRead, params: {} };
    if (st === 'below_floor') {
      return { w: W.belowFloor, params: { results: r.results, floor: r.floor } };
    }
    if (st === 'unpriced') {
      const noAtr = num(r.no_atr), noDir = num(r.no_direction);
      return { w: W.unpriced, params: {
        above: r.above_floor,
        noAtr: noAtr === null ? '—' : noAtr,
        noDir: noDir === null ? '—' : noDir,
      } };
    }
    return { w: W.unknown, params: {} };
  }

  /** How old the SCAN those cards came from is, in ms, or null. */
  function ageMs(scan, now) {
    const at = scan && scan.scan_at;
    if (typeof at !== 'string' || !at) return null;
    const t = Date.parse(at);
    if (!isFinite(t)) return null;
    const ms = (typeof now === 'number' ? now : Date.now()) - t;
    // A stamp in the FUTURE is a clock disagreement, not an age.
    return ms >= 0 ? ms : null;
  }

  /** The note under the cards: their age, and what the list leaves out. */
  function note(scan, now) {
    if (state(scan) !== 'cards') return [];
    const out = [];
    const ms = ageMs(scan, now);
    const at = scan && scan.scan_at;
    if (ms === null) {
      out.push({ w: W.undated, params: {}, stale: false });
    } else if (ms >= STALE_MS) {
      out.push({ w: W.old, params: { at: at, ago: ms }, stale: true });
    } else {
      out.push({ w: W.fromScan, params: { at: at }, stale: false });
    }
    const r = reading(scan) || {};
    const cards = (cardList(scan) || []).length;
    const above = num(r.above_floor);
    // A bounded list printed with no total reads as the total. It is stated
    // only when it BITES: a permanent "showing 3 of 3" is the row that trains
    // a reader to stop reading the line.
    if (above !== null && above > cards) {
      out.push({ w: W.partial, params: { cards: cards, above: above }, stale: false });
    }
    const dropped = (num(r.no_atr) || 0) + (num(r.no_direction) || 0);
    if (dropped > 0) {
      out.push({ w: W.dropped, params: { n: dropped }, stale: false });
    }
    return out;
  }

  return {
    state: state, why: why, note: note, ageMs: ageMs,
    reading: reading, cardList: cardList,
    W: W, KEYS: KEYS, STALE_MS: STALE_MS,
  };
}));
