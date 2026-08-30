"""A user picked "conservative" and it changed the prose, not the money.

`user_profile_store` has held `risk_pref` for a while. The agent reads it,
mentions it, tailors its tone to it — and every position it opened for a
self-declared conservative user was the exact size it opened for an aggressive
one, because nothing downstream of the chat prompt had ever consulted the
field. A preference that changes what you are TOLD and not what is DONE is the
shape of promise this repository exists to stop making.

THE THREE THINGS THIS FILE ACTUALLY GUARDS
------------------------------------------
1. TIGHTEN-ONLY. `aggressive` is 1.0. A field writable from a web form must
   never be able to make a position bigger — the caps and breakers are what
   bound losses, and a self-declaration is not evidence that more is safe.
2. AN UNREADABLE PREFERENCE CHANGES NOTHING. This is the one place in this
   repo where "fail toward safety" is the wrong instinct: shrinking a position
   because a file read failed is a size nobody chose, decided by a disk error.
   Not knowing the preference is exactly the state every user was in before.
3. SHADOW IS VISIBLE. Default OFF means the engine computes the would-be
   multiplier and audits it on the channel the applied path uses. #36 is the
   precedent — two shadow deltas went to `logger.debug`, which has no handler,
   so shadow mode was indistinguishable from not having been built.
"""
from __future__ import annotations

import inspect

import pytest

from bot.core.user_sizing import MULTIPLIERS, multiplier_for_user, size_multiplier


@pytest.fixture
def _sizing_flag():
    """CONFIG.risk is a frozen dataclass; the repo toggles it the same way in
    tests/test_equity_throttle.py. Restored unconditionally so a failure here
    cannot leave live sizing enabled for every test that follows."""
    from bot.config import CONFIG

    def _set(on):
        object.__setattr__(CONFIG.risk, "user_risk_pref_sizing_enabled", on)

    old = CONFIG.risk.user_risk_pref_sizing_enabled
    try:
        yield _set
    finally:
        object.__setattr__(CONFIG.risk, "user_risk_pref_sizing_enabled", old)


class TestItCanOnlyEverTighten:
    @pytest.mark.parametrize("pref", sorted(MULTIPLIERS))
    def test_no_declared_preference_raises_size(self, pref):
        mult, _ = size_multiplier(pref)
        assert mult <= 1.0, (
            f"{pref!r} would ENLARGE a position. Nothing a user types into a "
            "web form may do that — the caps and breakers bound the loss, not "
            "the preference field")

    def test_conservative_actually_reduces(self):
        # The mirror failure: a "tighten-only" rule that tightens by nothing is
        # the original defect wearing a flag.
        mult, why = size_multiplier("conservative")
        assert mult < 1.0
        assert "conservative" in why

    def test_aggressive_is_exactly_neutral(self):
        assert size_multiplier("aggressive")[0] == 1.0

    def test_the_table_itself_cannot_grow_a_multiplier_above_one(self):
        assert all(v <= 1.0 for v in MULTIPLIERS.values()), MULTIPLIERS


class TestAbsentIsNotAReduction:
    @pytest.mark.parametrize("pref", [None, "", "   ", "moderate", "CONSERVATIVE-ISH"])
    def test_an_unusable_preference_changes_nothing(self, pref):
        mult, why = size_multiplier(pref)
        assert mult == 1.0
        assert "no reduction" in why

    def test_case_and_whitespace_still_resolve(self):
        assert size_multiplier("  Conservative ")[0] == MULTIPLIERS["conservative"]

    def test_an_unreadable_store_changes_nothing_and_says_so(self):
        class Broken:
            @staticmethod
            def get(_uid):
                raise OSError("disk")
        mult, why = multiplier_for_user("u1", store=Broken)
        assert mult == 1.0, (
            "a position shrank because a file could not be read — a size "
            "nobody chose, decided by a disk error")
        assert "unreadable" in why, (
            "an unreadable profile and a user with no preference must not "
            "produce the same audit line; only one is a statement about them")

    def test_no_user_id_changes_nothing(self):
        # The shared operator engine's _person_user_id is "" — the default
        # path must be byte-identical to before this existed.
        assert multiplier_for_user("") == (1.0, "no user: no reduction")

    def test_a_user_with_a_preference_gets_it(self):
        class Store:
            @staticmethod
            def get(_uid):
                return {"risk_pref": "conservative"}
        assert multiplier_for_user("u1", store=Store)[0] < 1.0


