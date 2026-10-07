"""The drawdown gate reads the account marked to market, not the coin's
wallet balance, and the trip reason attributes a drop to open positions.

From the live bot, 2026-10-05 12:53:11 UTC, thirty minutes after a /reset:

    CIRCUIT BREAKER TRIPPED: max drawdown breached — equity is $12.36 below
    the session peak while REALIZED trade PnL accounts for only $0.00 of it.

No trade had closed. The venue constructor marks every Bitget client
``options.uta``, so ccxt's `fetch_balance` calls ``GET /api/v3/account/assets``
and keeps only the per-coin list; the account-level ``usdtEquity`` and
``usdtUnrealisedPnl`` beside it are dropped. `LiveExecutor.fetch_balance`'s
equity loop then found no ``data`` dict and the figure the gate compared
against its peak was the coin's ``balance``: a wallet figure that is not the
account's equity. A field name is not a quantity.

The executor now reads the envelope itself (one transport, ccxt's own
implicit method), ``total`` is ``usdtEquity``, ``unrealized_pnl`` rides
beside it, and the trip reason reads the unrealized figure: a drop the
realized and unrealized figures explain is a losing book, not a transfer or
a wrong reading. An envelope without ``usdtEquity`` is UNREAD, never the
wallet figure under the same key.
"""
from __future__ import annotations

import asyncio
import copy

import ccxt.async_support as ccxt_async
import pytest

from bot.config import CONFIG
from bot.core.live_executor import LiveExecutor
from bot.core.venues import get_venue
from bot.risk.portfolio import PortfolioTracker
from bot.risk.risk_engine import RiskEngine
from bot.utils.models import Direction, TradeIdea

# The documented shape of GET /api/v3/account/assets, at the balance the
# paste's reset re-seeded the peak from.
ENVELOPE = {
    "code": "00000", "msg": "success", "requestTime": "1759668791000",
    "data": {
        "accountEquity": "147.46", "usdtEquity": "147.46", "btcEquity": "0.0012",
        "unrealisedPnl": "0", "usdtUnrealisedPnl": "0", "btcUnrealizedPnl": "0",
        "effEquity": "147.46", "mmr": "0", "imr": "0", "mgnRatio": "0",
        "positionMgnRatio": "0", "positionValue": "0", "leverage": "0",
        "assets": [{
            "coin": "USDT", "equity": "147.46", "usdValue": "147.46",
            "balance": "147.46", "balanceOriginal": "147.46",
            "available": "147.46", "locked": "0", "debt": "0",
            "bonus": "0", "interestBase": "0",
        }],
    },
}


def _envelope(*, balance, available, locked, equity, unrealized):
    env = copy.deepcopy(ENVELOPE)
    d = env["data"]
    d["usdtEquity"] = d["accountEquity"] = d["effEquity"] = str(equity)
    d["usdtUnrealisedPnl"] = d["unrealisedPnl"] = str(unrealized)
    a = d["assets"][0]
    a["balance"] = a["balanceOriginal"] = str(balance)
    a["equity"] = str(equity)
    a["available"], a["locked"] = str(available), str(locked)
    return env


# $12.36 of margin committed to a position: the wallet figure moves, the
# account marked to market does not.
MARGIN_COMMITTED = _envelope(balance=135.10, available=122.74, locked=12.36,
                             equity=147.46, unrealized=0)
# The same $12.36 as an unrealized loss on an open position.
UNREALIZED_LOSS = _envelope(balance=147.46, available=135.10, locked=12.36,
                            equity=135.10, unrealized=-12.36)


def _unified_client(envelope, *, with_method=True):
    """ccxt's REAL Bitget client, marked UTA as `bot/core/venues.py` marks
    it, with the assets endpoint answered from a planted document and the
    parsed path armed to fail if it is asked."""
    ex = ccxt_async.bitget({"apiKey": "k", "secret": "s", "password": "p",
                            "options": {"defaultType": "swap", "uta": True}})
    calls = {"envelope": 0, "fetch_balance": 0}

    async def _assets(params=None):
        calls["envelope"] += 1
        return copy.deepcopy(envelope)

    async def _fetch_balance(params=None):
        calls["fetch_balance"] += 1
        raise AssertionError("the parsed path was asked on a unified client")

    if with_method:
        ex.privateUtaGetV3AccountAssets = _assets
    else:
        # A client whose implicit methods are not on it (a stand-in).
        ex.privateUtaGetV3AccountAssets = None
    ex.fetch_balance = _fetch_balance
    return ex, calls


