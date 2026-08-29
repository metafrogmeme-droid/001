"""The only pre-trade slippage estimate in the codebase, finally reached.

`bot/risk/order_router.py` sat in `tests/unreachable_baseline.txt` with ZERO
non-test callers. Its honesty defects were fixed earlier — three exits returned
a NUMBER for a book they could not read (0.0%, or a 0.02% "paper default", each
with `order_type` "MARKET"), and 0.0% is the best slippage there is — but a
fixed module nobody calls is still indistinguishable from one that does not
work.

WHY IT WAS NOT WIRED, AND WHY THAT REASON HAD EXPIRED

The baseline's own note said the blocker was cost, not doubt: "walking the book
before an entry means a fresh fetch_order_book on the live order path, which is
a latency and rate-limit decision rather than a wiring job."

True when written, stale by the time it was read. The QC-2b order-book wall
gate (`ENTRY_BOOK_WALL_GATE`, default "warn") already fetches that book on that
exact path. The call the note was protecting against was one the entry path had
been making all along, so the estimate is free. A note explaining why something
is unreachable is itself a claim, and it ages against code that keeps moving.

THE SIZE IS THE PART THAT IS EASY TO GET WRONG

`size_usd` is the MARGIN, not the notional — `quantity = size_usd * leverage /
price`. And at the wall gate, `leverage_mult` still holds CONFIG's default; the
real value resolves later. Estimating there, on either number, walks the book
for a fraction of the real order and reports a slippage for a trade nobody is
placing. At 10x that is a tenth of the true size. So the estimate runs AFTER
leverage resolves, on `quantity * price`.

DRIVEN, NOT GREPPED. The neighbouring gate's enablement test asserts a string
is present in the source. For a module whose entire defect was being present
and unreached, a scan proving the call is written repeats the mistake it is
here to fix.
"""
from __future__ import annotations

from tests.dep_policy import require

require("ccxt", "live_executor imports it")   # pinned: absent ⇒ fail, not skip

from unittest.mock import patch  # noqa: E402

from bot.core import live_executor as le  # noqa: E402
from bot.core.live_executor import LiveExecutor  # noqa: E402
from bot.risk.order_router import SmartOrderRouter  # noqa: E402
from bot.utils.models import Direction, TradeIdea  # noqa: E402


def _idea(direction=Direction.LONG) -> TradeIdea:
    long = direction == Direction.LONG
    return TradeIdea(
        id="TI-SLIP-001", asset="BTC/USDT", direction=direction,
        entry_price=100_000.0,
        stop_loss=98_000.0 if long else 102_000.0,
        take_profit=105_000.0 if long else 95_000.0,
        confidence=0.85, reasoning="slippage fixture",
    )


