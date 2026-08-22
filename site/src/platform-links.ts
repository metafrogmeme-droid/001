/**
 * The only script this site ships.
 *
 * Every page is prerendered to real HTML, so React has nothing to do at
 * runtime: there is no state, no interactivity, and no content that arrives
 * after paint. Hydrating anyway cost 281KB raw / 91KB gzip to re-render markup
 * that was already on screen — the same objection the brief raises against
 * pulling 90KB of GSAP to fade a heading in, and it would have been shipped by
 * a site whose pitch is that it does not waste your resources.
 *
 * React 19, TanStack Router and Tailwind v4 still BUILD the site — components,
 * one shared shell, typed routes, and the prerender that makes it crawlable.
 * They just do not follow the visitor home.
 *
 * What is left is this: resolve the platform origin once, fill in every
 * `data-platform-path` anchor, reveal the ones that were hidden. If nothing
 * answers, nothing changes — the cards keep their text and simply are not
 * clickable, and the Telegram and Docs links, which are real hrefs in the
 * markup, carry the page.
 */

/** Tried in order. api_bridge serves the first; nginx proxies the second. */
const ENDPOINTS = ['/platform-url', '/api/platform-url']

async function resolveBase(): Promise<string | null> {
  for (const path of ENDPOINTS) {
    try {
      const r = await fetch(path, { signal: AbortSignal.timeout(6000) })
      if (!r.ok) continue
      const d: unknown = await r.json()
      const url = typeof d === 'object' && d !== null && 'url' in d
        ? (d as { url?: unknown }).url
        : undefined
      // A configured-but-empty WEBSITE_URL comes back as "". That is "not
      // configured", not a link to the site root — the endpoint's own docstring
      // says it returns empty when unset. Treating "" as valid would point
      // every CTA at this page.
      if (typeof url === 'string' && url.length > 0) return url.replace(/\/+$/, '')
    } catch { /* try the next endpoint */ }
  }
  return null
}

export async function wirePlatformLinks(): Promise<void> {
  const nodes = document.querySelectorAll<HTMLAnchorElement>('[data-platform-path]')
  if (nodes.length === 0) return
  const base = await resolveBase()
  if (!base) return          // absence is not an error state; leave it alone
  for (const el of nodes) {
    el.href = base + (el.dataset.platformPath || '')
    el.hidden = false
  }
}
