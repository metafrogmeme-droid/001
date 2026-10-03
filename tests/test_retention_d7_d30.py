"""D7 and D30 retention is a measurement of signup, not a verdict.

The plan baselines the two rates and does not define a first-confirm
cohort. Signup is the account's created time. A return is latest activity
on or after that day. Four arms, each its own fact: someone old enough who
came back, someone old enough who did not, someone not yet at the day, and
a time that cannot be read. The last two are left out. A measured zero
stays zero. /users shows the reading and does not place an order.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from bot.compat import UTC
from bot.formatters.retention_baseline import (
    render_retention_baseline,
    retention_baseline,
)
from tests.test_telegram_commands import _last_reply_text, _make_handler, _make_update

NOW = datetime(2026, 10, 3, 12, 0, tzinfo=UTC)


def _at(**delta: float) -> str:
    return (NOW + timedelta(**delta)).isoformat()


def _user(role: str, created: str, seen: object = None) -> dict:
    row: dict = {"role": role, "created_at": created}
    if seen is not None:
        row["last_seen"] = seen
    return row


def _lines(text: str) -> list[str]:
    return text.splitlines()


def _horizon(text: str, days: int) -> str:
    prefix = f"Day {days}:"
    for line in _lines(text):
        if line.startswith(prefix):
            return line
    raise AssertionError(f"no {prefix} line in {text}")


def test_a_return_on_or_after_day_7_is_retained_and_a_miss_is_not():
    """Old enough and back, versus old enough and not back.

    Seen 20 days after a signup 40 days ago: back for day 7, not for day 30.
    """
    back = _user("paper", _at(days=-40), _at(days=-20))
    quiet = _user("trader", _at(days=-45), _at(days=-45))
    reading = retention_baseline([back, quiet], NOW)
    day7, day30 = reading.horizons
    assert day7.status == "measured"
    assert day7.retained == 1 and day7.eligible == 2
    assert day30.status == "measured"
    assert day30.retained == 0 and day30.eligible == 2
    card = render_retention_baseline(reading)
    assert _horizon(card, 7) == (
        f"Day 7: 1 of 2 returned (50%). Signups {_at(days=-45)[:10]} through {_at(days=-40)[:10]}."
    )
    assert _horizon(card, 30).startswith("Day 30: 0 of 2 returned (0%).")
    assert "unmeasured" not in _horizon(card, 7)
    assert "unmeasured" not in _horizon(card, 30)


def test_a_user_not_yet_at_the_horizon_is_not_counted_as_lost():
    back = _user("paper", _at(days=-10), _at(days=-1))
    recent = _user("paper", _at(days=-2), _at(days=-1))
    reading = retention_baseline([back, recent], NOW)
    day7, day30 = reading.horizons
    assert day7.retained == 1 and day7.eligible == 1
    assert day7.not_yet_due == 1
    assert day30.status == "unmeasured" and day30.eligible is None
    assert day30.not_yet_due == 2 and day30.retained is None
    card = render_retention_baseline(reading)
    assert _horizon(card, 7).startswith("Day 7: 1 of 1 returned (100%).")
    assert "1 signup is not yet at day 7" in _horizon(card, 7)
    assert "(0%)" not in _horizon(card, 7)
    assert _horizon(card, 30).startswith("Day 30: unmeasured.")
    assert "2 signups are not yet at day 30" in _horizon(card, 30)
    assert "0 of" not in _horizon(card, 30)
    assert "(0%)" not in _horizon(card, 30)


def test_an_unreadable_time_is_not_counted_as_zero():
    # Seen 20 days after signup: back for day 7, not for day 30.
    back = _user("paper", _at(days=-40), _at(days=-20))
    bad_created = {"role": "viewer", "created_at": "not-a-time", "last_seen": _at(days=-1)}
    missing_seen = _user("trader", _at(days=-40))
    blank_seen = _user("trader", _at(days=-40), "")
    not_a_record = "6307156912"
    epoch = {"role": "paper", "created_at": 1_700_000_000, "last_seen": _at(days=-1)}
    rows = [back, bad_created, missing_seen, blank_seen, not_a_record, epoch]
    reading = retention_baseline(rows, NOW)
    day7, day30 = reading.horizons
    assert day7.retained == 1 and day7.eligible == 1
    assert day7.unreadable == 5
    assert day30.retained == 0 and day30.eligible == 1
    assert day30.unreadable == 5
    card = render_retention_baseline(reading)
    assert "1 of 1 returned (100%)" in _horizon(card, 7)
    assert "5 times could not be read" in _horizon(card, 7)
    assert "(0%)" not in _horizon(card, 7)
    assert _horizon(card, 30).startswith("Day 30: 0 of 1 returned (0%).")
    assert "5 times could not be read" in _horizon(card, 30)


def test_an_empty_roster_is_unmeasured_and_a_measured_zero_stays_zero():
    empty = render_retention_baseline(retention_baseline([], NOW))
    assert _horizon(empty, 7) == "Day 7: unmeasured. No signups on this list."
    assert _horizon(empty, 30) == "Day 30: unmeasured. No signups on this list."
    assert "0%" not in empty
    assert "0 of" not in empty
    assert "By role" not in empty

    quiet = [
        _user("paper", _at(days=-40), _at(days=-40)),
        _user("paper", _at(days=-50), _at(days=-49)),
    ]
    card = render_retention_baseline(retention_baseline(quiet, NOW))
    assert _horizon(card, 7).startswith("Day 7: 0 of 2 returned (0%).")
    assert _horizon(card, 30).startswith("Day 30: 0 of 2 returned (0%).")
    assert "unmeasured" not in _horizon(card, 7)
    assert "unmeasured" not in _horizon(card, 30)


def test_the_boundary_is_the_day_itself_and_a_naive_stamp_is_utc():
    """Seen at the horizon counts. Seen a second earlier does not.

    A signup one second short of the horizon is not in the cohort at all.
    A naive stamp is the store's UTC clock with the offset omitted, so it
    is read, not dropped.
    """
    on_the_day = _user("paper", _at(days=-7), _at())
    early = _user("trader", _at(days=-7), _at(seconds=-1))
    short = _user("viewer", _at(days=-7, seconds=1), _at())
    reading = retention_baseline([on_the_day, early, short], NOW)
    day7 = reading.horizons[0]
    assert day7.retained == 1 and day7.eligible == 2
    assert day7.not_yet_due == 1

    naive = datetime(2026, 9, 26, 12, 0).isoformat()
    row = _user("paper", naive, _at())
    one = retention_baseline([row], NOW).horizons[0]
    assert one.status == "measured" and one.retained == 1 and one.eligible == 1


def test_roles_are_separate_readings_and_an_absent_role_is_not_a_role():
    back = _user("paper", _at(days=-40), _at(days=-10))
    quiet = _user("trader", _at(days=-40), _at(days=-40))
    blank_role = {"created_at": _at(days=-40), "last_seen": _at(days=-10), "role": ""}
    marked = {"role": "<paper>", "created_at": _at(days=-40), "last_seen": _at(days=-40)}
    reading = retention_baseline([back, quiet, blank_role, marked], NOW)
    by_role = dict(reading.by_role)
    assert by_role["paper"][0].retained == 1 and by_role["paper"][0].eligible == 1
    assert by_role["trader"][0].retained == 0 and by_role["trader"][0].eligible == 1
    assert "" in by_role
    assert by_role[""][0].retained == 1
    card = render_retention_baseline(reading)
    assert "paper, Day 7: 1 of 1 returned (100%)." in card
    assert "trader, Day 7: 0 of 1 returned (0%)." in card
    assert "role unrecorded, Day 7: 1 of 1 returned (100%)." in card
    assert "&lt;paper&gt;, Day 7: 0 of 1 returned (0%)." in card
    # The segments add up to the roster. A role with nobody is absent.
    assert "viewer" not in card
    eligible = 0
    retained = 0
    for horizons in by_role.values():
        day = horizons[0]
        assert day.eligible is not None and day.retained is not None
        eligible += day.eligible
        retained += day.retained
    assert eligible == reading.horizons[0].eligible
    assert retained == reading.horizons[0].retained


def test_one_of_three_prints_the_truncated_percent_beside_the_count():
    rows = [
        _user("paper", _at(days=-40), _at(days=-10)),
        _user("paper", _at(days=-40), _at(days=-40)),
        _user("paper", _at(days=-40), _at(days=-39)),
    ]
    card = render_retention_baseline(retention_baseline(rows, NOW))
    assert _horizon(card, 7).startswith("Day 7: 1 of 3 returned (33.3%).")


def test_the_four_arms_stay_in_their_own_buckets():
    rows = [
        _user("paper", _at(days=-40), _at(days=-10)),
        _user("trader", _at(days=-40), _at(days=-40)),
        _user("paper", _at(days=-1), _at()),
        {"role": "viewer", "created_at": "nope", "last_seen": _at()},
    ]
    day7 = retention_baseline(rows, NOW).horizons[0]
    assert day7.retained == 1
    assert day7.eligible == 2
    assert day7.not_yet_due == 1
    assert day7.unreadable == 1
    assert day7.status == "measured"


def test_a_naive_clock_is_refused():
    with pytest.raises(ValueError):
        retention_baseline([], datetime(2026, 10, 3, 12, 0))


@pytest.mark.asyncio
async def test_users_shows_the_baseline_and_places_no_order():
    """The roster is the non-test caller. The card reports both horizons."""
    handler = _make_handler()
    now = datetime.now(UTC)
    admin = handler.users.get("6307156912")
    assert isinstance(admin, dict)
    admin["created_at"] = (now - timedelta(days=45)).isoformat()
    admin["last_seen"] = admin["created_at"]

    handler.users.register("111", name="Back")
    back = handler.users.get("111")
    assert isinstance(back, dict)
    back["created_at"] = (now - timedelta(days=40)).isoformat()
    back["last_seen"] = (now - timedelta(days=20)).isoformat()

    handler.users.register("222", name="New")
    recent = handler.users.get("222")
    assert isinstance(recent, dict)
    recent["created_at"] = (now - timedelta(days=2)).isoformat()
    recent["last_seen"] = now.isoformat()

    handler.users.register("333", name="Unread")
    unread = handler.users.get("333")
    assert isinstance(unread, dict)
    unread["role"] = "viewer"
    unread["created_at"] = "not-a-time"
    unread["last_seen"] = now.isoformat()

    update, ctx = _make_update(text="/users")
    await handler._cmd_users(update, ctx)
    card = _last_reply_text(update)
    assert "This does not place an order." in card
    assert "$" not in card
    day7 = _horizon(card, 7)
    day30 = _horizon(card, 30)
    assert day7.startswith("Day 7: 1 of 2 returned (50%).")
    assert "1 signup is not yet at day 7" in day7
    assert "1 time could not be read" in day7
    assert day30.startswith("Day 30: 0 of 2 returned (0%).")
    assert "1 signup is not yet at day 30" in day30
    assert "paper, Day 7: 1 of 1 returned (100%)." in card
    assert "admin, Day 7: 0 of 1 returned (0%)." in card
    assert "viewer, Day 7: unmeasured." in card
