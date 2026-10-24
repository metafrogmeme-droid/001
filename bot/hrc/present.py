"""The admin view: utilization and rate points.

Dollars stay out of this view. ``rate_shock_loss_24h`` is added by
``admin_packet`` and dropped by ``card_text``. A missing number is omitted
on the dashboard and printed as unreadable on the card. A measured zero
stays zero.
"""

from __future__ import annotations

import os
from decimal import Decimal
from typing import Optional

from bot.caps.strictest import empty_book
from bot.hrc.collector import _as_raw, history_path, load_history
from bot.hrc.model import (
    DEFAULT_SPREAD_CEIL,
    KINK,
    RAW_PER_TOKEN,
    borrow_apy,
    can_unfreeze,
    cap_hit_pays,
    collateral_for,
    crossover_utilization,
    freeze_reason,
    hour_payoff,
    points_below,
    rate_shock_loss_24h,
    replay_withdrawal,
    trailing_mean,
    utilization_of,
)
from bot.hrc.rfq import quote_for

BORROW_CAP_HUMAN = Decimal(500_000_000)
_SCENARIOS = (("scenario_a", 21_500_000), ("scenario_b", 50_000_000))
_SOURCES = ("precompile", "signed_feed", "lower_of", "none", "info_api")


def load_reading(path: Optional[str] = None, env=None) -> dict:
    """The stored hour. A missing path is unreadable, not an empty book."""
    if path is None:
        path = history_path(env if env is not None else os.environ)
    return load_history(path)


def _dec(value) -> Optional[Decimal]:
    if isinstance(value, Decimal):
        return value
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return Decimal(value)
    if isinstance(value, str):
        try:
            return Decimal(value)
        except Exception:
            return None
    return None


def _fmt(value: Optional[Decimal]) -> Optional[str]:
    if value is None or not isinstance(value, Decimal):
        return None
    return format(value, "f")


def _put(out: dict, key: str, value: Optional[Decimal]) -> None:
    text = _fmt(value)
    if text is not None:
        out[key] = text


def _integrals(reading: dict) -> tuple[Optional[int], Optional[int], Optional[str]]:
    """Supplied and borrowed as ints in one unit, and that unit.

    Human amounts that are already whole tokens stay in human units, so a
    replay withdrawal in whole tokens is the same unit. Anything else is
    floored into raw units. An unknown unit is not a guess.
    """
    supplied = _dec(reading.get("supplied"))
    borrowed = _dec(reading.get("borrowed"))
    unit = reading.get("unit")
    if supplied is None or borrowed is None or supplied <= 0 or borrowed < 0:
        return None, None, None
    if unit == "human" and supplied == supplied.to_integral_value() and borrowed == borrowed.to_integral_value():
        return int(supplied), int(borrowed), "human"
    if unit == "raw" and supplied == supplied.to_integral_value() and borrowed == borrowed.to_integral_value():
        return int(supplied), int(borrowed), "raw"
    if unit == "human":
        return _as_raw(supplied, "human"), _as_raw(borrowed, "human"), "raw"
    return None, None, None


def _human_borrows(reading: dict) -> Optional[Decimal]:
    amount = _dec(reading.get("borrowed"))
    unit = reading.get("unit")
    if amount is None or amount < 0:
        return None
    if unit == "human":
        return amount
    if unit == "raw":
        return amount / Decimal(RAW_PER_TOKEN)
    return None


