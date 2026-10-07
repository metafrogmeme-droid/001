"""
RUNECLAW Cost Tracker -- session operating-cost ledger.

Separates operating costs (LLM tokens, infra) from trade PnL by design.
Trading costs (commission, slippage) are attributed per-trade in the portfolio.
Operating costs are tracked at the session level and netted in the waterfall:

    Gross trading PnL
      - exchange commission
      - slippage
    = Trading net PnL
      - LLM token cost
      - infra/hosting cost
    = Strategy net PnL after agent costs

Fail-closed cost accounting: an unknown model is not assumed free.
Tokens are recorded and flagged as UNPRICED so the operator knows cost is
unknown, not zero.

Per-category breakdown: scan / analyze / thesis / risk_decision / other.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from typing import Optional

from bot.utils.logger import audit, system_log


# USD per 1,000,000 tokens.  ILLUSTRATIVE — verify against current provider
# pricing, which changes and differs by model.  Do not trust these numbers.
# Keep in config/env for production; hardcoded here for prototype convenience.
LLM_PRICING: dict[str, dict[str, float]] = {
    "gpt-4o":              {"in": 2.50,  "out": 10.00},
    "gpt-4o-mini":         {"in": 0.15,  "out": 0.60},
    "claude-sonnet-5":   {"in": 3.00,  "out": 15.00},
    "claude-sonnet-4-6":   {"in": 3.00,  "out": 15.00},
    "claude-opus-4-8":     {"in": 15.00, "out": 75.00},
    "claude-haiku-4-5-20251001":    {"in": 0.80,  "out": 4.00},
    "claude-3-5-sonnet-20241022": {"in": 3.00, "out": 15.00},
    "claude-sonnet-4-20250514":   {"in": 3.00, "out": 15.00},
    # ULTRA's thesis/learning model. It was in NEITHER this table nor the
    # family list below, so `resolve_llm_price` answered (None, False) and
    # every ULTRA thesis call booked $0.00 — disarming the daily dollar guard
    # on the most expensive model in the product. The figure is the one the
    # product itself advertises when an operator turns ULTRA on: "Fable 5
    # bills $10/$50 per MTok" (bot/llm/provider.py, the ultra toggle message).
    "claude-fable-5":      {"in": 10.00, "out": 50.00},
    "claude-fable-5-1":    {"in": 10.00, "out": 50.00},
}

# Family-prefix fallback (checked in order, first prefix match wins) for model
# IDs not in the exact table above. The operator can point any tier at any
# model via env; the exact table will always lag behind dated IDs like
# "claude-sonnet-4-20250514". An unmatched PAID model previously booked $0.00
# (UNPRICED), which silently DISARMED the daily dollar-budget guard — with a
# paid key on every tier that meant unbounded spend up to the call limit
# (live incident 2026-07-11: all 4 tiers set to claude-sonnet-4-20250514,
# not in the table). Family pricing is an approximation — Anthropic has kept
# per-family pricing stable across versions — and an approximate cost that
# arms the budget guard beats an exact $0 that doesn't.
LLM_PRICING_FAMILIES: list[tuple[str, dict[str, float]]] = [
    ("claude-opus-",   {"in": 15.00, "out": 75.00}),
    ("claude-sonnet-", {"in": 3.00,  "out": 15.00}),
    ("claude-haiku-",  {"in": 0.80,  "out": 4.00}),
    ("claude-3-",      {"in": 3.00,  "out": 15.00}),
    ("gpt-4o-mini",    {"in": 0.15,  "out": 0.60}),
    ("gpt-4",          {"in": 2.50,  "out": 10.00}),
    ("gpt-5",          {"in": 2.50,  "out": 10.00}),
    # Non-Anthropic tiers the routing tables actually use (Gemini, Alibaba Qwen)
    # were UNPRICED → booked $0.00 → disarmed the daily budget guard (same class
    # as the 2026-07-11 incident). Approximate family pricing arms the guard;
    # exact per-model entries can be added to LLM_PRICING above when needed.
    # More-specific prefixes first (first match wins).
    # Gemini 3.x (2026): Pro tiers priced higher than Flash. More-specific
    # prefixes first so a Pro id doesn't fall into the generic Flash bucket.
    ("gemini-3.5-pro",   {"in": 1.25, "out": 5.00}),
    ("gemini-3.1-pro",   {"in": 1.25, "out": 5.00}),
    ("gemini-3-pro",     {"in": 1.25, "out": 5.00}),
    ("gemini-2.5-pro",   {"in": 1.25, "out": 5.00}),
    ("gemini-2.5-flash", {"in": 0.30, "out": 2.50}),
    ("gemini-1.5-pro",   {"in": 1.25, "out": 5.00}),
    ("gemini-",          {"in": 0.30, "out": 2.50}),   # Flash-class catch-all
    # Groq GPT-OSS (open-weight, served on Groq — the migration target after
    # llama-3.3/3.1 deprecation). IDs are namespaced "openai/gpt-oss-*", so they
    # never match the "gpt-4"/"gpt-5" prefixes above; price them explicitly or
    # they book $0 and disarm the daily budget guard.
    ("openai/gpt-oss",   {"in": 0.15, "out": 0.60}),
    ("gpt-oss",          {"in": 0.15, "out": 0.60}),
    # xAI Grok (2026-07). More-specific ids first so 4.5 isn't under-priced by the
    # generic grok fallback — an under-priced model soft-disarms the budget guard.
    ("grok-4.5",         {"in": 2.00, "out": 6.00}),
    ("grok-4.3",         {"in": 1.25, "out": 2.50}),
    ("grok",             {"in": 2.00, "out": 6.00}),   # unknown grok → price high
    ("qwen",             {"in": 0.40, "out": 1.20}),
    ("deepseek",         {"in": 0.14, "out": 0.28}),
    ("mistral",          {"in": 2.00, "out": 6.00}),
    ("llama",            {"in": 0.20, "out": 0.20}),
    # Anthropic premium families. `claude-fable-`/`claude-mythos-` matched none
    # of the four Anthropic prefixes above (opus/sonnet/haiku/3-), which is how
    # the priciest model in the product came to book $0.00.
    ("claude-fable-",    {"in": 10.00, "out": 50.00}),
    ("claude-mythos-",   {"in": 10.00, "out": 50.00}),
    # LAST RESORT, and the reason it exists: every entry above names a family
    # somebody thought of. A model from a family nobody has thought of yet
    # books $0.00 and disarms the budget guard, and that has now happened
    # twice — 2026-07-11 with a dated Sonnet id, and again with Fable, which
    # was routed and advertised at $10/$50 while costing the accounting
    # nothing. Priced at the top of Anthropic's range deliberately: an
    # over-estimate trips the guard early, which is recoverable, and an
    # under-estimate does not trip it at all.
    ("claude-",          {"in": 15.00, "out": 75.00}),
]


def resolve_llm_price(model: str) -> tuple[Optional[dict], bool]:
    """Price for a model id: (price, exact). Exact table first, then the
    family-prefix fallback; (None, False) when nothing matches."""
    price = LLM_PRICING.get(model)
    if price is not None:
        return price, True
    m = (model or "").lower()
    for prefix, fam_price in LLM_PRICING_FAMILIES:
        if m.startswith(prefix):
            return fam_price, False
    return None, False

# Categories for per-bucket cost tracking
#: "chat" was missing, so `record_llm(category="chat")` was coerced to
#: "other" by the line that guards this tuple — and the cost row is the ONE
#: durable per-call record naming the model that served a turn. Chat spend
#: was therefore indistinguishable from analysis spend in /costs, on the
#: surface a user is actually waiting on.
COST_CATEGORIES = ("scan", "analyze", "thesis", "risk_decision", "chat", "other")


def _default_category_costs() -> dict[str, float]:
    return {cat: 0.0 for cat in COST_CATEGORIES}


def _default_category_calls() -> dict[str, int]:
    return {cat: 0 for cat in COST_CATEGORIES}


@dataclass
class CostSummary:
    """Point-in-time snapshot of session operating costs."""
    llm_cost_usd: float = 0.0
    infra_cost_usd: float = 0.0
    llm_calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    unpriced_calls: int = 0  # model not in price table — cost is UNKNOWN, not zero
    # Per-category breakdown
    cost_by_category: dict[str, float] = field(default_factory=_default_category_costs)
    calls_by_category: dict[str, int] = field(default_factory=_default_category_calls)

    @property
    def operating_cost_usd(self) -> float:
        return round(self.llm_cost_usd + self.infra_cost_usd, 6)

    @property
    def avg_cost_per_call(self) -> float:
        return round(self.llm_cost_usd / self.llm_calls, 6) if self.llm_calls > 0 else 0.0


#: The bound chat reaches when its own spend meets its share of the daily
#: dollar budget (`LLM_CHAT_BUDGET_SHARE`). A word of its own because the
#: refusal a person reads is different: the rest of the budget is the engine's.
CHAT_SHARE_BOUND = "chat's share of the dollar budget"
#: Chat's own dollar cap (`LLM_DAILY_BUDGET_CHAT_USD`), when the operator set
#: one. Distinct from the share: the thesis side is not "the rest of the same
#: budget", it has its own cap.
CHAT_OWN_BOUND = "chat's own dollar budget"


def chat_spend_usd(snap: CostSummary) -> float:
    """Today's chat spend, for chat's share of the daily budget.

    Chat books its calls under ``"chat"``. When that figure cannot be read (a
    snapshot without the category, or a value that is not a finite number),
    the answer is the TOTAL spend: every dollar may have been chat's, so chat
    stops at its share of the total. That is the strict direction; the other
    fallback, zero, would let chat spend the whole budget unseen.
    """
    import math

    total = float(snap.llm_cost_usd)
    by_cat = getattr(snap, "cost_by_category", None)
    value = by_cat.get("chat") if isinstance(by_cat, dict) else None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return total
    value = float(value)
    if not math.isfinite(value) or value < 0:
        return total
    return value


def chat_call_count(snap: CostSummary) -> int:
    """How many of today's calls count against chat's call limit.

    When ``calls_by_category`` adds up to ``llm_calls``, chat's own count is
    the reading: thesis calls must not refuse chat. A split that does not add
    up is not that reading — every call may have been chat's — so the total
    is what stops chat. A measured zero inside a split that does add up is
    zero.
    """
    total = int(snap.llm_calls)
    by = getattr(snap, "calls_by_category", None)
    if not isinstance(by, dict):
        return total
    accounted = 0
    for value in by.values():
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            return total
        accounted += value
    if accounted != total:
        return total
    chat = by.get("chat", 0)
    # The positive arm is what mypy can narrow. `dict.get` is Any, and the
    # complementary test (`bool or not int`) leaves the return as Any, which
    # the whole-tree ratchet counts as no-any-return.
    if isinstance(chat, int) and not isinstance(chat, bool) and chat >= 0:
        return chat
    return total


def non_chat_spend_usd(snap: CostSummary) -> Optional[float]:
    """Dollars that are not chat's, or None when that split cannot be read.

    None is not zero. A missing or non-numeric chat figure, or a chat figure
    larger than the total, means the non-chat amount is unknown.
    """
    import math

    try:
        total = float(snap.llm_cost_usd)
    except (TypeError, ValueError):
        return None
    if isinstance(snap.llm_cost_usd, bool) or not math.isfinite(total) or total < 0:
        return None
    by = getattr(snap, "cost_by_category", None)
    if not isinstance(by, dict) or "chat" not in by:
        return None
    chat = by.get("chat")
    if isinstance(chat, bool) or not isinstance(chat, (int, float)):
        return None
    chat = float(chat)
    if not math.isfinite(chat) or chat < 0 or chat > total + 1e-9:
        return None
    return total - chat


def thesis_dollar_exhausted(snap: CostSummary, llm_cfg) -> bool:
    """Whether trade analysis should fall back to the rule engine on dollars.

    With ``thesis_budget_usd`` unset, this is the combined total against
    ``daily_budget_usd`` — the behaviour an install keeps until it sets the
    cap. With the cap set, only non-chat spend counts, so a chat day on its
    own cap cannot move analysis onto rules. An unreadable non-chat split
    compares the total against the thesis cap: every dollar may have been
    analysis.
    """
    cap = getattr(llm_cfg, "thesis_budget_usd", None)
    if cap is None:
        return float(snap.llm_cost_usd) >= float(llm_cfg.daily_budget_usd)
    spent = non_chat_spend_usd(snap)
    if spent is None:
        return float(snap.llm_cost_usd) >= float(cap)
    return spent >= float(cap)


def chat_budget_bound(snap: CostSummary, llm_cfg) -> str:
    """Which daily bound chat has reached, or "" when none.

    One reading for every chat model call: the reply in `_llm_chat` and the
    rolling-note fold in `_summarize_if_due`, which used to call the chat
    model with no check at all and book nothing, so "chat stops at its share"
    was false of the calls made after every reply. `llm_cfg` is the caller's
    `CONFIG.llm`, handed in so this module reads no global.

    The combined dollar total is a ceiling on every LLM dollar, chat's
    included, so it is checked first. ``chat_budget_usd`` set replaces only
    the share: chat stops at its own cap or at the combined total, whichever
    comes first. A cap set above the total is reached at the total. Unset
    keeps the share, so an existing install does not change.
    """
    if chat_call_count(snap) >= llm_cfg.daily_call_limit:
        return "daily call limit"
    if snap.llm_cost_usd >= llm_cfg.daily_budget_usd:
        return "daily dollar budget"
    own = getattr(llm_cfg, "chat_budget_usd", None)
    if own is not None:
        if chat_spend_usd(snap) >= float(own):
            return CHAT_OWN_BOUND
        return ""
    if chat_spend_usd(snap) >= llm_cfg.daily_budget_usd * llm_cfg.chat_budget_share:
        return CHAT_SHARE_BOUND
    return ""


class CostTracker:
    """Session operating-cost ledger.  Separate from trade PnL by design.

    Threading model: same single-threaded asyncio assumption as RiskEngine.
    RLock is defensive only.
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._s = CostSummary()
        self._lifetime = CostSummary()  # W1 FIX: separate lifetime vs daily
        self._current_day: str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    def _maybe_reset_daily(self) -> None:
        """W1 FIX: Reset daily counters at UTC day boundary."""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if today != self._current_day:
            # Accumulate into lifetime before resetting daily
            self._lifetime.llm_cost_usd += self._s.llm_cost_usd
            self._lifetime.infra_cost_usd += self._s.infra_cost_usd
            self._lifetime.llm_calls += self._s.llm_calls
            self._lifetime.prompt_tokens += self._s.prompt_tokens
            self._lifetime.completion_tokens += self._s.completion_tokens
            self._lifetime.unpriced_calls += self._s.unpriced_calls
            # Reset daily
            self._s = CostSummary()
            self._current_day = today

    def record_llm(
        self,
        model: str,
        prompt_tokens: int,
        completion_tokens: int,
        symbol: str = "",
        category: str = "other",
    ) -> float:
        """Record an LLM API call.  Returns USD cost (0.0 if model is unpriced).

        Fail-closed accounting: unknown model -> tokens recorded, cost = 0,
        but unpriced_calls is incremented so the operator knows the true cost
        is unknown, not zero.

        category: one of scan / analyze / thesis / risk_decision / other.
        """
        price, _exact = resolve_llm_price(model)
        priced = price is not None
        cost = (
            (prompt_tokens / 1_000_000) * price["in"]
            + (completion_tokens / 1_000_000) * price["out"]
        ) if priced else 0.0

        cat = category if category in COST_CATEGORIES else "other"

        with self._lock:
            self._maybe_reset_daily()
            self._s.llm_cost_usd += cost
            self._s.llm_calls += 1
            self._s.prompt_tokens += prompt_tokens
            self._s.completion_tokens += completion_tokens
            self._s.cost_by_category[cat] = self._s.cost_by_category.get(cat, 0.0) + cost
            self._s.calls_by_category[cat] = self._s.calls_by_category.get(cat, 0) + 1
            if not priced:
                self._s.unpriced_calls += 1

        audit(
            system_log,
            f"LLM cost {model}: ${cost:.6f} [{cat}]",
            action="cost_llm",
            result="PRICED" if priced else "UNPRICED",
            data={
                "model": model,
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "cost_usd": round(cost, 6),
                "symbol": symbol,
                "priced": priced,
                "category": cat,
            },
        )
        return cost

    def record_infra(self, cost_usd: float, note: str = "") -> None:
        """Record an infrastructure cost (hosting, data feeds, etc.)."""
        with self._lock:
            self._s.infra_cost_usd += cost_usd

    def snapshot(self) -> CostSummary:
        """Return a frozen copy of current daily cost state."""
        with self._lock:
            self._maybe_reset_daily()
            # Deep-copy the mutable dicts
            s = replace(self._s)
            s.cost_by_category = dict(self._s.cost_by_category)
            s.calls_by_category = dict(self._s.calls_by_category)
            return s

    def snapshot_lifetime(self) -> CostSummary:
        """Return lifetime costs (all days combined)."""
        with self._lock:
            self._maybe_reset_daily()
            lt = replace(self._lifetime)
            # Add current day's costs
            lt.llm_cost_usd += self._s.llm_cost_usd
            lt.infra_cost_usd += self._s.infra_cost_usd
            lt.llm_calls += self._s.llm_calls
            lt.prompt_tokens += self._s.prompt_tokens
            lt.completion_tokens += self._s.completion_tokens
            lt.unpriced_calls += self._s.unpriced_calls
            return lt
