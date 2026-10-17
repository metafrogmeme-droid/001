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
`self._send`. The rules of `/approve`, `/revoke`, `/set_tier` and
`/revoke_live` are not here: they are the one write path the admin page
shares (`bot/core/member_admin.py`), which is where `is_vouchable` is read, so
the vouching test patches it there.
"""
from __future__ import annotations

import html
from datetime import datetime
from typing import TYPE_CHECKING

from telegram import Update
from telegram.ext import ContextTypes

from bot.compat import UTC
from bot.formatters.retention_baseline import (
    render_retention_baseline,
    retention_baseline,
)
from bot.formatters.user_roster import render_table
from bot.utils.i18n import t
from bot.utils.logger import audit, system_log
from bot.utils.user_store import (
    ROLES,
    StoreWriteHeld,
    UserStore,
)

if TYPE_CHECKING:
    from bot.marketing.channel_forwarder import ChannelForwarder


def _cap_held(target_id: str, usd, cls: str) -> str:
    """`/setcap`'s sentence for a cap that is in force and not on disk.

    The store keeps the change (this process caps at the new figure), the
    file does not, and a restart goes back to whatever the file holds.
    `cls` is the exception's class name, never its text.
    """
    change = ("cleared" if usd is None
              else f"set to <b>${float(usd):,.2f}</b> per live trade")
    again = "off" if usd is None else f"{float(usd):g}"
    return (f"\u26a0\ufe0f Margin cap {change} for <code>{target_id}</code> — "
            "<b>NOT SAVED</b>.\n\n"
            f"It is in force for this bot process only: <code>users.json</code> "
            f"could not be written ({cls}). A restart goes back to the cap on "
            f"disk. Fix the disk, then run <code>/setcap {target_id} {again}</code> "
            "again.")


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
        role = args[1].strip().lower() if len(args) > 1 else "trader"

        # THE ONE WRITE PATH (bot/core/member_admin.py), shared with the admin
        # page. It holds the rules this command used to hold inline: a
        # numeric id only (read by migrate_self_admitted_roles, which depends
        # on this refusal to know a web-only account cannot have been vouched
        # for), the grantable roles (`paper` on purpose: where a
        # self-admitted user already sits), and `by=`, which is what makes
        # the authorization one the allowlist reads.
        from bot.core import member_admin
        res = member_admin.approve(self.users, target_id, role,
                                   by=self._get_tg_id(update),
                                   via=member_admin.TELEGRAM)
        if not res.ok:
            if res.error == "invalid_tg_id_numeric":
                await self._send(update,
                    f"\U0001f534 {t('invalid_tg_id_numeric', self._lang(update))}")
            elif res.error == "invalid_role":
                await self._send(update,
                    f"\U0001f534 {t('invalid_role', self._lang(update), role=html.escape(role))}")
            else:
                await self._send(update,
                    f"🔴 {t('approve_failed', self._lang(update), id=html.escape(target_id))}")
            return
        can_live = self._can_trade_live(target_id)
        trade_mode = "\U0001f525 Live" if can_live else "\U0001f4dd Paper"
        SEP = "\u2500" * 16
        await self._send(update,
            f"\u2705 {t('approve_result', self._lang(update), sep=SEP, name=html.escape(res.name), id=target_id, role=role, trade_mode=trade_mode)}")
        await member_admin.notify_approved(ctx.bot, self.users, target_id, role)

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

        # The rules (a numeric id; never the caller's own) are the one write
        # path's, shared with the admin page (bot/core/member_admin.py).
        from bot.core import member_admin
        res = member_admin.revoke(self.users, target_id, by=self._get_tg_id(update),
                                  via=member_admin.TELEGRAM)
        if res.ok:
            SEP = "─" * 16
            await self._send(update,
                f"⚠️ {t('revoke_result', self._lang(update), sep=SEP, id=target_id)}")
        elif res.error == "invalid_tg_id_format":
            await self._send(update, f"{t('invalid_tg_id_format', self._lang(update))}")
        elif res.error == "cannot_revoke_self":
            await self._send(update, f"\U0001f534 {t('cannot_revoke_self', self._lang(update))}")
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
        try:
            ok = self.users.set_live_trading(target_id, True)
        except StoreWriteHeld as exc:
            # The grant IS in force for this process (the map holds it) and
            # is NOT on disk; "something broke" would read as "nothing
            # happened", which for a grant is the loosening direction.
            await self._send(update,
                f"\u26a0\ufe0f {t('grant_live_held', self._lang(update), id=target_id, cls=exc.detail)}")
            return
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
        from bot.core import member_admin
        res = member_admin.revoke_live(self.users, target_id, by=self._get_tg_id(update),
                                       via=member_admin.TELEGRAM)
        if res.ok:
            await self._send(update,
                f"\U0001f4dd {t('revoke_live_result', self._lang(update), name=html.escape(res.name), id=target_id)}")
        elif res.held:
            # In force for this process, NOT on disk: said as such, never as
            # "nothing happened".
            await self._send(update,
                f"\u26a0\ufe0f {t('revoke_live_held', self._lang(update), id=target_id, cls=res.params.get('cls'))}")
        elif res.error == "invalid_tg_id":
            await self._send(update, f"\U0001f534 {t('invalid_tg_id', self._lang(update))}")
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
        # The rules are the one write path's (bot/core/member_admin.py),
        # shared with the admin page: a website-only member is
        # `web:<site user id>` and may hold any tier but the operator's (the
        # website reads `plan == 'admin'` as THE OPERATOR); a Telegram id is
        # ASCII digits only (`str.isdigit` is true for "١٢" and "１２"). The
        # website's `users.plan` mirror is started there too, from either door.
        from bot.core import member_admin
        from bot.utils.user_store import TIERS
        res = member_admin.set_tier(self.users, target_id, tier,
                                    by=self._get_tg_id(update),
                                    via=member_admin.TELEGRAM)
        if not res.ok:
            if res.error == "invalid_tier":
                await self._send(update,
                    f"\U0001f534 {t('invalid_tier', self._lang(update), tier=html.escape(tier), valid=', '.join(f'<code>{_t}</code>' for _t in TIERS))}")
            elif res.error == "user_not_found_id_period":
                await self._send(update, f"\U0001f534 {t('user_not_found_id_period', self._lang(update), id=target_id)}")
            elif res.error in ("invalid_tg_id", "set_tier_web_admin"):
                await self._send(update, f"\U0001f534 {t(res.error, self._lang(update))}")
            else:
                await self._send(update, f"\U0001f534 {t('set_tier_failed', self._lang(update))}")
            return
        tier_label = self.users.tier_label(target_id)
        role = (res.after or {}).get("role") or "trader"
        await self._send(update,
            f"\U0001f3af {t('set_tier_result', self._lang(update), name=html.escape(res.name), id=target_id, tier_label=tier_label, role=role)}")
        # Tell the member: "changed", not "upgraded", since the same command
        # lowers a tier.
        await member_admin.notify_tier(ctx.bot, self.users, target_id, tier)

    async def _cmd_channel(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """/channel — manage marketing channel auto-posting.

        TWO AUDIENCES, TWO REACHES. The bot's operator runs the forwarder:
        the switch, every registered group, posting to all of them, and which
        group reads full calls. An admin of a group the bot sits in manages
        THAT group: running /channel there adds it, /channel shows its own
        status, and /channel remove takes it out. Every subcommand used to be
        open to any group's admin, so the admin of one group could switch the
        operator's posting off, read the list of every other group, remove
        them, add arbitrary ids, and post a test to all of them.
        """
        is_bot_admin = self._is_admin(update)
        chat = update.effective_chat
        in_group = bool(chat and chat.type in ("group", "supergroup"))
        is_group_admin = False
        if not is_bot_admin and in_group:
            try:
                member = await ctx.bot.get_chat_member(
                    update.effective_chat.id, update.effective_user.id)
                is_group_admin = member.status in ("creator", "administrator")
            except Exception:
                pass
        if not is_bot_admin and not is_group_admin:
            await self._send(update, f"\U0001f512 {t('admin_only', self._lang(update))}")
            return
        own_group = chat.id if (in_group and chat is not None) else None

        # Running /channel in a group adds that group: a group admin opting
        # their own group in to the teasers.
        if in_group and chat is not None:
            self.forwarder.detect_group(chat.id, chat.type, chat.title or "")

        args = ctx.args or []
        if not args and not is_bot_admin:
            kind = "full calls" if own_group in set(self.forwarder.full_ids) else "teasers"
            await self._send(update, (
                f"\U0001f4e1 <b>Channel Forwarder</b>\n\n"
                f"This group (<code>{own_group}</code>) is registered and gets {kind}.\n"
                f"<code>/channel remove {own_group}</code> \u2014 stop posting here.\n\n"
                f"<i>Every other setting is the bot operator's.</i>"))
            return
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
                full = set(self.forwarder.full_ids)
                for gid in groups:
                    kind = "full calls" if gid in full else "teaser"
                    msg += f"\u2022 <code>{gid}</code> \u2014 {kind}\n"
            msg += (
                "\n<b>Commands:</b>\n"
                "<code>/channel on</code> \u2014 enable auto-posting\n"
                "<code>/channel off</code> \u2014 disable auto-posting\n"
                "<code>/channel add &lt;chat_id&gt;</code> \u2014 add group\n"
                "<code>/channel remove &lt;chat_id&gt;</code> \u2014 remove group\n"
                "<code>/channel test</code> \u2014 send test message\n"
                "<code>/channel full &lt;chat_id&gt;</code> \u2014 a members' room: full call cards (bot admin)\n"
                "<code>/channel teaser &lt;chat_id&gt;</code> \u2014 back to teasers (bot admin)\n\n"
                "<i>A group gets a teaser for a live call: the market, and a link to "
                "the private chat where members read it. Closes are posted whole. "
                "Running /channel in a group adds that group.</i>"
            )
            await self._send(update, msg)
            return

        sub = args[0].lower()
        if not is_bot_admin:
            # A group admin reaches their own group only: remove (or add) by
            # its own id. Anything global is the operator's.
            target = None
            if sub in ("add", "remove") and len(args) >= 2:
                try:
                    target = int(args[1])
                except ValueError:
                    target = None
            if target is None or target != own_group:
                await self._send(update, f"\U0001f512 {t('admin_only', self._lang(update))}")
                return
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
        elif sub in ("full", "teaser") and len(args) >= 2:
            # Which room reads a live call in full is the operator's decision
            # about who is a member, so a group admin cannot make their own
            # group a members' room.
            if not is_bot_admin:
                await self._send(update, f"\U0001f512 {t('admin_only', self._lang(update))}")
                return
            try:
                gid = int(args[1])
            except ValueError:
                await self._send(update, "\u274c Invalid chat ID.")
                return
            if gid not in self.forwarder.group_ids:
                await self._send(update,
                    f"\u274c <code>{gid}</code> is not a registered group. Add it first.")
                return
            self.forwarder.set_full(gid, sub == "full")
            await self._send(update, (
                f"\u2705 <code>{gid}</code> now gets full call cards."
                if sub == "full" else
                f"\u2705 <code>{gid}</code> now gets teasers."))
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
        """/broadcast <message> — send a custom message to all marketing channels.

        The bot's operator only. It used to accept an admin of ANY group the
        bot sits in, so one group's admin could post any text, under the
        bot's name, into every other group: a phishing door with the
        product's own voice.
        """
        if not self._is_admin(update):
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
        # D7 and D30 over this same list. Signup is created_at — the store
        # has no first-confirm stamp — and a return is last_seen on or after
        # that day. Not-yet-due and unreadable rows stay out of the rate.
        # Nothing here places, stages, or confirms an order.
        _retention = render_retention_baseline(
            retention_baseline(all_users, datetime.now(UTC)))
        if not all_users:
            await self._send(
                update,
                f"\U0001f4cb {t('no_registered_users', self._lang(update))}"
                + "\n\n" + _retention
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

        lines.append("")
        lines.append(_retention)
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
            try:
                self.users.set_max_margin(target_id, None)
            except StoreWriteHeld as exc:
                await self._send(update, _cap_held(target_id, None, exc.detail))
                return
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
        try:
            self.users.set_max_margin(target_id, usd)
        except StoreWriteHeld as exc:
            await self._send(update, _cap_held(target_id, usd, exc.detail))
            return
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
