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

import logging
import threading
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from typing import Any, Optional

from bot.utils.atomic_write import atomic_write_json
from bot.utils.json_store import READ, UNREADABLE, read_json_store
from bot.utils.logger import audit, system_log

logger = logging.getLogger(__name__)


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
    # The anonymous website preview's part of the "chat" figures above: a
    # SUBSET of them, booked by `record_llm(category="chat", public=True)`,
    # never added to any total a second time. Its own reading exists for one
    # bound, `public_chat_budget_bound`.
    public_chat_cost_usd: float = 0.0
    public_chat_calls: int = 0

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
#: The bound an ANONYMOUS website turn reaches when the public preview's own
#: spend meets its slice of chat's budget (`LLM_PUBLIC_CHAT_BUDGET_SHARE`).
#: A word of its own because the visitor is told the preview's limit, not
#: that the bot's budget is gone: signed-in chat still has the rest.
PUBLIC_SHARE_BOUND = "the public preview's share of chat's budget"


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


def budget_reading(snap: CostSummary, llm_cfg) -> dict:
    """Today's spend against every bound the LLM layer enforces, read from the
    same functions the gates call, for a card to print.

    ``thesis_spent`` is None when the chat/non-chat split cannot be read
    (`non_chat_spend_usd`), and ``thesis_left`` beside it is None too: what
    is left of a cap nobody can measure against is unknown, not the whole
    cap. ``thesis_exhausted`` is the gate's own verdict either way. With a
    thesis cap set, analysis is bounded by that cap alone, not by the daily
    total (`thesis_dollar_exhausted`; the owner holds that decision), so
    what it has left is not clipped to the total here either. Chat stops at
    its cap or the total, whichever comes first, and its ``chat_left`` is
    clipped to both. ``chat_bound`` is `chat_budget_bound`'s own word, ""
    when chat has reached none.
    """
    budget = float(llm_cfg.daily_budget_usd)
    spent = float(snap.llm_cost_usd)
    chat_cap = chat_cap_usd(llm_cfg)
    chat_spent = chat_spend_usd(snap)
    daily_left = max(0.0, budget - spent)
    thesis_cap = getattr(llm_cfg, "thesis_budget_usd", None)
    thesis_spent = non_chat_spend_usd(snap)
    thesis_left: Optional[float] = None
    if thesis_cap is not None and thesis_spent is not None:
        thesis_left = max(0.0, float(thesis_cap) - thesis_spent)
    return {
        "daily_budget": budget,
        "spent": spent,
        "daily_left": daily_left,
        "chat_cap": chat_cap,
        "chat_cap_is_own": getattr(llm_cfg, "chat_budget_usd", None) is not None,
        "chat_share": float(llm_cfg.chat_budget_share),
        "chat_spent": chat_spent,
        "chat_left": max(0.0, min(chat_cap - chat_spent, daily_left)),
        "chat_bound": chat_budget_bound(snap, llm_cfg),
        "chat_calls": chat_call_count(snap),
        "call_limit": int(llm_cfg.daily_call_limit),
        "thesis_cap": None if thesis_cap is None else float(thesis_cap),
        "thesis_spent": thesis_spent,
        "thesis_left": thesis_left,
        "thesis_exhausted": thesis_dollar_exhausted(snap, llm_cfg),
        "unpriced_calls": int(snap.unpriced_calls),
    }


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
    if chat_spend_usd(snap) >= chat_cap_usd(llm_cfg):
        own = getattr(llm_cfg, "chat_budget_usd", None)
        return CHAT_OWN_BOUND if own is not None else CHAT_SHARE_BOUND
    return ""


def chat_cap_usd(llm_cfg) -> float:
    """Chat's dollars for the day: the ceiling `chat_budget_bound` stops at.

    Its own cap when one is set, reached at the combined total when it sits
    above it; otherwise its share of the total. One reading, because four
    places ask it: the gate, the chat reply's audit line, the public
    preview's slice of it, and the /costs card that prints what is left
    (`budget_reading`). A card restating a cap is the copy that drifts the
    day the rule changes.
    """
    total = float(llm_cfg.daily_budget_usd)
    own = getattr(llm_cfg, "chat_budget_usd", None)
    if own is not None:
        return min(float(own), total)
    return total * float(llm_cfg.chat_budget_share)


