"""The trading command group — a slice out of the handler.

The commands that open, close, list and stop: `/trade`, `/buy`, `/sell`,
`/paper`, `/latest_signal`, `/orders`, `/open_positions` (and its
`/positions` alias), `/livepositions`, `/venues`, `/mystrategy`, `/halt`,
`/pause`, `/resume`, `/reset` and `/emergency_stop`, with the manual-trade
parser, the live-position card renderer, the pending-order reconciliation
helpers and the resume-gate read. This is the money path: every mutating
command sits behind `guard(...)`, the stop/start ones resolve
`self._control_scope` so a shared operator engine is never halted by a
non-operator, and the position views route through `self._caller_executor`
so nobody lists a book that is not theirs. Their behaviour is covered where
it always was (`test_telegram_commands`, `test_openorders_reconcile`,
`test_pending_order_desync`, `test_pending_orders_are_not_positions`,
`test_orphan_row_survives_an_unread_venue`,
`test_unreadable_orders_are_not_a_missing_stop`,
`test_resume_card_does_not_claim_enabled_while_blocked`,
`test_shared_engine_controls_are_operator_only`, `test_user_strategy`,
`test_venue_card`, `test_manual_trade_parser`);
`tests/test_handler_mixins.py` holds this class to the split's rules.

The leveraged return helpers the position cards use went to
`bot/utils/leveraged_return.py`, a leaf, because the position-detail
callback on the handler reads them too and a mixin must not import from
the handler.

A mixin, not a leaf: every method reads `self.engine` and answers through
`self._send` (or `_reply`, its alias, and `_send_photo`). `_signal_card_fn`
is the card sender `start_monitor` installs on the host once the bot is
running; it is `None` until then, and `/latest_signal` checks before it
calls.
"""
from __future__ import annotations

import asyncio
import html
import logging
import time
from datetime import datetime
from typing import TYPE_CHECKING, Awaitable, Callable, Optional

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from bot.compat import UTC
from bot.config import CONFIG
from bot.formatters.rich_cards import display_symbol, position_watch_line, render_open_positions
from bot.formatters.thesis_text import thesis_prose
from bot.skills.command_guard import guard
from bot.skills.scan_hints import _background_scan_is_fresh, _scan_timeout_hint, _skipped_symbols_note
from bot.utils.exc_text import _safe_exc_text
from bot.utils.i18n import t
from bot.utils.leveraged_return import _leveraged_pnl_usd
from bot.utils.logger import audit, system_log
from bot.warroom.warroom_bot import render_emergency_stop as wr_emergency_stop
from bot.warroom.warroom_bot import render_pause as wr_pause
from bot.warroom.warroom_bot import render_resume as wr_resume

if TYPE_CHECKING:
    from bot.core.engine import RuneClawEngine
    from bot.skills.skill_registry import SkillRegistry
    from bot.utils.user_store import UserStore

logger = logging.getLogger(__name__)


