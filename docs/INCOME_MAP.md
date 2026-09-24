# The crypto income map, measured against the code

Fifteen categories, ninety leaves — the whole map of ways people earn in crypto
— with **what RUNECLAW actually serves today** against each one.

Measured on 2026-09-13 by a thirty-six agent sweep: five agents inventoried the
product's surfaces (Telegram, the skill registry and chat-tool catalogue, the
web app, the on-chain surface, the engine and its providers) and found roughly
six hundred doors; fifteen classified the map against that inventory; fifteen
more re-drove each classification adversarially; one re-read the tree
afterwards asking what everybody had missed.

|  | leaves |
|---|---|
| **shipped** — a door exists and the capability behind it is real | 5 |
| **partial** — a door exists and serves part of the leaf, or serves it read-only, or is dark by default | 33 |
| **—** — nothing in the tree serves this | 52 |

Four of the five shipped leaves are in Active Trading. That is the shape of the
product and it is worth saying out loud: RUNECLAW is a perpetual-futures
trading agent that reads widely and executes narrowly.

## How to read a row

**Doors** are the addresses a person invokes — a Telegram command, a web page,
an HTTP route, a chat intercept. Every door printed in this document is
re-resolved on every test run by `tests/test_income_map_doors_exist.py`, which
reads the tables that actually dispatch (the command catalogue, the Express
mount table and each mounted router, the bot gateway's route table, the web
chat intercept list) rather than grepping for the string. A door that stops
existing fails the suite. **That is the only claim in this file anything
checks.**

Everything else is prose from the sweep, and three limits on it are worth
stating before you read a word of it:

**It is code-reading, not execution.** Nothing was fetched, no command was
dispatched, no panel was rendered. "Reachable" means a registration or mount
was read and a caller was found — strictly weaker than driving it, which is
the distinction this repo's #999 card exists to mark.

**The file:line citations are the sweep's own and were not verified.** They are
left in because removing them destroys the sentences, and the file paths stay
useful after the line numbers drift. The adversarial pass spent most of its
corrections on exactly these: one row cited a blank line as the reader of a
config table, another cited a preset dict as a strategy table. Where a verifier
refused part of a row, its refusal is printed under that row.

**Thirty-four of the ninety rows were corrected by the verifier**, and five of
those corrections changed the verdict. Those five are marked ⟲ and listed
below. A row with no ⟲ may still carry a correction to its doors or its
evidence — read the refusal note.

### The five verdicts the verifier overturned

All five went the same way, partial → nothing, and all five for the same
reason: **the doors were real and none of them did the thing the leaf names.**

| Leaf | Classified | Verified | Why |
|---|---|---|---|
| Delta-neutral vaults | partial | — | Every door is real and correctly gated; `/arbpair` now SIZES and PRICES the pair over the caller's own linked venues, and still nothing opens, hedges or holds one. The `$1,000` in `/arb` is a module constant accrued against recorded spread snapshots, not a deposit. |
| Points programs | partial | — | The chip renders, and the product measures, tracks and stores zero points of any kind. Two hits in the whole tree, both the same status-vocabulary literal. |
| Creator campaigns | partial | — | The row's own evidence sentence concedes it: "a referral/invite program and share tooling, not campaigns". |
| Ambassador roles | partial | — | One artifact in the entire tree: a rung named `Ambassador`, `state: 'planned'`, whose own `requires` field says it would ride on a token that does not exist. |
| DAO ambassador/mod programs | partial | — | Same rung. A referral loop is not an ambassador programme. |

## The map

### Active Trading

| Leaf | Today | Doors |
|---|---|---|
| Spot trading | partial | `/livebalance`, `/exposure`, `/networth`, `/spot`, `/api/spot/market`, `/api/spot/basis`, `/api/meme/swap/build`, `/memeplan` |
| Swing trading | **shipped** | `/swing`, `/fullscan`, `/mystrategy`, `/trade`, `/analyze`, `/pocretest` |
| Scalping | **shipped** | `/scalp`, `/fullscan`, `/mystrategy`, `/run`, `/trade` |
| Perp futures | **shipped** | `/trade`, `/positions`, `/open_positions`, `/livepositions`, `/orders`, `/leverage`, `/venues`, `/liveclose`, `/api/trade/propose`, `/api/trade/confirm`, `/api/trade/cancel` |
| Options | — | — |
| Funding rate farming | partial | `/funding`, `/fundingscan`, `/arb`, `/arbpair`, `/api/reports` |
| Basis trades | partial | `/spot`, `/api/spot/basis`, `/api/market/dex` |
| Triangular arbitrage | — | — |
| CEX/DEX arbitrage | partial | `/venue_router`, `/api/market/dex`, `/api/market/venue-router` |
| Copy trading | partial | `/api/arena/follow`, `/api/copy`, `/api/copy/unfollow`, `/api/copy/picks`, `/api/strategies`, `/api/bot-strategy`, `/mystrategy` |
| Algo/bot trading | **shipped** | `/autoconfirm`, `/mystrategy`, `/run`, `/momentum`, `/dip`, `/halt`, `/pause`, `/resume`, `/reset`, `/emergency_stop`, `/enforcing`, `/risk`, `/gates`, `/shadow`, `/backtest`, `/walkforward`, `/optimize`, `/api/lab`, `/api/controls`, `/api/bot-strategy` |

**Spot trading** — partial

