"""The paper `pnl: float = 0.0` default, and why it is not changed.

I flagged this default across #1020 and #1021 as a residual conflation: a
Trade that never recorded a result reads as a measured break-even, the same
absent-is-not-zero rule those PRs enforced everywhere else. The operator
approved changing it. Then the reachability check said not to, and this file
is that check made permanent.

`Portfolio._close_position_locked` closes a paper trade in ONE atomic
mutation:

    trade.model_copy(update={
        "status": TradeStatus.EXECUTED,
        "exit_price": exit_price,
        "pnl": round(net_pnl, 2),
        ...
        "closed_at": datetime.now(UTC),
    })

`pnl` and `closed_at` are set in the same dict literal. There is no ordering
between them to get wrong and no branch that sets one without the other, so a
CLOSED paper trade cannot carry an unset pnl. The 0.0 default reaches only
OPEN trades, and every consumer filters those out.

Changing the field to Optional would therefore fix a defect that cannot
occur, while touching twenty-plus call sites including risk_engine's
win/loss split, portfolio's `sum(t.pnl ...)` arithmetic, and the
adaptive-threshold input in engine.py. Optional there means TypeError on
every one of those sums unless each is audited. That is real risk bought
with no safety.

So the invariant is PINNED instead. If someone later adds a close path that
sets closed_at without pnl -- the only way the default could ever be read as
a measurement -- these tests fail and the Optional refactor becomes the
right answer after all. Until then the default is safe, and now it is safe
for a checked reason rather than an assumed one.

    THE FIX FOR AN UNREACHABLE DEFECT IS THE PROOF THAT IT IS UNREACHABLE.
"""
from __future__ import annotations

from datetime import datetime

import pytest

import bot.config as bot_config
from bot.compat import UTC
from bot.config import RUNTIME
from bot.core import engine as engine_mod
from bot.core.engine import RuneClawEngine
from bot.risk.portfolio import PortfolioTracker
from bot.risk.risk_engine import RiskEngine
from bot.utils.models import Direction, TradeExecution, TradeStatus
from tests.source_scan import code_only


def _src(path: str) -> str:
    return code_only(open(path, encoding="utf-8").read())


class TestClosingAlwaysRecordsAPnl:
    def test_close_sets_pnl_and_closed_at_in_one_mutation(self):
        src = _src("bot/risk/portfolio.py")
        i = src.index("def _close_position_locked")
        body = src[i:i + 4000]
        j = body.index("model_copy(update={")
        block = body[j:j + 500]
        assert '"pnl": round(net_pnl, 2)' in block, (
            "pnl must be written by the same mutation that closes the trade"
        )
        assert '"closed_at": datetime.now(UTC)' in block, (
            "closed_at must be written by that SAME mutation -- if these ever "
            "split into separate statements, a crash between them leaves a "
            "closed trade with a 0.0 pnl that reads as break-even"
        )

    def test_no_other_path_closes_a_paper_trade(self):
        # A second close path is how the invariant above would quietly stop
        # holding. The live executor closes LivePositions (pnl_usd, already
        # Optional and handled); this is about the paper Trade model.
        src = _src("bot/risk/portfolio.py")
        assert src.count("model_copy(update={") == 1, (
            "more than one mutation site in portfolio.py — check that each "
            "sets pnl alongside closed_at, or make pnl Optional and audit "
            "every consumer"
        )

    def test_the_field_is_still_a_plain_float(self):
        # The guard that tells a future reader this was a decision, not an
        # oversight. If this assertion is what fails, read the module
        # docstring above before changing it back.
        src = _src("bot/utils/models.py")
        assert "pnl: float = 0.0" in src, (
            "the default was changed to Optional — every `sum(t.pnl ...)` and "
            "`t.pnl > 0` in risk_engine, portfolio, multi_portfolio and "
            "engine's adaptive threshold now needs None handling"
        )


