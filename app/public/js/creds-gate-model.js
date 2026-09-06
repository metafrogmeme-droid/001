/**
 * Can this deployment protect an exchange key right now — as a PURE function.
 *
 * WHY IT IS NOT INLINE IN THE PANEL. `POST /api/credentials` refuses when
 * nothing can protect a submission, and for months the form above it looked
 * perfectly live: a user pasted real API keys, got a 503, and had no way to
 * read that as anything but their own mistake. The fix is a notice and a
 * hidden form — which is a CLAIM about a money surface, and a claim inline in
 * six thousand lines of browser script is one nothing can plant state against.
 *
 * THREE STATES, and collapsing the last two is the whole bug:
 *
 *   ready        a submission will be protected — sealed to the bot's own
 *                published key, or (legacy) encrypted under the shared key
 *   off          it will not, and we know why
 *   unknown      we could not read the key store, so we do not know
 *
 * `crypto_ready: false` is a claim — "this form is off" — and a failed read
 * has not earned it. `routes/credentials.js` sends null there instead, and
 * this maps it to `unknown`, which hides the form for the same reason `off`
 * does (never invite secrets into a form that may not work) while saying
 * something different, because the remedies differ: `awaiting_bot_key` clears
 * itself within a minute and a database that will not answer does not.
 *
 * An OLDER server sends neither field. That is not "off" — it is a deployment
 * whose form worked fine before this existed — so an absent `crypto_ready`
 * reads as ready, the same direction the panel behaved in yesterday.
 */
(function (root, factory) {
  const api = factory();
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.CredsGateModel = api;
}(typeof self !== 'undefined' ? self : this, function () {
  'use strict';

  /**
   * @param {object} status  /api/credentials/status payload
   * @returns {{state: 'ready'|'off'|'unknown', showForms: boolean,
   *            reason: string|null, detail: string|null}}
   */
  function gateState(status) {
    const s = status || {};
    // `undefined` means the field is absent (an older server); `null` means
    // the server explicitly could not tell. `== null` would merge them, and
    // they are the two cases this function exists to keep apart.
    if (!('crypto_ready' in s) || s.crypto_ready === undefined) {
      return { state: 'ready', showForms: true, reason: null, detail: null };
    }
    if (s.crypto_ready === true) {
      return { state: 'ready', showForms: true, reason: null, detail: null };
    }
    const reason = s.crypto_reason || null;
    const detail = s.crypto_detail || null;
    if (s.crypto_ready === null) {
      return { state: 'unknown', showForms: false, reason, detail };
    }
    return { state: 'off', showForms: false, reason, detail };
  }

  return { gateState };
}));
