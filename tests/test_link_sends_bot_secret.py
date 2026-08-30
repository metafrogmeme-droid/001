"""The /link call must carry X-Bot-Secret, and must re-read it every time.

RC-2026-001's server half refuses `/api/auth/validate-token` without this
header. Shipping that gate while the bot does not send the header breaks every
real /link — so this file is the half of the fix that makes the other half
safe to turn on.

WHY NOT A SOURCE SCAN. `os.getenv("BOT_SYNC_SECRET", "")` appearing in the
header dict proves the line exists. It does not prove the dict is the one
handed to `urllib.request.Request`, and #999 in CLAUDE.md is the standing
reminder that those are different claims — a card that was built, tested by
grep, and rendered zero times in production. So this drives `cmd_link` and
reads the headers off the request object it actually constructs.

The per-request property has its own test because it is a real behaviour, not
a style preference: `bot/utils/website_sync.py:104` records why the same header
is read per call there — a vault restore or an admin repair has to take effect
without restarting a bot that is holding open positions.
"""

from __future__ import annotations

import asyncio
import types
import urllib.error
import urllib.request

import pytest

from bot.skills import user_middleware as um


class _Msg:
    def __init__(self) -> None:
        self.sent: list[str] = []

    async def reply_text(self, *args, **kwargs) -> None:
        self.sent.append(args[0] if args else "")


class _Update:
    def __init__(self, chat_id: str = "555111") -> None:
        self.effective_chat = types.SimpleNamespace(id=chat_id)
        self.effective_user = types.SimpleNamespace(username="tester")
        self.message = _Msg()


def _run_link(monkeypatch, *, secret, token="linktoken123", chat_id="555111"):
    """Drive /link once and return the request it built.

    The network is cut deliberately: everything this file asserts is decided
    before the socket opens, and `cmd_link` already has a tested path for an
    unreachable website.
    """
    captured: dict = {}

    monkeypatch.setattr(um, "get_user_by_chat_id", lambda _cid: None)
    if secret is None:
        monkeypatch.delenv("BOT_SYNC_SECRET", raising=False)
    else:
        monkeypatch.setenv("BOT_SYNC_SECRET", secret)

    real_request = urllib.request.Request

    def _capture(url, data=None, headers=None, method=None):
        captured["url"] = url
        captured["headers"] = dict(headers or {})
        captured["body"] = data
        return real_request(url, data=data, headers=headers or {}, method=method)

    monkeypatch.setattr(urllib.request, "Request", _capture)

    def _no_network(*_a, **_k):
        raise urllib.error.URLError("network cut by the test")

    monkeypatch.setattr(urllib.request, "urlopen", _no_network)

    update = _Update(chat_id)
    context = types.SimpleNamespace(args=[token])
    asyncio.run(um.cmd_link(update, context))
    return captured


def test_link_targets_validate_token_with_the_chat_id(monkeypatch):
    """Anchor the other assertions to the right request."""
    cap = _run_link(monkeypatch, secret="s" * 48)
    assert cap["url"].endswith("/api/auth/validate-token")
    assert b'"chat_id": "555111"' in cap["body"]


def test_link_sends_the_bot_secret(monkeypatch):
    cap = _run_link(monkeypatch, secret="s" * 48)
    # urllib title-cases header keys on the Request object; assert on the dict
    # we handed it, which is what the header capture holds.
    assert cap["headers"].get("X-Bot-Secret") == "s" * 48


def test_the_secret_is_re_read_on_every_call(monkeypatch):
    """Import-time capture would make a vault restore need a bot restart."""
    first = _run_link(monkeypatch, secret="a" * 48)
    assert first["headers"].get("X-Bot-Secret") == "a" * 48

    second = _run_link(monkeypatch, secret="b" * 48)
    assert second["headers"].get("X-Bot-Secret") == "b" * 48, (
        "the second call sent the first call's secret — the value was captured "
        "at import time, so an admin repair would not take effect until restart"
    )


def test_an_unset_secret_sends_an_empty_header_not_a_crash(monkeypatch):
    """An unset secret must reach the server as a refusable value.

    The server's `linkBotSecretVerdict` scores an empty header `bad` and
    refuses at the `block` rung. What must NOT happen is a KeyError or a
    missing-header shape that some future middleware treats as exempt: the
    absence of a secret is a fact to transmit, not a case to skip.
    """
    cap = _run_link(monkeypatch, secret=None)
    assert "X-Bot-Secret" in cap["headers"]
    assert cap["headers"]["X-Bot-Secret"] == ""


@pytest.mark.parametrize("header", ["Content-Type", "Accept", "User-Agent"])
def test_the_existing_headers_survive(monkeypatch, header):
    """Adding one header must not have displaced the others."""
    cap = _run_link(monkeypatch, secret="s" * 48)
    assert header in cap["headers"]
