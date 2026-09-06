"""The inline-button dispatcher — a slice out of the handler.

`_handle_callback` is where every button tap lands: trade confirm and
reject, the drift-retry offer, position close and detail, the War Room
menu and the dashboard panes, pause/resume/stop, the yield actions, the
policy confirm, the duel picks, the language picker, and the admission
buttons. It authenticates the tap (`_check_auth`, the owner check on
trade callbacks, `_control_scope` on the stop/start ones) and then hands
off to the command that owns the surface — which is why its host contract
below names methods on five other groups as well as the handler's own.
Its behaviour is covered where it always was (`test_callback_owner_guard_is_fail_closed`,
`test_closeall_confirm_tg2b`, `test_pending_order_desync`, `test_audit_fixes_batch_1`,
`test_shared_engine_controls_are_operator_only`, `test_user_admission`);
`tests/test_handler_mixins.py` holds this class to the split's rules.

`safe_mode_notice` moved with it because the Safe Mode button is its only
caller: the text says the button is wired to nothing and points at the two
controls that act, and `tests/test_risk_controls_report_what_happened.py`
reads it from here.

A mixin, not a leaf: everything here reads `self.engine` or `self.users`
and answers through `self._send`. The two pieces of state it owns — the
confirmed-callback ids that make a double tap idempotent, and the pending
limit-price prompts the free-text intercept completes — are declared on the
mixin, not on the host.
"""
from __future__ import annotations

import asyncio
import html
import logging
import time
from typing import TYPE_CHECKING, Optional

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from bot.config import CONFIG
from bot.formatters.drift_offer import (
    atr_from_ohlcv,
    flatten_headline,
    paper_close_price,
    reanalyzed_idea,
    render_reanalyzed_offer,
    venue_fill_price,
)
from bot.formatters.rich_cards import display_symbol, fetch_analysis_data, market_context_line, rsi_label
from bot.skills.menu_keyboards import _KB_DASH, _KB_WARROOM
from bot.skills.scan_skill import callback_confirm_reject as _scan_callback
from bot.utils.exc_text import _safe_exc_text
from bot.utils.i18n import SUPPORTED_LANGS, get_user_lang, set_user_lang, t
from bot.utils.leveraged_return import _leveraged_pnl_usd, _leveraged_return_pct
from bot.utils.logger import audit, system_log
from bot.utils.user_store import is_vouchable
from bot.warroom.warroom_bot import render_emergency_stop as wr_emergency_stop
from bot.warroom.warroom_bot import render_pause as wr_pause
from bot.warroom.warroom_bot import render_start as wr_start
from bot.warroom.warroom_bot import render_strategy_mode as wr_strategy_mode

if TYPE_CHECKING:
    from bot.core.engine import RuneClawEngine
    from bot.marketing.channel_forwarder import ChannelForwarder
    from bot.skills.chat_runtime import RateLimiter
    from bot.utils.user_store import UserStore

logger = logging.getLogger(__name__)


def safe_mode_notice() -> str:
    """What the "Safe Mode" button says, now that it says something true.

    IT CHANGED NO STATE. The handler sent "Safe mode is on. I'll only take
    high-confidence setups from here." and wrote an audit record with
    result="OK" — a tamper-evident entry asserting a risk control was
    activated, for a control that does not exist. No threshold moved, no flag
    was set, nothing read it afterwards.

    The button sits between Pause and Stop Bot, both of which really act. That
    is what made it dangerous rather than merely wrong: an operator reaching
    for "make me safer" during a drawdown could press it, be told they were
    safer, and NOT press the one that works. A decoy on a risk panel costs
    more than a missing button.

    So it names what it is and routes to the controls that do act. Building a
    real safe mode — a reduce-only latch, a confidence floor — is a product
    decision about what the words should mean, and inventing one here would be
    the same overclaim wearing a different hat.
    """
    return (
        "⚠️ <b>Safe Mode is not wired to anything.</b>\n\n"
        "This button changed no setting. It previously reported that it had, "
        "which is worse than doing nothing — so it now says so instead.\n\n"
        "<b>What actually reduces risk right now:</b>\n"
        "• <b>Pause</b> — stops new entries. Open positions stay open "
        "and stay monitored.\n"
        "• <b>Stop Bot</b> — trips the breaker, clears queued ideas and "
        "flattens every account.\n\n"
        "<i>Use /risk to see what is currently blocking trades.</i>"
    )


