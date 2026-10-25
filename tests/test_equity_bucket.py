"""Tokenized-equity bucket: detection, ceilings, adaptive cuts, isolation.

The bucket is off unless EQUITY_BUCKET_ENABLED is set on a venue testnet or
a paper process. These tests pass that environment in, or set it on the
process and clear it on the way out, so a default run is unchanged.
"""
from __future__ import annotations

import pytest

from bot.core.equity_bucket import (
    CRYPTO_BASES,
    apply_to_risk,
    bucket_active,
    bucket_closes,
    budget_share,
    crypto_learning_isolated,
    decide,
    is_tokenized_equity,
    record_bucket_close,
    reset_bucket_state,
    slot_order,
    trade_fields,
)
from bot.core.testnet_book import LIVE, PAPER, TESTNET
from bot.learning.confidence_calibration import ConfidenceCalibrator
from bot.learning.setup_expectancy import SetupExpectancy
from bot.learning.voter_weights import VoterWeightLearner
from bot.risk.held_book import HeldRow
from bot.risk.portfolio import PortfolioTracker
from bot.risk.risk_engine import RiskEngine
from bot.utils.models import Direction, TradeIdea

_PAPER_ON = {
    "SIMULATION_MODE": "true",
    "LIVE_TRADING_ENABLED": "0",
    "EQUITY_BUCKET_ENABLED": "1",
}
_LIVE = {
    "SIMULATION_MODE": "false",
    "LIVE_TRADING_ENABLED": "1",
    "EQUITY_BUCKET_ENABLED": "1",
}
_TESTNET_ON = {
    "BITGET_SANDBOX": "1",
    "SIMULATION_MODE": "true",
    "LIVE_TRADING_ENABLED": "0",
    "EQUITY_BUCKET_ENABLED": "1",
}


def _open(symbol, direction="LONG", risk=1000.0, notional=None):
    from bot.core.equity_bucket import OpenLeg
    return OpenLeg(symbol, direction, risk, notional if notional is not None else risk)


def _healthy(n=20):
    return [1.0] * n


def _uncorrelated():
    # Eight points, near-zero correlation, enough for the Pearson floor.
    return {
        "BTC/USDT": [0.02, -0.01, 0.015, -0.02, 0.01, -0.015, 0.02, -0.01],
        "RAAPL/USDT": [0.01, 0.02, -0.01, -0.015, 0.02, 0.01, -0.02, 0.015],
    }


def _decide(legs, position=200.0, **kwargs):
    args = dict(
        symbol="RAAPL/USDT",
        direction="LONG",
        position_usd=position,
        leverage=2,
        stop_distance_pct=0.02,
        legs=legs,
        book_unread=False,
        max_open_positions=5,
        atr=3.0,
        entry_price=100.0,
        depth_usd=1_000_000.0,
        depth_known=True,
        returns=_uncorrelated(),
        closes=_healthy(),
        session_state="open",
        env=_PAPER_ON,
        active=True,
    )
    args.update(kwargs)
    return decide(**args)


def test_metadata_stock_marks_the_r_prefix_family():
    assert is_tokenized_equity("RFSLY/USDT", {"symbolType": "stock"})
    assert is_tokenized_equity("RTSLAUSDT", {"info": {"isRwa": "YES"}})
    assert is_tokenized_equity("RNVDA/USDT:USDT", {"isReality": "yes"})
    # A stock perp that is not the R-prefix family stays out of this bucket.
    assert not is_tokenized_equity("TSLA/USDT:USDT", {"symbolType": "stock"})
    assert not is_tokenized_equity("AAPLON/USDT", {"symbolType": "stock"})


def test_crypto_metadata_and_the_refusal_list_are_not_equities():
    for name in ("RUNE", "RENDER", "RSR", "RVN"):
        assert name in CRYPTO_BASES
        assert not is_tokenized_equity(f"{name}/USDT")
        assert not is_tokenized_equity(f"{name}/USDT", {"symbolType": "crypto"})
        # A mis-tagged row does not pull a crypto base into the equity book.
        assert not is_tokenized_equity(f"{name}/USDT", {"symbolType": "stock", "isRwa": "YES"})
    assert not is_tokenized_equity("RAVE/USDT")
    assert not is_tokenized_equity("RAY/USDT", {"symbolType": "stock"})