def public_chat_spend_usd(snap: CostSummary) -> float:
    """Today's dollars spent answering anonymous website visitors.

    A part of chat's spend. When it cannot be read (a snapshot without the
    field, or a value that is not a finite number at least zero), the answer
    is chat's whole spend: every chat dollar may have been the preview's, so
    the preview stops at its slice of all of it. Zero would let anonymous
    turns spend unseen, which is the defect this reading exists for.
    """
    import math

    value = getattr(snap, "public_chat_cost_usd", None)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return chat_spend_usd(snap)
    value = float(value)
    if not math.isfinite(value) or value < 0:
        return chat_spend_usd(snap)
    return value


def public_chat_call_count(snap: CostSummary) -> int:
    """Today's anonymous-preview calls; chat's whole count when unreadable,
    for `public_chat_spend_usd`'s reason."""
    value = getattr(snap, "public_chat_calls", None)
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return value
    return chat_call_count(snap)


def public_chat_budget_bound(snap: CostSummary, llm_cfg) -> str:
    """Which bound an ANONYMOUS website turn has reached, or "" when none.

    The preview spends from chat's budget, so whatever stops chat stops it
    first (`chat_budget_bound`, the same words). Then its own slice: a
    fraction (`public_chat_budget_share`, at most a half) of chat's dollars
    and of the daily call limit. Without it a few hundred anonymous turns
    spent chat's share and every signed-in user was refused for the day;
    with it, anonymous traffic can spend at most that fraction, and the rest
    is signed-in chat's. A share of 0 turns the model off for the preview.
    """
    bound = chat_budget_bound(snap, llm_cfg)
    if bound:
        return bound
    share = float(llm_cfg.public_chat_budget_share)
    if public_chat_call_count(snap) >= llm_cfg.daily_call_limit * share:
        return PUBLIC_SHARE_BOUND
    if public_chat_spend_usd(snap) >= chat_cap_usd(llm_cfg) * share:
        return PUBLIC_SHARE_BOUND
    return ""


#: The file the bot's ledger is kept in, under its data directory.
SPEND_FILE = "llm_spend.json"

#: What the day's figures cover, one reading (`CostTracker.persistence`).
SPEND_OFF = "off"                 # this process's own ledger; nothing kept on disk
SPEND_RESTORED = "restored"       # today's spend before this start was read back
SPEND_FRESH = "fresh"             # nothing spent today before this start
SPEND_UNREADABLE = "unreadable"   # the file would not read: counted from this start

_SUMMARY_NUMBERS = ("llm_cost_usd", "infra_cost_usd", "llm_calls", "prompt_tokens",
                    "completion_tokens", "unpriced_calls", "public_chat_cost_usd",
                    "public_chat_calls")


def _summary_json(s: CostSummary) -> dict:
    out: dict[str, Any] = {k: getattr(s, k) for k in _SUMMARY_NUMBERS}
    out["cost_by_category"] = dict(s.cost_by_category)
    out["calls_by_category"] = dict(s.calls_by_category)
    return out


def _summary_from_json(d: Any) -> Optional[CostSummary]:
    """A ledger read back, or None when any figure is missing or not a number:
    a ledger with a hole in it is not a smaller spend."""
    if not isinstance(d, dict):
        return None
    s = CostSummary()
    for k in _SUMMARY_NUMBERS:
        v = d.get(k)
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            return None
        setattr(s, k, type(getattr(s, k))(v))
    for k, kind in (("cost_by_category", float), ("calls_by_category", int)):
        m = d.get(k)
        if not isinstance(m, dict) or not all(
                isinstance(v, (int, float)) and not isinstance(v, bool) for v in m.values()):
            return None
        setattr(s, k, {str(c): kind(v) for c, v in m.items()})
    return s


def _spend_file_check(data: dict) -> str:
    if not isinstance(data.get("day"), str):
        return "no day"
    if _summary_from_json(data.get("daily")) is None:
        return "the day's ledger is incomplete"
    if _summary_from_json(data.get("lifetime")) is None:
        return "the lifetime ledger is incomplete"
    return ""


