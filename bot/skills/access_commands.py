"""The users-and-access command group — the fourth slice out of the handler.

`/approve`, `/revoke`, `/grant_live`, `/revoke_live`, `/set_tier`, `/setcap`,
`/weblive`, `/users`, and the two marketing-channel commands `/channel` and
`/broadcast`. The operator's decisions about WHO may do WHAT: admission,
roles, live-trading grants, per-user margin caps, the web live switch.
Their behaviour is covered where it always was
(`test_a_web_id_cannot_be_vouched_for`, `test_telegram_commands`,
`test_idle_yield_partial_report`, `test_command_audience_matches_permission`);
`tests/test_handler_mixins.py` holds this class to the split's rules.

A mixin, not a leaf. Every method here reads `self.users` — the store that
IS the access decision — gates on `self._is_admin`, and answers through
`self._send`. `is_vouchable` is imported into THIS module, which is where
`/approve` reads it; a test that patches the handler's copy of the name is
patching a name the gate no longer reads, so the vouching test patches it
here.
"""
from __future__ import annotations

import html
from datetime import datetime
from typing import TYPE_CHECKING

from telegram import Update
from telegram.ext import ContextTypes

from bot.compat import UTC
from bot.formatters.user_roster import render_table
from bot.utils.i18n import get_user_lang, t
from bot.utils.logger import audit, system_log
from bot.utils.user_store import ROLES, SELF_ADMISSION_ROLE, UserStore, is_vouchable

if TYPE_CHECKING:
    from bot.marketing.channel_forwarder import ChannelForwarder


