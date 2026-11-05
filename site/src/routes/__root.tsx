/**
 * The shell every page shares: skip link, sticky nav, outlet, footer.
 *
 * ONE SHELL, because the previous site had three. `index.html` was gold on
 * Georgia, `privacy.html` was Cinzel on ice-blue, and `submission.html` was
 * neon-on-black with a CRT overlay — three brands, none of them the product's.
 * A visitor moving between them had no way to tell they were still on the same
 * site, which is the cost this file exists to stop paying.
 */
import { Link, Outlet, createRootRoute } from '@tanstack/react-router'
import { SITE } from '../facts'
import { DOCS, PlatformLink, TELEGRAM } from '../components/PlatformLink'

/**
 * Desktop content links, Trade leftmost.
 *
 * Trade and AI are sections on the home page (`/#trade`, `/#ai`), not new
 * routes — the platform doors inside those sections are the existing
 * `data-platform-path` targets. Proof, Risk and Product are pages. Changelog
 * and Privacy stay in the footer: five content links plus Open platform and
 * Telegram is the bar that still fits.
 */
const PAGES = [
  { to: '/proof', label: 'Proof' },
  { to: '/risk', label: 'Risk' },
  { to: '/product', label: 'Product' },
] as const

const LINK = 'whitespace-nowrap text-sm transition-colors'

function ContentLinks() {
  return (
    <>
      <li>
        <a href="/#trade" className={`${LINK} text-ink-2 hover:text-ink`}>Trade</a>
      </li>
      <li>
        <a href="/#ai" className={`${LINK} text-ink-2 hover:text-ink`}>AI</a>
      </li>
      {PAGES.map((n) => (
        <li key={n.to}>
          {/* Colour lives on active/inactive, not on both. `text-ink-2` is
              later in the stylesheet than `text-accent`, so leaving it on an
              active link paints the current page the same grey as the rest. */}
          <Link
            to={n.to}
            className={LINK}
            inactiveProps={{ className: 'text-ink-2 hover:text-ink' }}
            activeProps={{ className: 'text-accent' }}
          >
            {n.label}
          </Link>
        </li>
      ))}
    </>
  )
}

function Wordmark() {
  return (
    <span className="inline-flex items-center gap-2.5">
      {/* The rune-R, inline SVG — no network request, scales cleanly, and
          inherits currentColor so it cannot drift from the accent. The
          contour is generated: scripts/brand_mark.py emits this same `d` into
          both favicons, the platform sprite and the PNG app icons, and
          tests/test_brand_mark_is_one_geometry.py fails if any of them
          drifts. A logo redrawn per surface is one logo per surface. */}
      <svg width="12" height="22" viewBox="9.5 4 13.1 24" aria-hidden="true" className="text-accent">
        <path
          d="M9.5 4 18.6 4 22.6 8.6 22.6 10.2 16.2 16.2 22.6 28 18.1 28 13.1 18.4 13.1 28 9.5 28ZM13.1 7.4 17.7 7.4 19.3 9.2 19.3 9.9 15.3 13.7 13.1 13.7Z"
          fill="currentColor" fillRule="evenodd"
        />
      </svg>
      <span
        className="font-[family-name:var(--font-brand)] text-[17px] font-bold tracking-[0.1em]"
      >
        {SITE.name}
      </span>
    </span>
  )
}

function Nav() {
  return (
    <header className="sticky top-0 z-50 border-b border-line bg-bg/85 backdrop-blur-md">
      <nav
        className="mx-auto max-w-6xl px-5 py-3.5"
        aria-label="Primary"
      >
        <div className="flex flex-wrap items-center gap-x-6 gap-y-3">
        <Link to="/" className="shrink-0 text-ink hover:text-ink">
          <Wordmark />
        </Link>
        <ul className="ml-auto hidden items-center gap-4 md:flex lg:gap-6">
          <ContentLinks />
        </ul>
        <div className="ml-auto flex shrink-0 items-center gap-2 md:ml-0">
          {/* Hidden until it resolves; Telegram is the always-present fallback
              so the nav is never left without a way through. */}
          <PlatformLink className="!px-4 whitespace-nowrap">Open platform</PlatformLink>
          <a
            href={TELEGRAM}
            target="_blank"
            rel="noopener"
            className="inline-flex min-h-11 shrink-0 items-center whitespace-nowrap rounded-md border border-line-2 px-4 py-2.5 text-sm font-semibold text-ink transition-colors hover:border-accent hover:text-accent-bright"
          >
            Telegram
          </a>
        </div>
      </div>
      {/* The same five links, on a phone, where the desktop row is hidden.
          Trade stays first. One row, scroll if the viewport is narrower. */}
      <ul className="mt-3 flex gap-4 overflow-x-auto pb-0.5 md:hidden">
        <ContentLinks />
      </ul>
    </nav>
    </header>
  )
}

