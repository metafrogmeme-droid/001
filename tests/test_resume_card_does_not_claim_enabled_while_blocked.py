"""The resume card said ENABLED / CLEAR while the warning-rate breaker refused entries.

Seen live on 2026-09-03 at 13:59 UTC+2: /resume printed "Trading ENABLED /
Circuit Breaker CLEAR"; /start one minute later said "New entries are
refused: warning_rate:engine_tick_failure"; /status said HALTED.
reset_circuit_breaker() clears the circuit breaker and nothing else; the
warning-rate breaker and the loss-streak gate sit outside it, and the card's
only other input, pending_retrip_reason(), knows daily loss and drawdown.
The same "narrow breaker read as the whole answer" defect the bridge's
/health was cured of on 2026-07-29, on the card the operator reads right
after typing the command they believed would clear it.

/resume reads trading_blocked_by AFTER the reset now, and the card has three
states, none of them a default: ENABLED when the gate is open, REFUSED with
the gate named and what clears it, UNREAD when the gate could not be read.
"""
from __future__ import annotations

import asyncio
import io
import tokenize
from pathlib import Path
from types import SimpleNamespace

import pytest

from bot.warroom.warroom_bot import render_resume, resume_gate_line

ROOT = Path(__file__).resolve().parent.parent


# ── the card, every state ────────────────────────────────────────────────

def test_an_open_gate_says_enabled():
    out = render_resume(gate="")["text"]
    assert "ENABLED" in out
    assert "refused" not in out.lower()


def test_a_tripped_warning_rate_breaker_says_refused_and_names_what_clears_it():
    out = render_resume(gate="warning_rate:engine_tick_failure")["text"]
    assert "REFUSED" in out and "ENABLED" not in out
    assert "engine_tick_failure" in out
    assert "clears on its own" in out
    assert "/resume does not clear it" in out
    assert "CLEAR" in out, "the circuit-breaker line was true and stays"


def test_a_loss_streak_gate_points_at_the_probe_schedule():
    out = render_resume(gate="loss_streak:3")["text"]
    assert "REFUSED" in out
    assert "/status" in out


def test_an_unreadable_gate_is_unread_not_enabled():
    out = render_resume(gate=None)["text"]
    assert "UNREAD" in out and "ENABLED" not in out
    assert "Could not read the entry gate" in out


def test_the_gate_line_is_three_valued():
    assert resume_gate_line("") == ""
    assert "unread" in resume_gate_line(None).lower() or "could not read" in resume_gate_line(None).lower()
    assert "circuit breaker" in resume_gate_line("manual")


def test_the_old_default_is_gone():
    """A caller that forgets the gate must not get ENABLED for free: the
    default is the open gate only because the tests above pin every
    other state, and the handler passes the real reading."""
    assert "ENABLED" in render_resume()["text"]


# ── the command, driven ──────────────────────────────────────────────────

def _host(risk):
    """A handler with only what /resume and its guard touch (the harness
    tests/test_every_command_answers_or_raises.py uses, narrowed)."""
    from bot.skills.telegram_handler import RateLimiter, TelegramHandler
    h = TelegramHandler.__new__(TelegramHandler)
    sent: list = []
    h._limiter = RateLimiter(10_000)
    user = SimpleNamespace(role="admin", lang="en", tier="admin", name="op", telegram_id="1", is_admin=True)
    h.users = SimpleNamespace(get=lambda tg: user, is_admin=lambda *a, **k: True,
                              is_authorized=lambda *a, **k: True, has_permission=lambda *a, **k: True,
                              save=lambda: None)
    h.engine = SimpleNamespace(risk=risk)

    async def _send(*a, **k):
        strings = [x for x in a if isinstance(x, str)] + [v for v in k.values() if isinstance(v, str)]
        sent.append(strings[-1] if strings else "")

    async def _guard(update, command="", ctx=None):
        return True
    h._send = _send
    h._guard = _guard
    h._is_admin = lambda u: True
    h._is_allowlisted = lambda u: True
    h._lang = lambda u: "en"
    h._control_scope = lambda update: (risk, "shared")
    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=1, first_name="op", username="op", language_code="en"),
        effective_chat=SimpleNamespace(id=1, type="private"),
        message=SimpleNamespace(text="/resume", reply_text=_send, chat_id=1, message_id=1),
        effective_message=SimpleNamespace(text="/resume", reply_text=_send), callback_query=None)
    ctx = SimpleNamespace(args=[], user_data={}, chat_data={}, bot_data={})
    return h, update, ctx, sent


async def _drive(risk):
    h, update, ctx, sent = _host(risk)
    await asyncio.wait_for(h._cmd_resume(update, ctx), timeout=15)
    assert sent, "/resume answered nothing"
    return sent[-1]


class _Risk:
    def __init__(self, blocked, raise_on_read=False):
        self._blocked = blocked
        self._raise = raise_on_read
        self.reset_calls = 0

    def reset_circuit_breaker(self):
        self.reset_calls += 1

    def pending_retrip_reason(self):
        return None

    @property
    def trading_blocked_by(self):
        if self._raise:
            raise RuntimeError("gate unreadable")
        return self._blocked


@pytest.mark.asyncio
async def test_resume_reads_the_gate_after_the_reset_and_says_refused():
    risk = _Risk("warning_rate:engine_tick_failure")
    out = await _drive(risk)
    assert risk.reset_calls == 1, "the reset still happens"
    assert "REFUSED" in out and "ENABLED" not in out
    assert "engine_tick_failure" in out


@pytest.mark.asyncio
async def test_resume_says_enabled_only_when_the_gate_is_open():
    assert "ENABLED" in await _drive(_Risk(""))


@pytest.mark.asyncio
async def test_resume_says_unread_when_the_gate_cannot_be_read():
    out = await _drive(_Risk("", raise_on_read=True))
    assert "UNREAD" in out and "ENABLED" not in out


def _code_only(path: Path) -> str:
    src = path.read_text(encoding="utf-8")
    return " ".join(tok.string for tok in tokenize.generate_tokens(io.StringIO(src).readline)
                    if tok.type != tokenize.COMMENT)


def test_the_gate_is_read_after_the_reset_not_before():
    code = _code_only(ROOT / "bot" / "skills" / "telegram_handler.py")
    i = code.find("async def _cmd_resume")
    body = code[i:code.find("async def ", i + 10)]
    reset = body.find("reset_circuit_breaker ( )")
    gate = body.find("_resume_gate_state ( risk )")
    assert reset > 0 and gate > reset, (
        "the gate must be read AFTER the reset, or it reports the state the reset just changed")
    assert "gate = _gate" in body
