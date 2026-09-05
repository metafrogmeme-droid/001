/**
 * The public changelog. Entries live in ../changelog.ts — see the note there
 * on why it is curated rather than generated from git.
 */
import { createFileRoute } from '@tanstack/react-router'
import { CHANGELOG } from '../changelog'

function Changelog() {
  return (
    <article className="mx-auto max-w-3xl px-5 py-16">
      <h1
        className="font-[family-name:var(--font-brand)] font-bold leading-tight"
        style={{ fontSize: 'var(--text-h1)' }}
      >
        Changelog
      </h1>
      <p className="mt-4 leading-relaxed text-ink-2">
        What changed, when, and where to check it. Written by hand from the
        pull requests, so that every entry points at something a reader can
        open. The full history is in the repository.
      </p>
      <ol className="mt-10 space-y-10">
        {CHANGELOG.map((e) => (
          <li key={e.date + e.title} className="reveal border-l border-line pl-5">
            <time dateTime={e.date} className="data text-xs uppercase tracking-wider text-ink-3">
              {e.date}
            </time>
            <h2 className="mt-1 font-[family-name:var(--font-brand)] text-xl font-bold">
              {e.title}
            </h2>
            <p className="mt-3 leading-relaxed text-ink-2">{e.body}</p>
            <p className="mt-3 text-xs text-ink-3">
              {e.refs.map((r, i) => (
                <span key={r}>
                  {i > 0 ? ' · ' : ''}
                  <code className="data">{r}</code>
                </span>
              ))}
            </p>
          </li>
        ))}
      </ol>
    </article>
  )
}

export const Route = createFileRoute('/changelog')({ component: Changelog })