def _exchange():
    import importlib.util
    import pathlib
    spec = importlib.util.spec_from_file_location(
        "_le_fixtures",
        pathlib.Path(__file__).resolve().parent / "test_live_executor.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod._mock_exchange()


def _deep_book():
    """Plenty of size at the top — a market order barely moves."""
    return {"bids": [[99_999.0 - i, 500.0] for i in range(25)],
            "asks": [[100_001.0 + i, 500.0] for i in range(25)]}


def _thin_book():
    """Dust at the top, then a cliff — a real order walks a long way.

    Sized against the notional that actually reaches the book, not against the
    caller's `size_usd`: MICRO_MAX_POSITION_USD clamps the margin, and the
    notional is that clamp times leverage. The first draft of this fixture put
    $2,000 on the top level and the "thin" book filled the whole order at the
    best price, reporting a perfectly honest 0.0%.
    """
    dust = 0.0000005
    return {"bids": [[99_999.0 - i * 400, dust] for i in range(25)],
            "asks": [[100_001.0 + i * 400, dust] for i in range(25)]}


def _run(book, *, mode="warn", direction=Direction.LONG, size_usd=100.0,
         wall="off"):
    """Drive the REAL execute() and return (result, audit events).

    Each run gets its OWN state_dir. LiveExecutor() with no arguments loads the
    shared on-disk position file, so a LONG opened by one call was still open
    for the next one and the second run came back "Already have an open LONG
    position on BTC/USDT" — the gate never ran, and the test read that silence
    as the gate being broken.
    """
    import asyncio
    import os
    import tempfile
    seen = []
    ex = _exchange()
    if book is not None:
        async def _ob(*a, **kw):
            return book
        ex.fetch_order_book = _ob
    else:
        async def _boom(*a, **kw):
            raise RuntimeError("venue book endpoint down")
        ex.fetch_order_book = _boom

    _state = tempfile.mkdtemp(prefix="slipgate-")
    owner = LiveExecutor(state_dir=_state)
    owner._exchange = ex
    real_audit = le.audit

    def _spy(*a, **k):
        seen.append((a, k))
        return real_audit(*a, **k)

    env = {"ENTRY_SLIPPAGE_GATE": mode, "ENTRY_BOOK_WALL_GATE": wall}
    from bot.config import CONFIG
    with patch.dict(os.environ, env), \
         patch.object(le, "audit", _spy), \
         patch.object(type(CONFIG), "is_live", return_value=True):
        result = asyncio.run(owner.execute(_idea(direction), size_usd=size_usd))
    return result, [k for _a, k in seen if k.get("action") == "entry_slippage"]


# ── the gate is REACHED ───────────────────────────────────────────────
def test_the_estimate_actually_runs_on_the_live_entry_path():
    # The assertion the baseline entry existed for: something calls it.
    _result, events = _run(_deep_book())
    assert events, ("no entry_slippage audit event — the module is still "
                    "unreached, which is the defect this wiring exists to fix")


def test_a_deep_book_reports_a_measured_number():
    _result, events = _run(_deep_book())
    assert events[-1]["result"] == "OK"
    slip = events[-1]["data"]["slippage_pct"]
    assert slip is not None and slip >= 0.0


def test_a_thin_book_is_flagged():
    _result, events = _run(_thin_book(), size_usd=500.0)
    assert events, "the gate did not run"
    assert events[-1]["result"] in ("WARN", "BLOCKED", "UNREADABLE")


# ── unreadable is never 0.0% ──────────────────────────────────────────
def test_a_failed_book_fetch_is_unreadable_not_zero_slippage():
    # 0.0% is the BEST slippage there is. An unreadable book must never
    # produce the most reassuring possible reading.
    _result, events = _run(None)
    assert events, "the gate did not run when the fetch failed"
    ev = events[-1]
    assert ev["result"] == "UNREADABLE"
    assert ev["data"].get("slippage_pct") is None


def test_an_empty_book_is_unreadable_not_zero_slippage():
    _result, events = _run({"bids": [], "asks": []})
    assert events and events[-1]["result"] == "UNREADABLE"
    assert events[-1]["data"].get("slippage_pct") is None


# ── the size it measures is the size being traded ─────────────────────
def test_the_notional_is_measured_not_the_margin():
    # `size_usd` is MARGIN. Walking the book with it, instead of
    # margin * leverage, sizes the estimate at a fraction of the real order.
    from bot.config import CONFIG
    margin = 100.0
    _result, events = _run(_deep_book(), size_usd=margin)
    assert events
    notional = events[-1]["data"]["notional_usd"]
    lev = CONFIG.exchange.default_leverage or 1
    if lev > 1:
        assert notional > margin * 1.5, (
            f"measured ${notional:,.2f} against ${margin:,.2f} of margin — "
            f"the book was walked for a trade nobody is placing")


# ── side selection ────────────────────────────────────────────────────
def _live_notional() -> float:
    """The notional that actually reaches the book.

    `size_usd` is clamped to MICRO_MAX_POSITION_USD (the margin cap) and then
    multiplied by leverage. Deriving it beats hard-coding: the first two drafts
    of these fixtures were sized against the caller's `size_usd` and put orders
    of magnitude too much depth on the "thin" side, so a book meant to be
    unwalkable filled at the best price and honestly reported 0.0%.
    """
    from bot.config import CONFIG
    from bot.core.live_executor import MICRO_MAX_POSITION_USD
    return MICRO_MAX_POSITION_USD * max(CONFIG.exchange.default_leverage or 1, 1)


def test_a_long_eats_the_asks_and_a_short_eats_the_bids():
    # A book thin on ONE side only. Reading the wrong side reports the calm
    # side's slippage for an order that will hit the other one.
    # Thin side: ~1.5x the order spread over 25 levels with wide price gaps,
    # so it is walkable (a measured number, not "insufficient depth") and the
    # walk is expensive. Deep side: one level that swallows the whole order.
    n = _live_notional()
    per_level = (n * 1.5 / 25) / 100_000.0        # base units per level
    lopsided = {"bids": [[99_999.0 - i, n * 10 / 100_000.0] for i in range(25)],
                "asks": [[100_001.0 + i * 900, per_level] for i in range(25)]}
    _r1, longs = _run(lopsided, size_usd=500.0, direction=Direction.LONG)
    _r2, shorts = _run(lopsided, size_usd=500.0, direction=Direction.SHORT)
    assert longs and shorts
    long_slip = longs[-1]["data"].get("slippage_pct")
    short_slip = shorts[-1]["data"].get("slippage_pct")
    assert long_slip is not None and short_slip is not None
    assert long_slip > short_slip, (
        "a LONG buys the asks — the thin side here — so it must not report "
        "the deep bid side's slippage")


# ── observe-first, and fail-open ──────────────────────────────────────
def test_off_skips_entirely():
    _result, events = _run(_deep_book(), mode="off")
    assert not events, "mode=off still ran the gate"


def _placed(result: str) -> bool:
    """Did the order actually go through?

    Asserting `"EXECUTION BLOCKED" not in result` is NOT this question, and the
    difference is not academic: a mutation that removed the fail-open `except`
    around the estimate survived the entire suite, because the raised error was
    caught by execute()'s outer handler and returned as a DIFFERENT failure
    string — one that contains no "EXECUTION BLOCKED" and no trade either.
    CLAUDE.md: asserting a short string is absent is the assertion that keeps
    misfiring. Assert the positive rendering instead.
    """
    return "LIVE BUY" in (result or "") or "LIVE SELL" in (result or "")


def test_warn_never_blocks_a_trade():
    result, events = _run(_thin_book(), mode="warn", size_usd=5000.0)
    assert events
    assert _placed(result), (
        f"warn mode did not place the trade — the point of warn is that it "
        f"observes only. Got: {str(result)[:200]}")


def test_the_default_is_warn_so_it_observes_without_blocking():
    import asyncio
    import os
    import tempfile

    from bot.config import CONFIG

    seen = []
    ex = _exchange()

    async def _ob(*a, **kw):
        return _deep_book()
    ex.fetch_order_book = _ob
    owner = LiveExecutor(state_dir=tempfile.mkdtemp(prefix="slipgate-"))
    owner._exchange = ex
    real_audit = le.audit
    env = dict(os.environ)
    env.pop("ENTRY_SLIPPAGE_GATE", None)          # unset: take the default
    env["ENTRY_BOOK_WALL_GATE"] = "off"
    with patch.dict(os.environ, env, clear=True), \
         patch.object(le, "audit", lambda *a, **k: (seen.append(k),
                                                    real_audit(*a, **k))[1]), \
         patch.object(type(CONFIG), "is_live", return_value=True):
        asyncio.run(owner.execute(_idea(), size_usd=100.0))
    assert [k for k in seen if k.get("action") == "entry_slippage"], (
        "the gate did not run with no env var set — its default is not 'warn'")


def test_a_gate_fault_never_takes_a_trade_down():
    # Fail-open at every layer, like its neighbours.
    import asyncio
    import os
    ex = _exchange()

    async def _ob(*a, **kw):
        return _deep_book()
    ex.fetch_order_book = _ob
    import tempfile
    owner = LiveExecutor(state_dir=tempfile.mkdtemp(prefix="slipgate-"))
    owner._exchange = ex

    class _Exploding(SmartOrderRouter):
        def estimate_slippage(self, *a, **k):
            raise RuntimeError("router blew up")

    from bot.config import CONFIG
    with patch.dict(os.environ, {"ENTRY_SLIPPAGE_GATE": "block",
                                 "ENTRY_BOOK_WALL_GATE": "off"}), \
         patch("bot.risk.order_router.SmartOrderRouter", _Exploding), \
         patch.object(type(CONFIG), "is_live", return_value=True):
        result = asyncio.run(owner.execute(_idea(), size_usd=100.0))
    assert _placed(result), (
        f"a crashing slippage estimate took the trade down — fail-open means "
        f"the order still goes through. Got: {str(result)[:200]}")


# ── one fetch, not two ────────────────────────────────────────────────
def test_enabling_the_gate_costs_no_extra_book_fetch():
    # The whole reason this could be wired: the wall gate already fetches the
    # book on this path. If enabling slippage adds a second call, the
    # latency/rate-limit objection in the baseline note comes back.
    import asyncio
    import os
    calls = {"n": 0}
    ex = _exchange()

    async def _ob(*a, **kw):
        calls["n"] += 1
        return _deep_book()
    ex.fetch_order_book = _ob
    import tempfile
    owner = LiveExecutor(state_dir=tempfile.mkdtemp(prefix="slipgate-"))
    owner._exchange = ex
    from bot.config import CONFIG
    with patch.dict(os.environ, {"ENTRY_SLIPPAGE_GATE": "warn",
                                 "ENTRY_BOOK_WALL_GATE": "warn"}), \
         patch.object(type(CONFIG), "is_live", return_value=True):
        asyncio.run(owner.execute(_idea(), size_usd=100.0))
    assert calls["n"] <= 1, (
        f"both gates on fetched the book {calls['n']} times — they must share one")


def test_the_slippage_gate_does_not_depend_on_the_wall_gates_flag():
    # A hidden coupling nobody could see from ENTRY_SLIPPAGE_GATE's own config.
    _result, events = _run(_deep_book(), mode="warn", wall="off")
    assert events, ("the slippage gate went silent because the WALL gate was "
                    "off — it must fetch its own book when it is the only one on")
