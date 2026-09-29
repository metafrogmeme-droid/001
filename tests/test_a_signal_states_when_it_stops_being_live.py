"""A published signal states when it stops being live, from the bot's own TTL.

The website's copy picks and its push sweep selected `status = 'OPEN'`, a
status no producer ever wrote (the engine and the scan both push NEW, and
nothing resolves a signal afterwards), so the follow feature could never show
or push a pick. A signal is live while the bot would still take it: its
creation plus `PENDING_IDEA_TTL`, the time an idea stays confirmable before
the tick's sweep drops it. The producer states that as `expires_at`, because
the website cannot read the bot's config and a second copy of the number
there would be a second answer. The website half is
`app/test/copy_picks_read_the_bots_stated_window.test.js`.
"""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from bot.config import CONFIG
from bot.utils.models import Direction, TradeIdea
from bot.utils.website_sync import build_signal_payload, signal_expires_at

T0 = datetime(2026, 9, 28, 17, 33, 0, tzinfo=UTC)


class TestTheWindow:

    def test_creation_plus_the_bots_idea_ttl(self):
        want = (T0 + timedelta(seconds=CONFIG.pending_idea_ttl)).isoformat()
        assert signal_expires_at(T0) == want

    def test_an_explicit_ttl_is_used(self):
        assert signal_expires_at(T0, 60) == (T0 + timedelta(seconds=60)).isoformat()

    def test_a_naive_time_is_read_as_utc(self):
        assert signal_expires_at(T0.replace(tzinfo=None), 60) == signal_expires_at(T0, 60)

    def test_another_zone_is_stated_in_utc(self):
        from datetime import timezone
        plus2 = T0.astimezone(timezone(timedelta(hours=2)))
        assert signal_expires_at(plus2, 60) == signal_expires_at(T0, 60)
        assert signal_expires_at(plus2, 60).endswith("+00:00")

    @pytest.mark.parametrize("created", [None, "2026-09-28", 1759080000, SimpleNamespace()])
    def test_a_creation_it_cannot_read_states_no_window(self, created):
        assert signal_expires_at(created, 60) == ""

    @pytest.mark.parametrize("ttl", [0, -5, float("nan"), True, "300"])
    def test_a_ttl_that_is_not_a_positive_number_states_no_window(self, ttl):
        assert signal_expires_at(T0, ttl) == ""


def _idea():
    idea = TradeIdea(id="T-9", asset="ARB/USDT", direction=Direction.LONG,
                     entry_price=1.0, stop_loss=0.95, take_profit=1.1,
                     confidence=0.7, reasoning="r", timestamp=T0)
    idea.blended_confidence_raw = 0.7
    return idea


class TestBothProducersStateIt:

    def test_the_row_carries_what_it_is_handed_and_nothing_by_default(self):
        idea = _idea()
        assert build_signal_payload("k", idea, expires_at="X")["expires_at"] == "X"
        assert build_signal_payload("k", idea)["expires_at"] == ""

    def test_the_engines_rows_state_creation_plus_ttl(self):
        from bot.core.engine import _build_signal_sync_payloads

        rows = _build_signal_sync_payloads([_idea()], lambda _a: "TREND_UP")
        assert rows[0]["expires_at"] == signal_expires_at(T0)
        assert rows[0]["expires_at"] != ""

    def _payload(self, ts):
        return {"timestamp": ts, "regime": {"label": "TREND_UP"},
                "entry_cards": [{"symbol": "ARB/USDT", "direction": "LONG", "score": 0.7,
                                 "entry": 1.0, "stop_loss": 0.95, "tp1": 1.1, "rr": 2}]}

    def test_the_scans_rows_state_the_scan_minute_plus_ttl(self):
        from bot.skills.scan_skill import _scan_signal_rows

        rows = _scan_signal_rows(self._payload("2026-09-28 17:33 UTC"))
        assert rows[0]["expires_at"] == signal_expires_at(T0)

    def test_a_scan_stamp_that_does_not_parse_states_no_window(self):
        from bot.skills.scan_skill import _scan_signal_rows

        rows = _scan_signal_rows(self._payload("sometime"))
        assert rows[0]["expires_at"] == ""
        assert rows[0]["signal_key"]   # the row is still published, just never live