class TradingCommands:
    """Open, close, list and stop. Host contract below; methods after."""

    if TYPE_CHECKING:
        # Provided by TelegramHandler, and ONLY declared here — declarations,
        # never bodies; tests/test_handler_mixins.py checks every name against
        # what the handler really defines.
        engine: RuneClawEngine
        registry: SkillRegistry
        users: UserStore
        _signal_card_fn: Optional[Callable[..., Awaitable[None]]]

        async def _send(self, update: Update, text: str,
                        reply_markup=None, edit: bool = False) -> None: ...

        async def _reply(self, update: Update, text: str, reply_markup=None) -> None: ...

        async def _send_photo(self, update: Update, png: bytes, caption: str,
                              reply_markup=None) -> bool: ...

        async def _guard(self, update: Update, command: str = "", ctx=None) -> bool: ...

        async def _refuse_shared_control(self, update: Update, command: str) -> None: ...

        def _get_tg_id(self, update: Update) -> str: ...

        def _lang(self, update: Update) -> str: ...

        def _is_operator(self, update: Update) -> bool: ...

        def _control_scope(self, update: Update): ...

        def _caller_executor(self, update: Update): ...

    @guard("mystrategy")
    async def _cmd_mystrategy(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """/mystrategy — choose which strategy preset YOUR confirms run through.

        ``/mystrategy`` shows the catalogue and your current selection;
        ``/mystrategy <name>`` pins a preset (aliases work: dip, momentum,
        scalp); ``/mystrategy off`` clears it. The selection is a TIGHTEN-ONLY
        veto on trades you confirm: it can refuse an idea that breaks your
        strategy's rules, it never creates trades and never touches the
        operator's global stance. Confirm-time enforces the symbols list and
        the confidence floor; RSI/regime/volume gates apply in the scan
        (where those facts exist) — stated, never silently claimed.
        """
        from bot.core import user_strategy_store as _store
        from bot.skills.skill_registry import RunStrategySkill as _RS
        _tg_id = self._get_tg_id(update)
        args = [a.lower() for a in (ctx.args or [])]
        if args[:1] in (["off"], ["clear"], ["reset"]):
            if _store.clear(_tg_id):
                await self._reply(update,
                    "\U0001f513 Strategy cleared — your confirms are ungated again.")
            else:
                await self._reply(update, "No strategy was set — nothing to clear.")
            return
        if args:
            _raw = " ".join(args)
            _key = _RS.ALIASES.get(_raw, _raw)
            if _key not in _RS.PRESETS:
                _names = " · ".join(sorted(_RS.PRESETS))
                await self._reply(update,
                    f"Unknown strategy \u2014 pick one of: {_names} (or /mystrategy off).")
                return
            _stored = _store.set_pref(_tg_id, _key, _RS.PRESETS.keys())
            if _stored is None:
                await self._reply(update,
                    "Could not save the selection \u2014 nothing changed. Try again.")
                return
            _cfg = _RS.PRESETS[_key]
            _enf = []
            if isinstance(_cfg.get("symbols"), (list, tuple)) and _cfg.get("symbols"):
                _enf.append("symbols")
            if _cfg.get("confidence_threshold") is not None:
                _enf.append(f"confidence \u2265 {_cfg['confidence_threshold'] * 100:.0f}%")
            _scan = [g for g in ("rsi_threshold", "regime", "volume_spike_min")
                     if _cfg.get(g) is not None]
            _lines = [
                f"{_cfg.get('icon', '')} <b>{html.escape(_cfg.get('label', _key))}</b> "
                "is now YOUR strategy.",
                "Trades you confirm that break its rules will be refused "
                "(tighten-only \u2014 it never places trades).",
                ("Enforced at confirm: " + ", ".join(_enf)) if _enf
                else "This preset carries no confirm-time gate \u2014 it filters in the scan only.",
            ]
            if _scan:
                _lines.append("Applied in the scan (not at confirm): " + ", ".join(_scan) + ".")
            _lines.append("/mystrategy off clears it any time \u2014 revocable is the point.")
            await self._reply(update, "\n".join(_lines))
            return
        _cur = _store.get(_tg_id)
        _lines = ["\u2694\ufe0f <b>Your bot, your strategy</b>"]
        for _k in sorted(_RS.PRESETS):
            _c = _RS.PRESETS[_k]
            _mark = " \u2b05 <b>yours</b>" if _k == _cur else ""
            _lines.append(f"{_c.get('icon', '')} <code>/mystrategy {html.escape(_k)}</code> "
                          f"\u2014 {html.escape(_c.get('desc', ''))}{_mark}")
        _lines.append("" if _cur else "\nNone selected \u2014 your confirms run ungated.")
        _lines.append("A selection is a tighten-only veto on YOUR confirms; "
                      "the operator loop keeps its own stance. /mystrategy off clears.")
        await self._reply(update, "\n".join(x for x in _lines if x))

    @guard("trade")
    async def _cmd_venues(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """/venues — choose which of your connected venues actually trade.

        ``/venues`` shows the current state; ``/venues bitget bybit`` sets the
        selection; ``/venues none`` returns to a single venue.

        DELIBERATELY THIN. Everything a reader could be misled by lives in
        ``bot/formatters/venue_card.py`` where a test can plant state and read
        the card back — the seam #999 is about. This method's only job is to
        gather facts and pass a refusal through verbatim.
        """
        import asyncio as _aio

        from bot.core.exchange_credentials import get_credential_store
        from bot.core.venue_selection import (ENFORCE_IMPLEMENTED,
                                              get_venue_selection_store,
                                              routing_decision)
        from bot.formatters.venue_card import venue_card

        tg_id = self._get_tg_id(update)
        store = get_venue_selection_store()
        creds = get_credential_store()

        def _connected(uid):
            return creds.list_venues(uid)

        def _open_on(uid, venue):
            """Open positions on ONE venue, or raise.

            RAISING is the point. `set_selection` treats an exception as
            "could not check" and refuses the deselect; returning 0 on failure
            would read as "there are none" and strand real positions.
            """
            ex = self.engine._executor_for(uid, venue)
            if ex is None:
                return 0
            return len(getattr(ex, "open_positions", None) or [])

        args = [a.strip().lower() for a in (ctx.args or []) if a.strip()]
        if args:
            wanted = [] if args[0] in ("none", "off", "clear", "single") else args
            ok, why = await _aio.to_thread(
                store.set_selection, tg_id, wanted,
                connected=_connected, open_positions=_open_on)
            if not ok:
                # Verbatim. The store's refusals already name the venue and the
                # count; rewording them here would be a second place for the
                # reason to drift from the rule that produced it.
                await self._send(update, f"\U0001f534 <b>Not changed</b> — {why}")
                return

        try:
            rd = routing_decision(tg_id, connected=_connected)
            conn = _connected(tg_id)
        except Exception as exc:
            system_log.warning("/venues state read failed for %s: %s", tg_id, exc)
            await self._send(update,
                "\U0001f534 Could not read your venue setup just now. Nothing "
                "was changed. Try again in a moment.")
            return

        pos = {}
        for v in (rd.get("venues") or ()):
            try:
                pos[v] = _open_on(tg_id, v)
            except Exception:
                # Omitted rather than zeroed: an unreadable count must not
                # print as "0 open" beside a venue that may hold positions.
                pass

        await self._send(update, venue_card(
            connected=conn, selected=store.raw_selection(tg_id),
            dropped=rd.get("dropped") or (), mode=rd.get("mode") or "off",
            enforce_available=ENFORCE_IMPLEMENTED, positions=pos))

    @guard("portfolio")
    async def _cmd_livepositions(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """/livepositions — show live positions and pending orders separately."""
        # Per-user isolation: route through the CALLER's executor so a user
        # only ever sees their own account's positions (resolves to the
        # shared operator executor when PER_USER_LIVE_ENABLED is off --
        # byte-identical default). Mirrors _cmd_open_positions / the pos_close
        # button callback, which already do this.
        executor = self._caller_executor(update)
        if executor is None:
            await self._send(update,
                "\U0001f512 <b>Access denied</b>\n\n"
                "No linked exchange account for this user.")
            return
        positions = executor._positions
        filled_pos = [p for p in positions.values() if p.status == "open"]
        pending_pos = [p for p in positions.values() if p.status == "pending_fill"]

        # ── Visual card path (position cards + pending-orders card). Best-effort:
        #    any failure falls through to the rich text readout below. ──
        if await self._render_livepositions_cards(update, filled_pos, pending_pos, executor):
            return

        # Fetch current prices for all relevant symbols
        current_prices: dict = {}
        all_pos = filled_pos + pending_pos
        if all_pos:
            try:
                exchange = await executor._get_exchange()
                for p in all_pos:
                    if p.symbol not in current_prices:
                        try:
                            tk = await exchange.fetch_ticker(p.symbol)
                            current_prices[p.symbol] = float(tk.get("last") or 0)
                        except Exception:
                            current_prices[p.symbol] = 0
            except Exception:
                pass

        # Best-effort: liquidation price + margin mode per symbol (read-only,
        # one guarded fetch — never blocks the card if it fails).
        ex_pos_map: dict = {}
        if filled_pos:
            try:
                exchange = await executor._get_exchange()
                _ex_positions = await exchange.fetch_positions(
                    params={"productType": "USDT-FUTURES"})
                for _ep in (_ex_positions or []):
                    if isinstance(_ep, dict) and _ep.get("symbol"):
                        ex_pos_map[_ep["symbol"]] = _ep
            except Exception:
                pass

        # Best-effort: ROLLING ATR per active symbol (Wilder, 1h candles) so the
        # trail-read threshold drifts tick-for-tick like the Playbook instead of
        # using the static atr_at_entry. Guarded — falls back to atr_at_entry.
        # Window matches the Playbook EXACTLY: kline_interval '1h', atr_period 14,
        # Wilder smoothing, limit = max(period + 5, 30) = 30 candles, so the ATR
        # number on the card equals the Playbook's _wilder_atr(bars, 14).
        _ATR_PERIOD = 14
        _ATR_LIMIT = max(_ATR_PERIOD + 5, 30)
        rolling_atr: dict = {}
        if filled_pos:
            try:
                exchange = await executor._get_exchange()
                from bot.core import position_telemetry as _pt
                for _p in filled_pos:
                    if _p.symbol in rolling_atr:
                        continue
                    try:
                        _ohlcv = await exchange.fetch_ohlcv(
                            _p.symbol, timeframe="1h", limit=_ATR_LIMIT)
                        if _ohlcv and len(_ohlcv) > 2:
                            _h = [float(c[2]) for c in _ohlcv]
                            _lo = [float(c[3]) for c in _ohlcv]
                            _cl = [float(c[4]) for c in _ohlcv]
                            _a = _pt.atr_from_candles(_h, _lo, _cl, period=_ATR_PERIOD)
                            if _a > 0:
                                rolling_atr[_p.symbol] = _a
                    except Exception:
                        pass
            except Exception:
                pass

        # Fallback: check exchange directly if no local positions at all
        if not filled_pos and not pending_pos:
            try:
                exchange = await executor._get_exchange()
                ex_positions = await exchange.fetch_positions(
                    params={"productType": "USDT-FUTURES"})
                ex_open = [p for p in (ex_positions or [])
                           if isinstance(p, dict) and float(p.get("contracts") or 0) > 0]
                if ex_open:
                    SEP = "\u2500" * 16
                    lines = [f"\U0001f4ca <b>LIVE POSITIONS</b> (from exchange)\n{SEP}\n"]
                    # One pure row per position; absent fields are dashes.
                    from bot.formatters.orphan_position import exchange_position_lines
                    for p in ex_open:
                        lines.append(exchange_position_lines(p))
                    lines.append("\n<i>\u26a0\ufe0f Showing exchange data \u2014 local tracking out of sync</i>")
                    await self._send(update, "\n".join(lines))
                    return
            except Exception:
                pass

        if not filled_pos and not pending_pos:
            await self._send(update, "\U0001f4ad No live positions or pending orders.")
            return

        SEP = "\u2500" * 16
        lines: list = []

        # ── Section 1: Active (filled) positions ──
        if filled_pos:
            lines.append(f"\U0001f4c8 <b>ACTIVE POSITIONS ({len(filled_pos)})</b>\n{SEP}\n")
            for p in filled_pos:
                dir_icon = "\U0001f7e2" if p.direction == "LONG" else "\U0001f534"
                sym_display = p.symbol.replace("/", "").replace(":USDT", "")
                sl_str = f"${p.stop_loss:,.4f}" if p.stop_loss > 0 else "\u26a0\ufe0f NOT SET"
                tp_str = f"${p.take_profit:,.4f}" if p.take_profit > 0 else "\u26a0\ufe0f NOT SET"
                lev = getattr(p, 'leverage', 10)
                cost = getattr(p, 'cost_usd', 0) or 0

                # Calculate uPnL
                cur = current_prices.get(p.symbol, 0)
                upnl_str = ""
                pnl_pct_str = ""
                if cur > 0 and p.entry_price > 0:
                    if p.direction == "LONG":
                        upnl = (cur - p.entry_price) / p.entry_price * cost
                        pnl_pct = (cur - p.entry_price) / p.entry_price * 100 * lev
                    else:
                        upnl = (p.entry_price - cur) / p.entry_price * cost
                        pnl_pct = (p.entry_price - cur) / p.entry_price * 100 * lev
                    sign = "+" if upnl >= 0 else ""
                    upnl_str = f"- uPnL: <code>{sign}${upnl:,.2f}</code> ({sign}{pnl_pct:.1f}%)\n"

                cur_str = f"- Current: <code>${cur:,.4f}</code>\n" if cur > 0 else ""

                # ── Read-only telemetry (matches the external Playbook readout) ──
                from bot.core import position_telemetry as _pt
                # Liquidation + margin mode (best-effort, from the exchange map).
                liq_line = ""
                _ep = ex_pos_map.get(p.symbol)
                if _ep and cur > 0:
                    _liq = _ep.get("liquidationPrice")
                    _mm = (_ep.get("marginMode") or _ep.get("marginType") or "").upper()
                    try:
                        _liqf = float(_liq) if _liq else None
                    except (TypeError, ValueError):
                        _liqf = None
                    if _liqf:
                        _ld = _pt.liq_distance_pct(cur, _liqf)
                        liq_line = (f"- Liq: <code>${_liqf:,.4f}</code>"
                                    + (f" ({_ld:.1f}% away)" if _ld is not None else "")
                                    + (f" | {_mm}" if _mm else "") + "\n")
                # Trail read (local — entry/SL/ATR + mark; never demands an order).
                # Prefer the rolling ATR (Playbook-style, drifts each tick); fall
                # back to atr_at_entry if the candle fetch was unavailable.
                trail_block = ""
                if cur > 0 and p.stop_loss > 0 and p.entry_price > 0:
                    _ts = getattr(p, "trailing_state", None)
                    _atr_val = rolling_atr.get(p.symbol) or (getattr(p, "atr_at_entry", 0.0) or 0.0)
                    _read = _pt.trail_read(
                        p.direction, p.entry_price, p.stop_loss, cur,
                        atr=_atr_val,
                        trailing_active=(_ts.get("trailing_active") if _ts else None))
                    trail_block = "\n".join(_pt.format_trail_read(_read)) + "\n"

                lines.append(
                    f"{dir_icon} <b>{p.direction} {sym_display}</b> {lev}x\n"
                    f"- Entry: <code>${p.entry_price:,.4f}</code>\n"
                    f"{cur_str}"
                    f"- Size: <code>${cost:,.2f}</code> | Qty: <code>{p.quantity:.6f}</code>\n"
                    f"- SL: <code>{sl_str}</code>\n"
                    f"- TP: <code>{tp_str}</code>\n"
                    f"{liq_line}"
                    f"{upnl_str}"
                    f"{trail_block}"
                    f"- ID: <code>{p.trade_id}</code>\n"
                )

        # ── Section 2: Pending limit orders ──
        if pending_pos:
            if filled_pos:
                lines.append("")  # spacer
            lines.append(f"\u23f3 <b>PENDING ORDERS ({len(pending_pos)})</b>\n{SEP}\n")
            for p in pending_pos:
                dir_icon = "\U0001f7e2" if p.direction == "LONG" else "\U0001f534"
                sym_display = p.symbol.replace("/", "").replace(":USDT", "")
                sl_str = f"${p.stop_loss:,.4f}" if p.stop_loss > 0 else "\u26a0\ufe0f NOT SET"
                tp_str = f"${p.take_profit:,.4f}" if p.take_profit > 0 else "\u26a0\ufe0f NOT SET"
                lev = getattr(p, 'leverage', 10)
                cost = getattr(p, 'cost_usd', 0) or 0

                # Distance to fill
                cur = current_prices.get(p.symbol, 0)
                dist_str = ""
                if cur > 0 and p.entry_price > 0:
                    dist_pct = abs(cur - p.entry_price) / p.entry_price * 100
                    dist_str = f" ({dist_pct:+.2f}% away)"

                # Time waiting + expiry countdown (the limit auto-cancels at the
                # 4h expiry \u2014 surface the countdown like the Playbook does).
                age_str = ""
                expiry_str = ""
                if hasattr(p, 'opened_at') and p.opened_at:
                    from datetime import datetime, timezone

                    from bot.config import CONFIG as _CFG
                    from bot.core import position_telemetry as _pt
                    now = datetime.now(timezone.utc)
                    delta = now - p.opened_at
                    mins = int(delta.total_seconds() // 60)
                    if mins < 60:
                        age_str = f"- Placed: <code>{mins}m ago</code>\n"
                    else:
                        hrs = mins // 60
                        age_str = f"- Placed: <code>{hrs}h {mins % 60}m ago</code>\n"
                    _rem = _pt.expiry_remaining_seconds(
                        p.opened_at.timestamp(),
                        _CFG.limit_orders.expire_seconds, now.timestamp())
                    expiry_str = f"- {_pt.format_expiry(_rem)}\n"

                cur_line = f"- Current: <code>${cur:,.4f}</code>{dist_str}\n" if cur > 0 else ""

                lines.append(
                    f"{dir_icon} <b>{p.direction} {sym_display}</b> \u2014 Limit Order\n"
                    f"- Limit: <code>${p.entry_price:,.4f}</code>\n"
                    f"{cur_line}"
                    f"- Size: <code>${cost:,.2f}</code> | Lev: {lev}x\n"
                    f"- SL: <code>{sl_str}</code>\n"
                    f"- TP: <code>{tp_str}</code>\n"
                    f"{age_str}"
                    f"{expiry_str}"
                    f"- ID: <code>{p.trade_id}</code>\n"
                )

        await self._send(update, "\n".join(lines))

    async def _render_livepositions_cards(self, update, filled_pos, pending_pos, executor=None) -> bool:
        """Render /livepositions as PNG cards: one position card per open position
        (composited into a single image) plus the pending-orders card.

        Best-effort and display-only: returns True if at least one card was sent;
        False (or on any error) lets the caller fall back to the text readout.

        executor: the CALLER's resolved executor (see _cmd_livepositions) --
        defaults to the shared operator executor for any other caller that
        hasn't been updated to resolve one (byte-identical to prior behaviour).
        """
        if not filled_pos and not pending_pos:
            return False
        if executor is None:
            executor = self.engine.live_executor
        try:
            from datetime import datetime, timezone

            from bot.formatters.signal_card import render_orders_card, render_position_card
            from bot.skills.chart_renderer import _composite_pngs

            exchange = None
            try:
                exchange = await executor._get_exchange()
            except Exception:
                pass

            async def _last(sym):
                """Current price, or None when we could not read it.

                None, not 0.0. A zero here used to flow into the position
                card as pnl_pct=0.0, which renders "+0.00%" beside a green
                stripe — a position that may be well underwater presented as
                exactly break-even, in an image, about real money.
                """
                try:
                    tk = await exchange.fetch_ticker(sym)
                    px = float(tk.get("last") or 0)
                    return px if px > 0 else None
                except Exception:
                    return None

            now = datetime.now(timezone.utc)
            sent_any = False

            # ── Position cards (one per open position, composited) ──
            pos_pngs: list = []
            for p in filled_pos:
                # No exchange client is the same fact as a failed ticker:
                # we do not know the current price.
                cur = await _last(p.symbol) if exchange else None
                lev = getattr(p, "leverage", 10) or 1
                cost = getattr(p, "cost_usd", 0) or 0
                # None means unreadable and the card renders "—". Omit, never
                # invent: a fabricated 0.00% is worse than an absent one,
                # because it looks like a measurement.
                pnl_usd = pnl_pct = None
                if cur and cur > 0 and p.entry_price > 0:
                    raw = ((cur - p.entry_price) if p.direction == "LONG"
                           else (p.entry_price - cur)) / p.entry_price
                    pnl_usd = _leveraged_pnl_usd(p.entry_price, cur, p.direction, cost, lev)
                    pnl_pct = raw * 100 * lev
                hold = ""
                if getattr(p, "opened_at", None):
                    mins = int((now - p.opened_at).total_seconds() // 60)
                    hold = f"{mins}m" if mins < 60 else f"{mins // 60}h {mins % 60}m"
                sl_pct = (abs(cur - p.stop_loss) / cur * 100) if (cur and cur > 0 and p.stop_loss > 0) else 0
                tp_pct = (abs(p.take_profit - cur) / cur * 100) if (cur and cur > 0 and p.take_profit > 0) else 0
                png = render_position_card({
                    "symbol": p.symbol, "direction": p.direction, "is_live": True,
                    "entry": p.entry_price, "now": cur or 0,
                    "pnl_pct": pnl_pct, "pnl_usd": pnl_usd, "net_pnl": pnl_usd,
                    "fees": 0.0, "size_usd": cost, "leverage": lev, "hold_time": hold,
                    "rr": getattr(p, "rr", 0) or 0,
                    "sl": p.stop_loss, "tp": p.take_profit,
                    "sl_pct": sl_pct, "tp_pct": tp_pct,
                    "sl_status": "on exchange" if getattr(p, "sl_order_id", None) else "bot-managed",
                    "tp_status": "on exchange" if getattr(p, "tp_order_id", None) else "bot-managed",
                })
                if png:
                    pos_pngs.append(png)
            if pos_pngs:
                combined = _composite_pngs(pos_pngs) if len(pos_pngs) > 1 else pos_pngs[0]
                # Surface WHY a stop is bot-managed (live incident follow-up):
                # the card shows the [bot-managed] label but not the venue's
                # rejection reason, so a persistent SL placement failure looked
                # like a design choice. Pull the recorded per-symbol reason.
                _why_lines = []
                for p in filled_pos:
                    if not getattr(p, "sl_order_id", None):
                        try:
                            _why = executor._last_sltp_reason(p.symbol)
                        except Exception:
                            _why = ""
                        if _why:
                            _sym_short = p.symbol.replace("/", "").replace(":USDT", "")
                            _why_lines.append(
                                f"⚠️ {html.escape(_sym_short)} SL bot-managed — venue said: "
                                f"<code>{html.escape(_why[:120])}</code>")
                _cap = f"\U0001f4c8 <b>ACTIVE POSITIONS ({len(pos_pngs)})</b>"
                if _why_lines:
                    _cap += "\n" + "\n".join(_why_lines[:4])
                if combined and await self._send_photo(update, combined, _cap):
                    sent_any = True

            # ── Pending limit orders card ──
            if pending_pos:
                order_rows = []
                for p in pending_pos:
                    cur = await _last(p.symbol) if exchange else 0.0
                    dist = (abs(cur - p.entry_price) / p.entry_price * 100) if (cur > 0 and p.entry_price > 0) else 0
                    order_rows.append({
                        "sym": p.symbol, "side": "BUY" if p.direction == "LONG" else "SELL",
                        "price": p.entry_price, "current_price": cur,
                        "amount": getattr(p, "quantity", 0) or 0, "type": "limit",
                        "dist_pct": dist, "oid": str(getattr(p, "trade_id", "")),
                    })
                opng = render_orders_card(order_rows, timestamp=f"{now.strftime('%H:%M')} UTC")
                if opng and await self._send_photo(
                        update, opng, f"⏳ <b>PENDING ORDERS ({len(order_rows)})</b>"):
                    sent_any = True

            return sent_any
        except Exception as exc:
            system_log.debug("livepositions card render failed: %s", exc)
            return False

    @guard("trade")
    async def _cmd_buy(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """/buy — DISABLED (futures-only mode)."""
        await self._send(update,
            "\u274c <b>Spot trading is disabled</b>\n\n"
            "RUNECLAW operates in <b>futures-only mode</b> (USDT-M perpetuals at 5x leverage).\n\n"
            "The bot automatically opens positions via AI analysis. "
            "Use <code>/livepositions</code> to view open positions.")

    @guard("trade")
    async def _cmd_sell(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """/sell — DISABLED (futures-only mode)."""
        await self._send(update,
            "\u274c <b>Spot trading is disabled</b>\n\n"
            "RUNECLAW operates in <b>futures-only mode</b> (USDT-M perpetuals at 5x leverage).\n\n"
            "Use <code>/liveclose TRADE_ID</code> to close a futures position.")

    @guard("portfolio")
    async def _cmd_paper(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """/paper [on|off] — toggle risk-free PAPER practice mode for YOUR trades.

        When ON, your confirmed trades are simulated into your paper portfolio
        (full SL/TP monitoring, no real orders). Other users are unaffected.
        """
        if not CONFIG.paper_sim_opt_in_enabled:
            await self._send(update,
                "📝 Paper practice mode is not enabled on this bot "
                "(<code>PAPER_SIM_OPT_IN_ENABLED</code> is off).")
            return
        tg_id = self._get_tg_id(update)
        action = (ctx.args[0].lower() if ctx.args else "status")
        if action in ("on", "enable", "start", "sim"):
            if self.users.set_sim_opt_in(tg_id, True):
                await self._send(update,
                    "📝 <b>PAPER mode ON</b> — your confirmed trades will be "
                    "<b>SIMULATED</b> (no real orders). Risk-free practice.\n"
                    "Switch back with <code>/paper off</code>.")
            else:
                await self._send(update, "⚠️ Could not enable paper mode (unknown user — use /start first).")
        elif action in ("off", "disable", "stop", "live"):
            if self.users.set_sim_opt_in(tg_id, False):
                await self._send(update,
                    "🔴 <b>PAPER mode OFF</b> — your confirmed trades will execute "
                    "<b>LIVE</b> (real orders), subject to your live-trading permission.")
            else:
                await self._send(update, "⚠️ Could not change paper mode (unknown user — use /start first).")
        else:
            on = self.users.sim_opt_in(tg_id)
            state = "🟢 ON — trades simulated" if on else "🔴 OFF — trades live"
            await self._send(update,
                f"📝 <b>PAPER practice mode: {state}</b>\n"
                f"<code>/paper on</code> — risk-free simulation  •  "
                f"<code>/paper off</code> — live trading")

    async def _cmd_trade(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """Manual trade placement: /trade buy SOL 71.42 sl 70.05 tp 76.42 [margin 250]"""
        if not update.message:
            return
        # Audit F-12: route through the standard guard so /trade enforces the
        # allowlist (F-2), the `trade` role permission, and the 24h session
        # staleness check \u2014 the prior inline `authorized`-only check skipped all
        # three, letting any authorized user (incl. a viewer role) queue trades.
        if not await self._guard(update, "trade"):
            return
        tg_id = self._get_tg_id(update)
        uid = str(update.effective_user.id) if update.effective_user else ""
        lang = self._lang(update)  # i18n: resolve once for this command

        text = (update.message.text or "").strip()
        # Remove /trade prefix
        args = text.split(None, 1)
        if len(args) < 2:
            await self._send(update, f"\U0001f4dd {t('trade_help', lang)}")
            return

        body = args[1].strip()
        parsed = self._parse_manual_trade(body)
        if isinstance(parsed, str):
            await self._send(update, f"\u26a0\ufe0f {parsed}")
            return

        direction, symbol, entry, sl, tp, margin_usd = parsed
        display_pair = f"{symbol}/USDT"

        # Build + register TradeIdea via the shared helpers (same code path as
        # the web gateway)
        from bot.skills.manual_trade import build_manual_idea, register_manual_idea
        try:
            idea = build_manual_idea(direction, symbol, entry, sl, tp)
        except ValueError as e:
            await self._send(update, f"\u26a0\ufe0f {t('trade_invalid', lang, detail=_safe_exc_text(e))}")
            return

        register_manual_idea(self.engine, idea, margin_usd)

        # Calculate R:R
        rr = idea.risk_reward_ratio
        sl_dist = abs(entry - sl) / entry * 100
        tp_dist = abs(tp - entry) / entry * 100

        margin_text = f"${margin_usd:,.0f}" if margin_usd else t("trade_margin_auto", lang)

        card = (
            f"\U0001f4cb <b>{t('lbl_manual_trade', lang)} \u2014 {html.escape(display_pair)} {direction}</b>\n"
            f"{'━' * 30}\n"
            f"{t('entry', lang)}:  <code>${entry:,.4f}</code>\n"
            f"{t('lbl_sl', lang)}:     <code>${sl:,.4f}</code> ({sl_dist:.1f}%)\n"
            f"{t('lbl_tp', lang)}:     <code>${tp:,.4f}</code> (+{tp_dist:.1f}%)\n"
            f"{t('lbl_rr', lang)}:    <code>{rr:.2f}</code>\n"
            f"{t('lbl_margin', lang)}: <code>{margin_text}</code>\n"
            f"{t('lbl_type', lang)}:   LIMIT\n"
            f"{'━' * 30}\n"
            f"<i>{t('trade_reduced_checks', lang)}</i>"
        )

        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("\u2705 " + t('confirm', lang), callback_data=f"confirm:{idea.id}:{uid}"),
             InlineKeyboardButton("\u274c " + t('cancel', lang), callback_data=f"reject:{idea.id}:{uid}")],
        ])

        await self._send(update, card, reply_markup=kb)
        audit(system_log, f"Manual trade created: {idea.id} {direction} {display_pair} entry={entry} sl={sl} tp={tp}",
              action="manual_trade_created", result="PENDING")

    def _parse_manual_trade(self, text: str):
        """Parse manual trade text. Returns (direction, symbol, entry, sl, tp, margin) or error string.

        Delegates to the shared parser (bot/skills/manual_trade.py) used by both
        Telegram and the web user gateway, so the two surfaces can't drift.
        """
        from bot.skills.manual_trade import parse_manual_trade
        return parse_manual_trade(text)

    @guard("halt")
    async def _cmd_halt(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        # Operator only, and there is no scoped variant to offer instead:
        # HaltSkill trips the shared breaker AND every per-user engine, clears
        # the shared idea book and transitions the whole engine to HALTED. None
        # of that has a per-account meaning, so `_control_scope` is not the
        # right tool — this is simply not a user's command.
        if not self._is_operator(update):
            await self._refuse_shared_control(update, "halt")
            return
        result = await self.registry.dispatch("halt", self.engine)
        await self._send(update, result)

    @guard("reset")
    async def _cmd_reset(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        risk, scope = self._control_scope(update)
        if risk is None:
            await self._refuse_shared_control(update, "reset")
            return
        was_active = risk.circuit_breaker_active
        streak_before = risk.consecutive_losses
        if scope == "shared":
            # Operator: reset the shared engine AND every per-user risk engine,
            # so resuming after a global halt clears every account's breaker.
            self.engine.reset_circuit_breaker_all()
        else:
            # This caller's own engine, and only theirs. The operator's breaker
            # — and every other user's — is untouched.
            risk.reset_circuit_breaker()
        lang = self._lang(update)
        if was_active:
            msg = f"\U0001f7e2 {t('reset_cb_done', lang)}"
        elif streak_before >= 3:
            msg = f"\U0001f7e2 {t('reset_streak_cleared', lang, n=streak_before)}"
        else:
            msg = f"\U0001f7e1 {t('reset_nothing', lang, n=streak_before)}"
        # The card must not let a personal reset read as a global one. Same rule
        # as every other surface here: the scope of a claim is part of the claim.
        if scope == "own":
            msg += f"\n\n<i>{t('control_scope_own', lang)}</i>"
        await self._send(update, msg)

    @guard("scan")
    async def _cmd_latest_signal(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """Show all pending trade signals with action buttons.

        If no signals are pending, auto-triggers a fresh scan cycle
        so the user always sees current opportunities.
        """

        # Filter to only show ideas above the display threshold (default 70%)
        from bot.config import CONFIG
        _display_min = CONFIG.risk.signal_display_min_confidence
        all_pending = list(self.engine.pending_ideas)
        pending = [i for i in all_pending if i.confidence >= _display_min]

        # If nothing clears the display threshold but the BACKGROUND loop already
        # found lower-confidence setups (full analysis), show those instantly
        # instead of triggering a slow interactive re-scan. Only re-scan when
        # there is genuinely nothing pending at all.
        below_note = ""
        if not pending and all_pending:
            pending = sorted(all_pending, key=lambda i: i.confidence, reverse=True)[:5]
            below_note = (f"ℹ️ <i>Best current setups (below the "
                          f"{_display_min:.0%} high-confidence line):</i>")

        if not pending:
            # Responsiveness gate: if the continuous background sweep ran
            # recently, its emptiness IS the current answer — a fresh re-scan
            # would just re-confirm "nothing" after another slow, throttle-
            # exposed pass (the live "bot seems slow" symptom). Serve an instant
            # honest status and only fall through to a live re-scan when the
            # background data is genuinely stale (loop stalled/throttled).
            _grace = int(getattr(CONFIG, "interactive_scan_fresh_grace_sec", 0) or 0)
            _last = float(getattr(self.engine, "_last_scan_time", 0.0) or 0.0)
            _interval = float(getattr(self.engine, "_current_scan_interval", 0.0)
                              or CONFIG.scan_interval_seconds)
            # How long a sweep actually takes, measured. None until one
            # finishes. Without it the window is sized as if a sweep were
            # instantaneous, which is the bug — see _background_scan_is_fresh.
            _sweep = getattr(self.engine, "_last_sweep_duration_s", None)
            _sweep = float(_sweep) if _sweep is not None else None
            _fresh, _next_in = _background_scan_is_fresh(
                _last, _interval, _grace, time.monotonic(), _sweep)
            _age = (time.monotonic() - _last) if _last > 0 else 0
            _skipped_note = _skipped_symbols_note(
                getattr(self.engine, "_last_analysis_timeout", None),
                time.monotonic())
            if _fresh:
                await self._send(update,
                    f"✅ <b>No setups above {_display_min:.0%} confidence "
                    f"right now.</b>\n\n"
                    # NOT "Full sweep". A sweep that skipped symbols on a
                    # per-symbol analysis timeout still lands here, and calling
                    # it full asserts coverage nobody measured — the same
                    # overclaim as printing a partial total as a whole one. The
                    # skipped count is appended below when there is one.
                    f"\U0001f4e1 Sweep ran {int(_age)}s ago — next in "
                    f"~{_next_in}s. The agent watches ~{CONFIG.top_movers_count} "
                    f"pairs continuously; a quiet tape means no high-conviction "
                    f"edge, not a stall.{_skipped_note}\n\n"
                    f"Try <code>/fullscan</code> for a deep multi-symbol pass now.")
                return
            await self._send(update,
                "\U0001f50d <b>No signals queued — running a quick scan...</b>")
            try:
                # Lightweight: skip the order-flow + multi-timeframe fetches so a
                # tap returns in seconds even under exchange throttling (the full
                # pipeline still runs in the background loop for auto-trading).
                result = await asyncio.wait_for(
                    self.engine.force_scan(
                        max_symbols=CONFIG.interactive_scan_count, lightweight=True),
                    timeout=CONFIG.interactive_scan_timeout_sec,
                )
                pending = [i for i in self.engine.pending_ideas if i.confidence >= _display_min]
                if not pending:
                    sig_count = result.get("signals", 0)
                    auto_count = result.get("auto_confirmed", 0)
                    msg = f"No trade setups above {_display_min:.0%} confidence found."
                    if sig_count > 0:
                        msg += f"\n\n\U0001f4e1 Scanned {sig_count} pairs"
                        if auto_count > 0:
                            msg += f" — {auto_count} were auto-confirmed"
                        msg += " but none passed confidence threshold."
                    msg += "\n\nTry <code>/fullscan</code> for deep multi-symbol analysis."
                    await self._send(update, msg)
                    return
            except asyncio.TimeoutError:
                pending = [i for i in self.engine.pending_ideas if i.confidence >= _display_min]
                if not pending:
                    await self._send(update,
                        "⏳ <b>Scan is taking longer than usual.</b> Try "
                        "<code>/latest_signal</code> again in a moment, or "
                        "<code>/fullscan</code> for the deep sweep."
                        + _scan_timeout_hint(getattr(self.engine, "analyzer", None),
                                             self.engine))
                    return
            except Exception as exc:
                await self._send(update,
                    f"Scan failed: {_safe_exc_text(exc)}\n"
                    f"Try <code>/fullscan</code> instead.")
                return

        uid = update.effective_user.id if update.effective_user else ""

        # Show ALL pending ideas, not just the last one.
        #
        # And SAY when some were filtered out. `pending` is the >= display-line
        # subset; `/status` counts `engine._pending_ideas` unfiltered, so the
        # two headline numbers disagree by exactly the ideas below the line —
        # "1 Trade Setup Found" beside "Pending Ideas: 2", with nothing on
        # either card explaining the gap. `below_note` already covers the case
        # where NOTHING clears the bar; the mixed case had no words at all,
        # which reads as "the engine found one idea" when it found two.
        #
        # Re-read the engine here rather than trusting the `all_pending` taken
        # at entry: the re-scan branches above refresh `pending` and not it.
        _all_now = list(self.engine.pending_ideas)
        _hidden = max(0, len(_all_now) - len(pending))
        _header = (f"\U0001f4a1 <b>{len(pending)} Trade Setup"
                   f"{'s' if len(pending) > 1 else ''} Found</b>")
        if _hidden and not below_note:
            _header += (f"\n<i>{_hidden} more below the {_display_min:.0%} "
                        f"confidence line, not shown — /status counts all "
                        f"{len(_all_now)}.</i>")
        _header += f"\n{'━' * 28}"
        if below_note:
            _header = f"{below_note}\n{_header}"
        await self._send(update, _header)

        # Cluster pending ideas by asset category (Crypto, Metal, Stock, …) so
        # /latest_signal reads grouped like the scan commands. TradeIdea has no
        # asset_category field, so derive it from the symbol via the shared
        # classifier. A lightweight header is sent when the category changes.
        from bot.core.market_scanner import (
            group_by_category, category_icon, category_for_symbol,
        )
        pending = [idea for _grp in
                   group_by_category(pending, lambda x: category_for_symbol(x.asset)).values()
                   for idea in _grp]
        _last_cat = None

        for i, idea in enumerate(pending, 1):
            # A single geometry-incomplete idea must not blow up the whole
            # command. The below-70% fallback surfaces lower-confidence ideas
            # straight from the background loop, and some carry a None
            # entry/SL/TP (a forming/watch setup) — `None > 0` and `${None:,.4f}`
            # both raise, which used to abort /latest_signal mid-list after the
            # first card rendered (live 2026-07-21: BTC shown, then "Something
            # broke on my end"). Render each idea defensively and skip a bad one.
            try:
                _cat = category_for_symbol(idea.asset)
                if _cat != _last_cat:
                    await self._send(update, f"{category_icon(_cat)} <b>{_cat}</b>")
                    _last_cat = _cat
                kb = InlineKeyboardMarkup([[
                    InlineKeyboardButton(t("btn_take_it", self._lang(update)), callback_data=f"confirm:{idea.id}:{uid}"),
                    InlineKeyboardButton(t("lbl_limit", self._lang(update)), callback_data=f"setlimit:{idea.id}:{uid}"),
                    InlineKeyboardButton(t("btn_skip", self._lang(update)), callback_data=f"reject:{idea.id}:{uid}"),
                ]])

                _dir = getattr(idea.direction, "value", str(idea.direction or "")) or ""
                d_icon = "\U0001f7e2" if _dir == "LONG" else "\U0001f534"

                def _num(v):
                    try:
                        return float(v)
                    except (TypeError, ValueError):
                        return 0.0
                entry, sl, tp = _num(idea.entry_price), _num(idea.stop_loss), _num(idea.take_profit)
                sl_pct = abs(entry - sl) / entry * 100 if entry > 0 else 0
                tp_pct = abs(tp - entry) / entry * 100 if entry > 0 else 0
                rr = _num(idea.risk_reward_ratio)
                pair = (idea.asset or "").replace("/USDT", "")
                _otype = getattr(idea, 'order_type', 'market') or 'market'
                _otype = str(_otype).upper()
                _otype_tag = f" {_otype}" if _otype == "LIMIT" else ""
                _st = str(getattr(idea, 'strategy_type', '') or '').upper()
                _st_tag = f" [{_st}]" if _st else ""

                # Try to send signal card image if available
                card_sent = False
                if hasattr(self, '_signal_card_fn') and self._signal_card_fn:
                    try:
                        chat_id = str(update.effective_chat.id) if update.effective_chat else ""
                        if chat_id:
                            await self._signal_card_fn(chat_id, idea, rank=i)
                            card_sent = True
                    except Exception:
                        pass

                if not card_sent:
                    # Text fallback. `idea.reasoning` opens with a provenance
                    # tag and is therefore never empty, so slicing it to 150
                    # printed "[gpt-4o|TREND_UP|swing|C=0.68]" in italics as
                    # the rationale whenever the model returned none. The line
                    # is dropped instead — the card's other four facts stand on
                    # their own.
                    _why = thesis_prose(idea.reasoning)
                    msg = (
                        f"{d_icon} <b>#{i} {html.escape(pair)}</b> — {_dir}{_st_tag}{_otype_tag}\n"
                        f"Entry: <code>${entry:,.4f}</code> | SL: <code>${sl:,.4f}</code> (-{sl_pct:.1f}%) | TP: <code>${tp:,.4f}</code> (+{tp_pct:.1f}%)\n"
                        f"R:R 1:{rr:.1f} | Conf <b>{idea.confidence:.0%}</b>"
                        + (f"\n<i>{html.escape(_why[:150])}</i>" if _why is not None else "")
                    )
                    await self._send(update, msg, reply_markup=kb)
            except Exception as exc:
                system_log.debug("latest_signal: skipped idea %s render: %s",
                                 getattr(idea, "id", "?"), exc)

            # Rate limit: avoid flooding Telegram
            if i < len(pending):
                await asyncio.sleep(0.3)  # asyncio is module-level imported

    @staticmethod
    def _synth_order_from_tracked(p) -> dict:
        """Build a ccxt-order-shaped dict from a bot-tracked pending_fill position.

        Lets a bot-tracked pending limit flow through the same rendering path as
        a real exchange order when the exchange query can't see it.
        """
        side = "buy" if getattr(p, "direction", "") == "LONG" else "sell"
        opened = getattr(p, "opened_at", None)
        return {
            "id": getattr(p, "trade_id", "") or "",
            "symbol": getattr(p, "symbol", "") or "",
            "type": "limit",
            "side": side,
            "price": getattr(p, "entry_price", 0) or 0,
            "amount": getattr(p, "quantity", 0) or 0,
            "remaining": getattr(p, "quantity", 0) or 0,
            "filled": 0,
            "status": "open",
            "triggerPrice": 0,
            "datetime": opened.isoformat() if opened is not None else "",
        }

    @staticmethod
    def _reconcile_open_orders(exchange_orders, tracked_pending, per_symbol_orders):
        """Decide what /openorders should display, reconciling the live exchange
        query with the bot's own tracked pending_fill orders.

        Returns ``(orders, desync)`` where ``desync`` is True when the exchange
        reports nothing but the bot is still tracking pending limit(s) — i.e. the
        bot-tracked records are being surfaced and should carry a warning.

        Priority:
          1. account-wide exchange result, if non-empty (source of truth);
          2. else, if the bot tracks nothing pending, genuinely empty;
          3. else, the per-symbol re-fetch result, if it found anything;
          4. else, the bot-tracked records, flagged as a possible desync.
        """
        if exchange_orders:
            return list(exchange_orders), False
        if not tracked_pending:
            return [], False
        if per_symbol_orders:
            return list(per_symbol_orders), False
        return [TradingCommands._synth_order_from_tracked(p) for p in tracked_pending], True

    async def _resolve_desync_orders(self, exchange, tracked_pending):
        """Resolve an open-orders desync definitively instead of guessing.

        When fetch_open_orders (account-wide AND per-symbol) shows nothing
        but the bot still tracks pending limits, the truth is one
        fetch_order call away: open-order queries exclude filled/cancelled
        orders BY DESIGN, so "exchange shows nothing" usually just means
        "it filled seconds ago" (live case 2026-07-13: a SHORT limit below
        market — marketable, cannot rest — showed as a scary desync when
        it had simply filled). Query each tracked order by id and report
        what actually happened.

        Returns ``(notes, synth_orders)``: human-readable resolution lines,
        and ccxt-shaped dicts for records that still merit rendering as
        open (order genuinely resting, or status unverifiable).
        """
        notes: list = []
        synths: list = []
        for p in tracked_pending:
            oid = getattr(p, "limit_order_id", None)
            sym = display_symbol(getattr(p, "symbol", ""))
            side = getattr(p, "direction", "") or "?"
            order = None
            status = None
            if oid:
                try:
                    order = await exchange.fetch_order(oid, p.symbol)
                    status = (order.get("status") or "").lower()
                except Exception:
                    status = None
            if status in ("closed", "filled"):
                avg = float((order.get("average") or order.get("price") or 0)
                            if order else 0)
                notes.append(
                    f"✅ {side} {sym} limit <b>FILLED</b>"
                    + (f" @ ${avg:,.4f}" if avg > 0 else "")
                    + " — the bot books the fill on its next check tick.")
            elif status in ("canceled", "cancelled", "rejected", "expired"):
                notes.append(
                    f"❌ {side} {sym} limit <b>{status.upper()}</b> on the "
                    "exchange — the bot clears it on its next check tick.")
            elif status == "open":
                # Genuinely resting — the open-orders queries missed it.
                synths.append(self._synth_order_from_tracked(p))
            else:
                synths.append(self._synth_order_from_tracked(p))
                notes.append(
                    f"⚠️ {side} {sym}: order status could not be verified — "
                    "possible desync; the bot reconciles on its next tick.")
        return notes, synths

    @guard("portfolio")
    async def _cmd_orders(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """Show open/pending orders on Bitget exchange."""

        await self._send(update, "<i>Fetching open orders from Bitget...</i>")

        try:
            exchange = await self.engine.live_executor._get_exchange()

            # Fetch all open orders (limit orders, trigger orders, SL/TP)
            open_orders = await exchange.fetch_open_orders(
                params={"productType": "USDT-FUTURES"})

            # Reconcile with the bot's own tracked pending limit orders.
            # /livepositions reads these from live_executor._positions; the
            # account-wide query above can miss them (Bitget's no-symbol futures
            # order query is unreliable), which makes the two commands disagree.
            # When that happens, retry per-symbol, and if the exchange still
            # shows nothing, surface the bot-tracked orders with a desync warning
            # instead of flatly reporting "none".
            try:
                tracked_pending = [
                    p for p in self.engine.live_executor._positions.values()
                    if getattr(p, "status", "") == "pending_fill"
                ]
            except Exception:
                tracked_pending = []

            per_symbol_orders: list = []
            if not open_orders and tracked_pending:
                seen_ids: set = set()
                for _p in tracked_pending:
                    try:
                        _per = await exchange.fetch_open_orders(_p.symbol)
                    except Exception:
                        _per = []
                    for _o in (_per or []):
                        _oid = _o.get("id", "")
                        if _oid not in seen_ids:
                            seen_ids.add(_oid)
                            per_symbol_orders.append(_o)

            open_orders, _desync = self._reconcile_open_orders(
                open_orders, tracked_pending, per_symbol_orders)

            if _desync:
                # Don't guess ("may have filled or been cancelled — verify
                # on Bitget"): fetch each tracked order by id and say what
                # actually happened. Filled/cancelled orders drop out of the
                # open-orders rendering — they are not open.
                notes, still_open = await self._resolve_desync_orders(
                    exchange, tracked_pending)
                open_orders = still_open
                if notes:
                    await self._send(update,
                        "🔎 <b>Pending order status</b>\n\n"
                        + "\n".join(notes))

            if not open_orders:
                await self._send(update,
                    "<b>Open Orders</b>\n\n"
                    "No pending orders on Bitget right now.\n\n"
                    "<i>Tip: Use the \"Limit\" button when confirming a trade to set a custom limit price.</i>")
                return

            # Group by type
            limit_orders = []
            sl_orders = []
            tp_orders = []
            other_orders = []

            from bot.config import CONFIG
            expire_sec = CONFIG.limit_orders.expire_seconds
            now_utc = datetime.now(UTC)

            for o in open_orders:
                otype = (o.get("type") or "").lower()
                sym = display_symbol(o.get("symbol", ""))
                side = (o.get("side") or "").upper()
                price = float(o.get("price") or 0)
                amount = float(o.get("amount") or o.get("remaining") or 0)
                trigger = float(o.get("triggerPrice") or o.get("stopPrice") or 0)
                filled = float(o.get("filled") or 0)
                status = o.get("status", "open")
                oid = o.get("id", "")[:12]
                created = o.get("datetime", "")[:16] if o.get("datetime") else ""

                # Calculate time remaining until expiry
                ttl_str = ""
                raw_dt = o.get("datetime") or ""
                if raw_dt and otype == "limit":
                    try:
                        from datetime import datetime as _dt
                        created_dt = _dt.fromisoformat(raw_dt.replace("Z", "+00:00"))
                        age_sec = (now_utc - created_dt).total_seconds()
                        remaining = max(0, expire_sec - age_sec)
                        if remaining <= 0:
                            ttl_str = " | \u23f0 expiring..."
                        else:
                            hrs = int(remaining // 3600)
                            mins = int((remaining % 3600) // 60)
                            if hrs > 0:
                                ttl_str = f" | \u23f0 {hrs}h {mins}m left"
                            else:
                                ttl_str = f" | \u23f0 {mins}m left"
                    except Exception:
                        pass

                entry = {
                    "sym": sym, "side": side, "price": price,
                    "trigger": trigger, "amount": amount, "filled": filled,
                    "status": status, "oid": oid, "created": created, "type": otype,
                    "ttl_str": ttl_str,
                }

                if "stop" in otype or "loss" in otype:
                    sl_orders.append(entry)
                elif "take" in otype or "profit" in otype:
                    tp_orders.append(entry)
                elif otype == "limit":
                    limit_orders.append(entry)
                else:
                    other_orders.append(entry)

            lines = [f"<b>Open Orders ({len(open_orders)})</b>", ""]

            # Fetch current prices for distance-to-fill calculation
            limit_syms = list({o["sym"] for o in limit_orders}) if limit_orders else []
            limit_prices_map: dict[str, float] = {}
            if limit_syms:
                try:
                    # Map display symbols back to exchange symbols for ticker fetch
                    _raw_syms = list({
                        raw_o.get("symbol", "") for raw_o in open_orders
                        if display_symbol(raw_o.get("symbol", "")) in limit_syms
                    })
                    if _raw_syms:
                        _tickers = await exchange.fetch_tickers(_raw_syms)
                        for _s, _t in _tickers.items():
                            limit_prices_map[display_symbol(_s)] = float(_t.get("last") or 0)
                except Exception:
                    pass

            if limit_orders:
                lines.append(f"<b>\U0001f4cb Limit Orders ({len(limit_orders)}):</b>")
                lines.append("")
                for o in limit_orders:
                    d_icon = "\U0001f7e2" if o["side"] == "BUY" else "\U0001f534"
                    dir_label = "LONG" if o["side"] == "BUY" else "SHORT"
                    fill_str = f" ({o['filled']:.4f} filled)" if o["filled"] > 0 else ""
                    cur_price = limit_prices_map.get(o["sym"], 0)

                    lines.append(f"{d_icon} <b>{o['sym']} {dir_label}</b> \u2014 Limit Order")
                    lines.append(f"  \U0001f4cd Limit: <code>${o['price']:,.4f}</code>{fill_str}")
                    if cur_price > 0:
                        dist = ((cur_price - o['price']) / cur_price) * 100
                        fill_hint = "\u2b07\ufe0f" if (o["side"] == "BUY" and cur_price > o['price']) else (
                            "\u2b06\ufe0f" if (o["side"] != "BUY" and cur_price < o['price']) else "\u2705")
                        lines.append(f"  \U0001f4b2 Current: <code>${cur_price:,.4f}</code>  {fill_hint} {dist:+.2f}% to fill")
                    lines.append(f"  \U0001f4b0 Qty: <code>{o['amount']:.4f}</code>{o['ttl_str']}")
                    lines.append(f"  ID: <code>{o['oid']}</code>")
                    if o['created']:
                        lines.append(f"  \u23f3 Placed: {o['created']}")
                    lines.append("")

            if sl_orders:
                lines.append(f"<b>Stop-Loss Orders ({len(sl_orders)}):</b>")
                for o in sl_orders:
                    trigger_str = f"trigger ${o['trigger']:,.4f}" if o['trigger'] > 0 else ""
                    lines.append(
                        f"  \U0001f6d1 <b>{o['sym']}</b> {o['side']} {trigger_str}")
                lines.append("")

            if tp_orders:
                lines.append(f"<b>Take-Profit Orders ({len(tp_orders)}):</b>")
                for o in tp_orders:
                    trigger_str = f"trigger ${o['trigger']:,.4f}" if o['trigger'] > 0 else ""
                    lines.append(
                        f"  \U0001f3af <b>{o['sym']}</b> {o['side']} {trigger_str}")
                lines.append("")

            if other_orders:
                lines.append(f"<b>Other ({len(other_orders)}):</b>")
                for o in other_orders:
                    lines.append(
                        f"  <b>{o['sym']}</b> {o['side']} {o['type']} "
                        f"@ <code>${o['price']:,.4f}</code>")
                lines.append("")

            lines.append("<i>Source: Bitget USDT-M Futures</i>")

            # ── Render orders card image ──
            card_sent = False
            try:
                from bot.formatters.signal_card import render_orders_card
                all_display_orders = limit_orders + sl_orders + tp_orders + other_orders
                card_data = []
                for o in all_display_orders[:6]:
                    cur_price = limit_prices_map.get(o["sym"], 0)
                    dist = ((cur_price - o['price']) / cur_price * 100) if cur_price > 0 and o['price'] > 0 else 0
                    card_data.append({
                        "sym": o["sym"],
                        "side": o["side"],
                        "price": o["price"],
                        "current_price": cur_price,
                        "amount": o["amount"],
                        "ttl_str": o.get("ttl_str", ""),
                        "oid": o["oid"],
                        "created": o.get("created", ""),
                        "type": o["type"],
                        "dist_pct": dist,
                    })
                now_str = datetime.now(UTC).strftime('%H:%M UTC')
                card_png = render_orders_card(card_data, timestamp=now_str)
                if card_png:
                    import io as _io
                    buf = _io.BytesIO(card_png)
                    buf.name = "orders.png"
                    chat_id = str(update.effective_chat.id) if update.effective_chat else ""
                    if chat_id:
                        await update.get_bot().send_photo(
                            chat_id=int(chat_id), photo=buf,
                            caption=f"\U0001f4cb <b>Open Orders</b> — {now_str}",
                            parse_mode="HTML")
                        card_sent = True
            except Exception as exc:
                system_log.warning("Orders card render failed: %s", exc)

            if not card_sent:
                await self._send(update, "\n".join(lines))
            # Always send text as well for copy-paste of IDs
            if card_sent:
                # Send compact text with order IDs only
                id_lines = ["<b>Order IDs</b> (for cancel):"]
                for o in all_display_orders[:6]:
                    dir_l = "LONG" if o["side"] == "BUY" else "SHORT"
                    id_lines.append(f"  {o['sym']} {dir_l} — <code>{o['oid']}</code>")
                await self._send(update, "\n".join(id_lines))

        except Exception as exc:
            logger.error(f"Orders fetch error: {exc}", exc_info=True)
            await self._send(update,
                f"\U0001f534 <b>Failed to fetch orders:</b> <code>{_safe_exc_text(exc)}</code>")

    @guard("portfolio")
    async def _cmd_open_positions(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """Show open positions in rich format — per-user."""
        user_id = self._get_tg_id(update)
        portfolio = self.engine.user_portfolios.get(user_id)

        positions_data = []

        # LIVE FIX: in LIVE mode, show positions from LiveExecutor.
        # Per-user isolation: route through the CALLER's executor so each user
        # sees only their own account's positions (resolves to the shared operator
        # executor when PER_USER_LIVE_ENABLED is off — byte-identical default).
        if CONFIG.is_live():
            executor = self._caller_executor(update)
            live_positions = executor.open_positions if executor else []
            if live_positions:
                prices: dict[str, float] = {}
                try:
                    exchange = await executor._get_exchange()
                    for p in live_positions:
                        if p.symbol not in prices:
                            try:
                                tk = await exchange.fetch_ticker(p.symbol)
                                last = float(tk.get("last") or 0)
                                if last > 0:
                                    prices[p.symbol] = last
                            except Exception:
                                pass
                except Exception:
                    pass

                for pos in live_positions:
                    # A failed ticker fetch used to fall back to the ENTRY
                    # price, which makes every derived number exactly 0.0:
                    # the card then printed "+0.0% ($0.00)" on a position
                    # whose current price was unknown. That is the sentence
                    # this repo's own doctrine opens with — an unfetchable
                    # price shown as +0.00% — and 0.0 is indistinguishable
                    # from a real, measured, break-even position. Carry the
                    # gap instead of papering over it.
                    _price_read = prices.get(pos.symbol)
                    last_price = (_price_read if _price_read is not None
                                  else pos.entry_price)
                    if pos.direction == "LONG":
                        pnl_pct_raw = ((last_price - pos.entry_price) / pos.entry_price) * 100
                    else:
                        pnl_pct_raw = ((pos.entry_price - last_price) / pos.entry_price) * 100
                    from datetime import datetime, timezone
                    hold_h = (datetime.now(timezone.utc) - pos.opened_at).total_seconds() / 3600
                    cost = pos.cost_usd if pos.cost_usd > 0 else pos.entry_price * pos.quantity
                    notional = last_price * pos.quantity
                    leverage = getattr(pos, 'leverage', 0) or (notional / cost if cost > 0 else 1.0)
                    pnl_pct = pnl_pct_raw * leverage
                    # Dollar P&L on the SAME (leveraged) basis as pnl_pct — the old
                    # (last-entry)*quantity understated it by the leverage multiple.
                    upnl_usd = _leveraged_pnl_usd(pos.entry_price, last_price, pos.direction, cost, leverage)
                    sl_dist = abs(last_price - pos.stop_loss) / last_price * 100 if last_price else 0
                    tp_dist = abs(pos.take_profit - last_price) / last_price * 100 if last_price else 0
                    risk_left = abs(last_price - pos.stop_loss) if pos.stop_loss else 0
                    reward_left = abs(pos.take_profit - last_price) if pos.take_profit else 0
                    rr_live = reward_left / risk_left if risk_left > 0 else 0
                    # Everything downstream of the mark is None when the mark
                    # was never read. Absent renders as "unknown"; zero renders
                    # as a claim.
                    _unread = _price_read is None
                    positions_data.append({
                        "pair": pos.symbol.replace("/", "").replace(":USDT", ""),
                        "direction": pos.direction,
                        "entry": round(pos.entry_price, 6),
                        "price_unavailable": _unread,
                        "current": None if _unread else round(last_price, 6),
                        "pnl_pct": None if _unread else round(pnl_pct, 2),
                        "pnl_usd": None if _unread else round(upnl_usd, 4),
                        "sl": round(pos.stop_loss, 6),
                        "tp": round(pos.take_profit, 6),
                        "sl_dist_pct": None if _unread else round(sl_dist, 2),
                        "tp_dist_pct": None if _unread else round(tp_dist, 2),
                        "size_usd": round(cost, 2),
                        "notional_usd": round(notional, 2),
                        "leverage": round(leverage, 2),
                        "rr_live": None if _unread else round(rr_live, 2),
                        "quantity": pos.quantity,
                        "comm_pct": CONFIG.risk.commission_pct,
                        "hold_hours": round(hold_h, 1),
                        "sl_order": "exchange" if pos.sl_order_id else "manual",
                        "tp_order": "exchange" if pos.tp_order_id else "manual",
                        "trade_id": pos.trade_id,
                        "status": getattr(pos, "status", "open"),
                        "strategy_type": getattr(pos, "strategy_type", "swing"),
                        # Adoption provenance for the card: whether this row is
                        # an adopted position and which ladder rung supplied
                        # its SL/TP ("exchange"/"inherited"/"default"/""), so
                        # "review the adopted position" doesn't require a code
                        # read to tell a strategy stop from a 3% safety default.
                        "origin": getattr(pos, "origin", ""),
                        "sl_tp_source": getattr(pos, "sl_tp_source", ""),
                    })
            elif executor:
                # No locally-tracked positions — fall back to exchange API
                # to catch orphans (positions opened outside bot or lost on restart)
                try:
                    exchange = await executor._get_exchange()
                    ex_positions = await exchange.fetch_positions()
                    open_ex = [p for p in (ex_positions or [])
                               if isinstance(p, dict) and float(p.get("contracts") or 0) > 0]
                    if open_ex:
                        syms = [p.get("symbol", "") for p in open_ex]
                        tickers = await exchange.fetch_tickers(syms)
                        prices = {s: float(t.get("last", 0)) for s, t in tickers.items() if t.get("last")}
                        # Try to fetch open trigger/conditional orders for SL/TP
                        sl_tp_map = {}  # symbol -> {"sl": price, "tp": price}
                        # WHETHER WE LOOKED, kept separate from what we found.
                        # This fetch is wrapped in `except: pass`, and an empty
                        # map is indistinguishable from "this book has no stop
                        # orders" — so one failed call reported EVERY orphan as
                        # unprotected. On a list of positions the bot did not
                        # open and is discovering, "SL: None" is the line that
                        # makes an operator act.
                        _orders_read = True
                        try:
                            open_orders = await exchange.fetch_open_orders()
                            for o in (open_orders or []):
                                osym = o.get("symbol", "")
                                otype = (o.get("type") or "").lower()
                                oside = (o.get("side") or "").lower()
                                trigger = float(o.get("triggerPrice") or o.get("stopPrice") or 0)
                                if trigger <= 0:
                                    continue
                                if osym not in sl_tp_map:
                                    sl_tp_map[osym] = {"sl": 0, "tp": 0}
                                # For a LONG: sell stop = SL, sell limit/take-profit = TP
                                # For a SHORT: buy stop = SL, buy limit/take-profit = TP
                                if "stop" in otype or "loss" in otype:
                                    sl_tp_map[osym]["sl"] = trigger
                                elif "take" in otype or "profit" in otype:
                                    sl_tp_map[osym]["tp"] = trigger
                                elif oside == "sell":
                                    # Closing sell = likely SL or TP for a long
                                    # Use price relative to entry to guess
                                    sl_tp_map[osym].setdefault("_sells", []).append(trigger)
                                elif oside == "buy":
                                    sl_tp_map[osym].setdefault("_buys", []).append(trigger)
                        except Exception:
                            # Not critical to the listing, but fatal to any
                            # claim about stops: absent is not "none".
                            _orders_read = False
                        from bot.formatters.orphan_position import (
                            orphan_position_row,
                        )
                        for p in open_ex:
                            sym = p.get("symbol", "")
                            sym_orders = sl_tp_map.get(sym, {})
                            # None = the conditional-order book could not be
                            # read, which is NOT the answer "this position has
                            # no stop". The row keeps them apart; one failed
                            # fetch used to report every orphan as unprotected.
                            positions_data.append(orphan_position_row(
                                p,
                                mark=prices.get(sym),
                                sl_price=(sym_orders.get("sl", 0) if _orders_read else None),
                                tp_price=(sym_orders.get("tp", 0) if _orders_read else None),
                                commission_pct=CONFIG.risk.commission_pct,
                            ))
                except Exception as exc:
                    logger.warning("Exchange position fallback failed: %s", exc)
        else:
            # PAPER mode: show paper positions
            open_pos = portfolio.open_positions
            if open_pos:
                try:
                    exchange = await self.engine.scanner._get_exchange()
                    syms = list({p.asset for p in open_pos})
                    tickers = await exchange.fetch_tickers(syms)
                    fresh = {s: float(t.get("last", 0)) for s, t in tickers.items() if t.get("last")}
                    if fresh:
                        portfolio.mark_to_market(fresh)
                except Exception:
                    pass

            with portfolio._lock:
                for tid, pos in portfolio._positions.items():
                    # NO ENTRY-PRICE FALLBACK. `.get(asset, pos.entry_price)`
                    # made an unpriced position render as exactly 0.00% and
                    # $0.00 — a position that may be 15% underwater presented as
                    # measured break-even, because the mark could not be read.
                    # The card ALREADY handles None correctly (pnl_unknown →
                    # "—", muted accent instead of green); the default was what
                    # stopped it ever seeing one.
                    _mark = portfolio._last_prices.get(pos.asset)
                    _priced = _mark is not None and _mark > 0
                    last_price = _mark if _priced else pos.entry_price
                    if _priced:
                        if pos.direction.value == "LONG":
                            pnl_pct_raw = ((last_price - pos.entry_price) / pos.entry_price) * 100
                        else:
                            pnl_pct_raw = ((pos.entry_price - last_price) / pos.entry_price) * 100
                        pos_lev = getattr(pos, 'leverage', 1) or 1
                        pnl_pct = pnl_pct_raw * pos_lev
                    else:
                        pnl_pct = None
                    from datetime import datetime, timezone
                    hold_h = (datetime.now(timezone.utc) - pos.opened_at).total_seconds() / 3600
                    positions_data.append({
                        "pair": pos.asset.replace("/", ""),
                        "direction": pos.direction.value,
                        "entry": round(pos.entry_price, 6),
                        # 0 rather than the entry price: the card renders an
                        # unreadable price as "—", and echoing the entry back as
                        # "NOW" would assert the market is sitting exactly there.
                        "current": round(last_price, 6) if _priced else 0,
                        "pnl_pct": round(pnl_pct, 2) if pnl_pct is not None else None,
                        "sl": round(pos.stop_loss, 6),
                        "tp": round(pos.take_profit, 6),
                        "size_usd": round(pos.quantity * pos.entry_price, 2),
                        "comm_pct": CONFIG.risk.commission_pct,
                        "hold_hours": round(hold_h, 1),
                    })

        # ── Split into filled positions vs pending orders ──
        filled_positions = [p for p in positions_data if p.get("status", "open") != "pending_fill"]
        pending_orders = [p for p in positions_data if p.get("status") == "pending_fill"]

        if not filled_positions and not pending_orders:
            await self._send(update, t("positions_none", self._lang(update)))
            return

        from bot.formatters.signal_card import render_position_card

        # ── SECTION 1: Open Positions (filled) ──
        _pos_lang = self._lang(update)
        if filled_positions:
            from bot.utils.portfolio_return import coverage_note, open_book_return
            _book = open_book_return(filled_positions)
            total_pnl = _book["pct"]
            pnl_icon = ("" if total_pnl is None
                        else "\U0001f7e2" if total_pnl > 0
                        else "\U0001f534" if total_pnl < 0 else "")
            _total = ("\u2014" if total_pnl is None
                      else f"{total_pnl:+.2f}% {t('lbl_total', _pos_lang)}")
            header = (f"\U0001f4ca <b>{t('hdr_open_positions_title', _pos_lang)} "
                      f"({len(filled_positions)})</b> {pnl_icon} {_total}")
            header += coverage_note(_book)
            # The other surface the degraded alert points at, and the one
            # whose entire subject is whether the stops are in place. verbose
            # is on here: /status may omit the healthy line to stay short, but
            # a positions card that says nothing about the monitor leaves the
            # reader to infer it ran, and inferring is what the alert asked
            # them not to do.
            _watch_line = position_watch_line(
                self.engine.position_watch()
                if hasattr(self.engine, "position_watch") else None,
                _pos_lang, verbose=True)
            if _watch_line:
                header += f"\n{_watch_line}"
            await self._send(update, header)
        elif not pending_orders:
            await self._send(update, t("positions_none_short", _pos_lang))

        for pos in filled_positions:
            tid = pos.get('trade_id', pos['pair'])
            pair = pos.get("pair", "N/A")
            direction = pos.get("direction", "LONG")
            entry = pos.get("entry", 0)
            current = pos.get("current", entry)
            # `.get(k, 0)` was manufacturing a measured break-even from an
            # absent field. The producers now send None when the mark could not
            # be read, and the card already renders that honestly — so the
            # default has to stop overriding it. Absent and None are the same
            # thing here: we do not know.
            pnl_pct = pos.get("pnl_pct")
            pnl_usd = pos.get("pnl_usd")
            _pnl_known = pnl_pct is not None and pnl_usd is not None
            # NO `, 0` / `, 1` DEFAULTS HERE, and that is not tidying.
            # `orphan_position_row` returns an explicit None for every field it
            # could not read — margin, leverage, R:R, age, the stop distances —
            # and `.get(key, default)` DOES NOT SUBSTITUTE FOR A STORED None.
            # So each default below was dead, the None flowed on, and
            # `if hold_h < 1:` raised TypeError on the first orphan with an
            # unreadable age. This loop has no try/except, so that killed the
            # WHOLE listing — on the command an operator runs precisely because
            # they do not know what is out there.
            #
            # The pnl_pct/pnl_usd reads directly above were fixed for exactly
            # this, with a comment saying so. Fixing the two lines left the
            # surface half-cured; these are the rest of the row.
            sl = pos.get("sl")
            tp = pos.get("tp")
            sl_dist = pos.get("sl_dist_pct")
            tp_dist = pos.get("tp_dist_pct")
            size_usd = pos.get("size_usd")
            leverage = pos.get("leverage")
            rr_live = pos.get("rr_live")
            hold_h = pos.get("hold_hours")
            sl_order = pos.get("sl_order", "")
            tp_order = pos.get("tp_order", "")
            comm_pct = pos.get("comm_pct", CONFIG.risk.commission_pct)

            # Hold time display. An age of "0m" reads as JUST OPENED, which is
            # a specific and wrong claim about a position of unknown age.
            if hold_h is None:
                hold_str = "unknown"
            elif hold_h < 1:
                hold_str = f"{hold_h * 60:.0f}m"
            elif hold_h < 24:
                hold_str = f"{hold_h:.1f}h"
            else:
                hold_str = f"{hold_h / 24:.1f}d"

            # Fee calculations. A fee is a fraction of a notional; with no
            # margin and no age there is no fraction to take, and $0.00 fees
            # would read as a free position.
            exit_notional = pos.get("notional_usd")
            if exit_notional is None:
                _qty = pos.get("quantity")
                exit_notional = (current * _qty
                                 if current is not None and _qty is not None
                                 else None)
            if size_usd is None or hold_h is None or exit_notional is None:
                entry_fee = exit_fee = total_fees = funding_paid = None
            else:
                entry_fee = size_usd * (comm_pct / 100.0)
                exit_fee = exit_notional * (comm_pct / 100.0)
                total_fees = entry_fee + exit_fee
                funding_paid = size_usd * (0.01 / 100.0) * (hold_h / 8.0)
            # Net is only knowable if gross is. Subtracting fees from an
            # unreadable gross would print a confident negative — the position
            # shown as down exactly the fee total, which reads as a real
            # measured loss rather than "we could not price this". The card
            # renders None here as "—" via its own net_unknown branch.
            net_pnl = (None if (pnl_usd is None or total_fees is None)
                       else pnl_usd - total_fees - funding_paid)

            sl_tag = "on exchange" if sl_order == "exchange" else "bot-managed"
            tp_tag = "on exchange" if tp_order == "exchange" else "bot-managed"

            pos_card_data = {
                "symbol": pair.replace("USDT", "/USDT") if "USDT" in pair else pair,
                "direction": direction,
                "is_live": CONFIG.is_live(),
                "entry": entry,
                "now": current,
                "pnl_pct": pnl_pct,
                "pnl_usd": pnl_usd,
                "net_pnl": net_pnl,
                "fees": (None if total_fees is None
                         else total_fees + funding_paid),
                "size_usd": size_usd,
                "leverage": leverage,
                "hold_time": hold_str,
                "rr": rr_live,
                "sl": sl,
                "tp": tp,
                "sl_pct": sl_dist,
                "tp_pct": tp_dist,
                "sl_status": sl_tag,
                "tp_status": tp_tag,
            }

            try:
                card_png = render_position_card(pos_card_data)
            except Exception as exc:
                system_log.debug("Position card render failed for %s: %s", pair, exc)
                card_png = None

            d_emoji = "\U0001f7e2" if direction == "LONG" else "\U0001f534"
            # A white circle, not green or red. Colour is a claim: a green dot
            # beside an unreadable position asserts it is winning, and red
            # asserts it is losing, on the strength of a price we never got.
            pnl_emoji = ("⚪" if not _pnl_known
                         else "\U0001f7e2" if pnl_pct >= 0 else "\U0001f534")
            _pnl_txt = ("price unavailable" if not _pnl_known
                        else f"{pnl_pct:+.2f}% (${pnl_usd:+,.2f})")
            # Owner-tag the destructive Close callback (RC-AUD-004 style IDOR
            # guard) so only the user who owns this position can close it.
            kb = InlineKeyboardMarkup([[
                InlineKeyboardButton(f"{pair}", callback_data=f"pos_details_{tid}"),
                InlineKeyboardButton("Close", callback_data=f"pos_close_{tid}:{user_id}"),
            ]])

            if card_png:
                mode_tag = "LIVE" if CONFIG.is_live() else "PAPER"
                st_tag = pos.get("strategy_type", "").upper()
                st_str = f" [{st_tag}]" if st_tag else ""
                cap = (f"<b>{html.escape(pair)}</b> {mode_tag}\n"
                       f"{d_emoji} {direction}{st_str} | {pnl_emoji} {_pnl_txt}")
                await self._send_photo(update, card_png, cap, reply_markup=kb)
            else:
                # Fallback to text if PNG render fails
                msg = render_open_positions([pos], lang=self._lang(update))
                await self._send(update, msg, reply_markup=kb)

        # ── SECTION 2: Pending Orders (unfilled limit orders) ──
        if pending_orders:
            from datetime import datetime, timezone
            pend_header = (f"\u2694\ufe0f <b>PENDING ORDERS ({len(pending_orders)})</b>")
            await self._send(update, pend_header)

            for po in pending_orders:
                pair = po.get("pair", "N/A")
                direction = po.get("direction", "LONG")
                limit_price = po.get("entry", 0)
                current = po.get("current", limit_price)
                sl = po.get("sl", 0)
                tp = po.get("tp", 0)
                size_usd = po.get("size_usd", 0)
                notional_usd = po.get("notional_usd", size_usd)
                leverage = po.get("leverage", 1)
                tid = po.get("trade_id", pair)
                hold_h = po.get("hold_hours", 0)
                quantity = po.get("quantity", 0)
                comm_pct = po.get("comm_pct", CONFIG.risk.commission_pct)
                sl_order = po.get("sl_order", "")
                tp_order = po.get("tp_order", "")

                # Distance from current price to limit
                if limit_price > 0 and current > 0:
                    dist_pct = ((current - limit_price) / current) * 100
                else:
                    dist_pct = 0

                # SL/TP distances from limit price (where it will fill)
                if sl > 0 and limit_price > 0:
                    sl_dist_pct = abs(limit_price - sl) / limit_price * 100
                else:
                    sl_dist_pct = 0
                if tp > 0 and limit_price > 0:
                    tp_dist_pct = abs(tp - limit_price) / limit_price * 100
                else:
                    tp_dist_pct = 0

                # R:R at fill
                risk_at_fill = abs(limit_price - sl) if sl > 0 else 0
                reward_at_fill = abs(tp - limit_price) if tp > 0 else 0
                rr_at_fill = reward_at_fill / risk_at_fill if risk_at_fill > 0 else 0

                # Fee estimate — fees are charged on notional, not margin
                entry_notional = notional_usd if notional_usd > 0 else (limit_price * quantity if quantity else size_usd * leverage)
                entry_fee = entry_notional * (comm_pct / 100.0)
                exit_notional = entry_notional  # assume same notional on exit
                exit_fee = exit_notional * (comm_pct / 100.0)
                total_fees = entry_fee + exit_fee

                d_icon = "\U0001f7e2" if direction == "LONG" else "\U0001f534"
                dir_label = "LONG" if direction == "LONG" else "SHORT"

                # Age display
                if hold_h < 1:
                    age_str = f"{hold_h * 60:.0f}m"
                elif hold_h < 24:
                    age_str = f"{hold_h:.1f}h"
                else:
                    age_str = f"{hold_h / 24:.1f}d"

                # Fill direction hint
                if direction == "LONG":
                    fill_hint = "\u2b07\ufe0f" if current > limit_price else "\u2705"
                else:
                    fill_hint = "\u2b06\ufe0f" if current < limit_price else "\u2705"

                sl_tag = "on exchange" if sl_order == "exchange" else "bot-managed"
                tp_tag = "on exchange" if tp_order == "exchange" else "bot-managed"
                strategy_type = po.get("strategy_type", "swing").upper()

                lines = [
                    f"{d_icon} <b>{html.escape(pair)} {dir_label}</b> \u2014 Limit Order \u2022 {strategy_type}",
                    "",
                    f"\U0001f4cd <b>Limit Price:</b> <code>${limit_price:,.4f}</code>",
                    f"\U0001f4b2 <b>Current:</b>    <code>${current:,.4f}</code>  {fill_hint} {dist_pct:+.2f}% to fill",
                    "",
                    f"\U0001f4b0 <b>Size:</b> <code>${size_usd:,.2f}</code> margin | <b>{leverage:.0f}x</b> leverage",
                ]
                if quantity > 0:
                    lines.append(f"   Qty: <code>{quantity:.4f}</code> contracts")

                lines.append("")

                if sl > 0:
                    lines.append(
                        f"\U0001f6d1 <b>SL:</b> <code>${sl:,.4f}</code>  ({sl_dist_pct:.2f}% from entry) [{sl_tag}]")
                if tp > 0:
                    lines.append(
                        f"\U0001f3af <b>TP:</b> <code>${tp:,.4f}</code>  ({tp_dist_pct:.2f}% from entry) [{tp_tag}]")
                if rr_at_fill > 0:
                    lines.append(f"\u2696\ufe0f <b>R:R at fill:</b> 1:{rr_at_fill:.1f}")

                lines.append("")
                lines.append(f"\U0001f4b8 <b>Est. fees:</b> ${total_fees:.4f} (entry + exit)")
                lines.append(f"\u23f3 <b>Waiting:</b> {age_str}")

                kb = InlineKeyboardMarkup([[
                    InlineKeyboardButton("Cancel", callback_data=f"pos_close_{tid}:{user_id}"),
                ]])

                await self._send(update, "\n".join(lines), reply_markup=kb)

        elif not filled_positions:
            await self._send(update, "No pending orders.")

    @guard("halt")
    async def _cmd_pause(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """Pause trading — activates circuit breaker."""
        risk, scope = self._control_scope(update)
        if risk is None:
            await self._refuse_shared_control(update, "pause")
            return
        risk.emergency_halt("pause_telegram")
        rendered = wr_pause(scope=scope)
        await self._send(update, rendered["text"])
        audit(system_log, "Bot paused via /pause", action="pause", result="OK",
              data={"scope": scope})

    @guard("reset")
    async def _cmd_resume(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """Resume trading — deactivates circuit breaker."""
        risk, scope = self._control_scope(update)
        if risk is None:
            await self._refuse_shared_control(update, "resume")
            return
        risk.reset_circuit_breaker()
        # Honest resume: if the daily-loss/drawdown condition still holds, the
        # breaker re-trips on the next evaluation — warn instead of showing a
        # clean CLEAR that the next status card contradicts with "Paused".
        _retrip = ""
        try:
            _retrip = risk.pending_retrip_reason() or ""
        except Exception:
            _retrip = ""
        # And the gate the reset does NOT clear: the warning-rate breaker and
        # the loss-streak gate sit outside reset_circuit_breaker(), so "Trading
        # ENABLED" was printed over entries still being refused (live, 13:59
        # on 2026-09-03). Read AFTER the reset, through the seam.
        _gate = self._resume_gate_state(risk)
        rendered = wr_resume(retrip_warning=_retrip, scope=scope, gate=_gate)
        await self._send(update, rendered["text"])
        audit(system_log, "Bot resumed via /resume", action="resume", result="OK",
              data={"retrip_warning": _retrip or None, "scope": scope,
                    "gate_after_reset": _gate})

    @staticmethod
    def _resume_gate_state(risk) -> Optional[str]:
        """trading_blocked_by after the reset: "" open, a reason string when
        entries are still refused, None when it could not be read."""
        try:
            return str(getattr(risk, "trading_blocked_by", "") or "")
        except Exception as exc:
            system_log.warning("/resume: entry gate unreadable after reset: %s", exc)
            return None

    @guard("halt")
    async def _cmd_emergency_stop(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """Emergency stop confirmation prompt."""
        # Operator only. The confirm button runs engine.emergency_halt_all(),
        # which halts every engine, clears queued ideas and FLATTENS EVERY
        # ACCOUNT — operator and per-user alike. Gated here as well as on the
        # callback so the button is never offered to somebody who cannot press
        # it: a confirm prompt that refuses on confirm is its own defect.
        if not self._is_operator(update):
            await self._refuse_shared_control(update, "emergency_stop")
            return
        rendered = wr_emergency_stop()
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("\u26d4 CONFIRM STOP", callback_data="emergency_confirm"),
             InlineKeyboardButton("\u21a9\ufe0f Cancel", callback_data="emergency_cancel")],
        ])
        await self._send(update, rendered["text"], reply_markup=kb)
