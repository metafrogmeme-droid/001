"""A control that reports success without having acted is the worst kind.

From the audit's "CONFIRMED, not remediated" tier. Three controls answered
success unconditionally, and each one is read by somebody deciding whether
they are safe:

- `/risk/halt` ran `engine.risk.emergency_halt(...)` under `except Exception:
  pass` and then returned `ok: True, circuit_breaker_active: True` no matter
  what happened. The EMERGENCY STOP reporting success on its own failure. Its
  docstring also promised "close all positions", which it has never done —
  that is `engine.emergency_halt_all`, behind the Telegram confirm button.

- The Telegram "Safe Mode" button changed NO state, told the operator
  "Safe mode is on. I'll only take high-confidence setups from here.", and
  wrote an audit record with result="OK" — sealing into the tamper-evident
  chain a claim that a risk control had been switched on. It sits between
  Pause and Stop Bot, both of which really act, so an operator reaching for
  "make me safer" could press it, be told they were safer, and not press the
  one that works.

- `handle_policy_clear` swallowed the exception and answered
  `ok: True, removed: False`. `dashboard.js` renders that exact pair as
  **"No policy was set."** — so a clear that THREW told the operator there had
  been no policy, while it stayed bound and stayed enforcing. The browser was
  already built for three outcomes; the producer only ever sent two.

NOT INCLUDED, deliberately: `warroom_bot.handle_callback` has the same
decorative Safe Mode line, and it is already recorded in
tests/unreachable_functions_baseline.txt. Fixing an unreachable surface is
fixing nothing, and counting it would inflate this change with work that
cannot be observed.
"""
from __future__ import annotations

import asyncio
import os
import secrets

# auth_routes raises at IMPORT time when JWT_SECRET is unset — a fail-closed
# guard doing its job, and importing api_bridge reaches it. Same line as
# tests/test_http_gate_parity.py, which imports the same module.
os.environ.setdefault("JWT_SECRET", secrets.token_hex(32))


# ── /risk/halt ────────────────────────────────────────────────────────────
#
# SUPERSEDED, and the six tests that stood here are why it took so long to see.
# The fix above made the halt read the breaker back instead of returning a
# literal -- and read it back from THIS PROCESS's engine. The bridge is not the
# bot: its engine is a copy, so a halt here tripped the copy, the copy said
# "tripped", and the bot kept trading. Every test here planted `_Engine(_Risk)`
# as `api_bridge.engine`, a stub whose halt DID take -- the one arrangement in
# which the bridge's own breaker is the bot's -- so "a successful halt reports
# success" was pinned as the contract over the process boundary it could not
# see. The endpoint refuses now and says nothing was halted
# (tests/test_the_bridge_is_a_reader_of_the_bots_state.py). What these still
# pin is the part that did not depend on the stub: it never reports success,
# it never calls a halt on the engine it is handed, and it never claims to
# close a position.

class _Risk:
    """A risk engine that records whether anything tried to halt it."""

    def __init__(self):
        self.calls = 0
        self.circuit_breaker_active = False

    def emergency_halt(self, reason):
        self.calls += 1
        self.circuit_breaker_active = True


class _Engine:
    def __init__(self, risk):
        self.risk = risk


def _call_halt(risk):
    """Drive the REAL endpoint function with a planted engine."""
    import api_bridge
    prev = api_bridge.engine
    api_bridge.engine = _Engine(risk)
    try:
        res = asyncio.run(api_bridge.risk_halt(_token="t", _rl=None))
    finally:
        api_bridge.engine = prev
    import json
    return res.status_code, json.loads(res.body)


def test_the_halt_never_reports_success():
    status, body = _call_halt(_Risk())
    assert status == 501
    assert body["ok"] is False and body["halted"] is False


def test_the_halt_never_halts_the_engine_it_is_handed():
    """Halting this process's engine is what produced the false 'tripped'."""
    risk = _Risk()
    _call_halt(risk)
    assert risk.calls == 0 and risk.circuit_breaker_active is False


