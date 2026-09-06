"""The start-here command group — a slice out of the handler.

`/start`, `/help`, `/status`, `/health`, `/dashboard`, `/version`, `/lang`,
`/leaderboard`, `/arena` and `/duel`, with the unknown-command reply, the
duel keyboard and its pick callback, the viewer-board handle read, and the
three status helpers (`_status_market_bias`, `_tick_age_s`,
`_tick_liveness`). These are the entry surface: what a new user sees first,
and what an operator reads to decide whether the engine is alive. Their
behaviour is covered where it always was (`test_registration_flow`,
`test_user_admission`, `test_status_tick_liveness`, `test_stall_diagnosis`,
`test_status_says_why_the_universe_shrank`,
`test_status_survives_an_unreadable_macro_calendar`,
`test_trading_blocked_by_surfaced`, `test_start_position_count`,
`test_start_dashboard_link`, `test_command_menu`, `test_board_cards`,
`test_arena_cards`, `test_bot_error_handler_version`);
`tests/test_handler_mixins.py` holds this class to the split's rules.

`_closed_on_utc_date` moved with the group because `/status` is its only
caller. The War Room menu, the dashboard keyboard and the dashboard link
went to `bot/skills/menu_keyboards.py`, a leaf, because the callback
handler on the host reads them too and a mixin must not import from the
handler.

A mixin, not a leaf: every method reads `self.engine` or `self.users` and
answers through `self._send`, and the access decisions (`_access_state`,
`_can_trade_live`, `_request_operator_admission`) stay on the host, where
the auth helpers live; they are declared below as the host contract.
"""
from __future__ import annotations

import asyncio
import html
import os
from datetime import datetime
from typing import TYPE_CHECKING, Optional

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from bot.compat import UTC
from bot.config import CONFIG
from bot.core.trade_gate import entry_gate, gate_label, gate_sentence
from bot.formatters.rich_cards import analyze_budget_line, monitor_checks_line, render_status_card, session_skip_line
from bot.skills.command_guard import guard
from bot.skills.menu_keyboards import _KB_DASH, _KB_WARROOM, _dashboard_url
from bot.utils.i18n import SUPPORTED_LANGS, get_user_lang, resolve_lang_choice, set_user_lang, t
from bot.utils.logger import system_log

if TYPE_CHECKING:
    from bot.core.engine import RuneClawEngine
    from bot.skills.chat_runtime import RateLimiter
    from bot.utils.user_store import UserStore


def _closed_on_utc_date(pos, day) -> bool:
    """True if a closed position's ``closed_at`` falls on the given UTC date.

    Handles both LivePosition objects and dict rows, and closed_at as a
    datetime or an ISO string. Used to make LIVE "Daily PnL" genuinely daily
    (closed_positions holds ALL closed trades ever).
    """
    ca = pos.get("closed_at") if isinstance(pos, dict) else getattr(pos, "closed_at", None)
    if ca is None:
        return False
    if isinstance(ca, str):
        try:
            ca = datetime.fromisoformat(ca)
        except Exception:
            return False
    try:
        if ca.tzinfo is None:
            ca = ca.replace(tzinfo=UTC)
        return ca.astimezone(UTC).date() == day
    except Exception:
        return False