def test_fallback_rule_names_the_family_and_not_r_crypto():
    for name in ("RAAPL", "RTSLA", "RNVDA", "RFSLY", "RGOOGL", "RCOIN"):
        assert is_tokenized_equity(f"{name}/USDT")
        assert is_tokenized_equity(f"{name}USDT")
    # An explicit crypto row beats the name list.
    assert not is_tokenized_equity("RAAPL/USDT", {"symbolType": "crypto"})
    assert not is_tokenized_equity("RAAPL/USDT", {"isRwa": "NO"})


def test_bucket_is_off_by_default_and_off_on_live_mainnet():
    assert not bucket_active({})
    assert not bucket_active({"EQUITY_BUCKET_ENABLED": "1", **{k: v for k, v in _LIVE.items()}})
    from bot.core.testnet_book import deployment_kind
    assert deployment_kind(_LIVE) == LIVE
    assert deployment_kind(_PAPER_ON) == PAPER
    assert deployment_kind(_TESTNET_ON) == TESTNET
    assert bucket_active(_PAPER_ON)
    assert bucket_active(_TESTNET_ON)
    assert not bucket_active({"BITGET_SANDBOX": "1", "SIMULATION_MODE": "true"})
    assert not bucket_active({"SIMULATION_MODE": "true", "EQUITY_BUCKET_ENABLED": "0"})


def test_flag_off_leaves_the_decision_alone():
    assert decide(
        symbol="RAAPL/USDT", direction="LONG", position_usd=100, leverage=2,
        stop_distance_pct=0.02, legs=(), book_unread=False, max_open_positions=5,
        env={"SIMULATION_MODE": "true"}, active=False,
    ) is None


def test_max_two_equity_positions():
    legs = [_open("RAAPL/USDT"), _open("RTSLA/USDT", direction="SHORT")]
    effect = _decide(legs, symbol="RNVDA/USDT")
    assert effect is not None
    assert any(line.startswith("EQUITY_MAX_OPEN") for line in effect.failed)


def test_slot_protection_refuses_when_crypto_holds_the_book():
    legs = [_open(f"C{i}/USDT") for i in range(5)]
    effect = _decide(legs, max_open_positions=5)
    assert effect is not None
    assert any(line.startswith("EQUITY_SLOT") for line in effect.failed)


def test_crypto_is_ordered_ahead_of_equities():
    class Idea:
        def __init__(self, asset):
            self.asset = asset
    pairs = [("e", Idea("RAAPL/USDT")), ("c", Idea("BTC/USDT")), ("e2", Idea("RTSLA/USDT"))]
    ordered = slot_order(pairs, env=_PAPER_ON)
    assert [pair[0] for pair in ordered] == ["c", "e", "e2"]
    assert slot_order(pairs, env={"SIMULATION_MODE": "true"})[0][0] == "e"


def test_per_trade_risk_is_half_the_crypto_size():
    effect = _decide([_open("BTC/USDT", risk=100_000)])
    assert effect is not None
    assert effect.failed == ()
    assert effect.multiplier == pytest.approx(0.5)
    assert "EQUITY_RISK_MULTIPLE" in effect.audit["reasons"]
    assert effect.position_usd == pytest.approx(100.0)


def test_risk_share_caps_equity_at_twenty_percent_of_open_risk():
    # Crypto risk 100. At a 20% share the new equity risk can be 25.
    # Margin 200 at 0.5x is 100, times leverage 2 times a 50% stop is risk 100,
    # which has to shrink to 25: size 25.
    effect = _decide(
        [_open("BTC/USDT", risk=100.0)],
        position=200.0,
        leverage=2,
        stop_distance_pct=0.50,
    )
    assert effect is not None
    assert "EQUITY_RISK_SHARE" in effect.audit["reasons"]
    assert effect.position_usd == pytest.approx(25.0)


def test_flat_book_cannot_be_an_all_equity_book():
    effect = _decide([])
    assert effect is not None
    assert any(line.startswith("EQUITY_RISK_SHARE") for line in effect.failed)


def test_same_direction_equities_cluster():
    returns = dict(_uncorrelated())
    returns["RTSLA/USDT"] = [0.01, -0.02, 0.01, -0.02, 0.015, -0.01, 0.02, -0.015]
    effect = _decide([
        _open("BTC/USDT", risk=100_000),
        _open("RTSLA/USDT", direction="LONG", risk=10.0),
    ], returns=returns)
    assert effect is not None
    assert "EQUITY_CLUSTER_SAME_DIRECTION" in effect.audit["reasons"]
    # 0.5 risk multiple, then 0.5 for the one same-direction equity.
    assert effect.multiplier == pytest.approx(0.25)


