/**
 * The submissions index: every project with a page, grouped by what the
 * evidence shows about it. Entries live in ../submissions.json — see
 * ../submissions.ts for what an entry may say.
 */
import { Link, createFileRoute } from '@tanstack/react-router'
import { StatusBadge } from '../components/Submission'
import { CHECKED, STATUS, STATUS_ORDER, SUBMISSIONS } from '../submissions'

function Submissions() {
  return (
    <article className="mx-auto max-w-5xl px-5 py-16">
      <h1
        className="font-[family-name:var(--font-brand)] font-bold leading-tight"
        style={{ fontSize: 'var(--text-h1)' }}
      >
        Submissions and projects
      </h1>
      <p className="mt-4 max-w-3xl leading-relaxed text-ink-2">
        Hackathon entries, live products and work in progress from the people
        behind RUNECLAW. Each has its own page. A status says only what the
        evidence shows, every link was opened on{' '}
        <time dateTime={CHECKED}>{CHECKED}</time>, and a private project shows
        its name and nothing else until its owner approves what to publish.
      </p>

      <section aria-labelledby="statuses-h" className="mt-10 rounded-lg border border-line bg-surface p-5">
        <h2 id="statuses-h" className="text-sm font-semibold text-ink">What each status means</h2>
        <dl className="mt-3 grid gap-4 sm:grid-cols-2">
          {STATUS_ORDER.map((s) => (
            <div key={s}>
              <dt><StatusBadge status={s} /></dt>
              <dd className="mt-1.5 text-sm leading-relaxed text-ink-2">{STATUS[s].meaning}</dd>
            </div>
          ))}
        </dl>
      </section>

      {STATUS_ORDER.map((s) => {
        const group = SUBMISSIONS.filter((e) => e.status === s)
        if (!group.length) return null
        return (
          <section key={s} aria-labelledby={`group-${s}`} className="mt-12">
            <h2
              id={`group-${s}`}
              className="font-[family-name:var(--font-brand)] text-2xl font-bold"
            >
              {STATUS[s].label}
              <span className="data ml-2 text-base font-normal text-ink-3">({group.length})</span>
            </h2>
            <ul className="mt-5 grid gap-4 sm:grid-cols-2">
              {group.map((e) => (
                <li key={e.slug}>
                  <Link
                    to="/submissions/$slug"
                    params={{ slug: e.slug }}
                    className="block h-full rounded-lg border border-line bg-surface p-5 transition-colors hover:border-accent"
                  >
                    <span className="flex flex-wrap items-center gap-2">
                      <span className="font-[family-name:var(--font-brand)] text-lg font-bold text-ink">
                        {e.name}
                      </span>
                      {e.private ? (
                        <span className="data text-xs uppercase tracking-wider text-ink-3">Private</span>
                      ) : null}
                    </span>
                    {e.event ? <span className="mt-1 block text-sm text-ink-3">{e.event}</span> : null}
                    <span className="mt-2 block text-sm leading-relaxed text-ink-2">
                      {e.summary ?? 'Details are not published yet.'}
                    </span>
                  </Link>
                </li>
              ))}
            </ul>
          </section>
        )
      })}

      <p className="mt-12 text-sm text-ink-3">
        The season 1 Bitget entry is preserved as it was submitted, in the{' '}
        <a href="/archive/hackathon" className="underline">hackathon archive</a>.
      </p>
    </article>
  )
}

export const Route = createFileRoute('/submissions/')({ component: Submissions })
