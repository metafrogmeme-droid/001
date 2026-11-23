'use strict';
/**
 * Every word the website's email says, in one place, keyed by template id.
 *
 * The wording is Marketing's final English copy of 23 November 2026 for the
 * seven templates it rewrote (welcome_verify, verify, password_reset_link,
 * password_changed, tier_upgrade, tier_downgrade, onboarding). The five
 * security notices it did not rewrite (password_set, password_reset,
 * totp_enabled, totp_disabled, account_deleted) keep the sentences
 * lib/account_mail.js sent before they moved here, word for word; only the
 * layout around them changed. A wording change is an edit to this file and
 * nothing else; the renderer in ./index.js owns the layout.
 *
 * `security: true` is the one reading of "is this a security mail". A
 * security mail never carries an unsubscribe link and is never held back by
 * an opt-out; every other template carries the link and honours it
 * (lib/account_mail.js asks this table, not its own list).
 *
 * Placeholders: `{{key}}` is required (the renderer throws when it is
 * missing, so a blank link can never ship), and `{{#key}}…{{/key}}` renders
 * only when `key` is not blank. Values are plain text; the renderer escapes
 * them for HTML. `if` on a block renders it only when that variable is true.
 *
 * Content rules (app/test/email_templates.test.js enforces them on every
 * render): no prices or dollar amounts, no profit, return or target claims,
 * no Telegram bot handle, no token, staking or presale talk, and nothing that
 * says live trading is a tap away or that a plan unlocks it.
 */

const CREDIT = 'HUMANOID-TRADERS · RUNECLAW';
const SECURITY_NOTE = 'This is an automatic security notice from RUNECLAW.';
const NOTICE_NOTE = 'This is an automatic notice from RUNECLAW.';
const ADVICE = 'RUNECLAW is a trading tool, not financial advice.';
const UNSUBSCRIBE = 'If you would rather not get these emails, you can unsubscribe: {{unsubscribe_url}}';
const GREETING = 'Hello{{#name}} {{name}}{{/name}},';

// What a plan notice lists when the bot's plan ladder could not be read.
// The sentence the tier notice already sent before this change.
const FEATURES_UNREADABLE = 'Your dashboard shows what it includes.';

// PREVIEW ONLY. Marketing's example bullets, so a reviewer can read a plan
// notice with something in it. Live mail lists what the bot's plan ladder
// says at send time (lib/plan_features.js), never these.
const PREVIEW_FEATURES = {
  basic: ['Example: Paper trading and the dashboard, the site chat, and the safety tools — including halt, risk, portfolio, and why a trade was refused.'],
  pro: ['Example: Everything in Basic, plus deeper scans and per-call analysis.'],
  elite: ['Example: Everything in Pro, plus longer research runs such as backtests, walk-forwards, optimization, and the learning tools.'],
};

const RESET_CTA = { cta: { label: 'Reset your password', url: '{{forgot_url}}', primary: true } };