def test_measured_correlation_shrinks_and_a_missing_series_assumes_it():
    hot = {
        "BTC/USDT": [0.01, 0.02, -0.01, 0.015, -0.02, 0.01, 0.02, -0.015],
        "RAAPL/USDT": [0.01, 0.02, -0.01, 0.015, -0.02, 0.01, 0.02, -0.015],
    }
    measured = _decide([_open("BTC/USDT", risk=100_000)], returns=hot)
    assert measured is not None
    assert "EQUITY_CORR_MEASURED" in measured.audit["reasons"]
    assert measured.multiplier < 0.5

    assumed = _decide([_open("BTC/USDT", risk=100_000)], returns=None)
    assert assumed is not None
    assert "EQUITY_CORR_ASSUMED" in assumed.audit["reasons"]
    assert assumed.multiplier == pytest.approx(0.25)


def test_vol_scales_to_the_equitys_own_realized_vol():
    # ATR 6 on a price of 100 is 6% against a 3% target: another 0.5.
    effect = _decide([_open("BTC/USDT", risk=100_000)], atr=6.0, entry_price=100.0)
    assert effect is not None
    assert "EQUITY_VOL_SCALE" in effect.audit["reasons"]
    assert effect.multiplier == pytest.approx(0.25)

    unread = _decide([_open("BTC/USDT", risk=100_000)], atr=None)
    assert unread is not None
    assert "EQUITY_VOL_UNKNOWN" in unread.audit["reasons"]
    assert unread.multiplier == pytest.approx(0.25)


def test_budget_starts_at_the_floor_shrinks_and_recovers_one_step():
    floor, reasons = budget_share(
        [1.0, 1.0, 1.0], ceiling=0.20, floor=0.05, window=20,
        min_closes=8, dd_limit=0.10, step=0.02)
    assert floor == pytest.approx(0.05)
    assert reasons == ("EQUITY_BUDGET_FLOOR",)

    shrunk, s_reasons = budget_share(
        [1.0] * 10 + [-5.0], ceiling=0.20, floor=0.05, window=20,
        min_closes=8, dd_limit=0.10, step=0.02)
    assert shrunk == pytest.approx(0.05)
    assert s_reasons == ("EQUITY_BUDGET_SHRINK",)

    # Eight losses, then eight equal wins: the window's profit factor is 1
    # and it never made a positive peak, so the share steps once off the floor
    # and does not jump back to the ceiling.
    recovered, r_reasons = budget_share(
        [-1.0] * 8 + [1.0] * 8, ceiling=0.20, floor=0.05, window=20,
        min_closes=8, dd_limit=0.10, step=0.02)
    assert recovered == pytest.approx(0.07)
    assert r_reasons == ("EQUITY_BUDGET_RECOVER",)

    cold = _decide([_open("BTC/USDT", risk=100_000)], closes=[1.0, 1.0])
    assert cold is not None
    assert "EQUITY_BUDGET_FLOOR" in cold.audit["reasons"]
    assert cold.audit["budget_share"] == pytest.approx(0.05)


def test_depth_caps_notional_and_an_unread_book_refuses():
    # 0.5x of margin 200 is 100, notional at 2x is 200. Ten percent of a
    # 500 depth is 50 notional, so margin becomes 25.
    capped = _decide([_open("BTC/USDT", risk=100_000)], depth_usd=500.0, depth_known=True)
    assert capped is not None
    assert "EQUITY_DEPTH_CAP" in capped.audit["reasons"]
    assert capped.position_usd == pytest.approx(25.0)

    unread = _decide([_open("BTC/USDT", risk=100_000)], depth_known=False, depth_usd=None)
    assert unread is not None
    assert any(line.startswith("EQUITY_DEPTH_UNKNOWN") for line in unread.failed)

    empty = _decide([_open("BTC/USDT", risk=100_000)], depth_known=True, depth_usd=0.0)
    assert empty is not None
    assert any(line.startswith("EQUITY_DEPTH_CAP") for line in empty.failed)