class CostTracker:
    """Session operating-cost ledger.  Separate from trade PnL by design.

    Threading model: same single-threaded asyncio assumption as RiskEngine.
    RLock is defensive only.

    THE DAY'S SPEND OUTLIVES A RESTART when the ledger is given a ``path``.
    It lived in memory only, and the engine builds a fresh one at start, so a
    restart began the day at $0: every daily cap that reads it -- the combined
    dollar total, chat's share, the public preview's slice, the call limit --
    was spendable again after each deploy. With a path, the ledger is written
    after every booking, and a start reads it back: the same day's figures
    are restored, and an earlier day's are folded into the lifetime total, as
    the day boundary folds them. A file that will not read is never written
    over (`bot/utils/json_store.py`), and the figures say they count from this
    start (`persistence`). Without a path -- tests, and a process that
    is not the bot (`detach_from_file`) -- it is this process's own ledger,
    as before.
    """

    def __init__(self, path: Optional[str] = None) -> None:
        self._lock = threading.RLock()
        self._s = CostSummary()
        self._lifetime = CostSummary()  # W1 FIX: separate lifetime vs daily
        self._current_day: str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        self._path: Optional[str] = path
        self.persistence: str = SPEND_OFF
        self._unreadable_detail = ""
        self._write_warned = False
        if path is not None:
            self._restore(path)

    def _restore(self, path: str) -> None:
        r = read_json_store(path, check=_spend_file_check)
        if r.state == UNREADABLE:
            self.persistence = SPEND_UNREADABLE
            self._unreadable_detail = r.detail
            logger.warning(
                "The LLM spend ledger could not be read (%s): today's spend before "
                "this start is unknown, and the file is kept as it is", r.detail)
            return
        if r.state != READ:
            self.persistence = SPEND_FRESH
            return
        daily = _summary_from_json(r.data["daily"])
        lifetime = _summary_from_json(r.data["lifetime"])
        assert daily is not None and lifetime is not None  # the check passed
        self._lifetime = lifetime
        if r.data["day"] == self._current_day:
            self._s = daily
            self.persistence = SPEND_RESTORED
        else:
            self._s = daily
            self._current_day = r.data["day"]
            self._maybe_reset_daily()  # folds that day into the lifetime total
            self.persistence = SPEND_FRESH

    def detach_from_file(self) -> None:
        """This ledger is a process's own, never the bot's file.

        For an engine built outside the bot (`detach_state_persistence`): its
        calls are its own, and what it read at start would be a copy of the
        bot's day that never moves again, so it starts from nothing, as every
        ledger did before the file existed.
        """
        with self._lock:
            self._path = None
            self._s = CostSummary()
            self._lifetime = CostSummary()
            self._current_day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            self.persistence = SPEND_OFF

    def _save(self) -> None:
        """Write the ledger whole. Never over a file that would not read; a
        write that does not land is warned once and tried again next time."""
        if self._path is None or self.persistence == SPEND_UNREADABLE:
            return
        try:
            atomic_write_json(self._path, {
                "day": self._current_day,
                "daily": _summary_json(self._s),
                "lifetime": _summary_json(self._lifetime),
            })
        except OSError as exc:
            if not self._write_warned:
                self._write_warned = True
                logger.warning("The LLM spend ledger could not be written (%s); "
                               "a restart would lose today's spend", type(exc).__name__)

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
            self._lifetime.public_chat_cost_usd += self._s.public_chat_cost_usd
            self._lifetime.public_chat_calls += self._s.public_chat_calls
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
        public: bool = False,
    ) -> float:
        """Record an LLM API call.  Returns USD cost (0.0 if model is unpriced).

        Fail-closed accounting: unknown model -> tokens recorded, cost = 0,
        but unpriced_calls is incremented so the operator knows the true cost
        is unknown, not zero.

        category: one of scan / analyze / thesis / risk_decision / other.

        public: the call answered an anonymous website visitor. Booked under
        its category as always, and ALSO in the public preview's subset —
        only for a chat call, so the subset can never exceed chat's figures.
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
            if public and cat == "chat":
                self._s.public_chat_cost_usd += cost
                self._s.public_chat_calls += 1
            if not priced:
                self._s.unpriced_calls += 1
            self._save()

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
                **({"public": True} if public and cat == "chat" else {}),
            },
        )
        return cost

    def record_infra(self, cost_usd: float, note: str = "") -> None:
        """Record an infrastructure cost (hosting, data feeds, etc.)."""
        with self._lock:
            self._maybe_reset_daily()
            self._s.infra_cost_usd += cost_usd
            self._save()

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
            lt.public_chat_cost_usd += self._s.public_chat_cost_usd
            lt.public_chat_calls += self._s.public_chat_calls
            return lt
