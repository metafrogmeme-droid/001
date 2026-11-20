"""The bundled macro seed calendar must stay well-formed and forward-covering.

The bot has no live macro feed — it gates trading off config/macro_calendar.seed.json.
If that file drifts (unsorted, unparseable dates, or no events past the staleness
horizon) the macro gate fail-closes and blocks live entries. This validates the
committed seed so a bad edit can't ship.
"""

import json
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

SEED = Path(__file__).resolve().parent.parent / "config" / "macro_calendar.seed.json"


@pytest.fixture(scope="module")
def seed():
    return json.loads(SEED.read_text())


def _dt(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def test_seed_has_required_shape(seed):
    assert set(("version", "generated_utc", "max_stale_hours", "events")) <= set(seed)
    assert isinstance(seed["events"], list) and seed["events"]
    assert isinstance(seed["max_stale_hours"], int) and seed["max_stale_hours"] > 0


def test_every_event_is_well_formed(seed):
    for e in seed["events"]:
        assert e["type"] in {"FOMC_DECISION", "CPI_RELEASE", "NFP_RELEASE", "CORE_PCE"}
        assert e["severity"] in {"CRITICAL", "HIGH", "MEDIUM", "LOW"}
        _dt(e["scheduled_utc"])  # raises if unparseable
        assert e.get("label")


def test_events_are_sorted_by_time(seed):
    times = [e["scheduled_utc"] for e in seed["events"]]
    assert times == sorted(times)


def test_no_duplicate_fomc_decisions(seed):
    fomc = [e["scheduled_utc"] for e in seed["events"] if e["type"] == "FOMC_DECISION"]
    assert len(fomc) == len(set(fomc))


def test_forward_coverage_beyond_staleness_horizon(seed):
    """There must be at least one event past generated_utc + max_stale_hours,
    or the gate marks the seed stale and fail-closes immediately on load."""
    gen = _dt(seed["generated_utc"])
    horizon = gen + timedelta(hours=seed["max_stale_hours"])
    assert any(_dt(e["scheduled_utc"]) >= horizon for e in seed["events"]), \
        "seed has no events past the staleness horizon — will fail-closed"


def test_runway_is_comfortable(seed):
    """The latest event should be well ahead of generation — a refresh nudge is
    fine, but a seed that lapses within weeks is not."""
    gen = _dt(seed["generated_utc"])
    latest = max(_dt(e["scheduled_utc"]) for e in seed["events"])
    assert (latest - gen).days >= 180, "less than ~6 months of macro runway"


def test_loads_clean_through_provider():
    from bot.core.macro_events import MacroEventProvider
    mp = MacroEventProvider(seed_path=SEED)
    mp._load_calendar()
    assert mp._calendar_loaded and not mp._calendar_blind


def test_the_core_pce_of_25_november_2026_is_in_the_seed_and_loads(seed):
    """The seed carries the Core PCE release of 25 November 2026 under the kind,
    label and time the hardcoded calendar (`bot/macro/calendar.py`) gives it,
    and the provider loads it as the MEDIUM event it classifies that kind as.

    The expected row is read off the hardcoded calendar and the time is also
    written out, so a change on either side fails here. The seed's `severity`
    is the provider's reading of the kind; the hardcoded calendar's `impact` is
    a default no event sets, so it is not compared."""
    from bot.core.macro_events import MacroEventProvider, MacroSeverity
    from bot.macro.calendar import build_2026_calendar
    from bot.macro.models import MacroEventType

    day = date(2026, 11, 25)
    hardcoded = [e for e in build_2026_calendar()
                 if e.event_type is MacroEventType.CORE_PCE
                 and e.scheduled_utc.date() == day]
    rows = [e for e in seed["events"]
            if e["type"] == "CORE_PCE" and _dt(e["scheduled_utc"]).date() == day]
    assert len(hardcoded) == 1, "the hardcoded calendar no longer schedules it"
    assert len(rows) == 1, "the seed must list this release exactly once"
    row, known = rows[0], hardcoded[0]

    assert row["scheduled_utc"] == "2026-11-25T13:30:00Z"  # 08:30 ET, in winter
    assert (row["type"], row["label"], _dt(row["scheduled_utc"])) == (
        known.event_type.value, known.label, known.scheduled_utc)
    assert row["severity"] == "MEDIUM"
    # Every seed event carries the same five fields; this one is no exception.
    assert set(row) == set(seed["events"][0])
    assert row["date_confidence"] in {"confirmed", "estimated", "tentative"}

    mp = MacroEventProvider(seed_path=SEED)
    assert mp.event_count == len(seed["events"]), "an event was dropped on load"
    loaded = [e for e in mp._events
              if e["event_type"] == "CORE_PCE" and e["scheduled_utc"].date() == day]
    assert len(loaded) == 1
    assert loaded[0]["scheduled_utc"] == known.scheduled_utc
    assert loaded[0]["label"] == known.label
    assert mp._severity_for(loaded[0]) is MacroSeverity.MEDIUM
