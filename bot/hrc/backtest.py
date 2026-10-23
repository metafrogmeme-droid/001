"""Replay hourly borrow rates through the same accrual the market uses.

The public tape is the HypeBasis hourly CSV. Our own collector is a single
mainnet precompile read: it cross-checks the curve, and it is not mixed
into the hourly distribution. Payouts are fractions of notional. A missing
hour is left out of the mean. It is not accrued as a 0% rate.

The manipulation curve is the supply that has to be withdrawn so the
curve's rate stays at a strike for the six-hour window, set next to a
scenario open-interest cap. It is a token amount. It is not interest and
it is not a dollar figure.
"""

from __future__ import annotations

import csv
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Optional, Sequence

from bot.hrc.index import accept_sample, decode_precompile
from bot.hrc.model import (
    GAP_LIMIT_HOURS,
    KIND_CAP,
    KIND_DIGITAL,
    KIND_SPREAD,
    PER_USER_BORROW_CAP,
    TRAILING_HOURS,
    accrual_per_unit,
    borrow_apy,
    default_carry_strike,
    quoted_rate_matches,
    supply_to_hold,
    utilization_of,
)

HYPEBASIS_URL = "https://hypebasis.io/native-reserves/data.csv"
# A scenario cap for the curve, not a live open-interest reading.
SCENARIO_OI_CAP = Decimal(1_000_000)
PAYOUT_STRIKES = (
    Decimal("0.04"),
    Decimal("0.05"),
    Decimal("0.10"),
    Decimal("0.20"),
    Decimal("0.40"),
)
MANIP_STRIKES = PAYOUT_STRIKES + (Decimal("0.525"), Decimal("1"), Decimal("1.01"))


def _dec(value: object) -> Optional[Decimal]:
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


