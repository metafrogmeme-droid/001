/**
 * The pieces the submissions index and its pages share.
 *
 * A status is said in WORDS, with colour only as a second channel: a reader
 * who cannot tell the accent from the warning colour still reads "Owner-
 * reported submission". The strongest claim (a confirmed submission) gets the
 * accent; an owner's report gets the warning colour, because it is a claim
 * nothing here can check; live and in-progress stay neutral.
 */
import type { Status, SubmissionLink } from '../submissions'
import { LINK_KIND_LABEL, STATUS, hostOf } from '../submissions'

const BADGE: Record<Status, string> = {
  confirmed: 'border-accent/50 bg-accent/10 text-accent-bright',
  'owner-reported': 'border-warn/50 bg-warn/10 text-warn',
  live: 'border-line-2 bg-surface-2 text-ink',
  wip: 'border-line-2 bg-surface text-ink-2',
}

export function StatusBadge({ status }: { status: Status }) {
  return (
    <span
      className={`data inline-flex items-center rounded-full border px-2.5 py-0.5 text-xs font-semibold uppercase tracking-wider ${BADGE[status]}`}
    >
      {STATUS[status].label}
    </span>
  )
}

/**
 * One link, with the host it leads to said beside it. An external link opens
 * in a new tab and says so to a screen reader; `noreferrer` because nothing
 * here needs to tell another site where its visitor came from.
 */
export function EvidenceLink({ link }: { link: SubmissionLink }) {
  const host = hostOf(link.href)
  const external = host !== null
  return (
    <li className="flex flex-col gap-1 rounded-lg border border-line bg-surface p-4 sm:flex-row sm:items-baseline sm:gap-4">
      <span className="data w-28 shrink-0 text-xs uppercase tracking-wider text-ink-3">
        {LINK_KIND_LABEL[link.kind]}
      </span>
      <span className="min-w-0">
        <a
          href={link.href}
          {...(external ? { target: '_blank', rel: 'noopener noreferrer' } : {})}
          className="break-words font-semibold text-ink underline decoration-line-2 underline-offset-4 hover:text-accent-bright hover:decoration-accent"
        >
          {link.label}
          {external ? <span className="sr-only"> (opens in a new tab)</span> : null}
        </a>
        {host ? <span className="data ml-2 text-xs text-ink-3">{host}</span> : null}
        {link.kind === 'video' ? (
          <span className="mt-1 block text-sm text-ink-3">
            Plays on YouTube. This site loads nothing from other servers, so the
            video is linked rather than embedded.
          </span>
        ) : null}
      </span>
    </li>
  )
}

export function NotShown({ items }: { items: readonly string[] }) {
  if (!items.length) return null
  return (
    <section aria-labelledby="not-shown-h" className="mt-10 rounded-lg border border-warn/40 bg-warn/5 p-5">
      <h2 id="not-shown-h" className="text-sm font-semibold text-warn">
        Not shown here, and why
      </h2>
      <ul className="mt-2 space-y-1.5 text-sm leading-relaxed text-ink-2">
        {items.map((t) => (
          <li key={t}>{t}</li>
        ))}
      </ul>
    </section>
  )
}
