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
        "recorded p50 / p95 days: "
        + _show((public.get("duration_observed") or {}).get("p50_days"))
        + " / "
        + _show((public.get("duration_observed") or {}).get("p95_days")),
        "r_borrow: " + _show(price["r_borrow"]),
        "y_stake: " + _show(price["y_stake"]) + " (" + str(price["y_stake_source"]) + ")",
        "carry: " + _show(price.get("carry")) + " (" + str(price.get("carry_source")) + ")",
        "s: " + _show(price["s"]),
        "recorded carry: " + _show((public.get("carry_recorded") or {}).get("carry")),
        "backtest: " + str(backtest["verdict"]),
        "widest stETH discount bp: " + _show(backtest["widest_steth_discount_bp"])
        + " on " + _show(backtest["widest_day"]),
        "cap applied: no",
        "pool deployed: no",
        "server signs: no",
        "deposit cap: " + str(public["pool"]["deposit_cap"]),
        "mainnet pool: " + str(public["pool"]["mainnet_pool"]),
    ]
    return "\n".join(lines)
