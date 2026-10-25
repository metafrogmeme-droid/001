"""Per-run token and dollar budget.

The estimate is ``bot.nlp.conversation_store._estimate_tokens`` (the same
reading the chat window uses). The dollars are ``bot.core.cost.llm_call_cost``.
A call whose size or usage cannot be read stops the run. It is not booked
as zero.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from bot.core.cost import CostTracker, llm_call_cost, resolve_llm_price
from bot.llm.usage import cache_token_count
from bot.nlp.conversation_store import _estimate_tokens

logger = logging.getLogger("audit_router")


def estimate_tokens(text: str) -> int | None:
    """The conversation store's estimator. None means the size was not read."""
    if not isinstance(text, str):
        return None
    return _estimate_tokens(text)


@dataclass
class CallUse:
    model: str
    tokens_in: int | None
    tokens_out: int | None
    cache_read: int
    cache_write: int
    cost_usd: float | None
    measured: bool


@dataclass
class Ledger:
    max_tokens: int
    max_cost_usd: float
    tokens: int = 0
    cost_usd: float = 0.0
    cost_complete: bool = True
    stopped: str | None = None
    calls: list[CallUse] = field(default_factory=list)
    tracker: CostTracker = field(default_factory=CostTracker)

    def projected_cost(self, model: str, est_in: int, max_out: int) -> float | None:
        """Worst-case dollars: the whole prompt billed as a cache write.

        A cache write is 1.25x the input price, which is more than an
        uncached read of the same tokens. Stopping on that figure stops
        before a cold cache write can pass the cap.
        """
        price, _exact = resolve_llm_price(model)
        if price is None:
            return None
        return llm_call_cost(price, 0, max_out, 0, est_in)

    def allow(self, model: str, est_in: int | None, max_out: int) -> str | None:
        """None when the call may run. A string is the reason it must not."""
        if self.stopped:
            return self.stopped
        if est_in is None:
            self.stopped = "token estimate unreadable"
            return self.stopped
        if est_in < 0 or max_out < 0:
            self.stopped = "token estimate unreadable"
            return self.stopped
        if self.tokens + est_in + max_out > self.max_tokens:
            self.stopped = (
                f"token budget: the next call needs {est_in + max_out} tokens "
                f"and {self.tokens} of {self.max_tokens} are already used"
            )
            return self.stopped
        cost = self.projected_cost(model, est_in, max_out)
        if cost is None:
            self.stopped = f"unpriced model {model}"
            return self.stopped
        if self.cost_usd + cost > self.max_cost_usd:
            self.stopped = (
                f"cost budget: the next call estimates ${cost:.6f} "
                f"and ${self.cost_usd:.6f} of ${self.max_cost_usd:.6f} is already used"
            )
            return self.stopped
        return None

    def record(self, model: str, usage: dict | None, max_out: int) -> None:
        """Book a call that ran. Unmeasured usage stops the run."""
        usage = usage or {}
        tin = usage.get("in")
        tout = usage.get("out")
        measured = (isinstance(tin, int) and not isinstance(tin, bool)
                    and isinstance(tout, int) and not isinstance(tout, bool)
                    and tin >= 0 and tout >= 0)
        if not measured:
            self.cost_complete = False
            self.stopped = "usage unmeasured; further calls stopped"
            self.calls.append(CallUse(model, None, None, 0, 0, None, False))
            logger.info(
                "audit_router usage model=%s tokens_in=unmeasured "
                "tokens_out=unmeasured cache_read=0 cache_write=0 cost_usd=unmeasured",
                model,
            )
            return
        cache_read = cache_token_count(usage.get("cache_read"))
        cache_write = cache_token_count(usage.get("cache_write"))
        price, _exact = resolve_llm_price(model)
        cost = None
        if price is not None:
            cost = llm_call_cost(price, tin, tout, cache_read, cache_write)
        self.tokens += tin + tout + cache_read + cache_write
        if cost is None:
            self.cost_complete = False
        else:
            self.cost_usd += cost
            try:
                self.tracker.record_llm(
                    model, tin, tout, category="other",
                    cache_read_tokens=cache_read, cache_write_tokens=cache_write,
                )
            except Exception as exc:
                logger.info(
                    "audit_router cost ledger failed (%s)", type(exc).__name__)
        self.calls.append(CallUse(
            model, tin, tout, cache_read, cache_write, cost, True))
        logger.info(
            "audit_router usage model=%s tokens_in=%s tokens_out=%s "
            "cache_read=%s cache_write=%s cost_usd=%s",
            model, tin, tout, cache_read, cache_write,
            f"{cost:.6f}" if cost is not None else "unpriced",
        )
        if self.tokens > self.max_tokens:
            self.stopped = (
                f"token budget: {self.tokens} tokens used, cap {self.max_tokens}"
            )
        elif cost is not None and self.cost_usd > self.max_cost_usd:
            self.stopped = (
                f"cost budget: ${self.cost_usd:.6f} used, cap ${self.max_cost_usd:.6f}"
            )
        if usage.get("cut"):
            self.stopped = "model output hit max_output_tokens; the reply is incomplete"
            self.cost_complete = False
