"""A risk-gated SOL idea maps to one small signal; everything else maps to a reason.

SOL only, analyzer ideas only, fresh bars only, calls only (not re-offers),
full analyses only (not the tap's technicals-only read).
LONG is ``long`` at the configured allocation; SHORT is ``flat`` at 0 -- the
adapter never shorts. A signal lives at most 60 s and is at most 512 bytes.
"""
from __future__ import annotations

import json
from datetime import datetime

import pytest

from bot.compat import UTC
from bot.integrations.audacefi import settings
from bot.integrations.audacefi.signal_map import (
    AudaceDraft,
    audacefi_draft_for,
    audacefi_signal_id,
    encode_audacefi_signal,
)
from bot.utils.models import ANALYZER_SOURCE, Direction, TradeIdea

NOW_MS = 1_780_000_000_000


def _idea(**kw) -> TradeIdea:
    base = dict(id="TI-sol00001", asset="SOL/USDT", direction=Direction.LONG,
                entry_price=150.0, stop_loss=145.0, take_profit=162.0, confidence=0.7,
                reasoning="r", source=ANALYZER_SOURCE, timeframe="1h",
                timestamp=datetime.now(UTC), bar_age_at_analysis_s=120.0,
                newest_bar_close_ms=float(NOW_MS - 120_000))
    base.update(kw)
    return TradeIdea(**base)


@pytest.fixture(autouse=True)
def _defaults(monkeypatch):
    for k in ("AUDACEFI_SIGNAL_TTL_S", "AUDACEFI_LONG_TARGET_BPS", "BAR_FRESHNESS_MAX_PERIODS"):
        monkeypatch.delenv(k, raising=False)


class TestWhatMaps:
    def test_a_sol_long_is_long_at_full_allocation(self):
        draft, why = audacefi_draft_for(_idea(), NOW_MS)
        assert why == "mapped"
        assert draft == AudaceDraft(id=audacefi_signal_id("TI-sol00001", "long"), symbol="SOL",
                                    side="long", targetBps=10_000, expiresAtMs=NOW_MS + 60_000)

    def test_a_sol_short_is_flat_at_zero_never_short(self):
        short = _idea(direction=Direction.SHORT, stop_loss=155.0, take_profit=140.0)
        draft, _ = audacefi_draft_for(short, NOW_MS)
        assert draft is not None
        assert (draft.side, draft.targetBps) == ("flat", 0)

    @pytest.mark.parametrize("asset", ["SOL/USDT", "SOL/USDT:USDT", "SOL/USDC:USDC", "sol/usdt"])
    def test_every_spelling_of_sol_is_sol(self, asset):
        draft, _ = audacefi_draft_for(_idea(asset=asset), NOW_MS)
        assert draft is not None and draft.symbol == "SOL"

    def test_the_allocation_is_configurable_and_clamped(self, monkeypatch):
        monkeypatch.setenv("AUDACEFI_LONG_TARGET_BPS", "2500")
        assert audacefi_draft_for(_idea(), NOW_MS)[0].targetBps == 2500
        monkeypatch.setenv("AUDACEFI_LONG_TARGET_BPS", "25000")
        assert audacefi_draft_for(_idea(), NOW_MS)[0].targetBps == 10_000

    def test_the_expiry_never_exceeds_sixty_seconds(self, monkeypatch):
        monkeypatch.setenv("AUDACEFI_SIGNAL_TTL_S", "300")
        assert audacefi_draft_for(_idea(), NOW_MS)[0].expiresAtMs == NOW_MS + 60_000
        monkeypatch.setenv("AUDACEFI_SIGNAL_TTL_S", "15")
        assert audacefi_draft_for(_idea(), NOW_MS)[0].expiresAtMs == NOW_MS + 15_000


class TestWhatDoesNot:
    @pytest.mark.parametrize("kw,needle", [
        ({"asset": "BTC/USDT"}, "not SOL"),
        ({"asset": "SOLANA/USDT"}, "not SOL"),
        ({"source": "manual"}, "not an analyzer idea"),
        ({"source": "unknown"}, "not an analyzer idea"),
        ({"repeat_of": "TI-older"}, "re-offer"),
        ({"analysis_depth": "lightweight"}, "lightweight analysis"),
        ({"bar_age_at_analysis_s": None}, "bar freshness: newest bar age unread"),
        ({"bar_age_at_analysis_s": 10_800.0}, "bar freshness: newest closed 1h bar is 10800s old"),
        ({"timeframe": None}, "bar freshness"),
    ])
    def test_it_says_why(self, kw, needle):
        draft, why = audacefi_draft_for(_idea(**kw), NOW_MS)
        assert draft is None
        assert needle in why, why


class TestTheWire:
    def test_the_body_is_compact_sorted_json_with_exactly_six_fields(self):
        draft, _ = audacefi_draft_for(_idea(), NOW_MS)
        body = encode_audacefi_signal(draft, 7)
        assert json.loads(body) == {"id": draft.id, "sequence": 7, "symbol": "SOL",
                                    "side": "long", "targetBps": 10_000,
                                    "expiresAtMs": NOW_MS + 60_000}
        assert body == json.dumps(json.loads(body), separators=(",", ":"),
                                  sort_keys=True).encode()
        assert len(body) <= settings.MAX_SIGNAL_BYTES

    def test_an_oversize_body_is_refused_not_truncated(self):
        big = AudaceDraft(id="x" * 600, symbol="SOL", side="long", targetBps=1, expiresAtMs=1)
        with pytest.raises(ValueError, match="> 512"):
            encode_audacefi_signal(big, 1)

    def test_the_id_is_stable_and_depends_on_the_side(self):
        assert audacefi_signal_id("TI-1", "long") == audacefi_signal_id("TI-1", "long")
        assert audacefi_signal_id("TI-1", "long") != audacefi_signal_id("TI-1", "flat")
        assert len(audacefi_signal_id("TI-1", "long")) == 24


def test_the_destination_is_a_constant_not_a_setting():
    assert (settings.GATEWAY_HOST, settings.GATEWAY_PORT, settings.GATEWAY_PATH) == \
        ("127.0.0.1", 8787, "/signals")


def test_the_flag_is_off_unless_set(monkeypatch):
    monkeypatch.delenv("AUDACEFI_DRY_RUN_ENABLED", raising=False)
    assert settings.audacefi_dry_run_on() is False
    monkeypatch.setenv("AUDACEFI_DRY_RUN_ENABLED", "true")
    assert settings.audacefi_dry_run_on() is True
