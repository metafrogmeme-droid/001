"""Seed macro calendar staleness must key on event COVERAGE, not file age.

A seed calendar of scheduled events (FOMC/CPI dates fixed a year ahead) does
not become wrong because the file's generated_utc is old — only when it stops
covering the forward horizon. The old logic marked ANY seed older than 72 h
stale and fail-closed the whole bot (BLOCK_NEW_ENTRIES on every trade), a
self-inflicted outage while the calendar still had valid events through
December. Now: aged-but-still-covering seed is trusted; only an exhausted
seed fails closed.

The limit itself is the seed's to state. `config/macro_calendar.seed.json`
carries `max_stale_hours: 720`, and the provider never read it: the engine
passes no limit, so the file was held to the constructor's 72. A seed four days
old with nothing listed beyond the next three days failed closed, though the
file said it was good for thirty. The second half of this file drives that: the
file's own value when it is a number from 1 to 720, the default of 72 when the
key is missing or unusable, the caller's value over both, and a live feed that
reads neither.

The file's value is how old the seed may grow. It is not how far ahead the seed
must reach: a seed past its limit is still trusted while it lists an event
beyond the next 72 hours, whatever the file says. So the shipped seed turns
stale three days before its last event, as it did before, and not thirty.
"""

import json
import logging
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from bot.compat import UTC
from bot.core import macro_events
from bot.core.macro_events import (
    DEFAULT_MAX_STALE_HOURS,
    SEED_LOOK_AHEAD_HOURS,
    SEED_MAX_STALE_HOURS_RANGE,
    MacroEventProvider,
)

SHIPPED = Path(__file__).resolve().parents[1] / "config" / "macro_calendar.seed.json"
_NO_KEY = object()


def _seed(tmp_path, generated_days_ago, event_offsets_hours):
    now = datetime.now(UTC)
    gen = now - timedelta(days=generated_days_ago)
    events = [{"name": f"EV{i}", "scheduled_utc":
               (now + timedelta(hours=h)).isoformat().replace("+00:00", "Z"),
               "severity": "high"}
              for i, h in enumerate(event_offsets_hours)]
    p = tmp_path / "seed.json"
    p.write_text(json.dumps({"generated_utc": gen.isoformat().replace("+00:00", "Z"),
                             "events": events}))
    return p


class TestSeedCoverageStaleness:
    def test_aged_seed_with_forward_coverage_is_trusted(self, tmp_path):
        # 30-day-old file, but events 200h/500h out (past the 72h horizon).
        p = _seed(tmp_path, generated_days_ago=30, event_offsets_hours=[200, 500])
        prov = MacroEventProvider(seed_path=p, max_stale_hours=72)
        assert prov._calendar_stale is False
        assert prov._calendar_blind is False
        # And it does NOT block: a clear (non-event) window trades normally.
        ctx = prov.get_context(now=datetime.now(UTC) + timedelta(hours=50))
        assert ctx.risk_state != "BLOCK_NEW_ENTRIES"

    def test_aged_seed_without_forward_coverage_is_stale(self, tmp_path):
        # 30-day-old file whose only events are already in the past / inside
        # the horizon → no forward coverage → genuinely blind → fail-closed.
        p = _seed(tmp_path, generated_days_ago=30, event_offsets_hours=[-100, 10])
        prov = MacroEventProvider(seed_path=p, max_stale_hours=72, failsafe=True)
        assert prov._calendar_stale is True
        ctx = prov.get_context()
        assert ctx.risk_state == "BLOCK_NEW_ENTRIES"

    def test_fresh_seed_never_stale(self, tmp_path):
        p = _seed(tmp_path, generated_days_ago=1, event_offsets_hours=[10, 300])
        prov = MacroEventProvider(seed_path=p, max_stale_hours=72)
        assert prov._calendar_stale is False

    def test_failsafe_off_never_blocks_even_when_stale(self, tmp_path):
        p = _seed(tmp_path, generated_days_ago=30, event_offsets_hours=[-100])
        prov = MacroEventProvider(seed_path=p, max_stale_hours=72, failsafe=False)
        ctx = prov.get_context()
        assert ctx.risk_state != "BLOCK_NEW_ENTRIES"


# --- the limit is the seed's own --------------------------------------------

