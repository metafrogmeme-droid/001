"""A cached balance was served as current long after the venue went dark.

`get_live_equity()` enforces its TTL on the FETCH side. But when fetches keep
failing, `_live_balance_cache_ts` stops advancing while `_live_balance_cache`
keeps its last value — and five call sites read the dict DIRECTLY, none of
them consulting the timestamp:

  - the website's equity fallback (scan_skill) — the primary surface showed
    an hours-old dollar figure as the live account;
  - the /yield free-margin row and its web-report twin — yield suggestions
    sized off margin that may no longer exist;
  - the admin card's "Available";
  - _engine_free_usdt, whose own docstring distinguishes "we do not know"
    from zero — and then treated a stale number as known.

`live_balance_cached(max_age_s)` is the one age-gated accessor. None means
"do not know"; every surface above already renders that honestly. A stale
dollar figure they cannot detect at all — which is why the gate lives on the
engine, not in five copies at the call sites.
"""
from __future__ import annotations

import time
from types import SimpleNamespace

from bot.core.engine import RuneClawEngine

from tests.source_scan import code_only


def _eng(cache, age_s):
    e = SimpleNamespace()
    e._live_balance_cache = cache
    e._live_balance_cache_ts = time.monotonic() - age_s
    e.live_balance_cached = RuneClawEngine.live_balance_cached.__get__(e)
    return e


class TestTheGate:
    def test_fresh_cache_is_returned(self):
        bal = {"total": 884.31, "free": 512.0}
        assert _eng(bal, age_s=30.0).live_balance_cached() == bal

    def test_stale_cache_is_none_not_the_old_number(self):
        # The defect: this returned {"total": 884.31} hours after the venue
        # connection died, and every reader presented it as current.
        assert _eng({"total": 884.31}, age_s=3600.0).live_balance_cached() is None

    def test_empty_cache_is_none(self):
        assert _eng({}, age_s=0.0).live_balance_cached() is None

    def test_the_boundary_is_the_callers_choice(self):
        e = _eng({"total": 1.0}, age_s=120.0)
        assert e.live_balance_cached(max_age_s=60.0) is None
        assert e.live_balance_cached(max_age_s=300.0) == {"total": 1.0}

    def test_never_initialised_timestamp_reads_as_stale(self):
        # ts=0.0 (never stamped) with a somehow-populated dict must not pass
        # the gate. This needs an explicit check in the accessor, NOT the age
        # arithmetic: monotonic's epoch is unspecified, and on a freshly
        # booted host "monotonic() - 0.0" is the host's uptime — CI's
        # just-booted runner returned the cache when this test relied on the
        # subtraction being "enormous by construction". It is not.
        e = SimpleNamespace(
            _live_balance_cache={"total": 5.0},
            _live_balance_cache_ts=0.0,
        )
        e.live_balance_cached = RuneClawEngine.live_balance_cached.__get__(e)
        assert e.live_balance_cached() is None

    def test_never_stamped_is_refused_even_when_uptime_is_short(self):
        # The CI failure, pinned directly: simulate a host whose monotonic
        # clock is minutes old, so the age arithmetic alone would call a
        # never-stamped cache "fresh".
        from unittest.mock import patch
        import bot.core.engine as engine_mod
        e = SimpleNamespace(
            _live_balance_cache={"total": 5.0},
            _live_balance_cache_ts=0.0,
        )
        e.live_balance_cached = RuneClawEngine.live_balance_cached.__get__(e)
        with patch.object(engine_mod.time, "monotonic", return_value=400.0):
            assert e.live_balance_cached(max_age_s=900.0) is None


class TestTheReadersUseIt:
    """The gate existing is not the fix — the readers going through it is.

    The same lesson as warning_rate_breaker_active: a correct primitive that
    nothing calls protects nothing.
    """

    def _direct_reads(self, path):
        src = code_only(open(path, encoding="utf-8").read())
        # Reads of the raw dict outside the engine itself. Attribute SETS
        # (handler /balance refresh) are writers and legitimately direct.
        return [ln for ln in src.splitlines()
                if "_live_balance_cache" in ln
                and "_live_balance_cache_ts" not in ln
                and "_live_balance_cache =" not in ln
                and "_live_balance_cache_ts =" not in ln
                and "_invalidate" not in ln]

    def test_scan_skill_reads_through_the_gate(self):
        src = code_only(open("bot/skills/scan_skill.py", encoding="utf-8").read())
        assert "live_balance_cached" in src
        assert self._direct_reads("bot/skills/scan_skill.py") == []

    def test_web_reports_reads_through_the_gate(self):
        src = code_only(open("bot/core/web_reports.py", encoding="utf-8").read())
        assert "live_balance_cached" in src
        assert self._direct_reads("bot/core/web_reports.py") == []

    def test_skill_registry_reads_through_the_gate(self):
        src = code_only(open("bot/skills/skill_registry.py", encoding="utf-8").read())
        assert "live_balance_cached" in src
        assert self._direct_reads("bot/skills/skill_registry.py") == []

    def test_telegram_handler_reads_through_the_gate(self):
        # The handler also WRITES the cache after /balance — writes are the
        # refresh path and stay direct; the filter above excludes them.
        # Every file the handler class is made of: `_engine_free_usdt` moved
        # into the yield mixin with the handler split, and a direct read that
        # moved with it would be invisible to a scan of one file.
        from tests.source_scan import handler_sources
        for path in handler_sources():
            reads = self._direct_reads(str(path))
            assert reads == [], f"{path.name}: direct stale-blind reads remain: {reads}"

    def test_admin_card_renders_unknown_as_unavailable_not_zero(self):
        src = code_only(open("bot/skills/skill_registry.py", encoding="utf-8").read())
        assert "unavailable" in src
        # $0.00 for "Available" claims "no margin left" — a different and
        # alarming statement, not a degraded one.
        assert 'free_bal = engine._live_balance_cache.get("free", 0.0)' not in src
