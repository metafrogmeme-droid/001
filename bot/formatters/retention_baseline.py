"""Day-7 and day-30 retention of the users on a roster.

Plan item F8. The plan says to baseline D7 and D30, segmented by role and
signup date, and does not name a first-confirm cohort. The user store's
signup stamp is ``created_at`` (written by ``UserStore.register`` and the
other creators). There is no first-confirm field. A return is ``last_seen``
on or after that day: the latest activity ``UserStore.touch`` and
``register`` write, not a visit log.

A user whose signup is not yet that many days ago is not in the cohort. A
signup or activity time that cannot be read is not in it either. Neither is
0%. A measured zero — every eligible stamp readable, and nobody back on or
after the day — stays zero.

This module places nothing, stages nothing, and calls no executor.
"""

from __future__ import annotations

import html
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Iterable, Literal

from bot.compat import UTC

#: The two horizons the plan names. Day 7, then day 30.
HORIZONS = (7, 30)

_UNRECORDED = ""
_Status = Literal["measured", "unmeasured"]


@dataclass(frozen=True)
class HorizonReading:
    """One horizon over one cohort.

    ``retained`` and ``eligible`` are ``None`` when the horizon is
    unmeasured. A measured zero keeps ``retained == 0`` and a positive
    ``eligible``. ``not_yet_due`` and ``unreadable`` are people left out of
    that rate, never folded into it.
    """

    days: int
    status: _Status
    retained: int | None
    eligible: int | None
    not_yet_due: int
    unreadable: int
    signup_from: str | None
    signup_through: str | None


@dataclass(frozen=True)
class RetentionBaseline:
    """The roster's two horizons, and the same reading per role."""

    horizons: tuple[HorizonReading, ...]
    by_role: tuple[tuple[str, tuple[HorizonReading, ...]], ...]


def _stamp(value: object) -> datetime | None:
    """An aware UTC time, or ``None`` when the field cannot be read.

    The store writes ``datetime.now(UTC).isoformat()``. A naive value is
    that same clock with the offset omitted, not a second timezone. Anything
    else — absent, blank, not a string, not a time — is unreadable.
    """
    if not isinstance(value, str) or value == "":
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed


def _created(record: object) -> datetime | None:
    if not isinstance(record, dict):
        return None
    return _stamp(record.get("created_at"))


def _seen(record: object) -> datetime | None:
    if not isinstance(record, dict):
        return None
    return _stamp(record.get("last_seen"))


def _role(record: object) -> str:
    """The role on the record, or ``""`` when it was not recorded.

    Absent is not a role. ``register`` writes one; a record without one is
    not paper, trader, or pending.
    """
    if not isinstance(record, dict):
        return _UNRECORDED
    role = record.get("role")
    if isinstance(role, str) and role != "":
        return role
    return _UNRECORDED


def _day(stamp: datetime) -> str:
    return stamp.astimezone(UTC).date().isoformat()


def _reading(records: list[object], now: datetime, days: int) -> HorizonReading:
    horizon = timedelta(days=days)
    retained = 0
    eligible = 0
    not_yet_due = 0
    unreadable = 0
    first: datetime | None = None
    last: datetime | None = None
    for record in records:
        created = _created(record)
        if created is None:
            unreadable += 1
            continue
        if now < created + horizon:
            not_yet_due += 1
            continue
        seen = _seen(record)
        if seen is None:
            unreadable += 1
            continue
        eligible += 1
        if seen >= created + horizon:
            retained += 1
        if first is None or created < first:
            first = created
        if last is None or created > last:
            last = created
    if eligible == 0:
        return HorizonReading(
            days=days,
            status="unmeasured",
            retained=None,
            eligible=None,
            not_yet_due=not_yet_due,
            unreadable=unreadable,
            signup_from=None,
            signup_through=None,
        )
    if first is None or last is None:
        raise RuntimeError("an eligible cohort has no signup time")
    return HorizonReading(
        days=days,
        status="measured",
        retained=retained,
        eligible=eligible,
        not_yet_due=not_yet_due,
        unreadable=unreadable,
        signup_from=_day(first),
        signup_through=_day(last),
    )