class StartCommands:
    """The entry surface: start, help, status and the boards. Host contract below; methods after."""

    if TYPE_CHECKING:
        # Provided by TelegramHandler, and ONLY declared here — declarations,
        # never bodies; tests/test_handler_mixins.py checks every name against
        # what the handler really defines.
        engine: RuneClawEngine
        users: UserStore
        _limiter: RateLimiter
        _known_commands: list
        _last_pane: dict[int, str]
        _WEB_LINK_HINT: str

        async def _send(self, update: Update, text: str,
                        reply_markup=None, edit: bool = False) -> None: ...

        async def _send_error(self, update: Update, command_name: str, exc: Exception) -> None: ...

        async def _render_pane(self, pane: str, user_id: Optional[str] = None) -> str: ...

        async def _request_operator_admission(self, tg_id: str, name: str, ctx) -> bool: ...

        def _get_tg_id(self, update: Update) -> str: ...

        def _lang(self, update: Update) -> str: ...

        def _is_admin(self, update: Update) -> bool: ...

        def _is_admin_id(self, tg_id: str) -> bool: ...

        def _access_state(self, tg_id: str) -> str: ...

        def _can_trade_live(self, tg_id) -> bool: ...

        def _caller_executor(self, update: Update): ...

        def _footer(self) -> str: ...

        def _seed_lang_from_telegram(self, update: Update, tg_id: str) -> bool: ...

    async def _cmd_version(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """/version — bot version + mode. Lightweight liveness check (rate-limited,
        no sensitive data)."""
        uid = update.effective_user.id if update.effective_user else 0
        if not self._limiter.allow(uid):
            return
        from bot import __version__

        from bot.utils.build_info import short as build_short
        mode = "LIVE" if CONFIG.is_live() else ("PAPER" if CONFIG.simulation_mode else "IDLE")
        # `__version__` is hand-maintained and has read "0.1.0" since the repo
        # was created, so /version answered "which code is running?" with a
        # constant. The build line is the part that can actually differ between
        # two runs, which is the only reason anyone asks.
        await self._send(update,
            f"⚔️ <b>RUNECLAW</b> v{html.escape(__version__)}\n"
            f"Build: <code>{html.escape(build_short())}</code>\n"
            f"Mode: <code>{mode}</code>")

    async def _cmd_start(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """GetClaw welcome — auto-registers new users."""
        # H-16 FIX: rate limit /start
        uid = update.effective_user.id if update.effective_user else 0
        if not self._limiter.allow(uid):
            return  # rate limited
        now = datetime.now(UTC).strftime("%H:%M UTC")
        user_tg = update.effective_user
        tg_id = self._get_tg_id(update)
        user_name = html.escape(user_tg.first_name) if user_tg else "Trader"

        # Auto-register on first contact
        _first_contact = self.users.get(tg_id) is None
        record = self.users.register(tg_id, name=user_name)
        if _first_contact:
            self._seed_lang_from_telegram(update, tg_id)
            # `t.me/<bot>?start=ref_<code>` arrives here as ctx.args[0]. /start
            # discarded it, so every invite link the website could mint was
            # inert and no share was ever attributable. Recorded once, on first
            # contact only — a returning user re-entering via someone else's
            # link does not get reassigned.
            from bot.formatters.share_invite import parse_start_payload
            _ref = parse_start_payload((ctx.args or [None])[0])
            if _ref:
                self.users.record_referrer(tg_id, _ref)

        # /start is one of the few UNGUARDED commands, so it used to hand a
        # stranger the full status card — equity, positions, win rate — and the
        # next command they tried answered "locked to its configured operator".
        # A welcome that promises what the following tap refuses is the reason
        # people concluded the bot was broken and left. Check the same gate the
        # commands check, and say the same thing about it.
        _access = self._access_state(tg_id)
        if _access == "needs_approval" or not record.get("authorized", False):
            from bot.formatters.onboarding import welcome_notice
            lang = get_user_lang(self.users, tg_id)
            notified = await self._request_operator_admission(
                tg_id, (user_tg.first_name if user_tg else ""), ctx)
            await self._send(update, welcome_notice(
                user_name, tg_id, access="needs_approval",
                operator_notified=notified, lang=lang))
            return

        # Authorized user — GetClaw ready
        role = record.get("role", "trader")
        from bot.core.live_readiness import mode_label
        mode_str = mode_label()
        user_portfolio = self.engine.user_portfolios.get(tg_id)
        state = user_portfolio.snapshot()
        # The headline answers "would a new entry be accepted", not "is one
        # specific breaker open". This card read
        # `self.engine.risk.circuit_breaker_active` alone — ONE of the five
        # conditions the pre-execute gate checks, and not the one that fired
        # in either of the two incidents this rule was written after. It also
        # read the SHARED breaker, so a user whose OWN daily-loss breaker was
        # open saw a green "Active". bot/core/trade_gate.py carries the whole
        # list once so this cannot drift from the gate again.
        _gate = entry_gate(self.engine, str(tg_id or ""))
        cb_active = bool(_gate["blocked"])

        # Displayed counts — defined for BOTH branches (the paper branch
        # previously never set them and the template references both).
        _filled_count = 0
        _pending_count = 0

        # LIVE FIX: show real exchange equity in LIVE mode
        if mode_str == "LIVE":
            # Truthful equity: never fake paper $10k when the live balance can't
            # be read. resolve_display_equity returns (None, "unavailable") in
            # that case so the card says so instead of the paper baseline.
            display_equity, _eq_source = await self.engine.resolve_display_equity(tg_id)
            # Per-user isolation: route through the CALLER's executor so this
            # status card reflects the SAME account /positions and /performance
            # use (resolves to the shared operator executor when
            # PER_USER_LIVE_ENABLED is off -- byte-identical default). A caller
            # with no access (per-user on, no linked account) sees zero
            # positions rather than the operator's.
            executor = self._caller_executor(update)
            open_pos = len(executor.open_positions) if executor else 0
            # Count filled vs pending separately
            _all_tracked = list(executor._positions.values()) if executor else []
            _filled_count = sum(1 for p in _all_tracked if p.status == "open")
            _pending_count = sum(1 for p in _all_tracked if p.status == "pending_fill")

            # Cross-check with exchange for accurate pending order count
            # The bot's internal count can be stale after restarts
            if executor:
                try:
                    _ex = await executor._get_exchange()
                    _ex_orders = await _ex.fetch_open_orders(
                        params={"productType": "USDT-FUTURES"})
                    # Only count limit orders (not SL/TP trigger orders)
                    _ex_limit_orders = [
                        o for o in (_ex_orders or [])
                        if (o.get("type") or "").lower() == "limit"
                    ]
                    _exchange_pending = len(_ex_limit_orders)
                    if _exchange_pending != _pending_count:
                        _pending_count = _exchange_pending
                except Exception:
                    pass  # Fall back to internal count

            # Fallback: if no locally-tracked positions, check exchange directly.
            # This catches orphan positions (opened but lost from local state).
            # Live incident (LTC, 2026-07-13): this fallback used to correct
            # `open_pos` — a variable the card never displays — while the
            # template shows `_filled_count`, so /start said "Open positions: 0"
            # with a live position on the exchange. Correct the DISPLAYED count.
            if _filled_count == 0 and executor:
                try:
                    _ex = await executor._get_exchange()
                    _ex_pos = await _ex.fetch_positions()
                    _ex_open = [p for p in (_ex_pos or [])
                                if isinstance(p, dict) and float(p.get("contracts") or 0) > 0]
                    if _ex_open:
                        _filled_count = len(_ex_open)
                        open_pos = len(_ex_open)
                except Exception:
                    pass

            # Win rate from the single shared source of truth so this card and
            # the Portfolio card (which now routes to the SAME account via
            # engine.viewer_executor) can never disagree — the reported
            # 38%-vs-52% mismatch.
            from bot.skills.live_stats import live_win_stats, streak_badge
            _start_stats = live_win_stats(executor.closed_positions if executor else [])
            # "N/A" covers both absences: no closes at all, and closes that
            # none of which carried a readable P&L. `win_rate` is None in the
            # second case — formatting it as 0 would put a measured total
            # defeat on the card in place of "we could not price these".
            if _start_stats["total"] and _start_stats["win_rate"] is not None:
                win_rate = f"{_start_stats['win_rate']:.0f}"
            else:
                win_rate = "N/A"
            # TG-2: a win/loss streak chip next to the win rate — a small, real
            # signal of current form (only shows at >= 2 in a row).
            _streak_badge = streak_badge(_start_stats.get("streak"))
        else:
            display_equity = state.equity_usd
            open_pos = state.open_positions
            _filled_count = state.open_positions   # paper: template shows this
            win_rate = f"{state.win_rate:.0%}".replace("%", "")
            _streak_badge = ""

        SEP = "\u2500" * 16
        # Unknown gets its own icon and word. Rounding an unreadable gate up to
        # a green "Active" is the bug; rounding it down to "Paused" is the
        # false alarm. Neither is the honest answer, so it says which it is.
        _gate_label = gate_label(_gate)
        status_icon = {"Active": "\U0001f7e2", "Paused": "\U0001f534"}.get(
            _gate_label, "⚪")
        status_label = _gate_label
        mode = mode_str
        # display_equity is None only in LIVE mode when the balance is
        # unreadable \u2014 show that plainly, never the paper baseline. The
        # template renders {equity} verbatim (no hardcoded "$") so the
        # "unavailable" word isn't prefixed with a dollar sign.
        equity = f"${display_equity:,.2f}" if display_equity is not None else "unavailable"
        time = now

        # Show user's tier and trading mode
        tier_label = self.users.tier_label(tg_id)
        can_live = self._can_trade_live(tg_id)
        trade_mode = "\U0001f525 Live" if can_live else "\U0001f4dd Paper"

        # Get user language preference
        lang = get_user_lang(self.users, tg_id)

        # Bilingual status labels
        # The bilingual line must not disagree with the English one. It said
        # 活躍 whenever the breaker was closed, which for an unreadable gate is
        # a green claim the English half no longer makes.
        if cb_active:
            status_label_zh = t("status_paused", "zh")
        elif _gate_label != "Active":
            status_label_zh = status_label
        else:
            status_label_zh = t("status_active", "zh")
        trade_mode_zh = t("mode_live", "zh") if can_live else t("mode_paper", "zh")
        pending_str = f' | Pending orders: <code>{_pending_count}</code>' if _pending_count > 0 else ''
        pending_str_zh = f' | 掛單: <code>{_pending_count}</code>' if _pending_count > 0 else ''

        # Format win rate with % sign, plus the streak chip when there is one.
        wr_display = f"{win_rate}%" if win_rate != "N/A" else "N/A"
        if _streak_badge:
            wr_display += f" · {_streak_badge}"

        body = t('welcome_ready', lang,
                 name=user_name,
                 status_icon=status_icon,
                 status_label=status_label,
                 status_label_zh=status_label_zh,
                 mode=mode,
                 equity=equity,
                 filled=_filled_count,
                 pending_str=pending_str,
                 pending_str_zh=pending_str_zh,
                 win_rate=wr_display,
                 tier=tier_label,
                 trade_mode=trade_mode,
                 trade_mode_zh=trade_mode_zh,
                 web_url=_dashboard_url(),
                 time=time)

        msg = f"<b>RUNECLAW</b>\n{SEP}\n\n{body}"
        # A one-word headline cannot say WHY, and "Paused" without a cause is
        # a dead end — the operator's only route to the answer during the
        # 2026-08-01 auth halt was to attempt a trade and read the rejection.
        # Named causes, straight from the fields the gate reads.
        _why = gate_sentence(_gate)
        if _why:
            msg += f"\n\n⚠️ <i>{html.escape(_why)}</i>"
        await self._send(update, msg, reply_markup=_KB_WARROOM)

    async def _handle_unknown_command(self, update: Update,
                                      ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """Answer slash commands the bot does not have.

        Previously these were swallowed in silence — the free-text handler
        excludes COMMAND and nothing caught the remainder — so every typo
        (and every operator-only command a normal user tried) looked like a
        dead bot. Now it always replies, suggests the nearest REAL command,
        and points at the menu. Rate-limited like every other entry point.
        """
        try:
            msg = update.effective_message
            text = (msg.text or msg.caption or "") if msg else ""
            name = text.split()[0].lstrip("/").split("@")[0] if text.strip() else ""
            uid = update.effective_user.id if update.effective_user else 0
            if not self._limiter.allow(uid):
                return
            from bot.skills.command_menu import unknown_command_reply
            await self._send(update, unknown_command_reply(
                name, list(self._known_commands),
                is_admin=self._is_admin(update),
                lang=get_user_lang(self.users, self._get_tg_id(update))))
        except Exception:
            system_log.debug("unknown-command reply failed", exc_info=True)

    async def _cmd_help(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """GetClaw help — organized command reference."""
        # H-16 FIX: rate limit /help
        uid = update.effective_user.id if update.effective_user else 0
        if not self._limiter.allow(uid):
            return  # rate limited
        tg_id = self._get_tg_id(update)
        is_auth = self.users.is_authorized(tg_id)
        user = self.users.get(tg_id)
        role = user.get("role", "pending") if user else "pending"
        lang = get_user_lang(self.users, tg_id)

        _sep = "\u2500" * 20

        # /help is the one command a refused user is pointed at, so it has to
        # be TRUE for them. The grouped catalogue below already hides
        # operator-only groups on the grounds that "a command you are refused
        # looks exactly like a command that is broken" — that argument covers
        # the allowlist exactly, and 125 refused commands is the strongest
        # possible version of it. Someone not admitted gets the access notice
        # and the three commands that genuinely work for them
        # (ROLE_PERMISSIONS["pending"]: start, help, lang).
        if not is_auth or self._access_state(tg_id) == "needs_approval":
            from bot.formatters.onboarding import access_denied_notice
            if not user:
                self.users.register(tg_id, name=(
                    update.effective_user.first_name
                    if update.effective_user else ""))
                self._seed_lang_from_telegram(update, tg_id)
                lang = get_user_lang(self.users, tg_id)
            _notified = await self._request_operator_admission(
                tg_id,
                (update.effective_user.first_name
                 if update.effective_user else ""),
                ctx)
            await self._send(update,
                access_denied_notice(tg_id, operator_notified=_notified,
                                     lang=lang)
                + f"\n{_sep}\n"
                + t("help_pending_available", lang))
            return

        tier_label = self.users.tier_label(tg_id)
        can_live = self._can_trade_live(tg_id)
        trade_mode = "\U0001f525 Live" if can_live else "\U0001f4dd Paper"

        msg = (
            f"{t('help_title', lang)}\n"
            f"{_sep}\n"
            f"{tier_label} | {trade_mode}\n\n"
            f"{t('help_tip', lang)}\n\n"
            f"{t('help_market', lang)}\n\n"
            f"{t('help_trading', lang)}\n\n"
            f"{t('help_portfolio', lang)}\n\n"
            f"{t('help_strategy', lang)}\n\n"
            f"{t('help_tools', lang)}\n\n"
            f"{t('help_controls', lang)}\n\n"
            f"{t('help_account', lang)}\n\n"
            f"{t('help_ai', lang)}\n"
        )

        # Live trading (show for users with live access)
        if can_live or role == "admin":
            msg += f"\n{t('help_live', lang)}\n"

        # Admin section
        if role == "admin":
            msg += (
                f"\n{t('help_admin', lang)}\n"
                f"/stockscan \u2014 {'股市掃描' if lang == 'zh' else 'stock market scan'}\n"
                f"/channel \u2014 {'管理自動發佈' if lang == 'zh' else 'manage auto-posting'}\n"
                f"/broadcast \u2014 {'群組廣播' if lang == 'zh' else 'send message to groups'}\n"
            )

        await self._send(update, msg)

        # The grouped command reference (bot/skills/command_catalog.py).
        # /help used to name 5 of 125 commands, so ~110 working features were
        # discoverable only by word of mouth — the whole of "there are too
        # many commands and it is not clear what they do". Operator-only
        # groups are hidden from normal users on purpose: a command you are
        # refused looks exactly like a command that is broken. Split on GROUP
        # boundaries so a section is never torn across two messages.
        try:
            from bot.skills.command_catalog import render_group, render_help
            _admin = self._is_admin(update)
            _arg = (ctx.args or [None])[0]
            # The catalogue localizes per item in every dictionary language
            # and falls back to English per item, so a coverage gap shows as
            # one English line rather than an English wall.
            _hl = lang
            if _arg:
                # "/help trading" — one section instead of the whole wall.
                await self._send(update, render_group(_arg, is_admin=_admin, lang=_hl))
            else:
                for chunk in render_help(is_admin=_admin, lang=_hl):
                    await self._send(update, chunk)
        except Exception:
            system_log.debug("grouped help render failed", exc_info=True)

    @guard("lang")
    async def _cmd_lang(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """Choose the bot's language — any of the fourteen the website offers.

        ``/lang`` shows a keyboard built from SUPPORTED_LANGS; ``/lang es``,
        ``/lang español`` and ``/lang spanish`` all work. The dictionary
        behind it (bot/utils/i18n.py) carries every key in every language, so
        the choice changes the bot's own words, not only the model's reply.
        """
        tg_id = self._get_tg_id(update)
        current_lang = get_user_lang(self.users, tg_id)

        args = ctx.args or []
        if args:
            new_lang = resolve_lang_choice(" ".join(args))
            if new_lang is not None:
                set_user_lang(self.users, tg_id, new_lang)
                await self._send(update, t("lang_switched", new_lang))
                return

        # No args or invalid — show buttons, two per row, in the web's order.
        codes = list(SUPPORTED_LANGS)
        buttons = [
            [InlineKeyboardButton(SUPPORTED_LANGS[c], callback_data=f"lang:{c}")
             for c in codes[i:i + 2]]
            for i in range(0, len(codes), 2)
        ]
        await self._send(update,
            f"🌐 {t('lang_prompt', current_lang)}\n\n"
            f"<b>{SUPPORTED_LANGS.get(current_lang, 'English')}</b>",
            reply_markup=InlineKeyboardMarkup(buttons))

    @staticmethod
    def _tick_age_s(engine) -> "float | None":
        """Seconds since the engine last started a tick, or None if it never
        has. Never raises — a status card must not fail on a liveness read.

        Deliberately NOT placed between a @guard decorator and its command:
        an insertion there silently re-targets the decorator (it wrapped this
        helper and left /leverage unguarded once — caught by the audience
        ratchet test, and now by this comment)."""
        try:
            import time as _t
            last = getattr(engine, "_last_tick_started_ts", None)
            if last is None:
                return None
            return max(0.0, _t.monotonic() - float(last))
        except Exception:
            return None

    @staticmethod
    def _tick_liveness(engine) -> "tuple[bool, float | None]":
        """(stalled, seconds_until_next_tick) for the status card.

        The verdict is delegated to ProactiveMonitor._is_tick_stalled — the
        SAME predicate the watchdog pages on — so /status and the CRITICAL
        alert can never disagree. That predicate treats time inside a
        DECLARED wait as healthy: the engine stamps _next_tick_due_ts before
        every inter-tick sleep, covering both the smart-scan quiet-market
        interval (up to 600s) and the run loop's failure backoff (capped at
        300s). A bare age threshold cannot tell either apart from a hang.

        Fail-safe: on any error return (False, None) — an unreadable liveness
        read must not manufacture a stall warning, and the watchdog alert is
        the authority on a real one either way.

        Placed here, AWAY from the @guard("leverage") below: inserting a
        method between a decorator and its command silently re-targets the
        decorator and leaves that command unguarded.
        """
        try:
            import time as _t
            from bot.core.proactive_monitor import ProactiveMonitor as _PM
            last = getattr(engine, "_last_tick_started_ts", None)
            due = getattr(engine, "_next_tick_due_ts", None)
            now = _t.monotonic()
            stalled = _PM._is_tick_stalled(
                last, now, _PM.TICK_STALL_THRESHOLD_S, next_due=due)
            next_in = None
            if due is not None and not stalled:
                next_in = max(0.0, float(due) - now)
            return (bool(stalled), next_in)
        except Exception:
            return (False, None)

    def _duel_keyboard(self, rounds) -> "InlineKeyboardMarkup | None":
        """One row of buttons per uncalled round.

        The callback data is `duel:<round_id>:<pick>` — ids and enum tokens,
        never prose. #999 shipped a card whose callback carried a human-readable
        label where the lookup expected a symbol, and it rendered zero times in
        production while looking perfectly correct in the source.
        """
        from bot.formatters.duel_card import pick_callback_data
        rows = []
        for r in rounds or []:
            if r.get("my_call") or not r.get("callable"):
                continue
            rid = r.get("id")
            if rid is None:
                continue
            sym = str(r.get("symbol") or "?")
            rows.append([
                InlineKeyboardButton(f"{sym} LONG", callback_data=pick_callback_data(rid, "long")),
                InlineKeyboardButton("SHORT", callback_data=pick_callback_data(rid, "short")),
                InlineKeyboardButton("PASS", callback_data=pick_callback_data(rid, "pass")),
            ])
        return InlineKeyboardMarkup(rows) if rows else None

    async def _cmd_duel(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """/duel — today's Daily Duel card: call LONG, SHORT or PASS before the
        agent's own call is shown, and the market settles it 24h later.

        The duel is scored in the web app. This surface reads the card and posts
        a call through the same code the website uses; scoring it again here
        would be a second set of rules that agreed only until one changed."""
        import asyncio as _aio
        from bot.utils.duel_pull import fetch_card
        from bot.formatters.duel_card import render_card
        status, data = await _aio.to_thread(fetch_card, self._get_tg_id(update))
        if status == 404:
            await self._send(update, self._WEB_LINK_HINT)
            return
        # A failed read is stated as a failed read. Sending an empty card would
        # say "no rounds today", which is a claim about the game that an
        # unreadable feed does not support.
        if status != 200 or not data:
            await self._send(update, render_card(None))
            return
        await self._send(update, render_card(data),
                         reply_markup=self._duel_keyboard(data.get("rounds")))

    async def _handle_duel_callback(self, update, data: str) -> None:
        """A tap on one of the duel buttons: `duel:<round_id>:<pick>`."""
        import asyncio as _aio
        from bot.utils.duel_pull import place_pick, fetch_card
        from bot.formatters.duel_card import (
            render_pick_result, render_card, parse_callback_data)
        parsed = parse_callback_data(data)
        if parsed is None:
            return
        round_id, pick = parsed
        tg_id = self._get_tg_id(update)
        status, result = await _aio.to_thread(place_pick, tg_id, round_id, pick)
        await self._send(update, render_pick_result(result, status))
        # Redraw so the called round loses its buttons and the agent's stance
        # appears; a stale card still offering a call that was just recorded
        # invites a second tap that can only be refused.
        c_status, card = await _aio.to_thread(fetch_card, tg_id)
        if c_status == 200 and card:
            await self._send(update, render_card(card),
                             reply_markup=self._duel_keyboard(card.get("rounds")))

    @guard("dashboard")
    async def _cmd_leaderboard(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """/leaderboard — the public verified board, from the bot's own registry.

        Read locally, not pulled from the web: bot/proofofpnl/leaderboard.py IS
        the source of truth (the engine publishes each opted-in member's sealed
        statement into it), so a network hop would add a failure mode and a
        staleness window for data already on this disk.
        """
        try:
            from bot.formatters.board_cards import render_leaderboard
            from bot.proofofpnl.leaderboard import (get_leaderboard_registry,
                                                    rank_entries)
            # Rank EVERY entry, not the top 50: ranked_total is the card's
            # denominator, and a capped scan would print "10 of 50" on an
            # 80-member board — a fabricated total, which is the one thing the
            # denominator exists to prevent. The display cut happens in the
            # renderer, where it is stated.
            entries = get_leaderboard_registry().all_entries()
            ranked = rank_entries(entries, limit=max(1, len(entries)))
            handle, opted_in = self._viewer_board_handle(self._get_tg_id(update))
            await self._send(update, render_leaderboard(
                ranked, viewer_handle=handle, ranked_total=len(ranked),
                viewer_opted_in=opted_in))
        except Exception as exc:
            await self._send_error(update, "the leaderboard", exc)

    @guard("dashboard")
    async def _cmd_arena(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """/arena — the Paper Arena's season standings and live tape.

        Read over the wire, unlike /leaderboard: the Arena lives entirely in
        the web app's database and there is deliberately no second copy of the
        season rules here. The two boards are fetched independently so one
        timing out does not blank the other.
        """
        try:
            from bot.formatters.arena_cards import render_arena
            from bot.utils.arena_pull import fetch_arena
            season, tape = await asyncio.to_thread(fetch_arena)
            handle, _opted_in = self._viewer_board_handle(self._get_tg_id(update))
            await self._send(update, render_arena(season, tape,
                                                  viewer_handle=handle))
        except Exception as exc:
            await self._send_error(update, "the arena", exc)

    def _viewer_board_handle(self, tg_id: str) -> tuple[Optional[str], Optional[bool]]:
        """This viewer's leaderboard handle, and whether that is a MEASUREMENT.

        Returns ``(handle, opted_in)`` where ``opted_in`` is tri-state:
        ``True`` with a handle, ``False`` for a confident "not opted in", and
        ``None`` when nobody has read the opt-in set yet — which must not be
        reported to the member as "you are not on the board".

        The opt-in set lives in the website's ``users.leaderboard_handle`` and
        reaches this process through the engine's desired-state pull, which
        caches it on ``_user_board_handles`` only after a SUCCESSFUL fetch
        (a transport failure returns early and leaves it untouched). So its
        absence genuinely means unknown, and reading it costs no network hop.

        The operator is not in that set — their handle comes from the env var
        the engine publishes under — so they are resolved separately. Without
        this they would be told they were not on a board their own row is on.
        """
        from bot.proofofpnl.leaderboard import HANDLE_MAX

        def _canon(raw) -> str:
            # The board's rows carry `build_row`'s normalisation, so the
            # viewer's handle must arrive in the SAME form or it can never
            # match. The web caps handles at 20 and agrees already; the
            # operator's env var has no cap at all, and a 25-character one was
            # told "you are opted in but not ranked yet" with its own row at
            # rank 1 on the same card.
            return str(raw or "").strip()[:HANDLE_MAX]

        operator_handle = _canon(os.environ.get("PROOFOFPNL_LEADERBOARD_HANDLE"))
        if operator_handle and self._is_admin_id(tg_id):
            return operator_handle, True
        mapping = getattr(self.engine, "_user_board_handles", None)
        if not isinstance(mapping, dict):
            return None, None                    # never pulled — unknown
        handle = _canon(mapping.get(str(tg_id)))
        return (handle, True) if handle else (None, False)

    @guard("status")
    async def _cmd_health(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """Show system health status."""
        text = self.engine.health.format_telegram()
        # The same claim /status stopped making: "SYSTEM HEALTH: HEALTHY" over
        # a monitor whose checks are down is a headline about the plumbing
        # that leaves out the alerting. Unreadable is said, not omitted.
        try:
            _mon = getattr(self, "monitor", None)
            _down = monitor_checks_line(
                _mon.check_failures() if _mon is not None else None,
                self._lang(update))
            if _down:
                text += f"\n{_down}"
        except Exception:
            text += f"\n{t('fmt_monitor_checks_unread', self._lang(update))}"
        await self._send(update, text)

    @guard("dashboard")
    async def _cmd_dashboard(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        # L-14 FIX: key by user_id instead of chat_id to avoid cross-user pane leaks
        user_id = self._get_tg_id(update)
        pane = self._last_pane.get(user_id, "status")
        body = await self._render_pane(pane, user_id=user_id)
        text = body + self._footer()
        await self._send(update, text, reply_markup=_KB_DASH)
        self._last_pane[user_id] = pane

    def _status_market_bias(self) -> str:
        """The bias line for /status, or an honest "unread" when the calendar
        cannot answer.

        `self.engine.macro_calendar.evaluate()` was called bare at the top of
        /status, before any try. The card is a composite -- equity, positions,
        drawdown, ticks, budget -- and a calendar read failing killed all of
        it: the operator typed /status and got "Something broke on my end",
        on the one card meant for exactly the moment something is broken.
        Composite views OMIT the source that failed and say so; they do not
        go dark. A bias that could not be read is labelled as unread rather
        than printed as a level, because "Normal" beside a failed read is a
        measurement that was never taken.
        """
        try:
            macro = self.engine.macro_calendar.evaluate()
            return str(macro.state.value).replace("_", " ").title()
        except Exception as exc:
            system_log.warning("/status: macro calendar unreadable: %s", exc)
            return t("val_bias_unread", "en")

    @guard("status")
    async def _cmd_status(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        user_id = self._get_tg_id(update)
        # Show per-user equity in status
        user_portfolio = self.engine.user_portfolios.get(user_id)
        state = user_portfolio.snapshot()
        # The headline must answer "can we trade", not "is one specific
        # breaker open". circuit_breaker_active covers daily_loss / drawdown /
        # streak / manual and NOT the warning-rate breaker, so this card
        # printed a green ACTIVE while WARNING_RATE_BREAKER was rejecting live
        # trades. That fix named ONE more gate; two others were found later.
        # entry_gate carries every condition the pre-execute gate checks, for
        # THIS caller's account rather than only the shared one.
        _g = entry_gate(self.engine, str(user_id or ""))
        blocked_by = "; ".join(_g["reasons"])
        cb = bool(_g["blocked"])
        _bias = self._status_market_bias()
        # Two different questions, and one expression was answering both.
        #
        # WHICH BOOK TO READ is "is this a real exchange account?" — true
        # whenever simulation is off, including when live is not armed, because
        # the account can still hold positions from an earlier session.
        #
        # WHAT THE CARD SAYS is a different question: `is_live()` also needs the
        # arm flag and the chat allow-list, so a bot with SIMULATION_MODE=false
        # and live never armed places nothing while this card announced LIVE.
        real_account = not CONFIG.simulation_mode
        from bot.core.live_readiness import mode_label as _mode_label
        mode = _mode_label()
        # LIVE FIX: show real exchange equity and live position count.
        # Truthful equity: None in LIVE mode means the balance is unreadable —
        # the status card renders "unavailable" rather than the paper baseline.
        if real_account:
            equity, _eq_source = await self.engine.resolve_display_equity(user_id)
            executor = self.engine.live_executor
            open_count = len(executor.open_positions)
            # BUGFIX: closed_positions is ALL closed trades ever, so summing it
            # made "Daily PnL" an all-time cumulative figure that never reset.
            # Filter to positions closed TODAY (UTC) so it's genuinely daily.
            _today = datetime.now(UTC).date()
            # Tri-state, because "nothing closed today" and "today's closes
            # could not be priced" are different days. The first really is
            # $0.00; the second rendered as "⚪ 0.00%" beside a "/ +5.0% limit",
            # a measured flat day manufactured from no measurement.
            from bot.formatters.realized_totals import realized_totals as _rt_daily
            _today_closed = [t for t in (executor.closed_positions or [])
                             if _closed_on_utc_date(t, _today)]
            _daily = _rt_daily(_today_closed)
            daily_pnl = (round(_daily["net"], 2)
                         if _daily["net"] is not None else None)
        else:
            equity = state.equity_usd if hasattr(state, "equity_usd") else 10_000.0
            open_count = state.open_positions
            daily_pnl = round(state.daily_pnl, 2) if hasattr(state, "daily_pnl") else 0.0
        # Show the drawdown the BREAKER ENFORCES, not the paper snapshot.
        # In LIVE mode the gate measures against a live high-water mark while
        # this card was rendering the paper portfolio's figure — which never
        # moves in pure-live operation. An operator could therefore read
        # "0.0%" from a gate that was refusing trades at 9%. Fail-safe: any
        # error falls back to the previous paper number rather than blanking
        # the line.
        #
        # ...and the fallback was the remaining half of the same defect. The
        # paper figure is a reasonable thing to show when the live read fails
        # — blanking the line on a transient fault is worse — but the card
        # printed it with NO STATEMENT of which one it was, beside the LIVE
        # limit. `drawdown_source` travels to the card now, so the fail-safe
        # is labelled rather than disguised.
        #
        # `if state.max_drawdown_pct else 0.0` was the other door: falsy, so
        # an unreadable paper drawdown became a measured 0.0% — the calmest
        # possible reading, manufactured, on the control that decides how much
        # real money is lost before the bot halts.
        # The LIMIT beside it must be the drawdown cap the breaker enforces.
        # It was CONFIG.risk.max_daily_loss_pct — the DAILY-LOSS cap, a
        # different control entirely — so the card read "0.0% / +5.0% limit"
        # while the drawdown breaker was set at 7%. That advertises a tighter
        # cap than exists, and the gauge bar divides by it too, so the bar was
        # wrong as well. #959 fixed the numerator and missed the denominator.
        # (`effective_limit_pct` accounts for live-vs-paper and any runtime
        # operator override, so the seam prefers it over this default.)
        from bot.formatters.drawdown_card import resolve_display_drawdown
        try:
            _st = self.engine.risk.drawdown_status()
        except Exception:
            _st = None
        drawdown, drawdown_source, drawdown_limit = resolve_display_drawdown(
            state.max_drawdown_pct, _st, CONFIG.risk.max_drawdown_pct)

        # BUGFIX: the status card renders daily_pnl through a percent formatter
        # (appends "%"), and the adjacent "/ +X% limit" is a percent-of-equity
        # daily-loss cap — so daily_pnl must be a PERCENT, not raw dollars.
        # Previously a −$56 daily figure printed as "−56.0%". Convert here.
        # None travels: dividing an unknown by equity would TypeError, and
        # substituting 0.0 here would put the manufactured zero back one line
        # after removing it.
        # ...and 0.0 for an unreadable EQUITY put the manufactured zero back
        # by the other door: "0.0%" beside a daily-loss cap reads as break-even.
        daily_pnl_pct = (None if daily_pnl is None
                         else (daily_pnl / equity * 100.0)
                         if equity and equity > 0 else None)

        # Loop liveness for the card. The stall VERDICT comes from the
        # watchdog's own predicate, not from the age — time inside a declared
        # sleep or a failure backoff is healthy waiting, and calling that a
        # stall spends the trust the line exists to earn.
        tick_stalled, next_tick_in_s = self._tick_liveness(self.engine)

        msg = render_status_card(
            mode=mode,
            active=not cb,
            equity=equity,
            open_positions=open_count,
            daily_pnl=(None if daily_pnl_pct is None else round(daily_pnl_pct, 2)),
            drawdown=drawdown,
            drawdown_source=drawdown_source,
            max_drawdown=drawdown_limit,
            market_bias=_bias,
            pending_ideas=len(self.engine.pending_ideas) if hasattr(self.engine, "pending_ideas") else 0,
            lang=self._lang(update),
            # Seconds since the engine last STARTED a tick. None when the
            # engine has not ticked yet (documented monotonic None-sentinel)
            # — the card then omits the line rather than printing a zero.
            tick_age_s=self._tick_age_s(self.engine),
            tick_stalled=tick_stalled,
            next_tick_in_s=next_tick_in_s,
            phase_timeout=getattr(self.engine, "_last_phase_timeout", None),
            phase_headroom=(self.engine.phase_headroom()
                            if hasattr(self.engine, "phase_headroom") else None),
            # Did the SL/TP monitor actually run? The degraded alert says open
            # positions "could be" unmonitored and sends the reader HERE, so
            # this is where the answer has to be — otherwise /status repeats
            # the symptom, which is the same hole the phase-cause carry fixed.
            position_watch=(self.engine.position_watch()
                            if hasattr(self.engine, "position_watch") else None),
            # What the last failing tick raised. Same reason as the two above:
            # the warning-rate breaker alert says new entries are suppressed
            # and sends the reader to /status, so /status has to be able to
            # answer. It could not, so the alert guessed a subsystem.
            tick_error=getattr(self.engine, "_last_tick_error", None),
        )
        # A red headline with no reason sends the operator hunting. Name the
        # blocker. The warning-rate breaker in particular had no operator
        # surface at all, so trades were rejected with the card reading green
        # and /health reporting the breaker clear.
        if blocked_by:
            if blocked_by.startswith("warning_rate:"):
                _key = blocked_by.split(":", 1)[1]
                msg += (f"\n⛔ Trading blocked: <b>warning-rate breaker</b> — "
                        f"infrastructure warnings firing too often "
                        f"(<code>{_key}</code>). Clears once the rate drops.")
            elif blocked_by.startswith("loss_streak:"):
                # The dedicated streak line below carries the probe timing;
                # this one only has to make the red headline legible.
                msg += ("\n⛔ Trading blocked: <b>loss-streak gate</b> — "
                        "see the streak line below for when a probe is due.")
            else:
                msg += (f"\n⛔ Trading blocked: <b>circuit breaker</b> "
                        f"(<code>{blocked_by}</code>).")
        # Will the analyze phase finish the universe it was handed? The
        # timeout line below says a phase died; it does not say the universe
        # is simply wider than the budget, which is the fix the operator can
        # actually apply. Shown only when a real shortfall is forecast from a
        # MEASURED rate — never as a guess, and never when it fits.
        # Shared with the degraded ALERT, which is the surface that actually
        # wakes someone. Inline here, the remedy lived on a screen you had to
        # go and open while the alert named only the phase that died.
        _budget = analyze_budget_line(
            getattr(self.engine, "_analyze_capacity", None), self._lang(update))
        if _budget:
            msg += f"\n{_budget}"
        # Which classes the sweep left out on purpose. Read off the scanner,
        # where the drop is recorded; None when no sweep has run yet, and the
        # renderer says nothing for None.
        _skipped = session_skip_line(
            getattr(getattr(self.engine, "scanner", None), "_session_dropped", None),
            self._lang(update))
        if _skipped:
            msg += f"\n{_skipped}"
        # Venue visibility: which exchange live orders route to right now
        # (admins switch with /venue; non-default venues matter to see).
        # Keyed on the ACCOUNT, not the armed state — an idle real account
        # still routes to a venue, and that is worth seeing before arming.
        if real_account:
            try:
                _v = self.engine.live_executor._venue
                msg += (f"\n🏦 Venue: <b>{_v.display_name}</b> "
                        f"({_v.quote}-margined) — /venue to switch")
            except Exception:
                pass
        # Strangle visibility: when the soft loss-streak gate is latched the
        # bot scans but cannot trade — say so instead of looking merely idle.
        try:
            ss = self.engine.risk.streak_state()
            if ss.get("latched"):
                p = ss.get("probe_in_seconds")
                probe = ("probing disabled" if p is None
                         else "probe trade allowed NOW" if p <= 0
                         else f"probe trade in {p / 3600.0:.1f}h")
                msg += (f"\n⚠️ Loss streak "
                        f"<code>{ss['consecutive_losses']}/{ss['soft_limit']}</code>"
                        f" — new entries gated ({probe}).")
        except Exception:
            pass
        # Is the alerting that would page about any of the above running? A
        # check that raised every tick used to silence the whole monitor with
        # nothing saying so; the monitor counts those now, and this is where
        # the count has to show. Unreadable is said, not omitted -- an absent
        # line here reads as "all checks up".
        try:
            _mon = getattr(self, "monitor", None)
            _down = monitor_checks_line(
                _mon.check_failures() if _mon is not None else None,
                self._lang(update))
            if _down:
                msg += f"\n{_down}"
        except Exception:
            msg += f"\n{t('fmt_monitor_checks_unread', self._lang(update))}"
        await self._send(update, msg, reply_markup=_KB_WARROOM)