class _ClassicStub:
    def __init__(self, payload, options=None):
        self.payload = payload
        self.options = options or {}
        self.asked_envelope = False

    async def privateUtaGetV3AccountAssets(self, params=None):
        self.asked_envelope = True
        raise AssertionError("a classic client asked the UTA envelope")

    async def fetch_balance(self, params=None):
        return self.payload


def _read(exchange) -> dict:
    """Drive the REAL LiveExecutor.fetch_balance over a planted client."""
    ex = LiveExecutor.__new__(LiveExecutor)
    ex._venue = get_venue("bitget")
    ex._exchange = exchange

    async def _get_exchange():
        return ex._exchange

    ex._get_exchange = _get_exchange
    return asyncio.run(LiveExecutor.fetch_balance(ex))


@pytest.fixture
def live(monkeypatch):
    monkeypatch.setattr(type(CONFIG), "is_live", lambda self: True)


def _idea():
    return TradeIdea(asset="BTC/USDT", direction=Direction.LONG, entry_price=100.0,
                     stop_loss=97.0, take_profit=109.0, confidence=0.8,
                     reasoning="t", risk_reward_ratio=3.0)


def _risk(tmp_path, name="risk.json"):
    pt = PortfolioTracker(initial_balance=1000.0,
                          state_file=str(tmp_path / f"pf-{name}"))
    return RiskEngine(pt, state_file=str(tmp_path / name))


# ── the reading ───────────────────────────────────────────────────────────

class TestAUnifiedClientReadsTheAccountMarkedToMarket:

    def test_total_is_usdtEquity_and_the_wallet_figure_is_kept_apart(self):
        ex, calls = _unified_client(MARGIN_COMMITTED)
        out = _read(ex)
        assert "error" not in out, out
        assert out["total"] == pytest.approx(147.46), "the coin's balance was read as the equity"
        assert out["wallet_total"] == pytest.approx(135.10)
        assert out["unrealized_pnl"] == pytest.approx(0.0)
        assert out["equity_source"] == "usdtEquity"
        # The per-coin fields still come through ccxt's own parser.
        assert out["free"] == pytest.approx(122.74) and out["used"] == pytest.approx(12.36)
        assert calls == {"envelope": 1, "fetch_balance": 0}, "one transport, one read"

    def test_an_unrealized_loss_is_in_the_equity_and_named_beside_it(self):
        ex, _ = _unified_client(UNREALIZED_LOSS)
        out = _read(ex)
        assert out["total"] == pytest.approx(135.10)
        assert out["wallet_total"] == pytest.approx(147.46)
        assert out["unrealized_pnl"] == pytest.approx(-12.36)

    def test_an_envelope_without_usdtEquity_is_unread_not_the_wallet_figure(self):
        env = copy.deepcopy(MARGIN_COMMITTED)
        del env["data"]["usdtEquity"]
        ex, _ = _unified_client(env)
        out = _read(ex)
        assert "error" in out and out["total"] == 0, out
        assert out.get("unrealized_pnl") is None
        # Two readings of one quantity on alternate calls would seed the
        # peak from one and compare the other; the wallet figure is never
        # minted under `total`.
        assert out["total"] != pytest.approx(135.10)

    def test_a_non_finite_or_absent_unrealized_figure_is_none_not_zero(self):
        env = copy.deepcopy(MARGIN_COMMITTED)
        env["data"]["usdtUnrealisedPnl"] = "nan"
        assert _read(_unified_client(env)[0])["unrealized_pnl"] is None
        del env["data"]["usdtUnrealisedPnl"]
        assert _read(_unified_client(env)[0])["unrealized_pnl"] is None

    def test_a_unified_client_that_cannot_ask_for_the_envelope_reads_as_before(self):
        """A stand-in without the implicit method: the classic path, which on
        a unified document is the wallet figure, and says so in
        `equity_source` rather than pretending."""
        ex, calls = _unified_client(MARGIN_COMMITTED, with_method=False)

        async def _parsed(params=None):
            calls["fetch_balance"] += 1
            return {"info": copy.deepcopy(MARGIN_COMMITTED["data"]["assets"]),
                    "USDT": {"free": 122.74, "used": 12.36, "total": 135.10}}
        ex.fetch_balance = _parsed
        out = _read(ex)
        assert calls["fetch_balance"] == 1 and calls["envelope"] == 0
        assert out["total"] == pytest.approx(135.10)
        assert out["equity_source"] == "wallet_total"
        assert out["unrealized_pnl"] is None

    def test_a_classic_client_keeps_the_classic_path_and_its_unrealized_figure(self):
        payload = {"info": {"data": [{"marginCoin": "USDT", "usdtEquity": "11.13921165",
                                      "accountEquity": "11.13919278",
                                      "unrealizedPL": "-1.5"}]},
                   "USDT": {"free": 10.0, "used": 2.6, "total": 12.6}}
        stub = _ClassicStub(payload, options={"defaultType": "swap"})
        out = _read(stub)
        assert stub.asked_envelope is False
        assert out["total"] == pytest.approx(11.13921165)
        assert out["equity_source"] == "usdtEquity"
        assert out["unrealized_pnl"] == pytest.approx(-1.5)
        assert out["wallet_total"] == pytest.approx(12.6)

    def test_a_classic_document_with_no_equity_row_has_no_unrealized_figure(self):
        stub = _ClassicStub({"info": {}, "USDT": {"free": 5.0, "used": 0.0, "total": 5.0}})
        out = _read(stub)
        assert out["total"] == pytest.approx(5.0) and out["equity_source"] == "wallet_total"
        assert out["unrealized_pnl"] is None


