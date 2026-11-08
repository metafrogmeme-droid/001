/**
 * The submissions index and its pages, as data.
 *
 * ONE READING. `submissions.json` is read here for the pages and by
 * `prerender.js` for the routes, the titles and the sitemap, so a page cannot
 * exist without an entry or an entry without a page.
 *
 * WHAT AN ENTRY MAY SAY. Every link was opened on `checked` and resolved; a
 * status is what the evidence shows, never what an entry hoped for:
 *
 *   confirmed       a public submission page was read
 *   owner-reported  the owner's own records say submitted; no receipt to link
 *   live            deployed and reachable; no submission claimed
 *   wip             being built; no submission claimed
 *
 * A PRIVATE entry carries its name and status and nothing else: no
 * description, no links, no addresses, until its owner approves what is
 * published. `site/test/submissions.test.js` holds both rules to the built
 * pages.
 *
 * Same site rules as everywhere else: no dollar figure, no price, nothing
 * from facts.ts MUST_NOT_CLAIM.
 */
import data from './submissions.json'

export type Status = 'confirmed' | 'owner-reported' | 'live' | 'wip'
export type LinkKind = 'submission' | 'demo' | 'repo' | 'explorer' | 'video' | 'social' | 'site'

export type SubmissionLink = {
  readonly kind: LinkKind
  readonly label: string
  readonly href: string
}

export type Submission = {
  readonly slug: string
  readonly name: string
  readonly status: Status
  readonly private?: boolean
  readonly event: string | null
  readonly summary: string | null
  readonly body: readonly string[]
  readonly links: readonly SubmissionLink[]
  readonly notShown: readonly string[]
}

export const CHECKED: string = data.checked

export const STATUS: Readonly<Record<Status, { label: string; meaning: string }>> =
  data.statuses

/** The order the index shows the groups in, strongest claim first. */
export const STATUS_ORDER: readonly Status[] = ['confirmed', 'owner-reported', 'live', 'wip']

export const SUBMISSIONS: readonly Submission[] = data.entries as readonly Submission[]

/** What a private entry says instead of a description. */
export const PRIVATE_NOTE =
  'The source is private, and a public description has not been approved yet. '
  + 'This page will say more once the owner approves what to publish.'

export const PRIVATE_NOT_SHOWN: readonly string[] = [
  'Description: awaiting the owner’s approval to publish.',
  'Repository: private, so not linked.',
  'Submission: none on file, so this page claims none.',
]

export const LINK_KIND_LABEL: Readonly<Record<LinkKind, string>> = {
  submission: 'Submission',
  demo: 'Demo',
  repo: 'Source',
  explorer: 'Explorer',
  video: 'Video',
  social: 'Social post',
  site: 'On this site',
}

export function bySlug(slug: string): Submission | undefined {
  return SUBMISSIONS.find((s) => s.slug === slug)
}

/** The host a reader is sent to, said beside every external link. */
export function hostOf(href: string): string | null {
  if (!/^https?:\/\//.test(href)) return null
  try {
    return new URL(href).host.replace(/^www\./, '')
  } catch {
    return null
  }
}
