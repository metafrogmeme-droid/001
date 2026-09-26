/* Trade confirm — what a confirm ANSWER may claim on this page.
 *
 * A 200 from `/api/trade/confirm` says the request was handled. It never said
 * a trade was placed, and both surfaces read it as though it did: the
 * dashboard's modal closed on a green "Trade confirmed." and the chat card
 * printed the answer as the execution, for every sentence the bot's confirm
 * can write -- the risk gate's refusal, the duplicate skip, "Paper trading is
 * disabled", the chosen-strategy refusal, the practice cooldown. The bot now
 * says which, as `placed` beside the answer, read by the same function its
 * Telegram Confirm button asks (`placed_nothing`), so this page decides
 * nothing from the WORDING and nothing from the status code.
 *
 * FOUR OUTCOMES, because four things can come back:
 *
 *   failed    the request did not go through (not ok) -- each surface keeps
 *             its own sentence for that, which is not this model's subject;
 *   refused   `placed === false`: the bot handled it and placed NOTHING;
 *   placed    `placed === true`: something was placed;
 *   unread    `placed` present and not a boolean: the page cannot tell, and
 *             neither green nor red may be painted over it.
 *
 * AN ABSENT `placed` IS AN OLDER BOT, and it keeps the behaviour it has always
 * had -- read as placed. That is a deliberate compatibility decision rather
 * than a reading: `app/` and `bot/` are different deploy targets, and a page
 * that refused every confirm until the bot box was redeployed would take the
 * one-tap paper flow away from everybody for a fix about refusals. It is
 * flagged `legacy` so a surface can tell the two apart if it ever needs to.
 */
(function (root) {
  'use strict';

  function outcome(r) {
    if (!r || !r.ok) return { kind: 'failed' };
    var d = (r.data && typeof r.data === 'object') ? r.data : {};
    var text = typeof d.result_html === 'string' ? d.result_html : '';
    if (d.placed === false) return { kind: 'refused', text: text };
    if (d.placed === true) return { kind: 'placed', text: text, legacy: false };
    if (!Object.prototype.hasOwnProperty.call(d, 'placed')) {
      return { kind: 'placed', text: text, legacy: true };
    }
    return { kind: 'unread', text: text };
  }

  var api = { outcome: outcome };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  root.TradeConfirmModel = api;
})(typeof self !== 'undefined' ? self : this);
