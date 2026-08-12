"""The /scan telemetry read a file nothing has ever written.

Two independent breaks, either one enough on its own:

  PATH.  `scan_skill._fetch_live_exchange_data` looked for `closed_trades.json`
  in `bot/`, then in the repo root. `live_executor` has written it to
  `$RUNECLAW_STATE_DIR/closed_trades.json` (default `data/`) since F-14.
  Neither of the two searched locations has ever existed, so every call took
  the "file missing" branch and returned `[]`.

  KEYS.  Even on the days the path was right, the reader asked for `net_pnl`,
  then `pnl`, then fell back to 0. The writer emits `pnl_usd`. Every real row
  therefore summed as a break-even close and scored as a non-win.

`bot/backtest/parity.py` had the path right the whole time, in a docstring,
three directories away. That is the shape: a path restated instead of shared,
which is the same defect as a CI step restated instead of parsed.

WHAT THE PAYLOAD SAID. `total_trades: 0, net_pnl: 0, win_rate: 0` — published
to the website's engine card as a measured record of an account that has been
trading. Not "unavailable"; a specific, wrong, confident number.

The round-trip test below is the one that matters. It writes rows through the
executor's OWN serializer and reads them back through the scan reader, so the
two cannot drift again without failing here. A hand-written fixture would
have carried the reader's wrong keys and passed.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone

import pytest

import bot.skills.scan_skill as ss
from bot.core.live_executor import LivePosition, closed_trade_row


def _pos(pnl_usd, *, symbol="BTC/USDT:USDT", tid="t1"):
    return LivePosition(
        trade_id=tid, symbol=symbol, direction="LONG",
        entry_price=100.0, quantity=1.0, cost_usd=100.0,
        stop_loss=90.0, take_profit=120.0, leverage=1, is_spot=False,
        opened_at=datetime(2026, 8, 1, tzinfo=timezone.utc),
        closed_at=datetime(2026, 8, 2, tzinfo=timezone.utc),
        close_price=110.0, pnl_usd=pnl_usd, status="closed",
        strategy_type="breakout", close_reason="tp",
    )


@pytest.fixture
def record(tmp_path, monkeypatch):
    """Point the reader at a temp file and hand back a writer for it."""
    path = tmp_path / "closed_trades.json"
    monkeypatch.setattr(ss, "_closed_trades_file", lambda: str(path))

    def write(rows):
        path.write_text(json.dumps(rows, default=str), encoding="utf-8")
    return path, write


# ── the path ─────────────────────────────────────────────────────────

def test_the_reader_asks_the_executor_where_the_file_is():
    """Not "resolves to the same string today" — literally the same source of
    truth, so a change to RUNECLAW_STATE_DIR or to the executor's layout moves
    both together."""
    from bot.core import live_executor
    assert ss._closed_trades_file() == live_executor._CLOSED_TRADES_FILE


def test_neither_place_the_old_code_looked_was_ever_right():
    """Pinned so a future 'defensive' fallback cannot quietly restore them.
    A reader that searches several plausible paths reports whichever it finds
    and never says which — and here it found none of them, forever."""
    resolved = os.path.abspath(ss._closed_trades_file())
    bot_dir = os.path.dirname(os.path.abspath(ss.__file__))
    repo_root = os.path.dirname(os.path.dirname(bot_dir))
    for wrong in (os.path.join(os.path.dirname(bot_dir), "closed_trades.json"),
                  os.path.join(repo_root, "closed_trades.json")):
        assert resolved != os.path.abspath(wrong), (
            f"the reader is back on {wrong}, which nothing writes")


# ── the round trip ───────────────────────────────────────────────────

def test_what_the_executor_writes_is_what_the_reader_reads(record):
    """The anti-drift assertion: real writer, real reader, no fixture between
    them. 3 wins / 2 losses / +$200 — the payload used to publish 0/0/0."""
    path, write = record
    write([closed_trade_row(_pos(v, tid=f"t{i}")) for i, v in
           enumerate([150.0, 120.0, 30.0, -40.0, -60.0])])

    data = ss._fetch_live_exchange_data()
    assert data is not None, "a readable record produced no telemetry at all"
    assert data["total_trades"] == 5
    assert data["net_pnl"] == 200.0, "the executor's pnl_usd was not read"
    assert data["win_rate"] == 60.0
    assert data["closed_record_unreadable"] is False


def test_the_per_trade_rows_carry_the_real_pnl(record):
    path, write = record
    write([closed_trade_row(_pos(42.5, tid="a")),
           closed_trade_row(_pos(None, tid="b"))])
    data = ss._fetch_live_exchange_data()
    pnls = [t["pnl"] for t in data["closed_trades"]]
    assert pnls == [42.5, None], "an unpriced close was published as $0.00"


# ── absence vs measurement ───────────────────────────────────────────

def test_an_unpriceable_record_reports_null_not_zero(record):
    path, write = record
    write([closed_trade_row(_pos(None, tid="a")),
           closed_trade_row(_pos(None, tid="b"))])
    data = ss._fetch_live_exchange_data()
    assert data["total_trades"] == 2
    assert data["net_pnl"] is None, "0.0 here claims a measured break-even"
    assert data["win_rate"] is None, "0.0 here claims both trades lost"


def test_a_partially_priced_record_scores_only_what_it_can_read(record):
    path, write = record
    write([closed_trade_row(_pos(100.0, tid="a")),
           closed_trade_row(_pos(-50.0, tid="b")),
           closed_trade_row(_pos(None, tid="c"))])
    data = ss._fetch_live_exchange_data()
    assert data["total_trades"] == 3, "the count is of closes, not of prices"
    assert data["win_rate"] == 50.0, "the unpriced close was scored as a loss"
    assert data["net_pnl"] == 50.0, "the unpriced close was summed as a zero"


def test_a_corrupt_file_is_flagged_rather_than_read_as_an_empty_book(record):
    path, write = record
    path.write_text("{ this is not json", encoding="utf-8")
    data = ss._fetch_live_exchange_data()
    assert data is not None, "an unreadable record must still be reportable"
    assert data["closed_record_unreadable"] is True
    assert data["net_pnl"] is None
    assert data["win_rate"] is None


def test_a_json_document_of_the_wrong_shape_is_also_unreadable(record):
    """A bare number or a string parses fine and is not a trade list. Without
    the type check it reached len()/iteration and produced nonsense or an
    exception swallowed as an empty book."""
    path, write = record
    path.write_text('"closed_trades"', encoding="utf-8")
    data = ss._fetch_live_exchange_data()
    assert data["closed_record_unreadable"] is True


def test_a_missing_file_is_genuinely_no_trades_yet(record):
    """The one case that IS zero: the executor writes on the first close, so
    no file means nothing has closed. It must not be flagged unreadable —
    a caveat on every fresh install is how a real one gets ignored."""
    path, write = record
    assert not path.exists()
    data = ss._fetch_live_exchange_data()
    assert data is None or data["closed_record_unreadable"] is False


def test_a_measured_flat_book_still_reports_its_zero(record):
    """The other half of the rule: two closes that both broke even is a real
    result and must not be hidden by the fix for the absent case."""
    path, write = record
    write([closed_trade_row(_pos(0.0, tid="a")),
           closed_trade_row(_pos(0.0, tid="b"))])
    data = ss._fetch_live_exchange_data()
    assert data["net_pnl"] == 0.0, "a measured break-even was suppressed"
    assert data["win_rate"] == 0.0, "neither close won — that IS 0%"


# ── the two branches that skip the file-only path ────────────────────

def test_a_venue_switch_does_not_discard_the_realized_record(record, monkeypatch):
    """`/venue` away from Bitget skips the Bitget-keyed balance readout — and
    used to `return result`, the untouched defaults, throwing away the trade
    file it had just read. A venue switch alone published "0 trades, $0.00,
    0%". The record is what THIS bot closed; it does not belong to a venue."""
    path, write = record
    write([closed_trade_row(_pos(80.0, tid="a")),
           closed_trade_row(_pos(-30.0, tid="b"))])
    monkeypatch.setattr("bot.core.venues.get_venue",
                        lambda *a, **k: type("V", (), {"id": "hyperliquid"})())
    data = ss._fetch_live_exchange_data()
    assert data is not None
    assert data["total_trades"] == 2
    assert data["net_pnl"] == 50.0, "the realized record was discarded"
    assert data["win_rate"] == 50.0


