# ADR 0008: Per-user live trading and the Authority Envelope

- **Status:** Proposed
- **Date:** 2026-09-29
- **Plan items:** D12 (docs/IMPROVEMENT_PLAN_2026-09-29.md, "D. Capital gate and execution integrity"); "Decisions the owner needs to make" #12
- **Informed by:** ADR 0001 (not yet run) for `PER_USER_LIVE_ENABLED`, `WEB_LIVE_TRADING_ENABLED` and `LIVE_OPEN_TO_KEY_HOLDERS` in production.

## Context

`PER_USER_LIVE_ENABLED` (default off, `bot/config.py`
`AppConfig.per_user_live_enabled`) makes the engine build an executor on each
user's own keys; with it on, a regular user's live confirm is refused unless
their own keys are linked and decrypt (`bot/core/engine.py`
`RuneClawEngine.per_user_live_eligibility`). Web live trading additionally
requires a bound Authority Envelope in enforce mode
(`bot/web/web_live_gate.py`, the `envelope_enforcing` precondition). On
Telegram an enforce-mode envelope is asked on the live execute path when the
person has one bound (`RuneClawEngine._own_account_order_authority`), but none
is required: a person with no envelope trades their own keys unbounded by
one. The envelope ships default off, with
`shadow` and `enforce` modes (`docs/guardian_authority.md`, "Staged
enforcement"; `bot/guardian/order_authority.py`).

At `/connect` the withdraw scope is probed on Bitget and told to the user
(`bot/guardian/authority_preflight.py` `withdraw_notice`; other venues read as
"not readable"), and the key is stored whatever the answer
(`bot/skills/account_commands.py`). The same card always says "Per-user live
trading is not yet enabled", whatever the flag holds.

## Options

- **(a)** Require an enforce-mode envelope before `PER_USER_LIVE_ENABLED` is
  ever turned on, for Telegram as for web.
- **(b)** Keep today's behaviour and correct `docs/guardian_authority.md` and
  every surface that repeats it so none implies an envelope bounds per-user
  Telegram live.
- **(c)** Keep per-user live off.

In every option: refuse a key whose withdraw scope is readable and enabled at
`/connect`, unless the operator explicitly overrides, and name only the
exception class in a re-check refusal.

## Recommended default

(c) during research mode (ADR 0002), and (a) before it is ever re-enabled.

## Consequences

- No per-user live order is placed while research mode holds; the flag stays
  off in production (confirmed by ADR 0001's readout once run).
- Turning it on later needs a drive showing a per-user Telegram confirm refused
  without an enforce-mode envelope and placed within one.
- The `/connect` card reads the flag instead of a fixed sentence.
- Target: 0 new withdraw-enabled keys stored.

## Decision

Decision: pending operator.
