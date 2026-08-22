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
 * Only destinations that EXIST.
 *
 * The first draft listed How it works / Risk / Proof / Platform because the
 * brief's information architecture calls for them. None are built yet, so all
 * four would have 404'd — on a site whose entire argument is that it does not
 * assert things it cannot back. A nav is a promise about what is behind it.
 * These come back one at a time, as each page ships.
 */
const NAV = [
  { to: '/privacy', label: 'Privacy' },
] as const

function Wordmark() {
  return (
    <span className="inline-flex items-center gap-2.5">
      {/* The hex rune mark, inline SVG — no network request, scales cleanly,
          and inherits currentColor so it cannot drift from the accent. */}
      <svg width="22" height="22" viewBox="0 0 24 24" aria-hidden="true" className="text-accent">
        <path
          d="M12 1.6 21 6.8v10.4L12 22.4 3 17.2V6.8z"
          fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinejoin="round"
        />
        <path d="M9 16V8h3.4a2.6 2.6 0 0 1 0 5.2H9.6L14 16" fill="none"
          stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
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
        className="mx-auto flex max-w-6xl items-center gap-6 px-5 py-3.5"
        aria-label="Primary"
      >
        <Link to="/" className="shrink-0 text-ink hover:text-ink">
          <Wordmark />
        </Link>
        <ul className="ml-auto hidden items-center gap-6 md:flex">
          {NAV.map((n) => (
            <li key={n.to}>
              <Link
                to={n.to}
                className="text-sm text-ink-2 transition-colors hover:text-ink"
                activeProps={{ className: 'text-accent' }}
              >
                {n.label}
              </Link>
            </li>
          ))}
        </ul>
        <div className="ml-auto flex items-center gap-2 md:ml-0">
          {/* Hidden until it resolves; Telegram is the always-present fallback
              so the nav is never left without a way through. */}
          <PlatformLink className="!px-4">Open platform</PlatformLink>
          <a
            href={TELEGRAM}
            target="_blank"
            rel="noopener"
            className="inline-flex min-h-11 items-center rounded-md border border-line-2 px-4 py-2.5 text-sm font-semibold text-ink transition-colors hover:border-accent hover:text-accent-bright"
          >
            Telegram
          </a>
        </div>
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
              <li><Link to="/proof" className="text-ink-2 hover:text-ink">How the proof works</Link></li>
              <li><Link to="/risk" className="text-ink-2 hover:text-ink">The risk gate</Link></li>
              <li><Link to="/privacy" className="text-ink-2 hover:text-ink">Privacy</Link></li>
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

export const Route = createRootRoute({
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