def _aged(tmp_path, *, age_hours, events_at_hours, limit=_NO_KEY):
    """A seed generated `age_hours` ago, listing one event at each offset from
    now, whose `max_stale_hours` is `limit` (the key is left out for _NO_KEY)."""
    now = datetime.now(UTC)
    data = {
        "generated_utc": (now - timedelta(hours=age_hours)).isoformat().replace("+00:00", "Z"),
        "events": [{"label": f"EV{i}", "scheduled_utc":
                    (now + timedelta(hours=h)).isoformat().replace("+00:00", "Z")}
                   for i, h in enumerate(events_at_hours)],
    }
    if limit is not _NO_KEY:
        data["max_stale_hours"] = limit
    p = tmp_path / "seed.json"
    p.write_text(json.dumps(data))
    return p


def _said(caplog):
    """What the provider's logger warned, as text."""
    return [r.getMessage() for r in caplog.records
            if r.name == "bot.core.macro_events" and r.levelno >= logging.WARNING]


@pytest.fixture
def warnings(caplog):
    caplog.set_level(logging.WARNING, logger="bot.core.macro_events")
    return lambda: _said(caplog)


#: Four days old, and nothing listed beyond the next three: stale under 72.
SHORT_RUNWAY = dict(age_hours=100, events_at_hours=[10])


class TestTheSeedIsHeldToItsOwnLimit:
    def test_a_seed_that_says_720_is_not_stale_at_100_hours(self, tmp_path):
        prov = MacroEventProvider(seed_path=_aged(tmp_path, limit=720, **SHORT_RUNWAY),
                                  failsafe=True)
        assert prov._max_stale_hours == 720
        assert prov._calendar_stale is False and prov._calendar_blind is False
        assert prov.get_context().risk_state == "CLEAR"

    def test_the_same_seed_held_to_72_is_stale(self, tmp_path):
        """THE CONTROL: the fixture can produce the state the fix removes."""
        prov = MacroEventProvider(seed_path=_aged(tmp_path, limit=720, **SHORT_RUNWAY),
                                  max_stale_hours=72, failsafe=True)
        assert prov._calendar_stale is True
        assert prov.get_context().risk_state == "BLOCK_NEW_ENTRIES"

    @pytest.mark.parametrize("age_hours,stale", [(719, False), (721, True)])
    def test_720_is_where_it_turns_stale(self, tmp_path, age_hours, stale):
        prov = MacroEventProvider(seed_path=_aged(
            tmp_path, limit=720, age_hours=age_hours, events_at_hours=[10]), failsafe=True)
        assert prov._calendar_stale is stale
        assert (prov.get_context().risk_state == "BLOCK_NEW_ENTRIES") is stale

    def test_a_stale_seed_blocks_as_before_and_names_the_limit_it_was_held_to(self, tmp_path):
        p = _aged(tmp_path, limit=720, age_hours=721, events_at_hours=[10])
        ctx = MacroEventProvider(seed_path=p, failsafe=True).get_context()
        assert (ctx.risk_state, ctx.size_multiplier, ctx.is_stale) == (
            "BLOCK_NEW_ENTRIES", 0.0, True)
        assert "max age 720h" in ctx.explanation
        # MACRO_FAILSAFE off still lets trades through a stale calendar.
        off = MacroEventProvider(seed_path=p, failsafe=False)
        assert off._calendar_stale is True
        assert off.get_context().risk_state != "BLOCK_NEW_ENTRIES"

    @pytest.mark.parametrize("event_at_hours,stale", [(71, True), (73, False), (100, False)])
    def test_the_look_ahead_stays_at_72_hours_whatever_the_file_says(
            self, tmp_path, event_at_hours, stale):
        """Past its own limit, a seed is trusted while it lists an event beyond
        the next 72 hours. The file's 720 is how old it may grow, not how far
        ahead it must reach: an event 100 h out is enough, one 71 h out is not."""
        prov = MacroEventProvider(seed_path=_aged(
            tmp_path, limit=720, age_hours=721, events_at_hours=[event_at_hours]),
            failsafe=True)
        assert prov._look_ahead_hours == SEED_LOOK_AHEAD_HOURS == 72
        assert prov._calendar_stale is stale
        assert (prov.get_context().risk_state == "BLOCK_NEW_ENTRIES") is stale

    def test_the_shipped_seed_is_held_to_what_it_says(self):
        said = json.loads(SHIPPED.read_text(encoding="utf-8"))["max_stale_hours"]
        low, high = SEED_MAX_STALE_HOURS_RANGE
        assert low <= said <= high, "the shipped limit would be read as unusable"
        assert said != DEFAULT_MAX_STALE_HOURS, "this cannot tell the file from the default"
        prov = MacroEventProvider(seed_path=SHIPPED)
        assert prov._max_stale_hours == said
        assert prov._look_ahead_hours == 72
        assert prov._calendar_stale is False and prov._calendar_blind is False

    @pytest.mark.parametrize("hours_before_last,stale", [(100, False), (50, True)])
    def test_the_shipped_seed_turns_stale_72_hours_before_its_last_event(
            self, monkeypatch, hours_before_last, stale):
        """When a boot finds the shipped seed stale is where it was before the
        file's limit was read: inside 72 hours of the last listed event. Were
        the look-ahead the file's 720, both instants here would read stale."""
        shipped = json.loads(SHIPPED.read_text(encoding="utf-8"))
        last = max(datetime.fromisoformat(e["scheduled_utc"].replace("Z", "+00:00"))
                   for e in shipped["events"])
        generated = datetime.fromisoformat(shipped["generated_utc"].replace("Z", "+00:00"))
        instant = last - timedelta(hours=hours_before_last)
        assert instant - generated > timedelta(hours=shipped["max_stale_hours"]), (
            "the seed is not past its own limit at this instant, so nothing is measured")

        class _Clock(datetime):
            @classmethod
            def now(cls, tz=None):
                return instant.astimezone(tz) if tz is not None else instant

        monkeypatch.setattr(macro_events, "datetime", _Clock)
        prov = MacroEventProvider(seed_path=SHIPPED, failsafe=True)
        assert prov._calendar_blind is False
        assert prov._calendar_stale is stale

    def test_the_engine_passes_no_limit_so_the_seed_states_it(self, tmp_path, monkeypatch):
        from bot.core.engine import RuneClawEngine

        monkeypatch.chdir(tmp_path)
        prov = RuneClawEngine().macro_provider
        assert prov._seed_path == SHIPPED
        assert prov._explicit_max_stale_hours is None
        assert prov._max_stale_hours == json.loads(
            SHIPPED.read_text(encoding="utf-8"))["max_stale_hours"]
        assert prov._look_ahead_hours == 72


