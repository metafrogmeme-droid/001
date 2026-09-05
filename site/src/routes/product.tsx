/**
 * What ships today — the product page.
 *
 * The homepage is deliberately short, and `docs/ROADMAP.md` is deliberately
 * status-checked ("rows move to 🟢 only when the capability is reachable by a
 * user today"). This page is the public half of that discipline: every
 * paragraph names a live surface and the file that implements it, and the
 * last section says what is NOT here, because a product page that omits its
 * gaps is a roadmap wearing a different headline.
 *
 * Same rules as every page in this build: no figure without a source, no risk-
 * check count, no price, nothing from facts.ts MUST_NOT_CLAIM.
 */
import { createFileRoute } from '@tanstack/react-router'
import { DOCS, PlatformLink, TELEGRAM } from '../components/PlatformLink'

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

function Gap({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="mt-5 rounded-lg border border-warn/40 bg-warn/5 p-4">
      <p className="text-sm font-semibold text-warn">{title}</p>
      <div className="mt-1.5 text-sm leading-relaxed text-ink-2">{children}</div>
    </div>
  )
}

function Product() {
  return (
    <article className="mx-auto max-w-3xl px-5 py-16">
      <h1
        className="font-[family-name:var(--font-brand)] font-bold leading-tight"
        style={{ fontSize: 'var(--text-h1)' }}
      >
        What ships today
      </h1>
      <P>
        Everything on this page is reachable by a signed-in user right now, and
        each section names the code behind it. What is still ahead is at the
        bottom, said plainly.
      </P>

      <H>Talk to it</H>
      <P>
        The same assistant answers in Telegram and in the web drawer, with one
        shared memory of the conversation. When a question depends on your
        account — positions, risk state, what was rejected and why, the macro
        calendar, what is moving — it calls a read-only tool and answers from
        the reading rather than from an earlier turn. The tools it can reach
        are derived from the permission table the slash commands use, so it
        cannot reach anything a typed command could not.
      </P>
      <ul className="mt-5 space-y-3">
        <Src at="bot/nlp/chat_tools.py">The tool catalogue and the permission gate</Src>
        <Src at="bot/web/user_gateway.py · bot/skills/telegram_handler.py">
          Streamed replies on both surfaces; a fragment is provisional until the checked final text replaces it
        </Src>
        <Src at="bot/nlp/conversation_store.py">Shared memory, with a rolling note of what the cap pruned</Src>
        <Src at="bot/utils/i18n.py">Answers in thirty-four languages; the interface in fourteen</Src>
      </ul>

      <H>Trade — paper first</H>
      <P>
        Paper is the default. The Arena gives you a virtual account priced from
        the live feed, with liquidation mechanics, seasons and a board that
        publishes percentages and counts, never balances. Live trading is a
        choice: you bring your own exchange keys, they are validated read-only
        and encrypted at rest, and the operator switches live on per user.
      </P>
      <ul className="mt-5 space-y-3">
        <Src at="app/routes/arena.js">The Arena</Src>
        <Src at="bot/core/exchange_credentials.py">Per-user keys, encrypted at rest</Src>
        <Src at="bot/core/venues.py">The venue adapters</Src>
        <Src at="config/risk_manifest.yaml">The gate every entry passes — described on the risk page, without a count</Src>
      </ul>

      <H>Prove it</H>
      <P>
        Every engine call and every Arena trade is hashed at decision time, before
        the market moves. Daily Merkle roots make a whole day timestampable, and a
        per-call receipt can be re-derived in your own browser without trusting
        the operator. The proof page explains what that does and does not
        establish.
      </P>
      <ul className="mt-5 space-y-3">
        <Src at="app/lib/callseal.js · app/routes/roots.js">Seals and roots</Src>
        <Src at="/provable · /roots · /call on the platform">The verification contract, in public</Src>
      </ul>

      <H>Guardian</H>
      <P>
        Six surfaces that warn, simulate and prove — a sealed decision ledger, a
        liquidation stress lab, a market-crowding radar, a pre-sign prompt-
        injection scan, a dependency-aware unwind planner and a plain-words
        authority compiler. None of them moves funds.
      </P>
      <ul className="mt-5 space-y-3">
        <Src at="app/server.js">/flight · /stress · /sentinel · /firewall · /escape · /intent</Src>
      </ul>

      <H>Build on it</H>
      <P>
        Other agents can read the same things a user can: an MCP server on the
        bot, a second on the web with the Arena's paper trades as its only
        writes, public read-only endpoints, an ERC-8257 tool manifest and an
        ERC-8004 identity.
      </P>
      <ul className="mt-5 space-y-3">
        <Src at="bot/mcp/server.py · app/routes/mcp.js">The two MCP servers</Src>
        <Src at="app/routes/tool8257.js · app/routes/discovery.js">Manifest and identity</Src>
        <Src at="/developers on the platform">The developer page</Src>
      </ul>

      <H>What is not here yet</H>
      <P>
        The roadmap marks these as building, planned or gated, and this page
        will not say otherwise until they are reachable.
      </P>
      <Gap title="No token, and nothing that depends on one">
        No token exists and no sale has run. Tooling exists in a draft state and
        refuses mainnet. Rewards that would ride on it say so where they appear.
      </Gap>
      <Gap title="No deposit-taking product">
        Idle-margin yield is a read-only rate display. Nothing here takes custody
        of funds it does not need, and nothing manages a pooled balance.
      </Gap>
      <Gap title="No independent security review has been completed">
        The engine carries its own red team and an internal deep audit. Nobody
        independent has reviewed it, and the risk page says so too.
      </Gap>

      <div className="mt-12 flex flex-wrap items-center gap-3">
        <PlatformLink>Paper trade — free</PlatformLink>
        <a
          href={TELEGRAM}
          target="_blank"
          rel="noopener"
          className="inline-flex min-h-11 items-center justify-center rounded-md border border-line-2 px-5 py-2.5 text-sm font-semibold text-ink transition-colors hover:border-accent hover:text-accent-bright"
        >
          Talk to it on Telegram
        </a>
        <a href={DOCS} target="_blank" rel="noopener" className="text-sm text-ink-2 underline">
          Read the handbook
        </a>
      </div>
    </article>
  )
}

export const Route = createFileRoute('/product')({ component: Product })
