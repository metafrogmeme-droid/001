/**
 * Everything that points at the live platform.
 *
 * THE PROBLEM. The platform is a SEPARATE deployment and its origin is operator
 * config (`WEBSITE_URL`). `api_bridge.py` exposes it at `/platform-url`; behind
 * nginx the same thing is proxied at `/api/platform-url`. Neither is guaranteed
 * to answer, and there is no correct value to guess — a hardcoded origin keeps
 * looking like a working link after the platform moves and sends every visitor
 * somewhere else without erroring.
 *
 * A related bug this shape kills: the proof strip first linked to `/proof`,
 * `/arena` and `/provable` as site-relative paths. Those are PLATFORM routes on
 * another host, so each would have resolved against the marketing origin and
 * 404'd — on the three links whose entire job is to prove the product is
 * checkable. A relative href to another deployment looks correct in review and
 * is wrong in production, because there is no origin for it to be relative to.
 *
 * THE SHAPE. These components render NO href and hold no state. They emit
 * `data-platform-path`, and `src/platform-links.ts` — thirty lines of vanilla
 * JS, the only script this site ships — resolves the origin once and fills them
 * in. Two consequences:
 *
 *   * An `<a>` with no href is not a link: it is not focusable, not clickable,
 *     and announces as plain text. So with no JS, or with the platform
 *     unconfigured, the CARD STILL READS and simply is not clickable. Omission
 *     without losing the content, which is the guard/omit table's second row.
 *   * There is one resolution mechanism, used everywhere, instead of each
 *     surface inventing its own fallback.
 *
 * Lifted from the previous `website/index.html`, which got the fallback logic
 * right. What is added is that the fallback CTAs are now guaranteed to exist
 * beside it rather than being a comment about what happens if it fails.
 */

/** Stable public destinations. Real hrefs — safe to render unconditionally. */
export const TELEGRAM = 'https://t.me/HTRUNECLAW_bot'
export const DOCS = 'https://humanoid-traders-1.gitbook.io/humanoid-traders-ai'

export function PlatformLink(
  { children = 'Open the platform', className = '', variant = 'primary', path = '', hideUntilResolved = true }:
  {
    children?: React.ReactNode
    className?: string
    variant?: 'primary' | 'ghost'
    /** Platform-relative path, e.g. '/proof'. Empty means the platform root. */
    path?: string
    /**
     * A CTA that does nothing is worse than an absent one, so buttons hide
     * until resolved. Content cards pass false: their text is worth reading
     * whether or not it can be clicked.
     */
    hideUntilResolved?: boolean
  },
) {
  const skin = variant === 'primary'
    ? 'bg-accent text-bg hover:bg-accent-bright'
    : 'border border-line-2 text-ink hover:border-accent hover:text-accent-bright'
  return (
    <a
      data-platform-path={path}
      hidden={hideUntilResolved}
      className={
        'inline-flex min-h-11 items-center justify-center rounded-md px-5 py-2.5 '
        + `text-sm font-semibold transition-colors ${skin} ${className}`
      }
    >
      {children}
    </a>
  )
}