class TestAFileWithNoUsableLimit:
    def test_a_missing_key_is_the_default_and_says_so(self, tmp_path, warnings):
        prov = MacroEventProvider(seed_path=_aged(tmp_path, **SHORT_RUNWAY), failsafe=True)
        assert prov._max_stale_hours == DEFAULT_MAX_STALE_HOURS == 72
        assert prov._calendar_stale is True
        assert prov.get_context().risk_state == "BLOCK_NEW_ENTRIES"
        assert any("names no max_stale_hours" in w and "default of 72 h" in w
                   for w in warnings()), warnings()

    @pytest.mark.parametrize("value", [
        "720", "abc", "", None, True, False, [], [720], {}, {"hours": 720},
        0, -5, 0.5, 720.5, 721, 99999, float("nan"), float("inf"), float("-inf")],
        ids=repr)
    def test_an_unusable_value_is_the_default_and_says_so(self, tmp_path, warnings, value):
        prov = MacroEventProvider(seed_path=_aged(tmp_path, limit=value, **SHORT_RUNWAY),
                                  failsafe=True)
        assert prov._max_stale_hours == 72
        assert prov._calendar_stale is True
        assert prov.get_context().risk_state == "BLOCK_NEW_ENTRIES"
        said = [w for w in warnings() if "not a number from 1 to 720" in w]
        assert len(said) == 1 and "default of 72 h" in said[0], warnings()
        # Absent and unusable are different facts and get different sentences.
        assert not any("names no max_stale_hours" in w for w in warnings())

    @pytest.mark.parametrize("value", [1, 72, 100, 100.5, 720])
    def test_a_number_in_range_is_the_limit_and_is_not_warned_about(
            self, tmp_path, warnings, value):
        prov = MacroEventProvider(seed_path=_aged(
            tmp_path, limit=value, age_hours=0.5, events_at_hours=[10]))
        assert prov._max_stale_hours == value
        assert prov._calendar_stale is False
        assert warnings() == []

    def test_one_hour_is_a_limit_a_seed_can_fail(self, tmp_path):
        """The low bound is held like any other value: two hours old, with the
        only event half an hour out, is stale under a limit of one hour."""
        p = _aged(tmp_path, limit=1, age_hours=2, events_at_hours=[0.5])
        assert MacroEventProvider(seed_path=p)._calendar_stale is True


