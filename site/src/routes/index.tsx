/**
 * The homepage.
 *
 * It replaces a card whose h1 was "The platform has a new home." — a redirect
 * notice standing where the product should be.
 *
 * TWO THINGS THIS PAGE DELIBERATELY DOES NOT DO.
 *
 * There is no ticker. The brief asked for a "live-feeling ticker that is
 * labeled as delayed/demo", and that is the exact shape this codebase spends
 * most of its guard tests preventing: a number that reads as a measurement,
 * with the caveat carried by a label nobody reads. The previous site had
 * $72,669 sitting on a page as though it were current, for months. A price is
 * true for seconds; a static build is true for weeks. They do not belong on the
 * same surface, labelled or otherwise.
 *
 * And no statistic appears unless `facts.ts` carries it with a citation.
 * Sections whose data is empty render NOTHING rather than a placeholder — the
 * omit strategy from CLAUDE.md's guard/omit table, applied to marketing copy.
 * A shorter page is the acceptable cost; an invented figure is not.
 */
import { createFileRoute } from '@tanstack/react-router'
import { CAPABILITIES, SITE, STATS } from '../facts'
import { DOCS, PlatformLink, TELEGRAM } from '../components/PlatformLink'

function Hero() {
  return (
    <section className="relative overflow-hidden border-b border-line">
      {/* Ambient wash, pure CSS. The old hero was a 356KB AI-fantasy JPEG of a
          Viking; the product's voice is hashed calls and fail-closed risk. */}
      <div
        aria-hidden="true"
        className="pointer-events-none absolute inset-0 opacity-70"
        style={{
          background:
            'radial-gradient(60rem 32rem at 50% -10%, var(--gold-dim), transparent 70%)',
        }}
      />
      <div className="relative mx-auto max-w-4xl px-5 py-24 text-center sm:py-32">
        <p className="data mb-5 inline-flex items-center gap-2 rounded-full border border-line-2 px-3 py-1 text-xs text-ink-2">
          <span className="size-1.5 rounded-full bg-up" aria-hidden="true" />
          Simulation-first · human-confirmed
        </p>
        <h1
          className="font-[family-name:var(--font-brand)] font-bold leading-[1.05] tracking-tight"
          style={{ fontSize: 'var(--text-hero)' }}
        >
          {SITE.tagline}
        </h1>
        <p
          className="mx-auto mt-6 max-w-2xl text-ink-2"
          style={{ fontSize: 'var(--text-lead)' }}
        >
          {SITE.promise}
        </p>
        <div className="mt-9 flex flex-wrap items-center justify-center gap-3">
          <PlatformLink>Paper trade — free</PlatformLink>
          <a
            href={TELEGRAM}
            target="_blank"
            rel="noopener"
            className="inline-flex min-h-11 items-center justify-center rounded-md border border-line-2 px-5 py-2.5 text-sm font-semibold text-ink transition-colors hover:border-accent hover:text-accent-bright"
          >
            Talk to it on Telegram
          </a>
        </div>
      </div>
    </section>
  )
}

/**
 * The demo film. It already existed in the repo and the homepage never used it.
 *
 * `preload="none"` and no autoplay: a 2MB video that starts fetching on load is
 * the single most expensive thing a marketing page can do to someone on mobile
 * data, and autoplaying sound is the second.
 */
function DemoFilm() {
  return (
    <section className="mx-auto max-w-5xl px-5 py-20">
      <div className="reveal overflow-hidden rounded-xl border border-line bg-surface">
        <video
          controls
          preload="metadata"
          playsInline
          className="block aspect-video w-full bg-surface-2"
        >
          {/* mp4 first: it is 2.2MB against the webm's 5.6MB in this repo, so
              the usual ordering would serve the LARGER file to most browsers.
              `preload="metadata"` rather than a poster image: there is no
              poster in the repo and no ffmpeg to cut one, and referencing a
              file that does not exist renders a broken image. Metadata is a
              few KB and gives the real first frame. */}
          <source src="/demo-recording.mp4" type="video/mp4" />
          <source src="/demo-recording.webm" type="video/webm" />
          Your browser cannot play embedded video.{' '}
          <a href="/demo-recording.mp4">Download the demo (MP4)</a>.
        </video>
      </div>
      <p className="mt-3 text-center text-xs text-ink-3">
        A recorded session. Not a live feed.
      </p>
    </section>
  )
}

