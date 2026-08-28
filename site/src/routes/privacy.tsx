/**
 * Privacy.
 *
 * WHAT THIS REPLACES. `website/privacy.html`, dated 31 May 2026, described a
 * self-hosted single-operator Telegram bot. Every one of its negative claims is
 * now false, and each was checked against the code rather than taken on trust:
 *
 *   "We do not collect your real name, email address, or phone number."
 *     app/auth.js:542 — INSERT INTO users (email, password_hash)
 *   "We do not use cookies or web tracking of any kind."
 *     app/lib/session_cookie.js — sets rc_auth, rc_session, rc_jwt
 *   "We do not store exchange API credentials on any external server — all
 *    keys remain in the operator's local environment variables."
 *     bot/core/exchange_credentials.py — a per-user Fernet vault, server-side
 *   "No Telegram user data is included in LLM requests."
 *     bot/skills/telegram_handler.py:1982 — appends the saved agent profile
 *     (risk preference, watchlist) to the system prompt
 *   "No data is transmitted to Humanoid Traders or any central server."
 *     the entire web platform
 *
 * None of that was a lie when written. The document simply stopped describing
 * the product, and a privacy policy that describes a different product is worse
 * than none: it is a specific, confident, checkable assurance about the wrong
 * system. The LLM line got *more* wrong this week, from a change in this repo
 * that taught the Telegram path to read a user's saved profile.
 *
 * WHAT THIS DOCUMENT WILL NOT DO. It states what the code does and stops there.
 * Where a protection does not exist — a retention period — it says so plainly
 * instead of describing an intention in the present tense. "We retain data only
 * as long as necessary" is the sentence every policy reaches for when nothing
 * purges anything, and it is how a document like this becomes untrue again.
 *
 * SELF-SERVICE DELETION WAS ON THAT LIST AND IS NOT ANY MORE. This page said,
 * correctly, that no endpoint in the product deleted an account. Writing the
 * absence down is what made it visible, and `DELETE /api/auth/account` now
 * exists — bot first, then the web database, aborting rather than half-erasing.
 * The test that pinned the old sentence failed on the commit that built it,
 * which is the ratchet doing its job in the direction that matters: the page
 * cannot fall behind the product without something going red.
 *
 * NOT LEGAL ADVICE, AND NOT LEGAL SIGN-OFF. This is an accurate description of
 * observed behaviour, written by reading the source. Jurisdictional obligations
 * — GDPR lawful basis, CCPA disclosures, data-subject rights, a controller of
 * record — are not decided by code and are not settled here.
 */
import { createFileRoute } from '@tanstack/react-router'

const UPDATED = '22 August 2026'

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

function L({ items }: { items: React.ReactNode[] }) {
  return (
    <ul className="mt-4 space-y-2.5">
      {items.map((it, i) => (
        <li key={i} className="flex gap-3 leading-relaxed text-ink-2">
          <span aria-hidden="true" className="mt-2 size-1.5 shrink-0 rounded-full bg-accent" />
          <span>{it}</span>
        </li>
      ))}
    </ul>
  )
}

/** Sections where the honest answer is "this does not exist yet". */
function Gap({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="mt-5 rounded-lg border border-warn/40 bg-warn/5 p-4">
      <p className="text-sm font-semibold text-warn">{title}</p>
      <div className="mt-1.5 text-sm leading-relaxed text-ink-2">{children}</div>
    </div>
  )
}

