"""One driver for `LiveExecutor._ensure_leverage`, shared by its suites.

`test_preorder_leverage_abort.py` grew this harness for the pre-order abort;
`test_leverage_readback_governs_the_fill.py` needs the same one with more
knobs (the observed margin mode, a per-side set that refuses, the direction
the order will fill in). A second copy of a driver is a second answer about
what the code does the moment one of them is edited — the shape CLAUDE.md
records about `tests/test_preflight_matches_ci.py`'s private `code_only` —
so it lives here and both import it.

It is NOT a test module: no `test_` prefix, nothing collected from it.
"""

from __future__ import annotations

import asyncio
from typing import Any, Optional


class LeverageDrive:
    """What one `_ensure_leverage` run did.

    `aborted`/`why` are the guard's verdict; `set_calls` is every
    `set_leverage` the venue saw, in order, as ``(leverage, symbol, params)``
    — so a test can ask whether BOTH hold sides were pushed rather than
    whether the code contains a loop.
    """

    def __init__(self) -> None:
        self.aborted: bool = False
        self.why: str = ""
        self.set_calls: list = []
        self.leverage_reads: int = 0
        self.position_reads: int = 0

    @property
    def hold_sides(self) -> list:
        """The `holdSide` values pushed, in order. `None` for the bare call."""
        return [(p or {}).get("holdSide") for _, _, p in self.set_calls]


def drive_ensure_leverage(
    readings: Any,
    *,
    positions: Any = None,
    target: int = 5,
    set_raises: bool = False,
    per_side_raises: bool = False,
    fail_open: bool = True,
    margin_mode: Optional[str] = None,
    side: str = "",
    force_per_side: Optional[str] = None,
    runs: int = 1,
    monkeypatch: Any = None,
) -> LeverageDrive:
    """Run `_ensure_leverage` against a stub venue and return what happened.

    ``readings`` is the sequence `fetch_leverage` answers (an ``Exception``
    entry is raised instead of returned); ``positions`` is what
    `fetch_positions` answers, or ``None`` to make that call raise.
    ``margin_mode`` is planted as the executor's OBSERVED mode — the value the
    margin-mode verification read above would have written — because the read
    that decides which leverage field governs is placed under that mode, not
    under the configured one. ``runs`` calls the method that many times on the
    SAME executor, which is the only way to drive the once-per-symbol warnings
    (a fresh executor each time cannot tell "once per process" from "every
    time").
    """
    from bot.core import live_executor as LE

    assert monkeypatch is not None, "the env knobs need monkeypatch"
    monkeypatch.setenv("LEVERAGE_FAIL_OPEN", "1" if fail_open else "0")
    if force_per_side is not None:
        monkeypatch.setenv("LEVERAGE_FORCE_PER_SIDE", force_per_side)
    else:
        monkeypatch.delenv("LEVERAGE_FORCE_PER_SIDE", raising=False)

    out = LeverageDrive()
    seq = list(readings)

    class _Exchange:
        async def set_margin_mode(self, *a, **k):
            return None

        async def set_leverage(self, leverage=None, symbol=None, params=None,
                               **k):
            out.set_calls.append((leverage, symbol, params))
            holdside = (params or {}).get("holdSide")
            if holdside is None and set_raises:
                raise RuntimeError("venue refused set_leverage")
            if holdside is not None and per_side_raises:
                raise RuntimeError("venue refused holdSide leverage")
            return {}

        async def fetch_leverage(self, *a, **k):
            out.leverage_reads += 1
            if not seq:
                raise RuntimeError("no more readings")
            nxt = seq.pop(0)
            if isinstance(nxt, Exception):
                raise nxt
            return nxt

        async def fetch_positions(self, *a, **k):
            out.position_reads += 1
            if positions is None:
                raise RuntimeError("fetch_positions unavailable")
            return positions

    class _Venue:
        id = "bitget"

        @staticmethod
        def futures_params():
            return {}

    ex = LE.LiveExecutor.__new__(LE.LiveExecutor)
    ex._venue = _Venue()
    ex._lev_unverified_warned = set()
    ex._hedge_mode = False
    # The real object sets this in __init__; `__new__` skips it, and an
    # AttributeError here is swallowed by the verification block's broad
    # handler — which turns the whole read-back into a silent no-op. A fixture
    # short a seam is a fixture that cannot see the thing it is driving.
    ex._actual_margin_mode = margin_mode
    ex._margin_mode_unread_warned = set()

    ex_obj = _Exchange()

    async def _get_exchange():
        return ex_obj

    ex._get_exchange = _get_exchange
    ex._compute_target_leverage = lambda symbol: target

    async def _detect_hold_mode():
        return None

    ex._detect_hold_mode = _detect_hold_mode

    async def _all() -> None:
        for _ in range(max(1, runs)):
            await ex._ensure_leverage("TRX/USDT", side)

    try:
        asyncio.run(_all())
    except RuntimeError as exc:
        out.aborted = True
        out.why = str(exc)
    except Exception:
        # Anything else means the stub is short a seam, not that the guard
        # decided something — surface it rather than reading it as "proceed".
        raise
    return out


def lev(x: Any) -> dict:
    """A `fetch_leverage` payload that reads as `x` with NO margin mode.

    Deliberately modeless: it exercises the "a value we cannot place" state,
    which is the one that keeps its historical confirmation.
    """
    return {"leverage": x, "info": {"leverage": str(x)}}