def test_the_endpoint_no_longer_claims_to_close_positions():
    """It never did. It now halts nothing either, and says both."""
    _status, body = _call_halt(_Risk())
    assert body["closed_positions"] is False
    assert "Nothing was halted" in body["message"]


# ── Safe Mode ─────────────────────────────────────────────────────────────

def test_safe_mode_does_not_claim_to_be_on():
    from bot.skills.callback_handler import safe_mode_notice
    out = safe_mode_notice()
    assert "not wired" in out.lower()
    for lie in ("Safe mode is on", "high-confidence setups from here"):
        assert lie not in out, f"the card still claims: {lie!r}"


def test_safe_mode_points_at_the_controls_that_do_act():
    """A decoy between two working buttons is worse than a missing button."""
    from bot.skills.callback_handler import safe_mode_notice
    out = safe_mode_notice()
    assert "Pause" in out and "Stop Bot" in out


def _code_only(src: str) -> str:
    """Source with comments stripped.

    Needed because the comment explaining this very fix quotes `result="OK"`
    to say what it replaced. CLAUDE.md records four prior false failures from
    exactly this; the first draft of the assertion below was the fifth.
    """
    import io
    import tokenize
    out = []
    for tok in tokenize.generate_tokens(io.StringIO(src).readline):
        if tok.type != tokenize.COMMENT:
            out.append(tok.string)
    return " ".join(out)


def test_the_safe_mode_handler_does_not_audit_an_activation():
    """The audit chain is tamper-evident. What goes in it must be true."""
    import inspect
    import textwrap

    from bot.skills.telegram_handler import TelegramHandler
    src = _code_only(textwrap.dedent(
        inspect.getsource(TelegramHandler._handle_callback)))
    block = src.split('if data == "risk_safe_mode" :', 1)[1].split("return", 1)[0]
    assert 'result = "OK"' not in block, (
        "pressing a button that changes nothing still writes an OK activation "
        "record into the tamper-evident chain"
    )
    assert 'result = "NOOP"' in block


# ── policy clear ──────────────────────────────────────────────────────────

def test_a_failed_policy_clear_is_not_reported_as_no_policy(monkeypatch):
    """`ok:True, removed:False` renders in the browser as "No policy was set."

    The operator walks away believing nothing was bound. The policy is still
    bound and still enforcing.
    """
    import json

    from bot.web import user_gateway as ug

    class _Eng:
        def clear_intent_policy(self):
            raise RuntimeError("store locked")

    async def _guard(_request):
        return _Eng(), "42", None

    monkeypatch.setattr(ug, "_policy_op_guard", _guard)
    resp = asyncio.run(ug.handle_policy_clear(object()))
    body = json.loads(resp.body)
    assert body["ok"] is False, (
        "a clear that threw answered ok:True, which the dashboard renders as "
        "'No policy was set.'"
    )
    assert body["removed"] is None, "False here means 'there was none to clear'"
    assert resp.status == 500


def test_a_real_clear_and_a_real_nothing_to_clear_stay_distinguishable(monkeypatch):
    """The two honest outcomes must not collapse into each other."""
    import json

    from bot.web import user_gateway as ug

    for returned, expected in ((True, True), (False, False)):
        class _Eng:
            def clear_intent_policy(self, _r=returned):
                return _r

        async def _guard(_request, _e=_Eng()):
            return _e, "42", None

        monkeypatch.setattr(ug, "_policy_op_guard", _guard)
        body = json.loads(asyncio.run(ug.handle_policy_clear(object())).body)
        assert body["ok"] is True
        assert body["removed"] is expected


def test_the_policy_clear_failure_never_echoes_the_driver_message(monkeypatch):
    import json

    from bot.web import user_gateway as ug

    class _Eng:
        def clear_intent_policy(self):
            raise RuntimeError("dsn=postgres://user:pw@host/db locked")

    async def _guard(_request):
        return _Eng(), "42", None

    monkeypatch.setattr(ug, "_policy_op_guard", _guard)
    body = json.loads(asyncio.run(ug.handle_policy_clear(object())).body)
    assert "postgres://" not in repr(body)
    assert body["error"] == "RuntimeError"
