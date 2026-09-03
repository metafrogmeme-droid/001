"""The web yield section turned "could not read the free margin" into "$0 idle".

`engine.live_balance_cached()` returns None by design when the balance is
stale or never read -- its docstring: "None means do not know. Downstream
surfaces already render that honestly." `yield_radar.build_report` is one of
them: `futures_free_usdt=None` marks the report INCOMPLETE and leaves the
futures row out because we do not know, not because nothing is there.

Between the two, `web_reports._yield_section` did `float(cache.get("free")
or 0.0)`: the None became 0.0, build_report took the "nothing idle" path,
and the admin dashboard printed "Total idle $X" over a spot-only figure
with no note. Honest on both sides, defeated in the middle -- and the
payload dropped `incomplete` even when build_report had set it.
"""
from __future__ import annotations

from types import SimpleNamespace

import bot.core.web_reports as wr
from bot.core.yield_radar import YieldReport


def _wire(monkeypatch, cache):
    """A credentialed client, an engine whose cache read returns `cache`, and
    a build_report that records what it was handed."""
    seen = {}
    monkeypatch.setattr("bot.core.bitget_v3_client.BitgetV3Client.from_config",
                        staticmethod(lambda: SimpleNamespace(has_credentials=True)))

    def fake_build(client, futures_free_usdt=0.0, prices=None):
        seen["free"] = futures_free_usdt
        rep = YieldReport()
        if futures_free_usdt is None:
            rep.incomplete = "Free futures margin could not be read, so it is not counted below."
        rep.total_idle_usd = 40.0
        return rep
    monkeypatch.setattr("bot.core.yield_radar.build_report", fake_build)
    engine = SimpleNamespace(live_balance_cached=lambda max_age_s=900.0: cache)
    return engine, seen


def test_an_unread_balance_is_handed_over_as_none_and_the_note_travels(monkeypatch):
    engine, seen = _wire(monkeypatch, None)
    payload = wr._yield_section(engine)
    assert seen["free"] is None, "THE DEFECT: None was coerced to 0.0 before build_report saw it"
    assert payload["incomplete"], "the payload must carry the incompleteness, not just the totals"
    assert "could not be read" in payload["incomplete"]


def test_a_cache_without_a_free_field_is_also_unread(monkeypatch):
    engine, seen = _wire(monkeypatch, {"equity": 100.0})
    wr._yield_section(engine)
    assert seen["free"] is None


def test_a_real_free_margin_is_passed_through(monkeypatch):
    engine, seen = _wire(monkeypatch, {"free": 12.5})
    payload = wr._yield_section(engine)
    assert seen["free"] == 12.5
    assert payload["incomplete"] == "", "a complete read carries no note"


def test_a_genuine_zero_free_margin_is_zero_not_unknown(monkeypatch):
    """0.0 is a measured 'nothing idle on futures'. `is None`, not falsiness."""
    engine, seen = _wire(monkeypatch, {"free": 0.0})
    payload = wr._yield_section(engine)
    assert seen["free"] == 0.0
    assert payload["incomplete"] == ""


def test_a_non_numeric_free_field_is_unread(monkeypatch):
    engine, seen = _wire(monkeypatch, {"free": "n/a"})
    wr._yield_section(engine)
    assert seen["free"] is None