def test_closed_session_scales_and_an_unread_clock_does_not_skip():
    thin = _decide([_open("BTC/USDT", risk=100_000)], session_state="closed")
    assert thin is not None
    assert "EQUITY_SESSION_THIN" in thin.audit["reasons"]
    assert thin.multiplier == pytest.approx(0.5 * 0.25)
    assert thin.failed == ()

    skip = _decide(
        [_open("BTC/USDT", risk=100_000)],
        session_state="closed",
        env={**_PAPER_ON, "EQUITY_SESSION_CLOSED_MULT": "0"},
    )
    assert skip is not None
    assert any(line.startswith("EQUITY_SESSION_CLOSED") for line in skip.failed)

    unknown = _decide([_open("BTC/USDT", risk=100_000)], session_state="unknown")
    assert unknown is not None
    assert "EQUITY_SESSION_UNREAD" in unknown.audit["reasons"]
    assert unknown.failed == ()
    assert unknown.multiplier == pytest.approx(0.5)


def test_unreadable_open_risk_refuses():
    effect = _decide([_open("BTC/USDT", risk=None, notional=None)])
    assert effect is not None
    assert any(line.startswith("EQUITY_RISK_UNREAD") for line in effect.failed)
    # apply_to_risk treats a missing notional as an unread book.
    row = HeldRow("BTC/USDT", "LONG", None, None)
    hooked = apply_to_risk(
        symbol="RAAPL/USDT", direction="LONG", position_usd=100, leverage=2,
        stop_distance_pct=0.02, atr=3.0, entry_price=100.0,
        held_rows=[row], portfolio=None, book_unread=False, max_open_positions=5,
        depth_signal=None, env=_PAPER_ON,
    )
    assert hooked is not None
    assert any(line.startswith("EQUITY_BOOK_UNREAD") for line in hooked.failed)


class _Row:
    def __init__(self, symbol, won, votes=None, trade_id="T1"):
        self.symbol = symbol
        self.paper_trade_id = trade_id
        self.pnl_result = 1.0 if won else -1.0
        self.confidence = 0.8
        self.blended_confidence_raw = 0.8
        self.confidence_basis = "measured"
        self.confluence_votes = votes
        self.market_regime = "trend"
        self.direction = "LONG"
        self.decision = "APPROVED"


def _learning_rows():
    # A decision row (no pnl) joined to an outcome row, plus a setup row
    # that carries its own pnl. One equity, one crypto.
    btc_decision = _Row("BTC/USDT", True, votes=[("rsi", 1, 1.0)], trade_id="BTC")
    btc_decision.pnl_result = None
    btc_outcome = _Row("BTC/USDT", True, trade_id="BTC")
    eq_decision = _Row("RAAPL/USDT", False, votes=[("rsi", 1, 1.0)], trade_id="AAPL")
    eq_decision.pnl_result = None
    eq_outcome = _Row("RAAPL/USDT", False, trade_id="AAPL")
    return [btc_decision, btc_outcome, eq_decision, eq_outcome]


def test_equity_closes_do_not_feed_crypto_learners(monkeypatch):
    monkeypatch.setenv("EQUITY_BUCKET_ENABLED", "0")
    monkeypatch.setenv("SIMULATION_MODE", "true")
    monkeypatch.setenv("LIVE_TRADING_ENABLED", "0")
    rows = _learning_rows()
    assert len(ConfidenceCalibrator.samples_from_decisions(rows)) == 2
    assert len(VoterWeightLearner.samples_from_decisions(rows)) == 2
    assert len(SetupExpectancy.samples_from_decisions(
        [_Row("BTC/USDT", True, trade_id="B"), _Row("RAAPL/USDT", False, trade_id="A")])) == 2

    monkeypatch.setenv("EQUITY_BUCKET_ENABLED", "1")
    cal = ConfidenceCalibrator.samples_from_decisions(rows)
    votes = VoterWeightLearner.samples_from_decisions(rows)
    setups = SetupExpectancy.samples_from_decisions(
        [_Row("BTC/USDT", True, trade_id="B"), _Row("RAAPL/USDT", False, trade_id="A")])
    assert len(cal) == 1
    assert cal[0][1] is True
    assert len(votes) == 1
    assert len(setups) == 1
    assert setups[0][0] == "BTC/USDT"
    assert crypto_learning_isolated("RAAPL/USDT")
    assert not crypto_learning_isolated("BTC/USDT")
    assert trade_fields("RAAPL/USDT") == {"asset_class": "equity"}
    assert trade_fields("RUNE/USDT") == {}


