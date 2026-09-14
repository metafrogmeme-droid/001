/**
 * The status strip's reading: which account is trading, and WHY it says so.
 *
 * It does NOT decide the mode. `readMode(pf)` in dashboard.js is the client's
 * one reading and stays there — its own guard slices it out of dashboard.js by
 * string index, and a copy here would be a second answer that keeps that guard
 * green while the strip diverges. The verdict arrives as a PARAMETER; this
 * file reads the payload only for the sentence.
 *
 * FOUR outcomes, because three different things are being asked:
 *
 *   live / paper  -> a mode was read. Say where from.
 *   unreadable    -> no mode was read. Never PAPER, and say what the figures
 *                    below the strip therefore are.
 *   absent        -> there is no bot on this deployment, so PAPER is a fact
 *                    about the SITE, not a reading of an engine. Same word as
 *                    `paper`, a different sentence AND a different tone,
 *                    because "the bot is simulating" and "there is no bot" are
 *                    different facts and routes/portfolio.js's `unconfigured`
 *                    branch is the only place that knows. Amber there would
 *                    claim *simulating*; the muted swatch claims nothing.
 *
 * WHAT IT CANNOT SAY, stated here so nobody builds on the gap: IDLE — a real
 * account with live trading never armed. bot/core/live_readiness.py's
 * mode_label() answers LIVE/PAPER/IDLE/UNKNOWN and nothing carries the last
 * two across the wire. This model answers three of the four and abstains
 * rather than folding IDLE into PAPER.
 *
 * Written clean of the honesty ratchet's four shapes on purpose: no `|| 0`,
 * no `?? 0`, no `Number(x) || 0`, no `x >= 0 ? a : b` — there is no number
 * in here to coerce, and the strip must never grow one.
 */