def test_the_exchange_success_path_keeps_the_record_tri_state(record, monkeypatch):
    """The branch that adds unrealized P&L to the realized total. Adding it to
    a None realized would print the OPEN book's paper P&L as the account's
    lifetime result — a different quantity wearing the label."""
    import sys
    import types

    path, write = record
    write([closed_trade_row(_pos(None, tid="a")),
           closed_trade_row(_pos(None, tid="b"))])
    monkeypatch.setattr("bot.core.venues.get_venue",
                        lambda *a, **k: type("V", (), {"id": "bitget"})())

    class _Ex:
        def set_sandbox_mode(self, *a, **k): pass
        def fetch_balance(self, *a, **k): return {"USDT": {"total": 884.31}}
        def fetch_positions(self, *a, **k):
            # A live open position with real unrealized P&L: the red herring.
            # It is a genuine number and it is NOT the realized record.
            return [{"contracts": 1.0, "unrealizedPnl": 17.5, "symbol": "BTC/USDT:USDT",
                     "side": "long", "entryPrice": 100.0, "notional": 100.0,
                     "initialMargin": 100.0, "leverage": 1}]

    fake = types.ModuleType("ccxt")
    fake.bitget = lambda *a, **k: _Ex()
    monkeypatch.setitem(sys.modules, "ccxt", fake)

    data = ss._fetch_live_exchange_data()
    assert data["equity"] == 884.31, "the exchange leg did not run"
    assert data["open_count"] == 1
    assert data["net_pnl"] is None, (
        "an unpriceable realized record was published as the open book's "
        "unrealized P&L")
    assert data["win_rate"] is None


# ── the paper branch, same defect one attribute over ──────────────────