def test_isolated_live_close_counts_for_daily_loss_and_not_the_streak(tmp_path):
    engine = RiskEngine(PortfolioTracker(initial_balance=10_000, state_file=str(tmp_path / "p.json")),
                        state_file=str(tmp_path / "risk.json"))
    engine.record_live_trade_result(-25.0, isolated_equity=True)
    assert engine.consecutive_losses == 0
    assert engine.live_daily_pnl_today() == pytest.approx(-25.0)
    engine.record_live_trade_result(-10.0)
    assert engine.consecutive_losses == 1
    assert engine.live_daily_pnl_today() == pytest.approx(-35.0)


def test_paper_equity_close_does_not_move_the_streak(monkeypatch, tmp_path):
    monkeypatch.setenv("EQUITY_BUCKET_ENABLED", "1")
    monkeypatch.setenv("SIMULATION_MODE", "true")
    monkeypatch.setenv("LIVE_TRADING_ENABLED", "0")
    monkeypatch.delenv("BITGET_SANDBOX", raising=False)
    monkeypatch.delenv("HYPERLIQUID_TESTNET", raising=False)
    reset_bucket_state()
    risk = RiskEngine(
        PortfolioTracker(initial_balance=10_000, state_file=str(tmp_path / "unused.json")),
        state_file=str(tmp_path / "risk.json"))
    book = PortfolioTracker(initial_balance=10_000, state_file=str(tmp_path / "book.json"))
    seen = {}

    def _close(pnl, symbol=""):
        seen["symbol"] = symbol
        if symbol and crypto_learning_isolated(symbol):
            record_bucket_close(pnl, symbol)
            return
        risk.record_trade_result(pnl)

    book._on_trade_close = _close
    idea = TradeIdea(
        id="eq1", asset="RAAPL/USDT", direction=Direction.LONG,
        entry_price=100.0, stop_loss=95.0, take_profit=110.0,
        confidence=0.8, reasoning="test", signals_used=["rsi"])
    book.open_position(idea, size_usd=100.0, leverage=1)
    book.close_position("eq1", 90.0)
    assert seen["symbol"] == "RAAPL/USDT"
    assert risk.consecutive_losses == 0
    assert len(bucket_closes()) == 1
    assert bucket_closes()[0] < 0
    # A stop-out takes the same door: the symbol still arrives, so the
    # streak stays put and the bucket records the close.
    stopped = TradeIdea(
        id="eq2", asset="RTSLA/USDT", direction=Direction.LONG,
        entry_price=100.0, stop_loss=95.0, take_profit=110.0,
        confidence=0.8, reasoning="test", signals_used=["rsi"])
    book.open_position(stopped, size_usd=100.0, leverage=1)
    hit = book.check_stops({"RTSLA/USDT": 90.0})
    assert len(hit) == 1
    assert seen["symbol"] == "RTSLA/USDT"
    assert risk.consecutive_losses == 0
    assert len(bucket_closes()) == 2


def test_risk_gate_tags_an_equity_and_leaves_crypto_alone(monkeypatch, tmp_path):
    monkeypatch.setenv("EQUITY_BUCKET_ENABLED", "1")
    monkeypatch.setenv("SIMULATION_MODE", "true")
    monkeypatch.setenv("LIVE_TRADING_ENABLED", "0")
    monkeypatch.delenv("BITGET_SANDBOX", raising=False)
    monkeypatch.delenv("HYPERLIQUID_TESTNET", raising=False)

    def _idea(asset):
        return TradeIdea(
            id=asset, asset=asset, direction=Direction.LONG,
            entry_price=100.0, stop_loss=98.0, take_profit=106.0,
            confidence=0.9, reasoning="test", signals_used=["rsi"])

    risk = RiskEngine(
        PortfolioTracker(initial_balance=10_000, state_file=str(tmp_path / "p.json")),
        state_file=str(tmp_path / "risk.json"))
    equity = risk.evaluate(_idea("RAAPL/USDT"), atr=2.0)
    crypto = risk.evaluate(_idea("BTC/USDT"), atr=2.0)
    assert any(line.startswith("EQUITY_") for line in equity.checks_failed + equity.checks_passed)
    assert not any(line.startswith("EQUITY_") for line in crypto.checks_failed)
    # The crypto check can still say "EQUITY: zero..." only on a zero book.
    # This book is funded, so the only EQUITY token on a crypto idea would be
    # this bucket, and it must not be there.
    assert not any(line.startswith("EQUITY_") for line in crypto.checks_passed)