def dashboard(reading: dict) -> dict:
    """Utilization, the curve, and the two replays. No dollar amount."""
    if not isinstance(reading, dict) or reading.get("readable") is False:
        reason = reading.get("reason") if isinstance(reading, dict) else None
        return {"readable": False, "reason": reason if isinstance(reason, str) else "unreadable"}
    network = reading.get("network")
    synthetic = reading.get("synthetic")
    source = reading.get("source")
    out: dict = {
        "readable": True,
        "network": network if network in ("mainnet", "testnet") else None,
        "synthetic": synthetic if isinstance(synthetic, bool) else None,
        "source": source if source in _SOURCES else "none",
        "kind": "spread",
        "ceiling": format(DEFAULT_SPREAD_CEIL, "f"),
    }
    supplied_d = _dec(reading.get("supplied"))
    borrowed_d = _dec(reading.get("borrowed"))
    stated = reading.get("unit")
    util = None
    if stated == "human" and supplied_d is not None and borrowed_d is not None and supplied_d > 0 and borrowed_d >= 0:
        util = borrowed_d / supplied_d
    elif stated == "raw" and supplied_d is not None and borrowed_d is not None:
        whole = supplied_d == supplied_d.to_integral_value() and borrowed_d == borrowed_d.to_integral_value()
        if whole and supplied_d > 0:
            util = utilization_of(int(supplied_d), int(borrowed_d))
    supplied_i, borrowed_i, unit = _integrals(reading)
    rate = borrow_apy(util)
    _put(out, "utilization", util)
    _put(out, "borrow_apy", rate)
    _put(out, "points_to_kink", points_below(KINK, util))
    funding = _dec(reading.get("funding_apr"))
    cross = crossover_utilization(funding)
    _put(out, "points_to_crossover", points_below(cross, util) if cross is not None else None)
    human = _human_borrows(reading)
    if human is not None:
        _put(out, "borrow_cap_ratio", human / BORROW_CAP_HUMAN)
    if borrowed_i is not None and unit == "raw":
        hit = cap_hit_pays(borrowed_i, int(BORROW_CAP_HUMAN) * RAW_PER_TOKEN)
    elif borrowed_i is not None and unit == "human":
        hit = cap_hit_pays(borrowed_i, int(BORROW_CAP_HUMAN))
    else:
        hit = None
    if hit is not None:
        out["cap_hit"] = hit
    rates = _rate_window(reading.get("rates"))
    mean = trailing_mean(rates) if rates is not None else None
    _put(out, "trailing_mean", mean)
    strike = _dec(reading.get("strike"))
    out.update(_series_terms(reading, mean, strike))
    quote = quote_for(empty_book(), "usdc", mean, strike, 24, None, None, None)
    _put(out, "premium", quote.get("premium") if isinstance(quote.get("premium"), Decimal) else None)
    coll = quote.get("collateral_fraction")
    _put(out, "collateral_fraction", coll if isinstance(coll, Decimal) else None)
    out["replays"] = _replays(supplied_i, borrowed_i, unit)
    return out


def _series_terms(reading: dict, mean: Optional[Decimal], strike: Optional[Decimal]) -> dict:
    """Payoff, collateral, and freeze, when the reading actually carries them.

    A missing notional, hour, or freeze window is left out. It is not stored
    as a payoff of zero or as a freeze that did not happen.
    """
    out: dict = {}
    notional = reading.get("notional")
    hours = reading.get("hours")
    pay = hour_payoff(notional if type(notional) is int else None, mean, strike)
    if type(pay) is int:
        out["hour_payoff_tokens"] = str(pay)
    coll = collateral_for(
        notional if type(notional) is int else None,
        strike,
        hours if type(hours) is int else None,
    )
    if type(coll) is int:
        out["collateral_tokens"] = str(coll)
    previous = reading.get("previous_hour")
    hour = reading.get("hour")
    formula_ok = reading.get("formula_ok")
    model_ok = reading.get("model_ok")
    reason = freeze_reason(
        previous if type(previous) is int else None,
        hour if type(hour) is int else None,
        formula_ok if isinstance(formula_ok, bool) else None,
        model_ok if isinstance(model_ok, bool) else None,
    )
    if isinstance(reason, str):
        out["freeze"] = reason
    frozen_at = reading.get("frozen_at")
    now = reading.get("now_hour")
    good = reading.get("good_hours")
    if type(frozen_at) is int and type(now) is int and isinstance(good, (list, tuple)):
        out["can_unfreeze"] = can_unfreeze(frozen_at, now, good, reading.get("guardian") is True)
    return out