class TestTheConsumersOnlyReadClosedTrades:
    """The 0.0 default is harmless precisely because of this filtering."""

    @pytest.fixture
    def paper_adaptive(self, monkeypatch):
        """The adaptive block on, in PAPER mode, at a bar of 0.85, with the
        window and both bars pinned so the arithmetic below is this test's and
        not the environment's. Everything is restored."""
        adaptive = bot_config.CONFIG.adaptive
        pinned = {"adaptive_threshold_enabled": True, "adaptive_threshold_lookback": 10,
                  "adaptive_threshold_high_wr": 0.70, "adaptive_threshold_low_wr": 0.40,
                  "adaptive_threshold_min": 0.60, "adaptive_threshold_max": 0.90}
        prev = {k: getattr(adaptive, k) for k in pinned}
        for k, v in pinned.items():
            object.__setattr__(adaptive, k, v)      # frozen config
        monkeypatch.setattr(type(bot_config.CONFIG), "is_live", lambda self: False)
        monkeypatch.setattr(engine_mod, "audit", lambda log, msg, **kw: None)
        prev_bar = RUNTIME.auto_confirm_threshold
        RUNTIME.auto_confirm_threshold = 0.85
        try:
            yield RUNTIME
        finally:
            RUNTIME.auto_confirm_threshold = prev_bar
            for k, v in prev.items():
                object.__setattr__(adaptive, k, v)

    @staticmethod
    def _trade(i: int, *, closed: bool) -> TradeExecution:
        """A closed paper WIN, or an OPEN trade left exactly as the model
        builds it: `closed_at` None and `pnl` at its 0.0 default -- the
        placeholder this file is about."""
        extra = ({"status": TradeStatus.EXECUTED, "exit_price": 105.0, "pnl": 5.0,
                  "closed_at": datetime(2026, 9, 1, tzinfo=UTC)} if closed else {})
        return TradeExecution(trade_id=f"T{i}", asset="BTC/USDT", direction=Direction.LONG,
                              entry_price=100.0, stop_loss=98.0, take_profit=106.0,
                              quantity=1.0, **extra)

    @staticmethod
    def _engine(tmp_path, book, tag: str) -> RuneClawEngine:
        eng = RuneClawEngine.__new__(RuneClawEngine)
        eng.portfolio = PortfolioTracker(initial_balance=10_000.0)
        eng.portfolio._history.extend(book)
        eng.risk = RiskEngine(PortfolioTracker(), state_file=str(tmp_path / f"r{tag}.json"))
        return eng

    def test_the_adaptive_threshold_filters_to_closed(self, tmp_path, paper_adaptive):
        # The highest-stakes consumer: this feeds auto_confirm_threshold, so
        # an open trade's placeholder 0.0 entering it would move a TRADING
        # decision, not just a display. DRIVEN: the block is the seam
        # `_adapt_auto_confirm_threshold` now, and the scan that stood here
        # pinned the spelling `recent_wins = sum(...)`, which went red on the
        # seam's rewrite while the property it guards held throughout.
        #
        # Three closed wins beside seven OPEN trades fill the ten-trade
        # window. Read as closed only, three is under the five-close floor and
        # the bar stays. Read raw, the seven placeholders are seven losses --
        # a 30% "record" under the 40% losing bar -- and the bar RISES: a
        # trading decision moved by zeros nobody measured.
        book = ([self._trade(i, closed=True) for i in range(3)]
                + [self._trade(10 + i, closed=False) for i in range(7)])
        self._engine(tmp_path, book, "a")._adapt_auto_confirm_threshold()
        assert paper_adaptive.auto_confirm_threshold == pytest.approx(0.85), (
            "three closed wins are under the five-close floor; the seven open "
            "placeholders must not be counted as seven losses"
        )
        # And the other direction, so a filter that is merely ABSENT cannot
        # pass by the floor alone: six closed wins beside four open ones is a
        # 100% record over six and LOWERS the bar one step, where the raw read
        # is 6 of 10 and moves nothing.
        book = ([self._trade(i, closed=True) for i in range(6)]
                + [self._trade(10 + i, closed=False) for i in range(4)])
        self._engine(tmp_path, book, "b")._adapt_auto_confirm_threshold()
        assert paper_adaptive.auto_confirm_threshold == pytest.approx(0.80), (
            "six closed wins are a 100% record; four open placeholders must "
            "not dilute it to a 60% one that moves nothing"
        )

    def test_the_risk_engine_scores_only_closed_trades(self):
        src = _src("bot/risk/risk_engine.py")
        i = src.index("wins = [t for t in closed if t.pnl > 0]")
        window = src[i - 600:i]
        assert "closed" in window