function Footer() {
  return (
    <footer className="mt-24 border-t border-line">
      <div className="mx-auto max-w-6xl px-5 py-12">
        <div className="flex flex-wrap items-start justify-between gap-8">
          <div className="max-w-sm">
            <Wordmark />
            <p className="mt-3 text-sm text-ink-3">{SITE.tagline}</p>
          </div>
          <nav aria-label="Footer" className="flex flex-wrap gap-x-10 gap-y-3 text-sm">
            <ul className="space-y-2">
              <li><a href="/#trade" className="text-ink-2 hover:text-ink">Trade</a></li>
              <li><a href="/#ai" className="text-ink-2 hover:text-ink">AI</a></li>
              <li><Link to="/proof" className="text-ink-2 hover:text-ink">Proof</Link></li>
              <li><Link to="/risk" className="text-ink-2 hover:text-ink">Risk</Link></li>
              <li><Link to="/product" className="text-ink-2 hover:text-ink">Product</Link></li>
              <li><Link to="/changelog" className="text-ink-2 hover:text-ink">Changelog</Link></li>
              <li><Link to="/privacy" className="text-ink-2 hover:text-ink">Privacy</Link></li>
              <li><Link to="/terms" className="text-ink-2 hover:text-ink">Terms</Link></li>
              <li>
                <a href={DOCS} target="_blank" rel="noopener" className="text-ink-2 hover:text-ink">
                  Docs
                </a>
              </li>
              <li>
                <a href={TELEGRAM} target="_blank" rel="noopener" className="text-ink-2 hover:text-ink">
                  Telegram
                </a>
              </li>
              <li>
                <a href="/archive/hackathon" className="text-ink-2 hover:text-ink">
                  Hackathon archive
                </a>
              </li>
            </ul>
          </nav>
        </div>

        {/*
          The persistent disclaimer the old site never had. It sits on every
          page rather than on the one page somebody remembers to add it to,
          which is the only version of this that works.
        */}
        <p className="mt-10 border-t border-line pt-6 text-xs leading-relaxed text-ink-3">
          RUNECLAW is trading software, not financial advice. Nothing here is a
          recommendation to buy or sell any asset. Trading leveraged
          cryptocurrency carries substantial risk of loss, including loss of
          your entire balance. Past or simulated performance does not indicate
          future results. 18+.
        </p>
      </div>
    </footer>
  )
}

/**
 * The page for a path that does not exist. Prerendered to `404.html`, which
 * both static hosts serve for an unknown path: api_bridge's StaticFiles does
 * so on its own when the file is present, and nginx is told to in nginx.conf.
 * Before this, an unknown path got the homepage with a 200 — a wrong page
 * reported as the right one.
 */
function NotFound() {
  return (
    <article className="mx-auto max-w-3xl px-5 py-24 text-center">
      <p className="data text-xs uppercase tracking-wider text-ink-3">404</p>
      <h1
        className="mt-3 font-[family-name:var(--font-brand)] font-bold leading-tight"
        style={{ fontSize: 'var(--text-h1)' }}
      >
        There is no page here
      </h1>
      <p className="mx-auto mt-4 max-w-xl leading-relaxed text-ink-2">
        The address may be old or mistyped. Nothing on this site moved silently:
        every page it has is linked from the header or the footer, and the
        platform lives at its own address.
      </p>
      <div className="mt-8 flex flex-wrap items-center justify-center gap-3">
        <Link
          to="/"
          className="inline-flex min-h-11 items-center justify-center rounded-md bg-accent px-5 py-2.5 text-sm font-semibold text-bg transition-colors hover:bg-accent-bright"
        >
          Back to the start
        </Link>
        <PlatformLink>Open the platform</PlatformLink>
      </div>
    </article>
  )
}

export const Route = createRootRoute({
  notFoundComponent: NotFound,
  component: () => (
    <>
      <a className="skip-link" href="#main">Skip to content</a>
      <Nav />
      <main id="main">
        <Outlet />
      </main>
      <Footer />
    </>
  ),
})
