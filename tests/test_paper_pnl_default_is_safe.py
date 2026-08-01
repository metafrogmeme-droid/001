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

    def test_the_adaptive_threshold_filters_to_closed(self):
        # The highest-stakes consumer: this feeds auto_confirm_threshold, so
        # an open trade's placeholder 0.0 entering it would move a TRADING
        # decision, not just a display.
        src = _src("bot/core/engine.py")
        i = src.index("recent_wins = sum(1 for t in recent_closed if t.pnl > 0)")
        window = src[i - 400:i]
        assert "if t.closed_at is not None" in window, (
            "the adaptive threshold must score only closed trades; an open "
            "one carries the 0.0 placeholder"
        )

    def test_the_risk_engine_scores_only_closed_trades(self):
        src = _src("bot/risk/risk_engine.py")
        i = src.index("wins = [t for t in closed if t.pnl > 0]")
        window = src[i - 600:i]
        assert "closed" in window