def _rate_window(raw) -> Optional[list]:
    if not isinstance(raw, list):
        return None
    window: list[Optional[Decimal]] = []
    for item in raw:
        if item is None:
            window.append(None)
            continue
        parsed = _dec(item)
        window.append(parsed)
    return window


def _replays(supplied: Optional[int], borrowed: Optional[int], unit: Optional[str]) -> dict:
    out = {}
    for name, human_size in _SCENARIOS:
        if supplied is None or borrowed is None or unit not in ("human", "raw"):
            out[name] = {"readable": False, "reason": "unreadable"}
            continue
        size = human_size if unit == "human" else human_size * RAW_PER_TOKEN
        row = replay_withdrawal(supplied, borrowed, size)
        if row.get("readable") is not True:
            out[name] = {"readable": False, "reason": row.get("reason") or "unreadable"}
            continue
        shown: dict[str, object] = {}
        text_u = _fmt(row.get("utilization"))
        text_r = _fmt(row.get("borrow_apy"))
        if text_u is not None:
            shown["utilization"] = text_u
        if text_r is not None:
            shown["borrow_apy"] = text_r
        shown["readable"] = True
        out[name] = shown
    return out


def admin_packet(reading: dict) -> dict:
    """The dashboard plus the 24h shock, which is a dollar amount.

    The shock is None when borrows were not read, or when the oracle is not
    the 1 the USDC reserve printed. It is not zero in either case.
    """
    view = dashboard(reading)
    borrows = _human_borrows(reading) if isinstance(reading, dict) else None
    oracle = _dec(reading.get("oracle")) if isinstance(reading, dict) else None
    if borrows is None or oracle is None or oracle != Decimal(1):
        shock = None
    else:
        shock = rate_shock_loss_24h(borrows)
    view["rate_shock_loss_24h"] = _fmt(shock)
    return view


def _line(label: str, value) -> str:
    if value is None or value == "":
        return f"{label}: unreadable"
    return f"{label}: {value}"


def card_text(packet: dict) -> str:
    """The telegram card. The shock field is not read."""
    lines = []
    if isinstance(packet, dict) and packet.get("preview") is True:
        lines.append("Admin preview. Not public.")
    if not isinstance(packet, dict) or packet.get("readable") is False:
        reason = packet.get("reason") if isinstance(packet, dict) else None
        if isinstance(reason, str) and reason:
            lines.append(f"Unreadable ({reason}).")
        else:
            lines.append("Unreadable.")
        return "\n".join(lines)
    if packet.get("synthetic") is True:
        lines.append("Testnet readings are synthetic.")
    elif packet.get("synthetic") is False and packet.get("network") == "mainnet":
        lines.append("Mainnet read.")
    lines.append(_line("Network", packet.get("network")))
    lines.append(_line("Source", packet.get("source")))
    lines.append(_line("Utilization", packet.get("utilization")))
    lines.append(_line("Borrow APY", packet.get("borrow_apy")))
    lines.append(_line("Points below the kink", packet.get("points_to_kink")))
    lines.append(_line("Points below the funding crossover", packet.get("points_to_crossover")))
    lines.append(_line("Borrow cap used", packet.get("borrow_cap_ratio")))
    lines.append(_line("Trailing mean", packet.get("trailing_mean")))
    lines.append(_line("Series", "cap spread"))
    lines.append(_line("Ceiling", packet.get("ceiling")))
    lines.append(_line("Premium fraction", packet.get("premium")))
    if packet.get("cap_hit") is True:
        lines.append("Cap-hit: yes")
    elif packet.get("cap_hit") is False:
        lines.append("Cap-hit: no")
    else:
        lines.append("Cap-hit: unreadable")
    replays = packet.get("replays")
    if isinstance(replays, dict):
        for name in ("scenario_a", "scenario_b"):
            row = replays.get(name)
            if not isinstance(row, dict) or row.get("readable") is not True:
                lines.append(f"{name}: unreadable")
                continue
            lines.append(
                f"{name}: utilization {row.get('utilization')} borrow {row.get('borrow_apy')}"
            )
    return "\n".join(lines)
