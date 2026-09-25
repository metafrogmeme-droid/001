"""In live mode the VaR gate and Kelly's half-fraction read the live record.

Both VaR paths read the PAPER tracker: the covariance path took its equity
and open positions, the per-trade proxy its trade history. No live fill
writes that tracker. Driven on the operator engine in live mode, $200 of
equity, two live positions of $500 notional on the book, the covariance
path on (live risk hardening turns it on):

    PORTFOLIO_VAR: 0.04% <= 15.0% limit      the gate's line
    12.50%                                    the same formula handed the
                                              live equity and the live rows

a $200 account priced as a $10,000 one holding nothing. On a per-user
engine the tracker is the person's PRACTICE book, so practice shorts were
the "current portfolio" a live long joined; and with no price history the
fall-through proxy read the paper record ("skipped (insufficient trade
history)" over a live record it never looked at).

Kelly's half-Kelly ceiling read the same tracker's history: a live record
of 20 closes at 75% gave a $0 ceiling (a no-op) on the operator engine,
and 20 PRACTICE closes gave $130 on a live $1,000 on a per-user engine.

The evaluation hands the live book and the live equity in
(`_compute_live_var`), the proxy reads the live record's per-close returns
(`_realized_return_window`, fed on every priced live close and seeded from
the closed-trade record at boot), and Kelly reads the realized window in
live mode. The tracker is read by neither in live mode.
"""
from __future__ import annotations

import ast
import dataclasses
import inspect
import os
import random
import statistics
from datetime import datetime
from types import SimpleNamespace

import pytest

import bot.config as bot_config
import bot.risk.risk_engine as risk_engine_mod
from bot.compat import UTC
from bot.core import engine as engine_mod
from bot.core import live_executor
from bot.risk.held_book import HeldRow
from bot.risk.portfolio import PortfolioTracker
from bot.risk.risk_engine import _NOT_ENFORCED, RiskEngine, VarStatus
from bot.utils.close_reason import NON_FILL_CLOSE_REASONS
from bot.utils.models import Direction, TradeExecution, TradeIdea, TradeStatus


class _Session:
    size_multiplier = 1.0


@pytest.fixture(autouse=True)
def _one_session(monkeypatch):
    """The session scales size by the wall clock; the figures here are
    written at x1.0 (the live-book suite records why)."""
    monkeypatch.setattr("bot.core.session_aware.get_current_session",
                        lambda now=None: _Session())


@pytest.fixture
def covariance(monkeypatch):
    """The covariance path on, as live risk hardening turns it on, with a
    floor a 40-point series clears."""
    new_risk = dataclasses.replace(bot_config.CONFIG.risk, var_covariance_enabled=True,
                                   var_covariance_min_points=5)
    new_cfg = dataclasses.replace(bot_config.CONFIG, risk=new_risk)
    monkeypatch.setattr(bot_config, "CONFIG", new_cfg)
    monkeypatch.setattr(risk_engine_mod, "CONFIG", new_cfg)
    return new_cfg


@pytest.fixture
def enforce():
    """LIVE_BOOK_RISK_GATES_ENABLED on the config the risk engine reads."""
    cfg = risk_engine_mod.CONFIG
    prev = cfg.risk.live_book_risk_gates_enabled

    def _set(on):
        object.__setattr__(cfg.risk, "live_book_risk_gates_enabled", on)

    try:
        _set(True)
        yield _set
    finally:
        _set(prev)


def _series(p0, n=40, vol=0.02, seed=1):
    rng = random.Random(seed)
    out, p, t = [], p0, 1_700_000_000.0
    for i in range(n):
        p *= 1 + rng.gauss(0, vol)
        out.append((t + 60 * i, p))
    return out


HIST = {"BTC/USDT": _series(60_000.0, seed=1), "ETH/USDT": _series(3_000.0, seed=2),
        "SOL/USDT": _series(150.0, seed=3)}
PX = {"BTC/USDT": 60_000.0, "ETH/USDT": 3_000.0, "SOL/USDT": 150.0}


def _idea(asset="BTC/USDT", direction=Direction.LONG, price=None):
    price = PX[asset.split(":")[0]] if price is None else price
    if direction == Direction.LONG:
        sl, tp = price * 0.98, price * 1.06
    else:
        sl, tp = price * 1.02, price * 0.94
    return TradeIdea(asset=asset, direction=direction, entry_price=price, stop_loss=sl,
                     take_profit=tp, confidence=0.8, reasoning="x")


