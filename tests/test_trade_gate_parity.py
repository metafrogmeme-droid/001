"""Six surfaces answered "can we trade" and each checked a different subset.

The pre-execute gate refuses a new entry on FIVE conditions. /start checked
ONE of them — and not the one that fired in either of the two incidents:

    2026-07-29  the warning-rate breaker rejected a live trade while /health
                said circuit_breaker_active: false. trading_blocked_by was
                added and wired into _banner, /status, the /risk tile and the
                chat prompt.

    2026-08-01  a transport blip marked venue auth down; entries were halted
                and NOTHING said so. The operator's /start read "Active |
                LIVE". The auth gate was wired into the chat prompt.

Both widenings reached the surfaces someone thought of. /start — the most-used
command, and the exact card in the screenshot of the second incident — was
never one of them. It also read the SHARED breaker, so a user whose OWN
daily-loss breaker was open was told they were Active.

    A GATE WITH FIVE CONDITIONS AND SIX RENDERINGS OF IT WILL DISAGREE WITH
    ITSELF. THE FIX FOR A CLAIM MADE IN SIX PLACES IS ONE PLACE.

These tests hold the helper to the gate's actual condition list, and hold
every surface to the helper.
"""
from __future__ import annotations

from types import SimpleNamespace as NS

import pytest

from bot.core.trade_gate import (UNREADABLE, entry_gate, gate_label,
                                 gate_sentence)
from tests.source_scan import code_only



def _risk(blocked: str = "", cb: bool = False):
    return NS(trading_blocked_by=blocked, circuit_breaker_active=cb)


def _engine(*, halted=False, shared=None, own=None, auth_ok=True, detail=""):
    """An engine shaped like the real one's read surface.

    `own=None` means per-user is OFF: risk_for returns the SHARED engine, the
    production default.
    """
    shared = shared if shared is not None else _risk()
    return NS(_halted=halted,
              risk=shared,
              risk_for=lambda uid="": (own if own is not None else shared),
              live_auth_healthy=lambda uid="": auth_ok,
              _live_auth_detail={"": detail, "u1": detail})


class TestEveryConditionTheGateChecks:
    """One test per arm of the pre-execute gate in engine.confirm_*."""

    def test_a_clear_engine_is_clear(self):
        g = entry_gate(_engine(), live=True)
        assert g == {"blocked": False, "unknown": False, "reasons": []}
        assert gate_label(g) == "Active"
        assert gate_sentence(g) == ""

    def test_the_kill_switch_blocks(self):
        g = entry_gate(_engine(halted=True), live=True)
        assert g["blocked"]
        assert "kill switch engaged" in g["reasons"]

    def test_the_shared_breaker_blocks(self):
        g = entry_gate(_engine(shared=_risk("daily_loss")), live=True)
        assert g["blocked"] and g["reasons"] == ["daily_loss"]

    def test_the_warning_rate_breaker_blocks(self):
        # The 2026-07-29 gate: outside circuit_breaker_active entirely.
        g = entry_gate(_engine(shared=_risk("warning_rate:api", cb=False)),
                       live=True)
        assert g["blocked"], "the breaker that started this whole rule"

    def test_the_callers_own_breaker_blocks(self):
        # Per-user ON: this user's daily-loss breaker is open, the shared one
        # is not. /start read the shared one and told them "Active".
        g = entry_gate(_engine(shared=_risk(), own=_risk("daily_loss")),
                       "u1", live=True)
        assert g["blocked"] and g["reasons"] == ["daily_loss"]

    def test_the_venue_auth_halt_blocks(self):
        g = entry_gate(_engine(auth_ok=False), live=True)
        assert g["blocked"]
        assert any("venue auth" in r for r in g["reasons"])

    def test_the_auth_reason_carries_its_recovery_route(self):
        # A halt reported with no way to clear it is a dead end — which is
        # exactly what the operator hit. The flag only resets on preflight.
        g = entry_gate(_engine(auth_ok=False), live=True)
        assert "restart" in " ".join(g["reasons"])

    def test_the_auth_detail_is_included_when_there_is_one(self):
        g = entry_gate(_engine(auth_ok=False, detail="40006 invalid key"),
                       live=True)
        assert "40006" in " ".join(g["reasons"])

    def test_the_auth_gate_is_live_only(self):
        # In paper there is no venue to authenticate against; reporting a halt
        # would be a false alarm on an account that cannot be affected.
        assert not entry_gate(_engine(auth_ok=False), live=False)["blocked"]


