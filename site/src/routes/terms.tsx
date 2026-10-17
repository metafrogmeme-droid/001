/**
 * Terms.
 *
 * WHY THIS EXISTS NOW. Pi's Developer Portal will not list an app without a
 * terms-of-service URL (2026-10-17), and RUNECLAW had none anywhere: not here,
 * not on the platform. A missing page is honest in its way; the risk was the
 * next step, a template pasted in to clear the form, promising things the
 * product does not do.
 *
 * WHAT THIS DOCUMENT WILL NOT DO. The rule privacy.tsx holds applies here: it
 * states what the product does and stops there. Where a sentence describes
 * the product (the paper balance, what a tip changes, how an account is
 * deleted), `site/test/terms_truth.test.js` checks it against the code, and a
 * change to the code that makes it false fails there.
 *
 * NOT LEGAL ADVICE, AND NOT LEGAL SIGN-OFF. The operator has not named a
 * governing law, a court or a registered entity, so none is named here. A
 * jurisdiction is not something code can decide, and inventing one would be
 * the most confident sentence on the page.
 */
import { createFileRoute } from '@tanstack/react-router'
import { H, L, P } from '../components/Prose'

const UPDATED = '17 October 2026'

function Terms() {
  return (
    <article className="mx-auto max-w-3xl px-5 py-16">
      <h1 className="font-[family-name:var(--font-brand)] text-[length:var(--text-h2)] font-bold">
        Terms of use
      </h1>
      <p className="data mt-2 text-sm text-ink-3">Last updated {UPDATED}</p>

      <P>
        These terms cover RUNECLAW, run by Humanoid Traders (&ldquo;we&rdquo;):
        the website at www.humanoid-traders.com, the Telegram bot, and the paper
        Arena as an app in the Pi Browser and in Farcaster. Using any of them
        means you accept these terms. What we collect, and what you can delete,
        is on the{' '}
        <a className="text-accent underline" href="/privacy">privacy page</a>.
      </P>

      <H>Not financial advice</H>
      <P>
        Signals, scores, confidence figures, charts and the agent&rsquo;s
        replies are produced by software from market data. They can be wrong,
        late or incomplete. None of them is investment advice or a
        recommendation to buy or sell anything. You make your own decisions and
        you carry their results.
      </P>

      <H>The Arena is paper trading</H>
      <L items={[
        <>Every Arena account starts with the same 10,000 vUSDT virtual balance
          and trades it at live market prices.</>,
        <>vUSDT is a number on our server, not a currency or a token. It cannot
          be bought, sold, withdrawn or exchanged for anything, and it has no
          cash value.</>,
        <>A place on a season board measures paper results. Paper results do
          not predict what the same trades would do with real money.</>,
      ]} />

      <H>Real trading, if you connect an exchange</H>
      <P>
        Where the product offers live trading, orders go to your own exchange
        account with your own API keys, and every order and its result is
        yours. Leveraged trading can lose your entire balance. The risk checks
        reduce some risks and remove none: software, exchanges and networks
        fail. Use exchange keys scoped to trading, never to withdrawal.
      </P>

      <H>Tips in the Pi app</H>
      <L items={[
        <>A tip is optional. It unlocks nothing, buys no service, and changes
          nothing about your account or the Arena.</>,
        <>The amounts are the ones the app offers, and you confirm the payment
          in Pi&rsquo;s own payment screen.</>,
        <>A completed tip is a transfer on the Pi blockchain. We cannot reverse
          it, and there is no refund.</>,
        <>Pi Network is not a party to these terms, and does not sponsor or
          endorse RUNECLAW.</>,
      ]} />

      <H>Your account</H>
      <L items={[
        <>You must be 18 or older to use RUNECLAW.</>,
        <>Keep your sign-in safe. What is done with your account is your
          responsibility.</>,
        <>You can delete your account yourself at any time; the privacy page
          says what deletion removes and what it cannot.</>,
      ]} />

      <H>Acceptable use</H>
      <P>Do not:</P>
      <L items={[
        <>attack, probe or overload the service, or get around its rate limits
          or access controls;</>,
        <>sign in as someone else, or use another person&rsquo;s account;</>,
        <>run several accounts, or exploit a fault, to move up a board;</>,
        <>use the service to break the law where you live, including the law on
          trading and on crypto-assets.</>,
      ]} />
      <P>
        We can suspend or close an account that breaks these terms.
      </P>

      <H>The service as it is</H>
      <P>
        RUNECLAW is provided as it is and as available. It can be down, slow or
        wrong, and features change or end. To the extent the law allows, we are
        not liable for trading losses, lost profits, or losses from relying on a
        signal, score or reply, or from the service being unavailable. Nothing
        here takes away a right that the law where you live gives you and does
        not let a contract remove.
      </P>

      <H>Other services</H>
      <P>
        Exchanges, Telegram, Pi Network, Farcaster, Google, Discord, X and the AI
        providers the agent uses run under their own terms. We do not control
        them.
      </P>

      <H>Changes</H>
      <P>
        When these terms change, the date at the top moves. Using RUNECLAW after
        a change means you accept the new terms.
      </P>
      <P>
        Questions:{' '}
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

export const Route = createFileRoute('/terms')({ component: Terms })
