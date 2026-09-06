"""The agent-posture and notes command group — a slice out of the handler.

`/agent` and the stance proposal the free-text intercept routes "be more
careful" / "push harder" / "back to normal" into, `/share`, `/mynotes` and
`/watch`. The stance blurbs move with the two methods that read them. Their
behaviour is covered where it always was (`test_agent_stance`,
`test_intent_routing_and_unavailable`, `test_telegram_commands`);
`tests/test_handler_mixins.py` holds this class to the split's rules.

A mixin, not a leaf: the stance proposal reads the process-wide RUNTIME
through the handler's own confirm callback, and every card answers through
`self._send`.
"""
from __future__ import annotations

import html
from typing import TYPE_CHECKING

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from bot.config import CONFIG
from bot.skills.command_guard import guard
from bot.utils.logger import system_log

if TYPE_CHECKING:
    from bot.core.engine import RuneClawEngine
    from bot.core.proactive_monitor import ProactiveMonitor
    from bot.skills.skill_registry import SkillRegistry
    from bot.utils.user_store import UserStore


class AgentCommands:
    """Posture, notes and watchlists. Host contract below; methods after."""

    if TYPE_CHECKING:
        # Provided by TelegramHandler, and ONLY declared here — declarations,
        # never bodies; tests/test_handler_mixins.py checks every name against
        # what the handler really defines.
        engine: RuneClawEngine
        registry: SkillRegistry
        users: UserStore
        monitor: ProactiveMonitor

        async def _send(self, update: Update, text: str,
                        reply_markup=None, edit: bool = False) -> None: ...

        async def _guard(self, update: Update, command: str = "", ctx=None) -> bool: ...

        def _is_admin(self, update: Update) -> bool: ...

        def _get_tg_id(self, update: Update) -> str: ...

    _STANCE_BLURB = {
        "defensive": ("🛡 <b>Defensive</b> — smaller sizing bias, stricter "
                      "setup selection, capital protection first."),
        "balanced": ("⚔️ <b>Balanced</b> — the default posture: normal "
                     "sizing, the full setup playbook."),
        "aggressive": ("🔥 <b>Aggressive</b> — larger sizing bias, more "
                       "setups taken. Every risk gate stays ON."),
        "manual": ("🧘 <b>Manual</b> — the engine proposes, you confirm "
                   "every trade."),
    }

    async def _propose_stance(self, update: Update, stance: str) -> None:
        """The agent's reply to 'be more careful' etc.: restate what it
        heard, show what would change, and wait for an explicit button
        press. The button routes to the existing mode_ callback, which is
        permission-gated — this method itself changes nothing."""
        from bot.config import RUNTIME
        if stance not in self._STANCE_BLURB:
            return
        current = RUNTIME.strategy_mode
        if stance == current:
            await self._send(update,
                f"👍 We're already trading <b>{current.capitalize()}</b>.\n\n"
                f"{self._STANCE_BLURB[current]}\n\n"
                "<i>/agent shows the full posture.</i>")
            return
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton(f"✅ Switch to {stance.capitalize()}",
                                  callback_data=f"mode_{stance}"),
             InlineKeyboardButton("Keep current", callback_data="stance_keep")],
        ])
        await self._send(update,
            "🎯 <b>Got it — you want to adjust how I trade.</b>\n\n"
            f"Current: <b>{current.capitalize()}</b>\n"
            f"Proposed: {self._STANCE_BLURB[stance]}\n\n"
            "<i>Nothing changes until you confirm. The fail-closed risk gate, "
            "loss breakers and drawdown caps apply in every stance.</i>",
            reply_markup=kb)

    async def _cmd_agent(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """/agent — your agent's posture in plain language, with one-tap
        stance presets. You can also just SAY it: 'be more careful',
        'push harder', 'back to normal'."""
        if not await self._guard(update, "status"):
            return
        from bot.config import RUNTIME
        mode = RUNTIME.strategy_mode
        lines = ["🤖 <b>Your agent's posture</b>", self._STANCE_BLURB.get(
            mode, f"<b>{mode.capitalize()}</b>")]
        lines.append(
            f"Mode <b>{'LIVE' if CONFIG.is_live() else 'PAPER'}</b> · "
            f"auto-trades at <code>{RUNTIME.auto_confirm_threshold:.0%}</code> "
            "confidence · signals messaged at <code>70%</code>+")
        try:
            ex = getattr(self.engine, "live_executor", None)
            n_open = len(getattr(ex, "open_positions", []) or []) if ex else 0
            lines.append(f"Carrying <b>{n_open}</b> open position(s) — "
                         "<i>/positions for detail</i>")
        except Exception:
            pass
        lines.append(
            "<i>Change how I trade by talking to me — “be more careful”, "
            "“push harder”, “back to normal” — or tap a preset:</i>")
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("🛡 Defensive", callback_data="mode_defensive"),
             InlineKeyboardButton("⚔️ Balanced", callback_data="mode_balanced")],
            [InlineKeyboardButton("🔥 Aggressive", callback_data="mode_aggressive"),
             InlineKeyboardButton("🧘 Manual", callback_data="mode_manual")],
        ])
        await self._send(update, "\n\n".join(lines), reply_markup=kb)

    @guard("scan")
    async def _cmd_watch(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """/watch [on|off|status] — toggle proactive alerts for this chat."""

        tg_id = self._get_tg_id(update)
        args = ctx.args or []
        action = args[0].lower() if args else "status"

        if action == "on":
            self.monitor.enable_chat(tg_id)
            await self._send(update,
                "\U0001f514 <b>PROACTIVE ALERTS ON</b>\n\n"
                "I'll push alerts for:\n"
                "\u2022 Volume spikes on scanned assets\n"
                "\u2022 Circuit breaker state changes\n"
                "\u2022 Black-swan anomaly detections\n"
                "\u2022 New trade signals pending confirmation\n"
                "\u2022 Engine state changes (halt/cooldown)\n\n"
                "Use <code>/watch off</code> to disable.")
        elif action == "off":
            self.monitor.disable_chat(tg_id)
            await self._send(update,
                "\U0001f515 <b>PROACTIVE ALERTS OFF</b>\n\n"
                "You won't receive unsolicited alerts.\n"
                "Use <code>/watch on</code> to re-enable.")
        else:
            enabled = self.monitor.is_enabled(tg_id)
            status = "\U0001f7e2 ON" if enabled else "\U0001f534 OFF"
            await self._send(update,
                f"\U0001f514 <b>WATCH STATUS</b>: {status}\n\n"
                f"Active watchers: {self.monitor.enabled_chat_count}\n\n"
                f"Use <code>/watch on</code> or <code>/watch off</code> to toggle.")

    async def _cmd_share(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """NEWS-3b: /share <text> — save a note your agent can reference (PRIVATE
        to you). Or reply to any message (e.g. a forwarded newsletter) with
        /share to save that text. Same encrypted per-user store as the web
        "Share with your agent" panel. §4: user-supplied only — the bot never
        fetches anything — private, never redistributed."""
        if not update.message:
            return
        tg_id = self._get_tg_id(update)
        if not self.users.is_authorized(tg_id):
            await self._send(update, "Use /start to register first.")
            return
        text = " ".join(ctx.args or []).strip()
        source = ""
        replied = update.message.reply_to_message
        if not text and replied is not None:
            text = (replied.text or replied.caption or "").strip()
            # Best-effort provenance when the replied-to message was forwarded.
            try:
                origin = getattr(replied, "forward_origin", None)
                nm = None
                if origin is not None:
                    nm = (getattr(getattr(origin, "sender_chat", None), "title", None)
                          or getattr(getattr(origin, "sender_user", None), "full_name", None)
                          or getattr(origin, "sender_user_name", None))
                else:  # older PTB
                    nm = (getattr(getattr(replied, "forward_from_chat", None), "title", None)
                          or getattr(getattr(replied, "forward_from", None), "full_name", None))
                if nm:
                    source = str(nm)[:120]
            except Exception:
                source = ""
        if not text:
            await self._send(update,
                "🗒️ <b>Share with your agent</b>\n"
                "─────────────────\n\n"
                "Give your agent something to remember — a newsletter you got, "
                "notes, an excerpt:\n\n"
                "<pre> /share &lt;your text&gt;</pre>\n"
                "…or <b>reply to any message</b> (e.g. a forwarded newsletter) "
                "with <code>/share</code> to save it.\n\n"
                "<i>🔒 Private to you, stored encrypted — your agent draws on it "
                "in chat. Manage your notes on the web dashboard. Only share "
                "content you're allowed to.</i>")
            return
        from bot.db.models import (IdentityCollision, add_user_ingest_note,
                                   ensure_settings_parent, settings_user_id)
        uid = settings_user_id(tg_id)
        if uid is None:
            await self._send(update, "Couldn't resolve your account — try /start.")
            return
        try:
            ensure_settings_parent(uid)
        except IdentityCollision:
            # RC-2026-026: the row at this id holds a bot-native account, so
            # this note would be filed under somebody else. Refuse, and say so
            # without naming the other account.
            system_log.error("identity collision saving ingest note for %s", tg_id)
            await self._send(update,
                "Couldn't save that — your account and another record share an "
                "internal id, so saving would file this note under someone "
                "else. Nothing was stored. Please contact support.")
            return
        nid = add_user_ingest_note(uid, "", text, source)
        if nid is None:
            await self._send(update, "Nothing to save — send some text.")
            return
        src = f" · from {html.escape(source)}" if source else ""
        await self._send(update,
            f"🗒️ <b>Saved to your agent's private notes.</b>{src}\n"
            f"<i>{len(text)} characters — your agent can reference it in chat "
            f"now. Manage your notes on the web dashboard.</i>")

    async def _cmd_mynotes(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """NEWS-3c: /mynotes — list the notes you've shared with your agent
        (PRIVATE, previews only). /mynotes clear — forget them all. Read/delete
        parity for the web "Share with your agent" panel."""
        if not update.message:
            return
        tg_id = self._get_tg_id(update)
        if not self.users.is_authorized(tg_id):
            await self._send(update, "Use /start to register first.")
            return
        from bot.db.models import (clear_user_ingest_notes,
                                   list_user_ingest_notes, settings_user_id)
        uid = settings_user_id(tg_id)
        if uid is None:
            await self._send(update, "Couldn't resolve your account — try /start.")
            return
        if ctx.args and ctx.args[0].lower() == "clear":
            n = clear_user_ingest_notes(uid)
            await self._send(update, f"🗑️ Cleared {n} shared note(s).")
            return
        notes = list_user_ingest_notes(uid, limit=15)
        if not notes:
            await self._send(update,
                "🗒️ You haven't shared anything with your agent yet.\n"
                "<i>Use <code>/share &lt;text&gt;</code>, or reply to a message "
                "(e.g. a forwarded newsletter) with <code>/share</code>.</i>")
            return
        lines = []
        for n in notes:
            head = html.escape(n["title"] or (f"from {n['source']}" if n["source"] else "note"))
            preview = html.escape(" ".join((n["body"] or "").split())[:140])
            lines.append(f"• <b>{head}</b> — {preview}")
        await self._send(update,
            f"🗒️ <b>Your shared notes</b> ({len(notes)})\n"
            "─────────────────\n"
            + "\n".join(lines)
            + "\n\n<i>Private to you. <code>/mynotes clear</code> forgets them "
              "all; manage them individually on the web dashboard.</i>")