const TEMPLATES = {
  // Sent once, at registration. It carries the verify link, so it is a
  // security mail even though it also welcomes.
  welcome_verify: {
    security: true,
    advice: true,
    subject: 'Welcome to RUNECLAW — confirm your email',
    preheader: 'Your paper account is ready — no exchange keys needed.',
    blocks: [
      { greet: true },
      { p: 'Welcome to RUNECLAW. Your account is open, and you can start on paper right now.' },
      { p: 'The Paper Trading Arena uses live market prices and the same virtual balance for every account. No exchange keys, no deposit, nothing to configure. You can also ask the live analyst from the chat on the site.' },
      { p: 'A few things that are true from the first day:' },
      { ul: [
        'Every call is sealed when it is made, before the market moves, so a result cannot be quietly rewritten later.',
        'The server never holds mainnet keys or signs for users. You hold the keys.',
        'Live trading, if you want it later, runs on your own withdrawal-disabled exchange keys and needs operator approval. Paper works without any of that. You can link Telegram from your account when you want exchange-key management, live-trading controls and alerts. We never message you first, and we never ask for your keys.',
      ] },
      { p: 'Guardian checks, when you use them, give public signals with reasons. They are not a safe or scam verdict.' },
      { p: 'Whenever you are ready, step into the Arena.' },
      { cta: { label: 'Open the Arena', url: '{{arena_url}}' } },
      { p: 'Please confirm this email address for your RUNECLAW account.' },
      { p: 'Confirming it means password-reset links and account notices can reach you. It does not start live trading.' },
      { cta: { label: 'Confirm your email', url: '{{verify_url}}', primary: true } },
      { p: 'This link expires in {{expires_in}}.' },
      { note: 'If you didn’t create an account, ignore this message.' },
    ],
  },

  verify: {
    security: true,
    subject: 'Verify your RUNECLAW email',
    preheader: 'Confirm this address. The link expires in {{expires_in}}.',
    blocks: [
      { greet: true },
      { p: 'Please confirm this email address for your RUNECLAW account.' },
      { p: 'Confirming it means password-reset links and account notices can reach you. It does not start live trading.' },
      { cta: { label: 'Confirm your email', url: '{{verify_url}}', primary: true } },
      { p: 'This link expires in {{expires_in}}.' },
      { note: 'If you didn’t create an account, ignore this message.' },
    ],
  },

  password_reset_link: {
    security: true,
    subject: 'Reset your RUNECLAW password',
    preheader: 'This link expires in {{expires_in}}. If you didn’t ask for it, you can ignore this.',
    blocks: [
      { greet: true },
      { p: 'We received a request to reset the password on your RUNECLAW account.' },
      { p: 'Choose a new password with the button below. The link expires in {{expires_in}}.' },
      { cta: { label: 'Choose a new password', url: '{{reset_url}}', primary: true } },
      { note: 'If you didn’t request this, ignore this email. Your password is unchanged.' },
    ],
  },

  // Marketing's rewrite, with the time the change was made kept in the first
  // sentence: every other security notice states it, and the owner asked for
  // this one to as well.
  password_changed: {
    security: true,
    subject: 'Your RUNECLAW password was changed',
    preheader: 'If this wasn’t you, reset your password now.',
    blocks: [
      { greet: true },
      { p: 'The password on your RUNECLAW account was changed on {{when}}.' },
      { p: 'Every other signed-in session was signed out.' },
      { p: 'If this wasn’t you, reset your password now with “Forgot your password?” on the site, and reply to this email.' },
      RESET_CTA,
    ],
  },

  // ── Not rewritten by Marketing: the earlier wording, restyled only. ──────

  password_set: {
    security: true,
    subject: 'A password was added to your RUNECLAW account',
    preheader: 'A password was added to your RUNECLAW account.',
    blocks: [
      { p: 'A password was added to your RUNECLAW account on {{when}}.' },
      { p: 'You can now sign in with your email and that password, as well as the way you signed in before. Every other signed-in session was signed out.' },
      { p: 'If this wasn\'t you, someone has signed in to your account. Reset the password now with "Forgot your password?" at {{site_url}}/ and reply to this email.' },
      RESET_CTA,
    ],
  },

  password_reset: {
    security: true,
    subject: 'Your RUNECLAW password was reset',
    preheader: 'The password on your RUNECLAW account was reset with an emailed link.',
    blocks: [
      { p: 'The password on your RUNECLAW account was reset with an emailed link on {{when}}.' },
      { p: 'Every signed-in session was signed out.' },
      { p: 'If this wasn\'t you, someone can read your email. Secure your email account first, then reset your RUNECLAW password again at {{site_url}}/ and reply to this email.' },
      RESET_CTA,
    ],
  },

  totp_enabled: {
    security: true,
    subject: 'Two-factor authentication is on',
    preheader: 'Two-factor authentication was turned on for your RUNECLAW account.',
    blocks: [
      { p: 'Two-factor authentication was turned on for your RUNECLAW account on {{when}}.' },
      { p: 'Signing in with a password now also asks for a code from your authenticator app.' },
      { p: 'If this wasn\'t you, someone has signed in to your account. Reply to this email straight away.' },
    ],
  },

  totp_disabled: {
    security: true,
    subject: 'Two-factor authentication was turned off',
    preheader: 'Two-factor authentication was turned off for your RUNECLAW account.',
    blocks: [
      { p: 'Two-factor authentication was turned off for your RUNECLAW account on {{when}}.' },
      // The route asks for a signed-in session and a current code, not the
      // password, so the notice claims exactly that much about whoever did it.
      { p: 'Signing in no longer asks for a code from your authenticator app.' },
      { p: 'If this wasn\'t you, someone is signed in to your account and has one of your codes. Reset your password now with "Forgot your password?" at {{site_url}}/ (that signs every session out), turn two-factor back on, and reply to this email.' },
      RESET_CTA,
    ],
  },

  account_deleted: {
    security: true,
    subject: 'Your RUNECLAW account was deleted',
    preheader: 'Your RUNECLAW account was deleted.',
    blocks: [
      { p: 'Your RUNECLAW account was deleted on {{when}}.' },
      // What the route's own reply says, and no more: the bot half only runs
      // when a gateway is configured, so "and in the bot" is not always true.
      { p: 'Your account and its data have been erased.' },
      { p: 'If this wasn\'t you, reply to this email straight away. A deletion cannot be undone, but we can look at how it happened.' },
    ],
  },

  // ── Not security mail: unsubscribe link, and an opt-out holds them back. ─

  // Also the wording when the direction of the change is not known (the bot
  // sent no previous plan): nothing in it says "upgraded".
  tier_upgrade: {
    security: false,
    note: NOTICE_NOTE,
    subject: 'Your RUNECLAW plan is now {{tier_name}}',
    preheader: 'Here’s what {{tier_name}} includes on your account.',
    blocks: [
      { greet: true },
      { p: 'Your RUNECLAW plan is now {{tier_name}}.' },
      { p: 'Here’s what it includes:' },
      { features: true },
      { p: 'Your dashboard always shows the current plan. Paper trading and the safety tools stay with you on every plan.' },
      { cta: { label: 'See your plan', url: '{{dashboard_url}}', primary: true } },
    ],
  },

  tier_downgrade: {
    security: false,
    note: NOTICE_NOTE,
    subject: 'Your RUNECLAW plan is now {{tier_name}}',
    preheader: 'Your plan has changed. Here’s what {{tier_name}} includes now.',
    blocks: [
      { greet: true },
      { p: 'Your RUNECLAW plan is now {{tier_name}}.' },
      { p: 'Here’s what it includes now:' },
      { features: true },
      { p: 'Paper trading and the safety tools stay with you. Your dashboard shows the current plan if you want to look it over.' },
      { cta: { label: 'See your plan', url: '{{dashboard_url}}', primary: true } },
    ],
  },

  onboarding: {
    security: false,
    subject: 'A few first steps on RUNECLAW',
    preheader: 'The Arena, a sealed call, and the Study Room — whenever you’re ready.',
    blocks: [
      { greet: true },
      { p: 'Your RUNECLAW account has been sitting ready. Here are three concrete ways to use it — all on paper or on public pages, with no exchange keys.' },
      { ol: [
        { text: 'Open the Paper Trading Arena and place a paper trade, or follow a strategy agent on paper inside your own risk limits. Everyone starts with the same virtual balance, at live market prices.',
          links: ['{{arena_url}}'] },
        { text: 'Verify a public call yourself. Every call is sealed when it is made, before the market moves. You can check that record in your own browser.',
          links: ['{{provable_url}}'] },
        { text: 'Sit with a Study Room lesson, or take that day’s Daily Duel card. Both are practice. Neither is advice.',
          links: ['{{learn_url}}', '{{duel_url}}'] },
      ] },
      { p: 'Whenever you’re ready, the Arena is the most direct next step.' },
      { cta: { label: 'Open the Arena', url: '{{arena_url}}', primary: true } },
      { p: 'If you haven’t confirmed your email yet, you can resend the link from your account.', if: 'unverified' },
    ],
  },
};

module.exports = {
  CREDIT, SECURITY_NOTE, NOTICE_NOTE, ADVICE, UNSUBSCRIBE, GREETING,
  FEATURES_UNREADABLE, PREVIEW_FEATURES, TEMPLATES,
};