def _engine(tmp_path, portfolio=None, name="r", history=True):
    eng = RiskEngine(portfolio or PortfolioTracker(initial_balance=10_000.0),
                     state_file=os.path.join(str(tmp_path), name + ".json"))
    eng._price_history = {k: list(v) for k, v in HIST.items()} if history else {}
    return eng


def _live(eng, book, idea=None, equity=200.0):
    return eng.evaluate(idea or _idea(), atr=600.0, live_equity=equity, max_position_usd=None,
                        live_open_count=len(book or ()), live_mode=True, live_book=book)


def _line(check, gate="PORTFOLIO_VAR"):
    lines = [x for x in check.checks_passed + check.checks_failed if x.startswith(gate)]
    assert len(lines) == 1, (gate, lines)
    return lines[0]


def _paper_twin(equity, rows):
    """The same rows in a paper tracker at the same equity: what the formula
    answers when it is handed the live figures."""
    pt = PortfolioTracker(initial_balance=equity)
    for r in rows:
        base = r.symbol.split(":")[0]
        d = Direction.LONG if r.direction == "LONG" else Direction.SHORT
        pt.open_position(_idea(base, d), r.margin_usd, leverage=int(r.notional_usd / r.margin_usd))
    return pt


BOOK = (HeldRow("ETH/USDT", "LONG", 100.0, 500.0), HeldRow("SOL/USDT", "LONG", 100.0, 500.0))


def _closes(n_wins, n_losses, win=50.0, loss=20.0):
    out = []
    for i in range(n_wins + n_losses):
        pnl = win if i < n_wins else -loss
        out.append(TradeExecution(
            trade_id=f"C{i}", asset="BTC/USDT", direction=Direction.LONG, entry_price=100.0,
            stop_loss=98.0, take_profit=106.0, exit_price=105.0 if pnl > 0 else 98.0,
            quantity=1.0, status=TradeStatus.EXECUTED, pnl=pnl))
    return out


# ── the VaR gate over the live book ────────────────────────────────────────


def test_the_live_book_is_priced_at_the_live_equity(tmp_path, covariance, enforce):
    """The case that was driven: the line names the live book and carries the
    figure the formula gives when handed the live figures."""
    eng = _engine(tmp_path)
    check = _live(eng, BOOK)
    line = _line(check)
    assert line.endswith("<= 15.0% limit on the live book (2 held)"), line
    twin = _engine(tmp_path, _paper_twin(200.0, BOOK), name="twin")
    truth = twin._compute_portfolio_var(check.position_size_usd, idea=_idea())
    assert truth.status == VarStatus.OK and truth.proposed_var_pct > 5.0, truth
    assert f"PORTFOLIO_VAR: {truth.proposed_var_pct:.2f}% <=" in line
    # What the gate read before: the empty $10,000 tracker.
    stale = eng._compute_portfolio_var(check.position_size_usd, idea=_idea())
    assert stale.proposed_var_pct < 0.1, stale


def test_a_practice_book_on_the_tracker_is_not_the_live_book(tmp_path, covariance, enforce):
    """A per-user engine's tracker holds the person's PRACTICE positions;
    two practice shorts used to be the portfolio a live long joined."""
    practice = _paper_twin(10_000.0, (HeldRow("ETH/USDT", "SHORT", 100.0, 500.0),
                                      HeldRow("SOL/USDT", "SHORT", 100.0, 500.0)))
    assert len(practice.open_positions) == 2
    with_practice = _line(_live(_engine(tmp_path, practice, name="u"), BOOK))
    clean = _line(_live(_engine(tmp_path), BOOK))
    assert with_practice == clean


def test_a_book_the_cap_refuses_is_refused_by_name_and_reported_unenforced(tmp_path, covariance, enforce):
    heavy = (HeldRow("ETH/USDT", "LONG", 1_000.0, 5_000.0), HeldRow("SOL/USDT", "LONG", 1_000.0, 5_000.0))
    check = _live(_engine(tmp_path), heavy)
    line = _line(check)
    assert line in check.checks_failed and check.verdict.value == "REJECTED"
    assert line.startswith("PORTFOLIO_VAR: proposed ") and "> 15.0% limit" in line
    assert line.endswith("on the live book (2 held)"), line
    enforce(False)
    check = _live(_engine(tmp_path, name="off"), heavy)
    line = _line(check)
    assert line in check.checks_passed and line.endswith(f"({_NOT_ENFORCED})"), line


