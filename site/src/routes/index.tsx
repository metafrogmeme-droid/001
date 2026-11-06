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
 *
 * SECTION ORDER. Trade, then a short "talk to it", then the proof line.
 * Stats, the demo and the capability grid are still on this page — under
 * that, not deleted. The sourced "what ships" write-up is the product page.
 * The BOT Chain note is last. A deployed contract is not the product, so it
 * does not lead the page and it is not a nav item.
 */
import { createFileRoute, Link } from '@tanstack/react-router'
import { BOT_CHAIN, CAPABILITIES, SITE, STATS } from '../facts'
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
        {/* "human-confirmed" was here too, and was false for the same reason
            as the promise below it — see the note in facts.ts. Auto-confirm
            places live orders with no human press on shipped defaults. */}
        <p className="data mb-5 inline-flex items-center gap-2 rounded-full border border-line-2 px-3 py-1 text-xs text-ink-2">
          <span className="size-1.5 rounded-full bg-up" aria-hidden="true" />
          Paper by default · live is opt-in
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
          {/* /arena is the paper-trade door (app/server.js). The button used to
              open the platform root, which is a catalogue, not a trade. */}
          <PlatformLink path="/arena">Paper trade — free</PlatformLink>
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
 * A platform card that stays readable when the origin is unknown.
 * `data-platform-path` is filled in by platform-links.ts; with no href the
 * card is text, not a link that 404s against this host.
 */
