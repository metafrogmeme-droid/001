/**
 * One project's page. Its words come from ../submissions.json; a private
 * entry renders its name and status and the fixed private note, never a
 * description or a link (see ../submissions.ts).
 */
import { Link, createFileRoute, notFound } from '@tanstack/react-router'
import { EvidenceLink, NotShown, StatusBadge } from '../components/Submission'
import { CHECKED, PRIVATE_NOTE, PRIVATE_NOT_SHOWN, STATUS, bySlug } from '../submissions'

function SubmissionPage() {
  const entry = Route.useLoaderData()
  const isPrivate = entry.private === true
  return (
    <article className="mx-auto max-w-3xl px-5 py-16">
      <nav aria-label="Breadcrumb" className="text-sm text-ink-3">
        <ol className="flex flex-wrap gap-2">
          <li>
            <Link to="/submissions" className="underline hover:text-ink">Submissions</Link>
          </li>
          <li aria-hidden="true">/</li>
          <li aria-current="page" className="text-ink-2">{entry.name}</li>
        </ol>
      </nav>

      <h1
        className="mt-6 font-[family-name:var(--font-brand)] font-bold leading-tight"
        style={{ fontSize: 'var(--text-h1)' }}
      >
        {entry.name}
      </h1>
      <div className="mt-4 flex flex-wrap items-center gap-3">
        <StatusBadge status={entry.status} />
        {isPrivate ? (
          <span className="data text-xs uppercase tracking-wider text-ink-3">Private</span>
        ) : null}
      </div>
      <p className="mt-3 text-sm text-ink-3">{STATUS[entry.status].meaning}</p>
      {entry.event ? (
        <p className="mt-6 text-ink-2">
          <span className="text-ink-3">Event: </span>{entry.event}
        </p>
      ) : null}

      {isPrivate ? (
        <p className="mt-6 leading-relaxed text-ink-2">{PRIVATE_NOTE}</p>
      ) : (
        <>
          {entry.summary ? (
            <p className="mt-6 text-lg leading-relaxed text-ink">{entry.summary}</p>
          ) : null}
          {entry.body.map((p) => (
            <p key={p} className="mt-4 leading-relaxed text-ink-2">{p}</p>
          ))}
        </>
      )}

      {entry.links.length ? (
        <section aria-labelledby="links-h" className="mt-12">
          <h2
            id="links-h"
            className="font-[family-name:var(--font-brand)] text-2xl font-bold"
          >
            Links
          </h2>
          <p className="mt-2 text-sm text-ink-3">
            Each one was opened on <time dateTime={CHECKED}>{CHECKED}</time>.
          </p>
          <ul className="mt-5 space-y-3">
            {entry.links.map((l) => (
              <EvidenceLink key={l.href} link={l} />
            ))}
          </ul>
        </section>
      ) : null}

      <NotShown items={isPrivate ? PRIVATE_NOT_SHOWN : entry.notShown} />

      <p className="mt-12">
        <Link to="/submissions" className="text-sm text-ink-2 underline hover:text-ink">
          All submissions and projects
        </Link>
      </p>
    </article>
  )
}

export const Route = createFileRoute('/submissions/$slug')({
  loader: ({ params }) => {
    const entry = bySlug(params.slug)
    if (!entry) throw notFound()
    return entry
  },
  component: SubmissionPage,
})
