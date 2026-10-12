"""The LLM-offline card says what an outage changes, which depends on who decides.

It said "RUNNING ON RULES … rule engine only — no AI thesis, weaker signals"
in every case. Since 11 October the rule engine decides every trade by
default (`AnalyzerConfig.llm_decides` off), so an outage changes no decision:
what stops is the model's read beside each call and its calibration rows.
"Weaker" was never measured either way. Its all-clear said "AI theses are
back online" on the same footing.

Driven through `_check_llm_degraded` with the card's real config field.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

import bot.core.proactive_monitor as pm
from bot.core.proactive_monitor import ProactiveMonitor


def _cards(monkeypatch, llm_decides):
    """The offline card, then the all-clear, under `llm_decides`."""
    monkeypatch.setattr(pm, "CONFIG", SimpleNamespace(analyzer=SimpleNamespace(
        llm_degraded_alert_enabled=True, llm_degraded_alert_min_streak=3,
        llm_decides=llm_decides)))
    health = {"degraded_streak": 5, "degraded_seconds": 300.0,
              "last_ok_seconds_ago": None, "last_error": "", "chain_walk": None}
    mon = ProactiveMonitor(SimpleNamespace(
        analyzer=SimpleNamespace(llm_health=lambda: health)))
    down = mon._check_llm_degraded()
    health["degraded_streak"] = 0
    up = mon._check_llm_degraded()
    assert len(down) == 1 and len(up) == 1
    return down[0].body, up[0].body


def test_with_the_rule_engine_deciding_an_outage_changes_no_trade(monkeypatch):
    down, up = _cards(monkeypatch, llm_decides=False)
    assert "THE MODEL'S READ IS PAUSED" in down
    assert "Trading is unchanged" in down and "LLM_DECIDES is off" in down
    assert "RUNNING ON RULES" not in down
    assert "weaker" not in down and "no AI thesis" not in down
    assert "read beside each call resumes" in up
    assert "AI theses" not in up


def test_with_the_model_deciding_the_rules_take_its_place(monkeypatch):
    down, up = _cards(monkeypatch, llm_decides=True)
    assert "RUNNING ON RULES" in down
    assert "deciding in the model's place" in down
    assert "Trading is unchanged" not in down
    assert "weaker" not in down
    assert "thesis decides trades again" in up


@pytest.mark.parametrize("analyzer, want", [
    (SimpleNamespace(), False), (SimpleNamespace(llm_decides=True), True)])
def test_a_config_without_the_field_reads_as_the_shipped_default(monkeypatch, analyzer, want):
    """The field defaults off (`test_the_rule_engine_decides_and_the_llm_narrates`
    holds the default); a config that does not carry it reads the same."""
    monkeypatch.setattr(pm, "CONFIG", SimpleNamespace(analyzer=analyzer))
    assert pm._llm_decides() is want
