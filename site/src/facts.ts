/**
 * EVERY FACTUAL CLAIM THE SITE MAKES, IN ONE PLACE, EACH WITH ITS SOURCE.
 *
 * The old site shipped three numbers for one thing. `submission.html` said the
 * engine ran 19 pre-trade checks; README.md says 23 in three separate places
 * with a breakdown; and BTC sat frozen at $72,669 from June 2026 inside a page
 * that read as live. Nobody lied on purpose — the numbers were typed into
 * markup in three files at three times, and markup has no way to disagree with
 * itself out loud.
 *
 * So claims are DATA here, not JSX. Three consequences, all of them the point:
 *
 *   1. A number appears once. Changing it is one edit, not a grep.
 *   2. `source` is required by the type. A claim with no citation does not
 *      compile, which is the cheapest possible enforcement of "say where you
 *      got it".
 *   3. Anything not yet verified is simply ABSENT from this file, and the
 *      components omit what is absent rather than rendering a placeholder.
 *      A marketing page with one fewer statistic is a smaller page; a marketing
 *      page with an invented statistic is a liability.
 *
 * NEVER put a live market price in here. A price is true for seconds, and this
 * file is compiled into static HTML — which is exactly how $72,669 came to sit
 * on a page for three months looking current. Prices come from the live API on
 * the platform, or they do not appear.
 */

export type Claim = {
  /** The sentence as a reader sees it. */
  readonly text: string
  /** Where it was verified. A repo path, a file:line, or a doc reference. */
  readonly source: string
}

export type Stat = {
  readonly value: string
  readonly label: string
  readonly source: string
  /** Shown under the number when the plain figure would overstate. */
  readonly caveat?: string
}

export const SITE = {
  name: 'RUNECLAW',
  /** Overridable at build time via SITE_ORIGIN; see site/prerender.js. */
  origin: 'https://www.humanoid-traders.com',
  tagline: 'The AI trading engine you can talk to.',
  /**
   * "HUMAN-CONFIRMED" WAS HERE AND IT WAS FALSE.
   *
   * The first draft read "Paper by default, risk-gated, human-confirmed". Two
   * of those three survived a check against the code; the third did not.
   *
   *   bot/config.py:2188-2190
   *     # Allow auto-confirm to place LIVE (real-money) orders with no human
   *     # press. OPERATOR-ACTIVATED default ON. Set AUTO_CONFIRM_LIVE_ENABLED=0
   *     # to require a human tap for every live trade (the fail-closed posture).
   *     auto_confirm_live_enabled: bool = _env_bool("AUTO_CONFIRM_LIVE_ENABLED", True)
   *
   * With that default and `auto_confirm_threshold` at 0.85, a signal clearing
   * the bar places a real-money order with nobody pressing anything. README.md
   * says "explicit human confirmation before execution" in three places and is
   * wrong in all three. The homepage would have been the fourth.
   *
   * What replaced it is the pair that IS true on shipped defaults:
   *   bot/config.py:2142  simulation_mode: bool = _env_bool("SIMULATION_MODE", True)
   *   bot/config.py:2143  live_trading_enabled: bool = _env_bool("LIVE_TRADING_ENABLED", False)
   */
  promise:
    'Simulation-first: paper is the default and live trading stays off until '
    + 'you switch it on. Every call is hashed before the market moves, so the '
    + 'record can be checked by someone who does not trust the operator.',
} as const

/**
 * The engine's own numbers.
 *
 * POPULATED ONLY FROM VERIFIED SOURCES. The content-truth audit counts these
 * against `config/risk_manifest.yaml` rather than trusting prose, because prose
 * is what disagreed with itself last time. Until a figure clears that check it
 * does not belong here, and the section that would have shown it renders
 * without it.
 */
export const STATS: readonly Stat[] = []

/** Capability claims for the bento grid. Same rule: sourced or absent. */
export const CAPABILITIES: readonly Claim[] = []

/**
 * Things the site must never assert.
 *
 * Kept in the code rather than in a doc because a doc is not consulted while
 * writing a headline. `docs/ROADMAP.md` marks these gated or vision-only, and
 * `docs/TOKEN_ROADMAP.md` opens "No token exists. No sale has run."
 */
export const MUST_NOT_CLAIM: readonly string[] = [
  '$RCLAW token, staking, fee discounts, or any token-dependent reward',
  'agent vaults (ERC-4626) or any deposit-taking product',
  'DAO governance or on-chain performance-fee splits',
  'copy-trading revenue share, marketplace billing, or creator earnings',
  'idle-margin yield as anything but a read-only rate display',
  'a completed independent security audit',
  'any live market price baked into the page',
] as const
