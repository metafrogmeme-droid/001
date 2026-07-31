/**
 * The engine-status chip's verdict, as a PURE function.
 *
 * The most prominent claim on the dashboard, and until now it was computed
 * inline inside dashboard.js — six thousand lines of browser script with no
 * seam at which to assert "this state renders that verdict". The same reason
 * the Telegram adoption card shipped a detail that never rendered once: a
 * surface with no seam has no assertion.
 *
 * FOUR states, not two. During this week's database outages the site stayed
 * up and every tab announced ENGINE OFFLINE, because /api/bot/sync/scan was
 * refused with a 503 by the not-ready gate. The engine was running the whole
 * time, and an operator then has to rule out a trading outage that never
 * happened.
 *
 *   read failed        -> we cannot SEE the engine. Say exactly that.
 *   never read yet     -> connecting.
 *   read ok, no scan   -> nothing recorded. Not the same as down.
 *   read ok, has scan  -> judge by age; age is real evidence.
 *
 * `scanOk` is deliberately tri-state: null = not attempted, false = the read
 * failed, true = the read succeeded. Collapsing it to a boolean is what made
 * "cannot see" and "is down" the same sentence.
 */
(function (root, factory) {
  const api = factory();
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.EngineStatusModel = api;
}(typeof self !== 'undefined' ? self : this, function () {
  'use strict';

  const LIVE_MAX_S = 900;    // 15m
  const STALE_MAX_S = 1800;  // 30m

  /**
   * @param {string|null} syncTime  received_at/timestamp of the last scan
   * @param {boolean|null} scanOk   tri-state read outcome (see above)
   * @param {number} nowMs          injected, so the verdict is testable
   * @returns {{text:string, cls:string, title:string}}
   */
  function engineChipState(syncTime, scanOk, nowMs) {
    if (!syncTime) {
      if (scanOk === false) {
        return {
          text: '● ENGINE STATUS UNKNOWN',
          cls: 'chip--warn',
          title: 'The site could not read its own engine feed, so it cannot '
            + 'tell you whether the engine is running. This is a website '
            + 'problem, not evidence of a trading outage.',
        };
      }
      if (scanOk === null) {
        return { text: '● CONNECTING', cls: 'chip--offline',
                 title: 'Reading the engine feed…' };
      }
      return { text: '● NO ENGINE DATA', cls: 'chip--offline',
               title: 'The feed was read successfully and holds no scan yet.' };
    }
    const ageSec = (nowMs - new Date(syncTime).getTime()) / 1000;
    // A payload exists, so age is real evidence and the verdict stands on it.
    // A failed REFRESH is NOTED rather than allowed to overwrite what we know.
    const stale = scanOk === false
      ? ' The latest refresh failed, so this may be older than it looks.' : '';
    const ago = `Last engine scan ${Math.round(ageSec / 60)}m ago.`;
    if (ageSec < LIVE_MAX_S) {
      return { text: '● ENGINE LIVE', cls: 'chip--up', title: ago + stale };
    }
    if (ageSec < STALE_MAX_S) {
      return { text: '● ENGINE STALE', cls: 'chip--warn', title: ago + stale };
    }
    return { text: '● ENGINE OFFLINE', cls: 'chip--offline', title: ago + stale };
  }

  return { engineChipState, LIVE_MAX_S, STALE_MAX_S };
}));
