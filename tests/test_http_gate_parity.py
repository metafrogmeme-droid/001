"""/health said "" while every bot card said Paused.

#1031 put all five conditions the pre-execute gate checks into
bot/core/trade_gate.py and routed every BOT surface through it — /start,
_banner, /status, the /risk tile, the chat prompt, the web dashboard chip.

It did not route the HTTP ones. api_bridge kept

    "trading_blocked_by": engine.risk.trading_blocked_by

which is the shared engine's field alone: the kill switch and the venue auth
halt sit outside it. So the divergence that PR existed to remove did not go
away, it MOVED — to /health, which is the exact surface the operator checked
during the 2026-07-29 incident and was told the breaker was clear.

    ROUTING FIVE SURFACES THROUGH ONE HELPER AND LEAVING THE SIXTH IS NOT A
    SMALLER VERSION OF THE FIX. IT IS THE SAME BUG WITH A SHORTER LIST.

Sixth time this session, and the second time in two PRs, that the answer to
"which other surface makes this claim" was one I had just finished editing
around.

THE PUBLIC/PRIVATE SPLIT

/health takes no token. /risk/status does. That trading is halted, and which
CLASS of gate did it, is the kind of operational status /health already
publishes. The venue's own error text is not: it is the one part of a reason
that is externally sourced and unbounded in content. Scrubbing it made it
safe to show a user; it did not make it public.
"""
from __future__ import annotations

import os
import secrets
from types import SimpleNamespace as NS

# api_bridge refuses to import without a JWT secret, which is why nothing in
# this suite had ever imported it — every assertion about that file was a
# source scan, and the two helpers below are the first behaviour in it that
# is actually executed by a test.
#
# GENERATED, never a literal. A checked-in secret-shaped constant is a
# gitleaks finding and a bad example to copy, even in a test.
os.environ.setdefault("JWT_SECRET", secrets.token_hex(32))

from bot.core.trade_gate import entry_gate
from tests.source_scan import code_only

SRC = code_only(open("api_bridge.py", encoding="utf-8").read())

VENUE_DETAIL = "bitget GET api.bitget.com/api/v3/market/instruments"


def _engine(*, halted=False, blocked="", auth_ok=True, detail=VENUE_DETAIL):
    risk = NS(trading_blocked_by=blocked, circuit_breaker_active=False,
              warning_rate_breaker_active=False)
    return NS(_halted=halted, risk=risk, risk_for=lambda uid="": risk,
              live_auth_healthy=lambda uid="": auth_ok,
              _live_auth_detail={"": detail})


class TestTheDetailIsNotPublic:
    def test_the_public_form_names_the_category(self):
        g = entry_gate(_engine(auth_ok=False), live=True,
                       include_detail=False)
        assert g["blocked"]
        assert any("venue auth" in r for r in g["reasons"]), (
            "that trading is halted is legitimate operational status"
        )

    def test_the_public_form_drops_the_venue_text(self):
        g = entry_gate(_engine(auth_ok=False), live=True,
                       include_detail=False)
        joined = " ".join(g["reasons"])
        assert "bitget" not in joined and "api.bitget.com" not in joined, (
            "an unauthenticated endpoint must not echo the venue's response"
        )

    def test_the_gated_form_keeps_it(self):
        g = entry_gate(_engine(auth_ok=False), live=True)
        assert "bitget" in " ".join(g["reasons"]), (
            "the operator behind a token needs the actual diagnostic"
        )

    def test_dropping_the_detail_never_drops_the_halt(self):
        # The failure that would matter: quietening the reason into silence
        # and reporting a halted engine as clear.
        for detail in ("", VENUE_DETAIL, "x" * 400):
            g = entry_gate(_engine(auth_ok=False, detail=detail), live=True,
                           include_detail=False)
            assert g["blocked"], f"halt lost with detail={detail[:20]!r}"

    def test_the_categories_are_unaffected(self):
        # Only the venue text is externally sourced; the breaker causes are
        # internal tokens and stay on both forms.
        for kw in ({}, {"include_detail": False}):
            g = entry_gate(_engine(blocked="daily_loss"), live=True, **kw)
            assert g["reasons"] == ["daily_loss"]


# ── the bridge's two endpoints ─────────────────────────────────────────────
#
# RE-POINTED. These pinned both endpoints to `entry_gate(engine)` -- the
# bot-side gate run over THIS PROCESS's engine, which is a copy of the bot's
# loaded when the bridge started and never refreshed. So with the bot halted
# the bridge said clear (tests/test_the_bridge_is_a_reader_of_the_bots_state.py).
# The divergence this file was written to stop "moving to HTTP" had been on
# HTTP the whole time, one process over. Both endpoints read what the BOT
# saved now, through one helper; the rest of the rule they guarded holds, and
# is what is asserted below.

def _saved_state(tmp_path, *, circuit_open, cause=""):
    import json
    f = tmp_path / "combined_state.json"
    f.write_text(json.dumps({"risk": {
        "circuit_open": circuit_open, "consecutive_losses": 0,
        "last_loss_time": None, "circuit_breaker_trips": 0,
        "circuit_trip_cause": cause, "circuit_trip_day": ""}}))
    return NS(_combined_state_file=str(f))


