"""Every Telegram command, against an engine whose every read fails, either answers or raises.

The one outcome this forbids is SILENCE: a handler that returns without
sending anything. The global error handler (`_on_error`) turns a raise into
a logged, generic reply, so a raise is a degraded-but-honest outcome; a bare
return is the silent failure its docstring warns about -- the operator types
/status and nothing comes back.

The harness is the real TelegramHandler with __init__ skipped, an engine
whose every attribute read raises, and the auth gate stubbed open. It is how
the misplaced @guard on the news helper was found (a TypeError where the
engine's own error should have been), so it also pins that no command raises
a TypeError or AttributeError from the handler's OWN code -- those are the
harness's signature for "called with the wrong shape", not "the read failed".
"""
from __future__ import annotations

import asyncio
import inspect
from types import SimpleNamespace

import pytest

from bot.skills.telegram_handler import RateLimiter, TelegramHandler

# Reach the network directly (cross-venue funding APIs), not the engine; a
# failing-engine smoke has nothing to say about them and they hang offline.
NETWORK_BOUND = {"_cmd_arb", "_cmd_fundingscan"}


class _Boom:
    """Every attribute read is another _Boom; calling or awaiting one raises."""
    def __getattr__(self, name):
        if name.startswith("__"):
            raise AttributeError(name)
        return _Boom()
    def __call__(self, *a, **k):
        raise RuntimeError("read failed")
    def __await__(self):
        async def _c():
            raise RuntimeError("read failed")
        return _c().__await__()
    def __bool__(self):
        return False
    def __iter__(self):
        return iter(())
    def __len__(self):
        return 0
    def __contains__(self, k):
        return False
    def get(self, *a, **k):
        raise RuntimeError("read failed")


def _harness():
    sent = []
    h = TelegramHandler.__new__(TelegramHandler)
    h.engine = _Boom()
    h._limiter = RateLimiter(10_000)
    user = SimpleNamespace(role="admin", lang="en", tier="admin", name="op", telegram_id="1", is_admin=True)
    h.users = SimpleNamespace(get=lambda tg: user, register=lambda *a, **k: user, is_admin=lambda *a, **k: True,
                              is_authorized=lambda *a, **k: True, all=lambda: [], save=lambda: None,
                              list_users=lambda *a, **k: [], get_sol_wallet=lambda *a, **k: None)
    for attr in ("registry", "signal_tracker", "intent_router", "conversations", "monitor", "forwarder"):
        setattr(h, attr, _Boom())
    h._last_pane = {}

    async def _send(*a, **k):
        # Called as _send(update, text) by the handler and as send(text) by
        # skill progress callbacks; record whatever text arrived.
        strings = [x for x in a if isinstance(x, str)] + [v for v in k.values() if isinstance(v, str)]
        sent.append(strings[-1] if strings else "")

    async def _guard(update, command="", ctx=None):
        return True
    h._send = _send
    h._guard = _guard
    h._is_admin = lambda u: True
    h._is_allowlisted = lambda u: True
    h._lang = lambda u: "en"
    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=1, first_name="op", username="op", language_code="en"),
        effective_chat=SimpleNamespace(id=1, type="private"),
        message=SimpleNamespace(text="/x", reply_text=_send, chat_id=1, message_id=1,
                                reply_photo=_send, reply_document=_send),
        effective_message=SimpleNamespace(text="/x", reply_text=_send), callback_query=None)
    ctx = SimpleNamespace(args=[], bot=_Boom(), user_data={}, chat_data={}, bot_data={}, application=_Boom())
    return h, update, ctx, sent


COMMANDS = sorted(n for n, v in vars(TelegramHandler).items()
                  if n.startswith("_cmd_") and inspect.iscoroutinefunction(v) and n not in NETWORK_BOUND)


@pytest.mark.asyncio
@pytest.mark.parametrize("name", COMMANDS)
async def test_command_answers_or_raises_never_silent(name):
    h, update, ctx, sent = _harness()
    try:
        await asyncio.wait_for(getattr(h, name)(update, ctx), timeout=15)
    except asyncio.TimeoutError:
        pytest.fail(f"{name} hung against a failing engine")
    except RuntimeError:
        # The engine's own error, or a deliberate RuntimeError from the
        # handler ("a scan is already running"): either way _on_error answers.
        return
    except (TypeError, AttributeError) as exc:
        # The engine never raises these; they come from the handler's own
        # code being called with the wrong shape. /news: a @guard on a helper.
        if "'_Boom'" in str(exc) or "SimpleNamespace" in str(exc):
            return                                  # harness shape, not the handler's
        pytest.fail(f"{name} raised from its own code: {type(exc).__name__}: {exc}")
    except Exception:
        return                                      # any other raise is answered by _on_error
    assert sent, f"{name} returned without answering -- the silent failure"
