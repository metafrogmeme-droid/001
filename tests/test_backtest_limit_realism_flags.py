"""The runner can ask for the resting-limit realism the engine already models."""
from __future__ import annotations

from bot.backtest.models import BacktestConfig
from bot.backtest.runner import _limit_realism_kwargs, build_parser


def _args(*argv):
    return build_parser().parse_args(list(argv))


def test_no_flag_means_no_kwargs_and_the_historical_touch_model():
    assert _limit_realism_kwargs(_args()) == {}
    cfg = BacktestConfig(symbol="BTC/USDT:USDT", **_limit_realism_kwargs(_args()))
    assert cfg.limit_through_bp is None and cfg.limit_queue_p is None
    assert cfg.limit_reclaim is False and cfg.limit_maker_entry is False


def test_every_flag_reaches_the_config_field_it_names():
    kw = _limit_realism_kwargs(_args(
        "--limit-through-bp", "5", "--limit-queue-p", "0.5",
        "--limit-queue-sure-bp", "10", "--limit-reclaim", "--limit-maker-entry"))
    assert kw == {"limit_through_bp": 5.0, "limit_queue_p": 0.5,
                  "limit_queue_sure_bp": 10.0, "limit_reclaim": True,
                  "limit_maker_entry": True}
    cfg = BacktestConfig(symbol="BTC/USDT:USDT", **kw)
    assert (cfg.limit_through_bp, cfg.limit_queue_p, cfg.limit_queue_sure_bp) == (5.0, 0.5, 10.0)
    assert cfg.limit_reclaim is True and cfg.limit_maker_entry is True


def test_a_zero_value_is_a_setting_not_an_absent_flag():
    kw = _limit_realism_kwargs(_args("--limit-through-bp", "0", "--limit-queue-p", "0"))
    assert kw == {"limit_through_bp": 0.0, "limit_queue_p": 0.0}