# ── the gate, through the real RiskEngine ─────────────────────────────────

class TestTheGateOnTheTwoReadings:

    def test_margin_committed_does_not_trip_and_an_unrealized_loss_does(self, live, tmp_path):
        """Peak re-seeded at $147.46 (the paste's reset). Then $12.36 goes
        into a position's margin: the wallet figure reads 8.4% down and the
        OLD reading tripped the 7% live cap on it; the account marked to
        market has not moved and the gate passes. The same $12.36 as an
        unrealized loss IS a drawdown and still trips."""
        seed = _read(_unified_client(ENVELOPE)[0])
        committed = _read(_unified_client(MARGIN_COMMITTED)[0])
        losing = _read(_unified_client(UNREALIZED_LOSS)[0])

        risk = _risk(tmp_path)
        risk.evaluate(_idea(), live_equity=seed["total"], live_mode=True,
                      live_unrealized_pnl=seed["unrealized_pnl"])
        assert risk._live_equity_peak == pytest.approx(147.46)
        risk.evaluate(_idea(), live_equity=committed["total"], live_mode=True,
                      live_unrealized_pnl=committed["unrealized_pnl"])
        assert not risk.circuit_breaker_active, "margin committed to a position tripped the breaker"

        # The other arm, on a fresh engine: the wallet figure the old reading
        # handed the gate does trip, which is the paste.
        old = _risk(tmp_path, "old.json")
        old.evaluate(_idea(), live_equity=seed["wallet_total"], live_mode=True)
        old.evaluate(_idea(), live_equity=committed["wallet_total"], live_mode=True)
        assert old.circuit_breaker_active and old.circuit_trip_cause == "drawdown"

        # And a real mark-to-market drawdown still trips on the new reading.
        risk.evaluate(_idea(), live_equity=losing["total"], live_mode=True,
                      live_unrealized_pnl=losing["unrealized_pnl"])
        assert risk.circuit_breaker_active and risk.circuit_trip_cause == "drawdown"
        assert risk._last_live_unrealized == pytest.approx(-12.36)


# ── the trip reason ───────────────────────────────────────────────────────

