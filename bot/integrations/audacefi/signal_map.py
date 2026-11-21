"""One risk-gated idea -> one AudaceFi signal, or the reason there is none.

The mapping is pure: it reads the idea and the clock it is handed, and writes
nothing. The outbox assigns the sequence; the publisher sends.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Optional

from bot.core.bar_freshness import bar_freshness_max_periods
from bot.core.symbol_form import normalize_symbol
from bot.integrations.audacefi.settings import (
    MAX_SIGNAL_BYTES,
    MAX_SIGNAL_TTL_MS,
    audacefi_long_target_bps,
    audacefi_signal_ttl_ms,
)
from bot.utils.candles import bar_freshness_verdict
from bot.utils.models import ANALYZER_SOURCE

#: The one market this adapter publishes.
AUDACEFI_SYMBOL = "SOL"

SIDE_LONG = "long"
SIDE_FLAT = "flat"


@dataclass(frozen=True)
class AudaceDraft:
    """A signal before the outbox has given it a sequence."""

    id: str
    symbol: str
    side: str
    targetBps: int
    expiresAtMs: int


def audacefi_signal_id(idea_id: str, side: str) -> str:
    """Stable across replays: the same idea and side always give the same id."""
    return hashlib.sha256(f"{idea_id}|{side}".encode("utf-8")).hexdigest()[:24]


def audacefi_draft_for(idea: Any, now_ms: int) -> tuple[Optional[AudaceDraft], str]:
    """The draft signal for ``idea`` at ``now_ms``, or ``(None, why not)``.

    Refused unless the idea is the analyzer's own (a typed ticket is a
    person's, not a signal), on SOL, carries a fresh bar reading (the same
    verdict and threshold the risk gate used), and is a call rather than a
    re-offer of one already published. LONG is ``long`` at the configured
    allocation; SHORT is ``flat`` at 0, because this adapter never shorts.
    """
    if getattr(idea, "source", None) != ANALYZER_SOURCE:
        return None, f"not an analyzer idea (source={getattr(idea, 'source', None)!r})"
    asset = getattr(idea, "asset", "")
    if normalize_symbol(str(asset)) != AUDACEFI_SYMBOL:
        return None, f"not {AUDACEFI_SYMBOL} ({asset!r})"
    if getattr(idea, "repeat_of", None) is not None:
        return None, "a re-offer of a call already published"
    ok, line = bar_freshness_verdict(getattr(idea, "bar_age_at_analysis_s", None),
                                     getattr(idea, "timeframe", None) or "",
                                     bar_freshness_max_periods())
    if not ok:
        return None, f"bar freshness: {line}"
    direction = getattr(getattr(idea, "direction", None), "value", None)
    if direction == "LONG":
        side, bps = SIDE_LONG, audacefi_long_target_bps()
    elif direction == "SHORT":
        side, bps = SIDE_FLAT, 0
    else:
        return None, f"direction unread ({direction!r})"
    ttl = min(audacefi_signal_ttl_ms(), MAX_SIGNAL_TTL_MS)
    return AudaceDraft(id=audacefi_signal_id(str(idea.id), side), symbol=AUDACEFI_SYMBOL,
                       side=side, targetBps=int(bps), expiresAtMs=int(now_ms) + ttl), "mapped"


def encode_audacefi_signal(draft: AudaceDraft, sequence: int) -> bytes:
    """The wire body: compact, sorted-key JSON. Raises if it exceeds the cap."""
    body = json.dumps({"id": draft.id, "sequence": int(sequence), "symbol": draft.symbol,
                       "side": draft.side, "targetBps": draft.targetBps,
                       "expiresAtMs": draft.expiresAtMs},
                      separators=(",", ":"), sort_keys=True).encode("utf-8")
    if len(body) > MAX_SIGNAL_BYTES:
        raise ValueError(f"signal is {len(body)} bytes > {MAX_SIGNAL_BYTES}")
    return body
