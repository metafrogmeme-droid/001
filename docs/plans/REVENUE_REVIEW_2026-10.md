# Revenue review, October 2026

**Read 2026-10-30, against `main` at `256546f6`.** Read-only: this document changes no code.
It covers what the product charges for today, where the sign-up funnel loses people, which
copy is a risk, and a ranked list of changes that would bring in revenue without a trading
edge.

**Basis.** Every claim below was followed through the code and is cited as `path::Symbol` or
`path::Symbol#"marker"`. Two things could not be read from the repository, and every
statement here is limited by them:

- **The production environment.** No deployed `.env` or `~/runeclaw-persist/web.env` was
  available. Where a feature depends on a flag, this document gives the code default and says
  whether production overrides it could not be checked.
- **Traffic and conversion.** No analytics and no member counts were read, and the code has no
  funnel measurement (see F10). The impact ratings in section 4 are a ranking, not a forecast.

**Why product revenue.** `docs/research/prereg/NEW_STRATEGY_SUMMARY_2026-10-28.md` ends: "A
profitable edge over holding BTC is not shown." The README's own live record (read
2026-10-17) gives 249 exits at a 37% win rate and a profit factor of 0.61. Any revenue plan
that depends on the bot making money for its users has nothing to stand on yet.

---

## Summary

1. **There is no way to buy anything on the main product.** There is no card processor,
   Telegram Stars invoice or checkout in `app/`, `bot/` or `token/`: a search for stripe,
   paddle, paypal, `send_invoice`, `pre_checkout` and `XTR` finds nothing. Pro and Elite are
   granted by hand (`/set_tier`). The one payment path that is complete in the code is Pi
   Network "Coach Plus" and tips, and it only works inside the Pi Browser app.
2. **Every upgrade prompt ends at a card with nothing to buy.** The chat-quota message and the
   Contract Studio quota message say "Upgrade" and link to the plan card. The plan card says
   "Tiers are granted by the operator. Ask in @HTRUNECLAW_bot". The bot's `/tier` says the
   same, and the bot has no command for asking.
3. **Those upgrade prompts promise things no tier gives.** The prompts list "live signals",
   "deeper research", "the security-flag pass" and "testnet deploy". None of them is on the
   tier ladder: live signals go to every signed-in member, and testnet deploy is admin-only.
   This breaks the repo's own rule that a card names only a benefit something grants.
4. **The site promises live trading that members do not get by default.** The landing page
   and account panel say "go live on your own exchange keys". With the code defaults
   (`LIVE_ADMIN_ONLY=true`, `PER_USER_LIVE_ENABLED=false`), a member's confirm is a paper fill.
5. **Daily Duel, the best engagement loop, connects to nothing.** The Daily Duel bot's welcome
   promises a Season 1 leaderboard, but `/leaderboard` replies "coming soon". Its only web
   link is `/doge`, which needs a DogeOS testnet wallet and has no site navigation, no sign-up
   and no link to the main bot.
6. **The cheapest wins are fixes:** make every upgrade prompt render from `plan_ladder`, add a
   counts-only funnel readout, and repair three referral leaks. After that, one real payment
   path (Telegram Stars is the natural fit for a Telegram product) selling compute (chat,
   `/analyze`, backtests) rather than calls.

---

## 1. What is monetised today

