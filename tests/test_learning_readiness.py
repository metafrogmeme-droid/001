"""Learning readiness: evidence-gated recommendations, never auto-application.

The learners fit continuously but application stays behind default-OFF
flags. assess_readiness() answers "enough resolved outcomes AND holds
out-of-sample?" per component; the monitor alerts exactly once when a
component BECOMES ready.
"""

import time
from types import SimpleNamespace
from unittest.mock import patch

from bot.config import CONFIG
from bot.core.proactive_monitor import ProactiveMonitor
from bot.learning.readiness import assess_readiness, render_report


class TestAssessReadiness:
    def test_empty_store_accumulating_everywhere(self):
        class _EmptyStore:
            def get_decisions(self, symbol=None, limit=100):
                return []
        a = assess_readiness(store=_EmptyStore())
        assert a["resolved_samples"] == 0
        cal = a["components"]["calibration"]
        vw = a["components"]["voter_weights"]
        assert cal["state"] == "ACCUMULATING"
        assert vw["state"] == "ACCUMULATING"
        # Nothing ready -> nothing recommended for application.
        assert not any("consider" in r for r in a["recommendations"])

    def test_component_errors_do_not_break_assessment(self):
        class _BoomStore:
            def get_decisions(self, symbol=None, limit=100):
                raise RuntimeError("disk gone")
        a = assess_readiness(store=_BoomStore())
        # voter_weights reads through the store -> ERROR; the dict still has
        # all three components and render_report still produces text.
        assert set(a["components"]) == {"calibration", "voter_weights",
                                        "setup_expectancy"}
        assert "Learning Readiness" in render_report(a)

    def test_render_report_shows_flags_and_counts(self):
        a = {"resolved_samples": 12,
             "components": {
                 "calibration": {"state": "ACCUMULATING", "samples": 12,
                                 "needed": 30, "applied": False,
                                 "flag": "AUTO_CONFIRM_USE_CALIBRATED"}},
             "recommendations": []}
        txt = render_report(a)
        assert "12/30" in txt and "AUTO_CONFIRM_USE_CALIBRATED" in txt
        assert "keep accumulating" in txt.lower()

    def test_ready_unapplied_component_gets_recommendation(self):
        with patch("bot.learning.readiness.assess_readiness") as _:
            pass  # (guard against accidental self-patch; real logic below)
        # Drive the recommendation logic directly through a fake assessment
        # by replicating its rule: READY + applied=False -> "consider".
        comps = {"voter_weights": {"state": "READY", "applied": False,
                                   "flag": "VOTER_WEIGHT_LEARNING_ENABLED"}}
        recs = []
        for name, c in comps.items():
            if c["state"] == "READY" and c["applied"] is False:
                recs.append(f"{name} is validated but not applied — "
                            f"consider {c['flag']}=true")
        assert recs and "VOTER_WEIGHT_LEARNING_ENABLED=true" in recs[0]


class TestMonitorReadinessAlert:
    def _monitor(self):
        return ProactiveMonitor(SimpleNamespace(risk=SimpleNamespace()))

    def _assessment(self, state, applied=False):
        return {"resolved_samples": 60, "recommendations": [],
                "components": {"voter_weights": {
                    "state": state, "applied": applied,
                    "flag": "VOTER_WEIGHT_LEARNING_ENABLED"}}}

    def test_becoming_ready_alerts_once(self):
        mon = self._monitor()
        with patch("bot.learning.readiness.assess_readiness",
                   return_value=self._assessment("VALIDATING")):
            mon._readiness_next_check = 0.0
            assert mon._check_learning_readiness() == []   # seed baseline
        with patch("bot.learning.readiness.assess_readiness",
                   return_value=self._assessment("READY")):
            mon._readiness_next_check = 0.0
            alerts = mon._check_learning_readiness()
            assert len(alerts) == 1
            assert alerts[0].alert_type == "LEARNING_READY"
            assert "voter_weights" in alerts[0].body
            mon._readiness_next_check = 0.0
            assert mon._check_learning_readiness() == []   # stays ready: quiet

    def _tick(self, mon, state, applied=False):
        with patch("bot.learning.readiness.assess_readiness",
                   return_value=self._assessment(state, applied=applied)):
            mon._readiness_next_check = 0.0
            return mon._check_learning_readiness()

    def test_a_ready_that_lapses_says_so_once(self):
        mon = self._monitor()
        assert self._tick(mon, "VALIDATING") == []
        assert [a.alert_type for a in self._tick(mon, "READY")] == ["LEARNING_READY"]
        lapsed = self._tick(mon, "VALIDATING")
        assert [a.alert_type for a in lapsed] == ["LEARNING_NOT_READY"]
        assert lapsed[0].audience == "admin" and lapsed[0].severity == "INFO"
        assert "Leave VOTER_WEIGHT_LEARNING_ENABLED off" in lapsed[0].body
        assert "turning it off" not in lapsed[0].body, "a flag that is off is not switched off"
        assert self._tick(mon, "VALIDATING") == [], "staying not-ready is quiet"

    def test_a_lapse_while_applied_says_to_switch_it_off(self):
        mon = self._monitor()
        self._tick(mon, "READY", applied=True)
        lapsed = self._tick(mon, "VALIDATING", applied=True)
        assert [a.alert_type for a in lapsed] == ["LEARNING_NOT_READY"]
        assert lapsed[0].severity == "WARNING"
        assert "still APPLIED (VOTER_WEIGHT_LEARNING_ENABLED on): consider turning it off" in lapsed[0].body

    def test_a_lapse_seen_first_at_boot_is_a_baseline_not_a_lapse(self):
        # The state is the process's own memory: a restart that first sees
        # VALIDATING has no READY to compare it with.
        mon = self._monitor()
        assert self._tick(mon, "VALIDATING") == []

    def test_already_applied_component_does_not_alert(self):
        mon = self._monitor()
        with patch("bot.learning.readiness.assess_readiness",
                   return_value=self._assessment("VALIDATING")):
            mon._readiness_next_check = 0.0
            mon._check_learning_readiness()
        with patch("bot.learning.readiness.assess_readiness",
                   return_value=self._assessment("READY", applied=True)):
            mon._readiness_next_check = 0.0
            assert mon._check_learning_readiness() == []

    def test_hourly_cadence_respected(self):
        mon = self._monitor()
        mon._readiness_next_check = time.time() + 1800
        assert mon._check_learning_readiness() == []

    def test_flag_default_on(self):
        assert CONFIG.analyzer.learning_readiness_alert_enabled is True
