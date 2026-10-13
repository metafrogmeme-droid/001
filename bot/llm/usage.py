"""What the brain costs, measured — never estimated into a fake precision.

RUNECLAW makes an LLM call per symbol per analysis stage. On 2026-07-30 the
scan universe went 70 -> 85 and analysis concurrency 12 -> 16, both good
changes, and nobody could say what either did to spend, because
`llm_complete` threw `response.usage` away and returned the text.

TOKENS ARE MEASURED. The provider reports them; we accumulate them.

DOLLARS ARE NOT, unless the operator supplies prices. A hardcoded price table
is a number that LOOKS measured, goes stale the first time a provider
re-prices or the model tier changes, and then misleads with the authority of
a printed figure — the exact defect this codebase spent a day removing from
its operator surfaces. Set LLM_PRICE_PER_1M_IN / LLM_PRICE_PER_1M_OUT to get
a cost line; leave them unset and the surface shows tokens and says nothing
about money.

Fail-open throughout: accounting must never be the reason an analysis fails.
"""
from __future__ import annotations

import os
import threading
from typing import Optional, TypedDict

_LOCK = threading.Lock()

#: Cumulative since process start, and for the batch currently being counted.
_TOTALS: dict[str, int] = {"in": 0, "out": 0, "calls": 0}
_BY_MODEL: dict[str, dict[str, int]] = {}


def record(model: str, tokens_in: int, tokens_out: int) -> None:
    """Accumulate one call's REPORTED usage. Never raises."""
    try:
        ti, to = int(tokens_in or 0), int(tokens_out or 0)
        if ti < 0 or to < 0:
            return
        with _LOCK:
            _TOTALS["in"] += ti
            _TOTALS["out"] += to
            _TOTALS["calls"] += 1
            m = _BY_MODEL.setdefault(str(model or "unknown"),
                                     {"in": 0, "out": 0, "calls": 0})
            m["in"] += ti
            m["out"] += to
            m["calls"] += 1
    except Exception:
        pass


def usage_pair(response) -> Optional[tuple[int, int]]:
    """The MEASURED (input, output) token pair off a provider response, or
    None when the response carries no usage at all.

    Anthropic: usage.input_tokens / usage.output_tokens
    OpenAI-compatible: usage.prompt_tokens / usage.completion_tokens

    None, never (0, 0): a zero would read as "this call was free", which is a
    different and false claim from "we did not measure this call". The chat
    cost path books the estimate it always used when this answers None, and
    the measured pair when it does not.
    """
    try:
        u = getattr(response, "usage", None)
        if u is None:
            return None
        ti = getattr(u, "input_tokens", None)
        if ti is None:
            ti = getattr(u, "prompt_tokens", None)
        to = getattr(u, "output_tokens", None)
        if to is None:
            to = getattr(u, "completion_tokens", None)
        if ti is None and to is None:
            return None
        ti, to = int(ti or 0), int(to or 0)
        if ti < 0 or to < 0:
            return None
        return ti, to
    except Exception:
        return None


def cache_pair(response) -> tuple[int, int]:
    """The prompt cache's (read, write) tokens off a provider response.

    An Anthropic response reports them BESIDE ``input_tokens``
    (``cache_read_input_tokens`` / ``cache_creation_input_tokens``), and
    bills them at their own rates, so a cost read from ``usage_pair`` alone
    left a cached call's writes unbooked. An OpenAI-compatible response
    counts cached tokens inside ``prompt_tokens`` and has no such fields, and
    a call that used no cache reports none: both are (0, 0), which here is a
    measurement -- nothing was billed apart from the input -- not a gap.
    """
    try:
        u = getattr(response, "usage", None)
        if u is None:
            return 0, 0
        return (cache_token_count(getattr(u, "cache_read_input_tokens", None)),
                cache_token_count(getattr(u, "cache_creation_input_tokens", None)))
    except Exception:
        return 0, 0


def cache_token_count(value) -> int:
    """One reported cache count as a whole number of tokens.

    Absent (None, a non-number, a negative) is 0, and for the cache that is
    a measurement: a response that reports no cache reads or writes billed
    none, and the caller's input count already holds the whole prompt.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0
    return max(0, int(value))


class CacheTokens(TypedDict):
    """``CostTracker.record_llm``'s two cache arguments."""
    cache_read_tokens: int
    cache_write_tokens: int


def cache_kwargs(usage_out: dict) -> CacheTokens:
    """``record_llm``'s cache arguments from a ``usage_out`` dict that
    `bot.llm.provider.llm_complete` / `llm_complete_with_tools` filled. An empty dict
    is a call that reported no cache, or a provider whose input count holds
    its cached tokens: (0, 0)."""
    return CacheTokens(cache_read_tokens=cache_token_count(usage_out.get("cache_read")),
                       cache_write_tokens=cache_token_count(usage_out.get("cache_write")))


def record_from_response(model: str, response) -> None:
    """Pull usage off a provider response object, whatever shape it has, and
    accumulate it. A response WITHOUT usage records nothing rather than
    recording zeros — see ``usage_pair`` for why.
    """
    pair = usage_pair(response)
    if pair is None:
        return
    record(model, pair[0], pair[1])


def _prices() -> tuple[Optional[float], Optional[float]]:
    """Operator-supplied $/1M tokens, or (None, None). Never guessed."""
    def _f(key: str) -> Optional[float]:
        raw = (os.getenv(key) or "").strip()
        if not raw:
            return None
        try:
            v = float(raw)
            return v if v >= 0 else None
        except ValueError:
            return None
    return _f("LLM_PRICE_PER_1M_IN"), _f("LLM_PRICE_PER_1M_OUT")


def snapshot() -> dict:
    """Measured usage, plus a cost ONLY if prices were supplied.

    `cost_usd` is absent — not zero, not null-with-a-zero-sibling — when no
    prices are configured. Absent is the honest shape for "not measured".
    """
    with _LOCK:
        totals = dict(_TOTALS)
        by_model = {k: dict(v) for k, v in _BY_MODEL.items()}
    out: dict = {
        "calls": totals["calls"],
        "tokens_in": totals["in"],
        "tokens_out": totals["out"],
        "by_model": by_model,
    }
    p_in, p_out = _prices()
    if p_in is not None and p_out is not None:
        out["cost_usd"] = round(
            totals["in"] / 1_000_000.0 * p_in
            + totals["out"] / 1_000_000.0 * p_out, 4)
        out["cost_basis"] = "operator-supplied $/1M rates"
    return out


def reset() -> None:
    """Test seam."""
    with _LOCK:
        _TOTALS.update({"in": 0, "out": 0, "calls": 0})
        _BY_MODEL.clear()
