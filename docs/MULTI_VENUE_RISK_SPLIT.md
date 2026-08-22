# Scoping simultaneous multi-venue trading

**Status: scope only. No code has been written for this.**

The question: today a user can *connect* several venues and *trade* on one. What
would it take for one user to trade several venues at the same time, safely?

The credential half is done and shipped. This document is about the other half —
the risk accounting — and it exists because a half-done version of it is worse
than not doing it at all: a kill switch that halts one venue while another keeps
trading is a worse product than a single-venue bot.

---

## 1. What already exists, which is more than it looks

The first assumption to discard is that this needs new architecture. **The split
by key already exists and is in production — it was built for per-USER, and
per-venue is the same shape one dimension over.**

| already built | where |
|---|---|
| a `RiskEngine` per key | `engine.py` — `self._user_risk: dict[str, RiskEngine]` |
| a `PortfolioTracker` per key | `bot/risk/multi_portfolio.py` — `MultiUserPortfolio` |
| per-key state files | `data/risk_state_{user}.json` |
| a resolver with a safe default | `risk_for(user_id)` — returns the shared operator engine when the flag is off |
| a per-key executor | `_executor_for(user_id)` → `LiveExecutor(user_id, credentials, venue)` |
| per-venue credentials | `set_venue` / `list_venues` / `delete_venue`, and the web API |

So the pattern, the persistence, the lazy-create-and-cache, and the
"default is byte-identical to before" discipline are all proven code. This is
not a rewrite. It is a second key.

## 2. The rule that governs the whole thing

`risk_for()`'s own docstring already states it, and it transfers to venues
without modification:

> Only ACCOUNT-specific state is isolated. MARKET-wide context (regime,
> order-flow signal, rolling price history) is shared from the operator engine
> via `_sync_risk_market_context` so every user evaluates against identical
> market conditions — **the per-user split must never loosen a market gate, only
> separate the account breakers.**

For venues: **an account breaker is per (user, venue). A market gate is not.**
BTC's regime does not change because you are looking at it from Bybit.

Getting this backwards is the way this feature turns into a loosening. Two
venues each with their own "max 5 open positions" is ten positions against one
person's money, and nobody chose ten.

## 3. What splits, what does not, and what must be decided

### Splits per (user, venue) — these are account facts

- circuit breaker (`circuit_open`, `circuit_trip_cause`, `circuit_trip_day`)
- consecutive-loss streak (`consecutive_losses`, `last_loss_time`)
- equity high-water mark (`live_equity_peak`) and the drawdown measured off it
- open positions and their trailing state
- realised PnL, trade history, per-day PnL
- free-margin clamp — margin is venue-local by definition; Bybit's balance
  cannot fund a Bitget order

### Does NOT split — market facts, shared as they are today

- market regime, order-flow signal, rolling price history
- funding/positioning reads (already cross-venue, already on request)
- the scan universe

### Must NOT split, and this is the safety core

- **The kill switch.** `live_executor.py` holds `_HALT_CHECK` as a MODULE-LEVEL
  global, set once by the engine via `set_halt_check`. Every executor —
  per-user, per-venue, all of them — consults the same function, and
  `trading_halted()` fails CLOSED. One stop button stops everything, today, and
  that property must survive this change untouched.
- **The flatten path.** "Close everything" must mean everything, across every
  venue the user has open, or the emergency exit is a partial one.

### Genuinely undecided — these are product calls, not engineering ones

These are the questions this scope cannot answer for you, and each changes the
work:

1. **Are the caps per-venue or per-person?** Max open positions, daily loss
   limit, drawdown limit, max margin. Per-venue is simpler to build and
   multiplies the real exposure by the number of venues. Per-person is the
   honest reading of "my daily loss limit" and requires a cross-venue
   aggregate the engine does not currently compute.
2. **Is drawdown measured per venue or on total equity?** A 10% drawdown on one
   venue while another is up may not be a drawdown at all.
3. **What does the loss streak count?** Three losses on Bitget and two on Bybit
   — is that a streak of five?

My recommendation on all three: **caps and drawdown per PERSON, breakers per
VENUE.** A cap is a statement about how much of your money is at risk, and money
is not per-venue. A breaker is a statement about a book behaving badly, and a
book is. But this is a decision to make deliberately, not to inherit from
whichever is easier to code.

## 4. The one real blocker

**`TradeExecution` has no `venue` field.** Neither does `TradeIdea`.

