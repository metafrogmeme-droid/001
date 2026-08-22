/**
 * How the proof works — the site's central claim, in full.
 *
 * The homepage says "every call is hashed before the market moves, so the
 * record can be checked by someone who does not trust the operator." That is
 * the whole pitch, and until now the only place to read what it means was the
 * platform, behind a link. A claim a visitor cannot check is a slogan.
 *
 * EVERY SENTENCE HERE IS READ OUT OF THE CODE, and the file:line is on the
 * page rather than in a comment, because a reader who does not trust the
 * operator is exactly the reader this page is for. The construction below is
 * `app/lib/sealroot.js`'s own docstring restated for a human — same steps, same
 * order, same edge cases.
 *
 * WHAT IS DELIBERATELY NOT HERE:
 *
 *   - No count of how many calls are sealed. That is a live figure and this is
 *     a static build; `facts.ts` exists because a number frozen into markup is
 *     how `$72,669` sat on a page as a current price for three months.
 *   - No claim that anchoring has happened for any particular day. A day may
 *     be unanchored, `roots.html` renders that plainly, and so does this.
 *   - No claim about an audit. `MUST_NOT_CLAIM` forbids it and no audit exists.
 *
 * The three limits at the bottom are the reason this page can be believed. A
 * proof page that lists only what the scheme guarantees is marketing; the
 * useful half is what it does not.
 */
import { createFileRoute } from '@tanstack/react-router'

function P({ children }: { children: React.ReactNode }) {
  return <p className="mt-4 leading-relaxed text-ink-2">{children}</p>
}

function H({ children }: { children: React.ReactNode }) {
  return (
    <h2 className="mt-12 font-[family-name:var(--font-brand)] text-2xl font-bold">
      {children}
    </h2>
  )
}

/** A claim with the file it was read from, shown to the reader. */
function Src({ children, at }: { children: React.ReactNode; at: string }) {
  return (
    <li className="flex gap-3 leading-relaxed text-ink-2">
      <span aria-hidden="true" className="mt-2 size-1.5 shrink-0 rounded-full bg-accent" />
      <span>
        {children}{' '}
        <code className="data whitespace-nowrap text-xs text-ink-3">{at}</code>
      </span>
    </li>
  )
}

/** Sections where the honest answer is a limit rather than a feature. */
function Limit({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="mt-5 rounded-lg border border-warn/40 bg-warn/5 p-4">
      <p className="text-sm font-semibold text-warn">{title}</p>
      <div className="mt-1.5 text-sm leading-relaxed text-ink-2">{children}</div>
    </div>
  )
}