def retention_baseline(users: Iterable[object], now: datetime) -> RetentionBaseline:
    """D7 and D30 for ``users`` as of ``now``.

    ``now`` is timezone-aware. The iterable is the roster already read —
    this function does not open the store, and it does not place an order.
    """
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    records = list(users)
    grouped: dict[str, list[object]] = {}
    for record in records:
        grouped.setdefault(_role(record), []).append(record)
    horizons = tuple(_reading(records, now, days) for days in HORIZONS)
    by_role = tuple((role, tuple(_reading(grouped[role], now, days) for days in HORIZONS)) for role in sorted(grouped))
    return RetentionBaseline(horizons=horizons, by_role=by_role)


def _percent(retained: int, eligible: int) -> str:
    """The ratio as a percent, truncated to a tenth.

    ``eligible`` is a positive count. A measured zero is ``0%``. Truncation
    rather than rounding, and the count is printed beside it, so the percent
    is not a second, sharper measurement.
    """
    tenths = retained * 1000 // eligible
    whole, frac = divmod(tenths, 10)
    if frac == 0:
        return f"{whole}%"
    return f"{whole}.{frac}%"


def _left_out(reading: HorizonReading) -> str:
    parts: list[str] = []
    pending = reading.not_yet_due
    if pending == 1:
        parts.append(f"1 signup is not yet at day {reading.days}")
    elif pending > 1:
        parts.append(f"{pending} signups are not yet at day {reading.days}")
    unread = reading.unreadable
    if unread == 1:
        parts.append("1 time could not be read")
    elif unread > 1:
        parts.append(f"{unread} times could not be read")
    if not parts:
        return ""
    return "Left out: " + "; ".join(parts) + "."


def _horizon_sentence(reading: HorizonReading) -> str:
    label = f"Day {reading.days}"
    if reading.status == "measured":
        retained = reading.retained
        eligible = reading.eligible
        if retained is None or eligible is None or eligible <= 0:
            raise ValueError("a measured horizon has a positive eligible count")
        if reading.signup_from is None or reading.signup_through is None:
            raise ValueError("a measured horizon names its signup span")
        span = reading.signup_from
        if reading.signup_through != reading.signup_from:
            span = f"{reading.signup_from} through {reading.signup_through}"
        text = f"{label}: {retained} of {eligible} returned ({_percent(retained, eligible)}). Signups {span}."
        extra = _left_out(reading)
        if extra:
            return f"{text} {extra}"
        return text
    if reading.status != "unmeasured":
        raise ValueError(f"unknown retention status {reading.status!r}")
    if reading.not_yet_due == 0 and reading.unreadable == 0:
        return f"{label}: unmeasured. No signups on this list."
    extra = _left_out(reading)
    return f"{label}: unmeasured. {extra}"


def _role_label(role: str) -> str:
    if role == _UNRECORDED:
        return "role unrecorded"
    return html.escape(role, quote=False)


def render_retention_baseline(baseline: RetentionBaseline) -> str:
    """The roster card's retention block. Counts and percents only.

    An unmeasured horizon says unmeasured. A measured zero says 0%. This
    does not place an order.
    """
    lines = [
        "<b>Retention</b>",
        "Of the users on this list. Signup is when the account was created. "
        "A day counts a user only once that signup is at least that many days "
        "ago, and counts them returned when their latest activity is on or "
        "after that day. A user who has not reached the day, or whose time "
        "could not be read, is left out of the rate. "
        "This does not place an order.",
        "",
    ]
    for reading in baseline.horizons:
        lines.append(_horizon_sentence(reading))
    if baseline.by_role:
        lines.append("")
        lines.append("By role")
        for role, readings in baseline.by_role:
            label = _role_label(role)
            for reading in readings:
                lines.append(f"{label}, {_horizon_sentence(reading)}")
    return "\n".join(lines)