class TestItMatchesTheGateItDescribes:
    def test_the_gate_source_still_checks_exactly_these(self):
        """If the gate grows a sixth condition, this fails and names the job.

        The failure mode being guarded is silent divergence: a new refusal
        added to the money path that no status surface learns about. That is
        how both prior incidents happened.
        """
        src = code_only(open("bot/core/engine.py", encoding="utf-8").read())
        i = src.index("_user_breaker = False")
        j = src.index("result = await executor.execute(", i)
        gate = src[i:j]
        for cond in ("self._halted",
                     "self.risk.circuit_breaker_active",
                     "_user_breaker",
                     "self.risk_for(user_id).circuit_breaker_active",
                     "self.live_auth_healthy(user_id)"):
            assert cond in gate, (
                f"the pre-execute gate no longer reads {cond!r} — "
                f"bot/core/trade_gate.py mirrors this list and must be "
                f"updated with it"
            )

    def test_no_other_refusal_hides_in_the_gate(self):
        # Counting `return "Trade REJECTED` inside the window: a new one means
        # a new refusal reason that no surface reports.
        src = code_only(open("bot/core/engine.py", encoding="utf-8").read())
        i = src.index("_user_breaker = False")
        j = src.index("result = await executor.execute(", i)
        assert src[i:j].count('return "Trade REJECTED') == 1
        assert src[i:j].count('return ("Trade REJECTED') == 1


class TestUnknownIsNotRoundedToEither:
    """Rounding up recreates the bug; rounding down is the false alarm."""

    class _Hostile:
        @property
        def trading_blocked_by(self):
            raise RuntimeError("nope")

        @property
        def circuit_breaker_active(self):
            raise RuntimeError("nope")

    def test_an_unreadable_breaker_is_unknown_not_clear(self):
        g = entry_gate(_engine(shared=self._Hostile()), live=True)
        assert g["unknown"]
        assert not g["blocked"], "unknown is not a positive blocking reading"
        assert gate_label(g) == "Unknown"

    def test_unknown_says_it_cannot_confirm(self):
        g = entry_gate(_engine(shared=self._Hostile()), live=True)
        s = gate_sentence(g)
        assert "cannot confirm" in s
        assert "refused" not in s, "unknown must not be phrased as a verdict"

    def test_a_confirmed_block_does_not_hedge(self):
        # Blocked and unknown are independent. A real breaker plus one
        # unreadable field is still blocked, and the user gets a straight
        # answer rather than a hedge about the part that failed to read.
        g = entry_gate(_engine(halted=True, shared=self._Hostile()), live=True)
        assert g["blocked"] and g["unknown"]
        assert gate_label(g) == "Paused"
        assert "kill switch engaged" in gate_sentence(g)

    def test_an_unreadable_auth_probe_is_unknown(self):
        e = _engine()
        e.live_auth_healthy = lambda uid="": (_ for _ in ()).throw(
            RuntimeError("nope"))
        assert entry_gate(e, live=True)["unknown"]

    def test_a_narrow_flag_that_is_false_does_not_prove_clear(self):
        # circuit_breaker_active answers LESS than trading_blocked_by, so a
        # False reading of it cannot stand in for the one that failed.
        class Half:
            circuit_breaker_active = False

            @property
            def trading_blocked_by(self):
                raise RuntimeError("nope")

        assert entry_gate(_engine(shared=Half()), live=True)["unknown"]

    def test_a_narrow_flag_that_is_true_still_blocks(self):
        class Half:
            circuit_breaker_active = True

            @property
            def trading_blocked_by(self):
                raise RuntimeError("nope")

        g = entry_gate(_engine(shared=Half()), live=True)
        assert g["blocked"] and "circuit breaker open" in g["reasons"]


class TestItNeverTakesDownTheCardItAnnotates:
    @pytest.mark.parametrize("bad", [None, NS(), 7, "engine", []])
    def test_junk_input_is_unknown_not_an_exception(self, bad):
        g = entry_gate(bad, live=True)
        assert g["unknown"] and not g["blocked"]

    def test_a_raising_risk_for_is_absorbed(self):
        e = _engine()
        e.risk_for = lambda uid="": (_ for _ in ()).throw(RuntimeError("nope"))
        # The shared engine was still read, so this is a complete answer for
        # the default (per-user OFF) deployment.
        assert not entry_gate(e, live=True)["blocked"]

    @pytest.mark.parametrize("g", [{}, {"reasons": None}, {"blocked": True}])
    def test_the_renderers_tolerate_a_partial_dict(self, g):
        assert isinstance(gate_label(g), str)
        assert isinstance(gate_sentence(g), str)