class AccessCommands:
    """The operator's admission, role and grant commands. Host contract below."""

    if TYPE_CHECKING:
        # Provided by TelegramHandler, and ONLY declared here — declarations,
        # never bodies; tests/test_handler_mixins.py checks every name against
        # what the handler really defines.
        users: UserStore
        forwarder: ChannelForwarder

        async def _send(self, update: Update, text: str,
                        reply_markup=None, edit: bool = False) -> None: ...

        def _is_admin(self, update: Update) -> bool: ...

        def _lang(self, update: Update) -> str: ...

        def _get_tg_id(self, update: Update) -> str: ...

        def _can_trade_live(self, tg_id) -> bool: ...

    async def _cmd_approve(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """Admin only: /approve <telegram_id> [role]"""
        if not self._is_admin(update):
            await self._send(update, f"\U0001f512 {t('admin_only', self._lang(update))}")
            return

        args = ctx.args or []
        if not args:
            await self._send(update,
                f"\U0001f4cb {t('approve_usage', self._lang(update))}")
            return

        target_id = args[0].strip()

        # Input validation: Telegram IDs are numeric only. Shared with the
        # `admit:` callback and read by migrate_self_admitted_roles, which
        # depends on this refusal to know a web-only account cannot have been
        # vouched for — see user_store.is_vouchable.
        if not is_vouchable(target_id):
            await self._send(update,
                f"\U0001f534 {t('invalid_tg_id_numeric', self._lang(update))}")
            return

        role = args[1].strip().lower() if len(args) > 1 else "trader"

        # `paper` is grantable here on purpose: it is where a self-admitted user
        # already sits, so an admin who wants to keep someone at that level —
        # or move a "trader" back down to it without revoking access outright —
        # can, instead of having only /revoke as the next step down.
        if role not in ("trader", SELF_ADMISSION_ROLE, "viewer", "admin"):
            await self._send(update,
                f"\U0001f534 {t('invalid_role', self._lang(update), role=html.escape(role))}")
            return

        # `by=` is what makes this stick. Without it the store records an
        # authorization the allowlist does not read, and /approve announces
        # "USER APPROVED" to the admin and "Access Granted" to the user while
        # the very next command still answers "not approved yet".
        ok = self.users.authorize(target_id, role=role,
                                  by=self._get_tg_id(update))
        if ok:
            target = self.users.get(target_id)
            name = target.get("name", "Unknown") if target else "Unknown"
            can_live = self._can_trade_live(target_id)
            trade_mode = "\U0001f525 Live" if can_live else "\U0001f4dd Paper"
            SEP = "\u2500" * 16
            await self._send(update,
                f"\u2705 {t('approve_result', self._lang(update), sep=SEP, name=html.escape(name), id=target_id, role=role, trade_mode=trade_mode)}")
            # Notify the approved user
            try:
                await ctx.bot.send_message(
                    chat_id=int(target_id),
                    text=(
                        f"🟢 {t('access_granted', get_user_lang(self.users, target_id), sep=SEP, role=role)}"
                    ),
                    parse_mode="HTML")
            except Exception:
                pass  # User may not have started the bot yet
        else:
            await self._send(update,
                f"🔴 {t('approve_failed', self._lang(update), id=html.escape(target_id))}")

    async def _cmd_revoke(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """Admin only: /revoke <telegram_id>"""
        if not self._is_admin(update):
            await self._send(update, f"\U0001f512 {t('admin_only', self._lang(update))}")
            return

        args = ctx.args or []
        if not args:
            await self._send(update,
                f"{t('revoke_usage', self._lang(update))}")
            return

        target_id = args[0].strip()

        # L-13 FIX: validate Telegram ID format
        if not target_id.isdigit():
            await self._send(update, f"{t('invalid_tg_id_format', self._lang(update))}")
            return

        # Don't let admin revoke themselves
        if target_id == self._get_tg_id(update):
            await self._send(update, f"\U0001f534 {t('cannot_revoke_self', self._lang(update))}")
            return

        ok = self.users.revoke(target_id)
        if ok:
            SEP = "─" * 16
            await self._send(update,
                f"⚠️ {t('revoke_result', self._lang(update), sep=SEP, id=target_id)}")
        else:
            await self._send(update,
                f"\U0001f534 {t('user_not_found_id', self._lang(update), id=html.escape(target_id))}")

    async def _cmd_grant_live(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """Admin only: /grant_live <telegram_id> — allow user to trade live."""
        if not self._is_admin(update):
            await self._send(update, f"\U0001f512 {t('admin_only', self._lang(update))}")
            return
        args = ctx.args or []
        if not args:
            await self._send(update,
                f"\U0001f4cb {t('grant_live_usage', self._lang(update))}")
            return
        target_id = args[0].strip()
        if not target_id.isdigit():
            await self._send(update, f"\U0001f534 {t('invalid_tg_id', self._lang(update))}")
            return
        user = self.users.get(target_id)
        if not user or not user.get("authorized"):
            await self._send(update,
                f"\U0001f534 {t('grant_live_not_approved', self._lang(update), id=target_id)}")
            return
        ok = self.users.set_live_trading(target_id, True)
        if ok:
            name = user.get("name", "Unknown")
            await self._send(update,
                f"\U0001f525 {t('grant_live_result', self._lang(update), name=html.escape(name), id=target_id, role=user.get('role', 'trader'))}")
        else:
            await self._send(update, f"\U0001f534 {t('grant_live_failed', self._lang(update))}")

    async def _cmd_revoke_live(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """Admin only: /revoke_live <telegram_id> — restrict user to paper only."""
        if not self._is_admin(update):
            await self._send(update, f"\U0001f512 {t('admin_only', self._lang(update))}")
            return
        args = ctx.args or []
        if not args:
            await self._send(update,
                f"{t('revoke_live_usage', self._lang(update))}")
            return
        target_id = args[0].strip()
        if not target_id.isdigit():
            await self._send(update, f"\U0001f534 {t('invalid_tg_id', self._lang(update))}")
            return
        ok = self.users.set_live_trading(target_id, False)
        if ok:
            user = self.users.get(target_id)
            name = user.get("name", "Unknown") if user else "Unknown"
            await self._send(update,
                f"\U0001f4dd {t('revoke_live_result', self._lang(update), name=html.escape(name), id=target_id)}")
        else:
            await self._send(update, f"\U0001f534 {t('user_not_found', self._lang(update))}")

    async def _cmd_set_tier(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """Admin only: /set_tier <telegram_id> <tier> — change user tier."""
        if not self._is_admin(update):
            await self._send(update, f"\U0001f512 {t('admin_only', self._lang(update))}")
            return
        args = ctx.args or []
        if len(args) < 2:
            from bot.utils.user_store import TIERS
            tiers_str = " / ".join(f"<code>{_tier}</code>" for _tier in TIERS)
            await self._send(update,
                f"\U0001f4cb {t('set_tier_usage', self._lang(update), tiers=tiers_str)}")
            return
        target_id = args[0].strip()
        tier = args[1].strip().lower()
        if not target_id.isdigit():
            await self._send(update, f"\U0001f534 {t('invalid_tg_id', self._lang(update))}")
            return
        from bot.utils.user_store import TIERS
        if tier not in TIERS:
            await self._send(update,
                f"\U0001f534 {t('invalid_tier', self._lang(update), tier=html.escape(tier), valid=', '.join(f'<code>{_t}</code>' for _t in TIERS))}")
            return
        user = self.users.get(target_id)
        if not user:
            await self._send(update, f"\U0001f534 {t('user_not_found_id_period', self._lang(update), id=target_id)}")
            return
        ok = self.users.set_tier(target_id, tier)
        if ok:
            # Mirror the change to the website so users.plan follows the
            # bot's tier authority (best-effort, background).
            try:
                from bot.utils.website_sync import sync_tiers_in_background
                sync_tiers_in_background(self.users.all_tiers())
            except Exception:
                pass
            name = user.get("name", "Unknown")
            tier_label = self.users.tier_label(target_id)
            await self._send(update,
                f"\U0001f3af {t('set_tier_result', self._lang(update), name=html.escape(name), id=target_id, tier_label=tier_label, role=user.get('role', 'trader'))}")
            # Notify the user
            try:
                await ctx.bot.send_message(
                    chat_id=int(target_id),
                    text=(f"\U0001f3af {t('account_upgraded', get_user_lang(self.users, target_id), tier_label=tier_label)}"),
                    parse_mode="HTML")
            except Exception:
                pass
        else:
            await self._send(update, f"\U0001f534 {t('set_tier_failed', self._lang(update))}")

    async def _cmd_channel(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """/channel — manage marketing channel auto-posting."""
        # Allow bot admins OR Telegram group admins
        is_bot_admin = self._is_admin(update)
        is_group_admin = False
        if not is_bot_admin and update.effective_chat and update.effective_chat.type in ("group", "supergroup"):
            try:
                member = await ctx.bot.get_chat_member(
                    update.effective_chat.id, update.effective_user.id)
                is_group_admin = member.status in ("creator", "administrator")
            except Exception:
                pass
        if not is_bot_admin and not is_group_admin:
            await self._send(update, f"\U0001f512 {t('admin_only', self._lang(update))}")
            return

        # Auto-detect this group if command is run in one
        chat = update.effective_chat
        if chat and chat.type in ("group", "supergroup"):
            self.forwarder.detect_group(chat.id, chat.type, chat.title or "")

        args = ctx.args or []
        if not args:
            groups = self.forwarder.group_ids
            status = "\U0001f7e2 ON" if self.forwarder.is_enabled else "\U0001f534 OFF"
            _sep = "\u2500" * 18
            msg = (
                f"\U0001f4e1 <b>Channel Forwarder</b>\n"
                f"{_sep}\n\n"
                f"Status: {status}\n"
                f"Groups: <code>{len(groups)}</code>\n"
            )
            if groups:
                for gid in groups:
                    msg += f"\u2022 <code>{gid}</code>\n"
            msg += (
                "\n<b>Commands:</b>\n"
                "<code>/channel on</code> \u2014 enable auto-posting\n"
                "<code>/channel off</code> \u2014 disable auto-posting\n"
                "<code>/channel add &lt;chat_id&gt;</code> \u2014 add group\n"
                "<code>/channel remove &lt;chat_id&gt;</code> \u2014 remove group\n"
                "<code>/channel test</code> \u2014 send test message\n\n"
                "<i>Groups are also auto-detected when the bot receives a message in them.</i>"
            )
            await self._send(update, msg)
            return

        sub = args[0].lower()
        if sub == "on":
            self.forwarder.set_enabled(True)
            await self._send(update, "\U0001f7e2 Channel auto-posting <b>enabled</b>.")
        elif sub == "off":
            self.forwarder.set_enabled(False)
            await self._send(update, "\U0001f534 Channel auto-posting <b>disabled</b>.")
        elif sub == "add" and len(args) >= 2:
            try:
                gid = int(args[1])
                self.forwarder.add_group(gid)
                await self._send(update, f"\u2705 Group <code>{gid}</code> added.")
            except ValueError:
                await self._send(update, "\u274c Invalid chat ID. Must be a number.")
        elif sub == "remove" and len(args) >= 2:
            try:
                gid = int(args[1])
                self.forwarder.remove_group(gid)
                await self._send(update, f"\u2705 Group <code>{gid}</code> removed.")
            except ValueError:
                await self._send(update, "\u274c Invalid chat ID.")
        elif sub == "test":
            now = datetime.now(UTC).strftime("%H:%M UTC")
            await self.forwarder.post_custom(
                f"\U0001f916 <b>RUNECLAW Test</b>\n\n"
                f"Channel forwarder is working.\n"
                f"Signals, trade results, and daily reports will auto-post here.\n\n"
                f"<i>{now}</i>")
            await self._send(update, "\u2705 Test message sent to all groups.")
        else:
            await self._send(update,
                "\u274c Unknown subcommand. Use <code>/channel</code> for help.")

    async def _cmd_broadcast(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """/broadcast <message> — send a custom message to all marketing channels."""
        is_bot_admin = self._is_admin(update)
        is_group_admin = False
        if not is_bot_admin and update.effective_chat and update.effective_chat.type in ("group", "supergroup"):
            try:
                member = await ctx.bot.get_chat_member(
                    update.effective_chat.id, update.effective_user.id)
                is_group_admin = member.status in ("creator", "administrator")
            except Exception:
                pass
        if not is_bot_admin and not is_group_admin:
            await self._send(update, f"\U0001f512 {t('admin_only', self._lang(update))}")
            return
        args = ctx.args or []
        if not args:
            await self._send(update,
                "\U0001f4e2 <b>Broadcast</b>\n\n"
                "<code>/broadcast Your message here</code>\n\n"
                "Sends a custom message to all registered groups.")
            return
        text = " ".join(args)
        await self.forwarder.post_custom(f"\U0001f4e2 {html.escape(text)}")
        await self._send(update, f"\u2705 Broadcast sent to {self.forwarder.group_count} group(s).")

    async def _cmd_users(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """Admin only: list all registered users."""
        if not self._is_admin(update):
            await self._send(update, f"\U0001f512 {t('admin_only', self._lang(update))}")
            return

        # A ROSTER FROM A STORE THAT STARTED EMPTY is not a roster of who is
        # registered — it is a roster of who has messaged since. Both render as
        # a confident count, which is how "the users are gone" looked identical
        # to "nobody ever signed up".
        #
        # READ FROM THE STORE THIS COMMAND ACTUALLY USES. The first version of
        # this caveat asked `bot.db.models.database_is_new()` — the SQLite
        # database, which /users never touches: the roster comes from
        # UserStore and data/users.json. It could not have fired for the
        # incident it was written for, and would have stayed silent while
        # reporting two accounts out of eighteen.
        fresh_db = ""
        try:
            if getattr(self.users, "started_empty", False):
                fresh_db = (
                    "\n\n⚠️ <b>This user store started empty on this run.</b>\n"
                    "There was no readable <code>users.json</code> when the "
                    "bot started, so this list is everyone who has messaged "
                    "SINCE — not necessarily everyone who was registered "
                    "before. Check the <code>data/</code> symlink and any "
                    "recent restore.")
        except Exception:
            fresh_db = ""

        all_users = self.users.list_users()
        if not all_users:
            await self._send(
                update,
                f"\U0001f4cb {t('no_registered_users', self._lang(update))}"
                + fresh_db)
            return

        counts = self.users.count()
        SEP = "─" * 16
        lines = [
            f"👥 {t('users_header', self._lang(update), n=len(all_users))}\n"
            f"{SEP}\n",
        ]

        # Summary with role icons. Iterate ROLES, not a second hardcoded tuple:
        # this list is the operator's headcount, and a role missing from the
        # loop is a group of real users rendering as absent — a stranger who
        # let themselves in would simply not appear in the count.
        role_icons = {"admin": "🔒", "trader": "⚔️", "paper": "📝",
                      "viewer": "👁", "pending": "⏳"}
        for role in ROLES:
            c = counts.get(role, 0)
            if c > 0:
                icon = role_icons.get(role, "")
                lines.append(f"- {icon} {role}: <code>{c}</code>")
        lines.append("")

        # User list. The table is a pure renderer (bot/formatters/user_roster)
        # because this is the card an operator reads BEFORE typing /approve
        # <id> \u2014 it used to print the last 8 characters of the id, which is not
        # a key in the store and gave no sign it had been shortened.
        lines.extend(render_table(all_users, self._can_trade_live, limit=15))

        if len(all_users) > 15:
            lines.append(f"\n<i>{t('users_more', self._lang(update), n=len(all_users))}</i>")

        await self._send(update, "\n".join(lines) + fresh_db)

    async def _cmd_setcap(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """Admin only: /setcap <telegram_id> <max_margin_usd | off> — cap how much
        margin a regular user may commit to a single live trade (tighten-only,
        never above the global micro cap). 'off' clears the cap."""
        if not self._is_admin(update):
            await self._send(update, f"\U0001f512 {t('admin_only', self._lang(update))}")
            return
        args = ctx.args or []
        if len(args) != 2:
            await self._send(update,
                "📋 <b>Usage:</b> <code>/setcap &lt;telegram_id&gt; &lt;max_margin_usd | off&gt;</code>\n\n"
                "Caps a user's per-trade margin (only reduces; never exceeds the "
                "global live cap). Example: <code>/setcap 12345678 50</code> or "
                "<code>/setcap 12345678 off</code>.")
            return
        target_id, raw = args[0].strip(), args[1].strip().lower()
        if not target_id.isdigit():
            await self._send(update,
                f"\U0001f534 {t('invalid_tg_id_numeric', self._lang(update))}")
            return
        if not self.users.get(target_id):
            await self._send(update, "🔴 No such user. They must /start first.")
            return
        if raw in ("off", "none", "clear", "0"):
            self.users.set_max_margin(target_id, None)
            await self._send(update,
                f"🟢 Margin cap <b>cleared</b> for <code>{target_id}</code> — "
                "back to the global live cap.")
            return
        try:
            usd = float(raw)
        except ValueError:
            await self._send(update,
                "🔴 Amount must be a number (USD) or <code>off</code>.")
            return
        if usd <= 0:
            await self._send(update, "🔴 Cap must be greater than 0 (or <code>off</code>).")
            return
        self.users.set_max_margin(target_id, usd)
        await self._send(update,
            f"🟢 Margin cap set: <code>{target_id}</code> may commit at most "
            f"<b>${usd:,.2f}</b> margin per live trade (still bounded by the global cap).")

    async def _cmd_weblive(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """/weblive <web:id> [on|off] — web live-trading readiness + enablement.

        Operator-only. With no action it prints the five-precondition readiness
        card for that web user; ``on``/``off`` flips their dedicated
        web_live_enabled opt-in (one of the five gates). The GLOBAL switch stays
        the deployment env var WEB_LIVE_TRADING_ENABLED — this never turns the
        whole capability on, only a single user's opt-in, and moves no funds."""
        if not self._is_admin(update):
            await self._send(update, "🔒 /weblive is operator-only.")
            return
        args = (ctx.args or []) if hasattr(ctx, "args") else []
        if not args:
            await self._send(update,
                "Usage: <code>/weblive web:&lt;id&gt; [on|off]</code>\n"
                "Shows a web user's live-trading readiness; on/off flips their opt-in.")
            return
        target = str(args[0]).strip()
        if not target.startswith("web:"):
            await self._send(update, "🔴 Target must be a web id, e.g. <code>web:5</code>.")
            return
        try:
            from bot.web import web_live_admin as adm
            action = str(args[1]).lower() if len(args) > 1 else ""
            if action in ("on", "off"):
                ok = adm.set_user_enabled(self.users, target, action == "on")
                if not ok:
                    await self._send(update, f"🔴 Could not update {html.escape(target)} "
                                     "(unknown user?).")
                    return
                audit(system_log, f"Operator set web_live_enabled={action} for {target}",
                      action="op_weblive_toggle", result=action)
            card = adm.human_readable(target, adm.user_readiness(self.users, target))
            await self._send(update, f"<pre>{html.escape(card)}</pre>")
        except Exception as exc:
            system_log.warning("/weblive failed: %s", exc)
            await self._send(update, "🔴 Readiness check failed — see logs.")
