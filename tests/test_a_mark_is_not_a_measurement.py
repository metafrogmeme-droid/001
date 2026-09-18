"""A forming candle's close IS the current price — so both bars are wanted.

Twenty-five venue candle reads never dropped the still-forming bar, and a
blanket sweep would have been the wrong fix: six of them read the last bar as
the MARK, where dropping it answers with a close up to one whole timeframe
old. The rule those six follow is the mark BEFORE the drop and the window
AFTER it, and this file drives the two halves apart.

`scan_skill._scan_symbol` is the sharpest of them and the reason the split is
not a nicety. It computed

    vol_ratio = v[-1] / mean(v[-20:])

so a part-period bar's volume was charged against a 20-bar mean of whole ones:
on a 4h scan a bar one hour old reads about 0.25x, on the one figure whose
whole job is to detect a volume SPIKE. Its `price` wants the same bar, for the
opposite reason.

WHAT IS DRIVEN AND WHAT IS SCANNED, stated rather than blurred. `_scan_symbol`
and `rich_cards.fetch_analysis_data` take an exchange as an argument, so both
are RUN against a stub whose newest bar is a planted part-period one and whose
answers are read off the returned dict. The three `scan_commands` handlers
reach for `self.engine`, `self._send`, an `Update` and a locale, so there the
ORDER is pinned structurally — which is the property that matters, because a
mark read after the drop is a stale price and no card can tell.
"""
from __future__ import annotations

import ast
import pathlib

import pytest

from bot.utils.candles import timeframe_to_ms

REPO = pathlib.Path(__file__).resolve().parent.parent

# A part-period bar: distinctive close and a volume a fifth of its neighbours',
# which is what a bar an hour into a 4h period looks like.
PARTIAL_CLOSE = 4242.0
PARTIAL_VOLUME = 2.0
WHOLE_VOLUME = 10.0


def _series(timeframe: str, n: int = 40):
    """`n` bars, the last one still FORMING (a fifth of its period elapsed)."""
    import time
    tf_ms = timeframe_to_ms(timeframe)
    last_open = time.time() * 1000.0 - tf_ms // 5
    rows = []
    for i in range(n - 1, 0, -1):
        base = 1000.0 + i
        rows.append([last_open - i * tf_ms, base, base + 5, base - 5,
                     base + 1, WHOLE_VOLUME])
    rows.append([last_open, 1000.0, PARTIAL_CLOSE + 1, 999.0,
                 PARTIAL_CLOSE, PARTIAL_VOLUME])
    return rows


class _Venue:
    def __init__(self, rows) -> None:
        self.rows = rows

    async def fetch_ohlcv(self, symbol, timeframe, limit=100):
        return [list(r) for r in self.rows]

    async def fetch_order_book(self, symbol, limit=20):
        return {"bids": [[1000.0, 1.0]], "asks": [[1001.0, 1.0]]}


class TestTheScanCardReadsBothBars:

    async def test_the_price_is_the_forming_bar_and_the_volume_ratio_is_not(self):
        from bot.skills.scan_skill import _scan_symbol

        r = await _scan_symbol(_Venue(_series("4h")), "BTC/USDT")
        assert r is not None

        assert r["price"] == pytest.approx(PARTIAL_CLOSE), (
            "the MARK was taken after the drop, so the card shows a close up "
            "to four hours old")
        # The 20-bar mean is over whole bars, so a ratio computed with the
        # part-period bar in the numerator lands near 0.2 rather than 1.0.
        assert r["vol_ratio"] == pytest.approx(1.0, abs=0.05), (
            f"vol_ratio {r['vol_ratio']} — the part-period bar's volume is "
            f"still being charged against a mean of whole ones")

    async def test_a_thin_series_still_answers_nothing_rather_than_guessing(self):
        """The drop costs a bar, so the 30-bar floor is met one bar later. It
        must still REFUSE rather than compute on what is left."""
        from bot.skills.scan_skill import _scan_symbol

        assert await _scan_symbol(_Venue(_series("4h", n=12)), "BTC/USDT") is None


class TestTheAnalysisCardReadsBothBars:

    async def test_the_price_is_the_forming_bar_and_the_window_is_closed(self):
        from bot.formatters.rich_cards import fetch_analysis_data

        d = await fetch_analysis_data(_Venue(_series("1h")), "BTC/USDT", "1h")
        assert d is not None
        assert d["price"] == pytest.approx(PARTIAL_CLOSE), (
            "the card's price is not the current price")
        # `high_24h` is a max over the newest 24 bars. The planted part-period
        # bar carries the highest high in the series, so its presence in the
        # WINDOW is directly visible.
        assert d["high_24h"] < PARTIAL_CLOSE, (
            "the 24-bar high still includes the bar that has not closed")