| Stream | How a user pays | What they get | Works end to end? |
|---|---|---|---|
| Membership tiers (Basic, Pro, Elite) | No way to pay. Only the operator can set a tier: `/set_tier` (`bot/skills/access_commands.py::AccessCommands._cmd_set_tier`), which writes `bot/utils/user_store.py::UserStore.set_tier`. | The ladder in `bot/token/tier_gate.py::_DEFAULT_FEATURE_MIN_TIER`. Pro: `/scalp /intraday /swing /deepscan /patterns /analyze /quant`. Elite: backtest, walk-forward, optimize, learn. The ladder refuses nobody unless `TIER_GATE_ENABLED` or the token gate is on (`bot/token/tier_gate.py::tiers_enforced`), and both default off. Pro and above skip the free chat quota (`bot/web/chat_quota.py::is_quota_exempt`). Pro and above also get the operator-funded model on `/analyze`, but only when `PER_USER_LLM_TIERS_ENABLED` is on, and it defaults off. | **No.** There is nothing to sell and nowhere to buy it. A grant has no expiry (`UserStore.set_tier` stores one word), so a paid period cannot be represented. |
| `$RCLAW` stake tiers | Stake `$RCLAW` and verify a wallet with `/linkwallet`. | The same ladder, at 10,000 tokens for Pro and 100,000 for Elite (`bot/token/tier_gate.py::plan_ladder`). | **No.** The gate is off by default (`bot/token/tier_gate.py::gate_enabled`). The balance reader only accepts devnet, testnet and localhost RPC hosts (`bot/token/tier_gate.py::_rpc`). The `$RCLAW` mint that exists is on mainnet (`token/config/rclaw.mainnet.json`), so a real holder's stake cannot be read. |
| Free chat quota (the only paywall in place) | It is a cap, not a charge. | 5 AI questions a day by default (`bot/web/chat_quota.py::DEFAULT_FREE_DAILY_LIMIT`). The cap is on whenever chat runs on the operator's paid key (`bot/web/chat_quota.py::quota_enabled`). | The cap works. The upgrade prompt it shows leads nowhere (see F2). |
| Pi tips and **Coach Plus** | Pi, through the Pi SDK inside the Pi Browser app. | Tips unlock nothing. Coach Plus raises the daily allowance for Ask the Claw for `PI_COACH_PLUS_DAYS` (default 30) once the payment completes (`app/routes/pi_payments.js::post('/coach-plus')`, `app/lib/pi_coach.js::COACH_PLUS_KIND`). | **Yes in code:** order, approve, complete and incomplete, idempotent, with the payment read back from Pi using the server key, and tested (`app/test/pi_payments.test.js`). **Not verified in production.** It is off unless `PI_NETWORK_API_KEY` and `PI_COACH_PLUS_PI` are set, and the Pi app's listing status is not recorded in the repo. It earns Pi and reaches only Pi Browser users. |
| Pi season prizes | Not applicable. | Prizes in Pi for paper-Arena placings (`app/routes/pi_prizes.js`). | No route pays anyone. This is a **cost** to the operator, not revenue, and it is off by default. |
| `$RCLAW` token / presale | No sale has run. | The `/token` page and `/rclaw` show the mint, supply, locks and multisig. They show no sale block (`app/routes/token.js`), and `app/test/the_token_page_names_one_mint.test.js` fails if the served docs name a sale. | Presale tooling exists (`token/presale/`, Smithii and Metaplex Genesis). Under `token/README.md` and `docs/TOKEN_ROADMAP.md` §10–11, it waits on legal review and an audit. **Not recommended as a revenue path:** see R-9. |
| Referral program | Nothing is paid. | A count, a ladder chip, and Daily Duel squad membership (`app/auth.js::REFERRAL_TIERS`, `app/lib/duel_squads.js::buildSquads`). The perks "Fee credits" and "A share of protocol revenue" are marked `planned`. | Sign-up attribution works for email and password sign-ups only. Three leaks are listed in F8. |
| Strategy marketplace, copy-follow | Free. | Follow an agent and copy its picks on paper (`app/routes/copy.js`). | No fee, commission or revenue-share code. |
| Memeclaw launchpad | Not applicable. | Testnet only (`app/lib/memeclaw_config.js`). | The curve fee is `fee_bps: 0`. |
| Developer, MCP and tool APIs | Free by design. | `app/public/developers.html` says "Free and open: no pricing, no access gate". | No metering and no API keys for paid use. |
| Exchange affiliate links | Not applicable. | Plain deep links (`app/lib/venue_links.js`). | No referral or broker parameter on any venue link. `docs/INCOME_MAP.md` (Referrals & Affiliates) says the same. |

**What the code supports today:** the product's revenue is zero on every surface except Pi
Coach Plus. That one is complete in the code, unverified in production, and limited to Pi
Browser users.

---

## 2. The funnel and where it leaks

The intended path is landing page `/`, then sign-up (email or OAuth), then the Arena or the
dashboard, then linking Telegram (`/link <token>`), then a paid tier. Daily Duel and the Pi and
DogeOS apps are separate ways in.