def test_the_paper_book_total_is_not_a_constant_zero():
    """`sum(getattr(t, 'net_pnl', 0) for t in history)` over `TradeExecution`,
    which has `pnl` and NO `net_pnl` — so the paper engine card has reported
    $0.00 for every book it has ever shown, beside a win rate computed
    correctly from the same rows."""
    from bot.utils.models import TradeExecution
    from bot.utils.win_rate import pnl_stats, win_stats

    assert "net_pnl" not in TradeExecution.model_fields, (
        "TradeExecution gained net_pnl — revisit the readers")
    assert "pnl" in TradeExecution.model_fields

    history = [TradeExecution(trade_id=f"p{i}", asset="BTCUSDT",
                              direction="LONG", entry_price=100.0,
                              quantity=1.0, stop_loss=90.0, take_profit=120.0,
                              pnl=v)
               for i, v in enumerate([30.0, -10.0, 5.0])]
    assert sum(getattr(t, "net_pnl", 0) for t in history) == 0, (
        "the premise: the old expression is a constant 0")
    assert pnl_stats(history)["total"] == 25.0
    assert win_stats(history)["rate"] == pytest.approx(2 / 3)


def _paper_execution(pnl, i):
    from bot.utils.models import TradeExecution
    return TradeExecution(trade_id=f"p{i}", asset="BTCUSDT", direction="LONG",
                          entry_price=100.0, quantity=1.0, stop_loss=90.0,
                          take_profit=120.0, pnl=pnl)


def _paper_payload(history):
    """Drive the paper branch of the payload builder with a real history."""
    from unittest.mock import MagicMock, patch

    cfg = MagicMock()
    cfg.simulation_mode = True          # stated, never left to MagicMock truthiness
    cfg.live_trading_enabled = False
    cfg.risk.max_open_positions = 5

    engine = MagicMock()
    engine.portfolio._history = history
    engine.portfolio.snapshot.return_value = MagicMock(equity_usd=10_250.0,
                                                       open_positions=1)
    with patch("bot.config.CONFIG", cfg), \
         patch.object(ss, "_build_features_block", return_value={}):
        return ss._build_scan_payload([], engine)["circuit_breaker"]


def test_the_paper_card_publishes_the_book_it_actually_has():
    """End to end through the payload builder: +$25 over three closes used to
    reach the website as $0.00 beside a correctly-computed 66.7% win rate —
    two figures from the same rows, disagreeing."""
    cb = _paper_payload([_paper_execution(v, i)
                         for i, v in enumerate([30.0, -10.0, 5.0])])
    assert cb["total_trades"] == 3
    assert cb["net_pnl"] == 25.0, "the paper book's realized total was a constant 0"
    assert cb["win_rate"] == pytest.approx(66.7, abs=0.05)


def test_an_empty_paper_book_claims_nothing():
    cb = _paper_payload([])
    assert cb["total_trades"] == 0
    assert cb["net_pnl"] is None, "$0.00 over no trades says the book is flat"
    assert cb["win_rate"] is None, "0% over no trades says everything lost"


# ── the branch that sets neither ─────────────────────────────────────

def _live_payload(live_data):
    from unittest.mock import MagicMock, patch

    cfg = MagicMock()
    cfg.simulation_mode = False
    cfg.live_trading_enabled = True
    cfg.risk.max_open_positions = 5
    with patch("bot.config.CONFIG", cfg), \
         patch.object(ss, "_fetch_live_exchange_data", return_value=live_data), \
         patch.object(ss, "_build_features_block", return_value={}):
        return ss._build_scan_payload([], MagicMock())["circuit_breaker"]


def test_live_mode_with_an_unreadable_account_publishes_no_figures():
    """The 2026-07-28 card, exactly. Live mode, the readout fails, the equity
    fallback runs — and it sets equity and positions and NOTHING else. Whatever
    the two statistics were initialised to is what the website prints, so the
    initialisation is the claim. It has to be None."""
    cb = _live_payload(None)
    assert cb["live_unavailable"] is True
    assert cb["net_pnl"] is None, (
        '"+0.00" beside an unavailable account is a fabricated measurement')
    assert cb["win_rate"] is None, '"0.0%" claims every trade lost'


def test_an_unreadable_closed_record_reaches_the_payload():
    """The flag is what lets the card say "could not be read" rather than
    showing bare dashes with no reason, and "0 trades" rather than nothing.
    A flag computed and not forwarded renders zero times."""
    cb = _live_payload({
        "equity": 884.31, "net_pnl": None, "win_rate": None,
        "total_trades": 0, "open_count": 0,
        "open_positions": [], "closed_trades": [],
        "closed_record_unreadable": True,
    })
    assert cb["record_unreadable"] is True
    assert cb["net_pnl"] is None


def test_a_readable_empty_record_is_not_flagged_unreadable():
    cb = _live_payload({
        "equity": 884.31, "net_pnl": None, "win_rate": None,
        "total_trades": 0, "open_count": 0,
        "open_positions": [], "closed_trades": [],
        "closed_record_unreadable": False,
    })
    assert cb["record_unreadable"] is False
