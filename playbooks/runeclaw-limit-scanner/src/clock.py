"""US event times against a schedule that runs on Asia/Shanghai.

The cron timezone is Shanghai. A US macro print is an America/New_York wall
time. Reading that wall time as Shanghai, or the Shanghai hour as New York,
blacks out the wrong part of the day. Conversion goes through the zone, so
daylight saving moves the UTC instant and the Shanghai clock with it.
"""
from datetime import datetime, timezone
from typing import Optional
from zoneinfo import ZoneInfo

NEW_YORK = ZoneInfo("America/New_York")
SHANGHAI = ZoneInfo("Asia/Shanghai")


def us_wall_to_utc(year: int, month: int, day: int, hour: int, minute: int) -> datetime:
    """A US wall time as UTC. The schedule's Shanghai zone is not an input."""
    local = datetime(year, month, day, hour, minute, tzinfo=NEW_YORK)
    return local.astimezone(timezone.utc)


def shanghai_clock(instant: datetime) -> datetime:
    """What a Shanghai cron would display at ``instant``."""
    if instant.tzinfo is None:
        instant = instant.replace(tzinfo=timezone.utc)
    return instant.astimezone(SHANGHAI)


def covers(now: datetime, event_utc: datetime, before_min: float, after_min: float) -> bool:
    """True when ``now`` is inside the window around a UTC event."""
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    if event_utc.tzinfo is None:
        event_utc = event_utc.replace(tzinfo=timezone.utc)
    delta = (now - event_utc).total_seconds() / 60.0
    return -float(before_min) <= delta <= float(after_min)


def _list_state(events, now: datetime, before: float, after: float) -> str:
    """``hit`` inside a window, ``clear`` outside all of them, ``unreadable``
    when a row is not a US wall time. ``events is None`` is clear: earnings
    are optional. A present value that is not a list is not."""
    if events is None:
        return "clear"
    if not isinstance(events, list):
        return "unreadable"
    for event in events:
        if not isinstance(event, dict):
            return "unreadable"
        try:
            event_utc = us_wall_to_utc(
                int(event["year"]), int(event["month"]), int(event["day"]),
                int(event["hour"]), int(event["minute"]))
        except (KeyError, TypeError, ValueError):
            return "unreadable"
        if covers(now, event_utc, before, after):
            return "hit"
    return "clear"


def blackout_reason(cfg: dict, now: Optional[datetime] = None) -> str:
    """Why entries are paused, or "" when they are not.

    Off unless ``event_blackouts.enabled`` is true. On, an unreadable list
    refuses: a blackout that cannot be checked is not a clear window.
    ``events`` are US macro prints and ``earnings`` are US earnings times.
    Each row is ``{year, month, day, hour, minute}`` in America/New_York.
    The schedule clock is Asia/Shanghai; these wall times are not read as
    Shanghai hours. Both are converted to UTC before the comparison.
    """
    block = cfg.get("event_blackouts") if isinstance(cfg, dict) else None
    if not isinstance(block, dict) or block.get("enabled") is not True:
        return ""
    if "events" not in block or not isinstance(block.get("events"), list):
        return "blackout_unreadable"
    try:
        before = float(block.get("before_min", 30))
        after = float(block.get("after_min", 30))
    except (TypeError, ValueError):
        return "blackout_unreadable"
    if before < 0 or after < 0:
        return "blackout_unreadable"
    if now is None:
        now = datetime.now(timezone.utc)
    events = _list_state(block.get("events"), now, before, after)
    if events == "unreadable":
        return "blackout_unreadable"
    if events == "hit":
        return "us_event_blackout"
    earnings = _list_state(block.get("earnings"), now, before, after)
    if earnings == "unreadable":
        return "blackout_unreadable"
    if earnings == "hit":
        return "earnings_blackout"
    return ""
