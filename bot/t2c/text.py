"""The admin chat line. Unread fields say so. No exception text."""
from __future__ import annotations


def _show(value: object) -> str:
    if value is None:
        return "not read"
    return str(value)


def format_signal_text(reading: dict) -> str:
    public = reading["public"]
    price = public["price"]
    backtest = public["backtest"]
    lines = [
        "T2C cap signal",
        "A cap and LTV signal. Not a liquidation price.",
        "flag: " + ("on" if public["flag_on"] else "off"),
        "P: " + _show(price["p"]),
        "T_p95 days: " + _show(price["t_p95_days"]) + " (" + str(price["t_p95_source"]) + ")",
        "r_borrow: " + _show(price["r_borrow"]),
        "y_stake: " + _show(price["y_stake"]) + " (" + str(price["y_stake_source"]) + ")",
        "s: " + _show(price["s"]),
        "backtest: " + str(backtest["verdict"]),
        "widest stETH discount bp: " + _show(backtest["widest_steth_discount_bp"])
        + " on " + _show(backtest["widest_day"]),
        "cap applied: no",
    ]
    return "\n".join(lines)
