import { createFileRoute } from '@tanstack/react-router'

// Placeholder until the content-truth audit lands. Deliberately says nothing
// about what is collected: a privacy policy assembled from guesses is worse
// than no page, and this one is replaced wholesale by the audited version.
function Privacy() {
  return (
    <section className="mx-auto max-w-3xl px-5 py-20">
      <h1 className="font-[family-name:var(--font-brand)] text-3xl font-bold">Privacy</h1>
      <p className="mt-4 text-ink-2">This policy is being rewritten to cover web accounts, two-factor authentication, wallet links and Telegram linking. Until it is, the previous policy remains authoritative.</p>
      <p className="mt-4"><a className="text-accent underline" href="/privacy.html">Read the current policy</a></p>
    </section>
  )
}

export const Route = createFileRoute('/privacy')({ component: Privacy })