def test_an_unread_notional_cannot_be_checked(tmp_path, covariance, enforce):
    book = (HeldRow("ETH/USDT", "LONG", None, None), BOOK[1])
    check = _live(_engine(tmp_path), book)
    assert _line(check) == ("PORTFOLIO_VAR: notional or side unread on 1 of 2 held "
                            "position(s), so the 15.0% cap cannot be checked")
    assert _line(check) in check.checks_failed
    enforce(False)
    check = _live(_engine(tmp_path, name="off"), book)
    assert _line(check) in check.checks_passed and _NOT_ENFORCED in _line(check)


def test_an_unread_side_is_unread_too(tmp_path, covariance, enforce):
    """An unreadable side cannot be signed, and a wrong sign is a hedge read
    as a doubling (or the reverse)."""
    book = (HeldRow("ETH/USDT", "", 100.0, 500.0), BOOK[1])
    assert "unread on 1 of 2" in _line(_live(_engine(tmp_path), book))


def test_no_price_history_falls_to_the_live_record_never_the_tracker(tmp_path, covariance, enforce):
    tracker = PortfolioTracker(initial_balance=10_000.0)
    tracker._history.extend(_closes(6, 4))  # the paper proxy used to compute from these
    eng = _engine(tmp_path, tracker, history=False)
    assert _line(_live(eng, BOOK)) == (
        "PORTFOLIO_VAR: skipped (0 priced live close(s) on the record, 5 needed, and "
        "too little price history to model the live book)")
    four = eng.seed_realized_window([1.0] * 4, returns=[0.01, -0.02, 0.015, -0.01])
    assert four == 4
    assert _line(_live(eng, BOOK)).startswith("PORTFOLIO_VAR: skipped (4 priced live close(s)")
    eng.reset_performance_window()
    rets = [0.01, -0.02, 0.015, -0.01, 0.02, -0.005]
    eng.seed_realized_window([1.0] * 6, returns=rets)
    # One row SHORT: the proxy's exposure is gross, so a hedge is not a smaller book.
    hedged = (HeldRow("ETH/USDT", "SHORT", 100.0, 500.0), BOOK[1])
    r = eng._compute_portfolio_var(26.0, idea=_idea(), live_equity=200.0, live_rows=hedged)
    assert r.status == VarStatus.OK and r.note == "per-trade proxy over the live record"
    lev = risk_engine_mod.CONFIG.exchange.default_leverage or 1
    z = RiskEngine._var_z_score(0.95)
    expect = z * statistics.stdev(rets) * (1_000.0 + 26.0 * lev) / 200.0 * 100
    assert r.proposed_var_pct == pytest.approx(expect, abs=1e-3)
    assert r.current_var_pct == pytest.approx(z * statistics.stdev(rets) * 1_000.0 / 200.0 * 100, abs=1e-3)


def test_a_book_nobody_read_is_not_measured(tmp_path, covariance, enforce):
    check = _live(_engine(tmp_path), None)
    line = _line(check)
    assert line == "PORTFOLIO_VAR: not measured - the live book was not read"
    assert line in check.checks_failed


def test_the_row_spelling_is_matched_to_the_price_history(tmp_path, covariance, enforce):
    """The tick keys prices as the scanner spells a symbol; a live row is
    spelled as the venue does. The covariance path has to find the series."""
    eng = _engine(tmp_path)
    venue_spelled = tuple(HeldRow(r.symbol + ":USDT", r.direction, r.margin_usd, r.notional_usd)
                          for r in BOOK)
    a = eng._compute_portfolio_var(26.0, idea=_idea(), live_equity=200.0, live_rows=venue_spelled)
    b = eng._compute_portfolio_var(26.0, idea=_idea(), live_equity=200.0, live_rows=BOOK)
    assert a.note == b.note == "covariance over the live book"
    assert a.proposed_var_pct == b.proposed_var_pct


def test_the_paper_reading_is_unchanged(tmp_path):
    fresh = _engine(tmp_path, name="fresh")
    assert _line(fresh.evaluate(_idea(), atr=600.0)) == "PORTFOLIO_VAR: skipped (insufficient trade history)"
    tracker = PortfolioTracker(initial_balance=10_000.0)
    tracker._history.extend(_closes(3, 2, win=5.0, loss=2.0))
    line = _line(_engine(tmp_path, tracker, name="paper").evaluate(_idea(), atr=600.0))
    assert line.startswith("PORTFOLIO_VAR: ") and line.endswith("% <= 15.0% limit"), line
    assert "live book" not in line