function Proof() {
  return (
    <article className="mx-auto max-w-3xl px-5 py-16">
      <h1
        className="font-[family-name:var(--font-brand)] font-bold leading-tight"
        style={{ fontSize: 'var(--text-h1)' }}
      >
        How the proof works
      </h1>
      <P>
        The point of sealing a call is that you do not have to take the
        operator&rsquo;s word for the record. Everything below is what the code
        does, with the file it does it in — so you can read the implementation
        rather than this description of it.
      </P>

      <H>One call, one hash</H>
      <P>
        When the engine commits to a call, it writes a SHA-256 over that
        call&rsquo;s contents — the seal. It is minted at decision time, stored
        with the row, and never recomputed. A seal is 64 hex characters and
        nothing else; it reveals no prices and no positions.
      </P>
      <ul className="mt-4 space-y-2.5">
        <Src at="app/lib/sealroot.js">
          The hash is plain SHA-256 over UTF-8, with no salt and no secret, so
          anybody holding the same inputs computes the same seal.
        </Src>
        <Src at="app/routes/frame.js">
          The public card built from a sealed record draws symbol and direction
          only — never an entry, a stop, or an amount.
        </Src>
      </ul>

      <H>One day, one root</H>
      <P>
        Every seal minted on a single UTC day is folded into one Merkle root.
        Publishing that one hash commits to every call sealed that day at once:
        any single receipt can later prove it was included, and no call can be
        slipped into a day that has already been published without changing the
        root.
      </P>
      <P>
        The construction is fixed, and it is the whole contract — a verifier
        that follows these four rules will reproduce the root or prove it wrong:
      </P>
      <ol className="data mt-4 space-y-2 rounded-lg border border-line bg-surface p-5 text-sm text-ink-2">
        <li>
          <span className="text-accent">leaves</span> = the day&rsquo;s seals,
          deduped, sorted ascending
        </li>
        <li>
          <span className="text-accent">parent</span> ={' '}
          <code>sha256(utf8(leftHex + rightHex))</code> — the hex{' '}
          <em>strings</em> concatenated, not the bytes
        </li>
        <li>
          <span className="text-accent">odd node</span> — an unpaired last node
          is promoted unchanged
        </li>
        <li>
          <span className="text-accent">single leaf</span> — the root of a
          one-seal day is that seal
        </li>
      </ol>
      <ul className="mt-4 space-y-2.5">
        <Src at="app/lib/sealroot.js">
          The four rules above are that file&rsquo;s docstring, and the verify
          page mirrors the same construction.
        </Src>
        <Src at="app/lib/seal_roots.js">
          A root is computed once for a completed day and then stored
          immutably; recomputing it later yields the same value because a
          finished day&rsquo;s leaf set cannot grow.
        </Src>
      </ul>

      <H>Two things the day-root refuses to do</H>
      <P>
        Both of these are cases where the easy behaviour would produce a
        confident answer that is not true.
      </P>
      <ul className="mt-4 space-y-2.5">
        <Src at="app/lib/seal_roots.js">
          <strong className="text-ink">Today has no root.</strong> The day is
          still open, so committing to it early would be a claim about calls
          that have not happened.
        </Src>
        <Src at="app/lib/seal_roots.js">
          <strong className="text-ink">An empty day is omitted, not
          invented.</strong> A day with nothing sealed produces no row, rather
          than a root over zero leaves.
        </Src>
      </ul>

      <H>Checking one call yourself</H>
      <P>
        A receipt carries a membership proof: a short list of sibling hashes,
        each marked with the side it sits on. Fold your seal with them in order
        and you either arrive at the published root or you do not. The list
        grows with the logarithm of the day&rsquo;s volume, so checking a single
        call stays cheap no matter how busy the day was.
      </P>
      <P>
        The verification runs in your own browser on the platform&rsquo;s
        receipt page, against the root you can read separately. Nothing in that
        loop requires trusting the server that served it.
      </P>
      <div className="mt-8 flex flex-wrap gap-3">
        <a
          data-platform-path="/proof"
          hidden
          className="inline-flex min-h-11 items-center justify-center rounded-md bg-accent px-5 py-2.5 text-sm font-semibold text-bg transition-colors hover:bg-accent-bright"
        >
          Verify a call
        </a>
        <a
          data-platform-path="/roots"
          hidden
          className="inline-flex min-h-11 items-center justify-center rounded-md border border-line-2 px-5 py-2.5 text-sm font-semibold text-ink transition-colors hover:border-accent hover:text-accent-bright"
        >
          Read the daily roots
        </a>
      </div>

      <H>Anchoring, and what it adds</H>
      <P>
        A published root can also be written to Base as transaction calldata.
        That does not make the record more true — the Merkle math is the same
        either way — it makes the <em>timestamp</em> independent: the chain
        says when the commitment existed, and the operator cannot move it
        afterwards.
      </P>
      <Limit title="Not every day is anchored, and the pages say which.">
        An unanchored day renders as unanchored rather than implying a chain
        record that is not there. Where a day <em>is</em> anchored, the
        transaction is linked and the exact payload is printed, so you can
        compare the calldata yourself instead of trusting the label.
      </Limit>

      <H>What this does not prove</H>
      <P>
        A verification scheme is only worth as much as its stated limits, so
        here are the three that matter.
      </P>
      <Limit title="It proves commitment, not profitability.">
        A seal shows a call existed before the market moved. It says nothing
        about whether the call was good, and a record of sealed losses is
        exactly as provable as a record of sealed wins.
      </Limit>
      <Limit title="It covers what is sealed, not everything that happens.">
        The root folds the seals minted that day. A surface that is not sealed
        on a given deployment is simply not in it — the code skips such a
        surface rather than pretending its rows were covered.
      </Limit>
      <Limit title="It is not an audit.">
        Nobody independent has reviewed this codebase. Anchoring, hashing and
        public verification are mechanisms, not assurances, and calling them a
        security audit would be the kind of claim this project exists to avoid
        making.
      </Limit>

      <P className="mt-12">
        The implementation is <code className="data text-xs">app/lib/sealroot.js</code>{' '}
        and <code className="data text-xs">app/lib/seal_roots.js</code>. Both are
        short, and reading them is the only way to be sure this page is
        accurate.
      </P>
    </article>
  )
}

export const Route = createFileRoute('/proof')({
  component: Proof,
  head: () => ({
    meta: [
      { title: 'How the proof works — RUNECLAW' },
      {
        name: 'description',
        content:
          'Every call is hashed before the market moves and folded into a daily '
          + 'Merkle root. The construction, the two cases it refuses to answer, '
          + 'and the three things it does not prove.',
      },
    ],
  }),
})