class TestOneCauseIsReportedOnce:
    def test_the_shared_and_user_engine_are_deduped_when_identical(self):
        # PER_USER_LIVE_ENABLED off resolves risk_for to the SAME object.
        # Listing "daily_loss; daily_loss" reads like two problems.
        g = entry_gate(_engine(shared=_risk("daily_loss")), "u1", live=True)
        assert g["reasons"] == ["daily_loss"]

    def test_two_real_engines_latched_on_the_same_cause_say_it_once(self):
        g = entry_gate(_engine(shared=_risk("daily_loss"),
                               own=_risk("daily_loss")), "u1", live=True)
        assert g["reasons"] == ["daily_loss"]

    def test_distinct_causes_are_both_named(self):
        g = entry_gate(_engine(halted=True, shared=_risk("daily_loss"),
                               own=_risk("warning_rate:api")), "u1", live=True)
        assert len(g["reasons"]) == 3
        assert "kill switch engaged" in gate_sentence(g)
        assert "warning_rate:api" in gate_sentence(g)


class TestEverySurfaceReadsTheOneHelper:
    """The property that stops this recurring a fourth time."""

    def _src(self) -> str:
        # Every file the handler class is made of, not the handler's alone: /risk
        # lives in the portfolio mixin since the handler split, and a scan of
        # one file reads a move as the surface no longer calling the helper.
        from tests.source_scan import handler_sources
        return "\n".join(code_only(p.read_text(encoding="utf-8")) for p in handler_sources())

    def test_start_no_longer_reads_one_breaker(self):
        src = self._src()
        # Anchored on CODE, not a comment: code_only blanks comments, so a
        # comment anchor finds nothing. Fourth time a source scan in this
        # session has matched the file's layout instead of its content.
        i = src.index('role = record.get("role", "trader")')
        window = src[i:i + 2500]
        assert "entry_gate(self.engine" in window, (
            "/start is the card in the incident screenshot and the last "
            "surface to still check a single condition"
        )
        assert "cb_active = self.engine.risk.circuit_breaker_active" not in src

    def test_start_reports_the_cause_not_only_the_colour(self):
        src = self._src()
        assert "gate_sentence(_gate)" in src, (
            "a red headline with no cause left the operator to discover the "
            "reason by attempting a trade"
        )

    def test_start_has_a_third_state(self):
        src = self._src()
        assert "gate_label(_gate)" in src
        # `X or True` is vacuous — written once here and caught on re-read.
        # The real property: the icon is a LOOKUP with no green fallback, so
        # a label the map does not know cannot come out green.
        assert '{"Active": "\\U0001f7e2", "Paused": "\\U0001f534"}' in src, (
            "an unreadable gate must not fall through to the green icon"
        )
        i = src.index('{"Active": "\\U0001f7e2"')
        assert '.get(\n            _gate_label, "⚪")' in src[i:i + 200], (
            "the default arm is what makes Unknown visible"
        )

    def test_the_bilingual_label_cannot_contradict_the_english_one(self):
        src = self._src()
        i = src.index("status_label_zh")
        window = src[max(0, i - 400):i + 400]
        assert '_gate_label != "Active"' in window

    @pytest.mark.parametrize("anchor,name", [
        ("def _banner(", "the banner"),
        ("async def _cmd_status(", "/status"),
        ("async def _cmd_risk(", "/risk"),
    ])
    def test_the_other_surfaces_route_through_it_too(self, anchor, name):
        src = self._src()
        i = src.index(anchor)
        assert "entry_gate(" in src[i:i + 4000], f"{name} still rolls its own"

    def test_no_surface_still_hand_rolls_the_subset(self):
        src = self._src()
        # The two shapes that each fix left behind in the OTHER surfaces.
        assert 'getattr(self.engine.risk, "trading_blocked_by"' not in src, (
            "a surface reading the shared engine's field directly cannot see "
            "the caller's own breaker, the kill switch or the auth halt"
        )

    def test_the_scan_dashboard_chip_uses_it(self):
        src = code_only(open("bot/skills/scan_skill.py", encoding="utf-8").read())
        assert "entry_gate(engine)" in src
        assert 'getattr(risk, "trading_blocked_by"' not in src