class TestTheOrderIsPinnedWhereADriveWouldCostAHandler:
    """Three `/sweep`-family commands read `float(closes[-1])` into their card
    and compute a detector over the window. A drive needs `self.engine`, the
    send chokepoint, an `Update` and a locale; the property is the ORDER, so
    that is what is asserted — with the reason said out loud rather than
    presented as a behaviour test.
    """

    SITES = ("_cmd_sweep", "_cmd_zones", "_cmd_squeeze")

    @pytest.mark.parametrize("fn", SITES)
    def test_the_mark_is_read_before_the_drop(self, fn):
        src = (REPO / "bot" / "skills" / "scan_commands.py").read_text(encoding="utf-8")
        tree = ast.parse(src)
        node = next(n for n in ast.walk(tree)
                    if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                    and n.name == fn)
        mark_line = drop_line = None
        for sub in ast.walk(node):
            if isinstance(sub, ast.Assign) and any(
                    isinstance(t, ast.Name) and t.id == "mark" for t in sub.targets):
                mark_line = sub.lineno if mark_line is None else mark_line
            if (isinstance(sub, ast.Call) and isinstance(sub.func, ast.Name)
                    and sub.func.id == "drop_forming_candle"):
                drop_line = sub.lineno if drop_line is None else drop_line
        assert mark_line is not None, f"{fn} reads no mark"
        assert drop_line is not None, f"{fn} does not drop the forming bar"
        assert mark_line < drop_line, (
            f"{fn} reads the mark AFTER the drop — the card's price would be "
            f"the previous bar's close")

    @pytest.mark.parametrize("fn", SITES)
    def test_the_card_is_handed_the_mark(self, fn):
        """Reading a mark and then passing `closes[-1]` anyway is the whole
        defect with an unused variable added."""
        src = (REPO / "bot" / "skills" / "scan_commands.py").read_text(encoding="utf-8")
        tree = ast.parse(src)
        node = next(n for n in ast.walk(tree)
                    if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                    and n.name == fn)
        body = ast.unparse(node)
        assert "mark if mark > 0 else float(closes[-1])" in body, (
            f"{fn} does not hand its card the mark it read")


class TestTheRiskGatesDenominatorIsAClosedBar:
    """`scan_skill`'s three confirm paths each compute an ATR and hand it to
    `engine.risk.evaluate(idea, atr=...)`. A forming bar truncates the newest
    true range, so the gate sizes against an understated volatility.

    Structural, and the reason is the same as above: driving these needs a
    `query`, an engine, a registry and a risk engine. What is checked is that
    every ATR read in the file is over a hygiened series — derived from the
    calls rather than from a list of the three I fixed.
    """

    def test_every_atr_read_in_the_scanner_is_over_closed_bars(self):
        src = (REPO / "bot" / "skills" / "scan_skill.py").read_text(encoding="utf-8")
        tree = ast.parse(src)
        offenders = []
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            names = [c.func.attr if isinstance(c.func, ast.Attribute)
                     else getattr(c.func, "id", "")
                     for c in ast.walk(node) if isinstance(c, ast.Call)]
            if "_compute_atr" not in names:
                continue
            if "drop_forming_candle" not in names:
                offenders.append(node.name)
        assert not offenders, (
            "these compute an ATR over a series that may hold a forming bar, "
            "and two of them feed engine.risk.evaluate: " + repr(offenders))


class TestTheGatherVocabularyCoversWhatGatherReturns:
    """Removing the consumer-side drop surfaced fifteen narrowing complaints
    that an untyped return had been laundering through `Any`, and mypy was
    RIGHT: `gather(return_exceptions=True)` hands back a BaseException and the
    check was `isinstance(..., Exception)`, which does not cover one. A leg
    cancelled on its own answers `asyncio.CancelledError` — a BaseException,
    not an Exception — so it was ASSIGNED to `ohlcv` and carried into the
    analysis as an exception OBJECT rather than reported as a failed fetch.

    `_ctx`, three lines below, already read `BaseException`. The two that
    decide the analysis did not.
    """

    def test_both_deciding_legs_read_baseexception(self):
        src = (REPO / "bot" / "core" / "engine.py").read_text(encoding="utf-8")
        tree = ast.parse(src)
        checks = []
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Name)
                    and node.func.id == "isinstance"
                    and len(node.args) == 2):
                continue
            target, cls = node.args
            if not (isinstance(target, ast.Name)
                    and target.id in ("_r_ohlcv", "_r_of")):
                continue
            checks.append((target.id, getattr(cls, "id", ast.unparse(cls))))
        assert checks, "the two deciding legs are no longer bound to names"
        for name, cls in checks:
            assert cls == "BaseException", (
                f"{name} is checked against {cls}, which does not cover a "
                f"cancelled leg")

    def test_the_candles_are_bound_only_once_the_fetch_is_known_good(self):
        """`ohlcv` used to be assigned above the guard with `None` for the
        failure case, then read forty lines further down — so every reader
        carried an absence the guard had already returned on."""
        src = (REPO / "bot" / "core" / "engine.py").read_text(encoding="utf-8")
        tree = ast.parse(src)
        guard_line = None
        binds: list[int] = []
        for node in ast.walk(tree):
            if (isinstance(node, ast.Assign)
                    and any(isinstance(t, ast.Name) and t.id == "ohlcv"
                            for t in node.targets)
                    and isinstance(node.value, ast.Name)
                    and node.value.id == "_r_ohlcv"):
                binds.append(node.lineno)
            if (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Name)
                    and node.func.id == "isinstance"
                    and len(node.args) == 2
                    and isinstance(node.args[0], ast.Name)
                    and node.args[0].id == "_r_ohlcv"):
                guard_line = node.lineno if guard_line is None else guard_line
        # EXACTLY one. A second binding above the guard changes no behaviour
        # (the lower one wins) and is therefore invisible from any assertion
        # about the value -- but it leaves a reader two answers about where
        # `ohlcv` comes from, and one of them is the shape being removed. The
        # mutation round is what said this assertion had to count.
        assert len(binds) == 1, (
            f"`ohlcv` is bound from the leg {len(binds)} times: {binds}")
        assert guard_line is not None and guard_line < binds[0], (
            "`ohlcv` is bound before the branch that returns on a failed fetch")
