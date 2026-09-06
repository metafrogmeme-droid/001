"""The proactive-alert loop — a slice out of the handler.

`start_monitor` wires the engine's ProactiveMonitor to Telegram: the alert
sender (with the signal-card sender it installs on the host as
`_signal_card_fn`), the hooks that forward new signals and closes to the
marketing channels, the adoption and close cards, and the watchdog that
restarts the loop if it dies. `stop_monitor` cancels it; `_notify_admins`
is the operator broadcast every alert path ends in. Their behaviour is
covered where it always was (`test_proactive_alert_staleness`,
`test_monitor_one_broken_check_does_not_silence_the_rest`,
`test_graceful_stop_is_armed`, `test_health_reports_monitor_checks_down`,
`test_position_watch_reaches_the_operator`);
`tests/test_handler_mixins.py` holds this class to the split's rules.

A mixin, not a leaf: the loop reads `self.engine`, `self.monitor`,
`self.forwarder` and `self.users`, and answers through the bot the
application hands it. The task handle it owns (`_monitor_task`) is declared
on the mixin, not on the host.
"""
from __future__ import annotations

import asyncio
import html
import os
import re
from typing import TYPE_CHECKING, Awaitable, Callable, Optional

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from bot.config import CONFIG
from bot.marketing.public_text import public_close_line
from bot.utils.i18n import get_user_lang, t
from bot.utils.logger import _redact_string, audit, system_log

if TYPE_CHECKING:
    from bot.core.engine import RuneClawEngine
    from bot.core.proactive_monitor import ProactiveMonitor
    from bot.marketing.channel_forwarder import ChannelForwarder
    from bot.utils.user_store import UserStore


