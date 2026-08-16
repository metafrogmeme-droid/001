/**
 * May this swap be signed, right now, by this wallet?
 *
 * The last gate before an irreversible action. `meme_swap.build_swap` decided
 * the terms were obtainable; this decides whether they are still true at the
 * moment a human clicks, which is a different question and the one that costs
 * money when it is skipped.
 *
 * PURE, AND SEPARATE FROM THE PAGE ON PURPOSE. CLAUDE.md's rule — "when there
 * is no seam, make one" — exists because the dashboard's engine chip was built
 * inline in six thousand lines of browser script and could only be tested by
 * grepping for the spelling of an expression. A decision that authorises
 * spending should not be reachable only through a click, so it lives here and
 * `app/test/swap_sign_model.test.js` drives every branch.
 *
 * WHAT IT REFUSES, AND WHY EACH ONE IS A REAL LOSS
 *
 * expired      A quote is a price at a moment. A page open for two minutes
 *              holds terms that stopped being true ninety seconds ago, and the
 *              wallet will show the stale numbers without complaint. Checked
 *              at CLICK time, never at page load — that gap is the whole bug.
 * already_sent A broadcast is not idempotent. `intent_id` is the same for the
 *              same terms, so a double-click, a retry, or a back-button
 *              resubmit is recognised HERE rather than discovered on-chain.
 * wrong_wallet The transaction is built for one public key. If the user
 *              switched accounts in Phantom since the build — trivially easy,
 *              and invisible to this page unless it looks — signing binds a
 *              trade to an account that never agreed to it.
 * not_buildable / already_signed
 *              The build itself said no, or says it has already been through
 *              this. Either way there is nothing here to authorise.
 * not_signable A PERMANENT refusal, and the reason it is checked before the
 *              freshness ones: Jupiter v6 quotes mainnet only, so a build
 *              labelled `simulate` still carries a real MAINNET transaction.
 *              The server decides who may sign and says so in `signable`; this
 *              page fail-closes when the flag is absent rather than reading
 *              `network` and drawing its own conclusion. Reported first
 *              because "expired — request a fresh quote" would be a lie when
 *              no fresh quote will ever be signable either.
 * mainnet_unconfirmed
 *              Real funds require a deliberate second act. The build being
 *              mainnet is not the same as a human having said "mainnet".
 *
 * `ok: true` is returned only when every one of those is answered. There is no
 * branch that returns ok on missing information — an unreadable build, absent
 * terms and an unknown wallet all refuse, because the damaging default here is
 * "go ahead".
 */