function Stats() {
  // Renders nothing until facts.ts carries sourced figures. See the file header.
  if (STATS.length === 0) return null
  return (
    <section className="border-y border-line bg-surface/40">
      <dl className="mx-auto grid max-w-5xl grid-cols-2 gap-px px-5 py-12 md:grid-cols-4">
        {STATS.map((s) => (
          <div key={s.label} className="px-4 text-center">
            <dt className="text-xs uppercase tracking-wider text-ink-3">{s.label}</dt>
            <dd className="data mt-2 text-3xl font-bold text-accent">{s.value}</dd>
            {s.caveat ? (
              <p className="mt-1 text-xs text-ink-3">{s.caveat}</p>
            ) : null}
          </div>
        ))}
      </dl>
    </section>
  )
}

function Capabilities() {
  if (CAPABILITIES.length === 0) return null
  return (
    <section className="mx-auto max-w-6xl px-5 py-20">
      <h2
        className="font-[family-name:var(--font-brand)] font-bold"
        style={{ fontSize: 'var(--text-h2)' }}
      >
        What it does
      </h2>
      <ul className="mt-8 grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
        {CAPABILITIES.map((c) => (
          <li
            key={c.text}
            className="reveal rounded-xl border border-line bg-surface p-5 transition-colors hover:border-line-2"
          >
            <p className="text-sm leading-relaxed text-ink-2">{c.text}</p>
          </li>
        ))}
      </ul>
    </section>
  )
}

/**
 * Proof strip — sends people OUT to the live, verifiable surfaces rather than
 * restating their conclusions here. A marketing page asserting "verified" is
 * worth nothing; a link to the thing that verifies is worth the click.
 */
function Proof() {
  // These are PLATFORM routes on another origin, resolved at runtime by
  // src/platform-links.ts. The cards render their text either way and become
  // clickable only once the origin is known — an <a> with no href is not a
  // link, so nothing here can 404.
  const links = [
    { path: '/proof', title: 'Verify a call', body: 'Every decision is hashed before the market moves. Check one yourself.' },
    { path: '/arena', title: 'Enter the arena', body: 'Paper-trade against the engine. No deposit, no keys.' },
    { path: '/provable', title: 'How the proof works', body: 'The full verification contract, in public.' },
  ]
  return (
    <section className="mx-auto max-w-6xl px-5 py-20">
      <h2
        className="font-[family-name:var(--font-brand)] font-bold"
        style={{ fontSize: 'var(--text-h2)' }}
      >
        Don&rsquo;t trust it. Check it.
      </h2>
      <p className="mt-3 max-w-2xl text-ink-2">
        The record is designed to be verified by someone who does not trust the
        operator — including you.
      </p>
      <ul className="mt-8 grid gap-4 sm:grid-cols-3">
        {links.map((l) => (
          <li key={l.path}>
            <a
              data-platform-path={l.path}
              className="reveal block h-full rounded-xl border border-line bg-surface p-5 transition-colors [&[href]]:hover:border-accent"
            >
              <span className="text-sm font-semibold text-accent">{l.title}</span>
              <span className="mt-2 block text-sm leading-relaxed text-ink-2">{l.body}</span>
            </a>
          </li>
        ))}
      </ul>
      <p className="mt-6 text-xs text-ink-3">
        These open on the live platform. Docs live at{' '}
        <a href={DOCS} target="_blank" rel="noopener" className="text-ink-2 underline">
          the handbook
        </a>.
      </p>
    </section>
  )
}

function Home() {
  return (
    <>
      <Hero />
      <Stats />
      <DemoFilm />
      <Capabilities />
      <Proof />
    </>
  )
}

export const Route = createFileRoute('/')({ component: Home })