```python
class TradeExecution(BaseModel):
    trade_id: str
    asset: str
    direction: Direction
    ...
```

A closed trade cannot say where it happened. Every downstream consumer — PnL
attribution, the track record, the streak counter, drawdown, the trade journal,
Proof-of-PnL — is venue-blind, and would silently pool two venues' results into
one number the moment a second venue starts trading.

This is the foundation and it comes first. It is also the piece most likely to
have a long tail: the field has to be added, back-filled as a default for every
existing record, written at every construction site, and carried through every
serialization boundary — including the ones that cross into the web app and the
public track record.

Positions themselves are fine: `_positions` is keyed by `idea.id`, a UUID, so
two venues holding BTC do not collide.

## 5. Phases

Each phase is shippable and leaves the product correct if the next never lands.

**Phase 0 — attribution.** Add `venue` to `TradeExecution` and `TradeIdea`,
default `"bitget"` so every existing record stays valid. Write it at every
construction site. Nothing changes behaviourally; every trade can now say where
it happened. *This is the phase that must not be skipped, and the one that
makes the rest measurable.*

**Phase 1 — read-only truth.** Surface per-venue PnL, positions and equity in
the dashboard and `/portfolio`, from the field Phase 0 added. Still one active
venue, so the numbers are trivially correct — which is exactly why it is the
right time to build the reporting, before there is anything to get wrong.

**Phase 2 — the key.** `risk_for(user_id, venue)` and
`portfolio_for(user_id, venue)`, defaulting to today's behaviour when the flag
is off. State files become `data/risk_state_{user}_{venue}.json`. Mirrors
`risk_for()` exactly, including "operator and unattended paths keep the shared
engine".

**Phase 3 — the aggregate.** The cross-venue view the caps need: total equity,
total exposure, combined daily loss. This is new computation, not a refactor,
and it is where the decisions in §3 get implemented.

**Phase 4 — enable, behind a flag, in shadow.** `MULTI_VENUE_TRADING_ENABLED`,
default off. The same ladder every other control here uses.

## 6. Size

Rough, and stated as a range because the tail on Phase 0 is the uncertain part:

| phase | shape | risk |
|---|---|---|
| 0 | one field, many call sites | low, wide |
| 1 | reporting over existing data | low |
| 2 | mirror an existing pattern | medium — it is the money path |
| 3 | new aggregation logic | **highest** — this is where a cap can be silently loosened |
| 4 | flag + shadow | low |

The migration surface for Phase 2: `risk_for()` has 14 call sites against 27
direct `self.risk.` uses, and `user_portfolios` 15 against 25 direct
`self.portfolio.` uses. **Roughly two thirds of the risk and portfolio reads
still go to the shared instance.** Those are not all wrong — many are market
context or operator paths that SHOULD be shared — but each one has to be
classified before it can be trusted, and that classification is most of the
work in Phase 2.

## 7. What I would want proved before it is enabled

Not tests-in-general; these specific claims, because each is a way this feature
fails silently rather than loudly:

- One kill switch halts **every** venue. Drive the real halt path with two
  venues open and assert both refuse.
- `/flatten` closes positions on **every** venue, not the active one.
- A cap counted per person is not multiplied by the venue count — plant
  positions on two venues and assert the person-level cap still binds.
- A loss streak on one venue does not trip a breaker on another **if** the
  decision in §3 says breakers are per-venue — and does if it says otherwise.
  The test has to encode the decision, whichever it is.
- Drawdown is measured against the basis the decision names, and says which
  basis it used. (`/risk` already learned this lesson once: it substituted the
  paper number for the enforced one and printed HEALTHY over an unreadable
  reading.)
- A venue whose balance cannot be read is `unknown`, not `0` — an unreadable
  balance must not read as "no margin here" or, worse, as free margin.

## 8. Honest gaps in this scope

- I have not traced the **stop-loss / take-profit monitor** or the trailing-stop
  path per venue. Those run against open positions and will need the same key.
- I have not measured how the **Flight Recorder / Proof-of-PnL** chain treats
  venue, and it publishes.
- The **web app's** portfolio and track-record payloads assume one venue per
  user in places beyond the two I corrected; §Phase 1 is where that surfaces.

Each is a reason the Phase 0/1 ordering matters: they are all consumers of the
attribution field, and all of them are cheaper to find while there is still only
one venue trading.
