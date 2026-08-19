/**
 * RUNECLAW — what a panel says when the read did not succeed.
 *
 * Every dashboard panel that fails has said the same eight words —
 * "Couldn't load this panel." — under a Retry button, for every cause. On
 * 2026-08-18 that swallowed a diagnosis the bot had already made and sent:
 *
 *     HTTP 403 {"error":"not_allowlisted",
 *               "detail":"Not approved for this bot yet. Send /start to it in
 *                         Telegram — the operator gets a request..."}
 *
 * `routes/news.js` relayed it intact, `fetchJSON` parsed it, and `mustRead`
 * threw it away. The News radar told its owner it was broken while holding the
 * sentence that said what to do instead. Three more causes — the site not
 * configured for the bot at all (503), the gateway unreachable (502), and a
 * genuine crash — printed those same eight words, so nobody looking at the
 * screen could tell which of the four they had, and the Retry button could only
 * ever help with one of them.
 *
 * `renderPanel` ALREADY KNEW THIS. It special-cases 401 with its own copy and a
 * Sign in button, over a comment reading "Retry would loop forever against a
 * 401". The reasoning was right and was never extended to the other statuses
 * that are equally final. This module is that extension.
 *
 * A FIXED VOCABULARY, NOT A PASSTHROUGH. The upstream `detail` strings are
 * written for humans and would read well, and relaying them is still the wrong
 * shape: they are server text, untranslatable into the fourteen languages this
 * UI ships, and an error path is exactly where internal config leaks into a
 * user's screen. `/readyz` answers with a coarse code from a closed set for
 * this precise reason, and so does this — the CODE crosses the wire, the words
 * are chosen here.
 *
 * ANYTHING UNRECOGNISED FALLS BACK to the generic message and the Retry button.
 * A mapper that guesses at codes it does not know would invent a diagnosis,
 * which is the defect one level up from the one being fixed.
 *
 * Dual export: browser (window.PanelErrorModel) + Node (require) for tests.
 */
(function (root, factory) {
  const api = factory();
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.PanelErrorModel = api;
}(typeof self !== 'undefined' ? self : this, function () {
  'use strict';

  // action: what the panel offers underneath the sentence.
  //   'retry'  — the read could plausibly succeed next time
  //   'signin' — the session is the problem
  //   'none'   — nothing the reader does in this tab changes the answer, and a
  //              button that cannot work is worse than no button: it converts
  //              a clear refusal into a thing that looks intermittent.
  const BY_CODE = {
    not_allowlisted: {
      key: 'dd.err_not_approved', action: 'none', icon: 'icon-lock',
      fallback: 'Your account is not approved on the trading bot yet. '
        + 'Send /start to it in Telegram — the operator gets the request.',
    },
    not_authorized: {
      key: 'dd.err_not_registered', action: 'none', icon: 'icon-lock',
      fallback: 'Not registered on the trading bot yet. '
        + 'Send /start to it in Telegram to register.',
    },
    gateway_disabled: {
      key: 'dd.err_bot_unlinked', action: 'none', icon: 'icon-offline',
      fallback: 'This site is not connected to the trading bot — '
        + 'the operator needs to finish that setup.',
    },
    admin_only: {
      key: 'dd.err_operator_only', action: 'none', icon: 'icon-lock',
      fallback: 'This panel is for the operator account only.',
    },
    operator_only: {
      key: 'dd.err_operator_only', action: 'none', icon: 'icon-lock',
      fallback: 'This panel is for the operator account only.',
    },
    rate_limited: {
      key: 'dd.err_rate_limited', action: 'retry', icon: 'icon-offline',
      fallback: 'Too many requests just now — wait a moment and try again.',
    },
  };

  // Status-derived, for the causes that carry no code of their own. 503 is the
  // website's own `isConfigured()` refusal: it never reached the bot, so no
  // amount of retrying changes it and the operator is the one who can.
  const BY_STATUS = {
    401: {
      key: 'dd.session_expired', action: 'signin', icon: 'icon-offline',
      fallback: 'Your session expired — sign in again to see this.',
    },
    503: {
      key: 'dd.err_bot_unlinked', action: 'none', icon: 'icon-offline',
      fallback: 'This site is not connected to the trading bot — '
        + 'the operator needs to finish that setup.',
    },
  };

  const GENERIC = {
    key: 'dd.err_panel', action: 'retry', icon: 'icon-offline',
    fallback: 'Couldn’t load this panel.',
  };

  /**
   * @param {{status?: number, code?: string}} err
   * @returns {{key: string, fallback: string, action: string, icon: string}}
   */
  function panelFailure(err) {
    const code = err && typeof err.code === 'string' ? err.code : '';
    // The code wins over the status: one 403 means "not approved for this bot"
    // and another means "operator only", and they need different sentences.
    if (Object.prototype.hasOwnProperty.call(BY_CODE, code)) return BY_CODE[code];
    const status = err && Number(err.status);
    if (status && Object.prototype.hasOwnProperty.call(BY_STATUS, status)) {
      return BY_STATUS[status];
    }
    return GENERIC;
  }

  /** The reason code an upstream JSON body carried, or '' when it carried none. */
  function codeOf(data) {
    if (!data || typeof data !== 'object') return '';
    const e = data.error;
    // Only a CODE, never prose. `{error: "News radar unavailable"}` is a
    // sentence some route wrote, not a member of any vocabulary, and treating
    // it as a key would silently miss forever.
    return typeof e === 'string' && /^[a-z][a-z0-9_]{2,39}$/.test(e) ? e : '';
  }

  return { panelFailure, codeOf, BY_CODE, BY_STATUS, GENERIC };
}));