class TestTheEngineConsultsIt:
    """Present code that is never reached is #999, one subsystem over."""

    def _src(self):
        from bot.risk.risk_engine import RiskEngine
        return inspect.getsource(RiskEngine._evaluate_locked)

    def test_the_sizing_chain_calls_it(self):
        src = self._src()
        assert "multiplier_for_user" in src, (
            "user_sizing is a pure module nothing calls — exactly the state "
            "token_dossier, presale_claims and deployer_history were in")
        assert 'getattr(self, "_person_user_id", "")' in src, (
            "the engine must ask for the identity it was bound with; a "
            "hardcoded user would size everybody the same way again")

    def test_it_applies_only_when_the_flag_is_on(self):
        src = self._src()
        i = src.index("multiplier_for_user")
        block = src[i:i + 1200]
        assert "CONFIG.risk.user_risk_pref_sizing_enabled" in block
        assert "position_usd *= _pref_mult" in block

    def test_shadow_is_audited_where_shadow_can_be_seen(self):
        src = self._src()
        assert 'action="user_risk_pref_sizing", result="SHADOW"' in src, (
            "#36: two shadow deltas went to logger.debug, which has no "
            "handler, so shadow mode was invisible and never got evaluated")
        assert "logger.debug" not in src.split("multiplier_for_user")[1][:1200]

    def test_a_lookup_fault_never_rejects_a_trade(self):
        src = self._src()
        # Sliced to the next block, not a fixed width. The first draft took
        # 1600 characters and ran into the drawdown-recovery branch below,
        # found ITS `failed.append`, and reported this block as able to reject
        # a trade — the window answering a different question than the one
        # asked, exactly as test_user_preflight_parity records.
        i = src.index("multiplier_for_user")
        block = src[i:src.index("# Drawdown recovery mode", i)]
        assert "except Exception" in block
        assert "failed.append" not in block, (
            "a preferences lookup must never be able to reject a trade")

    def test_the_flag_is_off_by_default_and_visible_in_flags(self):
        from bot.config import CONFIG
        from bot.core.flag_status import audit_flag_report
        assert CONFIG.risk.user_risk_pref_sizing_enabled is False, (
            "a sizing change must be staged, not defaulted on")
        names = {env for _t, rows in audit_flag_report() for env, _l, _o in rows}
        assert "USER_RISK_PREF_SIZING_ENABLED" in names, (
            "a gated flag missing from /flags is a flag the operator cannot "
            "find out the state of")


class TestTheSizeActuallyChanges:
    """Source scans prove the code is PRESENT. This proves it is REACHED.

    #999 is the cautionary case and it was a source-scanned card that rendered
    zero times in production. So the multiplier is driven through the real
    `RiskEngine.evaluate` twice — once with a preference on file and once
    without — and the assertion is on the number the caller gets back.
    """

    @staticmethod
    def _engine(user_id=""):
        import os
        import tempfile

        from bot.risk.portfolio import PortfolioTracker
        from bot.risk.risk_engine import RiskEngine
        state = os.path.join(tempfile.mkdtemp(prefix="rc-pref-"), "risk_state.json")
        eng = RiskEngine(PortfolioTracker(initial_balance=10_000.0),
                         state_file=state)
        if user_id:
            eng.set_person_identity(user_id)
        return eng

    @staticmethod
    def _idea():
        from bot.utils.models import Direction, TradeIdea
        return TradeIdea(asset="BTC/USDT", direction=Direction.LONG,
                         entry_price=100.0, stop_loss=95.0, take_profit=110.0,
                         confidence=0.9, reasoning="test")

    @pytest.fixture
    def _profile(self, tmp_path, monkeypatch):
        monkeypatch.setenv("RUNECLAW_USER_PROFILE_FILE",
                           str(tmp_path / "profiles.json"))
        from bot.core import user_profile_store as ups
        ups.set_profile("careful", {"risk_pref": "conservative"})
        ups.set_profile("bold", {"risk_pref": "aggressive"})
        yield

    def test_a_conservative_user_gets_a_smaller_position(self, _profile, _sizing_flag):
        _sizing_flag(True)
        base = self._engine("bold").evaluate(self._idea()).position_size_usd
        small = self._engine("careful").evaluate(self._idea()).position_size_usd
        assert base > 0, "the baseline sized to nothing; the test proves nothing"
        assert small < base, (
            "a self-declared conservative user was sized identically to an "
            "aggressive one — the preference changed the prose, not the money")
        assert small == pytest.approx(base * MULTIPLIERS["conservative"], rel=1e-6)

    def test_the_flag_off_leaves_the_size_alone(self, _profile):
        from bot.config import CONFIG
        assert CONFIG.risk.user_risk_pref_sizing_enabled is False
        base = self._engine("bold").evaluate(self._idea()).position_size_usd
        same = self._engine("careful").evaluate(self._idea()).position_size_usd
        assert same == base, "a default-OFF flag changed live sizing"

    def test_the_operator_engine_is_untouched(self, _profile, _sizing_flag):
        # No person identity bound → _person_user_id is "" → no lookup, no
        # change, whatever the flag says. Auto-trade and the operator path run
        # on this engine.
        _sizing_flag(True)
        on = self._engine().evaluate(self._idea()).position_size_usd
        _sizing_flag(False)
        off = self._engine().evaluate(self._idea()).position_size_usd
        assert off == on

    def test_an_unreadable_profile_store_leaves_the_size_alone(
            self, _sizing_flag, monkeypatch):
        from bot.core import user_sizing
        _sizing_flag(True)
        monkeypatch.setattr(
            user_sizing, "multiplier_for_user",
            lambda *_a, **_k: (_ for _ in ()).throw(OSError("disk")))
        check = self._engine("careful").evaluate(self._idea())
        assert check.position_size_usd > 0
        assert any("USER_RISK_PREF: skipped" in c for c in check.checks_passed), (
            "a lookup fault has to be recorded — a size that silently stopped "
            "being adjusted is the flag looking enabled and doing nothing")