Spot ORDER placement on a CEX does not exist and is refused by name: /buy and
/sell both answer "Spot trading is disabled — RUNECLAW operates in futures-
only mode" (trading_commands.py:998, :1007), and a tree-wide grep finds no spot
create_order in bot/ at all (venues.py:276 sets defaultType 'spot' only for
market-data reads). What a user gets today is spot READING: /livebalance
prices the caller's spot holdings on their linked venue; exposure/networth net
wallet spot against perps; app/lib/spot.js pulls Bitget/Bybit/BingX spot
tickers and the spot/perp basis, reachable through the web chat 'spot'
intercept (chat.js:97) and through /spot on Telegram, which renders the SAME
card over the bot-secret sync channel (market_commands.py) — the /api/spot/*
routes it also backs have no browser caller. One genuine spot EXECUTION path exists and it is on-chain, not CEX:
bot/core/meme_swap.py:175 builds an unsigned Jupiter (Solana DEX) swap that
the user signs in their own wallet at /swap (swap-page.js:181). It is double-
gated off: build_swap refuses unless the /memeplan plan came back allowed,
which needs MEME_TRADING_ENABLED (default OFF, meme_executor.py:8-9), and
`signable` is False unless MEME_EXECUTION_NETWORK=mainnet, which
.env.example:883 ships as `simulate`.

*Gap.* No CEX spot order path of any kind — not disabled-by-flag but absent, and
deliberately so. The one spot-swap door is Solana DEX only, single-leg, no
portfolio/position tracking of the resulting holding, and inert on a stock
deploy behind two default-off flags.

*The verifier refused part of this row.* There is no /swap route. The page is served only as the static file
/swap.html, nothing in app/public, app/routes, app/lib, site/ or website/
links to it, and the route's absence is ENFORCED:
app/test/landing_reachability.test.js derives its page list from
`app.get('/x', …)` in server.js and requires every derived page to be linked
from index.html (exceptions itemised in app/test/unlinked_routes.json), so
adding /swap without a landing link fails on the sa…

**Swing trading** — **shipped**

Swing is a first-class hold-duration class in the engine, not a label.
analyzer.py:1154 classifies every idea's strategy_type, and
CONFIG.strategy_types then gives swing its own geometry and lifecycle: SL 2.5
ATR / TP 3.5 ATR (config.py:2136-2137), trailing ENABLED at 1.5 ATR
(:2119-2120), a 48h time-close with a 12h warn (:2121-2122), min confidence
0.50 (:2136), max risk 2% (:2142) — every one distinct from the scalp row
above it. skill_registry.py:1958 reads those multipliers when it builds the
SL/TP ladder. Doors: /swing (scan_commands.py:1045) dispatches pro_scan
mode=swing — 4h candles, top-5 movers, wide SL/TP (skill_registry.py:2473) —
and renders a signal card whose Take/Limit buttons run the normal confirm-and-
execute path; the router's scan_swing intent reaches the same skill through
SCAN_DISPATCH; /fullscan accepts a `swing` argument.

*Gap.* Swing here means swing-on-perpetuals; there is no spot swing book. The
strategy_type is assigned by the analyzer — a user cannot force a given idea
to be treated as a swing, only pick the scan timeframe. Tier feature
`premium_scan` nominally gates /swing at pro, though the whole $RCLAW gate is
off by default.

*The verifier refused part of this row.* Neither line does that. bot/skills/skill_registry.py:1958 is a blank line
between RunStrategySkill._list and _run_symbol_scan; :1822-1826 is the literal
"safe scalper" preset dict inside RunStrategySkill.PRESETS. No line in
skill_registry.py reads CONFIG.strategy_types at all — grep returns zero hits
for it in that file. The real readers are bot/core/analyzer.py:1860-1869
("SL/TP baselines come from CONFIG.strategy_types"),
bot/core/live_executor.py:5891 (…

**Scalping** — **shipped**

Same first-class treatment as swing, tuned the other way: scalp SL 1.5 ATR /
TP 2.0 ATR (config.py:2120-2121), trailing deliberately OFF (:2103), a 2h
time-close with a 1h warn (:2105-2106), min confidence 0.65 (:2134), max risk
1% (:2140); smart_exits.py:34 closes a scalp after 3 candles with under 0.5R
of movement; config.py:1591 recomputes session VWAP on 15m candles
specifically so scalps read a real intraday anchor. Doors: /scalp
(scan_commands.py:1011) dispatches pro_scan mode=scalp — 5m candles, top-3 by
volume, tight zones (skill_registry.py:2456); the router's scan_scalp intent
reaches the same skill; /mystrategy scalp pins the "Safe Scalper" preset
(tight SL 1.5 ATR, conf >= 75%, top-3 volume — skill_registry.py:1922) as a
tighten-only veto on that user's own confirms (trading_commands.py:411); /run
scalp and /fullscan scalp are the other two.

*Gap.* Scalping is a strategy class of the same perp execution engine, not a separate
low-latency path — fills go through the same ccxt REST order flow and the same
confirm card, so nothing here is sub-second execution. The scalp
classification is the analyzer's decision, not the user's.

**Perp futures** — **shipped**

This is the product. USDT-M perpetuals are placed for real through ccxt:
live_executor.py:4790 creates the entry order idempotently, :6400/:6423 attach
the exchange-side stop and take-profit, and every venue call carries
productType USDT-FUTURES (:1413, :1429, :1517); venues.py:276 selects the swap
market. Doors on Telegram: /trade parses `buy SOL 71.42 sl 70.05 tp 76.42
margin 250` into a Confirm card that places nothing until tapped
(trading_commands.py:1055); signal cards from /analyze, /scan and the pro scans
carry Take/Limit buttons; /positions, /livepositions, /orders read the book;
/leverage and /venues configure it. On the web: POST /api/trade/propose then
/confirm, 2FA-stepped-up, re-running the engine risk gate (webtrade.js:116).
Autonomously: engine.py:5562-5620 confirms and executes any idea at or above
RUNTIME.auto_confirm_threshold with no human tap.

*Gap.* Live is operator-gated and off by default — SIMULATION_MODE defaults True and
LIVE_TRADING_ENABLED defaults False (config.py:2335-2336), so a stock deploy
trades perps on paper until the operator runs /golive. A real order
additionally needs _can_trade_live (telegram_handler.py:3930), which requires
BOTH the env allowlist and the per-user store flag; web-only `web:<id>`
identities are structurally paper-only and can never pass it. Venue coverage
is Bitget (primary) with Bybit/Hyperliquid adapters; long/short perps only —
no spot, no margin, no cross-vs-isolated choice surfaced.

The margin BOUNDS can be derived from the account. `bot/core/size_bounds.py`
answers a per-trade and a total ceiling plus a capital reserve from the
AVAILABLE balance the venue reported, three-valued (`flat` / `unread` /
`balance`); `live_executor.size_bounds_for` is the one reader the preflight,
the clamp, both engine execution caps and the live-portfolio card ask.
Default OFF (`SIZE_BOUNDS_ENABLED`), and arming it alone can only TIGHTEN —
the two growth ceilings default to the flat caps, so raising a bound needs
`SIZE_BOUNDS_MAX_POSITION_USD` / `SIZE_BOUNDS_MAX_TOTAL_USD` as well. With the
flag off the preflight still records what the balance-relative bounds would
have done to every live order (`bot/core/bounds_shadow.py`,
`data/bounds_ledger.json`), and `/shadow bounds` renders it: how often the
would-be per-trade bound would have cut the order, how often the total bound
would have refused it, per account, with the balance-unread rows counted apart.

TRADE QUALITY chooses a size and a leverage within those bounds
(`bot/risk/quality_ladder.py`). The analyzer's MEASURED confidence lands on a
rung of a `name:floor:size_mult:leverage_mult` table (`QUALITY_LADDER_RUNGS`,
default `A:0.85:1.0:1.0,B:0.70:0.75:0.8,C:0.0:0.5:0.6`). The size half
multiplies the pre-cap figure AND the notional cap — pre-cap alone is clamped
straight back, the USER_RISK_PREF lesson — and the leverage half writes a
reduce-only cap onto the idea through the same attribute the margin-risk cap
uses, so the VENUE is set to the rung's leverage and the margin-risk verdict is
measured at it (the sizing leverage cancels out of that ratio, which is why the
cap had to reach the venue first). A manual ticket's confidence is a stamp
(`build_manual_idea` writes 1.0 on every one), so it takes no rung, and the
same reading now decides Kelly's confidence factor (default ON, tighten-only)
and the high-conviction floor. A table with a multiplier above 1.0 is refused
rather than clamped: growth is what `SIZE_BOUNDS_MAX_*` is for. Both halves
default OFF (`QUALITY_LADDER_SIZE_ENABLED`, `QUALITY_LADDER_LEVERAGE_ENABLED`)
and SHADOW when off — the risk gate audits the rung it would have applied with
`result="SHADOW"` and records every sized evaluation in `data/ladder_ledger.json`
(`bot/risk/ladder_shadow.py`), which `/shadow ladder` renders: per-rung counts
with their span and the engine that evaluated (the shared operator engine, or a
per-user one by its user), what each half would have cut and what it did cut
where it is on — the audit line alone was a shadow nobody could read back. The check and
both fill cards carry the size's BASIS
(`bot/core/size_trace.py`): which step decided the figure, how many steps it
took, and whether something off the record changed it after.

**Funding rate farming** — partial

A complete MEASUREMENT layer and an explicitly paper P&L tracker, with no
capture. /funding (market_commands.py:71, ungated) shows one perp's 8h rate
and annualized rate across Bitget/Bybit/Hyperliquid with the cross-venue
spread and a crowding read. /fundingscan (:149) does the same for many coins
at once, widest spread first, and names the delta-neutral direction. /arb
(:241) runs bot/core/arb_tracker.py, which accrues hypothetical carry on a
FIXED $1,000 delta-neutral notional over recorded hourly spread snapshots,
prints the fee reality check (0.24% of notional for four taker legs,
arb_tracker.py:50) and a VERDICT over the record — `arb_verdict`, four
outcomes: survives fees (the whole 95% interval on the per-entry net carry
above zero, past floors of 10 closed entries and 72h held), does not survive
fees (the whole interval below zero), record too thin (a floor unmet, or an
interval that straddles zero), or the record could not be read (kept apart
from "no history yet"). An on-period still running at the last snapshot is
counted and never scored. The same reports feed the dashboard's c-xfunding
and c-arb panels, and the verdict rides the public reports payload in percent
of the notional, in the bot's own sentence, which the panel prints and never
derives. /arbpair BASE [usd] (trading_commands.py, `trade`) is the first
reading past measurement: for one coin it takes the radar's two legs, asks
the CALLER's own credential store which of the two venues they linked,
reads each leg's equity through the same read-only balance snapshot
/connect validates with (bot/core/funding_arb.py — six-valued per leg:
read, unpriced, unreachable, not linked, unreadable, store unavailable),
sizes both legs to the smaller equity capped at the requested notional
(default the tracker's $1,000), prints the round-trip fee on that size, the
break-even hold at today's spread and the record's verdict beside it, and
PLACES NOTHING — a leg that could not be sized leaves the pair unsized
rather than half-hedged, and the card says what the message did.
app/lib/venue_router.js recommends the cheapest venue to hold a given
side by funding cost. Funding also genuinely affects live trading —
analyzer.py:1612 applies funding_cost_haircut to blended confidence, and
risk/funding_clock.py times settlements.

*Gap.* Nothing opens, hedges, rolls or closes a funding position. The
proposal card is the last reading before an execution path, and there is no
flag, button or executor for one yet — deliberately: a flag read by nothing
and a button leading to "not built" are both doors painted on a wall.
arb_tracker.py:16 states it outright: "Strictly paper: nothing here places,
sizes, or even proposes an order," and venue_router.js:3 says it "never
places, routes, or re-routes an order — auto-routing is a separate operator-
gated decision that does not exist in this codebase." No per-leg margin
management and no automated entry at a spread threshold. The verdict is the
evidence layer's own answer to whether a real capture strategy is worth
building; the proposal card that sizes both legs and places nothing SHIPPED
as /arbpair (described above), so what is unbuilt here is EXECUTION — a
decision after shadow evidence, not a card.

**Basis trades** — partial

Basis is COMPUTED and read, never traded. bot/core/basis.py's BasisAnalyzer is
constructed at engine.py:686 and fetched in `_analyze_signal`'s context gather
(engine.py:6420) — its
result is handed to analyzer.analyze at :6513 as `basis` CONTEXT that votes on
nothing. Its own docstring (basis.py:16-30) records that it had no caller
outside tests until recently and that a fabricated `basis_pct * 365`
"annualized" field was removed rather than propagated. On the web,
app/lib/spot.js exposes getSpotPerpBasis (route /api/spot/basis,
routes/spot.js:11 — no browser caller; the reachable doors are the chat 'spot'
intercept at chat.js:97 and /spot on Telegram, the same card), and app/lib/dex.js renders a DEX↔CEX basis
(Hyperliquid mids vs this venue's perp price, as delta_bps) into the Markets
view's c-dex panel.

*Gap.* No basis position exists as an object anywhere — no cash-and-carry leg
pairing, no spot leg (there is no spot execution at all), no roll, no
convergence tracking, no P&L. dex.js:1-10 states the boundary: "Public info
API only (no keys, no account, no orders) — non-custodial DEX execution
remains design-only pending operator + legal review."

**CEX/DEX arbitrage** — partial

A read-only price comparison, and it says so in its own header. app/lib/dex.js
fetches Hyperliquid `allMids` and puts them beside this venue's perp tickers
for eight majors, emitting a per-base delta in basis points (buildCompare,
dex.js:46); it is served at GET /api/market/dex and rendered in the dashboard
Markets view's c-dex panel. venue_router.js:26 folds that DEX basis into the
cheapest-venue-to-hold read alongside the cross-venue funding scan.

*Gap.* Read-only by explicit decision: dex.js:1-10 — "Public info API only (no keys,
no account, no orders) — non-custodial DEX execution remains design-only
pending operator + legal review" — and venue_router.js:3-7 — "It never places,
routes, or re-routes an order." There is no DEX order execution (the only on-
chain swap builder, meme_swap.py, is a single-leg Solana buy behind two
default-off flags, not an arb leg), no inventory or rebalancing model, no
bridge/settlement latency model, and no paper P&L for a price arb — the only
arb with a paper tracker is the funding one.

**Copy trading** — partial

Two real doors, both paper. (1) Arena practice-follow: POST /api/arena/follow
(arena.js:904), toggled from the Follow control at arena.html:1326, stores
enabled/margin/leverage; sweepFollows (arena.js:145-200) then AUTOMATICALLY
opens each new engine signal as a position in the caller's virtual 10,000
vUSDT Arena account (INSERT at arena.js:188), inheriting the signal's own stop
and target when they are still valid against the live fill. That is genuine
automatic mirroring — of the house engine, in virtual money. (2) Strategy-
agent follow: /api/copy (copy.js) follows a published engine agent or a
community strategy and returns a "would-take" picks feed built by applying
that agent's published gates to the live signal stream, surfaced in the
dashboard Agents view. Users can also publish their own strategy CONFIGS to
the marketplace (/api/strategies) and pin one to their own confirms
(/mystrategy, trading_commands.py:411).

*Gap.* No real-money copying anywhere, and no copying of another HUMAN's live trades.
copy.js:11-17 states it: "follow is a bookmark + a personalised would-take
feed. It moves NO funds and places NO trades — copying is user-initiated and
paper-only via the normal trade ticket." The Arena auto-follow mirrors the
engine's own signals, not a leader's fills, into virtual vUSDT; it is a lazy
sweep on account reads with no background job and a 5-signal-per-read cap.
There is no leader/follower allocation model, no proportional sizing off a
leader's equity, no performance-fee or revenue split, and no per-follower
execution on a real venue.

**Algo/bot trading** — **shipped**

The whole product is an algo bot and every layer is reachable. bot/main.py:587
starts engine.run(), the scan→analyze→risk→execute FSM; market_scanner feeds
analyzer, which runs an LLM thesis plus a weighted confluence vote over ~20
signal modules; RiskEngine (risk/risk_engine.py:113) is the fail-closed pre-
trade gate whose whole enforcing set /enforcing lists. engine.py:5562-5620
auto-confirms and EXECUTES any idea at or above RUNTIME.auto_confirm_threshold
(default 0.85, config.py:2395) with no human in the loop, adaptively moved by
realized win rate (:5302) and suppressible in live mode. Operators tune it
with /autoconfirm, halt it with /halt //pause //emergency_stop, and inspect it
with /risk, /gates, /shadow, /enforcing, /parity. Users get four named
strategy presets (Dip Sniper, Momentum Hunter, Safe Scalper, Full Scan —
skill_registry.py:1906) runnable via /run, /momentum, /dip, and pinnable to
their own confirms as a tighten-only veto (/mystrategy →
user_strategy_store.py:30, mirrored on the web at /api/bot-strategy). Research
rails exist and are wired: /backtest, /walkforward, /optimize, and the browser
Strategy Lab over frozen benchmark snapshots (bot/api/lab.py:46).

*Gap.* On a stock deploy the loop runs on paper — SIMULATION_MODE defaults True
(config.py:2335) — so "the bot trades for you" is live only after the operator
runs /golive and the caller passes _can_trade_live. Users cannot author
strategy CODE: the presets are a fixed four-row table plus threshold fields,
and published community strategies are declarative rule configs, not
executable logic. Several tier features (backtest/walkforward/optimize at
elite) nominally gate behind $RCLAW, though that gate is off by default.


### Long-Term Capital

| Leaf | Today | Doors |
|---|---|---|
| Core portfolio holding | partial | `/livebalance`, `/networth`, `/holdings`, `/exposure`, `/wallet`, `/classpf`, `/api/networth`, `/api/holdings`, `/api/wallet/portfolio`, `/api/tax/report` |
| Seed/private rounds | — | — |
| Public sales (ICO/IDO/IEO) | — | — |
| Governance token accumulation | — | — |
| Index/basket strategies | — | — |
| Angel investing in crypto startups | — | — |

**Core portfolio holding** — partial

READ-ONLY tracking of what you already hold, and it is genuinely wired on both
surfaces. /livebalance fetches the caller's own linked-venue futures balance
AND enumerates spot holdings, pricing each against a live ticker
(account_commands.py:822; an unread holdings list is None, never [], so
'nothing held' and 'nothing read' stay apart). /networth and /holdings
aggregate CEX venues plus SIWE-linked wallet chains, one row per source, with
an explicit error row for any source that would not read (holdings.js:1 header
states the rule). /exposure nets perp positions against on-chain spot.
/classpf buckets realized performance by asset class. The tax report splits
realized round-trips at the conventional 365-day short/long-term line
(tax.js:21, :91) — the only place in the tree that models a holding period at
all.

*Gap.* There is no way to ACQUIRE or hold a position as long-term capital. /buy and
/sell are hard-disabled with 'Spot trading is disabled — RUNECLAW operates in
futures-only mode' (trading_commands.py:998, :1007); the engine, live_executor
and every confirm path place USDT-M perps only. app/lib/spot.js is read-only
by its own header ('nothing in this module places orders') and its
reachable consumers are the chat intercept at chat.js:101 and /spot on
Telegram, which fetches that intercept's own card — the /api/spot/*
routes have no caller in the tree. There is no cost-basis lot ledger:
tax.js:5-9 says so outright ('There is no spot-lot ledger to match across, so
forcing FIFO cost-basis matching onto already-matched round-trips would
MISREPRESENT the data'), so the long-term split is computed over bot-booked
perp round-trips, not over held assets. No DCA, no accumulation schedule, no
target allocation, no rebalancing a user can trigger.

*The verifier refused part of this row.* partial STANDS — but two of the cited evidence items are wrong about what they
read


### Yield & DeFi

| Leaf | Today | Doors |
|---|---|---|
| Staking (native + liquid) | partial | `/api/idleyield`, `/api/defi`, `/defi`, `/idleyield`, `/yield`, `/api/dapps` |
| Restaking | — | — |
| Lending/borrowing spreads | partial | `/api/idleyield`, `/api/defi`, `/defi`, `/api/crossyield` |
| LP provision | partial | `/api/defi`, `/defi`, `/escape`, `/api/dapps` |
| Yield farming | — | — |
| Delta-neutral vaults | — ⟲ | `/fundingscan`, `/arb`, `/funding`, `/api/reports` |
| Stablecoin yield strategies | partial | `/api/idleyield`, `/stake`, `/unstake`, `/yield`, `/api/staking/fixed`, `/api/reports/yield` |
| Structured products | — | — |

**Staking (native + liquid)** — partial

LIQUID staking only, and only as a read. A signed-in web user gets (a) live
liquid-staking RATES — Lido stETH and Rocket Pool rETH, from DefiLlama
apyBase, behind a curated allowlist and a $20M TVL floor — matched against
their linked wallet's idle ETH by the idle-yield optimizer (/api/idleyield,
panel c-idleyield, and the 'idleyield' chat intercept); and (b) a POSITION
read of their Lido stETH mainnet balance via /api/defi, priced at the ETH
ticker with the approximation stated. Telegram's /yield and /idleyield are the
same reads for the OPERATOR account and are gated `_is_admin` at
yield_commands.py:58 and :142. Lido/Rocket Pool/ether.fi also appear in the
/api/dapps directory as outbound deep-links to their own sites.

*Gap.* Nothing stakes. There is no staking action on any surface — no delegation, no
deposit, no unstake, native or liquid; the user is told a rate and sent away.
NATIVE staking is absent entirely: no validator delegation, no Solana stake
accounts, no ETH solo staking, and no non-Lido/RocketPool LST. The
rclaw_staking Anchor program (devnet) is not a counterexample — I grepped its
four instruction names and every transaction sender is a test harness;
bot/token/tier_gate.py:438 only READS StakeAccount bytes via
getProgramAccounts, so a user cannot stake $RCLAW from Telegram, the web, or
any CLI in the repo. The only staking-shaped EXECUTION in the product is
Bitget Earn CEX savings, stables-only, on the Bitget account a trader linked
with /connect (see the stablecoin row).

*The verifier refused part of this row.* partial stands for the liquid-staking READ (that half I confirmed end to end),
but two of the seven listed doors are not doors. (1) tier_gate.py:438 is
`staked_of`, a read of $RCLAW stake for TIER GATING inside a module whose
first line is "DRAFT, feature-flagged OFF by default"
(TOKEN_TIER_GATE_ENABLED, RCLAW_MINT, RCLAW_STAKING_PROGRAM all unset by
default, devnet assumed, "nothing here signs or holds a key") — it supports no
staking capability for anyon…

**Lending/borrowing spreads** — partial

Both halves of a lending position are visible and the spread between them is
not. SUPPLY: Aave v3 supply APY for USDC, USDT, DAI and WETH, read as apyBase
from DefiLlama and ranked by the idle-yield optimizer for a signed-in user's
idle stables/ETH. BORROW: app/lib/defi.js calls Aave v3
Pool.getUserAccountData across five chains and renders totalCollateralBase,
totalDebtBase, availableBorrowsBase and healthFactor, with CRITICAL/thin
liquidation warnings below 1.1 and 1.5 — so an existing loan is monitored.
/api/crossyield adds a gas+bridge payback model for relocating idle capital to
a better supply rate.

*Gap.* No borrow RATE is read anywhere in the tree — I grepped bot/, app/lib and
app/routes for borrow APY/APR/rate, apyBaseBorrow, variableBorrowRate and
borrowAPY and got zero hits — so no supply-vs-borrow spread, no carry-trade
calculation and no rate-differential alerting is ever computed.
`availableBorrowsBase` is a headroom dollar figure, not a price of credit.
There is also no borrowing or repaying action: defi.js's own header says no
signing surface exists behind that router. Morpho, Compound and Spark are
directory links only (app/lib/dapps.js:30-33). Only Aave v3 is read; only four
assets have a supply rate.

**LP provision** — partial

One number. defi.js calls balanceOf on the Uniswap v3
NonfungiblePositionManager on five chains and renders 'N LP position(s) —
counted, not valued', with the module stating in its own header and payload
note that it will not fake fair LP valuation because that needs tick math. The
Guardian escape planner has an `lp` unwind step ('Remove liquidity from',
order 3, impermanent-loss reason) — but its wallet import produces only
spot/bridged rows and tells the user in as many words that 'the mirror sees
spot only — add perps, loans, LPs and staking yourself', so every LP row there
is hand-typed by the user.

*Gap.* No pool discovery, no pair identity, no LP value, no fee accrual, no APR, no
impermanent-loss measurement, no range/in-range status, and no add/remove-
liquidity action on any surface. A user learns only that some number of
Uniswap v3 NFTs exists under their address. Curve, Aerodrome and PancakeSwap
are directory links; nothing reads a position on them.

*The verifier refused part of this row.* partial stands (the read is real and reachable), but the number is NOT a
position count. readUniswapCount calls balanceOf on the Uniswap v3
NonfungiblePositionManager with an ERC20 ABI — that is the number of position
NFTs the wallet holds, which still includes every NFT whose liquidity has been
fully withdrawn but not burned. A wallet that has closed every LP renders "3
LP position(s)". It is also Uniswap-v3-only: every ERC20-LP protocol in the
product's …

**Delta-neutral vaults** — nothing serves this ⟲ — the verifier overturned *partial*

The research substrate for a delta-neutral funding carry, read-only.
/fundingscan and the funding radar compute annualized funding across Bitget,
Bybit and Hyperliquid, rank by spread, and print the delta-neutral leg per
coin ('long {cheapest} / short {richest}') with the caveat that this is what
the pair 'would collect, before fees/slippage. Read-only radar — no orders are
placed.' /arb is a strictly-paper carry tracker: hourly spread snapshots
accrued on a fixed $1,000 notional, breaking the paper position across gaps
over 3h, with a 0.24% round-trip fee reality check. Both are reachable by any
allowlisted trader/paper/viewer via `_guard(update, "status")`; the web
mirrors them as the c-xfunding and c-arb panels off /api/reports.

*Gap.* There is no vault. No deposit, no share/receipt token, no managed position, no
manager, no NAV, and no execution of either leg — /arbpair (bot/core/funding_arb.py)
now sizes and proposes the pair over the caller's linked venues and places
nothing; arb_tracker.py's own header still holds for the tracker itself
('nothing here places, sizes, or even proposes an order'), and
the roadmap framing is explicit that this is the evidence that gates whether a
real capture strategy is worth building. A user who reads the card has to open
both legs by hand on two venues. No basis-trade (spot/perp) vault either —
bot/core/basis.py is analyzer context that votes on nothing.

*The verifier refused part of this row.* none. Every door named is real, reachable and correctly gated — I confirmed
all four — but none of them is a delta-neutral vault, or any delta-neutral
position at all. Nothing in the tree opens, sizes, hedges, proposes or holds a
paired position, and no user capital of any kind is involved: the $1,000 in
/arb is the module constant PAPER_NOTIONAL_USD accrued against recorded spread
snapshots, not a deposit. What these doors actually serve is cross-venue fu…

**Stablecoin yield strategies** — partial

Two different products under one word. On the website, for a signed-in
user: READ ONLY — the idle-yield optimizer matches their wallet's idle
USDC/USDT/DAI to the best Aave v3 supply rate (non-custodial preferred
honestly over a marginally higher custodial CEX rate) and says so; the
module's own first lines are 'RECOMMEND, never auto-deploy' and
'RECOMMENDATION-ONLY — never moves funds'. On Telegram, real execution into
Bitget Earn savings on the CALLER's OWN account: /stake shows a plan over the
idle stables of the Bitget account they linked with /connect (an admin who
linked none gets the operator's, as before), and the yld:s callback calls
yield_radar.execute_stake with THAT account's client, re-clamped from live
balances at press time and holding a 30% margin reserve; STAKEABLE_COINS is
exactly ('USDT','USDC'); /stake fixed is a two-step lock whose second confirm
must echo the exact lock end date; /unstake redeems. The permission is
`stake`, held by trader and admin — a self-admitted paper user and a viewer
are refused by role — and the plan card, the button's owner tag and the
sealed record all name the account acted on (bot/core/earn_account.py is
the one reading, eight states, asked again at press time so a plan over one
book cannot execute against another). The web twin POST /api/staking/fixed
stays operator-only — authMiddleware, a TOTP step-up, and `_is_admin_id` 403
in the gateway — and /api/reports/yield requires plan==='admin'; /yield and
/idleyield are still admin-only reads of the operator's book.

*Gap.* The custodial execution is Bitget Earn flexible/fixed savings, and
Bitget only: a caller linked to Bybit or BingX is told so and nothing moves
(their Earn is unserved — recorded rather than guessed at). The website's own
staking route is still the operator's. Not on-chain: nothing supplies to
Aave, nothing enters a stablecoin vault, and non-custodial stablecoin yield
is recommendation-only end to end. Coverage is four assets (USDC/USDT/DAI on
Aave v3, plus whatever Bitget Earn lists); no sDAI/sUSDS, no Ethena, no
T-bill/RWA stable yield, no Curve/Convex stable pools.


### Points & Rewards Meta

| Leaf | Today | Doors |
|---|---|---|
| Airdrop farming | partial | `/airdrops`, `/api/airdrops`, `/api/airdrops/me` |
| Testnet/mainnet grinding | partial | `/airdrops`, `/api/airdrops`, `/me` |
| Points programs | — ⟲ | — |
| Referral loops | partial | `/api/auth/referrals`, `/api/public/invite/:code`, `/start`, `/api/public/duel/squads`, `/duel` |
| Node/DePIN rewards | — | — |
| Loyalty/quest platforms | partial | `/api/command`, `/command`, `/duel`, `/learn`, `/leaderboard`, `/arena` |

**Airdrop farming** — partial

A real, reachable READ-ONLY radar. Five hand-curated campaigns (curated_at
'2026-01') each with project type, chains, status, cost, effort, requirements,
a human checklist and the official link. Logged in, GET /api/airdrops/me adds
eligibility HINTS computed from the caller's own SIWE-linked wallet — per
chain, 'already holds funds, gas is covered' vs 'no readable funds, you would
need to bridge first' (app/lib/airdrops.js:139) — and the module's own
docstring is explicit that a hint is a fact about the wallet, never a claim of
qualification. Operators can swap the catalog without a deploy via
AIRDROP_CATALOG_PATH, and a broken file falls through to the seed rather than
blanking the radar (app/lib/airdrops.js:122-131). Five doors render it; the
chat reply restates the anti-sybil line itself, and /airdrops on Telegram is
that same chat card, fetched rendered over the bot-secret sync channel
(market_commands.py), so the line reaches Telegram verbatim.

*Gap.* The farming half does not exist and is a stated product line, not an omission:
app/lib/airdrops.js:10-17 refuses automated participation, transaction
signing, wallet generation, multi-wallet orchestration and activity generated
solely to qualify; ANTI_SYBIL_NOTE (:161) ships that refusal to every caller.
There is no endpoint that performs, signs or schedules any step
(app/routes/airdrops.js:8-10). Nothing tracks which steps a user has
completed, nothing reads an allocation or a claim, and the catalog is a static
snapshot the card tells you to re-verify on the official link. The Telegram
door, /airdrops, is the web card and nothing more: a caller whose Telegram
account is linked to a web account gets their own wallet-readiness hints,
anybody else the public radar — never a guessed wallet.

**Testnet/mainnet grinding** — partial

The same radar carries both halves. Two testnet campaigns — monad-testnet
(:37) and megaeth-testnet (:55) — with checklists that say to verify the URL
on verified socials, claim from the OFFICIAL faucet ('never pay for testnet
tokens'), use the network genuinely and keep to one wallet. Two mainnet-
activity entries — base-onchain (:72, 'gas-only') and arbitrum-open (:108) —
whose steps point at the governance forum and say to use protocols you'd use
anyway. A 'testnet' chain key short-circuits the wallet hint to 'free faucet
funds; your linked wallet works as-is' (:143-145), and the panel has a
dedicated 'testnet' status chip (dashboard.js:1509-1519).

*Gap.* Informational only, and deliberately. Nothing calls a faucet, submits a
transaction, or measures on-chain activity on any testnet; the wallet hint
reads BALANCES on mainnet chains and nothing at all on testnets. No record of
which steps a user performed, no streak/progress over a campaign, no re-check.
Every entry's notes line says an airdrop is not promised (:52, :68, :86). Same
absence of any Telegram or bot-side door.

*The verifier refused part of this row.* partial stands on the catalog content, but one cited door is a DEAD BRANCH. No
shipped campaign has status 'testnet'. SEED_CATALOG's five statuses are live,
live, expected, points, live (airdrops.js:41, :59, :76, :92, :112); both
testnet campaigns (monad-testnet :37, megaeth-testnet :55) carry status 'live'
and express testnet-ness through chains:['testnet'], not status. So
statusChip's 'testnet' row — along with 'active', 'claim', 'confirmed',
'snapshot' …

**Points programs** — nothing serves this ⟲ — the verifier overturned *partial*

One curated entry: hyperliquid-ecosystem (:88), status 'points', costs
'capital', effort 'medium', whose steps are 'research each HyperEVM app's
points program on its official docs', 'deploy only capital whose loss you can
absorb — points never justify bad risk' and 'track your positions in the
Portfolio view like any other exposure', with a note that the base HYPE
airdrop already happened. The dashboard renders a distinct blue 'points' chip
for that status (dashboard.js:1515), so a points-farming campaign is visually
separated from a live claim and from a taken snapshot.

*Gap.* Thin, and it reads other people's programs rather than running one. No points
BALANCE is fetched, stored or displayed for any program — I grepped the
lib/route/panel layer and the only per-user read on this path is wallet funds-
by-chain (app/lib/airdrops.js:139). No accrual estimate, no snapshot tracking,
no per-app breakdown, no notification when a program changes. RUNECLAW runs no
points program of its own: the nearest thing is the $RCLAW tier gate, which
maps an on-chain STAKE (not points) to a feature tier, is OFF by default
(needs TOKEN_TIER_GATE_ENABLED + RCLAW_MINT, neither set anywhere), and has no
staking client on any surface — so it is not a points program and it is not
on.

*The verifier refused part of this row.* none (an advisory mention, not a points capability). The door is real and the
chip does render, but the product measures, tracks and stores ZERO points of
any kind. A whole-tree grep across app/lib, app/routes and bot/ for points
state returns exactly two hits, both the same literal: the docstring listing
the allowed status vocabulary (airdrops.js:31) and the one entry that uses it
(airdrops.js:92). There is no points balance, no per-program tracker, no po…

**Referral loops** — partial

The loop itself is wired end to end on BOTH surfaces. An 8-char random, non-
enumerable code is minted and back-filled (auth.js:405, :901); registration
with a valid ?ref= credits users.referred_by, refusing self-referral
(auth.js:588-602); the Account panel shows the link, a live 'N joined' count
and three share buttons (dashboard.js:5210-5258); an anonymous ?ref= landing
resolves the referrer's public handle only, 404s unknown codes and is rate-
limited and cached (public_invite.js:25). Telegram is covered too: /start
parses the ref_ payload and writes it write-once, refusing self-referral
(start_commands.py:163-166 → user_store.py:659). And one perk is genuinely
backed — app/lib/duel_squads.js builds Daily Duel SQUADS out of exactly this
referral graph, served by GET /api/public/duel/squads (public_duel.js:101) and
rendered on /duel.

*Gap.* It pays nothing, and the code says so in its own comments. Of the five
REFERRAL_TIERS (auth.js:435-452), only Starter and Connector carry state
'live'; Advocate/Ambassador/Legend are state 'planned' with a `requires` line
saying so — 'nothing in the product is weighted by referrals today', and fee
credits and revenue share 'would ride on the $RCLAW token, which does not
exist yet… Not an offer.' The comment above the table states that
`referralTier` has exactly one caller — the endpoint that prints it — and
nothing in the tree gates a feature on a referral count; I confirmed by grep
that the only readers of referred_by are the count query, duel_squads and
account_erasure. No commission, no rev-share, no credit, no tier discount is
computed or paid anywhere.

*The verifier refused part of this row.* partial stands for the WEB surface only. On Telegram the loop is redemption-
only, and what it redeems is read by nothing. Three independent checks: (1)
record_referrer writes record['referred_by'] into the JSON user store
(user_store.py:680) and a whole-tree grep for readers of that key returns only
the writer's own guard at user_store.py:676 and four assertions in
tests/test_share_invite.py — zero non-test readers, which is this repo's own
unreachable-mod…

**Loyalty/quest platforms** — partial

RUNECLAW ships its OWN quest/loyalty layer and it is reachable, not
scaffolding. The Command Deck (routes/command.js, authMiddleware at :24,
mounted server.js:436, page server.js:476, linked from /arena, /duel, /learn,
/rune, /explore, index.html and the palette) renders on one screen: a paper-
Arena close streak with a grace rule (arena_streaks.js:25), three weekly
quests rotated deterministically by ISO week from QUEST_POOL (:47, :80), exit
discipline, a diary streak (achievements.js:18), lesson progress, the daily
rune and ten achievement glyphs. The honesty posture is unusually strong:
achievements are pure predicates recomputed from records on every read, never
grant tables (achievements.js:1-11), so the deck 'can never show a trophy the
records don't back', and the route's header pins that no balance, equity or
dollar rides the payload. Alongside it the Daily Duel (a daily pick with a
90-day record) and /learn (lessons + a per-day trading diary) are real
recurring-engagement loops.

*Gap.* Two distinct gaps. (1) ZERO integration with any external loyalty/quest
platform — grep for galxe, layer3, zealy, quest3, crew3, guild.xyz and taskon
across the whole tree returns nothing, so none of this earns points or rewards
anywhere off-platform. (2) Internally it pays NOTHING: quests, streaks, glyphs
and duel marks are counts and facts only, with no token, credit, fee discount
or payout attached, and the quests are computed over PAPER arena closes. The
one badge that would be an on-chain loyalty asset — the free, one-per-wallet,
permanently soulbound RuneOfEntry ERC-721 (contracts/rune/RuneOfEntry.sol:27)
— is NOT DEPLOYED: NFT_CONTRACT_ADDRESS appears nowhere (not even in
.env.example), so nft.js:89 returns null and buildMintPlan answers ready:false
with the reason 'NFT_CONTRACT_ADDRESS is not set (contract not deployed yet)'
(nft.js:165), and the deck's rune read answers null.

*The verifier refused part of this row.* partial, but ONLY for RUNECLAW's own in-product engagement layer; coverage of
the loyalty/quest-PLATFORM meta is zero. A whole-tree grep for galxe, layer3,
zealy, quest3, crew3, guild.xyz and taskon returns ZERO hits in any .js, .py,
.html or .md — the product neither integrates with, tracks, nor mentions a
single external quest platform. Everything the row cites is verified reachable
and I confirm it (routes/command.js authMiddleware :25, mounted server.j…


### Attention Economy

| Leaf | Today | Doors |
|---|---|---|
| Kaito-style mindshare farming | — | — |
| Creator campaigns | — ⟲ | `/api/auth/referrals`, `/api/public/invite/:code`, `/api/share/card`, `/broadcast`, `/channel` |
| Bounty boards | — | — |
| Research-for-hire | — | — |
| Data labeling for AI/crypto | — | — |
| Reputation-based rewards | partial | `/api/reputation`, `/dashboard`, `/api/web3/profile`, `/api/arena/account`, `/arena`, `/api/command`, `/command`, `/leaderboard`, `/track`, `/api/public/agent/:address` |

**Creator campaigns** — nothing serves this ⟲ — the verifier overturned *partial*

A referral/invite program and share tooling, not campaigns. A signed-in web
user gets a personal invite code and a count of who signed up on it
(app/auth.js:896, rendered in the Account view's c-ainvite panel at
dashboard.js:4506), can share a dollar-free PNG close card (/api/share/card),
and a visitor arriving on /api/public/invite/:code is credited to the
referrer. On Telegram the operator — or, as I read the body, any Telegram
group admin/creator of the chat it is run in — can push text to the marketing
channels with /broadcast (access_commands.py:334) and manage those channels
with /channel. Exactly one referral perk is real and I verified it:
app/lib/duel_squads.js:5,92 builds Daily Duel squads out of users.referred_by,
so one recruit really does put you both on a squad.

*Gap.* There is no campaign anywhere: no brief, no sponsor, no submission, no content
tracking, no creator leaderboard and no payout. The reward half of the
referral ladder is explicitly dead, and the code says so itself — three of the
five REFERRAL_TIERS rows are state:'planned' with a `requires` line reading
'Not in force yet' or 'Would ride on the $RCLAW token, which does not exist
yet' (app/auth.js:443-452), and the comment above the table states 'This
endpoint still grants nothing: referralTier has one caller, right below, and
nothing in the tree gates a feature on a referral count' (app/auth.js:433). I
checked that claim: referralTier's only non-test caller is the endpoint that
prints it. Do not read this row as creator-campaign coverage.

*The verifier refused part of this row.* none. Every door listed exists and is reachable, and not one of them is a
campaign — the row's own evidence sentence concedes it ('A referral/invite
program and share tooling, not campaigns') and the status contradicts that
sentence. Verified: (1) GET /api/auth/referrals (app/auth.js:896,
authMiddleware, mounted server.js:327) returns a code + count, rendered in the
Account view's c-ainvite panel (dashboard.html panel p-ainvite at
dashboard.js:4506, loader…

**Reputation-based rewards** — partial

The SCORING half is shipped and genuinely reachable. A signed-in web user sees
an outcome-based reputation score, grade, four sub-scores and honest red flags
computed only from their realized closed trades (app/routes/reputation.js:22,
mounted at app/server.js:440, fetched by the Reputation view at
dashboard.js:5834; the scorer is app/lib/reputation.js:70 and abstains with
unrated/null rather than a zero). Wallet-native on-chain badges are earned
from what an address verifiably holds (app/lib/badges.js:26 → the Worlds view
panel at dashboard.js:5636). Arena badges are earned from closed paper trades
(app/lib/arena_badges.js:18 → app/routes/arena.js:311). The Command Deck
grants achievement glyphs 'by arithmetic, never by grant tables'
(app/lib/achievements.js, app/routes/command.js:129). Public standing exists
on the leaderboard/track-record boards, and bot/proofofpnl/erc8004.py:52 binds
a fills-derived reputation block into a signed ERC-8004 identity card (which
refuses to attach any reputation to an unpublished statement, and whose
publisher is PROOFOFPNL_PUBLISH_ENABLED=0 by default at .env.example:619).

*Gap.* Nothing REWARDS any of it, and I checked rather than assumed:
computeReputation's only non-test caller in the tree is the route that prints
it (app/routes/reputation.js:49), no permission, tier, fee, limit or feature
anywhere reads a reputation score, badge or achievement, and
app/lib/achievements.js:9 states the posture outright — 'No dollar ever
unlocks anything, no scarcity is manufactured, nothing is for sale.' The one
tier system that does gate features ($RCLAW tier_gate) keys on an on-chain
STAKE, not reputation, and is inert by default. The only reputation-shaped
perks ever written down are the referral ladder's fee credits and revenue
share, which their own rows label not-in-force pending a token that does not
exist. So: reputation is measured and displayed today; it buys nothing.

*The verifier refused part of this row.* partial — STATUS STANDS for the scoring half, but two of the listed doors do
not serve this row and the 'rewards' half is recognition-only. Verified
shipped and reachable: GET /api/reputation (app/routes/reputation.js,
authMiddleware, mounted app/server.js:440) is fetched by a real nav view —
dashboard.js:47 registers { id:'reputation' }, dashboard.js:8703 maps it to
renderReputation, which fetches at dashboard.js:5834 and abstains with
'Unrated' rather th…


### Prediction & Forecasting

| Leaf | Today | Doors |
|---|---|---|
| Sports/political markets | — | — |
| Crypto price markets | — | — |
| Forecasting tournaments | partial | `/duel`, `/api/duel/today`, `/api/duel/pick`, `/api/duel/me`, `/api/duel/season`, `/api/public/duel/board`, `/api/public/duel/squads` |
| Market-making on prediction platforms | — | — |
| Cross-market arbitrage | — | — |

**Forecasting tournaments** — partial

A complete, reachable, publicly-ranked forecasting contest — the Daily Duel —
with ZERO prize and ZERO stake. Three rounds a UTC day, each a symbol plus the
agent's hidden stance; the player calls LONG/SHORT/PASS and is scored over a
24h horizon measured from their own call, beating the agent scoring double
(app/lib/duel.js:5-8, :40-43). Doors, opened and read: Telegram /duel at
bot/skills/start_commands.py:595 (@guard("start"), which `pending` holds, so
the free on-ramp stays reachable by a newcomer while the allowlist gate and the
rate limit are no longer skipped — it carried NO gate at all until 2026-09-18),
registered at
telegram_handler.py:1004, with LONG/SHORT/PASS inline buttons whose taps land
in _handle_duel_callback at start_commands.py:613; the web page at
app/server.js:477 driving the four authed routes at
app/routes/duel.js:43/57/73/98; and the session-free public board and referral
'squads' board at app/routes/public_duel.js:3 (mounted app/server.js:370),
ranked per calendar-month season. Scoring is one pure module both surfaces
share (app/lib/duel.js), accuracy is null-not-zero when nothing resolved and
unresolved rounds are excluded from the denominator (app/lib/duel.js:183-195),
settlement runs lazily in batches on read (duel_service.js:117), and every
pick is cryptographically SEALED at the moment it is made (duel_service.js:195
-> callseal.sealDuelPick, kind 'duel_pick' in docs/PROVABLE_CALLS_SPEC.md:97)
so it rides into the day's Merkle root — a forecast committed before its
outcome is known and re-derivable by a third party.

*Gap.* Income. There is no entry fee, no prize pool, no payout and no token reward
attached to a duel result — the §4 rule at app/routes/duel.js:16 forbids any
currency amount on the surface — so this is a reputation/streak game, not an
earning leaf. Also missing for a real forecasting tournament: no proper
scoring rule (accuracy percent, marks and streaks only; no Brier or log score,
no calibration credit, no confidence-weighted calls), questions are auto-
generated from the agent's own signals plus the majors rather than authored,
the horizon is fixed at 24h, and there is no integration with any external
tournament (Metaculus, Good Judgment, Kalshi contests). The bot scores its OWN
forecast calibration separately (bot/learning/confidence_calibration.py via
/calibration and /readiness) but those are admin-only internals, not a user-
facing tournament.

*The verifier refused part of this row.* STILL partial — the contest itself is real and every door in the row exists
and is reachable, and I confirmed that half independently. But the row's
headline evidence claim is false in both of its load-bearing halves: a duel
pick's seal does NOT ride into any daily Merkle root, and it is NOT re-
derivable by a third party. The seal is computed, written to duel_picks.seal /
.seal_payload, echoed back to the caller's OWN /api/duel/me history, and read
by nobo…


### Mining & Hardware

| Leaf | Today | Doors |
|---|---|---|
| ASIC mining | — | — |
| GPU mining | — | — |
| Pool vs solo | — | — |
| Cloud mining | — | — |
| DePIN hardware ops | — | — |


### NFTs & Digital Collectibles

| Leaf | Today | Doors |
|---|---|---|
| Flipping | partial | `/nft`, `/api/nft/radar`, `/api/nft/wallet/:address`, `/api/web3/collectibles` |
| Minting | partial | `/api/nft/mint-plan`, `/api/nft/stats`, `/rune`, `/api/command` |
| Fractional/lending markets | — | — |
| Royalty income | — | — |
| Collection creation | partial | `/api/contract/studio`, `/api/contract/compile`, `/api/web3/deploy` |

**Flipping** — partial

READ-ONLY market intelligence only. The web chat intercept at
app/routes/chat.js:99 calls opensea.maybeHandleNftChat, which returns the top
collections ranked by REAL seven-day traded volume with floor price and owner
counts (app/lib/opensea.js:60-99), and the same lib backs GET /api/nft/radar
and GET /api/nft/wallet/:address (app/routes/nft.js:47-55). The Worlds view
mirrors the caller's own NFT holdings (app/routes/web3.js:37-58), and
networth.js:118-133 lists collectibles as CONTEXT and deliberately never sums
their floor into the total. The dApps directory links out to
OpenSea/Blur/Magic Eden on their own sites (app/lib/dapps.js:47-49) — a link,
not a capability.

*Gap.* There is NO execution half anywhere in the tree: no listing, bid, offer,
fulfilment, sweep, or NFT transfer path on any surface. opensea.js's own
header states the scope — "No marketplace machinery — no listings, no offers,
no fulfillment, no minting, no wallet credentials" (app/lib/opensea.js:5-9) —
and its radar payload carries "RUNECLAW never lists, bids, mints or trades
NFTs" (app/lib/opensea.js:88). Both HTTP routes (/radar, /wallet/:address)
have no browser caller anywhere in the repo; the chat intercept and /nft on
Telegram — the same card, fetched rendered over the sync channel — are the UI
doors. And the whole surface is inert on a stock deploy: OPENSEA_API_KEY is
commented out at .env.example:756 and set nowhere, so configured() is false
and every reader honestly answers available:false / not_configured rather than
a fabricated radar. The Telegram door, /nft, renders that same honest card
(market_commands.py fetches the web intercept's own rendering), so an
unconfigured key reads 'unavailable' on both surfaces rather than as a radar
on one and silence on the other.

*The verifier refused part of this row.* The STATUS is right and the read-only framing is right — I drove every path:
the intercept table is dispatched in a loop (app/routes/chat.js:208-213), the
nft row is chat.js:99, opensea.CHAT_RE/maybeHandleNftChat are
opensea.js:125-141, /api/nft is mounted at server.js:434,
/api/web3/collectibles is web3.js:37-58 and the Worlds view consumes it at
dashboard.js:5650-5683, networth.js:118-133 lists collectibles and never sums
a floor, dapps.js:47-49 is three…

**Minting** — partial

One complete, genuinely end-to-end mint path is BUILT for a single collection
— the Rune of Entry soulbound signup badge. The contract exists and is tested
(contracts/rune/RuneOfEntry.sol:57 mint(bytes), free/non-payable, one per
wallet, art generated and stored fully on-chain). The server signs an EIP-712
MintVoucher bound to the caller's linked wallet and returns the exact
mint(bytes) calldata plus chain/RPC/explorer (app/lib/nft.js:158-206); it
never signs or sends the transaction. The browser flow is fully wired:
dashboard.js:4703 fetches the plan, :4712 renders the button, :4886-4930
switches the wallet's chain and calls eth_sendTransaction from the USER's own
wallet with their own gas. /rune (rune.html:107) reads GET /api/nft/stats for
a minted count.

*Gap.* NOT DEPLOYED AND NOT CONFIGURED, so today nobody can mint.
NFT_CONTRACT_ADDRESS and NFT_VOUCHER_KEY appear nowhere in the tree except
documentation (app/docs/GOLIVE_RUNBOOK.md:48-49,
contracts/rune/README.md:40,50); there is no .env. contractAddress() therefore
returns null (app/lib/nft.js:89-92), buildMintPlan returns {ready:false,
not_ready_reasons:[…'contract not deployed yet'…]} (app/lib/nft.js:163-173),
the dashboard renders NO mint button at all (the runeBlock is built only `if
(p && p.ready)`, dashboard.js:4707), and /rune prints "The forge is cold — the
collection is not deployed yet" (rune.html:111). Deployment is a manual `forge
create` by the operator. Scope gap even once lit: this is ONE free, one-per-
wallet, permanently SOULBOUND badge (transferFrom/safeTransferFrom revert,
RuneOfEntry.sol:96-98; ERC-5192 locked() is always true, :104-105) — a signup
badge, not mint-for-profit income, and there is no mint calendar, allowlist
tracker, or third-party mint door anywhere.

*The verifier refused part of this row.* partial survives ONLY as "code complete, collection NOT deployed, mint dark by
default". Every cited line exists and the wiring is real, but the door does
not render on any deployment the repo describes: buildMintPlan returns
{ready:false, not_ready_reasons:[…]} unless BOTH NFT_CONTRACT_ADDRESS and
NFT_VOUCHER_KEY are set (app/lib/nft.js:87-98, 155-170), and NEITHER variable
appears in .env.example at all — audit/env_diff.md:277-278 lists both as env
vars …

**Collection creation** — partial

A signed-in web user can DRAFT and COMPILE an NFT collection contract. The
Contract Studio view ships an explicit 'ERC-721 NFT' starter template whose
spec text is "an ERC-721 NFT collection with a fixed max supply, a per-wallet
mint limit, an owner-set mint price, and metadata baseURI — using OpenZeppelin
ERC721 + Ownable" (dashboard.js:6242). It posts to /api/contract/studio
(dashboard.js:6415 → app/routes/contract.js:37 → gateway
handle_contract_studio, user_gateway.py:1225), which is gated by _guard_user
(:1246) — any authorized web caller, NOT admin-only, with a free daily draft
quota — and returns a Solidity draft plus heuristic security flags.
/api/contract/compile (dashboard.js:6374 → contract.js:71 →
handle_contract_compile, user_gateway.py:1342) is likewise _guard_user (:1368)
and runs real solc off the event loop, reporting whether the collection
actually builds.

*Gap.* The draft is where it stops for an ordinary user — nothing lets them DEPLOY a
collection. POST /api/web3/deploy (dashboard.js:6339) reaches
handle_contract_deploy (user_gateway.py:1384), which returns 403 'contract
deploy is admin-only' for every non-admin (:1398-1399), is TESTNET-ONLY with
mainnet refused regardless of any flag, must pass an enforcing Authority
Envelope, and is inert until the operator supplies WEB3_SIGNER_PRIVATE_KEY and
installs eth-account (which CI does not). So a user gets source code they must
take elsewhere to ship. The output is also explicitly a DRAFT with flags,
never an audit — the audit disclaimer travels with every response
(user_gateway.py:1228-1229). No metadata hosting, no art pipeline, no
allowlist/Merkle tooling, no mint-page generator, and no royalty configuration
accompanies it.

*The verifier refused part of this row.* The DRAFT half is upheld exactly as described — I confirmed the 'ERC-721 NFT'
template string at dashboard.js:6242, the #studio view is in VIEWS
(dashboard.js:54) and RENDER (dashboard.js:8701), the POST reaches
handle_contract_studio (contract.js:37 → user_gateway.py:1225), and
_guard_user with NO command argument means any signed-in web caller (auto-
provisioned paper user, user_gateway.py:281-321) — not admin-only, metered by
chat_quota (user_gateway.py:…


### GameFi

| Leaf | Today | Doors |
|---|---|---|
| Play-to-earn | — | — |
| Asset farming/flipping | partial | `/api/web3/collectibles`, `/api/nft/radar`, `/api/nft/wallet/:address` |
| Guild scholarships | — | — |
| Tournament/esports prizes | partial | `/arena`, `/leaderboard`, `/duel`, `/api/arena/season`, `/api/leaderboard/opt-in`, `/api/public/duel/board`, `/api/public/duel/squads`, `/command` |

**Asset farming/flipping** — partial

Read-only market intelligence next to the flip, and nothing that flips. (1) A
web-chat intercept answers 'nft radar'/'opensea'/'floor price' with the top
collections by real 7-day volume, each row carrying floor price in ETH, 7d
volume and owner count (chat.js:99 → opensea.js:127, radar built at
opensea.js:59-96). (2) The dashboard Worlds view (dashboard.js:5559, fetches
/api/web3/collectibles at :5640 → web3.js:38) splits the caller's SIWE-linked
wallet's NFTs into metaverse holdings via a curated slug map — The Sandbox,
Decentraland, Otherside, Voxels, Somnium Space, typed land / name / wearable —
and renders each world as an 'Enter →' deep-link into the official world
(worlds.js:19-38, classifyWorlds :55). (3) GET /api/nft/radar (nft.js:47) and
GET /api/nft/wallet/:address (nft.js:51) are mounted and anonymously reachable
but have no caller anywhere in the repo — raw HTTP only. Scope is stated and
enforced at the source: 'READ-ONLY. ... No marketplace machinery — no
listings, no offers, no fulfillment, no minting, no wallet credentials'
(opensea.js:4-12), and the radar's own disclaimer says 'RUNECLAW never lists,
bids, mints or trades NFTs'.

*Gap.* No acquisition or disposal path of any kind — nothing lists, bids, buys,
sells, mints or transfers, and OpenSea's trade-executing agent skills are
deliberately not used (opensea.js:7-11). No cost basis, no holding P&L, no
rarity or valuation model, no sweep/snipe/alert on floor movement; the Worlds
view prints counts and links with NO price at all. Nothing farms an in-game
asset or currency: no game is integrated, only the metaverse COLLECTION slugs
are recognised. And the whole surface is inert until the operator sets
OPENSEA_API_KEY — commented out at .env.example:756 — in which case every door
honestly answers available:false rather than an empty radar.

*The verifier refused part of this row.* Still partial, but the door list is wrong in both directions. (1) EVERY door
the row names is DARK on a stock deploy: opensea.js:30 `configured()` reads
OPENSEA_API_KEY, .env.example ships it COMMENTED OUT (line 744), and with it
unset getNftRadar returns NOT_CONFIGURED (opensea.js:52-57) — the chat
intercept then replies '🖼 NFT radar — unavailable: the operator has not
configured an OpenSea API key yet' (opensea.js:130-134),
/api/web3/collectibles returns…

**Tournament/esports prizes** — partial

The COMPETITION half ships and is genuinely reachable on both surfaces; the
PRIZE half does not exist anywhere. Arena seasons are real operator-authored
time windows over the paper book — 'a NAMED TIME WINDOW over the existing
Arena, never a reset', ranking percent return from trades closed inside the
window (arena_seasons.js:3-11), with rule variants a live season enforces
server-side (max leverage 1-20, majors only: arena_seasons.js:27-50, enforced
at arena.js:173/444/587). GET /api/arena/season is public and returns status
plus in-window standings (arena.js:990-1014), with the wrong-season trap
already fixed by pickCurrentSeason (:979-987). Alongside it: an opt-in
anonymous ranked leaderboard showing handle, return %, trade count and win
rate and never a dollar (leaderboard.js:1-10), the Daily Duel with a 90-day
record and referral 'squads' board (duel.js:3-17, duel_squads.js), and the
Command Deck's streaks/weekly quests/achievement glyphs. Telegram doors: /duel
(start_commands.py:595, @guard("start")), /leaderboard (:640) and /arena (:667).

*Gap.* No prize, purse, payout, entry fee, wager or token/NFT award exists in any of
it — grep for prize/reward/payout across arena.js, arena_seasons.js,
arena.js's engine, leaderboard.js, duel*.js and achievements.js returns only
'reward:risk' ratio text; the single use of the word 'prize' in the tree is
bot/formatters/board_cards.py:72 naming a promo prize footer as the
HYPOTHETICAL future leak its dollar guard exists to catch. Rank, glyph and
streak are the entire reward. Season creation and deletion are adminOnly and
API-only with no UI (arena.js:1069, :1095, :1149, :1178), so a user cannot
host a tournament; there is no bracket, no team registration, no
entry/settlement of stakes, and no esports data or integration of any kind.

*The verifier refused part of this row.* The STATUS is upheld — I drove the citations and the tournament half is real
and reachable, the prize half is zero (board_cards.py:66-72 even names 'a
promo footer naming a prize' as the plausible FUTURE leak this surface guards
against). Three corrections to the row as written. (1) '/squads' IS NOT A
DOOR. app/server.js serves exactly /command:476, /duel:477, /leaderboard:501
and /arena:514 — there is no /squads route anywhere. Squads is a SECTION
inside …


### Content & Personal Brand

| Leaf | Today | Doors |
|---|---|---|
| X/Twitter | partial | — |
| YouTube | — | — |
| Newsletters | partial | `/api/letter/latest`, `/api/public/letter`, `/letter`, `/api/learn/letter`, `/learn`, `/share` |
| Paid communities | partial | `/api/arena`, `/arena`, `/api/leaderboard`, `/api/public/duel/squads`, `/duel`, `/api/strategies`, `/api/copy`, `/channel`, `/broadcast` |
| Sponsored posts | — | — |
| Affiliate deals | partial | `/api/auth/referrals`, `/api/public/invite/:code`, `/start` |
| Ambassador roles | — ⟲ | — |
| Premium research/reports | partial | `/research`, `/token`, `/alpha`, `/api/research/:symbol`, `/api/research/:symbol/web`, `/api/reports`, `/api/today`, `/api/public/proofofpnl`, `/proof`, `/track` |

**X/Twitter** — partial

An outbound SHARE rail, nothing more. Three places build a real
`twitter.com/intent/tweet` compose URL: the invite panel (which prepends the
caller's own ?ref= link), the public trader-record page and the sealed-call
receipt page. Two more share paths use `navigator.share` (the OS sheet, which
can hand off to X if the app is installed) with a Telegram fallback — the
symbol-modal decision picture (dashboard.js:2686-2696) and the journal close
card, which first fetches a server-rendered percent-only PNG from
/api/share/card (dashboard.js:4198-4225). ~20 public pages carry twitter:card
+ og meta so a shared link unfurls, and four pages are SSR-injected with live
values for the unfurl. Separately, X is an OAuth IDENTITY provider:
app/lib/oauth2.js:58 requests scope `tweet.read users.read` but the only call
made with the token is profileUrl `users/me` (parseProfile reads id + avatar),
and the provider is advertised only when both env vars are set —
.env.example:571 ships them commented out.

*Gap.* Nothing posts to X, schedules a post, reads a timeline or mentions, or
measures a single impression/follower. There is no per-user X account
connection for publishing (the OAuth token is used once, for identity, and X
returns no email so a placeholder is synthesized). No thread composer, no
content calendar, no scheduling, no analytics, no monetization of an X
audience of any kind.

**Newsletters** — partial

RUNECLAW publishes ONE weekly letter and it is genuinely wired: the Agent
Letter is composed once per completed ISO week from recorded data (closed
trades, equity snapshots, the signal stream, the agent feed), stored in
`agent_letters`, and served authed at /api/letter/* and publicly, dollar-free,
at /api/public/letter/* — read by two dashboard panels, the /letter page, a
chat intercept and an MCP tool. It is honest about thin data (unpriced closes
leave the series rather than zeroing it) and the public recomposition strips
dollars. /api/learn/letter is a second, learner-scoped weekly letter.

*Gap.* It is the OPERATOR's letter about the OPERATOR's book — app/lib/letter.js:18
pins every query to `OPERATOR_USER_ID`; there is no per-user letter and no way
for a user to author, edit, schedule or send one. There is no distribution
list and no email send: app/lib/mailer.js is transactional only (its two
callers in app/auth.js are email verification and password reset), so nothing
ever mails a letter; delivery is a dashboard panel plus a web-push nudge. No
subscribers, no signup form anywhere on the app or the marketing site, no paid
tier, no paywall, no open/click metrics. The only user-facing 'newsletter'
feature is INGEST — pasting a newsletter you already received into your own
private encrypted context.

**Paid communities** — partial

Community plumbing is real and reachable, and all of it is free. Squads are
the referral graph made visible on the Duel board (one recruit makes you a
captain — the 'Connector' tier at app/auth.js:437 is the one tier marked
state:'live'). Copy-follow, the strategy marketplace, the Arena, the public
leaderboard and the operator's Telegram broadcast channel all exist. An
ACCESS-TIER mechanism also exists: basic/pro/elite plans, a 5-question/day
free chat quota with pro/elite/admin exempt (bot/web/chat_quota.py:32), and a
per-feature tier map in bot/token/tier_gate.py.

*Gap.* There is no payment rail anywhere in the tree — no Stripe, no checkout, no
billing, no invoice, no x402 pricing block (app/test/tool8257.test.js:50
asserts the manifest has none). The plan card is STATIC and says so: "Tiers
are granted by the operator through the Telegram bot… online checkout is
coming later" (dashboard.js:5139). The $RCLAW tier gate that would enforce a
paid tier is wired at four call sites but INERT — gate_enabled() needs
TOKEN_TIER_GATE_ENABLED and RCLAW_MINT, neither of which is set anywhere, and
docs/TOKEN_ROADMAP.md:27 says "No token exists. No sale has run." Most
importantly for this leaf: a USER cannot run a community here at all —
/channel and /broadcast are gated to a bot admin or a Telegram group
admin/creator, there is no user-created group, no members list, no dues, no
gated chat.

*The verifier refused part of this row.* Status PARTIAL survives (the community layer is genuinely shipped and
reachable — I confirmed /api/arena, /api/duel + /api/public/duel, /api/copy,
/api/strategies, /api/public/leaderboard and the ChannelForwarder are all
mounted/instantiated), but there is no paid door anywhere in the tree. A
whole-tree grep for stripe|checkout in product code returns exactly one hit,
and it is the membership card's own sentence: 'Tiers are granted by the
operator through …

**Affiliate deals** — partial

The ATTRIBUTION half of an affiliate program is shipped and reachable end to
end: every account gets a random 8-char referral code (auth.js:405), a ?ref=
signup is credited to the referrer with self-referral guarded
(auth.js:588-602), GET /api/auth/referrals returns the link plus the count of
people who joined on it (auth.js:896-916), the invite panel renders the link
with one-tap Telegram/X/Warpcast shares, /api/public/invite/:code personalises
the landing while revealing only an already-public handle, and Telegram /start
parses a `ref_<code>` deep-link payload on first contact
(start_commands.py:163). Referrals also do one real thing: they build your
Duel squad.

*Gap.* It pays nothing, and the code says so in as many words — app/auth.js:433-434:
"This endpoint still grants nothing: `referralTier` has one caller, right
below, and nothing in the tree gates a feature on a referral count." Three of
the five tiers are state:'planned', two of them explicitly contingent on a
token that does not exist. There is no commission, no payout ledger, no
attribution of revenue (there is no revenue), and no third-party affiliate
integration: app/lib/venue_links.js:17 builds plain
Bitget/Bybit/BingX/OKX/Hyperliquid/DexScreener deep links with no referral
parameter on any of them. One concrete hole: the Telegram close-card share
button is constructed with no ref_code (alerts_monitor.py:422-424 passes only
the bot username), so `invite_link` falls through to the bare
`https://t.me/<bot>` and that share is unattributable.

*The verifier refused part of this row.* Status PARTIAL survives — I drove the whole attribution chain and every link
of it is real: genReferralCode() at auth.js:406-408, credit-on-register with
self-referral guarded at auth.js:591-606, GET /api/auth/referrals returning
code+count at auth.js:896-916, the invite panel's Telegram/X/Warpcast buttons
at dashboard.js:5251-5255, GET /api/public/invite/:code, the Telegram
`ref_<code>` deep link parsed on FIRST CONTACT ONLY at
start_commands.py:163-166 v…

**Ambassador roles** — nothing serves this ⟲ — the verifier overturned *partial*

Exactly one thing: a named rung on the referral ladder. app/auth.js:444-446 is
the whole of it — `{ at: 10, name: 'Ambassador', state: 'planned', perk: 'Fee
credits.', requires: 'Would ride on the $RCLAW token, which does not exist yet
— no token has launched and no sale has run.' }`. It is computed from a real
referral count by referralTier() (which answers null rather than 'Starter'
when the count is not a measurement) and rendered by referral-tier-model.js in
the invite panel, painted muted AND carrying its caveat in words because
colour alone is not readable aloud. So a user with 10 referrals really does
see the word Ambassador, and is told in the same breath that it is not in
force.

*Gap.* There is no ambassador program: no application, no selection, no agreement, no
role on the user record, no permission, no payout, no fee credit (fee credits
would need billing, which does not exist), and no content or promotion
obligations tracked anywhere. It is a label on a card that the card itself
marks as planned. The word appears nowhere else in the tree.

*The verifier refused part of this row.* none. The only artifact in the tree is a string literal on a ladder the code
itself says grants nothing. `grep -rni ambassador` over the whole tree
(excluding node_modules/.git) returns exactly ONE file: app/auth.js. The rung
is `{ at: 10, name: 'Ambassador', state: 'planned', perk: 'Fee credits.',
requires: 'Would ride on the $RCLAW token, which does not exist yet — no token
has launched and no sale has run.' }`. The comment directly above
REFERRAL_TIERS …

**Premium research/reports** — partial

RUNECLAW genuinely produces research: a cited per-symbol dossier (/research,
which fetches the web app's research card over HTTP via
web_data_pull.fetch_research — scan_commands.py:181-182), the contract-
detective dossier that composes token_safety + deployer_history and leads with
what it could NOT read (/token → bot/core/token_research.py:74), the Daily
Alpha card, the weekly Agent Letter, the hourly intelligence reports, and the
sealed Proof-of-PnL statement. A tiering MECHANISM exists too:
bot/token/tier_gate.py:92 puts deepscan/patterns/analyze_asset/quant_analyze
behind `pro` and run_backtest/walk_forward/optimize/learning behind `elite`,
and a 5/day free chat quota fences the free tier.

*Gap.* Nothing here is sold, and nothing lets a user sell. There is no checkout,
subscription or payment anywhere in the tree; store tiers are set only by
admin /set_tier, and the $RCLAW gate that would enforce a paid tier is OFF by
default (tier_gate.py:365 — TOKEN_TIER_GATE_ENABLED and RCLAW_MINT are both
unset, so check_user returns (True,'ok') for everyone and no tier gate blocks
anything today). A user cannot author, price, paywall, version or distribute a
research report: the only user-authoring surface is a strategy CONFIG
published free to the marketplace (user_strategies.js:4), and the one report-
generating door that costs money to run — cited live web research — is admin-
only. The research that exists is the product's own output delivered to
whoever already has access, not an income stream a person can run.

*The verifier refused part of this row.* Status PARTIAL survives (the research surfaces are real and I drove each
door), but the tiering claim is false on every ordinary deploy. tier_gate is
wired — check_user() is called from telegram_handler.py:1438/4560 and
user_gateway.py:262 — and its FIRST line is `if not gate_enabled(): return
True, "ok"` (tier_gate.py:821). gate_enabled() (line 365-371) requires BOTH
`TOKEN_TIER_GATE_ENABLED` AND a configured mint. The module's line-1 docstring
reads '$RC…


### Freelance & Services

| Leaf | Today | Doors |
|---|---|---|
| Smart contract dev | partial | `/api/contract/studio`, `/api/contract/compile`, `/api/web3/deploy` |
| Audits/security | partial | `/token`, `/xray`, `/approvals`, `/firewall` |
| Growth/marketing | — | — |
| Community management | — | — |
| Design & video editing | — | — |
| BD & sales | — | — |
| Support/moderation | — | — |

**Smart contract dev** — partial

A real, reachable Solidity drafting tool. Any logged-in web user (a website
signup auto-provisions as a paper trader and clears _guard_user at
user_gateway.py:1250 — no role permission, no $RCLAW tier) opens the Contract
Studio view, picks one of five one-tap starters (ERC-20, ERC-721, Escrow,
Multisig, Vesting) or types a free-text spec, and gets back a Solidity DRAFT
plus heuristic security flags, with Copy and Download .sol buttons. Free
accounts spend from the same 5/day chat quota; paid tiers are unmetered
(user_gateway.py:1258-1276). A Compile button posts the draft to solc for
bytecode+ABI+diagnostics, and a testnet Deploy bar appears once bytecode
exists.

*Gap.* Two of the three stages are inert or closed on a stock deploy, and the client
half of freelancing does not exist at all. COMPILE: compile_source lazily
imports solcx and returns available=False / error='compiler_unavailable' when
it is absent (contract_studio.py:196-199) — neither solcx nor py-solc-x
appears in requirements.lock or requirements-ci.txt, so compile answers
'compiler not available' unless the operator installs the toolchain. DEPLOY:
handle_contract_deploy is admin-only by _is_admin_id
(user_gateway.py:1384-1395), testnet-only with mainnet refused regardless of
flag, needs an enforcing Authority Envelope, and needs eth-account, which is
also in neither requirements file. There is no Telegram door (grep of
bot/skills for contract_studio / contract/studio returns nothing — web only).
And nothing in the tree serves the freelance side of this leaf: no client
intake, no scope/quote, no deliverable handoff, no invoicing and no payment
rails — app/test/interop_design.test.js:34 structurally asserts that no
x402/X-PAYMENT/facilitator/payTo/402 machinery exists in any app/routes file,
and the plan card says 'online checkout is coming later' (dashboard.js:5139).
This is a tool a smart-contract dev could use on their own work; it is not a
way to be paid for that work.

**Audits/security** — partial

Genuinely wired, human-reachable security-REVIEW tooling, on three surfaces.
Telegram: /token (scan_commands.py:191, @guard('token') — trader/paper/viewer)
runs token_research.investigate() and composes token_safety (what the contract
can do to holders) with deployer_history/taint/fates into one dossier that
leads with what it could NOT read; /xray (guardian_commands.py:430) decodes
calldata into plain-English actions before signing; /approvals
(guardian_commands.py:370) is the allowance X-ray. Web: /firewall
(server.js:465) is a pure local paste scanner for prompt-injection, seed-
phrase lures, drain language, hidden/bidi/homoglyph characters, poisoned
addresses — no network, nothing leaves the page; /approvals (server.js:475)
enumerates token approvals and emits revoke plans the owner signs themselves.
Contract Studio runs scan_security_flags (contract_studio.py:115) — a
deterministic Solidity rule set (tx.origin auth, selfdestruct, delegatecall,
unchecked low-level call, block.timestamp, …) over the model's own output. MCP
exposes scan_transaction, xray_transaction and scan_token_safety to external
agents (mcp.js:108, :159, :779).

*Gap.* Every one of these is a FLAG, never a verdict, and the code says so at the
boundary: AUDIT_DISCLAIMER (contract_studio.py:29) — 'Heuristic flags only …
Get a professional audit before deploying to mainnet' — travels with every
Studio response, scan_token_safety's own description says '"no flags" means
the checks found nothing, not that the token is safe', and the marketing site
states flatly that 'No security audit of this codebase exists'
(site/src/routes/risk.tsx:180) with MUST_NOT_CLAIM forbidding the claim. So
the product deliberately refuses to produce the thing an audit engagement
sells. Beyond that, nothing serves the service side: there is no
client/engagement record, no findings report a third party can be handed, no
severity triage workflow, no remediation sign-off, and no payment — same
interop_design.test.js gate as above. A security practitioner can use these to
check their own or a counterparty's contract; they cannot run an audit
practice through RUNECLAW.


### Building

| Leaf | Today | Doors |
|---|---|---|
| dApps & protocols | partial | `/api/contract/studio`, `/api/contract/compile`, `/api/web3/deploy` |
| Trading/analytics tools | partial | `/mcp`, `/api/public`, `/api/market`, `/api/signals`, `/api/feed/recent`, `/embed/signals`, `/embed/arena`, `/api/lab/meta`, `/run` |
| Bots | partial | `/api/strategies`, `/api/bot-strategy`, `/mystrategy`, `/api/arena/keys`, `/api/copy/picks` |
| Wallets & infra | — | — |
| AI x crypto tooling | **shipped** | `/mcp`, `/api/arena/keys`, `/developers`, `/api/llm` |
| Crypto SaaS | — | — |

**dApps & protocols** — partial

Any logged-in web user can draft a Solidity contract from a plain-English spec
in the Contract Studio — five starter templates (ERC-20, ERC-721, Escrow,
Multisig, Vesting) or free text — read the heuristic security flags that come
back with it, compile it (solc bytecode + ABI + diagnostics), and copy or
download the .sol. The drafting and compile gateway handlers are _guard_user,
not admin: bot/web/user_gateway.py:1244 and :1364 (the _is_admin read at :1248
only picks the LLM tier).

*Gap.* Nobody but the operator can ship the thing they drafted. POST /contract/deploy
is `if not _is_admin_id(...): 403` (bot/web/user_gateway.py:1398), testnet-
only with mainnet hard-refused, and inert until the operator installs eth-
account and supplies WEB3_SIGNER_PRIVATE_KEY behind an enforcing envelope.
Compile itself depends on an operator-installed py-solc-x that is in neither
requirements.lock nor requirements-ci.txt — compile_source returns
available:false with 'compiler_unavailable' otherwise
(bot/core/contract_studio.py:168-198). The /dapps view is a curated directory
of OTHER people's apps, and the repo's own protocols (programs/rclaw_staking,
devnet, no client; contracts/rune/RuneOfEntry.sol, NFT_CONTRACT_ADDRESS unset)
are RUNECLAW's, with no user deploy door.

*The verifier refused part of this row.* partial — STATUS UNCHANGED, but the claim must be narrowed to drafting. The
draft half is genuinely shipped and reachable (nav id 'studio' dashboard.js:54
→ renderContractStudio dashboard.js:6227, registered dashboard.js:8701; POST
/api/contract/studio app/routes/contract.js:37 → gateway handler
user_gateway.py:1225 gated by _guard_user at :1244, route registered
user_gateway.py:4491; five template buttons, flags, Copy and Download .sol at
dashboard.js:625…

**Trading/analytics tools** — partial

RUNECLAW serves the DATA layer for somebody else's tool, not a tool-builder. A
developer can read its analytics with no account (MCP at POST /mcp, the
ERC-8257 invoke endpoint, and the public REST list printed on
developers.html), and can frame its signal and Arena cards inside their own
site — /embed is the one carve-out from the site-wide frame-ancestors 'none',
GET-only, cookie-less and actionless by construction
(app/routes/embed.js:1-34). A logged-in user can also run a bounded backtest
in the Lab against frozen benchmark snapshots.

*Gap.* No surface in the product builds a tool: no custom panels, no saved queries,
no scripting, and no webhooks at all (a grep for 'webhook' across app/routes,
app/lib and bot/web returns nothing). Every builder-facing endpoint is read-
only by design, and the Lab takes a fixed clamped parameter set — dataset,
whitelisted symbols, bars, confidence, volume_spike_min, regime_filter,
rsi_max (bot/api/lab.py:147-181) — not arbitrary strategy code. Nothing hosts,
prices or distributes what you build.

**Bots** — partial

Two real, end-to-end doors. (1) A member composes a strategy out of the
engine's own rule vocabulary — direction, min confidence, min R:R, percent
size/exposure/loss caps, symbol allow/deny, regime, horizon
(app/lib/user_strategies.js:18-33) — saves it, publishes it to the community
marketplace, and ARMS it on their own bot: the web projects its signal-
checkable rules, the bot re-validates and stores the snapshot
(bot/core/user_strategy_store.py:108-148), and bot/core/engine.py:7197-7234
evaluates it on every confirm and refuses the trade when it fails. Followers
of a published strategy get its would-take picks (app/routes/copy.js:105). (2)
Anyone can mint an rcarena_ key from the Arena page and point their OWN bot at
/mcp to open and close paper positions ranked on the public board.

*Gap.* Neither door produces a bot that trades real money on its own. The armed
strategy is a tighten-only VETO over signals the operator's engine generated —
it originates no entries, and the community snapshot keeps only five gate
kinds (confidence_threshold, symbols, blocked_symbols, direction,
regime_filter; user_strategy_store.py:124-142), silently dropping the rest.
The Arena key is structurally paper-only: 'it reaches the paper Arena and
nothing else… there is no code path from one of these keys to a live order, an
exchange credential, a wallet, or another user's row'
(app/lib/arena_keys.js:9-19). No code or scripting anywhere, no hosting, and
no fee, revenue share or payout for a published strategy or a followed agent.

**AI x crypto tooling** — **shipped**

A builder of an AI agent can use RUNECLAW today with no operator involvement.
Point any MCP client at POST /mcp (mounted app/server.js:415, unauthenticated,
per-IP limited) and get 31 read tools. The ones carrying `computesOnInput` run
over input the caller supplies rather than over RUNECLAW's data —
scan_transaction (prompt-injection/drain/approval/address-poisoning flags),
xray_transaction (calldata decoded to the known selector set, UNKNOWN outside
it), compile_intent, stress_portfolio, plan_escape (app/routes/mcp.js:115,
:166, :224, :251, :302) — the 'safety checks for YOUR agent'
developers.html:55 advertises. The MARKER is the list, here as everywhere: this
paragraph named four of the five and cited four of the five lines, because
xray_transaction joined the family and no prose moved. Mint an rcarena_
key yourself from the Arena page's Agent keys panel (arena.html:366 →
app/routes/arena.js:1232, max 5, revocable, shown once) and the three arena_*
write tools let that agent paper-trade and be ranked. The manifest and invoke
endpoint are served for on-chain discovery, and /api/llm lets a user plug
their own model key in.

*Gap.* Consumption only, and unmonetizable by design: the ERC-8257 manifest
deliberately carries no pricing block and no access predicate, with per-call
charging (x402) stated as design-only (app/lib/tool8257.js:16-22). The agent-
IDENTITY half has no UI — POST /api/agents (claim a slug, app/server.js:363)
and POST /api/arena/keys/agent (bind a key to it, arena.js:1272) are mounted
and authed but referenced by nothing in app/public; the only thing naming the
claim door is an error string, 'Claim it first at POST /api/agents'
(app/lib/arena_keys.js:155), which is this repo's own card-names-a-door-with-
nothing-behind-it shape. Every write an outside agent can make is paper money.


### DAOs

| Leaf | Today | Doors |
|---|---|---|
| Contributor roles | — | — |
| Governance participation | — | — |
| Bounty completion | — | — |
| Ambassador/mod programs | — ⟲ | `/api/auth/referrals` |

**Ambassador/mod programs** — nothing serves this ⟲ — the verifier overturned *partial*

A referral ladder is genuinely shipped and reachable, and one of its five
rungs is literally named 'Ambassador' — but that rung grants nothing and says
so. The door: GET /api/auth/referrals (app/auth.js:896, authMiddleware)
returns the caller's invite code, the count of accounts that signed up on it,
and a tier; the dashboard Account view renders it into #c-ainvite (panel
declared app/public/js/dashboard.js:4506, fetched :5198, tier painted :5224
through the pure app/public/js/referral-tier-model.js). What is LIVE on that
ladder is one rung: 'Connector' at 1 invite (app/auth.js:438, state 'live'),
whose perk is real — app/lib/duel_squads.js builds Daily Duel squads out of
exactly the users.referred_by graph. 'Ambassador' at 10 invites
(app/auth.js:445) is state:'planned', perk 'Fee credits', with requires:
'Would ride on the $RCLAW token, which does not exist yet — no token has
launched and no sale has run.' The comment above the table (app/auth.js:433)
states the whole ladder's limit plainly: 'This endpoint still grants nothing:
referralTier has one caller, right below, and nothing in the tree gates a
feature on a referral count.' There is NO moderator program at all; the
closest adjacent fact is that /broadcast and /channel
(bot/skills/access_commands.py:254, :334) accept a Telegram GROUP admin or
creator of the chat they are run in, which is Telegram's own moderation status
being honoured, not a role RUNECLAW confers or rewards.

*Gap.* The income half is entirely absent: no fee credits, no revenue share, no
payout of any kind is computed or paid for referrals — the tier is a label
with a caveat attached, explicitly rendered as planned (muted styling plus the
caveat in WORDS, pinned by app/test/referral_tier_honesty.test.js). There is
no ambassador application, enrolment, quota, content requirement or reporting
surface, and no moderator role, mod tooling or mod compensation anywhere in
the tree. Two of the five rungs ('Ambassador', 'Legend') depend on a token
that docs/TOKEN_ROADMAP.md:27 says does not exist.

*The verifier refused part of this row.* none — the doors exist and are reachable, but what they serve is a
referral/invite loop, not an ambassador or moderator program; the rung
literally named 'Ambassador' confers nothing and is marked planned in its own
data


### Referrals & Affiliates

| Leaf | Today | Doors |
|---|---|---|
| Exchange referrals | — | — |
| Platform affiliate programs | partial | `/api/auth/referrals`, `/duel`, `/start` |
| Wallet/app referrals | — | — |
| Creator referral deals | — | — |

**Platform affiliate programs** — partial

RUNECLAW runs its OWN referral program and the signup-attribution half is
genuinely wired end to end — but it is a status/social program, not an income
one, and nothing pays out. Driven through the code: the landing page reads
?ref= (app/public/index.html:1218), personalises the page by asking GET
/api/public/invite/:code for the referrer's public handle only
(app/routes/public_invite.js:35 — unknown codes 404, no account enumeration),
and posts the code with the signup (index.html:1288). app/auth.js:592-602
mints the new user's own 8-char code and writes users.referred_by when the
code resolves to someone else (self-referral and unknown codes ignored). GET
/api/auth/referrals (app/auth.js:896) returns {code, count, tier, next}, back-
filling a code for older accounts, and the Account view's c-ainvite panel
(dashboard.js:5210) renders the link, three share buttons and the ladder. The
tier ladder is honest by construction: REFERRAL_TIERS (auth.js:435-452)
carries a per-perk `state`, referralTier() returns NULL rather than "Starter"
for an unreadable count (auth.js:463), and referral-tier-model.js:71 omits the
whole block when there is nothing honest to say. THE ONE PERK THAT IS REAL is
the Daily Duel squad: app/lib/duel_squads.js:83 buildSquads reads exactly this
referral graph, served at GET /api/public/duel/squads
(app/routes/public_duel.js:101) and rendered on /duel (duel.html:254).
Everything else you could call income is explicitly declared not in force —
"Fee credits" and "A share of protocol revenue" each print `requires: Would
ride on the $RCLAW token, which does not exist yet` (auth.js:445-451), and the
code comment at auth.js:432 states plainly that `referralTier` has one caller
and NOTHING in the tree gates a feature on a referral count. There is also NO
affiliate relationship with any third-party platform: the dApp directory
(app/lib/dapps.js) and venue links carry no partner params, and the strategy
marketplace (app/routes/user_strategies.js) and copy-follow
(app/routes/copy.js) have no fee, commission or revenue-share code at all.

*Gap.* No money. There is no commission, rebate, payout, credit or balance anywhere
in the referral path — the entire program is a count, a chip and a squad row.
Three concrete holes beyond that: (1) OAuth/Telegram/X signups are NOT
credited — upsertSocialUser (app/auth.js:1122) inserts the user with no
referral read, and rc_ref is consumed only by the email/password register
(index.html:1288-1290), so the localStorage persistence whose own comment says
it exists "so it survives an OAuth round-trip" reaches no OAuth path. (2) The
Telegram half is a dead end: /start records an attribution into the bot's JSON
store (start_commands.py:163-166 → user_store.record_referrer at :659), and
grepping bot/ for readers of `referred_by` finds NONE outside that write and
its own self-referral check — the bot store never mints a referral_code,
nothing syncs it to the MySQL users table the count is computed from, and
there is no Telegram command to see your own invite link (command_catalog's
`share` is the private-notes command). (3) The close-card share button passes
only close_data and the bot username (alerts_monitor.py:422-423), so invite_link()
is called with ref_code=None and the shared link is a bare t.me/<bot> with no
attribution.

*The verifier refused part of this row.* Still partial for the WEB email/password program, but two of the named doors
do not lead where the row says. (1) THE TELEGRAM ref_ DEEP LINK HAS NO
PRODUCER AND NO READER. The receiver is real and reachable —
start_commands.py:163-166 calls parse_start_payload(ctx.args[0]) on first
contact and writes users.record_referrer — but nothing in the product ever
mints such a link. invite_link(bot_username, ref_code) (share_invite.py:118)
is called from exactly on…


### Infrastructure Ops

| Leaf | Today | Doors |
|---|---|---|
| Validators | partial | `/idleyield`, `/api/idleyield`, `/api/defi` |
| RPC/archival nodes | — | — |
| Indexers & oracles | — | — |
| Storage/bandwidth/compute providers | — | — |
| Sequencers & relayers | — | — |

**Validators** — partial

Read-only validator-yield DISCOVERY, nothing more. The idle-asset optimizer
fetches live ETH liquid-staking APYs for Lido (stETH) and Rocket Pool (rETH)
from DefiLlama and ranks them against custodial CEX Earn rates, deliberately
preferring the non-custodial option and stating the tradeoff
(bot/core/idle_yield_feeds.py:52-56, curated allowlist; MIN_TVL_USD floor; a
failed fetch yields NO option, never a fabricated APY). Two doors reach it:
GET /api/idleyield (app/routes/idleyield.js, authMiddleware — any signed-in
web user, mounted app/server.js:387) via gateway POST /idleyield
(bot/web/user_gateway.py:4517, which calls fetch_noncustodial_options at
:3000), and Telegram /idleyield, which is ADMIN-ONLY by an inline _is_admin
check (bot/skills/yield_commands.py:142). Separately, an existing stETH
position is MIRRORED read-only from the mainnet contract (app/lib/defi.js:36
LIDO_STETH, :103 readLido) through GET /api/defi and the c-defi panel. The
route header states the boundary in its own words: "Read-only —
recommendation, not execution" (app/routes/idleyield.js:4), and /idleyield's
docstring says "it recommends, it never moves a cent".

*Gap.* No staking, delegating, or validator operation exists on any surface. Nothing
in the tree builds, signs or sends a Lido/Rocket Pool deposit, an ETH beacon
deposit, or a Solana delegateStake — I grepped for the definitions and counted
callers, and the only execution path named /stake or /unstake is BITGET CEX
flexible/fixed Earn (bot/skills/yield_commands.py:297 _cmd_stake, @guard("stake")
— trader and admin, acting on the CALLER's own linked account;
money moves solely on the confirm callback at
bot/skills/callback_handler.py:570 execute_stake/execute_unstake against
bot/core/yield_radar.py). That is a custodial exchange savings product, not
validator income. Two naming traps that must not be read as coverage: (1)
programs/rclaw_staking is NOT validator staking — its own header calls it "a
minimal non-custodial stake vault" where "users escrow tokens into a program-
owned vault" purely so bot/token/tier_gate.py can read a staked amount and
unlock premium features; it is DRAFT/DEVNET-ONLY, unaudited, and has no client
at all (every sender of stake/unstake in the tree is a test harness —
tier_gate only READS via getProgramAccounts). (2) Every 'validator' string in
production Python/JS is either a pydantic/symbol input validator or `solana-
test-validator`, a local test harness. So the honest reading is: a user can
learn what a validator-backed rate is and see a stETH balance they already
hold; they cannot earn validator yield through RUNECLAW.

*The verifier refused part of this row.* Still discovery-only, but the door list is wrong in three places and short in
one. (a) programs/rclaw_staking/src/lib.rs is NOT validator infrastructure and
is not shipped: its own header reads "DRAFT / DEVNET-ONLY", "DO NOT DEPLOY TO
ANY CLUSTER HOLDING REAL VALUE", "no third-party audit", and "A real
deployment stays gated behind the roadmap's Phase 0 Guardrails". It is a per-
user token-escrow vault whose staked amount bot/token/tier_gate.py reads to
unl…

## Capabilities this map has no leaf for

The critic re-read the tree after the fifteen classifiers finished and found
capabilities none of them had named. The list is TWENTY-TWO now, and it was
written down as a smaller number in three places while growing under them —
slices kept shipping capabilities the map still has no leaf for (the trade
co-pilot, trade costs, why a stop could not be placed, the POC-retest setup)
and each was appended under a count that did not move. It is derived by
`tests/test_the_income_map_counts_its_own_list.py` now rather than restated a
fourth time: a count in prose is the part that rots first, and this one rotted
in the document a session is scoped from. The earlier figure is not quoted
here, because that guard forbids a stale numeral in this section and prose
narrating one is indistinguishable from the count itself. Several are not under-served
leaves — they are whole surfaces the map does not ask about, which is a fact
about the map rather than about the product. They are listed as the critic
wrote them, with its own `which_leaf` guess omitted where it said the guess
was one.

**RWA (tokenized real-world-asset) sector radar**

RWA (tokenized real-world-asset) sector radar — a curated, runtime-filtered
universe of RWA platforms/chains/DeFi scored off live venue tickers. FIVE
doors, none named by any of the fifteen agents. This is the single largest gap
in the map.

*Where.* Dashboard Markets view panel #p-rwa/#c-rwa
(app/public/js/dashboard.js:1290 jump-nav, :1349 panel, :1439 fetch) → GET
/api/market/rwa (app/routes/market.js:169, auth:false, public); Telegram /rwa
(@guard("rwa"), bot/skills/market_commands.py:69, registered
bot/skills/telegram_handler.py:1008, reads the web via
bot/utils/web_data_pull.py → /api/bot/sync/card/rwa, the card RENDERED);
web chat intercept row 4
'rwa' (app/routes/chat.js INTERCEPTS, says "a tokenized-asset sector
snapshot"); MCP tool get_rwa_radar (app/routes/mcp.js:645); implementation
app/lib/rwa.js; operator studies scripts/research/rwa_funding.py and
rwa_session_gap.py (both have __main__ guards and LEFT
tests/unreachable_baseline.txt).

**Meme & AI-token RADAR (the discovery half)**

Meme & AI-token RADAR (the discovery half). The agents named only the
execution-adjacent /api/meme/swap/build and /memeplan; the read-only on-chain
meme/AI snapshot with a safety read is a separate, wider door.

*Where.* Dashboard Markets panel #c-meme → GET /api/market/meme
(app/routes/market.js:181, public; dashboard.js:1545); web chat intercept
'meme' (app/routes/chat.js); MCP get_meme_radar (app/routes/mcp.js:653);
app/lib/meme.js.

**On-chain flow radar (exchange flows / whale accumulation)**

On-chain flow radar (exchange flows / whale accumulation). Public panel plus a
BYOK provider that votes in the analyzer.

*Where.* Dashboard Markets panel #c-flow (jump-nav 'On-chain flow') → GET
/api/market/onchain-flow (app/routes/market.js:205; dashboard.js:1575),
app/lib/onchain_flow.js; engine side bot/core/onchain.py (BYOK
Glassnode/Arkham/Nansen) imported by bot/core/analyzer.py and
bot/core/token_safety.py; bot/core/smart_money.py imported by analyzer.py.

**3D Strength Map**

3D Strength Map — factor scoring of the WHOLE Bitget USDT-perp universe
(price, 24h change, volume, funding, OI) plotted as a star map. A public, no-
account market-discovery surface.

*Where.* Public page GET /strengthmap (app/server.js:676) →
app/public/js/strengthmap.js:253 → GET /api/market/strengthmap?limit=240
(app/routes/market.js:221), app/lib/strengthmap.js; linked from
app/public/index.html:149 and explore.html:183. Also the dashboard Markets
'Sector sweep' panel #p-radar3d (dashboard.js:1342).

**Tokenized US STOCK perpetuals**

Tokenized US STOCK perpetuals — a whole asset class nobody classified: market-
session detection, stock-specific risk overrides, stock universe scan, sector
rotation, index beta.

*Where.* Telegram /stockscan (@guard("scan"),
bot/skills/scan_commands.py:1293, registered telegram_handler.py:1225) and
/mode stocks (universe switch, command_catalog.py:96);
bot/core/stock_trading.py, also read by bot/core/engine.py:7747
(get_market_session) and scan_commands.py:375.

**Price alerts and anomaly-alert scoping**

Price alerts and anomaly-alert scoping — user-set price triggers evaluated in
the WEB app (they work while the bot process is down), plus per-operator
anomaly scope ('held' vs 'all') and a rate floor.

*Where.* Web chat intercept row 1 'alerts' (app/routes/chat.js, first in the
table); GET/POST /api/alerts and DELETE /api/alerts/:id (app/routes/alerts.js;
dashboard.js:643, :675, :691, :7427); app/lib/alerts.js; Telegram /watch and
/alerts (registered telegram_handler.py), bot/core/anomaly_scope.py +
black_swan.py + proactive_monitor.py.

**Signal replay**

Signal replay — "what if I'd taken every signal with $1k?" run over the web's
own recorded signal history. This is the evidence surface a person uses before
deciding to follow the engine at all.

*Where.* Web chat intercept row 2 'replay' (app/routes/chat.js); GET
/api/replay?stake=&days= (app/routes/replay.js; dashboard.js:3983 and :7473
for the Agent Hub tile #c-hubreplay); app/lib/replay.js; MCP run_what_if
(app/routes/mcp.js:712).

**Trade co-pilot**

Trade co-pilot — a deterministic (no LLM, no network) second opinion on a
manual ticket before confirm: reward:risk, stop distance, geometry, size vs
equity, the engine's current lean and the caller's existing exposure. It
advises, never blocks, and it SAYS WHICH of those it could look at: `verdict`
is four-valued (`partial` is nothing flagged over a subject it could not
check), every subject carries its own reason for going unrun, and the score
carries the span it was taken over.

*Where.* ONE assembly, bot/core/copilot_context.review_ticket — the reading of
the book the ticket executes on (ticket_context), bot/core/trade_copilot.review
over it, and the score sentence stamped once — read by every door that proposes
a manual trade:

  * Telegram `/trade` (bot/skills/trading_commands.py `_cmd_trade`), which
    renders trade_copilot.review_card_html under the levels and above the
    Confirm keyboard; the free-text grammar path rewrites its message to
    `/trade …` and delegates here, so it inherits the block.
  * The web proposal (bot/web/user_gateway.py `_propose_from_text`), reached by
    POST /trade/propose (the dashboard ticket and the api bridge) and by the
    chat grammar branch; the review rides out on `pending_trade.copilot`.
  * POST /trade/copilot (handle_trade_copilot), the ticket form's Review
    button, which adds trade_copilot.human_readable for a non-browser caller.

Browser side: app/public/js/copilot-review-model.js renders the block, and the
dashboard ticket, the dashboard confirm modal (openTradeModal) and the chat
drawer's trade card (chat.js appendTradeCard) each call it — one renderer,
three surfaces, two bundles.

The reward:risk it checks is NET OF THE FEES THE TICKET WILL PAY, through
bot/core/trade_costs — one rule for the whole product: a rate is per LEG, a
resting limit entry is MAKER, every live exit is TAKER (the stop and the
take-profit are both placed as trigger market orders), and an unstated order
type is taker. So `order_type` rides with the ticket from all three doors, and
a ticket that clears the bar on price and fails it after fees is a flag rather
than a note reading "Strong reward:risk". The levels row (`levels_line`) is
stamped by the producer and printed by both renderers, because it used to be
assembled twice — once in Python for the Telegram card and byte for byte in
copilot-review-model.js.

*Gap.* The review's sentences are English on both surfaces, because they are
the producer's: one vocabulary, in trade_copilot. `/trade`'s card around it is
fourteen languages. Localising the review is ~25 keys × 14 plus a producer
refactor to emit keys and params, on both surfaces; rendering localised frame
words around English findings would be a second vocabulary for one review.
The five ENGINE-generated `confirm:` buttons are a recorded refusal rather than
a gap: `_engine_bias` reads the engine's own non-manual pending ideas, so an
engine idea would match itself and be told it is "aligned with the engine's
bias", and those levels already passed the risk gate at analysis time.

**Trade costs**

What one round trip costs, and the reward:risk that survives it —
bot/core/trade_costs. `entry_rate_pct` / `exit_rate_pct` / `round_trip_pct` /
`taker_legs_pct` / `fee_usd` / `net_reward_risk`, with the leg rule stated
once. Read by: the executor's seven fee sites (six copies of
`maker_fee_pct if is_limit_entry else taker_fee_pct` plus the time stop's
round-trip buffer), the co-pilot's reward:risk check, the resting limit-order
card and `position_fee_estimate` on /positions, three fee blocks in the
callback handler, the fee-aware entry gate's FEE half, and the funding-arb
reality check (four taker legs, derived rather than the hand-written 0.24 its
own comment always explained).

*Deliberately out.* Slippage: a fee is a rate the venue charges and the
executor books, a slippage estimate is a model, and folding one into the other
publishes an estimate with the authority of a charge — the entry gate keeps its
own slippage term. `maker_take_profit_enabled` is a BACKTEST knob (its config
comment says it does not alter live placement), so a live card never prices
itself off it. The paper book (bot/risk/portfolio.py) and the backtest keep
their own injected rate so a simulated fee matches the run being compared; the
one-rule ratchet lists each exemption with its reason and fails on a stale one.

**Why a stop could not be placed**

One reading, one sentence — bot/core/sltp_reason. `venue_reason` escapes and
truncates the recorded refusal once (three readers escaped it three ways and
one not at all, at 120 / everything / 160 characters), `refusal_line` says it
in words that are SOURCE-NEUTRAL, and `refusal_suffix` carries it onto a card.
Read by: the three stop-placement abort cards in `_sl_tp_or_abort` (which named
no cause at all), the unprotected-position escalation, /positions'
bot-managed-stop row and the proactive monitor's CRITICAL alert. The store
(`_note_sltp_error`) bounds itself by the same `REASON_MAX`, and both placers'
side-sanity refusal now records the sentence it computes.

*Deliberately out.* "The venue said": driven over every `_note_sltp_error`
call site, three of the four things the store holds are the bot's own words or
a network fault — `str(exc)` from a ccxt `create_order`, "success code but no
order id returned" and `f"exception: {exc}"` — so a line attributing them to
the venue is a confident wrong attribution on the card an operator reads to
decide what to change. The three SIBLING aborts in the same method are left
alone: they abort for fill slippage and a leverage overshoot and each already
names its own cause.

**The POC-retest swing setup, on two timeframes**

The operator's rules, written down on 2026-09-17, with their own framing as the
design constraint: *"These are sensible starting rules, not yet validated
results. I'd test the ATR buffer, 1-5-candle retest window, and 2R filter as
parameters rather than assuming they are optimal."* So every threshold is a
field of `PocRetestParams` and none is a literal in a comparison anywhere in
the module — the rule `BacktestConfig.market_is_perp` states about perp-ness.

`bot/core/poc_retest` is the detector and is PURE: `swing_leg` finds the most
recent completed 4h leg, `leg_poc` computes the Point of Control over THAT
LEG's candles (`analyzer`'s POC is over `volume_profile_lookback`, a different
price under the same word), and `retest_state` answers one of eight `STATES`
rather than a score — because a sequence that has not completed is not a
weaker version of one that has. `setup_verdict` applies the spec's two
rejections, and the 2R floor is on the NET ratio (`net_reward_risk`), which is
the defect every pre-placement surface here was cured of the day before.

`bot/core/poc_retest_scan` is the one place that fetches, and `/pocretest SOL`
(`@guard("analyze")`) is its only door. It places nothing and arms nothing, and
the card says so in as many words, beside the sample it read and the operator's
own "starting values, not validated results".

*What the reading refuses to do.* A forming candle's close is not a close and
this whole strategy is closes, so both timeframes go through
`drop_forming_candle` — driven, not asserted: the same 1h series with its final
bar still forming answers `no_breakout` where the settled one answers
`awaiting_retest`. A fetch that failed, a venue that answered with nothing and a
window too short for the leg or for ATR(14) are three facts with three
sentences, each kept apart from every member of `STATES`, because `no_breakout`
is a claim about price. An unreadable 4h candle is `no_poc` rather than a crash
(`compute_volume_profile` bins with `int(...)` and `int(nan)` raises) and rather
than a nan POC, which every comparison would answer False to — "price never
cleared the buffer", from a level nobody measured.

*Deliberately out, each with its reason.* There is no execution flag and no
Confirm button: a flag read by nothing is the fifth granularity and a button
behind it would lead to "not built yet", which is the `/vault` hint shape. There
is no universe sweep yet — the door is one asset, which is what "confirm the POC
retest" names. The target is the one
thing the rules do not give and 2R needs one, so it is the LEG'S OWN EXTREME,
stated as an assumption, refused as `no_target` when price has passed it rather
than manufactured from a multiple.

*The shadow record, and what it can and cannot say.* `/pocretest SOL` ARMS a
setup the strategy would take (`confirmed` AND a verdict of `ok` — recording a
rejected one would measure a strategy nobody proposed) and scores what is
pending from the bars it already fetched; `/pocshadow` prints the verdict.
bot/core/poc_retest_record.py:161 `score_setup` walks the bars after the retest
and answers one of six outcomes. R is `net_reward_risk`'s unit, whose
denominator is the fee-inclusive stopped-out loss, so a stop is exactly -1.0R
and the target pays the arm-time net R. The verdict is `mean_r_interval` with
the whole 95% interval clear of zero and `shadow_book.MIN_GATE_TRADES` as the
floor, both READ rather than restated.

Three things it deliberately does NOT claim. A bar that spans the stop and the
target is `ambiguous` — OHLC cannot order two touches inside one bar — so it
carries no R, is not in the mean, and is named in the sentence beside it,
because those bars are the volatile ones and dropping them silently reports the
calm half as the whole. A fill is modelled AT the entry price, so a bar that
opens beyond it fills worse and the recorded R is the OPTIMISTIC bound, which
is stated rather than modelled (pricing the gap needs the bar's open and a
per-row re-run of the fee model — a different quantity and its own slice). And
the scoring window is the entry-TF fetch, so a retest that has slid out of it
stays `unscored` rather than being dropped: a denominator that quietly excludes
the rows nobody could reach is a partial total printed as whole.

*Replayed over the frozen snapshots (2026-09-24), it does not pay, and the
record as first written would have said it did.* `scripts/poc_retest_replay.py`
reads every closed 1h bar the way `observe_setup` does. At the operator's
parameters the setup is +0.03R [−0.25, +0.30] over 262 scored setups on the
two disjoint v2 snapshots, and −0.39R [−0.74, +0.01] over 45 on the fresh
window. None of the 81 parameter cells clears zero, and a cell's rank on one
window says nothing about its rank on the other. The record, though, armed any
confirmed read whenever somebody asked, and scored it from its retest candle.
Asked once a day, it would have printed "survives, +2.04R", because 125 of the
228 setups it armed had already resolved before the read that armed them. A
read whose entry has traded since its retest candle is not armed now, and the
card says why. An armed setup carries the bar it was armed on. Rows written
before that are left out of `/pocshadow`'s verdict and counted beside it.
`docs/FROZEN_BENCHMARK.md` has the tables. Both headline windows are also a
committed file (`benchmark/poc_retest/result.json`). `/pocretest` prints them
under a confirmed setup and `/pocshadow` under the record, through
`bot/core/poc_retest_history.py`. That reader will not present a window as
this setup's history if the window's snapshot has changed, if it was measured
at other parameters or fees than the live read, or if its verdict disagrees
with its own interval.

**The PUBLIC Strategy-Agent marketplace**

The PUBLIC Strategy-Agent marketplace. The agents listed only the authed
dashboard Agents view; there is an unauthenticated directory with per-agent
SEO pages, a side-by-side compare page with each column's evidence class
declared, and a claimed-identity board.

*Where.* GET /agents (app/server.js:665), GET /agents/:slug with server-
injected per-agent SEO (server.js:764, app/lib/agent_seo.js,
app/public/strategy.html), GET /agents/compare (server.js:669,
public/compare.html), GET /a claimed agents (server.js:655); backed by
app/lib/agent_catalogue.js → bot gateway /public/strategies →
RunStrategySkill.PRESETS, and by committed scorecards
benchmark/scorecards/{dip-sniper,full-scan,momentum-hunter,safe-scalper}.json
produced by scripts/gen_agent_scorecards.py.

**Counterparty & custody-concentration monitor**

Counterparty & custody-concentration monitor — custodial vs self-custody
split, venue/chain HHI, largest single counterparty, settlement-issuer
concentration, computed off the caller's own holdings fan-out.

*Where.* GET /api/counterparty (app/routes/counterparty.js, JWT) →
dashboard.js:5714 → panels #c-cphead, #c-cpbuckets, #c-cpflags, #c-cpissuers;
app/lib/counterparty.js reusing buildHoldings.

**Risk Sentry**

Risk Sentry — a proactive read-only watch over the caller's standing book
(envelope drift, over-cap, concentration, crowding, daily-spend). Detection-
only.

*Where.* GET /api/sentry (app/routes/sentry.js, JWT → gateway /sentry,
bot/web/user_gateway.py) → dashboard.js:3835 panel #c-sentry; public /sentinel
page (server.js:463) and GET /api/market/sentinel (market.js:245); MCP
get_systemic_risk (mcp.js:432).

**Per-user watchlist**

Per-user watchlist — starred symbols that EXTEND the engine's pattern pushes
(the engine pushes patterns for WATCHED symbols, not just held ones), so it
changes what the bot tells you, not just what the page shows.

*Where.* GET /api/watchlist and POST /api/watchlist/toggle
(app/routes/watchlist.js, JWT; dashboard.js:2513 and :2696, panel #c-watch);
app/lib/pattern_watch.js.

**News radar with bring-your-own paid news key, and the personal-ingest direction**

News radar with bring-your-own paid news key, and the personal-ingest
direction. The agents caught only the News view ingest box under Newsletters;
the JWT news feed with high-impact flags on the caller's HELD positions, the
BYON key path (the user's own paid key rides the encrypted service channel
into the bot's Fernet store — the web server never keeps it), and the Telegram
command are all separate doors.

*Where.* GET /api/news, POST /api/news/key, /key/clear, /key/status
(app/routes/news.js → gateway /news, /news/key*, bot/web/user_gateway.py);
POST /api/ingest and /ingest/delete (app/routes/ingest.js → gateway /ingest);
dashboard panels #c-newsfeed, #c-newsbyon, #c-newsdd, #c-newsshare; Telegram
/news (registered telegram_handler.py); bot/core/news.py + news_byon.py.

**The EXECUTION half of the cross-chain yield planner**

The EXECUTION half of the cross-chain yield planner. The agents listed
/api/crossyield (the "is it worth the gas" scan) but not the plan-and-sign
path beneath it: a triple-gated execution preview and then a real first-leg
signed transfer.

*Where.* POST /api/web3/cross-plan (app/routes/web3_execute.js:97 → gateway
/cross/plan; dashboard.js:8516, panel #c-crossyield section 'Worth moving? —
cross-chain yield planner'); POST /api/web3/sign, POST /api/web3/sign/prepare,
GET /api/web3/sign/status (web3_execute.js:52/178/155; dashboard.js:8576,
:8630, :8657) → gateway /web3/sign*, bot/web/user_gateway.py. ADMIN-ONLY and
TESTNET-ONLY by the routes' own headers.

**$RCLAW staking program and the tier gate that reads it**

$RCLAW staking program and the tier gate that reads it — an Anchor program
with per-mint isolated vaults and a PINNED_MINT constant, whose stake records
the Python tier gate reads by getProgramAccounts memcmp on owner@9. The user-
facing door is wallet linking for staked-tier access.

*Where.* programs/rclaw_staking/src/lib.rs (+ tests/solvency.rs, attack.rs);
bot/token/tier_gate.py, tier_weight.py, solana_verify.py; Telegram /linkwallet
("link a Solana wallet (read-only) for $RCLAW tier access",
command_catalog.py:44); app/lib/solana_verify.js. CAVEAT:
programs/rclaw_staking/README.md opens with "⚠️ UNAUDITED / DO NOT DEPLOY" and
CLAUDE.md records the $RCLAW gate as OFF by default — so this is a built and
READ capability, not a live staking yield.

**$RCLAW token creation/verification and the Wormhole NTT cross-chain bridge**

$RCLAW token creation/verification and the Wormhole NTT cross-chain bridge.
The agents listed the presale:* scripts only; mint, verify, keygen and the
hub-and-spoke bridge are the same operator-shell family and are the
distribution half.

*Where.* token/package.json: npm run create (scripts/create_token.mjs),
verify, keygen, bridge:plan and bridge:transfer (token/bridge/ntt_bridge.mjs,
Solana devnet ↔ Base Sepolia, lock-on-Solana because mint authority is
revoked), presale:verify, presale:withdraw, presale:withdraw-unsold,
audit:gate, programs:inventory. Marked DRAFT/TESTNET in the file headers;
ntt_bridge.mjs documents a known upstream SDK load failure for the Solana leg.

**Farcaster as a distribution and ACTING surface**

Farcaster as a distribution and ACTING surface — not just the Warpcast share
button the agents found. Sign-In-With-Farcaster issues a bearer token
(deliberately not a cookie, because SameSite=Lax dies inside a third-party
frame), and there is a framable authenticated Mini App that opens Arena
positions.

*Where.* POST /api/farcaster/nonce and /api/farcaster/signin
(app/routes/farcaster_auth.js, app/lib/siwf.js; mounted server.js:396); GET
/miniapp/arena — "sign in with Farcaster and trade the season"
(app/routes/miniapp.js:119, mounted server.js:400, client /js/miniapp-arena.js
+ embed-arena-view.js); GET /.well-known/farcaster.json
(app/routes/discovery.js:86, app/lib/farcaster_manifest.js); launchable embed
cards in app/routes/embed.js:147/:174; GET /api/frame/* call receipt cards
(app/routes/frame.js).

**The rest of the scan/analysis command family**

The rest of the scan/analysis command family. The agents named /scalp, /swing,
/fullscan and /analyze; seven more registered read-one-thing doors serve the
same two leaves and were not counted, including /quant, which CLAUDE.md
records as only just wired.

*Where.* All registered in bot/skills/telegram_handler.py and catalogued in
bot/skills/command_catalog.py:88-97: /intraday (15m), /patterns, /squeeze,
/sweep, /zones, /session, /mode, plus /quant (@guard("analyze"),
bot/skills/quant_skill.py, skill quant_analyze — it LEFT
tests/unreachable_skills_baseline.txt). Web counterparts: GET /api/patterns/*
and /api/insight/* (app/routes/patterns.js, insight.js) feeding panels
#c-mkpat, #c-spat, #c-insight, #c-tinsight.

**Trading-cost / fee-reduction surface**

Trading-cost / fee-reduction surface — a costs breakdown command and a
documented lever inventory whose largest item is an off-code account setting
(~20% off every fill), with maker-preferred limit entry in the engine.

*Where.* Telegram /costs (command_catalog.py:123, registered in
telegram_handler.py); docs/FEE_REDUCTION.md (BGB discount runbook);
bot/core/limit_entry.py, bot/core/cost.py, bot/core/slippage.py, maker-
preference flags in bot/config.py and bot/core/venues.py; dashboard
#c-venuepnl / #c-breakdown (app/lib/venue_breakdown.js).

## What this sweep could not determine

Reproduced from the sweep's own completeness critic, unedited. It is the
half of the measurement that says where the measurement stops.

### Areas nobody read

- bot/guardian/, bot/compliance/, bot/proofofpnl/, bot/risk/, bot/llm/,
  bot/db/, bot/formatters/, bot/api/ and bot/mcp/ — I listed bot/ subpackages
  but read no file in any of these. bot/mcp/server.py I only grepped for tool
  names and saw an explicit allow-list I did not enumerate, so the Python MCP
  surface is unmeasured.

  **ANSWERED for bot/mcp/, and the doubt was pointed at the wrong risk.** The
  allow-list is nine tools and it is not what an agent reaches: driven,
  `POST /mcp` answers thirty-four tools from `app/routes/mcp.js`'s own registry
  and every one of the nine answers `{"code":-32602,"message":"Unknown tool:
  runeclaw_scan"}`. `app/routes/mcp.js` references neither `bot/mcp/server.py`
  nor any `runeclaw_*` name; nothing outside the tests constructs
  `RuneClawMCPServer`, and the one production import of the file reads
  `_MCP_AUTH_TOKEN` to assert the constructor refuses to start without it —
  a check about a constant, not a caller.

  What WAS measurable is that two published surfaces sent an agent developer
  at it. `docs/gitbook/mcp-integration.md` — the GitBook page `agent_card.json`
  names as the documentation — carried the status row *"Implemented --
  `bot/mcp/server.py`, live over JSON-RPC at `POST /mcp`"* and the sentence
  *"`app/routes/mcp.js` mounts it"*; the card's own `mcp_tools` listed the same
  nine and its `interfaces_note` named both files as the MCP interface. The
  guard standing over the page proved its table and `TOOL_CATALOGUE` agree
  exactly — they do, about a catalogue nothing can reach — and its CONTROL
  asserted the module exists, builds a catalogue, and that `app.use('/mcp'` is
  in `server.js`: three true things whose conjunction is false, because both
  ends existing is not a connection between them.

  **The surface is safe because the document is wrong about it**, which is what
  settled the wiring question with evidence rather than taste. `POST /mcp` is
  mounted with no auth (`app/server.js:415`; `routes/mcp.js` says so in its own
  comments), and driven, `runeclaw_portfolio` renders six dollar figures and
  `runeclaw_risk` two — the OPERATOR's book, because `call_tool` takes one
  shared bearer token and passes no caller identity to any skill. Mounting the
  catalogue there as the doc claimed would put account dollars on an anonymous
  route against §4 and hand every caller the operator's book. Three questions
  precede any door — who the caller is, what a per-caller read means with one
  shared token, which tools may answer at all — and none is a wiring line, so
  they are written on the page and in the module rather than answered here.

  **AND THE CONTAINMENT THAT SHIPPED BESIDE IT MEASURED THIS BOX'S PROXY.** CI
  named 74 tests reaching the network where the local run reported twelve. The
  cause was one variable: `HTTPS_PROXY` here is loopback, so every proxied
  venue read connected to 127.0.0.1 and `_outbound_verdict` read that as
  `local` and allowed it — `local` is a measurement of the ADDRESS, not of
  whether the connect leaves the box. Proxy endpoints are read once from the
  environment and refused as `proxy` before the loopback check; enforcement is
  a two-way ratchet over `tests/network_reach_baseline.txt` (42 files), keyed
  by FILE because 60 of the 74 pass when re-run alone and a nodeid baseline
  would churn.

  GROWTH is enforced every run; STALE is a deliberate re-measure, and the
  baseline's comment named `scripts/network_reach_gate.py` as where it happens
  **one commit before that script existed** — the `/vault` hint shape pointed
  at a code comment. It is built: a report path that changes no verdict
  (`RUNECLAW_REACH_REPORT`), three outcomes including CANNOT CHECK, and a
  `--write` that refuses a partial run rather than deleting forty rows on no
  evidence. Filed and NOT done here: stubbing the 42 files' venue seams, of
  which the 14 that still fail when re-run alone are the loudest.

  Fixed in the adapter itself, because a module nobody reaches becomes
  defective in exactly the ways `market_cap`, `basis` and `quant_analyze` did:
  the caller's error copy was a bare f-string of the exception with
  `_redact_string` applied to the traceback three lines above it, so a venue
  URL's query token reached whoever called the tool; and `_fullscan` advertised
  four modes over two behaviours, echoing `"mode": "scalp"` back over the
  identical whole-universe sweep. `MCP_ALLOW_EXECUTE`, named as the re-enable
  switch by the module comment and the published page, has no reader in either
  runtime — the `/vault` hint shape pointed at an environment variable.
  (`tests/test_the_mcp_adapter_says_what_it_does.py`,
  `app/test/the_published_mcp_tools_are_tools_the_route_answers.test.js`.)

  **PARTLY ANSWERED for bot/formatters/, and the instrument was the finding.**
  24 files and 8,073 lines of renderers were driven rather than read: every
  single-dict formatter called with nothing readable, and the card read back.
  The TEXT cards were honest — earlier slices had cured them — and the PNG
  cards were not, because nothing in this tree could read a PNG as text. The
  only instrument that existed counts PIXELS, which answers a COLOUR claim
  and not *what did it say*, and its fixture carries a readable change, so it
  could not have told a coerced figure from an honest one.

  Five cards printed a measured zero from a bare `{}`: `CONFIDENCE 0%` and
  `SCORE 0%` in the accent colour, `+0.00% 24h` in green three lines under a
  `$—` that abstains, `+0.0%` beside a green direction dot, and a
  `LONG | HOLD` label over an empty string. The coercion was at the PRODUCER
  as well — `float(tk.get("percentage") or 0)`, where ccxt reports `None` for
  a market with no published change and `app/lib/tickers.js` already writes
  `change: null` for the same fact — and `skill_registry`'s text scan card had
  been counting `unread` as its own bucket the whole time, so the PNG's
  producer was the uncured copy of an aggregate its sibling had already
  fixed. `tests/png_text.py` is the seam that makes every PNG renderer
  driveable; `pct_on_record` is the reading, and a MEASURED zero still prints
  in green on every one of those cards. Recorded in CLAUDE.md under "A PNG IS
  A SURFACE NO GUARD HERE COULD READ AS TEXT".

  The other seven packages (`bot/guardian/`, `bot/compliance/`,
  `bot/proofofpnl/`, `bot/risk/`, `bot/llm/`, `bot/db/`, `bot/api/`) are still
  unread — about 18,000 lines.

- README.md and README.zh-TW.md — the task named the README command table
  explicitly and I did not open it. I used bot/skills/command_catalog.py
  instead, on the strength of its own claim that a test asserts catalogue and
  registered handlers match EXACTLY. I did not run or read that test.

- docs/ — I read the first 80 lines of ROADMAP.md and 40 lines of
  FEE_REDUCTION.md. Roughly 80 other docs/*.md are unread, including
  TOKEN_ROADMAP.md, CROSS_CHAIN_REBALANCE_DESIGN.md, ONCHAIN.md,
  HYPERLIQUID_INTEGRATION_DESIGN.md, MULTI_VENUE_RISK_SPLIT.md,
  NEWS3_PERSONAL_INGEST.md and every AUDIT_REPORT_V*.md. Any shipped feature
  described only there is missing from my sweep.

- contracts/rune/RuneOfEntry.sol — never opened. The NFT mint rows were taken
  on the other agents' word; I did not check royalties, supply, or any
  secondary-market economics the contract might carry.

- app/public/js/ — I read dashboard.js selectively (grep + a few windows) and
  strengthmap.js at one line. About forty other client scripts (duel.js,
  sandbox.js, venue-rows.js, embed-arena-view.js, miniapp-arena.js, learn*,
  arena*) are unread, so panels whose fetch call I did not grep for may hide
  doors.

- website/ (the static marketing archive, ~40 assets plus archive/ and
  changelog/) and site/src/routes/*.tsx (5 prerendered pages) — listed, not
  read.

- agentbench/, benchmark/ (beyond the four scorecard filenames), demo/,
  playbooks/, evidence/, audit/, dashboard_static/, ollama/, nginx/, config/ —
  not examined at all.

- scripts/ — I read headers of receipts.py and gen_agent_scorecards.py and
  listed the directory. The other ~45 scripts, plus scripts/monitoring/ and
  scripts/cloudflared/, are unread; operator-shell income tooling could be
  sitting in any of them (the presale rows the other agents found are exactly
  that shape).

- bot/web/user_gateway.py — I extracted the 60-endpoint route table but read
  only handle_meme_swap_build and handle_trade_copilot. Endpoints I did not
  trace to a caller: /account/purge, /guardian/review*, /policy/*, /profile,
  /share-card, /user/strategy, /chat/history.

  ANSWERED, in the negative, which is the useful direction here: every one of
  the seven has a caller and eleven of the twelve handlers behind them reach an
  authorisation decision. `/account/purge` is `app/auth.js`'s delete path,
  `/chat/history` hydrates the chat drawer through `app/routes/chat.js`,
  `/user/strategy` is `app/routes/botstrategy.js`, `/share-card` is
  `app/routes/share.js`, `/guardian/review*` is `app/routes/guardian_review.js`
  and the four `/policy/*` are `app/routes/controls.js`. Driven for the gate
  rather than grepped: five carry `_guard_user` (and through it
  `permission_denial`, so F-14 staleness applies), two carry `_is_admin_id`,
  and the four policy handlers carry `_is_admin_id` through
  `_policy_op_guard`. The twelfth, `handle_share_card`, reaches no
  authorisation decision and is named in `scripts/guard_lint.py`'s
  `web-route-auth` rule as one of eight public-by-design exemptions, each with
  its own reason — "PNG from three clamped query params" — so the absence is a
  recorded decision rather than a gap.

  **And the first probe written for this reproduced the blind spot this repo
  had already recorded.** Reading each handler's OWN body for a gate call said
  the four `/policy/*` handlers had none, because the gate is one frame out in
  `_policy_op_guard` — the same one-hop gap the `check_user` ratchet documents,
  manufacturing exactly the accusation it exists to make. Following local
  helper hops to a fixed point is what answered it.

- Permission and tier gating for nearly every door I report. I verified
  REGISTRATION and WIRING, not who may run what. The only guards I actually
  read are @guard("rwa") on /rwa and @guard("scan") on /stockscan. Whether a
  paper-tier or signed-out caller reaches any of the rows above is
  unmeasured — and this repo's own history (the pro_scan/premium_scan
  mismatch) says skill name and feature name are not the same noun.

  PARTLY ANSWERED, and the hazard it names was real. The $RCLAW tier gate was
  driven with the gate enabled and no wallet linked: `check_user` is keyed by
  FEATURE and answers `(True, "ok")` for a name it does not hold, and
  `chat_tools._tier_verdict` passed it the SKILL — so eight of the nine paid
  skills were withheld and `pro_scan`, sold as `premium_scan`, was OFFERED.
  The capability card read "a scan tuned to one timeframe" above "8 more need
  a linked, verified wallet", undercounting by the row it had just offered.
  Not an execution bypass: the dispatch does read `feature_for`, so the caller
  was invited and then refused. Fixed, with a structural ratchet over every
  `check_user` call site and a mutation round; CLAUDE.md records it under "THE
  GATE IS ASKED ABOUT A FEATURE". The rest of the doubt stands — this measured
  ONE gate family, and the ROLE gate across these rows is still unread.


### Doubts the sweep held about its own answers

- EVERY claim above is code-reading, not execution. This was a read-only task
  and I ran nothing — no route was fetched, no command dispatched, no panel
  rendered. 'Reachable' here means: a registration or mount I read, plus a
  caller I grepped for and found. That is strictly weaker than driving it,
  which is the distinction this repo's #999 card (present, never reached)
  exists to mark.

- The Telegram /rwa door is CONDITIONAL in a way the row does not show:
  rwa_card_text fetches the card over the web app's card route. On a deployment
  where the web app is unreachable or WEBSITE_URL is unset it sends the
  channel-down sentence and no radar. The web panel and the chat intercept do
  not have that dependency. ANSWERED since: the fetch answers None for a
  non-200 and `web_card_text` answers None for a payload with no card string,
  so the command says which surface did not answer rather than rendering an
  empty card. (It used to pull the PAYLOAD and format it here, in a second
  Python copy of the website's card — which raised `TypeError` on the honest
  `null` the radar publishes for an unreadable 24h change. One renderer now.)

- All four Markets radars (rwa, meme, onchain-flow, strengthmap) read live
  venue tickers through app/lib/tickers.js. I did not verify their failure
  behaviour myself; app/lib/rwa.js's header claims an unlisted symbol is
  omitted rather than invented, which is the 'omit' strategy CLAUDE.md
  sanctions, but I read the claim, not the code path. DRIVEN since, and the
  claim held per-token and per-CATEGORY and failed above them: the SECTOR
  rollup was the uncured original of the aggregate the category loop had
  already fixed, so an unreadable row diluted the headline toward zero
  (-5.94% where the honest weighted mean over the rows that reported one is
  -7.04%, printed three lines apart on one card), a raw-subtraction sort named
  an unreadable row the sector's top gainer in a down market, `round2(null)`
  published BTC's unread change as flat, and a 200 that carried no rows was
  reported as the venue having delisted the sector. One aggregate now, with
  its sample beside the mean. onchain_flow.js is the sibling that got it
  right (`flowRow` answers null and `buildFlowRadar` names the bases in
  `unavailable`); strengthmap.js was cured earlier and its header records the
  same defect. meme.js's or-zero is at the NORMALIZER rather than the
  aggregate and its buys/sells feed a SAFETY read, so it was its own slice.
  DRIVEN since, and it was worse than that note: an unreported sells count was
  byte-identical to a measured zero, so `no-sells-yet` ("can't exit?") and
  `buys-only-skew` both fired and the risk tier was escalated to extreme, from
  a field nobody read, on the read that module's header calls the one a future
  agent-buy will gate on. The same coercion decided the ranking the header
  claims is "by real volume", the payload cap, the sector and per-chain totals
  and `top_by_volume`; `fmtVol` then printed `$0 liq` for a liquidity the
  normalizer had honestly kept null, while `riskRead` three lines away guarded
  it correctly. And `fetchTrendingPairs` answered `[]` for four different
  facts, so a failed read reached the card as "may be refreshing" and the
  dashboard panel as "no pairs clear the radar's liquidity and age floor" — two
  filters this radar does not have, in fourteen languages. All three signals
  and the volume are three-valued now, with their samples; `card_nums.js` is
  one volume rendering for the three cards that print one.

- ANSWERED. The $RCLAW staking row is still the one I would most expect a
  reader to over-read — the Anchor program says UNAUDITED / DO NOT DEPLOY in
  its own README, and it should be read as 'built and read by a gate', never
  as 'staking yield a user earns today'. But the two things I had not opened
  `bot/token/tier_gate.py` to confirm are both confirmed, and neither rests on
  a comment.

  The default is OFF, and **doubly** so: `gate_enabled()` is
  `_env_bool("TOKEN_TIER_GATE_ENABLED") and bool(mint_address())`
  (`tier_gate.py:365`), so an unset flag and an unconfigured mint each keep it
  inert on their own.

  The memcmp offsets are machine-checked from BOTH sides, which is more than
  the doubt asked for. `programs/rclaw_staking/src/lib.rs` holds `pub mod
  layout` and `layout_tests::borsh_offsets_match_the_python_gate` asserts every
  constant against the **Borsh** encoding rather than the in-memory struct —
  its own comment says why, since the compiler may reorder fields and
  `offset_of!` would prove nothing about what a client reads off the chain —
  and that test runs in the `Staking program (cargo)` CI job. The Python
  mirror is not trusted to match by prose either:
  `tests/test_token_tier_gate.py:711` parses `pub const NAME: usize = N;`
  straight out of `lib.rs` and compares each one, under a comment recording
  that the README used to claim the offsets were 'machine-checked on both'
  when only one side was — *"This is the missing half."* Derived by hand
  against the struct as a third reading, the two agree: discriminator 8,
  version 8, owner 9, mint 41, amount 73, staked_at 81, unlock_at 89, bump 97,
  `SPACE` 90, `RESERVED` 64, total 162.

- ANSWERED, and the chain was driven rather than read. The web3 sign /
  cross-plan / deploy rows said ADMIN-ONLY off the route files' own header
  comments (`app/routes/web3_execute.js:52, :89, :121`), and a comment that
  misdescribes which half of a gate is off is a failure mode this repo has
  recorded before. All three re-checks exist and refuse:
  `handle_web3_sign` (`bot/core/user_gateway.py:4517`), `handle_cross_plan`
  (`:1721`) and `handle_contract_deploy` (`:1606`) each `403` a non-admin —
  and the last of those is why the check had to be driven rather than
  grepped, because a search for `handle_web3_deploy`, the name the route
  suggests, matches nothing.

  **The comments holding moved the question to the LINK they depend on**, and
  that is where the answer is worth keeping: the gateway re-checks admin
  against a `telegram_id` **read out of the request body**, which the web
  layer supplies. Read end to end, nothing on that path is caller-chosen:
  `authMiddleware` (`app/auth.js:212`) sets `req.user` only from a
  `jwt.verify`'d token that also passes a revocation check, and
  `resolveBotIdentity` (`app/lib/identity.js:18`) then reads the telegram id
  out of the DB row keyed on `req.user.user_id` — never off the body, the
  query or a header — so the id the gateway admin-checks is the one the
  database holds for the JWT's own subject. `_is_admin_id`
  (`bot/skills/telegram_handler.py:4846`) is server-side too: the user store's
  role, or `ADMIN_TELEGRAM_IDS`. An escalation needs a foreign `telegram_id`
  written onto your own row, which is the invariant
  `identity.foreignIdentityBlock` already documents and asserts.

  One residual, stated because it is the half this repository cannot check:
  that invariant's storage-layer leg — *"`idx_users_telegram_id` makes the
  collision impossible"* — is a claim about a MySQL schema **this repo does
  not contain** (there is no `.sql` file in the tree). It is not load-bearing
  for the web3 routes, which have no second subject to disagree with, and
  `foreignIdentityBlock` was written to assert rather than trust it. It is
  recorded so the next reader does not take the index for something a test
  here proves.

- ANSWERED, and the boundary is stronger than the prose I was relying on. The
  meme rows must not be read as the bot trading memecoins, and
  `tests/unreachable_baseline.txt` stated `would_execute` is a hardcoded False
  with signing a slice that does not exist. Driven rather than taken from that
  file: `would_execute` is **not a function** — a search for
  `def would_execute` matches nothing, which is this repo's own 'grep the
  definition, not the name you remember' arriving from the other direction,
  since it was the baseline that named a dict KEY as though it were a method.
  It is a literal at its single construction site
  (`bot/core/meme_executor.py:131`, `"would_execute": False,  # planner only —
  never signs here`), and nothing anywhere computes it.

  What the baseline does not say is that there is a **fail-closed consumer**:
  `meme_swap.build_swap` refuses outright on `plan.get("would_execute") is not
  False` (`bot/core/meme_swap.py:178`), driven by
  `tests/test_meme_swap.py:132`. So the claim is not merely 'the planner never
  sets it' — a plan that DID claim it would execute is refused by the builder
  one layer down. `bot/web/user_gateway.py:1839` forwards the planner's own
  value to the web and manufactures nothing.

- ANSWERED, and what driving it found was one route over. /miniapp/arena
  'can act' is TRUE: `miniapp-arena.js` POSTs `/api/arena/open` and
  `/api/arena/close`, both `authMiddleware` + `tradeLimit`, on the caller's
  own VIRTUAL account (§4: "virtual funds only — nothing here can move real
  money"). The three season-administration routes carry an in-body
  `adminOnly(req, res)` that a middleware-chain read cannot see, which is the
  same blind spot `tests/command_gates.py` documents for the bot.

  **What the check for that found is in `GET /api/reports`.** Asking the
  question properly meant driving every express router's dispatch chain IN
  ORDER rather than grepping, and that walk says 93 of 276 routes carry no
  auth-family middleware — 17 of them invisible to `public_no_dollars.test.js`,
  whose public set was `!src.includes('authMiddleware')` at FILE level. One
  was `/api/reports`, which has no auth AND no limiter and was publishing
  `arb.carries[].earned_usd` per coin plus `parity.net_pnl` and
  `parity.total_fees` — the operator's realized net and fees on the LIVE book,
  justified in that route's header as "already public on /track" when /track
  indexes its equity curve to 100 precisely so no account size escapes. Fixed
  at the PRODUCER (`bot/core/web_reports.py`), because the route forwards the
  bot's sections wholesale and no key under `app/routes/` spells either name.
  Recorded in CLAUDE.md under the public-surface rules.

- A correction rather than a doubt: the earlier agents' 'Active Trading / Spot
  trading: /swap page'. There is no /swap route — app/public/swap.html is
  served only as /swap.html, and the page's own head comment says its
  canonical tag once named a 404 for exactly this reason. The capability
  exists; the address in that row does not.

- ANSWERED, and the answer was yes. Driven (`Object.keys(TOOLS)` is 31), all
  thirteen MCP tools I name — get_rwa_radar, get_meme_radar, get_dex_compare,
  get_gas, get_agent_feed, get_alpha_intel, get_showcase_trade, run_what_if,
  get_flight_record, verify_call, get_seal_roots, get_track_record,
  get_signals — are inside the '31 read tools' the Building row counts
  generically, so those rows ARE double-reporting it. Driving the count is
  also what found the defect one paragraph up: `computesOnInput` answers five
  and every piece of prose describing the family said four.

- which_leaf is a guess wherever I wrote it. I was given only the non-'none'
  rows of a 90-leaf map, not the leaf list, so 'no leaf fits' may be wrong
  three ways: the map may well have an RWA leaf, a market-making leaf and an
  options leaf that simply returned none and were therefore invisible to me.
  RWA in particular I would expect to have its own leaf.

- ANSWERED, and the fear it named was the wrong one — which is why driving it
  was worth more than the doubt. The scan-family row (/intraday, /patterns,
  /squeeze, /sweep, /zones, /session, /mode, /quant) was "the weakest of the
  nineteen": registered by grep, handler bodies read for none, and the
  macro_skills precedent of five registered skills whose every attribute probe
  named a field that did not exist.

  **The macro_skills shape does not apply.** Walked by AST, the eight handlers
  make exactly THREE attribute probes between them, and all three name real
  attributes: `engine._last_scan_signals` (set at `bot/core/engine.py:916`),
  `CONFIG.deepscan_timeout_sec` (`bot/config.py:2584`, and three sibling call
  sites read it with no `getattr` at all) and `engine.analyzer`
  (`bot/core/engine.py:671`). Every handler guards its own read and has an
  honest empty state; `/sweep` and its neighbours already carry the
  forming-candle hygiene the shared cache slice added.

  **What the drive found instead was an AUTH hole and an honesty defect**, and
  both are fixed rather than filed. `/session` and `/funding` carried no gate
  of any kind — `/session` the lone ungated row of the 26 in "Scan & analyse",
  `/funding` the lone one of the 12 in "Market context" — joining `/alpha` and
  `/duel`, so a caller the bot had never admitted reached them with no
  allowlist gate, no rate limit and no registration, and two of the four spend
  a live venue fetch per invocation. And `/funding`'s card answered *"No
  funding data found ... check the symbol"* out of two swallowed venue reads,
  which is CLAUDE.md's own opening example with a remedy attached. The gates,
  the card, a ratchet over every registered command's gate (`none` rows
  carrying a reason) and the six gate spellings that measuring this turned up
  are all recorded in CLAUDE.md under "A COMMAND WITH NO GATE IS ABSENT FROM A
  BASELINE OF WHAT IS GATED".

  Registration is still not behaviour, and the remaining six handlers were read
  rather than grepped for this answer.

### One of those doubts can be answered from here

The critic's last doubt is that its `which_leaf` guesses might be wrong because
it was handed only the non-`none` rows and never the leaf list. Against the
full ninety:

- **RWA has no leaf.** The critic suspected it would ("RWA in particular I
  would expect to have its own leaf") and it does not. Tokenised real-world
  assets are the largest capability on this page with nowhere on the map to
  put them.
- **Tokenised stock perpetuals have no leaf either** — no leaf on the map
  contains the word *stock*. A whole asset class, with its own market-session
  detection and risk overrides, that the map does not ask about.
- **Options does have a leaf** (Active Trading), and nothing serves it.
- **Market-making has a leaf** only inside Prediction & Forecasting
  ("Market-making on prediction platforms"). There is no general
  market-making leaf, so a CEX/DEX maker strategy would also land nowhere.

That is a finding about the map rather than about the product, and it is the
reason this document lists the unmapped capabilities above instead of
forcing each into the nearest leaf. Forcing them would have made the coverage
look better and told you less.