(function (root, factory) {
  const api = factory();
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.ModeStripModel = api;
}(typeof self !== 'undefined' ? self : this, function () {
  'use strict';

  const SRC_BOT    = { key: 'dd.ms_src_bot',    en: 'read from the trading bot' };
  const SRC_SYNC   = { key: 'dd.ms_src_sync',   en: 'read from the operator sync feed' };
  const SRC_SITE   = { key: 'dd.ms_src_site',   en: 'not read — this site’s own records' };
  const SRC_DEPLOY = { key: 'dd.ms_src_deploy', en: 'this deployment’s own configuration' };

  const WHY_LIVE      = { key: 'dd.ms_why_live',      en: 'The trading bot reports it is trading this account live. Orders reach a real exchange.' };
  const WHY_LIVE_SYNC = { key: 'dd.ms_why_live_sync', en: 'The operator’s own engine reports live trading. Orders reach a real exchange.' };
  const WHY_LIVE_NB   = { key: 'dd.ms_why_live_nobal', en: 'The bot reports live trading, but its account balance could not be read.' };
  const WHY_MIXED     = { key: 'dd.ms_why_mixed',     en: 'The bot is trading live, but the book shown below is this account’s simulated one. The bot reports the two together.' };
  const WHY_PAPER     = { key: 'dd.ms_why_paper',     en: 'The bot reports it is simulating. Nothing below placed an order at an exchange.' };
  const WHY_NOBOT     = { key: 'dd.ms_why_unconfigured', en: 'No trading bot is connected to this site, so there is no live account to be in. This is what the deployment is, not a reading of an engine.' };
  const WHY_UNREACH   = { key: 'dd.ms_why_unreach',   en: 'This site could not reach the trading bot, so the mode was never read. It is not paper — it is unknown.' };
  const WHY_NOMODE    = { key: 'dd.ms_why_nomode',    en: 'The bot answered without a mode this dashboard recognises. It is not safe to call that paper.' };

  const BELOW_LIVE  = { key: 'dd.ms_below_live',  en: 'The figures below are this account’s live book.' };
  const BELOW_PAPER = { key: 'dd.ms_below_paper', en: 'The figures below are simulated.' };
  const BELOW_NOBAL = { key: 'dd.ms_below_nobal', en: 'The equity figure below is missing rather than zero — nobody could read the balance.' };
  const BELOW_SITE  = { key: 'dd.ms_below_site',  en: 'The figures below are the last values this site recorded. They were not read from the bot just now and may be old.' };

  const AGE_OLD = { key: 'dd.ms_age_old', en: 'FIGURES OLD' };

  // Every key this model can emit. The guard test resolves all of them in all
  // fourteen languages — dashboard.js calls T() with a VARIABLE key, so the
  // i18n sweep that greps for the literal `T('dd.…'` cannot see a single one.
  const KEYS = [
    SRC_BOT, SRC_SYNC, SRC_SITE, SRC_DEPLOY,
    WHY_LIVE, WHY_LIVE_SYNC, WHY_LIVE_NB, WHY_MIXED, WHY_PAPER,
    WHY_NOBOT, WHY_UNREACH, WHY_NOMODE,
    BELOW_LIVE, BELOW_PAPER, BELOW_NOBAL, BELOW_SITE, AGE_OLD,
  ].map((x) => x.key);

  function out(kind, word, cls, tone, src, why, below, age) {
    return { kind, word, cls, tone, src, why, below, age };
  }

  // `stale` means two different things on the wire and reading it without
  // `source` is the defect one panel over. On the operator's sync feed it
  // means the last equity snapshot is over thirty minutes old: the MODE is
  // still a reading, the FIGURES are the thing that aged. Everywhere else a
  // stale payload has already had its mode nulled by readMode and never
  // reaches the live/paper branches.
  function ageOf(pf) {
    if (pf && pf.source === 'sync' && pf.stale === true) return AGE_OLD;
    return null;
  }

  /**
   * @param {{mode: (string|null), pf: (object|null)}} input
   *        mode — readMode(pf)'s answer. Anything that is not exactly 'LIVE'
   *        or 'PAPER' is treated as unknown: fail-closed, so a future fifth
   *        wire value cannot arrive as a verdict.
   *        pf   — the /api/portfolio payload, read only for the sentence.
   * @returns {{kind:string, word:string, cls:string, tone:string,
   *            src:{key:string,en:string}, why:{key:string,en:string},
   *            below:{key:string,en:string}, age:({key:string,en:string}|null)}}
   */
  function modeStrip(input) {
    const inp = input && typeof input === 'object' ? input : {};
    const pf = inp.pf && typeof inp.pf === 'object' ? inp.pf : null;
    const mode = (inp.mode === 'LIVE' || inp.mode === 'PAPER') ? inp.mode : null;

    // ABSENT first. This payload also carries mode 'PAPER' with stale false,
    // so readMode answers 'PAPER' for it and the ordinary paper sentence
    // would report a configuration as a measurement.
    if (pf && pf.unconfigured === true) {
      return out('absent', 'PAPER', 'chip--offline', 'absent', SRC_DEPLOY, WHY_NOBOT, BELOW_PAPER, null);
    }

    if (mode === 'LIVE') {
      const fromSync = !!(pf && pf.source === 'sync');
      const src = fromSync ? SRC_SYNC : SRC_BOT;
      let why = fromSync ? WHY_LIVE_SYNC : WHY_LIVE;
      let below = BELOW_LIVE;
      if (pf && pf.live_unavailable === true) { why = WHY_LIVE_NB; below = BELOW_NOBAL; }
      // MIXED is a real third value on the wire that readMode flattens to the
      // more alarming of two. The verdict stays LIVE — that is readMode's
      // call, not this file's — but the flattening loses the fact that the
      // book below is the SIMULATED one, and the strip is where that belongs.
      else if (pf && pf.mode === 'MIXED') { why = WHY_MIXED; below = BELOW_PAPER; }
      return out('live', 'LIVE', 'chip--live', 'live', src, why, below, ageOf(pf));
    }

    if (mode === 'PAPER') {
      const src = (pf && pf.source === 'sync') ? SRC_SYNC : SRC_BOT;
      return out('paper', 'PAPER', 'chip--paper', 'paper', src, WHY_PAPER, BELOW_PAPER, ageOf(pf));
    }

    // UNREADABLE. Two ways not to know, and they are different events: the
    // site answered and the bot did not (mode null, or a memory marked stale),
    // or the bot answered with a word this dashboard does not recognise.
    if (!pf || pf.mode == null || pf.stale === true) {
      return out('unreadable', 'MODE ?', 'chip--offline', 'unknown', SRC_SITE, WHY_UNREACH, BELOW_SITE, null);
    }
    return out('unreadable', 'MODE ?', 'chip--offline', 'unknown', SRC_SITE, WHY_NOMODE, BELOW_SITE, null);
  }

  return { modeStrip, KEYS };
}));
