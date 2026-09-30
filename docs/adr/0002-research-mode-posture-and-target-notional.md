# ADR 0002: Research-mode posture and target notional

- **Status:** Proposed
- **Date:** 2026-09-29
- **Plan items:** D1, D2 (docs/IMPROVEMENT_PLAN_2026-09-29.md, "D. Capital gate and execution integrity"); "Decisions the owner needs to make" #1
- **Informed by:** ADR 0001, which has not been run. What production holds for every flag below is unknown.

## Context

No strategy has an eligibility record (`benchmark/eligibility/` holds only its
README). Autonomous live orders are refused at the Lock-5 mint unless
`AUTO_CONFIRM_LIVE_ENABLED` is on and the record says the running strategy
survives (`bot/core/engine.py` `RuneClawEngine._autonomous_live_refusal`,
`bot/core/live_eligibility.py` `read_eligibility`). The flag's code default is
on (`bot/config.py` `AppConfig.auto_confirm_live_enabled`); `.env.example` sets
it off.

`MICRO_MAX_POSITION_USD` and `MICRO_MAX_TOTAL_EXPOSURE` are MARGIN caps, not
notional (`ExecutionConfig.max_live_position_usd`, whose comment says
"exchange notional is margin x leverage"). The standard leverage is
`ExchangeConfig.default_leverage` (code default 5), and a venue-minimum
round-up may place up to `ExchangeConfig.exchange_min_roundup_max_mult` (code
default 1.5) times the risk-sized quantity. A cap written as a notional is
therefore off by the leverage multiple.

## Options

- **(a)** Full shadow: no live orders at all.
- **(b)** Research mode: venue-minimum live orders, human-confirmed only.
  `AUTO_CONFIRM_LIVE_ENABLED=false`; choose a target notional N per order
  (about the largest venue minimum to be traded); set
  `MICRO_MAX_POSITION_USD = N / DEFAULT_LEVERAGE x EXCHANGE_MIN_ROUNDUP_MAX_MULT`
  and `MICRO_MAX_TOTAL_EXPOSURE` to k times that margin; keep
  `PER_USER_LIVE_ENABLED` off. The eligibility check at the mint stays the
  first line and the flag the second.
- **(c)** Status quo, relying on the governor.

## Recommended default

(b). It stops autonomous losses and keeps fills, fees and close pricing
flowing, which the cost model (A7), the venue-priced share (A6) and the replay
corpus (ADR 0010) all need. Capital rises only through eligibility records
(D2, D11), never by editing the caps.

## Consequences

- Before switching, the executor's minimum gate is run offline against a
  planted venue over the live universe and the refused symbols are counted and
  recorded here; a refusal places nothing.
- D1 makes `/status` and the web backstop say "research mode"; shadow
  recording and signal publishing stay on.
- The operator is the only live confirmer, so at least one minimum-size close a
  week is needed to keep execution telemetry flowing.
- N, k and the refused-symbol count are operator inputs, recorded here when
  decided; none is chosen by this ADR.

## Decision

Decision: pending operator.