# ── Kelly's record ─────────────────────────────────────────────────────────


def test_kelly_reads_the_live_record_in_live_mode(tmp_path):
    eng = _engine(tmp_path)
    eng.seed_realized_window([50.0] * 15 + [-20.0] * 5)
    paper = _engine(tmp_path, name="p")
    paper._portfolio._history.extend(_closes(15, 5))
    live_ceiling = eng._kelly_size_usd(_idea(), 1_000.0, live_mode=True)
    assert live_ceiling > 0
    assert live_ceiling == paper._kelly_size_usd(_idea(), 1_000.0), "one arithmetic, two records"
    assert eng._kelly_size_usd(_idea(), 1_000.0) == 0.0, "the tracker is empty: the old live reading"


def test_a_practice_record_does_not_size_a_live_order(tmp_path):
    practice = PortfolioTracker(initial_balance=10_000.0)
    practice._history.extend(_closes(15, 5))
    eng = _engine(tmp_path, practice)
    assert eng._kelly_size_usd(_idea(), 1_000.0, live_mode=True) == 0.0
    live = _live(eng, (), equity=10_000.0)
    assert not any("half-Kelly" in step for step in live.size_path), live.size_path
    paper = eng.evaluate(_idea(), atr=600.0)
    assert any("half-Kelly" in step for step in paper.size_path), paper.size_path


def test_the_gate_hands_kelly_the_mode():
    tree = ast.parse(inspect.getsource(risk_engine_mod))
    calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call)
             and isinstance(n.func, ast.Attribute) and n.func.attr == "_kelly_size_usd"]
    assert len(calls) == 1
    args = [ast.unparse(a) for a in calls[0].args]
    assert args == ["idea", "sizing_equity", "live_mode"], args


# ── the return window ──────────────────────────────────────────────────────


def test_a_priced_close_with_a_stated_notional_records_its_return(tmp_path):
    eng = _engine(tmp_path)
    eng.record_live_trade_result(-5.0, notional=500.0)
    eng.record_live_trade_result(7.0)                 # notional never stated
    eng.record_live_trade_result(3.0, notional=0.0)   # a zero is not a notional
    assert list(eng._realized_return_window) == [-0.01]
    assert list(eng._realized_pnl_window) == [-5.0, 7.0, 3.0]


def test_seeding_fills_an_empty_return_window_once(tmp_path):
    eng = _engine(tmp_path)
    assert eng.seed_realized_window([1.0, 2.0], returns=[0.1]) == 2
    assert eng.seed_realized_window([9.0], returns=[0.9]) == 0
    assert list(eng._realized_return_window) == [0.1]
    eng.reset_performance_window()
    assert list(eng._realized_return_window) == [] and list(eng._realized_pnl_window) == []


def _close(pnl, entry=100.0, qty=5.0, reason="SL HIT", when=1):
    return SimpleNamespace(pnl_usd=pnl, cost_usd=100.0, entry_price=entry, quantity=qty,
                           close_reason=reason, closed_at=datetime(2026, 9, when, tzinfo=UTC))


def test_the_closed_record_yields_returns_for_stated_notionals_only():
    rows = [_close(-5.0, when=3), _close(10.0, when=1), _close(4.0, entry=0.0, when=2),
            _close(None, when=4), _close(0.0, reason=next(iter(NON_FILL_CLOSE_REASONS)), when=5)]
    assert live_executor.realized_close_pnls(rows) == [10.0, 4.0, -5.0]
    assert live_executor.realized_close_returns(rows) == [10.0 / 500.0, -5.0 / 500.0]


def _calls(module, name):
    tree = ast.parse(inspect.getsource(module))
    return [n for n in ast.walk(tree) if isinstance(n, ast.Call)
            and isinstance(n.func, ast.Attribute) and n.func.attr == name]


def test_the_engine_records_and_seeds_the_return_beside_the_pnl():
    close = [c for c in _calls(engine_mod, "record_live_trade_result")]
    assert len(close) == 1
    kw = {k.arg: ast.unparse(k.value) for k in close[0].keywords}
    assert kw.get("notional") == "_live_executor_mod.position_size_basis(pos)[1]", kw
    seed = _calls(engine_mod, "seed_realized_window")
    assert len(seed) == 1
    kw = {k.arg: ast.unparse(k.value) for k in seed[0].keywords}
    assert kw.get("returns") == "_live_executor_mod.realized_close_returns(_closed_record)", kw
