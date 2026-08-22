/**
 * The gate that fails closed.
 *
 * NO CHECK COUNT APPEARS ON THIS PAGE, AND THAT IS ENFORCED ELSEWHERE.
 * `tests/test_no_hardcoded_risk_check_count.py` bans a number in front of the
 * word "check" on thirteen surfaces, because `_TOTAL_RISK_CHECKS = 23` was
 * maintained by hand against a file that changes, drifted DOWNWARD while the
 * engine grew to emit 36 labels, and was asserted on eleven-plus surfaces
 * including two i18n keys across fourteen languages. A marketing page is
 * exactly the kind of surface that would become the fourteenth.
 *
 * The real number is per-trade, varies with what applies to that trade, and is
 * already reported where it is measured — `checks_passed` on the decision
 * record. A headline figure could only ever be a second, less accurate copy.
 *
 * So this page describes the PROPERTY, which does not drift: a check that
 * cannot be evaluated rejects the trade. That is worth more than a count
 * anyway, and it is the one thing about a risk gate a reader cannot verify by
 * watching it work — a gate that silently passes on error looks identical to
 * one that does not, right up until it does not.
 */
import { createFileRoute } from '@tanstack/react-router'

function P({ children, className = '' }: { children: React.ReactNode; className?: string }) {
  return <p className={`mt-4 leading-relaxed text-ink-2 ${className}`}>{children}</p>
}

function H({ children }: { children: React.ReactNode }) {
  return (
    <h2 className="mt-12 font-[family-name:var(--font-brand)] text-2xl font-bold">
      {children}
    </h2>
  )
}

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

function Limit({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="mt-5 rounded-lg border border-warn/40 bg-warn/5 p-4">
      <p className="text-sm font-semibold text-warn">{title}</p>
      <div className="mt-1.5 text-sm leading-relaxed text-ink-2">{children}</div>
    </div>
  )
}

function Risk() {
  return (
    <article className="mx-auto max-w-3xl px-5 py-16">
      <h1
        className="font-[family-name:var(--font-brand)] font-bold leading-tight"
        style={{ fontSize: 'var(--text-h1)' }}
      >
        The gate that fails closed
      </h1>
      <P>
        Every entry passes a pre-trade risk gate. The property worth knowing is
        not how many things it checks — it is what happens when one of them
        cannot be answered.
      </P>

      <H>An unanswerable check is a rejection</H>
      <P>
        Each check runs inside its own error boundary, and an exception does not
        skip that check: it records a failure, and any failure rejects the
        trade. There is no path where a check that could not be evaluated is
        treated as a check that passed.
      </P>
      <div className="data mt-5 rounded-lg border border-line bg-surface p-5 text-sm leading-relaxed text-ink-2">
        <span className="text-accent"># the contract, verbatim</span>
        <br />
        This is the fail-closed contract: if ANY check cannot be evaluated,
        <br />
        the trade is REJECTED. No silent pass-through on errors.
      </div>
      <ul className="mt-4 space-y-2.5">
        <Src at="bot/risk/risk_engine.py">
          That sentence is the module&rsquo;s own, and the structure under it
          matches: every check sits in a <code>try</code> whose{' '}
          <code>except</code> appends to the failed list rather than the passed
          one.
        </Src>
        <Src at="bot/risk/risk_engine.py">
          The volatility guard is explicit about it — ATR is required and must
          be above zero, so an unreadable volatility reading rejects instead of
          defaulting to calm.
        </Src>
      </ul>
      <P>
        That is the opposite of the usual failure. A gate that swallows its own
        errors and lets the trade through looks identical to a working one from
        the outside, on every day that nothing goes wrong.
      </P>

      <H>Two breakers, and they are not the same breaker</H>
      <P>
        One halts on trading outcomes — prior losses, drawdown, a losing streak.
        The other halts on <em>infrastructure</em>: if warnings start firing too
        frequently, the engine stops trading even though nothing about the
        market has changed.
      </P>
      <ul className="mt-4 space-y-2.5">
        <Src at="bot/risk/risk_engine.py">
          They are separate fields and separate rejections, because a clear
          loss-breaker was once read as &ldquo;trading is fine&rdquo; while the
          warning-rate breaker was rejecting every live trade.
        </Src>
        <Src at="api_bridge.py">
          <code>/health</code> reports whether trading is blocked and by what —
          and reports <code>trading_gate_unknown</code> rather than an empty
          reason, so &ldquo;nothing is blocking&rdquo; cannot be confused with
          &ldquo;nobody could look&rdquo;.
        </Src>
      </ul>

      <H>Why there is no number on this page</H>
      <P>
        You will not find &ldquo;N pre-trade checks&rdquo; here, and the absence
        is deliberate. That figure used to be a constant maintained by hand
        against a file that changes. It drifted — downward — and ended up
        asserted across a dozen surfaces at three different values, each one
        looking like a specific and confident measurement.
      </P>
      <P>
        What runs is per-trade and depends on what applies to that trade. The
        engine already reports it where it is actually measured, on the decision
        record, and a headline figure could only ever be a second, less accurate
        copy of that.
      </P>

      <H>What a gate is not</H>
      <Limit title="It rejects trades. It does not predict them.">
        Passing the gate means no rule was violated and nothing was
        unreadable. It is not a forecast, not a confidence score, and not a
        claim that the trade will work.
      </Limit>
      <Limit title="Fail-closed costs opportunities, on purpose.">
        A data feed hiccup rejects entries that might have been fine. That is
        the trade being made: an entry not taken is recoverable, and an entry
        taken on a number nobody could read is not always.
      </Limit>
      <Limit title="Nobody independent has reviewed this.">
        No security audit of this codebase exists. The mechanisms above are
        real and readable; calling them assurance would be the kind of claim
        this project is organised around not making.
      </Limit>

      <P className="mt-12">
        The implementation is{' '}
        <code className="data text-xs">bot/risk/risk_engine.py</code>. It is long,
        and the contract quoted above sits directly over the checks it governs.
      </P>
    </article>
  )
}

export const Route = createFileRoute('/risk')({
  component: Risk,
  head: () => ({
    meta: [
      { title: 'The gate that fails closed — RUNECLAW' },
      {
        name: 'description',
        content:
          'Every entry passes a pre-trade risk gate. A check that cannot be '
          + 'evaluated rejects the trade rather than passing it — and there is '
          + 'deliberately no headline count on this page.',
      },
    ],
  }),
})
