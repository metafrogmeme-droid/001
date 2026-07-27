"""The Guardian tools reach Telegram: /approvals and /xray call the website's
public API — the same bounded, honest surfaces the web pages and MCP agents
use. Tests drive the handlers with the web layer stubbed."""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock


def _make_update(user_id=6307156912, args=None):
    update = MagicMock()
    update.effective_user = MagicMock()
    update.effective_user.id = user_id
    update.effective_chat = MagicMock()
    update.effective_chat.id = user_id
    update.message = MagicMock()
    update.message.reply_text = AsyncMock()
    update.callback_query = None
    ctx = MagicMock()
    ctx.args = args or []
    return update, ctx


def _make_handler():
    from bot.core.engine import RuneClawEngine
    from bot.skills.telegram_handler import TelegramHandler
    engine = RuneClawEngine()
    handler = TelegramHandler(engine)
    handler.users.seed_admin(str(6307156912))
    return handler


def _sent_text(update) -> str:
    call = update.message.reply_text.call_args
    if call is None:
        return ""
    return call[0][0] if call[0] else call.kwargs.get("text", "")


class TestApprovalsCommand:
    @pytest.mark.asyncio
    async def test_findings_render_with_honesty_lines(self):
        handler = _make_handler()
        handler._web_get_json = staticmethod(lambda url, timeout=20: {
            "read_only": True, "chain": "base", "label": "Base",
            "spenders_checked": ["Permit2 (Uniswap)"],
            "findings": [{"token": "USDC", "spender_label": "Permit2 (Uniswap)",
                          "spender": "0x0", "allowance_raw": "5", "decimals": 6,
                          "unlimited": True, "revoke_plan": {}}],
            "zero_pairs": 15, "unreadable_pairs": 1, "note": "x"})
        update, ctx = _make_update(args=["0x020A4366595292aa16184536bAB2E9949d0D925F", "base"])
        await handler._cmd_approvals(update, ctx)
        text = _sent_text(update)
        assert "UNLIMITED" in text
        assert "USDC" in text
        assert "unreadable" in text
        assert "never a guarantee" in text
        assert "/approvals?a=0x020A" in text

    @pytest.mark.asyncio
    async def test_unreadable_answers_unknown_not_zero(self):
        handler = _make_handler()
        handler._web_get_json = staticmethod(lambda url, timeout=20: None)
        update, ctx = _make_update(args=["0x020A4366595292aa16184536bAB2E9949d0D925F"])
        await handler._cmd_approvals(update, ctx)
        assert "unknown, not zero" in _sent_text(update)

    @pytest.mark.asyncio
    async def test_unresolvable_name_scans_nothing(self):
        handler = _make_handler()
        calls = []
        handler._web_get_json = staticmethod(
            lambda url, timeout=20: calls.append(url) or None)
        update, ctx = _make_update(args=["nobody.eth"])
        await handler._cmd_approvals(update, ctx)
        assert "does not resolve" in _sent_text(update)
        assert len(calls) == 1 and "/resolve/" in calls[0]

    @pytest.mark.asyncio
    async def test_no_args_prints_usage(self):
        handler = _make_handler()
        update, ctx = _make_update(args=[])
        await handler._cmd_approvals(update, ctx)
        assert "Usage:" in _sent_text(update)


class TestXrayCommand:
    @pytest.mark.asyncio
    async def test_actions_and_flags_render(self):
        handler = _make_handler()
        handler._web_post_json = staticmethod(lambda url, body, timeout=20: {
            "tool": "xray_transaction",
            "result": {"actions": [{"tid": "txr.a_approve",
                                    "en": "Grants {spender} an allowance of {amount} raw units.",
                                    "params": {"spender": "0xdead", "amount": "UNLIMITED"}}],
                       "flags": [{"id": "unlimited_approval", "sev": "high"}]}})
        update, ctx = _make_update(args=["0x095ea7b3" + "0" * 128])
        await handler._cmd_xray(update, ctx)
        text = _sent_text(update)
        assert "Grants 0xdead an allowance of UNLIMITED raw units." in text
        assert "unlimited_approval" in text
        assert "a flag is not a verdict" in text

    @pytest.mark.asyncio
    async def test_garbage_input_prints_usage_without_network(self):
        handler = _make_handler()
        called = []
        handler._web_post_json = staticmethod(
            lambda url, body, timeout=20: called.append(url))
        update, ctx = _make_update(args=["not-calldata"])
        await handler._cmd_xray(update, ctx)
        assert "Usage:" in _sent_text(update)
        assert not called