| # | Leak | Evidence |
|---|---|---|
| F1 | **No pricing anywhere on the landing page.** The only offer is "Free · paper-trade in your browser". No page says what Pro or Elite cost or how to get them, and there is no `/pricing` route. | A search of `app/public/index.html` for pricing, upgrade, elite and subscribe finds only a referral comment. No `href="/pricing\|/plans\|/upgrade"` exists in `app/public`. |
| F2 | **Upgrade prompts lead to a card with nothing to buy.** The web chat quota links to `/dashboard#account`, the top of the account view rather than the plan panel `#account/aplan`. The Contract Studio prompt links to `#account/aplan`. The plan card ends with "Tiers are granted by the operator … Ask in @HTRUNECLAW_bot". The bot's `/tier` ends with "Tiers are granted by the operator", and the bot has no command for requesting a tier. | `bot/utils/i18n.py::#"Upgrade to keep chatting"`, `bot/web/user_gateway.py::handle_contract_studio#"See plans"`, `app/public/js/plan-card-model.js::cardHtml`, `bot/formatters/tier_card.py::render_tier_card#"Tiers are granted by the operator"` |
| F3 | **Upgrade prompts sell benefits no tier grants** (also listed as H2 and H3 below). | The chat prompt names "priority models, live signals, and deeper research". Live calls go to any signed-in member (`app/lib/signal_access.js::signedIn`), and `/research` is not on `_DEFAULT_FEATURE_MIN_TIER`. The Contract Studio prompt names "the security-flag pass and testnet deploy". `scan_security_flags` runs on every draft, and deploy is admin-only (`bot/web/user_gateway.py::handle_contract_deploy#"contract deploy is admin-only"`). |
| F4 | **Even a granted Pro buys almost nothing by default.** With `TIER_GATE_ENABLED` off, every command is open to every member. A Pro member gains only the chat-cap exemption (when the quota is on) and the premium `/analyze` model (when `PER_USER_LLM_TIERS_ENABLED` is on). | `bot/token/tier_gate.py::tiers_enforced`, `bot/token/tier_gate.py::plan_ladder` |
| F5 | **The site promises live trading that members do not get by default.** The landing page says "or go live on your own exchange keys". After linking Telegram, the panel says "exchange-key management and live controls unlocked. Live trades run on your own exchange keys." With the code defaults, a member's confirm opens on paper. The bot says so in Telegram, but the website never does. | `app/public/index.html::#"or go live on your own exchange keys"`, `bot/config.py` (`live_admin_only` defaults `True`, `per_user_live_enabled` defaults `False`), `bot/core/engine.py` (the `live_admin_only` branch returns practice), `bot/skills/trading_commands.py#"Live trading on this bot is "` |
| F6 | **`/linkwallet` advertises tier access that cannot happen.** The `/help` line reads "link a Solana wallet (read-only) for $RCLAW tier access". The gate is off by default, and the reader refuses mainnet RPC hosts, where the real mint lives. | `bot/skills/command_catalog.py::GROUPS#"for $RCLAW tier access"`, `bot/token/tier_gate.py::_rpc` |
| F7 | **Daily Duel is a dead end.** The welcome says "Build your streak and climb the Season 1 leaderboard." `/leaderboard` answers `COMING_BOARD`, and the score and handle features are also "coming soon". The one link it gives is `humanoid-traders.com/doge`, a DogeOS-testnet wallet page that loads no site navigation (per its own header comment). Nothing points to `@HTRUNECLAW_bot`, to sign-up, or to an invite link. | `bot/daily_duel_bot.py::WELCOME`, `bot/daily_duel_bot.py::DailyDuelBot.cmd_leaderboard`, `bot/daily_duel_bot.py::COMING_SCORE`, `app/routes/dogeapp.js::page` |
| F8 | **Referral attribution leaks in three places.** (a) OAuth, Telegram and X sign-ups are never credited: `findOrCreateOAuthUser` inserts with no referral, and `rc_ref` is used only by the email register. (b) The Telegram close-card share button is built with no `ref_code`, so its link carries no attribution. (c) The bot records `referred_by` on `/start ref_<code>`, and nothing reads it: it never reaches the website's count. The main bot also has no `/invite` command. | `app/auth.js::findOrCreateOAuthUser`, `bot/skills/alerts_monitor.py#"close_data, await self._bot_username(bot))"`, `bot/utils/user_store.py::UserStore.record_referrer`. These match `docs/INCOME_MAP.md` (Referrals & Affiliates), re-checked here. |
| F9 | **The landing page leads with a losing record.** The strip under the hero shows the recorded win rate, a profit factor below 1, and max drawdown. That is honest and must stay true wherever it appears. As the first claim a visitor reads, though, it sells the weakest part of the product. The tools, the Duel and the proofs are stronger. | `app/public/index.html::#"function heroEyebrow"` (the stats renderer that follows it) |
| F10 | **Nothing measures the funnel.** There is no funnel endpoint, and the admin routes are `/settings` and `/members`. No number exists for sign-ups, link rate, first Arena trade, Duel retention, quota hits or upgrade clicks. | `app/routes/admin.js` |

