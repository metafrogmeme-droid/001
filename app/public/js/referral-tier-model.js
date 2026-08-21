/**
 * What the invite panel is allowed to SAY about your referral tier.
 *
 * A PERK IS A CLAIM, AND FOUR OF THE FIVE WERE NOT BACKED BY ANYTHING.
 *
 * `REFERRAL_TIERS` in app/auth.js shipped five milestone perks and its own
 * source comment stated the truth about them — "the perks are aspirational and
 * mostly land with the token/billing layer... It does NOT grant fee credits or
 * change live limits". The comment was honest. The card was not: it rendered
 * `Protocol revenue share when the token launches.` in the same voice, the same
 * weight and beside the same gold chip as `Your invite link is live.` — one of
 * which is true today and one of which depends on a token that
 * `docs/TOKEN_ROADMAP.md` opens by saying does not exist.
 *
 * That is this repo's signature failure moved one layer out from numbers to
 * promises: THE CODE KNEW AND THE SURFACE DID NOT SAY. Two of the five perks
 * were worse than aspirational — "Priority support" and "Early access to new
 * agents & features" are the PAID Pro and Elite plan's own selling points
 * (dashboard.js's plan table), offered here for one and three invites and
 * granted by nothing. Nothing in the tree reads a referral tier: `referralTier`
 * has exactly one caller, the endpoint that prints it.
 *
 * So a perk now carries its own state, and `perkInForce` is what the renderer
 * paints from. Colour is a claim; so is equal weight.
 *
 * ABSENT IS NOT STARTER. The old renderer opened with
 * `const tier = r.data.tier || { name: 'Starter', perk: '' }` and
 * `const count = r.data.count || 0`, so a response that carried a link but no
 * tier — the exact shape the endpoint now returns when the count is
 * unreadable — rendered as **Starter, 0 joined, 0% of the way to Connector**.
 * A confident bottom-of-the-ladder verdict manufactured from no data, shown to
 * someone who may have invited twenty people. `null` here means OMIT the tier
 * block; the link and the share buttons above it are independently true and go
 * on being shown. Guard would blank a panel that is mostly fine — see the
 * guard/omit table in CLAUDE.md.
 */
(function (root, factory) {
  const api = factory();
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.ReferralTierModel = api;
}(typeof self !== 'undefined' ? self : this, function () {
  'use strict';

  /** Shown under a perk nobody has earned yet when the server did not say why.
   *  A planned perk with no note would differ from a live one by colour alone,
   *  and colour does not survive being read aloud, screenshotted in greyscale,
   *  or rendered by a client that never loaded the stylesheet. */
  const NOT_IN_FORCE = 'Not in force yet.';

  const PERK_LIVE = 'ref-perk';
  const PERK_PLANNED = 'ref-perk ref-perk--planned';

  function readableCount(value) {
    // Number.isInteger, not `Number(x) || 0`. A tier is a statement about how
    // many people this person brought; computing one from an unreadable count
    // asserts a number nobody measured. `null` propagates to "omit".
    return Number.isInteger(value) && value >= 0 ? value : null;
  }

  /**
   * The invite panel's tier block, or null when there is nothing honest to say.
   *
   * @param {object|null} data  parsed body of GET /api/auth/referrals
   * @returns {null|{
   *   name: string, count: number,
   *   perk: {text: string, inForce: boolean, note: string|null, cls: string},
   *   next: null|{name: string, remaining: number, at: number, pct: number},
   *   topTier: boolean
   * }}
   */
  function referralTierState(data) {
    if (!data || typeof data !== 'object') return null;
    const count = readableCount(data.count);
    if (count === null) return null;

    const tier = data.tier;
    if (!tier || typeof tier !== 'object') return null;
    const name = String(tier.name || '').trim();
    if (!name) return null;

    // Anything that is not explicitly 'live' is planned. The default leans
    // toward NOT claiming: a tier from an older server, or one whose state a
    // future edit forgets to set, must not inherit "you have this".
    const inForce = tier.state === 'live';
    const requires = typeof tier.requires === 'string' ? tier.requires.trim() : '';
    const perkText = String(tier.perk || '').trim();

    const next = nextState(data.next, count);
    return {
      name,
      count,
      perk: {
        text: perkText,
        inForce,
        note: inForce ? null : (requires || NOT_IN_FORCE),
        cls: inForce ? PERK_LIVE : PERK_PLANNED,
      },
      next,
      topTier: next === null,
    };
  }

  function nextState(next, count) {
    if (!next || typeof next !== 'object') return null;
    const at = Number(next.at);
    const name = String(next.name || '').trim();
    // `at > 0` is load-bearing twice: it is the divisor below, and a milestone
    // at zero is one you are already past, so there is no progress to draw.
    if (!name || !Number.isFinite(at) || at <= 0) return null;
    const remaining = Number.isInteger(next.remaining) && next.remaining >= 0
      ? next.remaining
      : Math.max(0, at - count);
    return {
      name,
      at,
      remaining,
      pct: Math.max(0, Math.min(100, Math.round((count / at) * 100))),
    };
  }

  return { referralTierState, NOT_IN_FORCE, PERK_LIVE, PERK_PLANNED };
}));