class TestBothEndpointsReadTheBotsSavedBreaker:
    def test_both_endpoints_use_the_one_reader(self):
        for fn in ("async def health(", "async def risk_status("):
            i = SRC.index(fn)
            body = SRC[i:SRC.index("\n@app.", i)]
            assert "_bot_breaker_fields()" in body, fn

    def test_neither_endpoint_reads_this_processs_engine(self):
        assert "engine.risk.trading_blocked_by" not in SRC
        assert "entry_gate(" not in SRC, (
            "a gate run over this process's engine describes a copy of the "
            "bot, not the bot")

    def test_neither_can_carry_venue_text(self, tmp_path):
        # The public/private split this file drew rested on the venue-auth
        # reason carrying the venue's own error text. The bridge cannot read
        # that gate at all now, and what it reads from the saved block goes
        # through the bot's validator, which keeps its six typed fields and
        # nothing else -- so free text planted beside them never reaches the
        # wire, on the endpoint that takes no token.
        import json

        import api_bridge
        f = tmp_path / "combined_state.json"
        f.write_text(json.dumps({"risk": {
            "circuit_open": True, "circuit_trip_cause": "manual",
            "reason": VENUE_DETAIL, "last_error": VENUE_DETAIL}}))
        saved = api_bridge.engine
        try:
            api_bridge.engine = NS(_combined_state_file=str(f))
            out = api_bridge._bot_breaker_fields()
        finally:
            api_bridge.engine = saved
        assert out["trading_blocked_by"] == "manual"
        assert "bitget" not in repr(out)

    def test_risk_status_is_still_token_gated(self):
        i = SRC.index("async def risk_status(")
        assert "Depends(require_dashboard_token)" in SRC[i:i + 200]

    def test_health_is_still_the_unauthenticated_one(self):
        i = SRC.index("async def health(")
        assert "Depends(" not in SRC[i:i + 200]


class TestTheStatusFieldCannotBreakTheEndpoint:
    """A health endpoint that 500s on a risk hiccup reports the wrong outage."""

    def _fields(self, engine):
        import api_bridge
        saved = api_bridge.engine
        try:
            api_bridge.engine = engine
            return api_bridge._bot_breaker_fields()
        finally:
            api_bridge.engine = saved

    def test_a_missing_engine_is_unknown_not_clear(self):
        # A fabricated "" would read as "trading is fine".
        out = self._fields(None)
        assert out["trading_blocked_by"] == ""
        assert out["trading_gate_unknown"] is True
        assert "circuit_breaker_active" not in out

    def test_a_hostile_engine_is_unknown_not_an_exception(self):
        class Hostile:
            @property
            def _combined_state_file(self):
                raise RuntimeError("nope")

        out = self._fields(Hostile())
        assert out["trading_gate_unknown"] is True
        assert out["trading_blocked_by"] == ""
        assert out["circuit_breaker_read"] == "unreadable"

    def test_a_clear_breaker_is_told_apart_from_an_unreadable_one(self, tmp_path):
        # The old assertion: "a clear engine must not look like an unreadable
        # one, or the flag means nothing". Still the rule; the field that
        # carries it is `circuit_breaker_read`, because from this process the
        # gate as a whole is never complete.
        clear = self._fields(_saved_state(tmp_path, circuit_open=False))
        assert clear["circuit_breaker_read"] == "read"
        assert clear["circuit_breaker_active"] is False
        unread = self._fields(NS(_combined_state_file=str(tmp_path / "nope")))
        assert unread["circuit_breaker_read"] == "absent"
        assert "circuit_breaker_active" not in unread

    def test_a_blocked_breaker_reaches_the_wire(self, tmp_path):
        out = self._fields(_saved_state(tmp_path, circuit_open=True,
                                        cause="manual"))
        assert out["trading_blocked_by"] == "manual"
        assert out["circuit_breaker_active"] is True

    def test_the_unknown_flag_is_published_by_both(self):
        # One helper, and both endpoints spread it.
        assert SRC.count("**_bot_breaker_fields()") >= 2


class TestTheCorrectedRationale:
    """A comment claimed something the code does not do.

    #1031 justified scrubbing `trading_blocked_by` by saying a manual trip
    carries the operator's `/halt <reason>` text. It does not:
    `emergency_halt(reason)` passes `reason` to the AUDIT LOG and lets `cause`
    default to "manual", so the free text never reaches the field.

    The scrub is still correct as defence in depth. The stated reason was not,
    and a wrong rationale left in the tree is something a future reader
    relies on.
    """

    def test_emergency_halt_does_not_pass_the_reason_as_the_cause(self):
        src = code_only(open("bot/risk/risk_engine.py", encoding="utf-8").read())
        i = src.index("def emergency_halt(self, reason: str)")
        body = src[i:i + 400]
        assert "_trip_circuit_breaker(reason)" in body
        assert "cause=" not in body, (
            "if a cause is ever threaded through here, operator free text "
            "DOES reach trading_blocked_by and the scrub stops being "
            "defence in depth"
        )

    def test_the_trip_cause_defaults_to_a_fixed_token(self):
        src = code_only(open("bot/risk/risk_engine.py", encoding="utf-8").read())
        assert 'def _trip_circuit_breaker(self, reason: str, cause: str = "manual")' in src

    def test_the_scrub_is_still_in_place(self):
        # Kept deliberately: a no-op on a fixed token, and the cheapest
        # insurance if a future caller starts passing `cause` through.
        src = code_only(open("bot/core/trade_gate.py", encoding="utf-8").read())
        assert "_safe_detail(blocked_by" in src

    def test_the_helper_no_longer_claims_the_halt_reason_reaches_it(self):
        raw = open("bot/core/trade_gate.py", encoding="utf-8").read()
        assert "manual trip carries `/halt <reason>`" not in raw