Not a leak: a Telegram newcomer is admitted to paper automatically (`PAPER_AUTO_ACCEPT`
defaults `True`, `bot/config.py`), and the website account works without Telegram.

---

## 3. Honesty and compliance risks in copy

No literal dollar figure (`$` followed by a digit) was found in `app/public/*.html` or
`app/public/js/i18n.js` (checked by search on 2026-10-30). The landing stats and the replay
are percent-only by construction. The risks are below. Line numbers are as of `256546f6`; the
quoted text is what to search for.

| # | Where | Text | Risk | Fix |
|---|---|---|---|---|
| H1 | `README.md` "Live Trading Records" (around line 946; same in `README.zh-TW.md` around line 886). The GitHub repo is **public**: `gh api` reports `"visibility": "public"`. | "Trading Period: June 17-19, 2026 … Win Rate: 55.3% (21W / 17L) … Total Realized PnL: +$46.30", plus "$10-20 per trade" | A three-day window that contradicts the README's own 17-Oct record (37% win rate, profit factor 0.61). It also puts a dollar P&L on a public surface. "See `logs/` for full trading records" points at files git does not track (`git ls-files logs` is empty). | Delete the section, or replace it with a pointer to the generated live-record paragraph. |
| H2 | `bot/utils/i18n.py` key `chat_quota_exhausted_web` | "Upgrade to keep chatting … unlimited questions plus priority models, live signals, and deeper research" | Sells benefits nothing grants (F3), and has no way to buy. | Render the prompt from `plan_ladder` (R-1). |
| H3 | `bot/web/user_gateway.py::handle_contract_studio` | "Upgrade for unlimited Contract Studio drafts on the priority model, plus the security-flag pass and testnet deploy." | Two of its three benefits are free or admin-only. | Same as H2. |
| H4 | `app/public/index.html`, key `lp.how_3p` (and its 14 translations in `app/public/js/i18n.js`); `acc.linked_ok`, `acc.link_tg_body` | "or go live on your own exchange keys"; "Live trades run on your own exchange keys" | A capability claim that the defaults make false for members (F5). Once a paid tier exists, a buyer could reasonably read it as part of what they paid for. | Add the gateway's own reading of whether this account can trade live, and say "live trading is by invitation" when it cannot. |
| H5 | `app/routes/track.js` `GET /replay-trade`, shown as "Watch a real trade unfold" | Picks the close with the largest absolute P&L in the last 14 days | The pick is symmetric, so not cherry-picked toward wins. Still, a biggest-mover on the landing page reads as typical. `pnl_pct` divides by `size_usd`, a field CLAUDE.md warns has meant both margin and notional. | Label the clip "largest move of the last 14 days, win or loss" and confirm the basis with `position_size_basis`. |
| H6 | `bot/marketing/channel_forwarder.py::ChannelForwarder.post_signal` (members' text) | "RUNECLAW SIGNAL … Entry / Stop Loss / Take Profit / Confidence" | Specific trade instructions with no "not investment advice" line. The image card has one (`bot/formatters/signal_card.py`, "not investment advice"). The research shows no edge. This must never become a paid signal subscription, which is regulated investment advice in many jurisdictions. | Add the same one-line disclaimer the card carries. Keep calls free (see R-3). |
| H7 | `app/public/signal.html`, `app/public/agents.html`, `app/public/strategy.html`, `app/public/leaderboard.html`, `app/public/track.html` | Calls, backtest percentages, win rates | None of these pages carries an "advice" or "past results" line (search of the HTML; the landing page has "Past results ≠ future results"). | One shared footer line on every page that shows a call or a figure. |
| H8 | `app/auth.js::REFERRAL_TIERS` | "A share of protocol revenue." (state `planned`, "Not an offer") | It is labelled honestly. Even so, a revenue share earned by recruiting, tied to a token, is the shape regulators read as a security or a pyramid scheme. | Remove the perk rather than keep it as a plan. "Fee credits" can become "Pro days" (R-5). |
| H9 | `app/public/index.html` `sec.arena_h`; `app/public/arena.html` meta | "Prove your edge before you risk a cent"; "The zero-friction way to prove your edge" | Low risk. It presents paper trading as a step toward risking money. | Optional: "Test an idea without risking anything." |
| H10 | `bot/daily_duel_bot.py::WELCOME` | "climb the Season 1 leaderboard" | A door that does not exist yet (F7). | Ship the board, or reword the welcome until it ships. |
| H11 | Pi season prizes (`app/routes/pi_prizes.js`) | Prizes for paper-trading placings | Contest and sweepstakes rules vary by jurisdiction. The prizes are an operator cost, not revenue. | Leave it off unless the owner has checked the rules. |

---

## 4. Revenue improvements, prioritised

Ranked by expected impact for the effort, given that there is no edge. Effort: **S** is under
a day, **M** is a few days, **L** is a week or more. "Impact" is relative; no traffic data was
read. Each item follows the standing rules: off-by-default flags, no dollar figures on public
payloads, `NEVER_GATED` untouched, and nothing sold that depends on the bot winning.

| # | Change | Effort | Expected impact | Files |
|---|---|---|---|---|
| R-1 | **Make every upgrade prompt honest and pointed at the right place.** Build the chat and Contract Studio upgrade text from `plan_ladder`, so it names only what a tier grants. Link to `#account/aplan`. Until a payment path exists, say plainly "tiers are granted on request" and give a working way to request one: a `/requesttier` command that notifies the operator, the same pattern as `_request_operator_admission`. | S | Low revenue by itself; it is the prerequisite. Stops a sale promising things that are not delivered (H2, H3) and closes the dead end in F2. | `bot/utils/i18n.py`, `bot/web/chat_quota.py::exhausted_notice`, `bot/web/user_gateway.py::handle_contract_studio`, `bot/token/tier_gate.py::plan_ladder`, `bot/skills/command_catalog.py` |
| R-2 | **Counts-only funnel readout for the operator.** Daily counts of sign-ups (by method), Telegram links, first Arena trade, Duel calls and day-7 return, quota-exhausted events, plan-card views and upgrade-link clicks. Operator-only (`isOperator`), counts and ratios only, with no per-user rows. | S | Indirect, but every later item needs a before and after. Without it, nobody can tell whether R-3 to R-8 worked. | `app/routes/admin.js` (behind `authMiddleware` and `isOperator`), `bot/web/chat_quota.py::consume`, `app/auth.js` |
| R-3 | **One real payment path: Pro time paid in Telegram Stars, sold as compute.** Add an expiring grant (`tier_until`) next to `set_tier`, so a lapsed period falls back to Basic and `tier_reading` stays the one reading. Sell what costs money to serve and owes nothing to the edge: no daily chat cap, the premium model on `/analyze` (`PER_USER_LLM_TIERS_ENABLED`), and `/deepscan`, `/quant` and `/patterns`. Elite adds backtest, walk-forward and optimize. Turn on `TIER_GATE_ENABLED` only once this path works. Never sell calls, live access or limits; `NEVER_GATED` stays. Before building, confirm Telegram's current rules for digital goods sold in bots. python-telegram-bot is pinned at 22.7, which has the invoice API. | M | **Highest direct impact.** It is the only item that collects money from the existing Telegram audience. The price comes from cost-to-serve, which `tier_gate.py` already uses as the ladder's basis. | `bot/utils/user_store.py::UserStore.set_tier`, `bot/token/tier_gate.py::granted_tier`, a new payment handler in `bot/skills/`, `bot/skills/command_catalog.py`, `app/public/js/plan-card-model.js`, `bot/formatters/tier_card.py` |
| R-4 | **Finish Daily Duel and connect it to the product.** Ship the board, the score and handle linking that are "coming soon" (`COMING_BOARD`, `COMING_SCORE`, `COMING_HANDLE`). Show the streak with a share button that carries an invite. Add one line pointing to `@HTRUNECLAW_bot` and the website with a `ref`. Point the web link at `/duel` instead of the DogeOS-wallet page. | M | High engagement and the cheapest way to acquire users. It is a daily habit that needs no edge and no keys, and it already writes sealed, percent-only records. | `bot/daily_duel_bot.py`, `bot/duel_bot.py`, `app/routes/public_duel.js`, `app/public/duel.html` |
| R-5 | **Fix the referral leaks and give a reward that costs compute, not money.** Credit OAuth sign-ups from `rc_ref`. Pass `ref_code` into `close_share_button`. Add `/invite` to the main bot. Merge the bot-side `referred_by` into the website's count. Replace "Fee credits" and "A share of protocol revenue" with "N days of Pro per verified sign-up", which grants a real thing once R-3's expiring grant exists, and drop the revenue-share perk (H8). | S–M | Medium. Turns the existing invite panel and Duel squads into growth. | `app/auth.js::findOrCreateOAuthUser`, `app/auth.js::REFERRAL_TIERS`, `bot/skills/alerts_monitor.py`, `bot/formatters/share_invite.py::close_share_button`, `bot/utils/user_store.py::UserStore.record_referrer`, `app/public/js/referral-tier-model.js` |
| R-6 | **Bring Coach Plus to the main site and Telegram.** The Pi app already has a metered, fact-grounded coach with a paid allowance (`app/lib/pi_coach.js`). Offer the same allowance shape to website and Telegram members under the R-3 payment path, framed as education ("why did this trade fail", "what does funding mean") built on `/learn` and the lessons. | M | Medium. Education is the clearest thing to sell when there is no edge, and the code is mostly built. | `app/lib/pi_coach.js`, `app/routes/learn.js`, `app/lib/learn_tutor.js`, `bot/web/chat_quota.py` |
| R-7 | **Make the website match the product: lead with tools, add an honest plans block.** Move Guardian, scam-or-safe, approvals, Daily Duel and Provable Calls above the track-record stats (the stats stay, unchanged, lower down). Add a Plans section rendered from `/api/tier`'s ladder, never a hand-written table (`app/test/a_plan_card_is_the_bots_tier.test.js` exists for that). Fix H4's live-trading wording. | S–M | Medium conversion gain. The free tools are the product's real strength, and right now a visitor's first number is a profit factor below 1. | `app/public/index.html`, `app/public/js/i18n.js`, `app/public/js/plan-card-model.js` (bump `?v=`) |
| R-8 | **Metered API keys for the safety tools (B2B).** The read-only tools (scam-or-safe, approvals X-ray, sig decoder, firewall, botcheck) are useful to wallets and bots, need no edge, and are already behind `/api`. Keep a free tier, and add an API key with a higher limit for paid use. Changes `developers.html`'s "no pricing" sentence. | L | Medium to high if it finds buyers, but it needs sales effort and support. Unproven. | `app/routes/guard.js`, `app/routes/scam_or_safe.js`, `app/lib/rate_limit.js`, `app/auth.js`, `app/public/developers.html` |
| R-9 | **Do not pursue now:** paid live signals, a `$RCLAW` sale, revenue-share referrals, or exchange affiliate codes. Signals and a token sale need an edge and legal review the project does not have (H6, `docs/TOKEN_ROADMAP.md` §10–11). Affiliate codes pay the operator from the volume of users trading a strategy that loses on its own record, and that conflict is worse than the revenue is worth. If the owner wants affiliate codes anyway, they need a visible disclosure on every venue link, and no feature that encourages trading volume. | — | Avoids regulatory and reputational risk. | `app/lib/venue_links.js`, `token/presale/` |

**Suggested order:** R-1 and R-2 (this week), then R-3, then R-5 and R-4 together (they share
the invite plumbing), then R-7 and R-6. R-8 only after R-2 shows enough organic tool usage to
justify it.
