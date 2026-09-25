"""Web live-trading gate — the ONE decision for 'may this web user trade live'.

Background: web-only accounts ("web:<id>") are structurally paper-only today —
`_can_trade_live` and `UserStore.can_trade_live` both hard-return False for them.
That is the correct default and this module does NOT weaken it. Instead it adds
a SEPARATE, fail-closed path that opens live execution for a web user on THEIR
OWN connected exchange keys, and only when every safety precondition holds:

    1. feature_enabled   — operator turned the whole capability on
                           (env WEB_LIVE_TRADING_ENABLED, default OFF).
    2. bot_is_live        — the bot itself is in live mode (not paper/demo).
    3. routes_to_own_account — the bot builds per-user executors at all
                           (PER_USER_LIVE_ENABLED, default OFF). Without it the
                           engine's resolver answers the OPERATOR's executor for
                           every caller, so every other precondition could hold
                           and the order would still run on the operator's
                           account — the one thing this gate's premise ("on
                           THEIR OWN keys; RUNECLAW custodies nothing") rules
                           out. Driven before this row existed: every other
                           input satisfied, per-user live at its shipped
                           default, the gate said "all preconditions met" and
                           `_executor_for` answered `engine.live_executor`.
    4. user_opted_in      — the user has the dedicated web_live_enabled flag
                           (distinct from can_trade_live, so a stale legacy flag
                           can never open this path).
    5. has_own_keys       — the user connected their OWN venue credentials; the
                           trade routes to their account, RUNECLAW custodies
                           nothing.
    6. envelope_enforcing — a bound Authority Envelope in ENFORCE mode caps what
                           the trade may do (notional, symbols, drawdown) and is
                           revocable. This is the human-set, revocable authority
                           the custody discipline requires — no live web order
                           exists outside one.

All six must hold. The gate FAILS CLOSED: any missing/unknown input → paper,
with a reason naming the first unmet precondition so the UI can guide the user.
Pure and deterministic; the gateway sources the inputs and wires the result.

The third is a statement of what SHOULD happen, read off configuration; the
confirm handler also checks what WOULD happen — the executor the resolver
actually answers for this caller must not be the operator's — because a flag
is not a measurement of the resolver, which still falls back to the operator's
executor for a user whose keys it cannot use.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Optional

# Ordered preconditions: (key, human reason when unmet). First unmet wins.
_CHECKS = (
    ("feature_enabled", "live web trading is not enabled by the operator yet"),
    ("bot_is_live", "the bot is in paper mode — no live orders are placed"),
    ("routes_to_own_account",
     "per-user live accounts are off on this bot (the operator's "
     "PER_USER_LIVE_ENABLED), so a live order would run on the operator's "
     "account, not yours"),
    ("user_opted_in", "you haven't enabled live trading for your account"),
    ("has_own_keys", "connect your own exchange keys first (/connect)"),
    ("envelope_enforcing",
     "set an Authority Envelope in enforce mode — it caps and authorizes every "
     "live order, and is revocable at any time"),
)


@dataclass(frozen=True)
class WebLiveDecision:
    allowed: bool
    reason: str
    # Per-precondition pass/fail, so the UI can render a checklist of what's left.
    checklist: dict = field(default_factory=dict)


def feature_enabled(env: Optional[dict] = None) -> bool:
    """Operator master switch. Default OFF. Truthy = 1/true/yes/on."""
    raw = (env or os.environ).get("WEB_LIVE_TRADING_ENABLED", "")
    return str(raw).strip().lower() in ("1", "true", "yes", "on")


def routes_to_own_account(config: Any) -> bool:
    """Does this bot build per-user executors (PER_USER_LIVE_ENABLED)?

    The ONE reading both sourcing sites ask (`user_gateway._web_live_decision`
    and `web_live_admin.user_readiness`), so the operator's readiness card and
    the gate that decides a real order cannot answer that precondition
    two ways. `is True`, not truthiness: a stand-in config (a mock, a
    namespace without the field) is not a measurement that per-user accounts
    are on, and this is a gate that must fail closed.
    """
    return getattr(config, "per_user_live_enabled", None) is True


def evaluate(*, feature_enabled: bool, bot_is_live: bool, user_opted_in: bool,
             has_own_keys: bool, envelope_enforcing: bool,
             routes_to_own_account: bool) -> WebLiveDecision:
    """Decide whether a web user may place a LIVE order. Fail-closed."""
    state = {
        "feature_enabled": bool(feature_enabled),
        "bot_is_live": bool(bot_is_live),
        "routes_to_own_account": bool(routes_to_own_account),
        "user_opted_in": bool(user_opted_in),
        "has_own_keys": bool(has_own_keys),
        "envelope_enforcing": bool(envelope_enforcing),
    }
    for key, reason in _CHECKS:
        if not state[key]:
            return WebLiveDecision(allowed=False, reason=reason, checklist=state)
    return WebLiveDecision(allowed=True, reason="all preconditions met",
                           checklist=state)