class TestTheCallersLimitWins:
    def test_72_from_the_caller_beats_a_file_that_says_720(self, tmp_path, warnings):
        prov = MacroEventProvider(seed_path=_aged(tmp_path, limit=720, **SHORT_RUNWAY),
                                  max_stale_hours=72)
        assert prov._max_stale_hours == 72 and prov._calendar_stale is True

    def test_720_from_the_caller_beats_a_file_that_says_nothing(self, tmp_path, warnings):
        prov = MacroEventProvider(seed_path=_aged(tmp_path, **SHORT_RUNWAY),
                                  max_stale_hours=720)
        assert prov._max_stale_hours == 720 and prov._calendar_stale is False
        # The file was not asked, so there is nothing to warn about.
        assert not any("max_stale_hours" in w for w in warnings()), warnings()

    def test_a_callers_limit_is_still_the_look_ahead_too(self, tmp_path):
        """A limit passed in code has always been one number for both uses, and
        still is. The caller's 720 asks for an event beyond the next 720 hours;
        the file's 720 does not."""
        p = _aged(tmp_path, limit=720, age_hours=721, events_at_hours=[100])
        assert MacroEventProvider(seed_path=p)._calendar_stale is False
        mine = MacroEventProvider(seed_path=p, max_stale_hours=720)
        assert mine._look_ahead_hours == 720 and mine._calendar_stale is True
        # A short limit, on both sides of its own look-ahead.
        for event_at_hours, stale in ((20, True), (30, False)):
            q = _aged(tmp_path, limit=720, age_hours=25, events_at_hours=[event_at_hours])
            assert MacroEventProvider(seed_path=q, max_stale_hours=24)._calendar_stale is stale

    def test_the_callers_limit_is_not_held_to_the_files_range(self, tmp_path, warnings):
        prov = MacroEventProvider(seed_path=_aged(tmp_path, limit="garbage", **SHORT_RUNWAY),
                                  max_stale_hours=2000)
        assert prov._max_stale_hours == 2000 and prov._calendar_stale is False
        assert not any("max_stale_hours" in w for w in warnings()), warnings()


class TestTheLiveFeedReadsNeither:
    FEED = [{"event_type": "CPI", "label": "CPI",
             "scheduled_utc": "2031-01-01T13:30:00Z"}]

    def test_a_live_calendar_is_never_stale_and_the_file_is_not_asked(self, tmp_path, warnings):
        # A seed that would be stale on its own, with a limit that would warn.
        p = _aged(tmp_path, limit=99999, age_hours=5000, events_at_hours=[-10])
        prov = MacroEventProvider(seed_path=p, live_feed=lambda: list(self.FEED),
                                  failsafe=True)
        assert prov.event_count == 1
        assert prov._calendar_stale is False and prov._calendar_blind is False
        assert prov._max_stale_hours == DEFAULT_MAX_STALE_HOURS
        assert prov.get_context().risk_state == "CLEAR"
        assert warnings() == []

    def test_a_feed_that_fails_falls_back_to_the_seed_and_its_limit(self, tmp_path):
        def down():
            raise RuntimeError("feed down")

        p = _aged(tmp_path, limit=720, **SHORT_RUNWAY)
        prov = MacroEventProvider(seed_path=p, live_feed=down, failsafe=True)
        assert prov._max_stale_hours == 720 and prov._calendar_stale is False

    def test_a_reload_from_the_feed_does_not_keep_the_last_seeds_limit(self, tmp_path):
        up = {"yes": False}

        def feed():
            if not up["yes"]:
                raise RuntimeError("feed down")
            return list(self.FEED)

        prov = MacroEventProvider(seed_path=_aged(tmp_path, limit=720, **SHORT_RUNWAY),
                                  live_feed=feed)
        assert prov._max_stale_hours == 720
        up["yes"] = True
        prov.reload()
        assert prov.event_count == 1 and prov._calendar_stale is False
        assert prov._max_stale_hours == DEFAULT_MAX_STALE_HOURS