(function (root, factory) {
  const api = factory();
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.SwapSignModel = api;
}(typeof self !== 'undefined' ? self : this, function () {
  'use strict';

  /** A finite number, or null — never NaN, never a string that looked numeric. */
  function num(v) {
    if (v === null || v === undefined || typeof v === 'boolean') return null;
    const f = typeof v === 'number' ? v : parseFloat(v);
    return Number.isFinite(f) ? f : null;
  }

  function refuse(code, reason) {
    return { ok: false, code: code, reason: reason };
  }

  /**
   * `{ok, code, reason}` — the answer to "sign this now?".
   *
   * `ctx` = {nowMs, connectedWallet, sentIntents:Set|Array, mainnetConfirmed}.
   */
  function canSign(build, ctx) {
    const c = ctx || {};
    if (!build || typeof build !== 'object') {
      return refuse('no_build', 'Nothing to sign.');
    }
    if (build.buildable !== true) {
      return refuse('not_buildable',
        build.reason ? String(build.reason) : 'This swap could not be built.');
    }
    if (build.signed === true || build.broadcast === true) {
      return refuse('already_signed', 'This swap has already been sent.');
    }

    // Permanent conditions before transient ones. A `simulate` build is never
    // signable no matter how fresh its quote is, and telling the user to
    // request a new one would send them round a loop that cannot end.
    //
    // `!== true` and not `=== false`: an older server that has never heard of
    // this field must land on "no". Absent is not permission.
    if (build.signable !== true) {
      return refuse('not_signable', build.not_signable_reason
        ? String(build.not_signable_reason)
        : 'This build is review-only and will not be signed.');
    }

    const terms = build.terms;
    if (!terms || typeof terms !== 'object') {
      return refuse('no_terms', 'No terms to review — nothing will be signed.');
    }

    // Expiry FIRST among the term checks: a stale price is the failure a user
    // is least able to notice and most likely to pay for.
    const expiresAt = num(terms.expires_at);
    const nowS = num(c.nowMs) === null ? null : num(c.nowMs) / 1000;
    if (expiresAt === null || nowS === null) {
      // Terms we cannot date are terms we cannot honour.
      return refuse('expired', 'These terms cannot be dated — request a fresh quote.');
    }
    if (nowS >= expiresAt) {
      return refuse('expired',
        'This quote has expired — the price has moved. Request a fresh one.');
    }

    const intent = build.intent_id;
    if (!intent) {
      return refuse('no_intent', 'This build carries no intent id — refusing to send.');
    }
    const sent = c.sentIntents;
    const alreadySent = sent instanceof Set ? sent.has(intent)
      : Array.isArray(sent) ? sent.indexOf(intent) !== -1 : false;
    if (alreadySent) {
      return refuse('already_sent',
        'This exact swap was already sent. Request a fresh quote to trade again.');
    }

    // The build is bound to one public key. A wallet switch between build and
    // click is silent unless something looks for it.
    const want = build.user_public_key || (terms && terms.user_public_key) || null;
    const have = c.connectedWallet || null;
    if (!have) {
      return refuse('wrong_wallet', 'No wallet connected.');
    }
    if (want && want !== have) {
      return refuse('wrong_wallet',
        'The connected wallet is not the one this swap was built for. '
        + 'Reconnect the original account, or rebuild for this one.');
    }

    if (build.network === 'mainnet' && c.mainnetConfirmed !== true) {
      return refuse('mainnet_unconfirmed',
        'This is a MAINNET transaction with real funds. Confirm explicitly.');
    }

    return { ok: true, code: 'ready', reason: 'Ready to sign in your wallet.' };
  }

  /** Whole seconds until the quote dies, or 0 — never negative, never NaN. */
  function secondsLeft(build, nowMs) {
    const exp = num(build && build.terms && build.terms.expires_at);
    const now = num(nowMs);
    if (exp === null || now === null) return 0;
    return Math.max(0, Math.floor(exp - now / 1000));
  }

  /**
   * The cells a review card shows.
   *
   * Every unknown renders as an em dash rather than a zero: a price impact of
   * `0.00%` is a measurement, and printing it for a figure the quote did not
   * carry is the same defect as a `0.00%` win rate over no trades.
   */
  function reviewCells(build) {
    const t = (build && build.terms) || {};
    const impact = num(t.price_impact_pct);
    const slip = num(t.slippage_bps);
    return {
      network: build && build.network ? String(build.network) : '—',
      isMainnet: !!build && build.network === 'mainnet',
      // Review-only is the DEFAULT rendering, for the same reason the guard
      // fail-closes: a card that omits the warning whenever the flag is
      // missing shows its most dangerous face on its least certain input.
      reviewOnly: !(build && build.signable === true),
      reviewOnlyReason: (build && build.not_signable_reason)
        ? String(build.not_signable_reason)
        : 'This build is review-only and will not be signed.',
      // Said plainly wherever the card appears, because `simulate` reads as a
      // safety claim and is not one: Jupiter v6 quotes mainnet only, so these
      // bytes are a mainnet transaction whatever the mode is called.
      networkCaveat: 'Jupiter quotes mainnet only — this transaction is a '
        + 'MAINNET transaction regardless of the mode named above.',
      inAmount: t.in_amount ? String(t.in_amount) : '—',
      outAmount: t.out_amount ? String(t.out_amount) : '—',
      minReceived: t.other_amount_threshold ? String(t.other_amount_threshold) : '—',
      slippage: slip === null ? '—' : (slip / 100).toFixed(2) + '%',
      priceImpact: impact === null ? '—' : (impact * 100).toFixed(2) + '%',
      // Colour is a claim: an unknown impact gets no warning styling, and a
      // known-bad one does.
      impactClass: impact === null ? 'muted' : (impact >= 0.05 ? 'neg' : ''),
      intent: build && build.intent_id ? String(build.intent_id) : '—',
      // Stated on the card, not just in the code. Every reader of this page is
      // one assumption away from thinking the site did the trade.
      custody: 'RUNECLAW never signs and never holds your keys — you sign this '
        + 'in your own wallet.',
    };
  }

  return { canSign: canSign, secondsLeft: secondsLeft, reviewCells: reviewCells };
}));
