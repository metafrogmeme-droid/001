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
 *
 * OWNER SIGN-OFF, 22 October 2026: the section "The daily proof, and the
 * chains it is written to", as it read that day. The owner approved it as the
 * operator; it is not a lawyer's review, and the paragraph above still
 * stands. The approval is of that text, so `site/test/privacy_truth.test.js`
 * pins the section's digest: an edit to it fails there until the owner
 * approves the new text. Later the same day the owner told us to make the
 * factual updates the open items need, and four facts changed: a call's
 * receipt now shows the chips put on it (the clause "and the chips you put
 * on it, if any"), reviews of settlements are seals in a day's root (a
 * review names a call by its seal and no one by name), a day can be
 * anchored on Solana, read back through public Solana RPC services, and a
 * day can be stamped in EAS on Arbitrum One, Scroll or Linea, read back
 * through those chains' RPC providers. The pinned digest is of the text
 * with all four. The anchor watch's Discord alerts, added the same
 * day, carry no personal data (a day, a chain and what is wrong), so this
 * page says nothing about them.
 */
import { createFileRoute } from '@tanstack/react-router'
import { Gap, H, L, P } from '../components/Prose'

const UPDATED = '22 October 2026'

function Privacy() {
  return (
    <article className="mx-auto max-w-3xl px-5 py-16">
      {/* A class, not a style attribute: www serves this article under a
          policy with no inline styles (app/routes/legal.js). */}
      <h1 className="font-[family-name:var(--font-brand)] text-[length:var(--text-h2)] font-bold">
        Privacy
      </h1>
      <p className="data mt-2 text-sm text-ink-3">Last updated {UPDATED}</p>

      <P>
        RUNECLAW is a <strong>web platform</strong> with accounts and a{' '}
        <strong>Telegram bot</strong>, which share data when you link them. The
        paper Arena also runs as an app inside the <strong>Pi Browser</strong>{' '}
        and inside <strong>Farcaster</strong>, signed in with that network&rsquo;s
        account and opening a RUNECLAW account of its own. This describes what
        each one collects, where it is kept, and what leaves our servers. What
        you agree to by using it is in the{' '}
        <a className="text-accent underline" href="/terms">terms of use</a>.
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
          Google, Discord or X. Accounts made by a sign-in that supplies no
          email (Telegram, X, a wallet, Pi or Farcaster) are given a synthesised
          placeholder address rather than a real one.</>,
        <><strong>Your Pi uid</strong>, if you sign in with Pi in the Pi
          Browser. Pi gives each app its own uid for a person, and it is the one
          thing we keep from that sign-in: your Pi username is not stored. The
          Pi app offers it as a name for the leaderboard, and it is kept only if
          you choose it there.</>,
        <><strong>Your Farcaster ID (fid)</strong>, if you sign in with
          Farcaster. It is the one thing we keep from that sign-in.</>,
        <><strong>A record of each Pi payment</strong>, a tip or Coach Plus, if
          you make one from the Pi app: the amount, Pi&rsquo;s payment id and
          blockchain transaction id, whether it completed, and when.</>,
        <><strong>How many questions you ask the Pi app&rsquo;s coach</strong>{' '}
          each day, to keep its daily allowance. The count only: your questions
          and its answers are not stored, except that a question the
          prompt-injection check flags leaves a short excerpt of itself in the
          operator&rsquo;s audit log.</>,
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
        <>The Pi Browser and Farcaster apps set no cookie and store nothing in
          your browser. Their sign-in is a token held in the open page&rsquo;s
          memory, gone when you close the app.</>,
      ]} />

      <H>What leaves our servers</H>
      <P>
        <strong>AI providers.</strong> When you chat with the agent or ask for
        analysis, your message is sent to a large-language-model provider. So is
        context the agent needs to answer usefully — which can include your open
        positions, your saved profile (your stated risk appetite and watchlist),
        and a one-line summary of which assets you have recently asked the agent
        about. In the Pi app, Ask the Claw sends your question with a summary of
        your practice positions, your recent practice trades and your Duel calls,
        read for that question and not kept. The previous policy said no user
        data reached these providers. That was not correct.
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
      <P>
        <strong>Pi Network.</strong> Signing in with Pi sends the access token
        Pi&rsquo;s own SDK gives the app to Pi&rsquo;s App Studio, which checks
        it and answers your uid. If you tip or buy Coach Plus, our server sends Pi&rsquo;s payment
        id, and later the transaction id, to the Pi Platform to approve and
        complete the payment, and reads Pi&rsquo;s record of it. A tip is a
        transfer on the Pi blockchain, which is public: neither we nor anyone
        else can delete it there. Pi handles all of this under its own privacy
        policy.
      </P>
      <P>
        <strong>Farcaster.</strong> Signing in with Farcaster sends the signed
        sign-in message to Farcaster&rsquo;s sign-in service, which checks the
        signature, under Farcaster&rsquo;s own privacy policy.
      </P>
      <P>
        <strong>Email.</strong> When the site emails you, the message goes out
        through Google Workspace from info@humanoid-traders.com, under
        Google&rsquo;s own privacy policy. It emails you for the welcome and
        verification link, a password reset, and a notice when your password,
        your two-factor setting, your plan or your account changes. It sends no
        marketing email. Accounts made by a sign-in that supplies no email
        (Telegram, X, a wallet, Pi or Farcaster) hold a placeholder address,
        not a real one, and are never emailed.
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

      <H>The daily proof, and the chains it is written to</H>
      <P>
        Every seal the site makes in a UTC day (among them the agent&rsquo;s calls, Arena
        practice trades, Daily Duel rounds, calls, settlements and reviews of
        settlements, Memeclaw launches) goes into one Merkle root for that day, shown on{' '}
        <code>/roots</code> and in its public feed. A seal is a hash and names
        no one. A Duel call&rsquo;s receipt, opened by its own seal, shows the
        call, its prices and outcome, the handle you chose to show, if any, and
        the chips you put on it, if any: play money that cannot be bought,
        sold or cashed out.
      </P>
      <L items={[
        <><strong>When your wallet anchors a day.</strong> You can write a
          day&rsquo;s root to a public chain from your own wallet: to Base (a
          transaction to your own address, or a stamp in the Ethereum
          Attestation Service), to Arbitrum One, Scroll or Linea (a stamp in
          the Ethereum Attestation Service there), to the DogeOS testnet, to Dogecoin from
          Dogecoin Core, or to Solana (a transaction carrying the root as a
          memo). Your wallet sends it; we never sign. Once the chain
          confirms it, we keep and show publicly which chain, the transaction id
          and the address that sent it. We do not record which account submitted
          it. The same facts are on the chain itself, where nobody, us included,
          can delete them; deleting your account does not remove them.</>,
        <><strong>Bitcoin.</strong> Each day&rsquo;s root is sent to public
          OpenTimestamps calendar servers, which put it into Bitcoin. What leaves
          our servers is a SHA-256 digest of one line naming the day and its
          root, and nothing about you.</>,
        <><strong>Checking an anchor.</strong> To check one, our servers ask
          public blockchain services about its transaction id: RPC providers for
          Base, Arbitrum One, Scroll, Linea and the DogeOS testnet, BlockCypher and SoChain for Dogecoin,
          public Solana RPC services for Solana, blockstream.info and
          mempool.space for Bitcoin, and, when the operator
          has configured it, QuickNode through a data service. They see a
          request from our server, not from you.</>,
      ]} />

      <H>Memeclaw, the practice and testnet launchpad</H>
      <P>
        Memeclaw (<code>/memeclaw</code>, and on its own domain when we run one,
        served by the same servers under this same policy) lets anyone launch
        and trade coins that have no value, in one of two modes. Neither needs
        an account.
      </P>
      <P>
        <strong>Practice mode</strong> needs no wallet. Practice coins and
        practice DOGE exist on this site only.
      </P>
      <L items={[
        <><strong>What your browser keeps:</strong> a random practice key, in
          its local storage. Clearing the browser&rsquo;s data loses it, and with
          it the practice player.</>,
        <><strong>What we keep:</strong> a hash of that key (never the key
          itself), a random player id, its practice DOGE, the practice coins it
          holds, how many coins it launched today and when it last topped up.
          The practice coins launched (name, symbol, description) and every
          practice trade are kept under the creator&rsquo;s or trader&rsquo;s
          random id.</>,
        <><strong>What is shown publicly:</strong> the practice coins, their
          names and descriptions, and their trades, each under a random id that
          names no person.</>,
        <><strong>Forgetting it:</strong> &ldquo;Forget my practice
          player&rdquo; on the page deletes the key&rsquo;s hash, the practice
          DOGE and the holdings. The coins and trades stay, under the random
          id.</>,
      ]} />
      <P>
        <strong>Testnet mode</strong> runs on the DogeOS <strong>testnet</strong>.
        Your wallet sends every transaction; we never sign one and never hold a
        coin or a key.
      </P>
      <L items={[
        <><strong>What we keep when you plan a launch:</strong> the wallet
          address you launch from, and the name, symbol and description you
          typed, so the launch can be matched to them when the chain confirms
          it. A plan that is never launched is deleted after a day.</>,
        <><strong>What we keep once the chain has it:</strong> the coin, its
          creator&rsquo;s address, the launch transaction, and every buy and sell
          on its curve, with the trading wallet&rsquo;s address. All of it is
          already public on the DogeOS testnet, and a launch&rsquo;s terms are
          sealed into the day&rsquo;s public proof, so they cannot be deleted
          from it.</>,
        <><strong>What is shown publicly:</strong> the coins, their names and
          descriptions, creator and trader addresses, and trades, on the
          Memeclaw page and its receipts.</>,
        <><strong>Your IP address</strong> limits how fast plans and trades can
          be asked for, as everywhere on the site.</>,
      ]} />

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
        the record of any Pi tips and Coach Plus payments, the count of your
        coach questions, and any exchange credentials on either side.
        Your sessions end immediately. A tip itself stays on the Pi blockchain,
        which no one can erase.
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
          sign in with Google, Telegram, a wallet signature, Pi or Farcaster.</>,
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
        or email info@humanoid-traders.com.
      </P>
    </article>
  )
}

export const Route = createFileRoute('/privacy')({ component: Privacy })