function PlatformCards(
  { items }: { items: readonly { path: string; title: string; body: string }[] },
) {
  return (
    <ul className="mt-6 grid gap-4 sm:grid-cols-3">
      {items.map((l) => (
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
  )
}

/**
 * Trade strip. Paths that already exist on the platform — nothing invented:
 *   /dashboard            app/server.js, the cockpit
 *   /dashboard#signals    dashboard.js VIEWS id "signals"
 *   /arena                app/server.js
 */
function TradeStrip() {
  const doors = [
    {
      path: '/dashboard',
      title: 'Cockpit',
      body: 'Portfolio, markets and execution. Paper by default; live only on your own keys.',
    },
    {
      path: '/dashboard#signals',
      title: 'Signals',
      body: 'The signal stream. Each call is hashed before the market moves.',
    },
    {
      path: '/arena',
      title: 'Arena',
      body: 'Paper-trade against the engine. No deposit, no keys.',
    },
  ] as const
  return (
    <section id="trade" aria-labelledby="trade-h" className="scroll-mt-28 mx-auto max-w-6xl px-5 py-14">
      <h2
        id="trade-h"
        className="font-[family-name:var(--font-brand)] font-bold"
        style={{ fontSize: 'var(--text-h2)' }}
      >
        Trade
      </h2>
      <p className="mt-3 max-w-2xl text-ink-2">
        Paper first. The cockpit, the signal stream, or the arena.
      </p>
      <PlatformCards items={doors} />
    </section>
  )
}

/**
 * Short on purpose. The sourced write-up of the assistant lives on /product.
 *   /dashboard#chat   VIEWS id "chat"
 *   /dashboard#feed   VIEWS id "feed" — the agent's mind-stream
 * Telegram is a real href, so the section still has a way through when the
 * platform origin is unset.
 */
function AiTeaser() {
  return (
    <section id="ai" aria-labelledby="ai-h" className="scroll-mt-28 border-y border-line bg-surface/40">
      <div className="mx-auto flex max-w-6xl flex-col gap-6 px-5 py-12 md:flex-row md:items-center md:justify-between">
        <div className="max-w-xl">
          <h2
            id="ai-h"
            className="font-[family-name:var(--font-brand)] font-bold"
            style={{ fontSize: 'var(--text-h2)' }}
          >
            Talk to it
          </h2>
          <p className="mt-3 text-ink-2">
            The chat reads your portfolio, your risk state and a live scan
            through read-only tools — on the web and in Telegram.
          </p>
        </div>
        <div className="flex flex-wrap gap-3">
          <a
            href={TELEGRAM}
            target="_blank"
            rel="noopener"
            className="inline-flex min-h-11 items-center justify-center rounded-md border border-line-2 px-5 py-2.5 text-sm font-semibold text-ink transition-colors hover:border-accent hover:text-accent-bright"
          >
            Talk on Telegram
          </a>
          <PlatformLink path="/dashboard#chat" variant="ghost" hideUntilResolved={false}>
            Open the chat
          </PlatformLink>
          <PlatformLink path="/dashboard#feed" variant="ghost" hideUntilResolved={false}>
            Mind-stream
          </PlatformLink>
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
      <p className="mt-3 max-w-2xl text-sm text-ink-2">
        The short list. What ships, and what does not, is on the{' '}
        <Link to="/product" className="text-ink underline hover:text-accent">
          product page
        </Link>.
      </p>
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
    <section id="proof" aria-labelledby="proof-h" className="scroll-mt-28 mx-auto max-w-6xl px-5 py-14">
      <h2
        id="proof-h"
        className="font-[family-name:var(--font-brand)] font-bold"
        style={{ fontSize: 'var(--text-h2)' }}
      >
        Don&rsquo;t trust it. Check it.
      </h2>
      <p className="mt-3 max-w-2xl text-ink-2">
        The record is designed to be verified by someone who does not trust the
        operator — including you.
      </p>
      <PlatformCards items={links} />
      <p className="mt-6 text-xs text-ink-3">
        These open on the live platform. Docs live at{' '}
        <a href={DOCS} target="_blank" rel="noopener" className="text-ink-2 underline">
          the handbook
        </a>.
      </p>
    </section>
  )
}

/**
 * An external URL whose visible text is the href.
 *
 * The two BOT Chain origins have to be findable as those exact strings, in
 * the link and on the page. A label such as "explorer" would hide the URL
 * the review is looking for, and a trailing slash would be a different URL.
 */
function ExactUrl({ href }: { href: string }) {
  return (
    <a
      href={href}
      target="_blank"
      rel="noopener"
      className="inline-flex min-h-11 w-full items-center break-all rounded-md border border-line-2 px-4 py-2.5 text-sm font-semibold text-ink transition-colors hover:border-accent hover:text-accent-bright sm:w-auto"
    >
      {href}
      <span className="sr-only"> (opens in a new tab)</span>
    </a>
  )
}

/**
 * Prefer breaks after a slash and every 12 characters, so a 42-character
 * address wraps on a phone without leaving one glyph on its own line.
 * The characters stay in order; a break opportunity is not a space.
 */
function withBreaks(value: string) {
  const nodes: React.ReactNode[] = []
  value.split(/(\/)/).forEach((part, i) => {
    if (part === '/') {
      nodes.push(<span key={`s${i}`}>/<wbr /></span>)
      return
    }
    const groups = part.match(/.{1,12}/g) ?? []
    groups.forEach((group, j) => {
      nodes.push(
        <span key={`${i}-${j}`}>
          {group}
          {j < groups.length - 1 ? <wbr /> : null}
        </span>,
      )
    })
  })
  return nodes
}

function RecordLink({ href }: { href: string }) {
  return (
    <a
      href={href}
      target="_blank"
      rel="noopener"
      className="data mt-2 block text-sm text-ink underline decoration-line-2 underline-offset-4 hover:text-accent-bright"
    >
      {withBreaks(href)}
      <span className="sr-only"> (opens in a new tab)</span>
    </a>
  )
}

/**
 * BOT Chain file-hash contract. Last on the page on purpose.
 *
 * WHAT THIS SECTION IS NOT. It is not a claim that RUNECLAW trades are
 * sealed there, and it is not a button into a checker. The separate static
 * checker answered 404 on the date in facts.ts, so it is not linked: a link
 * to a missing page is a promise the click cannot keep. A deployed contract
 * is the commitment surface. It is not a finished product flow, and this
 * site does not sign or send anything to it.
 *
 * The hash commits the bytes of a file. It does not establish that the file's
 * contents are true. facts.ts holds the addresses; this function does not
 * restate them as a second copy.
 */
function BotChain() {
  const contractUrl = `${BOT_CHAIN.explorer}/address/${BOT_CHAIN.address}`
  const ownerUrl = `${BOT_CHAIN.explorer}/address/${BOT_CHAIN.ownerPublisher}`
  const txUrl = `${BOT_CHAIN.explorer}/tx/${BOT_CHAIN.deployTx}`
  return (
    <section
      id="botchain"
      aria-labelledby="botchain-h"
      className="scroll-mt-28 border-t border-line"
    >
      <div className="mx-auto max-w-6xl px-5 py-16">
        <p className="data text-xs uppercase tracking-wider text-ink-3">
          {`Separate network · chain id ${BOT_CHAIN.chainId}`}
        </p>
        <h2
          id="botchain-h"
          className="mt-3 max-w-3xl font-[family-name:var(--font-brand)] font-bold"
          style={{ fontSize: 'var(--text-h2)' }}
        >
          A file hash on BOT Chain
        </h2>
        <p className="mt-3 max-w-2xl text-ink-2">
          BOT Chain is not operated here. Its site and its explorer are its own.
          These two open there.
        </p>
        <ul className="mt-6 flex list-none flex-col gap-3 p-0 sm:flex-row sm:flex-wrap">
          <li><ExactUrl href={BOT_CHAIN.site} /></li>
          <li><ExactUrl href={BOT_CHAIN.explorer} /></li>
        </ul>

        <p className="mt-8 max-w-3xl leading-relaxed text-ink-2">
          {`${BOT_CHAIN.name} is deployed on that network. The explorer reports the `
            + 'source as verified, which means the published source matches the '
            + 'bytecode. That match is not an audit.'}
        </p>
        <dl className="mt-6 grid gap-4 sm:grid-cols-3">
          <div className="min-w-0 rounded-xl border border-line bg-surface p-5">
            <dt className="text-xs uppercase tracking-wider text-ink-3">Contract</dt>
            <dd>
              <p className="data mt-2 text-sm text-ink">{withBreaks(BOT_CHAIN.address)}</p>
              <p className="mt-2 text-xs text-ink-3">{BOT_CHAIN.name}</p>
              <RecordLink href={contractUrl} />
            </dd>
          </div>
          <div className="min-w-0 rounded-xl border border-line bg-surface p-5">
            <dt className="text-xs uppercase tracking-wider text-ink-3">
              Owner and first publisher
            </dt>
            <dd>
              <p className="data mt-2 text-sm text-ink">{withBreaks(BOT_CHAIN.ownerPublisher)}</p>
              <p className="mt-2 text-xs leading-relaxed text-ink-3">
                The deploy transaction set both roles to this one address.
              </p>
              <RecordLink href={ownerUrl} />
            </dd>
          </div>
          <div className="min-w-0 rounded-xl border border-line bg-surface p-5">
            <dt className="text-xs uppercase tracking-wider text-ink-3">Deploy transaction</dt>
            <dd>
              <p className="data mt-2 text-sm text-ink">{withBreaks(BOT_CHAIN.deployTx)}</p>
              <RecordLink href={txUrl} />
            </dd>
          </div>
        </dl>

        <div className="mt-8 max-w-3xl space-y-4 leading-relaxed text-ink-2">
          <p>
            An anchor stores a 32-byte hash, the publisher who submitted it, and
            a label the publisher chooses. The hash is a commitment to a
            file&rsquo;s bytes: hash the same file and you can see whether those
            exact bytes were submitted, and by whom. The contract does not keep
            the file. A matching hash is not proof that what the file says is
            true, and it is not a trading result.
          </p>
          <p>
            This is separate from the call hashes further up the page. Those are
            RUNECLAW&rsquo;s own record. This website does not submit a hash,
            and it does not sign or send a transaction.
          </p>
        </div>

        <div className="mt-6 max-w-3xl rounded-lg border border-warn/40 bg-warn/5 p-4" role="note">
          <p className="text-sm font-semibold text-warn">
            No file hash had been committed when this page was written
          </p>
          <p className="mt-1.5 text-sm leading-relaxed text-ink-2">
            {`On ${BOT_CHAIN.readOn} the contract log on the explorer contained the `
              + 'deployment events only. There was no Anchored event. This sentence '
              + 'is that reading, not a live counter. Anything anchored later is on '
              + 'the contract page, not here.'}
          </p>
        </div>
        <div className="mt-4 max-w-3xl rounded-lg border border-warn/40 bg-warn/5 p-4" role="note">
          <p className="text-sm font-semibold text-warn">
            A deployed contract is not a finished product flow
          </p>
          <p className="mt-1.5 text-sm leading-relaxed text-ink-2">
            A separate page for checking a file against the contract is not
            published, so this section does not link to one. Until that page
            exists, the contract on the explorer is the public record.
          </p>
        </div>
      </div>
    </section>
  )
}

function Home() {
  // Trade, then AI, then the one-line proof. Stats, the demo and the
  // capability grid stay — below that, so the long story is still on the
  // page and is no longer the first thing after the hero. BOT Chain is last.
  return (
    <>
      <Hero />
      <TradeStrip />
      <AiTeaser />
      <Proof />
      <Stats />
      <DemoFilm />
      <Capabilities />
      <BotChain />
    </>
  )
}

export const Route = createFileRoute('/')({ component: Home })