def _hour(stamp: str) -> Optional[datetime]:
    if not isinstance(stamp, str) or not stamp:
        return None
    try:
        return datetime.fromisoformat(stamp.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None


def load_hypebasis_usdc(path: Path) -> dict:
    """USDC rows from the HypeBasis CSV, one per capture.

    The rate kept is the curve on supplied and borrowed. The published
    borrow rate is a cross-check and must sit under 2 bp. A row that
    fails that check is an unreadable hour. Its rate is None. These
    amounts are human tokens; borrowed / supplied is the same ratio as
    the raw 1e8 words. The dollar columns in the file are not read.
    """
    rows: list[dict] = []
    with path.open(encoding="utf-8", newline="") as handle:
        for raw in csv.DictReader(handle):
            if raw.get("asset") != "USDC":
                continue
            when = _hour(raw.get("captured_at_utc") or "")
            supplied = _dec(raw.get("total_supplied"))
            borrowed = _dec(raw.get("total_borrowed"))
            quoted = _dec(raw.get("borrow_yearly_rate_decimal"))
            supply_rate = _dec(raw.get("supply_yearly_rate_decimal"))
            if when is None or supplied is None or borrowed is None or supplied <= 0 or borrowed < 0:
                rows.append({"ts": None, "rate": None, "reason": "unreadable"})
                continue
            util = borrowed / supplied
            rate = borrow_apy(util)
            if not quoted_rate_matches(util, quoted):
                rows.append({
                    "ts": when, "rate": None, "utilization": util,
                    "supplied": supplied, "borrowed": borrowed, "reason": "formula",
                })
                continue
            rows.append({
                "ts": when,
                "rate": rate,
                "utilization": util,
                "supplied": supplied,
                "borrowed": borrowed,
                "supply_rate": supply_rate,
                "quoted": quoted,
                "reason": "ok",
            })
    rows.sort(key=lambda item: item["ts"] or datetime.min.replace(tzinfo=timezone.utc))
    return {"rows": rows, "source": HYPEBASIS_URL}


def hour_index(rows: list[dict]) -> list[tuple[int, Optional[Decimal]]]:
    """(hour id, rate) from capture timestamps. The first capture is hour 0.

    A capture with no timestamp is dropped. It is not hour 0 of a second tape.
    """
    stamps = [row["ts"] for row in rows if isinstance(row.get("ts"), datetime)]
    if not stamps:
        return []
    origin = stamps[0]
    out: list[tuple[int, Optional[Decimal]]] = []
    for row in rows:
        when = row.get("ts")
        if not isinstance(when, datetime):
            continue
        hour = int((when - origin).total_seconds() // 3600)
        rate = row.get("rate") if isinstance(row.get("rate"), Decimal) else None
        out.append((hour, rate))
    dedup: dict[int, Optional[Decimal]] = {}
    order: list[int] = []
    for hour, rate in out:
        if hour not in dedup:
            order.append(hour)
        dedup[hour] = rate
    return [(hour, dedup[hour]) for hour in order]


def replay(
    samples: Sequence[tuple[int, Optional[Decimal]]],
    *,
    kind: int,
    strike: Decimal,
    ceil: Optional[Decimal] = None,
    digital: Optional[Decimal] = None,
) -> dict:
    """Accrue ``(hour, rate)`` pairs. A jump of more than two hours freezes.

    A None rate pays nothing and is not a 0% observation inside the mean.
    Before six readable hours exist, the payoff is 0 because the mean is
    unreadable. That 0 is the payoff rule, and the reason says so.
    """
    by_hour = {hour: rate for hour, rate in samples}
    pays: list[dict] = []
    frozen_at: Optional[int] = None
    last_good: Optional[int] = None
    previous: Optional[int] = None
    for hour, rate in samples:
        if previous is not None and hour - previous > GAP_LIMIT_HOURS:
            frozen_at = hour
            break
        previous = hour
        if rate is None:
            pays.append({"hour": hour, "pay": None, "reason": "no_rate"})
            continue
        window = [by_hour.get(hour - (TRAILING_HOURS - 1) + offset) for offset in range(TRAILING_HOURS)]
        if any(item is None for item in window):
            pays.append({"hour": hour, "pay": Decimal(0), "reason": "mean_unreadable"})
            continue
        assert all(isinstance(item, Decimal) for item in window)
        mean = sum((item for item in window if isinstance(item, Decimal)), Decimal(0)) / Decimal(TRAILING_HOURS)
        per = accrual_per_unit(kind, mean, strike, ceil, digital)
        if per is None:
            pays.append({"hour": hour, "pay": None, "reason": "unreadable"})
            continue
        pays.append({"hour": hour, "pay": per, "reason": "accrued", "mean": mean})
        last_good = hour
    return {"pays": pays, "frozen_at": frozen_at, "last_good_index": last_good}


def _quantile(values: list[Decimal], p: Decimal) -> Optional[Decimal]:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    idx = (Decimal(len(ordered) - 1) * p)
    lo = int(idx)
    hi = min(lo + 1, len(ordered) - 1)
    frac = idx - Decimal(lo)
    return ordered[lo] + (ordered[hi] - ordered[lo]) * frac


def _fmt(value: Optional[Decimal]) -> Optional[str]:
    if value is None:
        return None
    return format(value, "f")


def summarize(replayed: dict) -> dict:
    """Payout distribution for one strike. Unreadable hours stay out of it."""
    accrued = [
        item["pay"] for item in replayed["pays"]
        if item.get("reason") == "accrued" and isinstance(item.get("pay"), Decimal)
    ]
    paying = [item for item in accrued if item > 0]
    total = sum(accrued, Decimal(0))
    return {
        "accrued_hours": len(accrued),
        "paying_hours": len(paying),
        "mean_unreadable_hours": sum(1 for item in replayed["pays"] if item.get("reason") == "mean_unreadable"),
        "no_rate_hours": sum(1 for item in replayed["pays"] if item.get("reason") == "no_rate"),
        "total_per_unit": _fmt(total),
        "p50": _fmt(_quantile(accrued, Decimal("0.50"))),
        "p95": _fmt(_quantile(accrued, Decimal("0.95"))),
        "frozen_at": replayed["frozen_at"],
    }


def payout_table(samples: list[tuple[int, Optional[Decimal]]]) -> dict:
    """Per-strike distributions for the cap, the spread, and one digital."""
    spread = {}
    cap = {}
    for strike in PAYOUT_STRIKES:
        key = format(strike, "f")
        spread[key] = summarize(replay(samples, kind=KIND_SPREAD, strike=strike, ceil=Decimal("0.40")))
        cap[key] = summarize(replay(samples, kind=KIND_CAP, strike=strike))
    digital = {}
    for strike in (Decimal("0.04"), Decimal("0.05"), default_carry_strike()):
        digital[format(strike, "f")] = summarize(
            replay(samples, kind=KIND_DIGITAL, strike=strike, digital=Decimal("0.01"))
        )
    return {"spread_ceiling_0.40": spread, "cap_ceiling_1": cap, "digital_d_0.01": digital}


def manipulation_curve(supplied: Decimal, borrowed: Decimal, oi_cap: Decimal = SCENARIO_OI_CAP) -> list[dict]:
    """Withdrawal versus the scenario open-interest cap, one row per strike."""
    rows = []
    for strike in MANIP_STRIKES:
        held = supply_to_hold(supplied, borrowed, strike)
        withdrawal = held["withdrawal"]
        ratio = None
        per_user = None
        if isinstance(withdrawal, Decimal) and oi_cap > 0:
            ratio = withdrawal / oi_cap
        if isinstance(withdrawal, Decimal) and PER_USER_BORROW_CAP > 0:
            per_user = withdrawal / PER_USER_BORROW_CAP
        rows.append({
            "strike": format(strike, "f"),
            "reachable": held["reachable"],
            "target_utilization": _fmt(held["target_utilization"]),
            "withdrawal_tokens": _fmt(withdrawal),
            "oi_cap_tokens": format(oi_cap, "f"),
            "withdrawal_over_oi_cap": _fmt(ratio),
            "per_user_borrow_cap_tokens": format(PER_USER_BORROW_CAP, "f"),
            "withdrawal_over_per_user_cap": _fmt(per_user),
            "hours": 6,
        })
    return rows


def collector_cross_check(fixture: dict) -> dict:
    """The frozen mainnet precompile word. One hour, not a distribution."""
    words = fixture.get("precompile", {}).get("words") if isinstance(fixture, dict) else None
    if not isinstance(words, list) or len(words) != 8:
        return {"readable": False, "reason": "unreadable"}
    try:
        raw = b"".join(int(word).to_bytes(32, "big") for word in words)
    except (TypeError, ValueError, OverflowError):
        return {"readable": False, "reason": "unreadable"}
    accepted = accept_sample(decode_precompile(raw))
    if accepted is None:
        return {"readable": False, "reason": "rejected"}
    util = utilization_of(accepted["supplied"], accepted["borrowed"])
    return {
        "readable": True,
        "observed": fixture.get("observed"),
        "network": fixture.get("network"),
        "synthetic": fixture.get("synthetic"),
        "formula_rate": _fmt(accepted.get("formula_rate")),
        "utilization": _fmt(util),
        "note": "One mainnet precompile read. It is not an hourly path, so it is not in the payout distribution.",
    }


def build_report(csv_path: Path, collector_fixture: dict) -> dict:
    """The offline report. Rates and token amounts. No dollar payout."""
    loaded = load_hypebasis_usdc(csv_path)
    samples = hour_index(loaded["rows"])
    readable = [row for row in loaded["rows"] if isinstance(row.get("rate"), Decimal)]
    last = readable[-1] if readable else None
    curve = []
    if last is not None:
        curve = manipulation_curve(last["supplied"], last["borrowed"])
    stamps = [row["ts"] for row in loaded["rows"] if isinstance(row.get("ts"), datetime)]
    return {
        "source": HYPEBASIS_URL,
        "fetched": "2026-10-23",
        "asset": "USDC",
        "captures": len(loaded["rows"]),
        "tape_hours": len(samples),
        "readable_hours": len(readable),
        "first": stamps[0].strftime("%Y-%m-%dT%H:%M:%SZ") if stamps else None,
        "last": stamps[-1].strftime("%Y-%m-%dT%H:%M:%SZ") if stamps else None,
        "formula": "0.05 + 4.75 * max(0, u - 0.8)",
        "unit": "fraction of notional",
        "payouts": payout_table(samples),
        "manipulation": {
            "definition": (
                "Tokens of supply withdrawn so the borrow rate reaches the strike, "
                "held for 6 hours, divided by a scenario open-interest cap of 1000000 tokens "
                "and by the per-user borrow cap of 50000000 USDC tokens. "
                "A withdrawal of 0 means utilization is already there. "
                "An unreachable strike has no withdrawal, which is not zero."
            ),
            "per_user_borrow_cap_tokens": format(PER_USER_BORROW_CAP, "f"),
            "as_of": last["ts"].strftime("%Y-%m-%dT%H:%M:%SZ") if last else None,
            "supplied_tokens": _fmt(last["supplied"]) if last else None,
            "borrowed_tokens": _fmt(last["borrowed"]) if last else None,
            "utilization": _fmt(last["utilization"]) if last else None,
            "strikes": curve,
        },
        "collector": collector_cross_check(collector_fixture),
    }