class AlertsMonitor:
    """The proactive-alert loop and the operator broadcast. Host contract below; methods after."""

    #: The running monitor task, created by `start_monitor` and cancelled by
    #: `stop_monitor` — the mixin's own state, declared so the type checker
    #: knows it exists.
    _monitor_task: Optional[asyncio.Task]
    #: The signal-card sender `start_monitor` installs on the instance; the
    #: handler defaults it to None as a class attribute, and the trading
    #: group's contract names it under the same type.
    _signal_card_fn: Optional[Callable[..., Awaitable[None]]]

    if TYPE_CHECKING:
        # Provided by TelegramHandler, and ONLY declared here — declarations,
        # never bodies; tests/test_handler_mixins.py checks every name against
        # what the handler really defines.
        engine: RuneClawEngine
        monitor: ProactiveMonitor
        forwarder: ChannelForwarder
        users: UserStore

        def _is_admin_id(self, tg_id: str) -> bool: ...

        async def _bot_username(self, bot=None) -> Optional[str]: ...

        async def _fetch_chart_timeframes(self, asset: str, primary_data: dict | None) -> dict: ...

    async def start_monitor(self, bot) -> None:
        """Start the proactive monitor background task.
        Called from main.py after the Telegram app is initialized."""
        # Restore the persisted /watch list and auto-enroll the operator if it's
        # empty, so CRITICAL safety alerts survive restarts (previously the watch
        # list was in-memory only and every restart muted them until /watch on).
        try:
            self.monitor.hydrate()
        except Exception as exc:
            system_log.debug("proactive monitor hydrate skipped: %s", exc)
        # One boot-time tier push so the website's plan column converges with
        # the bot's tier authority even if it missed earlier /set_tier runs.
        try:
            from bot.utils.website_sync import sync_tiers_in_background
            sync_tiers_in_background(self.users.all_tiers())
        except Exception:
            pass
        # Wire up channel forwarder
        self.forwarder.set_bot(bot)
        async def _send_fn(chat_id: str, text: str, buttons=None) -> None:
            # `buttons` = optional (label, callback_data) pairs from proposal
            # alerts; the callbacks route to already-guarded handlers.
            try:
                markup = None
                if buttons:
                    markup = InlineKeyboardMarkup(
                        [[InlineKeyboardButton(lbl, callback_data=cb)]
                         for lbl, cb in buttons])
                await bot.send_message(
                    chat_id=int(chat_id), text=text, parse_mode="HTML",
                    reply_markup=markup)
            except Exception:
                pass

        # Opt-in: push a setup chart (with entry/SL/TP lines) alongside each
        # proactive NEW SIGNAL alert. Renders off-thread; degrades silently.
        async def _chart_fn(chat_id: str, idea) -> None:
            try:
                system_log.info("proactive _chart_fn called for %s", idea.asset if idea else "None")
                if not CONFIG.telegram.send_charts:
                    system_log.info("proactive chart: disabled in config")
                    return
                from bot.skills import chart_renderer
                if not chart_renderer.charts_available():
                    system_log.info("proactive chart: libs not available")
                    return
                candles_by_tf = await self._fetch_chart_timeframes(idea.asset, None)
                system_log.info("proactive chart candles: %s", {k: len(v) for k, v in candles_by_tf.items()} if candles_by_tf else "empty")
                if not candles_by_tf:
                    return
                await chart_renderer.send_idea_charts_multi(
                    bot, int(chat_id), candles_by_tf, idea,
                    theme=CONFIG.telegram.chart_theme)
                system_log.info("proactive chart sent for %s", idea.asset)
            except Exception as exc:  # noqa: BLE001 — best-effort
                system_log.warning("proactive chart_fn skipped: %s", exc, exc_info=True)

        self.monitor.set_chart_fn(_chart_fn)

        # Who may see an audience="admin" alert. The monitor imports no
        # telegram, so it cannot ask this itself; `_is_admin_id` stays the ONE
        # definition of admin rather than the monitor carrying a second copy.
        self.monitor.set_admin_fn(self._is_admin_id)

        # Signal card image renderer — sends a styled PNG card for each signal
        _bot_ref = bot
        async def _signal_card_fn(chat_id: str, idea, rank: int = 1,
                                  scan_data: dict = None) -> None:
            try:
                from bot.formatters.signal_card import signal_card_from_idea
                png = signal_card_from_idea(idea, rank=rank, scan_data=scan_data or {})
                if png:
                    import io as _io
                    buf = _io.BytesIO(png)
                    buf.name = "signal.png"
                    uid = CONFIG.telegram.chat_id or chat_id
                    # Build confirm/reject buttons on the card image. This is an
                    # engine→user push path (no `update`); resolve lang from chat_id.
                    _sc_lang = get_user_lang(self.users, chat_id)
                    kb = InlineKeyboardMarkup([[
                        InlineKeyboardButton(t("btn_take_it", _sc_lang),
                            callback_data=f"confirm:{idea.id}:{uid}"),
                        InlineKeyboardButton(t("lbl_limit", _sc_lang),
                            callback_data=f"setlimit:{idea.id}:{uid}"),
                        InlineKeyboardButton(t("btn_skip", _sc_lang),
                            callback_data=f"reject:{idea.id}:{uid}"),
                    ]])
                    pair = idea.asset.replace("/USDT", "")
                    direction = idea.direction.value if hasattr(idea.direction, "value") else str(idea.direction)
                    st = getattr(idea, 'strategy_type', '').upper()
                    st_str = f" [{st}]" if st else ""
                    cap = f"<b>{pair} {direction}</b>{st_str} | Conf {idea.confidence*100:.0f}%"
                    await _bot_ref.send_photo(
                        chat_id=int(chat_id), photo=buf,
                        caption=cap, parse_mode="HTML",
                        reply_markup=kb)
            except Exception as exc:
                system_log.debug("Signal card send failed: %s", exc)

        self._signal_card_fn = _signal_card_fn

        # Hook: forward new signals to marketing channels + send signal card
        _forwarder = self.forwarder
        _original_dispatch = self.monitor._dispatch

        async def _dispatch_with_forward(alert, send_fn):
            await _original_dispatch(alert, send_fn)
            # Send signal card image for trade signals
            if alert.alert_type == "TRADE_SIGNAL" and alert.idea is not None:
                for cid in list(self.monitor._enabled_chats):
                    try:
                        await _signal_card_fn(cid, alert.idea, rank=1)
                    except Exception:
                        pass
                # Forward to marketing channels
                try:
                    await _forwarder.post_signal(alert.idea)
                except Exception:
                    pass

        self.monitor._dispatch = _dispatch_with_forward
        self._monitor_task = asyncio.create_task(self.monitor.run(_send_fn))

        # Task-death tripwire: a dead monitor task means ALL internal alerting
        # is down while trading continues. Audit CRITICAL immediately (normal
        # shutdown cancellation is not a death).
        def _monitor_task_died(task) -> None:
            if task.cancelled():
                return
            exc = task.exception()
            audit(system_log,
                  f"Proactive monitor task DIED: {exc!r} — internal alerting "
                  f"is DOWN until restarted",
                  action="monitor_task", result="DIED",
                  data={"error": repr(exc)})

        self._monitor_task.add_done_callback(_monitor_task_died)

        # Reciprocal liveness: hand the engine a reference so its tick loop
        # can watch the monitor's heartbeat, plus a monitor-INDEPENDENT
        # callback that tells the admin and restarts the task when it died.
        # The SAME monitor object must be reused on restart — it carries the
        # _dispatch forward hook above and all dedup/watch state.
        self.engine._proactive_monitor = self.monitor

        async def _on_monitor_stale(age_s: float) -> None:
            restarted = False
            task = self._monitor_task
            if task is not None and task.done():
                self._monitor_task = asyncio.create_task(self.monitor.run(_send_fn))
                self._monitor_task.add_done_callback(_monitor_task_died)
                restarted = True
            msg = (f"🚨 <b>MONITOR STALLED</b> — the proactive alert loop last "
                   f"ran {age_s:.0f}s ago. Internal safety alerting was DOWN."
                   + ("\n♻️ The monitor task had died and was RESTARTED."
                      if restarted else
                      "\n⚠️ The task is still running but not progressing — "
                      "a hung send may be blocking it. Consider a restart."))
            for cid in _notify_chat_ids:
                try:
                    await _send_fn(str(cid), msg)
                except Exception as exc:
                    system_log.debug("Monitor-stale notify failed: %s", exc)

        self.engine._monitor_stale_callback = _on_monitor_stale

        # Register trade-close notification callback
        admin_chat_id = CONFIG.telegram.chat_id
        # Parse comma-separated admin chat IDs into list of ints
        _notify_chat_ids: list[int] = []
        if admin_chat_id:
            for cid in admin_chat_id.split(","):
                cid = cid.strip()
                if cid.isdigit():
                    _notify_chat_ids.append(int(cid))
        async def _on_trade_closed(msg: str) -> None:
            """Send a rich close confirmation to admin when a trade is closed."""
            if not _notify_chat_ids:
                return
            try:
                # Try to render a styled PNG close card
                close_data = getattr(self.engine.live_executor, '_last_close_data', None)
                # Consistency guard (live incident 2026-07-07): _last_close_data
                # is a shared last-write-wins slot. With 2+ closes in one sweep,
                # THIS message's close may not be the slot's occupant — rendering
                # it would caption/card the WRONG position. Only trust the slot
                # when its symbol actually appears in this message.
                if close_data:
                    _cd_sym = str(close_data.get("symbol", "")).replace(
                        "/", "").replace(":USDT", "").upper()
                    _msg_norm = msg.replace("/", "").replace(":USDT", "").upper()
                    if _cd_sym and _cd_sym not in _msg_norm:
                        close_data = None  # mismatched close — fall to text from msg
                # FAILURE messages must never be replaced by a card: the slot is
                # only written on close SUCCESS, so on a failed/urgent close it
                # holds an EARLIER close of possibly the same symbol — the guard
                # above passes and a stale "normal close" card would swallow the
                # only warning that a position is live and unprotected.
                if any(k in msg for k in ("CLOSE FAILED", "URGENT", "ENTRY ABORTED")):
                    close_data = None      # always deliver the failure text itself
                close_png = None
                if close_data:
                    try:
                        from bot.formatters.signal_card import render_close_card
                        close_png = render_close_card(close_data)
                    except Exception as exc:
                        system_log.debug("Close card render failed: %s", exc)

                if close_png:
                    # Send as photo with brief caption
                    from bot.formatters.signal_card import humanize_close_reason
                    sym = close_data.get("symbol", "").replace("/", "").replace(":USDT", "")
                    direction = close_data.get("direction", "")
                    # `.get(k, 0)` defaults a MISSING key only; a close booked
                    # UNPRICED carries a present None, which formats as a
                    # TypeError and, before that, would have read as $0.00 —
                    # a break-even nobody measured, in the caption under a
                    # card that says "unread".
                    pnl_usd = close_data.get("pnl_usd")
                    reason = close_data.get("reason", "closed")
                    pnl_emoji, reason_short = humanize_close_reason(reason, pnl_usd)
                    _pnl_txt = "unread" if pnl_usd is None else f"${pnl_usd:+,.2f}"
                    cap = (f"{pnl_emoji} <b>{html.escape(sym)}</b> {direction} CLOSED\n"
                           f"PnL: {_pnl_txt} | {html.escape(reason_short)}")
                    # The share button re-renders in PERCENT; it never forwards
                    # this caption, which carries dollars and is private by
                    # design. close_share_button returns None when the close
                    # cannot be told honestly (no readable percent), and then
                    # there is simply no button — see formatters/share_invite.
                    _share_kb = None
                    try:
                        from bot.formatters.share_invite import close_share_button
                        _btn = close_share_button(
                            close_data, await self._bot_username(bot))
                        if _btn:
                            _share_kb = InlineKeyboardMarkup(
                                [[InlineKeyboardButton(_btn["text"], url=_btn["url"])]])
                    except Exception as _sx:
                        system_log.debug("share button skipped: %s", _sx)
                    for _cid in _notify_chat_ids:
                        try:
                            await bot.send_photo(
                                chat_id=_cid,
                                photo=close_png,
                                caption=cap,
                                parse_mode="HTML",
                                reply_markup=_share_kb)
                        except Exception:
                            pass
                else:
                    # Fallback to text — use reason-specific heading. close_data
                    # can be None here, so there's no pnl_usd to key the sign
                    # off; fall back to a text heuristic on msg in that case.
                    from bot.formatters.signal_card import humanize_close_reason
                    reason = close_data.get("reason", "") if close_data else ""
                    # No `, 0` default: an unpriced close must reach
                    # humanize_close_reason as None so it answers ⚪ rather
                    # than the ✅ that `0 >= 0` buys.
                    pnl_for_sign = (close_data.get("pnl_usd") if close_data
                                    else (1.0 if "+$" in msg else -1.0))
                    emoji, heading = humanize_close_reason(reason, pnl_for_sign)
                    sym = close_data.get("symbol", "") if close_data else ""
                    direction = close_data.get("direction", "") if close_data else ""
                    if sym and direction:
                        card = f"{emoji} <b>{html.escape(sym)}</b> {direction} {heading}\n\n"
                    else:
                        card = f"{emoji} <b>{heading}</b>\n\n"
                    for line in msg.strip().split("\n"):
                        card += f"{html.escape(line)}\n"
                    for _cid in _notify_chat_ids:
                        try:
                            await bot.send_message(
                                chat_id=_cid, text=card.strip(),
                                parse_mode="HTML")
                        except Exception:
                            pass
            except Exception as exc:
                system_log.debug("Close notify send failed: %s", exc)

            # Forward trade close to marketing channels — those groups are
            # PUBLIC, and `msg` is the private close text: "PnL: +$12.3456
            # (+1.23%)". Compose the public line from close_data instead, in
            # percent. close_data is None here when the shared last-write-wins
            # slot did not match this close (see the guard above) or on a
            # failure message; the forwarder's own scrubber covers that path.
            try:
                await _forwarder.post_trade_closed(
                    public_close_line(close_data) or msg)
            except Exception:
                pass

        self.engine.set_close_notify_callback(_on_trade_closed)

        # Register limit-fill notification callback
        async def _on_limit_filled(msg: str) -> None:
            """Send a notification when a limit order is filled (position opened)."""
            if not _notify_chat_ids:
                return
            try:
                from datetime import datetime as _dt, timezone as _tz
                card = "\U0001f4e5 <b>TRADE OPENED</b>\n"
                card += "\u2500" * 28 + "\n\n"
                for line in msg.strip().split("\n"):
                    card += f"{html.escape(line)}\n"
                card += "\n" + "\u2500" * 28
                card += f"\n\U0001f43e RUNECLAW | {_dt.now(_tz.utc).strftime('%H:%M')} UTC"
                card += "\n<a href='#'>#RUNECLAW #LimitFill</a>"
                for _cid in _notify_chat_ids:
                    try:
                        await bot.send_message(
                            chat_id=_cid, text=card.strip(),
                            parse_mode="HTML")
                    except Exception:
                        pass
            except Exception as exc:
                system_log.debug("Fill notify send failed: %s", exc)

        self.engine.set_fill_notify_callback(_on_limit_filled)

        # Register periodic-sync adoption notification callback. These are
        # informational — the position/order is now TRACKED, nothing closed —
        # and were previously misrouted to the close path and rendered as
        # "❌ Closed — SYNC: Adopted untracked position B from exchange".
        async def _on_exchange_sync(msg: str) -> None:
            if not _notify_chat_ids:
                return
            try:
                card = "\U0001f504 <b>EXCHANGE SYNC</b>\n"
                card += "─" * 28 + "\n\n"
                for line in msg.strip().split("\n"):
                    card += f"{html.escape(line)}\n"
                card += ("\nThe bot found this on the exchange and is now "
                         "tracking it (SL/TP monitoring active).")
                for _cid in _notify_chat_ids:
                    try:
                        await bot.send_message(
                            chat_id=_cid, text=card.strip(),
                            parse_mode="HTML")
                    except Exception:
                        pass
            except Exception as exc:
                system_log.debug("Sync notify send failed: %s", exc)

        self.engine.set_sync_notify_callback(_on_exchange_sync)

        # ── Adoption notification ─────────────────────────────────
        async def _on_positions_adopted(adopted_symbols: list[str]) -> None:
            """Notify admin when exchange positions are adopted on startup.

            The card body is a PURE renderer (rich_cards.render_adoption_card)
            because while it lived inline here it was unreachable by tests —
            which is precisely how #999's per-position SL/TP outcome shipped
            and never rendered once. A surface with no seam has no assertion.
            """
            try:
                from bot.formatters.rich_cards import render_adoption_card
                _ex = getattr(self.engine, "live_executor", None)
                _positions = list(getattr(_ex, "_positions", {}).values()) if _ex else []
                lines = render_adoption_card(adopted_symbols, _positions).split("\n")
                admin_chat_id = os.environ.get("ADMIN_CHAT_ID") or os.environ.get("TELEGRAM_CHAT_ID", "")
                if admin_chat_id:
                    for _cid_str in admin_chat_id.split(","):
                        _cid_str = _cid_str.strip()
                        if _cid_str.isdigit():
                            try:
                                await bot.send_message(
                                    chat_id=int(_cid_str),
                                    text="\n".join(lines),
                                    parse_mode="HTML")
                            except Exception:
                                pass
            except Exception as exc:
                system_log.debug("Adopt notify send failed: %s", exc)

        self.engine.set_adopt_notify_callback(_on_positions_adopted)

        # ── Auto-confirm notification ──────────────────────────────
        async def _on_auto_confirmed(idea, result_msg: str) -> None:
            """Notify admin when a trade is auto-confirmed (high confidence)."""
            try:
                pair = idea.asset.replace("/USDT", "")
                direction = idea.direction.value if hasattr(idea.direction, "value") else str(idea.direction)
                conf = idea.confidence * 100
                from datetime import datetime as _dt, timezone as _tz
                card_lines = [
                    "\U0001f916 <b>AUTO-CONFIRMED TRADE</b>",
                    "\u2500" * 28,
                    "",
                    f"\U0001f4b0 <b>{pair}</b> {direction} | Conf <b>{conf:.0f}%</b>",
                    f"Entry: <code>${idea.entry_price:,.4f}</code>",
                    f"SL: <code>${idea.stop_loss:,.4f}</code> | TP: <code>${idea.take_profit:,.4f}</code>",
                    "",
                ]
                # Add result preview. The executor's line already carries HTML
                # (<b>\u2026</b>); html.escape() would turn those into literal "<b>"
                # text in the card. Strip the tags first, then escape the plain
                # text so it renders cleanly under parse_mode=HTML.
                first_line = result_msg.strip().split("\n")[0] if result_msg else ""
                if first_line:
                    _plain = re.sub(r"<[^>]+>", "", first_line)
                    card_lines.append(f"\u2192 {html.escape(_plain)}")
                card_lines.extend([
                    "",
                    "\u2500" * 28,
                    f"\U0001f43e RUNECLAW | {_dt.now(_tz.utc).strftime('%H:%M')} UTC",
                    "<i>Confidence exceeded auto-confirm threshold</i>",
                ])
                a_chat = os.environ.get("ADMIN_CHAT_ID") or os.environ.get("TELEGRAM_CHAT_ID", "")
                if a_chat:
                    for _cid_str in a_chat.split(","):
                        _cid_str = _cid_str.strip()
                        if _cid_str.isdigit():
                            try:
                                await bot.send_message(
                                    chat_id=int(_cid_str),
                                    text="\n".join(card_lines),
                                    parse_mode="HTML")
                            except Exception:
                                pass
            except Exception as exc:
                system_log.debug("Auto-confirm notify send failed: %s", exc)

        self.engine.set_auto_confirm_notify_callback(_on_auto_confirmed)

    async def stop_monitor(self) -> None:
        """Stop the proactive monitor."""
        self.monitor.stop()
        # Never started, or started and already cancelled: nothing to await.
        task = getattr(self, "_monitor_task", None)
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    async def _notify_admins(self, text: str, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """Send a notification to all admin users."""
        # Audit F-15 (defense-in-depth): this is a second legitimate direct-send
        # chokepoint alongside _send() (it targets every admin's chat_id, not
        # the current update's), so it needs its own redaction rather than
        # inheriting _send()'s.
        if text:
            try:
                text = _redact_string(text)
            except Exception:
                pass
        for u in self.users.list_users():
            if u.get("role") == "admin" and u.get("authorized"):
                try:
                    await ctx.bot.send_message(
                        chat_id=int(u["telegram_id"]),
                        text=text, parse_mode="HTML")
                except Exception:
                    pass