class CallbackHandler:
    """Every button tap, authenticated and routed. Host contract below; methods after."""

    #: Callback ids already acted on, so a double tap on Confirm cannot place
    #: a second order — the mixin's own state, declared so the type checker
    #: knows it exists (created on first use).
    _confirmed_ids: set[str]
    #: Users mid-way through typing a limit price for a pending idea, keyed by
    #: user id; the free-text intercept on the host completes them.
    _pending_limit_input: dict

    if TYPE_CHECKING:
        # Provided by TelegramHandler or one of the groups it is composed of,
        # and ONLY declared here — declarations, never bodies;
        # tests/test_handler_mixins.py checks every name against what the
        # composed handler really provides.
        engine: RuneClawEngine
        forwarder: ChannelForwarder
        users: UserStore
        _limiter: RateLimiter
        _last_pane: dict[int, str]

        async def _send(self, update: Update, text: str,
                        reply_markup=None, edit: bool = False) -> None: ...

        async def _send_photo(self, update: Update, png: bytes, caption: str,
                              reply_markup=None) -> bool: ...

        async def _refuse_shared_control(self, update: Update, command: str) -> None: ...

        async def _request_operator_admission(self, tg_id: str, name: str, ctx) -> bool: ...

        async def _render_pane(self, pane: str, user_id: Optional[str] = None) -> str: ...

        def _get_tg_id(self, update: Update) -> str: ...

        def _lang(self, update: Update) -> str: ...

        def _is_admin(self, update: Update) -> bool: ...

        def _is_operator(self, update: Update) -> bool: ...

        def _check_auth(self, update: Update) -> bool: ...

        def _caller_executor(self, update: Update): ...

        def _control_scope(self, update: Update): ...

        def _can_trade_live(self, tg_id) -> bool: ...

        def _live_refusal_key(self) -> str: ...

        def _footer(self) -> str: ...

        @staticmethod
        def _callback_owner_ok(caller_uid: str | None, expected_uid: str | None) -> bool: ...

        @staticmethod
        def _uid_matches(caller_uid: str | None, expected_uid: str | None) -> bool: ...

        @staticmethod
        def _split_pos_close_owner(rest: str) -> tuple[str, str | None]: ...

        # On the other groups: the surfaces a button hands off to.
        async def _apply_policy_callback(self, update: Update, data: str) -> None: ...

        async def _handle_duel_callback(self, update, data: str) -> None: ...

        async def _flatten_all_accounts(self, update: Update) -> None: ...

        async def _cmd_latest_signal(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None: ...

        async def _cmd_open_positions(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None: ...

        async def _cmd_orders(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None: ...

        async def _cmd_performance(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None: ...

        async def _cmd_risk(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None: ...

        async def _cmd_strategy(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None: ...

        def _engine_free_usdt(self) -> Optional[float]: ...

        def _yield_client(self): ...

    async def _handle_callback(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        try:
            await query.answer()
        except Exception:
            # Telegram expires callback queries after ~15s; a tap retried after
            # a lag (or redelivered after a TimedOut) raises BadRequest "query
            # is too old" HERE — before any real work. Answering only clears
            # the client's loading spinner, so a stale one is cosmetic: never
            # abort the button's actual action for it, and never page the
            # operator with "Something broke" over an expired tap.
            pass

        # M-18 FIX: rate limit callback buttons
        uid = update.effective_user.id if update.effective_user else 0
        if not self._limiter.allow(uid):
            return  # rate limited

        if not self._check_auth(update):
            # "Your account is not linked. Use /start to register." was wrong
            # for the common case: they usually ARE registered — /start created
            # the record — and the allowlist is what refused them. Sending them
            # to re-run the command that already worked reads as a broken bot.
            from bot.formatters.onboarding import access_denied_notice
            _cb_id = self._get_tg_id(update)
            _notified = await self._request_operator_admission(
                _cb_id,
                (update.effective_user.first_name
                 if update.effective_user else ""),
                ctx)
            try:
                await query.edit_message_text(
                    access_denied_notice(_cb_id, operator_notified=_notified,
                                         lang=self._lang(update)),
                    parse_mode="HTML")
            except Exception:
                pass
            return

        data = query.data or ""
        chat_id = update.effective_chat.id

        # ── Audit F-11: destructive callbacks require role permission ──
        # _check_auth (allowlist-gated) above stops strangers; this stops an
        # authorized non-privileged user from pausing, emergency-stopping, or
        # switching strategy mode via an inline button.
        _DESTRUCTIVE_CB_PERM = {
            "risk_safe_mode": "halt", "risk_pause": "halt",
            "risk_emergency_stop": "halt", "emergency_confirm": "halt",
            "closeall_confirm": "halt",
        }
        _required_perm = _DESTRUCTIVE_CB_PERM.get(data)
        if _required_perm is None and data.startswith("mode_"):
            _required_perm = "mode"
        # Guardian intent-policy apply buttons change enforcement → same gate as
        # a strategy-mode change. Cancel is harmless (no perm needed).
        if _required_perm is None and data.startswith("policy_") and data != "policy_cancel":
            _required_perm = "mode"
        if _required_perm and not self.users.has_permission(self._get_tg_id(update), _required_perm):
            role = (self.users.get(self._get_tg_id(update)) or {}).get("role", "pending")
            await self._send(update,
                f"\U0001f512 Your role (<code>{role}</code>) cannot perform this action.",
                edit=True)
            audit(system_log, f"Destructive callback denied: {data}",
                  action="callback_denied", result="DENIED",
                  data={"data": data, "role": role})
            return

        # ── Admit a user who was turned away (admin only) ────
        # The operator's half of the access request. Same authority as
        # /approve — _is_admin-gated, attributed, audited — so a person can be
        # let in with one tap instead of an env edit and a redeploy, which is
        # what "add a user" used to cost.
        if data.startswith("admit:"):
            target_id = data.split(":", 1)[1].strip()
            if not self._is_admin(update):
                await self._send(update,
                                 f"\U0001f512 {t('admin_only', self._lang(update))}",
                                 edit=True)
                return
            if not is_vouchable(target_id):
                await self._send(update,
                                 f"\U0001f534 {t('invalid_tg_id_numeric', self._lang(update))}",
                                 edit=True)
                return
            granted = self.users.authorize(target_id, role="trader",
                                           by=self._get_tg_id(update))
            if not granted:
                await self._send(update,
                                 f"\U0001f534 {t('approve_failed', self._lang(update), id=html.escape(target_id))}",
                                 edit=True)
                return
            _target = self.users.get(target_id) or {}
            await self._send(update,
                f"✅ <b>{html.escape(_target.get('name') or 'User')}</b> "
                f"(<code>{target_id}</code>) is in — role <code>trader</code>, "
                f"paper trading.\n"
                f"<i>Live trading is separate: it still needs the env "
                f"allowlist.</i>", edit=True)
            try:
                await ctx.bot.send_message(
                    chat_id=int(target_id),
                    text=t("reg_admitted_notice",
                           get_user_lang(self.users, target_id), role="trader"),
                    parse_mode="HTML")
            except Exception as exc:
                # Say the ping failed rather than letting the operator assume
                # the person knows. The admission itself already succeeded.
                system_log.warning("admit notice to %s failed: %s", target_id, exc)
                await self._send(update,
                    "⚠️ Approved, but I couldn't message them — "
                    "they may not have opened a chat with me yet.")
            return

        # ── Language switch callback ─────────────────────────
        if data.startswith("lang:"):
            new_lang = data.split(":", 1)[1]
            tg_id = self._get_tg_id(update)
            if new_lang in SUPPORTED_LANGS:
                set_user_lang(self.users, tg_id, new_lang)
                try:
                    await query.edit_message_text(
                        t("lang_switched", new_lang),
                        parse_mode="HTML")
                except Exception:
                    pass
            return

        # ── Daily Duel: a call on one of today's rounds ──
        if data.startswith("duel:"):
            await self._handle_duel_callback(update, data)
            return

        # ── Guardian intent-policy authoring confirm buttons ──
        if data.startswith("policy_"):
            await self._apply_policy_callback(update, data)
            return

        # ── Stance proposal declined ─────────────────────────
        if data == "stance_keep":
            from bot.config import RUNTIME as _rt
            await self._send(update,
                f"👍 Keeping <b>{_rt.strategy_mode.capitalize()}</b> — "
                "nothing changed.", edit=True)
            return

        # ── Fixed-term Earn LOCK buttons (operator money path, DOUBLE-confirm) ──
        # Step 1 (yldf:1:...) re-fetches the live catalog and shows the FINAL
        # confirm with the lock END date; step 2 (yldf:2:...) is the only
        # place a fixed-term subscription executes. Buttons carry
        # coin/productId/days, never an amount — execute_stake_fixed
        # recomputes, reserve-clamps, and re-validates the product live.
        if data.startswith("yldf:"):
            if not self._is_admin(update):
                await self._send(update,
                    "🔒 Earn actions move operator funds — admin only.",
                    edit=True)
                return
            parts = data.split(":")
            if len(parts) < 5 or parts[1] not in ("1", "2"):
                await self._send(update,
                    "Cancelled — nothing was moved.", edit=True)
                return
            step, f_coin, f_pid = parts[1], parts[2].upper(), parts[3]
            try:
                f_days = int(parts[4])
            except ValueError:
                await self._send(update,
                    "Bad lock term — nothing was moved.", edit=True)
                return
            from bot.core.yield_radar import (
                MIN_IDLE_USD, build_report, execute_stake_fixed,
                lock_end_date)
            client = self._yield_client()
            if client is None:
                await self._send(update,
                    "🔴 No operator Bitget keys — <code>/setexchange</code> "
                    "first.", edit=True)
                return
            if step == "1":
                report = await asyncio.to_thread(
                    build_report, client, self._engine_free_usdt())
                row = (None if report.error else
                       next((r for r in report.rows if r.coin == f_coin), None))
                term = next(
                    (t_ for t_ in ((row.fixed_terms if row else []) or [])
                     if str(t_.get("product_id")) == f_pid
                     and int(t_.get("days", 0)) == f_days), None)
                if (report.error or row is None or term is None
                        or row.stakeable_usd < MIN_IDLE_USD):
                    await self._send(update,
                        "🟡 That fixed-term option is no longer available "
                        "(or nothing stakeable after the reserve) — nothing "
                        "was moved. /stake fixed shows live terms.", edit=True)
                    return
                end = lock_end_date(f_days)
                kb = InlineKeyboardMarkup([
                    [InlineKeyboardButton(
                        f"🔒 YES — lock until {end}",
                        callback_data=f"yldf:2:{f_coin}:{f_pid}:{f_days}")],
                    [InlineKeyboardButton("❌ Cancel", callback_data="yld:x")],
                ])
                await self._send(update,
                    "⚠️ <b>FINAL CONFIRM — fixed-term lock</b>\n\n"
                    f"Lock ≈<code>${row.stakeable_usd:,.2f}</code> "
                    f"<b>{f_coin}</b> @ <code>{term['apy']:.2f}%</code> for "
                    f"<b>{f_days} days</b>.\n"
                    f"⛔ <b>NOT redeemable until {end} (UTC)</b> — the funds "
                    "cannot be withdrawn, traded, or used as margin before "
                    "that date.\n"
                    "<i>The exact amount is recomputed and reserve-clamped "
                    "when you press the button.</i>",
                    reply_markup=kb, edit=True)
                return
            # step == "2" — the ONLY place a fixed-term lock executes.
            await self._send(update, "⏳ Executing fixed-term lock…", edit=True)
            res = await asyncio.to_thread(
                execute_stake_fixed, client, f_coin, f_pid, f_days,
                self._engine_free_usdt())
            audit(system_log, f"Earn FIXED lock {f_coin} {f_days}d via double-confirm",
                  action="earn_action_fixed", result="OK" if res.ok else "FAIL",
                  data={"cb": data, "detail": res.message})
            icon = "✅" if res.ok else "🔴"
            await self._send(update,
                f"{icon} <b>Fixed-term lock {f_coin}</b>\n"
                f"{html.escape(res.message)}\n\n"
                "<i>/yield shows the radar. Fixed terms cannot be redeemed "
                "early.</i>", edit=True)
            return

        # ── Earn stake/redeem confirm buttons (operator money path) ──
        # The /stake and /unstake commands only PROPOSE; this is the sole
        # place funds actually move, and only for an admin. Buttons carry the
        # coin/productId, never an amount — execute_* recomputes and clamps
        # from live balances, so a stale button can never over-stake.
        if data.startswith("yld:"):
            if not self._is_admin(update):
                await self._send(update,
                    "🔒 Earn actions move operator funds — admin only.",
                    edit=True)
                return
            parts = data.split(":")
            action = parts[1] if len(parts) > 1 else ""
            if action == "x" or len(parts) < 3:
                await self._send(update,
                    "Cancelled — nothing was moved.", edit=True)
                return
            from bot.core.yield_radar import execute_stake, execute_unstake
            client = self._yield_client()
            if client is None:
                await self._send(update,
                    "🔴 No operator Bitget keys — <code>/setexchange</code> "
                    "first.", edit=True)
                return
            await self._send(update, "⏳ Executing Earn action…", edit=True)
            if action == "s":
                verb = f"Stake {parts[2]}"
                res = await asyncio.to_thread(
                    execute_stake, client, parts[2], self._engine_free_usdt())
            elif action == "r":
                verb = "Redeem"
                res = await asyncio.to_thread(execute_unstake, client, parts[2])
            else:
                await self._send(update, "Unknown Earn action.", edit=True)
                return
            audit(system_log, f"Earn {verb} via confirm button",
                  action="earn_action", result="OK" if res.ok else "FAIL",
                  data={"cb": data, "detail": res.message})
            icon = "✅" if res.ok else "🔴"
            await self._send(update,
                f"{icon} <b>{verb}</b>\n{html.escape(res.message)}\n\n"
                "<i>/yield shows the radar · /unstake redeems.</i>", edit=True)
            return

        # ── War Room menu callbacks ──────────────────────────

        if data == "open_warroom":
            rendered = wr_start()
            kb = _KB_WARROOM
            try:
                await query.edit_message_text(
                    rendered["text"], parse_mode="HTML", reply_markup=kb)
            except Exception:
                pass
            return

        if data == "latest_signal":
            # Delegate to the command handler
            await self._cmd_latest_signal(update, ctx)
            return

        if data == "performance":
            await self._cmd_performance(update, ctx)
            return

        if data == "risk_control":
            await self._cmd_risk(update, ctx)
            return

        if data == "strategy_mode":
            await self._cmd_strategy(update, ctx)
            return

        if data == "positions":
            await self._cmd_open_positions(update, ctx)
            return

        if data == "orders":
            await self._cmd_orders(update, ctx)
            return

        # ── Risk panel callbacks ─────────────────────────────

        if data == "risk_safe_mode":
            await self._send(update, safe_mode_notice(), edit=True)
            # NOT "activated", and not result="OK". The old line sealed a
            # claim that a risk control had been switched on into the
            # tamper-evident chain, every time somebody pressed a button that
            # did nothing.
            audit(system_log, "Safe mode button pressed — no state change "
                              "(not implemented)",
                  action="safe_mode", result="NOOP")
            return

        if data == "risk_pause":
            # Same authority as /pause. The permission map above gates this on
            # `halt`, which is a ROLE check — it says the caller may pause
            # something, not that they may pause EVERYBODY. The button and the
            # command must not disagree about that, or the gate is decorative.
            _risk, _scope = self._control_scope(update)
            if _risk is None:
                await self._refuse_shared_control(update, "pause")
                return
            _risk.emergency_halt("pause_risk_panel")
            rendered = wr_pause(scope=_scope)
            await self._send(update, rendered["text"], edit=True)
            audit(system_log, "Bot paused via risk panel", action="pause",
                  result="OK", data={"scope": _scope})
            return

        if data == "risk_emergency_stop":
            rendered = wr_emergency_stop()
            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("Yes, stop everything", callback_data="emergency_confirm"),
                 InlineKeyboardButton("Cancel", callback_data="emergency_cancel")],
            ])
            await self._send(update, rendered["text"], reply_markup=kb, edit=True if query.message else False)
            return

        if data == "emergency_confirm":
            # GLOBAL KILL-SWITCH: halt the shared engine AND every per-user risk
            # engine, clear queued ideas, and flatten EVERY account (operator +
            # per-user) — not just the operator.
            #
            # Operator only, mirroring /emergency_stop and the closeall_confirm
            # button beside it. The `halt` permission gate above is a role
            # check; this is the one that says whose accounts may be flattened.
            if not self._is_operator(update):
                await self._refuse_shared_control(update, "emergency_stop")
                return
            summary = await self.engine.emergency_halt_all("emergency_stop_telegram")

            close_summary = ""
            if summary.get("accounts"):
                parts = []
                for acct in summary["accounts"]:
                    parts.append(f"\n<b>{acct['account']}:</b>")
                    parts.extend(f"• {m[:100]}" for m in acct["messages"][:10])
                close_summary = "\n\n<b>Position closes:</b>" + "".join(parts)

            await self._send(update,
                f"⛔ <b>EMERGENCY STOP</b>\n\n"
                f"• Circuit breaker: ON ({summary.get('engines_halted', 0)} engine(s))\n"
                f"• Pending ideas: cleared ({summary.get('pending_cleared', 0)})\n"
                f"• {flatten_headline(summary.get('accounts', []))}"
                f"{close_summary}\n\n"
                f"Say \"resume\" when ready to restart.",
                edit=True)
            audit(system_log, "EMERGENCY STOP executed", action="emergency_stop", result="OK")
            return

        if data == "emergency_cancel":
            await self._send(update,
                "\u21a9\ufe0f Emergency stop cancelled. Bot continues.",
                edit=True)
            return

        if data == "closeall_confirm":
            # TG-2b: the actual /closeall flatten runs ONLY after this confirm.
            # Perm-gated above (halt) AND admin-gated when the command was issued.
            if not self._is_admin(update):
                await self._send(update, "\ud83d\udd12 Admin only.", edit=True)
                return
            await self._flatten_all_accounts(update)
            audit(system_log, "closeall confirmed + executed",
                  action="close_all", result="OK")
            return

        if data == "closeall_cancel":
            await self._send(update,
                "\u21a9\ufe0f Close-all cancelled. All positions untouched.",
                edit=True)
            return

        # ── Strategy mode callbacks ──────────────────────────

        if data.startswith("mode_"):
            # M-21 FIX: validate strategy mode against allowed values
            VALID_MODES = {"defensive", "balanced", "aggressive", "manual"}
            mode = data.removeprefix("mode_")
            if mode not in VALID_MODES:
                await self._send(update, "Invalid strategy mode.", edit=True)
                return
            from bot.config import RUNTIME
            RUNTIME.strategy_mode = mode
            rendered = wr_strategy_mode(mode)
            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("\U0001f6e1 Defensive", callback_data="mode_defensive"),
                 InlineKeyboardButton("\u2694\ufe0f Balanced", callback_data="mode_balanced")],
                [InlineKeyboardButton("\U0001f525 Aggressive", callback_data="mode_aggressive"),
                 InlineKeyboardButton("\U0001f9d8 Manual", callback_data="mode_manual")],
            ])
            try:
                await query.edit_message_text(
                    rendered["text"] + f"\n\n\u2705 Switched to <b>{mode.capitalize()}</b>",
                    parse_mode="HTML", reply_markup=kb)
            except Exception:
                pass
            audit(system_log, f"Strategy mode: {mode}", action="mode_switch", result="OK")
            # Public mind-stream: stance changes are part of the agent's
            # visible personality (mode name only, no account detail).
            try:
                from bot.core.agent_feed import FEED
                FEED.emit("stance", f"Stance changed to {mode.capitalize()}",
                          data={"mode": mode})
            except Exception:
                pass
            return

        # ── Signal action callbacks ──────────────────────────

        if data.startswith("signal_watch_"):
            pair = data.removeprefix("signal_watch_")
            await self._send(update,
                f"\U0001f441 <b>Watching {html.escape(pair)}</b>\n\n"
                "You will be notified on trigger.",
                edit=True)
            return

        # ── Position callbacks ───────────────────────────────

        if data.startswith("pos_details_"):
            ident = data.removeprefix("pos_details_")
            # ident can be a trade_id (TI-xxxx) or a pair name (EDGEUSDT)
            is_trade_id = ident.startswith("TI-")
            pair = ident  # fallback for display
            # Find the open position — check LIVE executor first
            user_id = self._get_tg_id(update)
            portfolio = self.engine.user_portfolios.get(user_id)
            pos_match = None
            is_live_pos = False

            _detail_ex = self._caller_executor(update)
            if CONFIG.is_live() and _detail_ex is not None:
                ident_clean = ident.replace("/", "").replace(":USDT", "")
                for lp in _detail_ex.open_positions:
                    if is_trade_id:
                        if lp.trade_id == ident:
                            pos_match = lp
                            is_live_pos = True
                            pair = lp.symbol.replace("/", "").replace(":USDT", "")
                            break
                    else:
                        lp_clean = lp.symbol.replace("/", "").replace(":USDT", "")
                        if lp_clean == ident_clean:
                            pos_match = lp
                            is_live_pos = True
                            pair = lp_clean
                            break

            if pos_match is None:
                for p in portfolio.open_positions:
                    if p.asset.replace("/", "").replace(":USDT", "") == pair:
                        pos_match = p
                        break

            # The button passes a trade_id (TI-xxxx). When local tracking has
            # gone stale — booked closed while the exchange still holds the
            # position (the "local tracking out of sync" case that made this
            # button say "position closed" while /livepositions showed it OPEN)
            # — resolve the SYMBOL from any local record (open OR closed) so the
            # exchange fallback below can match it by symbol instead of failing.
            _resolved_sym = None
            if is_trade_id and _detail_ex is not None:
                _rec = getattr(_detail_ex, "_positions", {}).get(ident)
                if _rec is None:
                    try:
                        _rec = next((c for c in _detail_ex.closed_positions
                                     if getattr(c, "trade_id", None) == ident), None)
                    except Exception:
                        _rec = None
                if _rec is not None:
                    _resolved_sym = getattr(_rec, "symbol", None)

            # Fallback: check exchange directly for untracked positions
            is_untracked = False
            if pos_match is None and CONFIG.is_live():
                try:
                    exchange_fallback = await self.engine.live_executor._get_exchange()
                    ex_positions = await exchange_fallback.fetch_positions()
                    ident_clean = ident.replace("/", "").replace(":USDT", "")
                    for ep in (ex_positions or []):
                        if not isinstance(ep, dict):
                            continue
                        contracts = float(ep.get("contracts") or 0)
                        if contracts <= 0:
                            continue
                        ep_sym = ep.get("symbol", "")
                        ep_clean = ep_sym.replace("/", "").replace(":USDT", "")
                        _rs_clean = (_resolved_sym or "").replace("/", "").replace(":USDT", "")
                        if (ep_clean == ident_clean or ep_sym == ident
                                or (_rs_clean and ep_clean == _rs_clean)):
                            # Build a lightweight mock object for rendering
                            from types import SimpleNamespace
                            from datetime import datetime, timezone
                            ts = ep.get("timestamp")
                            opened = datetime.fromtimestamp(ts / 1000, tz=timezone.utc) if ts else datetime.now(timezone.utc)
                            pos_match = SimpleNamespace(
                                entry_price=float(ep.get("entryPrice") or ep.get("info", {}).get("openPriceAvg") or 0),
                                quantity=contracts,
                                direction=(ep.get("side") or "long").upper(),
                                stop_loss=0,
                                take_profit=0,
                                opened_at=opened,
                                cost_usd=float(ep.get("initialMargin") or ep.get("collateral") or 0),
                                leverage=float(ep.get("leverage") or 1),
                                sl_order_id=None,
                                tp_order_id=None,
                                trade_id=ep_sym,
                                symbol=ep_sym,
                            )
                            is_live_pos = True
                            is_untracked = True
                            pair = ep_clean
                            break
                except Exception:
                    pass

            # Fetch live analysis data
            try:
                exchange = await self.engine.get_exchange()
            except Exception:
                exchange = None

            symbol = pair.replace("USDT", "/USDT") if "USDT" in pair else pair
            adata = None
            if exchange:
                adata = await fetch_analysis_data(exchange, symbol, timeframe="1h")

            if adata and pos_match:
                last_px = adata["price"]

                # Extract fields uniformly from live or paper position
                if is_live_pos:
                    _entry = pos_match.entry_price
                    _qty = pos_match.quantity
                    _dir = pos_match.direction  # already a string
                    _sl = pos_match.stop_loss
                    _tp = pos_match.take_profit
                    _opened = pos_match.opened_at
                    _cost = pos_match.cost_usd if pos_match.cost_usd > 0 else _entry * _qty
                    _sl_oid = pos_match.sl_order_id
                    _tp_oid = pos_match.tp_order_id
                else:
                    portfolio.mark_to_market({pos_match.asset: last_px})
                    _entry = pos_match.entry_price
                    _qty = pos_match.quantity
                    _dir = pos_match.direction.value if hasattr(pos_match.direction, 'value') else str(pos_match.direction)
                    _sl = pos_match.stop_loss
                    _tp = pos_match.take_profit
                    _opened = pos_match.opened_at
                    _cost = _entry * _qty
                    _sl_oid = None
                    _tp_oid = None

                pnl_pct = ((last_px - _entry) / _entry * 100)
                if _dir == "SHORT":
                    pnl_pct = -pnl_pct
                sz = _cost
                exit_notional = _qty * last_px
                pnl_usd = 0.0  # real leveraged value set below once leverage is known
                d_emoji = "\U0001f7e2" if _dir == "LONG" else "\U0001f534"
                pnl_emoji = "\U0001f7e2" if pnl_pct >= 0 else "\U0001f534"
                sl_dist = abs(last_px - _sl) / last_px * 100 if last_px else 0
                tp_dist = abs(_tp - last_px) / last_px * 100 if last_px else 0

                # R:R from current price
                risk_left = abs(last_px - _sl) if _sl else 0
                reward_left = abs(_tp - last_px) if _tp else 0
                rr_live = reward_left / risk_left if risk_left > 0 else 0

                # Leverage — prefer stored value from position, fall back to notional/cost
                notional_now = _qty * last_px
                if is_live_pos and getattr(pos_match, 'leverage', 0) and pos_match.leverage > 1:
                    leverage = float(pos_match.leverage)
                else:
                    _stored_lev = getattr(pos_match, 'leverage', 0) if not is_live_pos else 0
                    leverage = float(_stored_lev) if _stored_lev and _stored_lev > 1 else (notional_now / sz if sz > 0 else 1.0)

                # Real leveraged dollar P&L (was _qty×price-delta, which understated
                # it by the leverage multiple for a margin-based quantity).
                pnl_usd = _leveraged_pnl_usd(_entry, last_px, _dir, sz, leverage)
                # ...and put the PERCENT on the same basis as that dollar.
                #
                # It was computed ~50 lines above as a raw price move, because
                # that is the only place it could be: leverage is not resolved
                # until just now. So this card rendered "-0.13% ($-0.64)" — an
                # unleveraged percent beside a leveraged dollar — while
                # /open_positions rendered "-2.56% ($-0.64)" for the same
                # position a minute earlier. Read in sequence, a 2.4-point
                # recovery that never happened.
                #
                # Rescaled rather than recomputed so the raw move above keeps
                # driving sl_dist/tp_dist/R:R, which are genuinely price-based
                # and must NOT be multiplied by leverage.
                pnl_pct = _leveraged_return_pct(_entry, last_px, _dir, leverage)
                pnl_emoji = "\U0001f7e2" if pnl_pct >= 0 else "\U0001f534"

                # Fee calculations
                comm_pct = CONFIG.risk.commission_pct
                entry_fee = sz * (comm_pct / 100.0)
                exit_fee_est = exit_notional * (comm_pct / 100.0)
                total_fees = entry_fee + exit_fee_est

                # Funding rate estimate
                from datetime import datetime, timezone
                hold_hours = (datetime.now(timezone.utc) - _opened).total_seconds() / 3600
                funding_sessions = hold_hours / 8.0
                funding_rate = 0.01
                funding_paid = sz * (funding_rate / 100.0) * funding_sessions

                # Net PNL after all fees
                net_pnl = pnl_usd - total_fees - funding_paid

                # Hold time display
                if hold_hours < 1:
                    hold_str = f"{hold_hours * 60:.0f}m"
                elif hold_hours < 24:
                    hold_str = f"{hold_hours:.1f}h"
                else:
                    hold_str = f"{hold_hours / 24:.1f}d"

                # SL/TP order status
                if _sl_oid:
                    sl_tag = "on exchange"
                else:
                    sl_tag = "bot-managed"
                if _tp_oid:
                    tp_tag = "on exchange"
                else:
                    tp_tag = "bot-managed"

                mode_tag = " LIVE" if is_live_pos else ""
                lev_str = f" | {leverage:.0f}x" if leverage > 1 else ""

                lines = [
                    f"<b>{html.escape(pair)}</b>{mode_tag}",
                    f"{d_emoji} {_dir} | {pnl_emoji} {pnl_pct:+.2f}% (${pnl_usd:+,.2f})",
                    "",
                    f"Entry <code>{_entry:,.6f}</code> / Now <code>{last_px:,.6f}</code>",
                    f"Size <code>${sz:,.2f}</code>{lev_str} | Hold {hold_str} | R:R {rr_live:.1f}x",
                    f"SL <code>{_sl:,.6f}</code> ({sl_dist:.1f}%) {sl_tag}",
                    f"TP <code>{_tp:,.6f}</code> ({tp_dist:.1f}%) {tp_tag}",
                    f"Net PnL <code>${net_pnl:+,.2f}</code> (fees ${total_fees + funding_paid:.2f})",
                ]

                # Add market context on one line if available
                if adata:
                    # Through the seam: an absent or short-history RSI is
                    # "unread", not 0 (oversold) or 50 (neutral).
                    lines.append(market_context_line(adata))

                # Use trade_id for buttons if we have a live position
                btn_id = pos_match.trade_id if is_live_pos else pair
                kb = InlineKeyboardMarkup([[
                    InlineKeyboardButton("Close", callback_data=f"pos_close_{btn_id}:{user_id}"),
                    InlineKeyboardButton("Refresh", callback_data=f"pos_details_{btn_id}"),
                ]])

                # ── Render styled position card PNG ──
                pos_card_png = None
                try:
                    from bot.formatters.signal_card import render_position_card
                    pos_card_data = {
                        "symbol": symbol,
                        "direction": _dir,
                        "is_live": is_live_pos,
                        "entry": _entry,
                        "now": last_px,
                        "pnl_pct": pnl_pct,
                        "pnl_usd": pnl_usd,
                        "net_pnl": net_pnl,
                        "fees": total_fees + funding_paid,
                        "size_usd": sz,
                        "leverage": leverage,
                        "hold_time": hold_str,
                        "rr": rr_live,
                        "sl": _sl,
                        "tp": _tp,
                        "sl_pct": sl_dist,
                        "tp_pct": tp_dist,
                        "sl_status": sl_tag,
                        "tp_status": tp_tag,
                    }
                    if adata:
                        # None passes through: the card image omits the RSI
                        # cell rather than drawing a number nobody measured.
                        pos_card_data["rsi"] = adata.get("rsi")
                        pos_card_data["rsi_label"] = rsi_label(adata.get("rsi"))
                        pos_card_data["structure"] = adata.get("structure", "")
                    pos_card_png = render_position_card(pos_card_data)
                except Exception as exc:
                    system_log.debug("Position card render failed: %s", exc)

                # Try to build a position chart
                chart_png = None
                try:
                    from bot.skills.chart_renderer import build_position_chart
                    # C4: pass direction + entry ATR so the chart can draw the
                    # Playbook ratchet threshold (the "Trig" line) alongside the
                    # static entry/SL/TP. liq/trail are left to the renderer's
                    # defaults (drawn only when a caller supplies them).
                    _pos_atr = (getattr(pos_match, "atr_at_entry", 0.0) or 0.0) \
                        if is_live_pos else 0.0
                    chart_png = await build_position_chart(
                        None, symbol, entry=_entry, sl=_sl, tp=_tp,
                        direction=_dir, atr=_pos_atr)
                except Exception as exc:
                    system_log.warning("build_position_chart failed for %s: %s", symbol, exc)

                if pos_card_png:
                    # Send the styled position card as a photo with buttons
                    mode_tag = "LIVE" if is_live_pos else "PAPER"
                    cap = (f"<b>{html.escape(pair)}</b> {mode_tag}\n"
                           f"{d_emoji} {_dir} | {pnl_emoji} {pnl_pct:+.2f}% (${pnl_usd:+,.2f})")
                    await self._send_photo(update, pos_card_png, cap, reply_markup=kb)
                    # Also send chart below if available
                    if chart_png:
                        chart_cap = (f"<b>{html.escape(pair)}</b> · 1h\n"
                                     f"Entry <code>{_entry:,.6f}</code> | "
                                     f"Now <code>{last_px:,.6f}</code>")
                        await self._send_photo(update, chart_png, chart_cap)
                elif chart_png:
                    card_text = "\n".join(lines)
                    await self._send(update, card_text, edit=True)
                    cap = (f"<b>{html.escape(pair)}</b> · 1h\n"
                           f"Entry <code>{_entry:,.6f}</code> | Now <code>{last_px:,.6f}</code>\n"
                           f"{pnl_emoji} {pnl_pct:+.2f}% (${pnl_usd:+,.2f})")
                    await self._send_photo(update, chart_png, cap, reply_markup=kb)
                else:
                    await self._send(update, "\n".join(lines), edit=True, reply_markup=kb)
            elif pos_match:
                # No market data — show position info only
                if is_live_pos:
                    _entry = pos_match.entry_price
                    _qty = pos_match.quantity
                    _dir = pos_match.direction
                    _sl = pos_match.stop_loss
                    _tp = pos_match.take_profit
                    _opened = pos_match.opened_at
                    _cost = pos_match.cost_usd if pos_match.cost_usd > 0 else _entry * _qty
                else:
                    _entry = pos_match.entry_price
                    _qty = pos_match.quantity
                    _dir = pos_match.direction.value if hasattr(pos_match.direction, 'value') else str(pos_match.direction)
                    _sl = pos_match.stop_loss
                    _tp = pos_match.take_profit
                    _opened = pos_match.opened_at
                    _cost = _entry * _qty

                d_emoji = "\U0001f7e2" if _dir == "LONG" else "\U0001f534"
                sz = _cost
                comm_pct = CONFIG.risk.commission_pct
                entry_fee = sz * (comm_pct / 100.0)
                exit_fee_est = sz * (comm_pct / 100.0)
                from datetime import datetime, timezone
                hold_hours = (datetime.now(timezone.utc) - _opened).total_seconds() / 3600
                funding_sessions = hold_hours / 8.0
                funding_paid = sz * (0.01 / 100.0) * funding_sessions

                mode_tag = " \U0001f534 LIVE" if is_live_pos else ""
                lines = [
                    f"\U0001f4cb <b>{html.escape(pair)} \u2014 Position Detail</b>{mode_tag}",
                    "",
                    f"- Direction: {d_emoji} {_dir}",
                    f"- Entry: <code>{_entry:,.6f}</code>",
                    f"- SL: <code>{_sl:,.6f}</code>",
                    f"- TP: <code>{_tp:,.6f}</code>",
                    f"- Qty: <code>{_qty:,.4f}</code> | Size: <code>${sz:,.2f}</code>",
                    f"- Hold: <code>{hold_hours:.1f}h</code>",
                    "",
                    "<b>Fees & Costs:</b>",
                    f"- Entry fee ({comm_pct}%): <code>${entry_fee:.4f}</code>",
                    f"- Exit fee ({comm_pct}%, est): <code>${exit_fee_est:.4f}</code>",
                    f"- Funding ({hold_hours:.1f}h hold): <code>${funding_paid:.4f}</code>",
                    f"- Total costs: <code>${entry_fee + exit_fee_est + funding_paid:.4f}</code>",
                    "",
                    "<i>Market data unavailable \u2014 say \"trade\" for full analysis</i>",
                ]
                await self._send(update, "\n".join(lines), edit=True)
            else:
                await self._send(update,
                    f"\u2705 <b>{html.escape(pair)}</b> — position closed.\n\n"
                    "Say \"positions\" to see current state.",
                    edit=True)
                # Remove stale buttons
                try:
                    if update.callback_query and update.callback_query.message:
                        await update.callback_query.message.edit_reply_markup(reply_markup=None)
                except Exception:
                    pass
            return

        if data.startswith("pos_close_"):
            ident, owner_uid = self._split_pos_close_owner(
                data.removeprefix("pos_close_"))
            is_trade_id = ident.startswith("TI-")
            pair = ident  # fallback for display
            user_id = self._get_tg_id(update)
            # IDOR guard (RC-AUD-004 style): if the button carries an owner tag,
            # only that user may close it. The per-user executor routing below is
            # the primary isolation; this is defense-in-depth against a crafted /
            # replayed callback.
            if owner_uid is not None and not self._uid_matches(user_id, owner_uid):
                await self._send(update,
                    "\U0001f512 <b>Access denied</b>\n\n"
                    "Only the user who owns this position can close it.",
                    edit=True)
                audit(system_log,
                      f"pos_close IDOR blocked: caller={user_id} owner={owner_uid}",
                      action="callback_idor_block", result="DENIED")
                return
            portfolio = self.engine.user_portfolios.get(user_id)

            closed_trade = None
            live_closed = False

            # LIVE mode: close via the CALLER's executor so a user can only ever
            # close their OWN account's positions (resolves to the shared operator
            # executor when PER_USER_LIVE_ENABLED is off — byte-identical default).
            executor = self._caller_executor(update)
            if CONFIG.is_live() and executor is not None:
                for lp in list(executor.open_positions):
                    if is_trade_id:
                        matched = lp.trade_id == ident
                    else:
                        lp_clean = lp.symbol.replace("/", "").replace(":USDT", "")
                        ident_clean = ident.replace("/", "").replace(":USDT", "")
                        matched = lp_clean == ident_clean
                    if matched:
                        pair = lp.symbol.replace("/", "").replace(":USDT", "")
                        try:
                            result = await executor.close_position(lp.trade_id, "manual_nlp")
                            live_closed = True
                            # Live incident 2026-07-07: this block used to render
                            # a SUCCESS card unconditionally \u2014 a FAILED VET close
                            # (position reverted to open) still rendered a card
                            # from _last_close_data, which held ANOTHER symbol's
                            # close ("VETUSDT CLOSED" caption over a BTC card).
                            # 1) honor close_position's failure result;
                            if isinstance(result, str) and "CLOSE FAILED" in result:
                                await self._send(
                                    update,
                                    f"\u274c Close failed for <b>{html.escape(pair)}</b> "
                                    f"\u2014 the position is still open.\n"
                                    f"<code>{html.escape(result[:300])}</code>",
                                    edit=True)
                                break
                            # 2) only trust _last_close_data if it is THIS
                            #    position's close (another close finishing in the
                            #    same window can overwrite the shared slot).
                            close_data = getattr(executor, '_last_close_data', None)
                            if close_data:
                                _cd_sym = str(close_data.get("symbol", "")).replace(
                                    "/", "").replace(":USDT", "")
                                if _cd_sym != pair:
                                    close_data = None  # fall to per-position text
                            close_png = None
                            if close_data:
                                try:
                                    from bot.formatters.signal_card import render_close_card
                                    close_png = render_close_card(close_data)
                                except Exception:
                                    pass

                            if close_png:
                                from bot.formatters.signal_card import humanize_close_reason
                                # Tri-state — see the /close caption above.
                                pnl_val = close_data.get("pnl_usd")
                                pnl_emoji, reason_short = humanize_close_reason(
                                    close_data.get("reason", "manual"), pnl_val)
                                _pnl_txt = ("unread" if pnl_val is None
                                            else f"${pnl_val:+,.2f}")
                                cap = (f"{pnl_emoji} <b>{html.escape(pair)}</b> CLOSED\n"
                                       f"PnL: {_pnl_txt} | {html.escape(reason_short)}")
                                await self._send_photo(update, close_png, cap)
                            else:
                                # Fallback to text
                                from datetime import datetime, timezone
                                hold_h = (datetime.now(timezone.utc) - lp.opened_at).total_seconds() / 3600
                                cost = lp.cost_usd if lp.cost_usd > 0 else lp.entry_price * lp.quantity
                                close_px = lp.close_price or lp.entry_price
                                pnl_val = lp.pnl_usd or 0
                                pnl_emoji = "\U0001f7e2" if pnl_val >= 0 else "\U0001f534"
                                lines = [
                                    f"<b>{html.escape(pair)} closed</b>",
                                    "",
                                    f"Entry <code>{lp.entry_price:,.6f}</code> / Exit <code>{close_px:,.6f}</code>",
                                    f"Size <code>${cost:,.2f}</code> | Hold {hold_h:.1f}h",
                                    f"{pnl_emoji} PnL: <code>${pnl_val:+,.2f}</code>",
                                ]
                                await self._send(update, "\n".join(lines), edit=True)
                            # Remove buttons from the original details message
                            try:
                                if update.callback_query and update.callback_query.message:
                                    await update.callback_query.message.edit_reply_markup(reply_markup=None)
                            except Exception:
                                pass
                        except Exception as e:
                            live_closed = True  # prevent fallthrough to "not found"
                            await self._send(update,
                                f"Couldn't close {html.escape(pair)}.\n\n"
                                f"{html.escape(str(e)[:200])}\n"
                                "You can try again or close it on the exchange directly.",
                                edit=True)
                        break

            if live_closed:
                return  # Already sent response (success or error) — do not fall through

            # LIVE mode fallback: close untracked exchange positions directly.
            # Use the caller's executor/exchange so this can't reach into the
            # operator's (or another user's) account.
            if CONFIG.is_live() and not live_closed and executor is not None:
                try:
                    exchange = await executor._get_exchange()
                    ex_positions = await exchange.fetch_positions()
                    for ep in (ex_positions or []):
                        if not isinstance(ep, dict):
                            continue
                        contracts = float(ep.get("contracts") or 0)
                        if contracts <= 0:
                            continue
                        ep_sym = ep.get("symbol", "")
                        ep_clean = ep_sym.replace("/", "").replace(":USDT", "")
                        ident_clean = ident.replace("/", "").replace(":USDT", "")
                        if ep_clean == ident_clean or ep_sym == ident:
                            # Found it on exchange — close directly
                            side = (ep.get("side") or "long").upper()
                            close_side = "sell" if side == "LONG" else "buy"
                            entry_price = float(ep.get("entryPrice") or 0)
                            margin = float(ep.get("initialMargin") or ep.get("collateral") or 0)
                            leverage = int(float(ep.get("leverage") or 1))
                            close_params = {"productType": "USDT-FUTURES"}
                            hedge = getattr(executor, '_hedge_mode', False)
                            if hedge:
                                close_params["tradeSide"] = "close"
                            try:
                                order = await exchange.create_order(
                                    symbol=ep_sym, type="market",
                                    side=close_side, amount=contracts,
                                    params=close_params,
                                )
                                # The venue's fill, then the last price, then
                                # NOTHING. `fill_price = entry_price` booked a
                                # round trip whose PnL is exactly the fees --
                                # a measured-looking number for a close nobody
                                # read. The reconciler books the real fill from
                                # Bitget history on its next sweep.
                                fill_price = venue_fill_price(order)
                                fill_source = "fill" if fill_price is not None else None
                                if fill_price is None:
                                    try:
                                        ticker = await exchange.fetch_ticker(ep_sym)
                                        fill_price = paper_close_price(ticker)
                                        fill_source = "last" if fill_price is not None else None
                                    except Exception:
                                        fill_price = None

                                if fill_price is None:
                                    gross_pnl = commission = net_pnl = None
                                else:
                                    if side == "LONG":
                                        gross_pnl = (fill_price - entry_price) * contracts
                                    else:
                                        gross_pnl = (entry_price - fill_price) * contracts
                                    comm_pct = CONFIG.risk.commission_pct
                                    commission = ((entry_price * contracts + fill_price * contracts)
                                                  * (comm_pct / 100.0))
                                    net_pnl = gross_pnl - commission

                                # Record trade in closed_trades.json via executor
                                # First, check if this position was already closed by reconciliation
                                # to avoid double-counting with a different trade_id.
                                from datetime import datetime, timezone
                                from bot.core.live_executor import LivePosition
                                already_recorded = False
                                for ct in executor._closed_trades:
                                    ct_clean = ct.symbol.replace("/", "").replace(":USDT", "")
                                    if ct_clean == ep_clean and ct.direction == side:
                                        # Check if closed within the last 5 minutes
                                        ct_closed = ct.closed_at
                                        if ct_closed and (datetime.now(timezone.utc) - ct_closed).total_seconds() < 300:
                                            already_recorded = True
                                            break
                                if not already_recorded:
                                    ts = ep.get("timestamp")
                                    opened_at = datetime.fromtimestamp(ts / 1000, tz=timezone.utc) if ts else datetime.now(timezone.utc)
                                    closed_pos = LivePosition(
                                        trade_id=f"TI-manual-{ep_clean}-{int(datetime.now(timezone.utc).timestamp())}",
                                        symbol=ep_sym,
                                        direction=side,
                                        entry_price=entry_price,
                                        quantity=contracts,
                                        cost_usd=margin,
                                        stop_loss=0,
                                        take_profit=0,
                                        leverage=leverage,
                                        status="closed",
                                        close_price=fill_price,
                                        gross_pnl=(None if gross_pnl is None
                                                   else round(gross_pnl, 4)),
                                        commission=(None if commission is None
                                                    else round(commission, 4)),
                                        pnl_usd=(None if net_pnl is None
                                                 else round(net_pnl, 4)),
                                        opened_at=opened_at,
                                        closed_at=datetime.now(timezone.utc),
                                    )
                                    executor._append_closed_trade(closed_pos)

                                # Colour is a claim: green says "in profit" as
                                # loudly as the number does, and an unread fill
                                # is neither.
                                if net_pnl is None:
                                    pnl_emoji = "\u26aa"
                                    _exit_s = "<code>unread</code>"
                                    _pnl_s = ("Net PnL: unread — the venue returned no fill "
                                              "price; the reconciler records the real fill "
                                              "and PnL")
                                else:
                                    pnl_emoji = "\U0001f7e2" if net_pnl >= 0 else "\U0001f534"
                                    _approx = (" (≈ last price — fill not returned)"
                                               if fill_source == "last" else "")
                                    _exit_s = f"<code>{fill_price:,.6f}</code>{_approx}"
                                    _pnl_s = (f"Net PnL: <code>${net_pnl:+,.2f}</code> "
                                              f"(fees ${commission:.2f})")
                                lines = [
                                    f"\u2705 <b>{html.escape(ep_clean)} — Position Closed</b>",
                                    "",
                                    f"Entry <code>{entry_price:,.6f}</code> / Exit {_exit_s}",
                                    f"Size <code>${margin:,.2f}</code> | {leverage}x",
                                    f"{pnl_emoji} {_pnl_s}",
                                ]
                                await self._send(update, "\n".join(lines), edit=True)
                                # Remove buttons
                                try:
                                    if update.callback_query and update.callback_query.message:
                                        await update.callback_query.message.edit_reply_markup(reply_markup=None)
                                except Exception:
                                    pass
                            except Exception as e:
                                await self._send(update,
                                    f"Couldn't close {html.escape(ep_clean)} on exchange.\n\n"
                                    f"{html.escape(str(e)[:200])}\n"
                                    "Try closing it on the exchange directly.",
                                    edit=True)
                            live_closed = True
                            break
                except Exception as exc:
                    logger.warning("Exchange direct close fallback failed: %s", exc)

            if live_closed:
                return

            if not live_closed:
                # Paper mode close
                for pos in list(portfolio.open_positions):
                    if pos.asset.replace("/", "").replace(":USDT", "") == pair:
                        close_price = None
                        try:
                            exchange = await self.engine.get_exchange()
                            ticker = await exchange.fetch_ticker(pos.asset)
                            close_price = paper_close_price(ticker)
                        except Exception as e:
                            system_log.warning("Close position error for %s: %s", pair, e)
                        if close_price is None:
                            # Closing at entry_price books a PnL of exactly zero
                            # and RETIRES the position -- the trade is gone from
                            # the book at a price nothing quoted. Leave it open
                            # and say why.
                            await self._send(update,
                                f"\u26aa Could not read a price for <b>{html.escape(pair)}</b> — "
                                "position <b>NOT</b> closed. Try again in a moment.",
                                edit=True)
                            return
                        closed_trade = portfolio.close_position(pos.trade_id, close_price)
                        break

            if closed_trade:
                pnl_emoji = "\U0001f7e2" if closed_trade.pnl >= 0 else "\U0001f534"
                sz = closed_trade.quantity * closed_trade.entry_price
                from datetime import datetime, timezone
                hold_h = 0
                if closed_trade.opened_at and closed_trade.closed_at:
                    hold_h = (closed_trade.closed_at - closed_trade.opened_at).total_seconds() / 3600
                funding_paid = sz * (0.01 / 100.0) * (hold_h / 8.0) if hold_h > 0 else 0

                lines = [
                    f"\u2705 <b>{html.escape(pair)} — Position Closed</b>",
                    "",
                    f"- Entry: <code>{closed_trade.entry_price:,.4f}</code>",
                    f"- Exit: <code>{closed_trade.exit_price:,.4f}</code>",
                    f"- Size: <code>${sz:,.2f}</code>",
                    "",
                    "<b>PNL Breakdown:</b>",
                    f"- Gross PNL: <code>${closed_trade.gross_pnl:+,.2f}</code>",
                    f"- Commission: <code>${closed_trade.commission:.2f}</code>",
                    f"- Funding ({hold_h:.1f}h): <code>${funding_paid:.2f}</code>",
                    f"- <b>Net PNL: {pnl_emoji} <code>${closed_trade.pnl:+,.2f}</code></b>",
                    "",
                    "Say \"my portfolio\" for updated balance.",
                ]
                await self._send(update, "\n".join(lines), edit=True)
            else:
                await self._send(update,
                    f"\u2705 <b>{html.escape(pair)}</b> — already closed.\n\n"
                    "Say \"positions\" to see current state.",
                    edit=True)
                # Remove stale buttons
                try:
                    if update.callback_query and update.callback_query.message:
                        await update.callback_query.message.edit_reply_markup(reply_markup=None)
                except Exception:
                    pass
            return

        # ── Legacy pane callbacks (backward compat) ──────────

        if data.startswith("pane:"):
            pane = data.split(":", 1)[1]
            if pane == "refresh":
                pane = self._last_pane.get(self._get_tg_id(update), "status")
            self._last_pane[self._get_tg_id(update)] = pane
            body = await self._render_pane(pane, user_id=self._get_tg_id(update))
            text = body + self._footer()
            try:
                await query.edit_message_text(
                    text, parse_mode="HTML", reply_markup=_KB_DASH)
            except Exception:
                import re
                plain = re.sub(r"<[^>]+>", "", text)
                try:
                    await query.edit_message_text(
                        plain, parse_mode=None, reply_markup=_KB_DASH)
                except Exception:
                    pass
            return

        if data.startswith("nav:"):
            cmd = data.split(":", 1)[1]
            pane_map = {
                "scan": "scan", "status": "status",
                "risk": "risk", "portfolio": "portfolio",
                "backtest": "scan",
            }
            pane = pane_map.get(cmd, "status")
            self._last_pane[self._get_tg_id(update)] = pane
            body = await self._render_pane(pane, user_id=self._get_tg_id(update))
            text = body + self._footer()
            try:
                await query.edit_message_text(
                    text, parse_mode="HTML", reply_markup=_KB_DASH)
            except Exception:
                try:
                    await query.message.reply_text(
                        text, parse_mode="HTML", reply_markup=_KB_DASH)
                except Exception:
                    pass
            return

        # ── Scan skill callbacks (scan_confirm: / scan_reject: / scan_limit:) ──
        if data.startswith("scan_confirm:") or data.startswith("scan_reject:") or data.startswith("scan_limit:"):
            await _scan_callback(update, ctx)
            return

        # ── Trade confirm/reject ─────────────────────────────

        # ── Set custom limit price ──
        if data.startswith("setlimit:"):
            parts = data.split(":")
            trade_id = parts[1]
            expected_uid = parts[2] if len(parts) > 2 else None
            caller_uid = str(update.effective_user.id) if update.effective_user else None
            if not self._callback_owner_ok(caller_uid, expected_uid):
                await self._send(update,
                    "\U0001f512 <b>Access denied</b>", edit=True)
                return

            # Look up the idea to show current entry
            idea = self.engine._pending_ideas.get(trade_id)
            if not idea:
                await self._send(update,
                    t('trade_expired_rescan', self._lang(update)), edit=True)
                return

            pair = display_symbol(idea.asset)
            direction = idea.direction.value if hasattr(idea.direction, 'value') else str(idea.direction)

            # Store that this user is waiting to type a limit price
            if not hasattr(self, '_pending_limit_input'):
                self._pending_limit_input: dict = {}
            self._pending_limit_input[caller_uid] = {
                "trade_id": trade_id,
                "asset": idea.asset,
                "pair": pair,
                "direction": direction,
                "current_entry": idea.entry_price,
                "timestamp": time.time(),
            }

            await self._send(update,
                f"\U0001f4b0 <b>Set limit price for {pair} {direction}</b>\n\n"
                f"Current entry: <code>${idea.entry_price:,.4f}</code>\n"
                f"SL: <code>${idea.stop_loss:,.4f}</code> | TP: <code>${idea.take_profit:,.4f}</code>\n\n"
                f"Type your limit price (e.g. <code>84.07</code> or <code>0.0522</code>):",
                edit=True)
            return

        if data.startswith("confirm:"):
            parts = data.split(":")
            trade_id = parts[1]

            # OWNERSHIP FIRST, THEN THE DOUBLE-TAP GUARD. The order was the
            # other way round, and the guard CONSUMES the id: a stranger's
            # `confirm:<id>` was added to _confirmed_ids and only then denied,
            # so the trade's real owner tapping Confirm afterwards hit the
            # guard and was told "Already confirmed" — for a trade that never
            # executed. Anyone who could guess or observe a trade_id could burn
            # it, and the message told the owner it had gone through.
            #
            # A denial must not spend the thing it is denying. `reject:` had
            # the identical ordering and shares this set, so a stranger's
            # reject also burned the confirm.
            #
            # M3 FIX: validate callback belongs to requesting user.
            # RC-AUD-004: fail-closed. Every legitimate confirm button is built as
            # "confirm:<id>:<uid>" (see button construction sites), so a missing
            # owner tag means a crafted/replayed callback — deny rather than allow.
            expected_uid = parts[2] if len(parts) > 2 else None
            caller_uid = str(update.effective_user.id) if update.effective_user else None
            if not self._callback_owner_ok(caller_uid, expected_uid):
                await self._send(update,
                    "\U0001f512 <b>Access denied</b>\n\n"
                    "Only the user who requested this trade can approve it.",
                    edit=True)
                audit(system_log,
                      f"Callback IDOR blocked: caller={caller_uid} expected={expected_uid}",
                      action="callback_idor_block", result="DENIED")
                return

            # Double-tap guard: skip if this trade was already confirmed
            if not hasattr(self, '_confirmed_ids'):
                self._confirmed_ids: set[str] = set()
            if trade_id in self._confirmed_ids:
                try:
                    await query.answer("Already confirmed")
                except Exception:
                    pass
                return
            self._confirmed_ids.add(trade_id)
            # Cap the set to prevent unbounded growth
            if len(self._confirmed_ids) > 100:
                self._confirmed_ids = set(list(self._confirmed_ids)[-50:])

            # H-18 FIX: LIVE mode — check per-user live trading permission
            if CONFIG.is_live() and not self._is_admin(update):
                caller_uid_str = str(update.effective_user.id) if update.effective_user else ""
                if not self._can_trade_live(caller_uid_str):
                    await self._send(update,
                        f"\U0001f512 {t(self._live_refusal_key(), self._lang(update))}",
                        edit=True)
                    audit(system_log,
                          f"Non-admin trade confirm blocked: caller={caller_uid_str}",
                          action="admin_gate", result="DENIED")
                    return

            try:
                result = await self.engine.confirm_trade(trade_id, user_id=caller_uid or "")
            except Exception as exc:
                audit(system_log, f"confirm_trade raised: {exc}",
                      action="confirm_trade", result="ERROR")
                await self._send(update,
                    f"\u274c <b>Trade execution failed:</b> {_safe_exc_text(exc)}",
                    edit=True)
                return

            # ── Auto re-analyze on price drift ──
            # If price moved since analysis, rebuild the idea at current price and retry once
            if "price drifted" in result.lower() and "re-analyze" in result.lower():
                original_idea = self.engine._last_confirmed_idea
                if original_idea:
                    try:
                        await self._send(update,
                            f"\u26a0\ufe0f <b>Price moved — auto re-analyzing {original_idea.asset}...</b>")
                        exchange = await self.engine.scanner._get_exchange()
                        ticker = await exchange.fetch_ticker(original_idea.asset)
                        new_price = float(ticker.get("last", 0))
                        new_idea = reanalyzed_idea(original_idea, new_price)
                        if new_idea is not None:
                            ohlcv = await exchange.fetch_ohlcv(original_idea.asset, "4h", limit=30)
                            self.engine._pending_ideas[new_idea.id] = new_idea
                            self.engine._pending_atr[new_idea.id] = atr_from_ohlcv(ohlcv)
                            # OFFERED, not executed. The rebuilt idea has a
                            # different entry, flat placeholder levels and no
                            # analysis behind it -- executing it spends money on
                            # a thesis the user never saw, on the strength of a
                            # button they pressed for a different one.
                            _uid = update.effective_user.id if update.effective_user else ""
                            _lang = self._lang(update)
                            kb = InlineKeyboardMarkup([[
                                InlineKeyboardButton(t("btn_take_it", _lang),
                                    callback_data=f"confirm:{new_idea.id}:{_uid}"),
                                InlineKeyboardButton(t("lbl_limit", _lang),
                                    callback_data=f"setlimit:{new_idea.id}:{_uid}"),
                                InlineKeyboardButton(t("btn_skip", _lang),
                                    callback_data=f"reject:{new_idea.id}:{_uid}"),
                            ]])
                            await self._send(update,
                                render_reanalyzed_offer(original_idea, new_idea),
                                reply_markup=kb)
                            audit(system_log,
                                  f"Drift re-analysis offered for {new_idea.asset} "
                                  f"@ {new_price} (NOT executed)",
                                  action="auto_reanalyze", result="OFFERED")
                            return
                    except Exception as retry_exc:
                        audit(system_log, f"Auto re-analyze failed: {retry_exc}",
                              action="auto_reanalyze", result="ERROR")

            # Detect failure. Route through the canonical classifier (the same
            # one engine.confirm_trade and scan_skill's confirm callback use)
            # rather than a third local prefix list — a previous drifted copy
            # in scan_skill.py missed "EXECUTION BLOCKED:" (degraded-mode /
            # reduce-only), which announced a blocked trade as "EXECUTED". This
            # local list has the same gap (also missing "EXECUTION ABORTED",
            # "REFUSED:", "Live execution blocked") and would reproduce that
            # bug the first time this path hits one of those outcomes.
            from bot.core.live_executor import execution_indicates_failure
            _local_fail_markers = (
                "Trade not found", "not found", "expired", "No pending",
                "Trade REJECTED", "Trade HALTED", "Execution denied",
            )
            # Case-insensitive prefix check: catches both "Trade REJECTED" and
            # "Trade rejected" (post-critique, manual reject, etc.)
            result_lower = result.lower()
            is_failure = (execution_indicates_failure(result)
                          or any(result_lower.startswith(p.lower()) for p in _local_fail_markers))
            if not is_failure:
                msg = f"\u2705 {t('trade_executed_ok', self._lang(update))}\n\n{result}"
                # Forward trade open to marketing channels
                idea = self.engine._pending_ideas.get(trade_id) or self.engine._last_confirmed_idea
                if idea:
                    can_live = self._can_trade_live(caller_uid or "")
                    _mode = "LIVE" if can_live and not CONFIG.simulation_mode else "PAPER"
                    try:
                        await self.forwarder.post_trade_opened(idea, mode=_mode)
                    except Exception:
                        pass
            else:
                msg = f"\u274c {t('trade_executed_fail', self._lang(update))}\n\n{result}"
            # Try edit first (works for text messages), fall back to new message
            # (needed when buttons are on a photo message from chart flow)
            try:
                await self._send(update, msg, edit=True)
            except Exception:
                await self._send(update, msg)
            # Remove buttons from the original message (best-effort)
            try:
                if update.callback_query and update.callback_query.message:
                    await update.callback_query.message.edit_reply_markup(reply_markup=None)
            except Exception:
                pass
        elif data.startswith("reject:"):
            parts = data.split(":")
            trade_id = parts[1]

            # OWNERSHIP FIRST — see the note in the confirm branch. This set is
            # shared with `confirm:`, so denying a stranger here after
            # consuming the id would burn the owner's confirm as well.
            #
            # M3 FIX: validate callback belongs to requesting user.
            # RC-AUD-004: fail-closed — a missing owner tag means a crafted
            # callback (legitimate buttons are always "reject:<id>:<uid>").
            expected_uid = parts[2] if len(parts) > 2 else None
            caller_uid = str(update.effective_user.id) if update.effective_user else None
            if not self._callback_owner_ok(caller_uid, expected_uid):
                await self._send(update,
                    "\U0001f512 <b>Access denied</b>\n\n"
                    "Only the user who requested this trade can reject it.",
                    edit=True)
                audit(system_log,
                      f"Callback IDOR blocked: caller={caller_uid} expected={expected_uid}",
                      action="callback_idor_block", result="DENIED")
                return

            # Double-tap guard
            if not hasattr(self, '_confirmed_ids'):
                self._confirmed_ids: set[str] = set()
            if trade_id in self._confirmed_ids:
                try:
                    await query.answer("Already processed")
                except Exception:
                    pass
                return
            self._confirmed_ids.add(trade_id)
            try:
                result = self.engine.reject_trade(trade_id)
            except Exception as exc:
                audit(system_log, f"reject_trade raised: {exc}",
                      action="reject_trade", result="ERROR")
                await self._send(update,
                    f"\u274c <b>Trade execution failed:</b> {_safe_exc_text(exc)}",
                    edit=True)
                return
            msg = f"\u274c Got it, trade skipped.\n\n{result}"
            try:
                await self._send(update, msg, edit=True)
            except Exception:
                await self._send(update, msg)
            try:
                if update.callback_query and update.callback_query.message:
                    await update.callback_query.message.edit_reply_markup(reply_markup=None)
            except Exception:
                pass

        audit(system_log, f"Callback: {data}", action="telegram_callback")