class TestTheTripReasonReadsTheOpenBook:

    def _peaked(self, tmp_path):
        risk = _risk(tmp_path)
        risk._live_equity_peak = 147.46
        risk._live_daily_pnl = 0.0
        return risk

    def test_an_unrealized_loss_that_explains_the_drop_is_named_not_guessed(self, tmp_path):
        hint = self._peaked(tmp_path)._drawdown_transfer_hint(135.10, -12.36)
        assert "unrealized PnL $12.36 on open positions account for it" in hint
        assert "deposit/withdrawal" not in hint and "wrong balance reading" not in hint

    def test_a_known_open_book_that_does_not_explain_it_leaves_two_causes(self, tmp_path):
        hint = self._peaked(tmp_path)._drawdown_transfer_hint(135.10, 0.0)
        assert "Two things look like this" in hint
        assert "deposit/withdrawal" in hint and "wrong balance reading" in hint
        assert "cannot see" not in hint, "the reading now includes open positions"

    def test_an_unread_open_book_keeps_the_three_causes(self, tmp_path):
        hint = self._peaked(tmp_path)._drawdown_transfer_hint(135.10, None)
        assert "Three things look like this" in hint
        assert "which this comparison cannot see" in hint

    def test_realized_losses_that_explain_it_say_nothing_as_before(self, tmp_path):
        risk = self._peaked(tmp_path)
        risk._live_daily_pnl = -12.0
        assert risk._drawdown_transfer_hint(135.10, 0.0) == ""
        assert risk._drawdown_transfer_hint(135.10, None) == ""

    def test_an_unrealized_profit_explains_nothing(self, tmp_path):
        hint = self._peaked(tmp_path)._drawdown_transfer_hint(135.10, +5.0)
        assert "Two things look like this" in hint and "$0.00 account for only" in hint


# ── the recheck row carries it, by name ───────────────────────────────────

class TestTheRecheckRowCarriesTheUnrealizedFigure:

    def _engine(self, cached: dict):
        from types import SimpleNamespace

        from bot.core.engine import RuneClawEngine
        eng = RuneClawEngine.__new__(RuneClawEngine)
        eng.live_executor = SimpleNamespace(open_positions=[], user_id=None)
        eng._user_executors = {}
        eng.live_balance_cached = lambda: cached
        eng._executor_for = lambda uid: eng.live_executor
        return eng

    def test_off_the_same_payload_the_equity_came_from(self, live):
        eng = self._engine({"total": 135.10, "free": 122.74, "unrealized_pnl": -12.36,
                            "equity_source": "usdtEquity"})
        _rc = asyncio.run(eng._live_recheck_context(""))
        assert _rc.equity == pytest.approx(135.10)
        assert _rc.unrealized_usd == pytest.approx(-12.36)
        # And what that equity includes, for the tier card's basis sentence.
        assert _rc.equity_source == "usdtEquity"
        wallet = self._engine({"total": 135.10, "free": 122.74, "equity_source": "wallet_total"})
        assert asyncio.run(wallet._live_recheck_context("")).equity_source == "wallet_total"

    def test_every_live_gate_call_carries_the_equity_source_beside_the_unrealized_figure(self):
        # The two live evaluate() calls sit inside handlers no test runs. They
        # hand the gate the equity, its unrealized figure and its source off
        # one payload; a call that passes the figure and not the source would
        # leave the tier card claiming nothing, or the last reading's basis.
        import ast
        from pathlib import Path
        src = (Path(__file__).resolve().parent.parent / "bot/core/engine.py").read_text()
        calls = [n for n in ast.walk(ast.parse(src)) if isinstance(n, ast.Call)
                 and getattr(n.func, "attr", None) == "evaluate"
                 and any(k.arg == "live_unrealized_pnl" for k in n.keywords)]
        assert len(calls) == 2, [c.lineno for c in calls]
        for c in calls:
            assert any(k.arg == "live_equity_source" for k in c.keywords), c.lineno

    def test_a_payload_without_one_hands_the_gate_none_not_zero(self, live):
        eng = self._engine({"total": 135.10, "free": 122.74})
        _rc = asyncio.run(eng._live_recheck_context(""))
        assert _rc.unrealized_usd is None

    def test_a_wallet_coin_with_no_entry_is_not_reported_as_zero(self):
        env = copy.deepcopy(MARGIN_COMMITTED)
        env["data"]["assets"] = []          # the balance coin has no row
        out = _read(_unified_client(env)[0])
        assert "error" not in out and out["total"] == pytest.approx(147.46)
        assert out["wallet_total"] is None and out["free"] is None and out["used"] is None