function Privacy() {
  return (
    <article className="mx-auto max-w-3xl px-5 py-16">
      <h1
        className="font-[family-name:var(--font-brand)] font-bold"
        style={{ fontSize: 'var(--text-h2)' }}
      >
        Privacy
      </h1>
      <p className="data mt-2 text-sm text-ink-3">Last updated {UPDATED}</p>

      <P>
        RUNECLAW is two connected surfaces: a <strong>web platform</strong> with
        accounts, and a <strong>Telegram bot</strong>. They share data when you
        link them. This describes what each one collects, where it is kept, and
        what leaves our servers.
      </P>
      <P>
        The previous version of this page described a single-operator,
        self-hosted bot and had stopped being true of the product. Everything
        below was checked against the source rather than carried over.
      </P>

      <H>What we collect</H>
      <P>Depending on how you sign in:</P>
      <L items={[
        <><strong>Email and password</strong> — for email sign-up. The password
          is stored only as a bcrypt hash (cost 12); we never hold the password
          itself, and the confirmation field never leaves your browser.</>,
        <><strong>A provider ID and avatar URL</strong> — if you sign in with
          Google, Discord or X. Telegram-, X- and wallet-only accounts are given
          a synthesised placeholder address rather than a real one.</>,
        <><strong>Your Telegram ID</strong>, and username where Telegram supplies
          it, once you link the bot.</>,
        <><strong>A wallet address</strong>, if you connect one. The signature
          proving you own it is verified in memory and never stored.</>,
        <><strong>Two-factor secrets and backup codes</strong>, if you enable
          2FA.</>,
        <><strong>Your referral code</strong>, and who referred you.</>,
        <><strong>Preferences you save</strong> — risk appetite, watchlist,
          interface defaults.</>,
        <><strong>Which assets you ask the agent about</strong> — not the words
          you typed, but the ticker the bot itself resolved when it ran a tool
          for you, kept as a count per symbol over a rolling window of your
          twelve most recent. It is how the agent remembers you between
          conversations. Deleting your account deletes it.</>,
        <><strong>Your trading activity on the platform</strong> — paper and
          live orders, decisions, and the audit record attached to them.</>,
        <><strong>Your IP address, briefly</strong> — held in server memory for
          about fifteen minutes to rate-limit sign-ups and sign-ins. It is not
          written to any database table by the application.</>,
      ]} />

      <H>Cookies</H>
      <P>
        We set cookies, and the previous policy was wrong to say otherwise. They
        are functional only — there are no advertising, analytics or
        cross-site tracking cookies, and we do not sell or share data with
        third parties for marketing.
      </P>
      <L items={[
        <><code className="data">rc_auth</code>,{' '}
          <code className="data">rc_session</code> and{' '}
          <code className="data">rc_jwt</code> — your sign-in session. The
          session token is HttpOnly, so page scripts cannot read it.</>,
        <>Your browser&rsquo;s own <code className="data">localStorage</code> may
          hold a referral code from a link you followed, so it still applies if
          you sign up on a later visit.</>,
      ]} />

      <H>What leaves our servers</H>
      <P>
        <strong>AI providers.</strong> When you chat with the agent or ask for
        analysis, your message is sent to a large-language-model provider. So is
        context the agent needs to answer usefully — which can include your open
        positions, your saved profile (your stated risk appetite and watchlist),
        and a one-line summary of which assets you have recently asked the agent
        about. The previous policy said no user data reached these providers.
        That was not correct.
      </P>
      <P>
        Which provider handles a given request is set by the operator&rsquo;s
        configuration and can change; possible providers include OpenAI,
        Anthropic, Google and Alibaba/DashScope, and one configuration routes
        through a third-party relay rather than the vendor directly. We do not
        control, and do not here promise, what a provider does with data on its
        own side.
      </P>
      <P>
        <strong>Exchanges.</strong> Market data requests carry no personal
        information. Orders placed on your behalf are made with your own API
        credentials and are attributable to you at the exchange.
      </P>
      <P>
        <strong>Telegram, Google, Discord and X.</strong> Sign-in and messaging
        pass through their systems under their own privacy policies.
      </P>

      <H>Exchange API keys</H>
      <P>
        This is the section the old policy got most wrong, so it is worth being
        exact. Keys are <strong>not</strong> kept only in an operator
        environment variable, and they are <strong>not</strong> held solely on
        your device.
      </P>
      <L items={[
        <>Keys you submit are encrypted and stored on our servers, under a
          master key the operator holds.</>,
        <>The encryption is reversible by design — the bot must decrypt a key to
          place your order. This is not zero-knowledge, and nobody should
          describe it that way.</>,
        <>No interface ever returns a stored key. Status views show a
          non-reversible fingerprint or a yes/no.</>,
        <>You can remove your keys at any time from either surface. Removal
          rewrites the encrypted file rather than leaving the old ciphertext
          behind.</>,
        <>If you send keys to the bot in a Telegram message, that message is
          deleted on a best-effort basis. Telegram may still retain it. Prefer
          the website, and prefer exchange keys scoped to trading only, never
          withdrawal.</>,
      ]} />

      <H>Wallets</H>
      <P>
        Connecting a wallet proves you control an address and nothing more. The
        message you sign says so in its own text: it authorises no transaction
        and costs no gas. There is no code path in RUNECLAW that can sign a
        transaction with a wallet you link — every wallet route accepts an
        address and a signature and only ever verifies them.
      </P>

      <H>Keeping and deleting your data</H>
      <P>
        You can delete your account yourself. It asks for your password, your
        authenticator code if you have one enrolled, and the word DELETE typed
        out, because it cannot be undone.
      </P>
      <P>
        Deletion clears the bot first and the website second, and if the bot
        does not confirm, nothing is deleted anywhere and you are told so. That
        order is deliberate: your exchange API keys live in the bot, and the
        failure worth preventing is a website that reports your account gone
        while the keys that move money are still held.
      </P>
      <P>
        It removes your trades, positions, snapshots, alerts, watchlist,
        strategies, profile, diary, notification subscriptions, wallet links,
        and any exchange credentials on either side. Your sessions end
        immediately.
      </P>
      <Gap title="One row survives, with nothing in it that names you.">
        Your account row is kept as an empty shell — every identifying field
        cleared, the address replaced with a synthetic one. It stays because
        other people&rsquo;s referral history points at it, and deleting it
        would take their standing down with yours. What remains is the fact
        that an id once existed. We would rather write that here than describe
        the deletion as total.
      </Gap>
      <Gap title="There is no automatic retention limit.">
        Nothing currently expires or purges account records, trade history or
        stored chat context on a schedule. Chat context is bounded by volume
        rather than by age. We are not going to write &ldquo;we keep data only
        as long as necessary&rdquo; over a system that keeps it indefinitely.
      </Gap>
      <P>What you <em>can</em> do today, without asking anyone:</P>
      <L items={[
        <>Remove your exchange API keys, from the website or the bot.</>,
        <>Unlink a wallet, or unlink Telegram.</>,
        <>Clear your saved profile — clearing it deletes the stored record
          rather than blanking it, and the change reaches the bot too.</>,
        <>Sign out everywhere. Logging out, or changing your password, ends
          every session on every device.</>,
        <>Export your closed trades as CSV for tax purposes.</>,
        <>Delete the account outright, as above.</>,
      ]} />

      <H>Security</H>
      <L items={[
        <>Passwords are bcrypt-hashed and never stored in plaintext.</>,
        <>Password-reset and email-verification links are stored only as a
          SHA-256 hash, so a database leak cannot be replayed against them.
          Reset links expire in 30 minutes, verification links in 24 hours.</>,
        <>Sign-in is rate-limited per IP and per email, and password reset never
          reveals whether an address has an account.</>,
        <>Two-factor authentication is optional, standard authenticator-app
          TOTP, compared in constant time. It is <em>not</em> prompted when you
          sign in with Google, Telegram or a wallet signature.</>,
        <>The application refuses to start if its session signing secret is
          missing or too short.</>,
      ]} />

      <H>Children</H>
      <P>
        RUNECLAW is not for anyone under 18. We do not currently verify age at
        sign-up, so this is a condition of use rather than something the product
        enforces.
      </P>

      <H>Changes</H>
      <P>
        The failure this page replaces was not a wrong sentence; it was a
        document that stopped tracking the product. When what we collect
        changes, this changes with it, and the date at the top moves.
      </P>
      <P>
        Questions, or a deletion request:{' '}
        <a
          className="text-accent underline"
          href="https://t.me/HTRUNECLAW_bot"
          target="_blank"
          rel="noopener"
        >
          message the bot
        </a>{' '}
        or contact Humanoid Traders.
      </P>
    </article>
  )
}

export const Route = createFileRoute('/privacy')({ component: Privacy })
