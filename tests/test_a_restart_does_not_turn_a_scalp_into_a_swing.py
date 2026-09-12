"""`_save_positions` wrote neither `strategy_type` nor `signal_type`.

`LivePosition` carries both since the strategy-type work, and two exit rules
read the first on OPEN positions every tick:

    live_executor.py:7712  close_threshold = get_time_close_hours(pos_strategy)
                           scalp 2.0h · intraday 4.0h · swing 24h
    live_executor.py:7483  get_trailing_enabled / get_trailing_atr_mult
                           scalp trailing OFF · swing ON

`_load_positions` built every record with the dataclass defaults, so one
restart turned a scalp into a "swing": its no-profit time-stop moved from 2h
to 24h and its trailing rule switched on. And `closed_trade_row` writes
`pos.signal_type`, so every close after a restart was attributed to the
default signal type — the live parity report's "By signal type" buckets read
one restart's worth of history into `momentum_confluence`.

`_load_closed_trades` restored both for CLOSED trades all along (the parity
work added them there); the open-position store never did. Same shape as the
provenance markers in #336: a value that is not persisted is a value for one
process lifetime.

THE RED HERRING, planted below: a record written before these keys existed
carries no strategy to recover, so the defaults are the honest restore for it
— stated, not silently assumed by the constructor.
"""
from __future__ import annotations

import json
import pathlib

from bot.config import CONFIG
from bot.core.live_executor import LiveExecutor, LivePosition, closed_trade_row


def _pos(**kw):
    base = dict(trade_id="t1", symbol="SOL/USDT:USDT", direction="LONG", entry_price=140.0,
                quantity=1.0, cost_usd=14.0, stop_loss=137.0, take_profit=146.0, leverage=10,
                strategy_type="scalp", signal_type="vwap_reversion")
    base.update(kw)
    return LivePosition(**base)


def _restart(tmp_path, pos) -> LivePosition:
    ex = LiveExecutor(state_dir=str(tmp_path))
    ex._positions[pos.trade_id] = pos
    ex._save_positions()
    return LiveExecutor(state_dir=str(tmp_path))._positions[pos.trade_id]


def test_the_strategy_survives_a_restart(tmp_path):
    back = _restart(tmp_path, _pos())
    assert back.strategy_type == "scalp"
    assert back.signal_type == "vwap_reversion"


def test_the_keys_are_on_disk(tmp_path):
    ex = LiveExecutor(state_dir=str(tmp_path))
    ex._positions["t1"] = _pos()
    ex._save_positions()
    row = json.loads(pathlib.Path(ex._positions_file).read_text())["t1"]
    assert row["strategy_type"] == "scalp" and row["signal_type"] == "vwap_reversion"


def test_a_restored_scalp_keeps_its_time_stop(tmp_path):
    """The reading that costs money: the no-profit time-stop threshold the
    engine will apply to the restored position."""
    st = CONFIG.strategy_types
    assert st.get_time_close_hours("scalp") != st.get_time_close_hours("swing"), (
        "the premise moved: scalp and swing share a time-stop, so nothing here can distinguish them")
    back = _restart(tmp_path, _pos())
    assert st.get_time_close_hours(back.strategy_type) == st.get_time_close_hours("scalp")
    assert st.get_trailing_enabled(back.strategy_type) == st.get_trailing_enabled("scalp")


def test_a_restored_position_closes_under_its_own_signal_type(tmp_path):
    back = _restart(tmp_path, _pos())
    assert closed_trade_row(back)["signal_type"] == "vwap_reversion"
    assert closed_trade_row(back)["strategy_type"] == "scalp"


def test_a_record_written_before_the_keys_restores_with_the_stated_defaults(tmp_path):
    """RED HERRING. There is nothing to recover from an old record; the
    defaults are the honest restore and must not raise or invent."""
    ex = LiveExecutor(state_dir=str(tmp_path))
    ex._positions["t1"] = _pos()
    ex._save_positions()
    path = pathlib.Path(ex._positions_file)
    data = json.loads(path.read_text())
    del data["t1"]["strategy_type"]
    del data["t1"]["signal_type"]
    path.write_text(json.dumps(data))
    back = LiveExecutor(state_dir=str(tmp_path))._positions["t1"]
    assert back.strategy_type == "swing"
    assert back.signal_type == "momentum_confluence"


def test_a_pending_limit_keeps_its_strategy_too(tmp_path):
    """pending_fill records are saved by the same writer; a limit that fills
    after a restart must open under the strategy that placed it."""
    back = _restart(tmp_path, _pos(status="pending_fill", order_type="limit",
                                   limit_order_id="o1", strategy_type="intraday"))
    assert back.status == "pending_fill" and back.strategy_type == "intraday"
