/*
 * RCEmbedRead — reading the two payloads the embed board draws from.
 *
 * This module exists because of a bug that shipped, passed every gate, and
 * rendered the wrong thing on every single load.
 *
 *     var sigs = (j && j.data && j.data.signals) || [];
 *
 * `/api/signals` answers `{signals: [...]}` at the top level. There is no
 * `data` envelope — that shape belongs to `fetchJSON()` in app.js, which
 * returns `{ok, status, data}` and is what the DASHBOARD uses. The embed page
 * calls `fetch()` directly and the reader was copied across anyway, so
 * `j.data.signals` was `undefined` on every response the server has ever sent.
 *
 * `|| []` is what turned that into a lie. An empty list is not a neutral
 * fallback here: the board renders it as **"No open signals right now."** —
 * a confident, specific claim that the engine has found nothing, on the public
 * page whose entire job is to show that the engine is working. The candle
 * reader had the identical defect one level down (`j.data.data` against a
 * Bitget relay whose body IS `{code, msg, data:[...]}`), so every chart would
 * have said "no candles" too.
 *
 * So the readers are THREE-VALUED, and that is the whole point of extracting
 * them:
 *
 *   rows present, non-empty  ->  the list
 *   rows present, empty      ->  []      a real measurement: nothing is open
 *   key absent / not a list  ->  THROW   unreadable; the caller paints an error
 *
 * The throw is the `guard` strategy from CLAUDE.md's table. The caller's
 * existing `.catch` already paints "Signals could not be loaded. This is a
 * fault on our side, not a statement that there are none." — which is the
 * sentence that should have been on screen all along.
 *
 * WHY A SEPARATE FILE. No source scan can tell a reader that is present from
 * one that is correct; only running it against what the server actually sends
 * can. `embed_payload_contract.test.js` boots the real routers over real HTTP
 * and feeds their real bodies through these functions. That test is only
 * possible because the reading is reachable from outside the IIFE.
 *
 * Exposed as window.RCEmbedRead; module.exports in node so it can be tested.
 */
(function (root, factory) {
  if (typeof module === 'object' && module.exports) module.exports = factory();
  else root.RCEmbedRead = factory();
}(typeof self !== 'undefined' ? self : this, function () {
  'use strict';

  /** Thrown when a 200 carried a body this code cannot read. */
  function UnreadablePayload(what) {
    var e = new Error('unreadable ' + what + ' payload');
    e.name = 'UnreadablePayload';
    e.unreadable = what;
    return e;
  }

  /**
   * Signals from `/api/signals`.
   *
   * Shape: `{signals: [...]}`. Anything else is unreadable — including a body
   * that merely LOOKS plausible, like `{data:{signals:[]}}`, which is exactly
   * what the old reader expected and would have accepted as "none open".
   */
  function readSignals(body) {
    if (!body || typeof body !== 'object') throw UnreadablePayload('signals');
    if (!Array.isArray(body.signals)) throw UnreadablePayload('signals');
    return body.signals;
  }

  /**
   * Candles from `/api/market/candles/:symbol`.
   *
   * The route relays Bitget verbatim, so the body is `{code, msg, data:[...]}`
   * and the rows are at `body.data` — one level up from where the old reader
   * looked. An array directly is accepted too: it is unambiguous, and a reader
   * that refuses a shape it plainly understands buys nothing.
   */
  function readCandles(body) {
    if (Array.isArray(body)) return body;
    if (!body || typeof body !== 'object') throw UnreadablePayload('candles');
    if (!Array.isArray(body.data)) throw UnreadablePayload('candles');
    return body.data;
  }

  return { readSignals: readSignals, readCandles: readCandles };
}));
