/**
 * The public changelog, as data.
 *
 * Curated, not generated: a page rebuilt from `git log` on every commit would
 * make the committed site drift from the built site on every commit, and the
 * CI gate that keeps those equal is worth more than an automatic list. So an
 * entry is written by hand, dated, and points at the pull requests or commits
 * it summarises — a reader can go and check, which is the only kind of
 * changelog worth publishing on a site whose pitch is "don't trust it, check it".
 *
 * Same rules as every other fact on the site: no figure without a source, no
 * risk-check count, no price, nothing from MUST_NOT_CLAIM.
 */
export type Entry = {
  readonly date: string
  readonly title: string
  readonly body: string
  /** Pull requests, commits or files a reader can open. */
  readonly refs: readonly string[]
}

export const CHANGELOG: readonly Entry[] = [
  {
    date: '2026-09-05',
    title: 'The chat can read before it answers, and answers as it writes',
    body:
      'The assistant on Telegram and the web now calls read-only tools mid-turn — '
      + 'portfolio, risk state, rejected trades, the macro calendar, a scan — '
      + 'through the same permission table the commands use. Replies stream on '
      + 'both surfaces and are replaced by the checked final text. Answers the '
      + 'web gave locally now reach the shared memory, older turns fold into a '
      + 'rolling note instead of vanishing, the free-chat quota applies on Telegram '
      + 'as it did on the web, and the chat’s own messages follow your language.',
    refs: ['bot/nlp/chat_tools.py', 'bot/web/user_gateway.py', 'bot/nlp/conversation_store.py'],
  },
  {
    date: '2026-09-03',
    title: 'Read failures stopped rendering as measurements, across thirty surfaces',
    body:
      'A run of fixes on the rule this project is built around: an unreadable '
      + 'value is never a zero, and an absent one is never a measurement. Six '
      + 'surfaces that read the paper book and labelled it live, a yield panel that '
      + 'called an unread margin idle, a status card that went dark when the macro '
      + 'calendar could not answer, and a monitor whose one broken check silenced '
      + 'every alert while its heartbeat stayed fresh.',
    refs: ['PR #266–#286'],
  },
  {
    date: '2026-09-01',
    title: 'Audit findings RC-2026, closed',
    body:
      'Every default-on protection is now named in the example configuration, '
      + 'the two-factor step-up and the money move it protects must address one '
      + 'subject, a backup that skipped the credential stores no longer reports '
      + 'success, and the secret scan is scoped to the commit rather than every '
      + 'branch on the runner.',
    refs: ['PR #247–#258', 'docs/FIXES_2026-09-01.md'],
  },
  {
    date: '2026-08-25',
    title: 'A deploy is verified on both machines it lands on',
    body:
      'The web container and the bot box are checked separately, and “could not '
      + 'check” is a third outcome rather than a failure — a checker that reports '
      + 'an unreachable endpoint as a failed deploy sends an operator to roll back '
      + 'something that landed. The bot box resets to a URL, never to a remote name, '
      + 'after a deploy landed on a tree hundreds of commits stale.',
    refs: ['scripts/verify_deploy.sh', 'scripts/verify_deploy_source.sh'],
  },
  {
    date: '2026-08-22',
    title: 'The site stopped stating a risk-check count',
    body:
      'A headline total was maintained by hand against a file that changes and '
      + 'drifted across a dozen surfaces at three different values. The number that '
      + 'matters is per trade and is reported on the decision record, so the pages '
      + 'now describe the property — what happens when a check cannot be answered — '
      + 'and a test refuses any published count.',
    refs: ['site/test/site_honesty.test.js', 'tests/test_no_hardcoded_risk_check_count.py'],
  },
]