class TestTheChatPromptStillReportsTheHalt:
    """#1030's fix, now sourced from the helper rather than hand-rolled."""

    def _src(self) -> str:
        # Every file the handler class is made of, not the handler's alone: /risk
        # lives in the portfolio mixin since the handler split, and a scan of
        # one file reads a move as the surface no longer calling the helper.
        from tests.source_scan import handler_sources
        return "\n".join(code_only(p.read_text(encoding="utf-8")) for p in handler_sources())

    def test_the_prompt_names_the_halt(self):
        assert "NEW ENTRIES HALTED" in self._src()

    def test_the_prompt_lists_the_causes(self):
        src = self._src()
        i = src.index("NEW ENTRIES HALTED")
        window = src[max(0, i - 400):i + 300]
        assert '_g["reasons"]' in window

    def test_cb_keeps_its_old_meaning_for_existing_readers(self):
        # Deliberately NOT widened: a dozen call sites drive alerts and resume
        # logic off circuit_breaker_active, and redefining the field under
        # them would trade one wrong answer for another.
        src = self._src()
        assert "CB={'ON' if cb else 'OFF'}" in src
        i = src.index("CB={'ON' if cb else 'OFF'}")
        assert "cb = self.engine.risk.circuit_breaker_active" in src[i - 400:i]

    def test_the_model_is_told_not_to_round_unknown_up(self):
        src = self._src()
        assert "ENTRY GATE STATUS UNKNOWN" in src
        i = src.index("ENTRY GATE STATUS UNKNOWN")
        assert "do not tell the " in src[i:i + 300]


class TestNothingUnscrubbedReachesASurface:
    """F-15, widened blast radius.

    `_live_auth_detail` holds `str(exc)` from the preflight, stored
    unredacted. Its only reader used to be the chat system prompt. Routing it
    to /start, the /risk card and the WEBSITE's status chip is a real widening
    of where an unscrubbed ccxt exception can land — so it is scrubbed at the
    source, once, for every consumer.
    """

    def _reason(self, detail: str) -> str:
        return " ".join(entry_gate(
            _engine(auth_ok=False, detail=detail), live=True)["reasons"])

    def test_a_key_value_secret_is_redacted(self):
        out = self._reason("bitget failed apiKey=abcd1234SECRET")
        assert "abcd1234SECRET" not in out
        assert "REDACTED" in out

    def test_a_url_query_string_is_dropped(self):
        out = self._reason("GET https://api.bitget.com/v2/acct?secret=hunter2")
        assert "hunter2" not in out
        assert "api.bitget.com" in out, "which venue is the diagnostic"

    def test_markup_cannot_reach_a_surface(self):
        # An unescaped `<` breaks parse_mode=HTML and the message fails to
        # send — instrumentation taking down the surface it annotates.
        out = self._reason("<b>bad</b> & worse")
        assert "<" not in out and ">" not in out and "&" not in out

    def test_a_manual_halt_reason_is_scrubbed_too(self):
        # /halt <reason> is operator-typed and lands in _circuit_trip_cause.
        g = entry_gate(_engine(shared=_risk("manual: <script>x</script>")),
                       live=True)
        joined = " ".join(g["reasons"])
        assert "<" not in joined and ">" not in joined

    def test_a_scrub_failure_emits_nothing_rather_than_the_raw_string(self):
        class Hostile(str):
            def __str__(self):
                raise RuntimeError("nope")

        g = entry_gate(_engine(auth_ok=False, detail=Hostile("secret")),
                       live=True)
        assert "secret" not in " ".join(g["reasons"])
        assert g["blocked"], "the halt itself must still be reported"

    def test_the_detail_is_bounded(self):
        out = self._reason("x" * 500)
        assert len(out) < 200, "an unbounded label on a status card"

    def test_a_blocked_reason_that_scrubs_to_nothing_still_says_blocked(self):
        g = entry_gate(_engine(shared=_risk("<>")), live=True)
        assert g["blocked"] and g["reasons"] == ["blocked"], (
            "sanitising a cause away must not silently unblock the headline"
        )


class TestItDecidesNothing:
    def test_the_money_path_does_not_import_the_display_helper(self):
        # This module reports what the gate would do. If the gate ever started
        # ASKING it, a display bug would become a trading bug.
        src = code_only(open("bot/core/engine.py", encoding="utf-8").read())
        assert "trade_gate" not in src, (
            "the pre-execute gate must keep reading the raw fields itself"
        )

    def test_the_helper_does_not_write_anything(self):
        src = code_only(open("bot/core/trade_gate.py", encoding="utf-8").read())
        for forbidden in ("set_live_auth_status", "emergency_halt",
                          "reset_circuit_breaker", "setattr("):
            assert forbidden not in src, f"a status reader called {forbidden}"

    def test_unreadable_is_exported_for_callers_that_want_the_word(self):
        assert isinstance(UNREADABLE, str) and UNREADABLE
