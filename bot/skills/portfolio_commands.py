"""The portfolio-and-record command group — a slice out of the handler.

`/portfolio`, `/performance`, `/daily_report`, `/risk`, `/enforcing`,
`/rejected`, `/costs`, `/holdtime`, `/signals`, `/classpf`, `/networth` and
`/exposure`: the cards that tell an operator what the book holds, what it
made, and what is refusing trades. Every one of them is a read; nothing here
places or closes an order. Their behaviour is covered where it always was
(`test_daily_report_risk_is_measured`, `test_trade_gate_parity`,
`test_win_rate_disclosure`, `test_telegram_web_parity`,
`test_universe_expansion`, `test_i18n_portfolio`, `test_paper_numbers_under_a_live_label`);
`tests/test_handler_mixins.py` holds this class to the split's rules.

`_unpriced_tag` moved with the group because `/portfolio` is its only
caller: the " (+N unpriced)" suffix on a W/L line, silent on a clean set,
that #1020 added so a corrected win rate cannot read as covering the whole
total beside it.

A mixin, not a leaf: every method reads `self.engine` and answers through
`self._send`, `self._send_photo` or `self._send_error`. The four web-parity
formatters (`_format_networth`, `_format_exposure`, `_format_research`,
`_format_rwa`) stay on the handler beside each other — the market group
reads one of them too — and the two this group calls are declared below as
host staticmethods.
"""
from __future__ import annotations

import html
from datetime import datetime
from typing import TYPE_CHECKING, Optional

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from bot.compat import UTC
from bot.config import CONFIG
from bot.core.trade_gate import entry_gate
from bot.skills.command_guard import guard
from bot.utils.i18n import t
from bot.utils.logger import audit, system_log
from bot.utils.trade_filter import ORPHAN_PREFIXES as _ORPHAN_PREFIXES
from bot.utils.win_rate import pnl_stats as _pnl_stats
from bot.utils.win_rate import trade_pnl as _trade_pnl
from bot.utils.win_rate import win_stats as _win_stats
from bot.warroom.warroom_bot import render_daily_report as wr_daily_report
from bot.warroom.warroom_bot import render_performance as wr_performance
from bot.warroom.warroom_bot import render_risk as wr_risk

if TYPE_CHECKING:
    from bot.core.engine import RuneClawEngine
    from bot.core.signal_tracker import SignalTracker
    from bot.marketing.channel_forwarder import ChannelForwarder
    from bot.skills.skill_registry import SkillRegistry


def _unpriced_tag(stats: dict) -> str:
    """" (+N unpriced)" for a W/L line, or "" when everything scored.

    #1020 corrected the arithmetic on ten win counts and then showed the
    corrected rate with no hint that it covers fewer trades than the total
    beside it -- a field written and never read, which is the exact rule
    #1018 added ("a tally nothing reads answers nothing"). Silent on a clean
    set, because a caveat on every healthy line is how a real one is skipped.
    """
    try:
        n = int((stats or {}).get("unscored", 0) or 0)
    except (AttributeError, TypeError, ValueError):
        return ""
    return f" <i>(+{n} unpriced)</i>" if n > 0 else ""


class PortfolioCommands:
    """The book, the record and the gates, as cards. Host contract below; methods after."""

    if TYPE_CHECKING:
        # Provided by TelegramHandler, and ONLY declared here — declarations,
        # never bodies; tests/test_handler_mixins.py checks every name against
        # what the handler really defines.
        engine: RuneClawEngine
        registry: SkillRegistry
        forwarder: ChannelForwarder
        signal_tracker: SignalTracker
        _WEB_LINK_HINT: str

        async def _send(self, update: Update, text: str,
                        reply_markup=None, edit: bool = False) -> None: ...

        async def _send_error(self, update: Update, command_name: str, exc: Exception) -> None: ...

        async def _send_photo(self, update: Update, png: bytes, caption: str,
                              reply_markup=None) -> bool: ...

        def _get_tg_id(self, update: Update) -> str: ...

        def _lang(self, update: Update) -> str: ...

        def _caller_executor(self, update: Update): ...

        @staticmethod
        def _format_networth(paper: Optional[dict], cex: dict) -> str: ...

        @staticmethod
        def _format_exposure(data: dict) -> str: ...

    @guard("portfolio")
    async def _cmd_classpf(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """Live performance bucketed by asset class (Crypto / Metal /
        Commodity / ETF / Pre-IPO / Stock) — the evidence base for growing
        or pruning the non-crypto universe. Computed from the executor's
        closed trades; nothing surfaced this breakdown before."""
        from bot.core.market_scanner import category_for_symbol, category_icon

        trades = list(self.engine.live_executor.closed_positions or [])
        if not trades:
            await self._send(update, "📊 No closed live trades yet — "
                                     "per-class stats appear after the first close.")
            return

        from bot.utils.close_reason import is_filled_close

        buckets: dict[str, list[float]] = {}
        skipped_non_fills = 0
        for tr in trades:
            try:
                pnl = float(getattr(tr, "pnl_usd", 0) or 0)
                if not is_filled_close(getattr(tr, "close_reason", None), pnl):
                    skipped_non_fills += 1
                    continue  # never filled — no capital was at risk
                cat = category_for_symbol(getattr(tr, "symbol", "") or "")
            except Exception:
                continue
            buckets.setdefault(cat, []).append(pnl)

        n_filled = sum(len(v) for v in buckets.values())
        lines = ["📊 <b>Live performance by asset class</b>",
                 f"({n_filled} filled trades, net PnL"
                 + (f"; {skipped_non_fills} never-filled records excluded)"
                    if skipped_non_fills else ")")]
        for cat in sorted(buckets, key=lambda c: -sum(buckets[c])):
            pnls = buckets[cat]
            wins = [p for p in pnls if p > 0]
            losses = [-p for p in pnls if p < 0]
            gw, gl = sum(wins), sum(losses)
            pf = (gw / gl) if gl > 0 else (float("inf") if gw > 0 else 0.0)
            pf_s = "∞" if pf == float("inf") else f"{pf:.2f}"
            wr = 100.0 * len(wins) / len(pnls) if pnls else 0.0
            lines.append(
                f"{category_icon(cat)} <b>{cat}</b>: {len(pnls)} trades · "
                f"PF <b>{pf_s}</b> · WR {wr:.0f}% · net ${sum(pnls):+.2f}")
        lines.append("")
        lines.append("PF &gt; 1 = profitable class. Small samples lie — "
                     "judge classes on 20+ trades.")
        await self._send(update, "\n".join(lines))

    @guard("networth")
    async def _cmd_networth(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """/networth — the caller's own read-only cross-venue snapshot: paper
        equity plus one balance fetch on their connected venue (the same
        primitives the web gateway's net-worth endpoint uses)."""
        import asyncio as _aio
        tg_id = self._get_tg_id(update)
        paper = None
        try:
            snap = self.engine.user_portfolios.get(tg_id).snapshot()
            paper = {"equity_usd": round(float(snap.equity_usd), 2),
                     "total_pnl": round(float(snap.total_pnl), 2)}
        except Exception:
            paper = None
        cex: dict = {"connected": False}
        try:
            from bot.core.exchange_credentials import (balance_snapshot,
                                                       get_credential_store)
            store = get_credential_store()
            if store.has(tg_id):
                venue = store.get_venue(tg_id)
                fields = store.get(tg_id)
                if not fields:
                    cex = {"connected": True, "venue": venue,
                           "equity_usd": None, "detail": "credentials unreadable"}
                else:
                    try:
                        snap_cex = await _aio.wait_for(
                            balance_snapshot(venue, fields), timeout=25)
                    except _aio.TimeoutError:
                        snap_cex = {"venue": venue, "equity_usd": None,
                                    "detail": "venue timeout"}
                    cex = {"connected": True, **snap_cex}
        except Exception as exc:
            system_log.debug("/networth CEX read failed: %s", exc)
            cex = {"connected": False}
        await self._send(update, self._format_networth(paper, cex))

    @guard("exposure")
    async def _cmd_exposure(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """/exposure — net per-asset exposure across perps + on-chain spot,
        the same netting the web Exposure panel shows."""
        import asyncio as _aio
        from bot.utils.web_data_pull import fetch_exposure
        data = await _aio.to_thread(fetch_exposure, self._get_tg_id(update))
        if not data or "assets" not in data:
            await self._send(update, self._WEB_LINK_HINT)
            return
        await self._send(update, self._format_exposure(data))

    @guard("journal")
    async def _cmd_holdtime(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Show hold-time analytics by strategy type."""
        try:
            from bot.formatters.market_cards import render_holdtime
            await self._send(update, render_holdtime(
                self.engine.hold_analytics.summary()))
        except Exception as exc:
            await self._send_error(update, "the hold-time analysis", exc)

    @guard("portfolio")
    async def _cmd_portfolio(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        user_id = self._get_tg_id(update)
        lang = self._lang(update)  # i18n: resolve once for this render
        # Use per-user portfolio if it exists, otherwise show shared
        if self.engine.user_portfolios.has_user(user_id):
            portfolio = self.engine.user_portfolios.get(user_id)
        else:
            portfolio = self.engine.user_portfolios.get(user_id)  # creates new one

        positions = portfolio.open_positions

        # Fetch fresh prices before rendering so PnL is accurate
        if positions:
            try:
                exchange = await self.engine.scanner._get_exchange()
                syms = list({p.asset for p in positions})
                tickers = await exchange.fetch_tickers(syms)
                fresh_prices = {s: float(t.get("last", 0)) for s, t in tickers.items() if t.get("last")}
                if fresh_prices:
                    portfolio.mark_to_market(fresh_prices)
            except Exception:
                pass  # fall back to whatever prices we have

        state = portfolio.snapshot()
        history = portfolio.trade_history

        # LIVE FIX: in LIVE mode, show real exchange balance prominently
        mode_str = "LIVE" if CONFIG.is_live() else "PAPER"
        # ── Values for the PNG stats card, set by BOTH branches ──────────
        # `state` is the PAPER tracker. The card below rendered six tiles
        # straight off it under a hero that was the real exchange equity, so a
        # live-only account read "$+0.00" IN GREEN, "0%" win rate, 0 trades and
        # "0.0%" drawdown in gray — every one of them a paper fact wearing a
        # live label, and `_pnl >= 0` is the table's "unreadable won" shape.
        # The text lines under it had already been rewritten around
        # realized_totals()/win_stats(); the PNG returns before they are ever
        # sent, so the honest half only ran when Pillow failed.
        #
        # None means NOT MEASURED and renders as an em dash in gray. It is
        # never the same value as a measured zero.
        _card_pnl: Optional[float] = None
        _card_wr: Optional[float] = None
        _card_open = 0
        _card_trades = 0
        _card_exposure: Optional[float] = None
        _card_dd: Optional[float] = None
        _card_dd_src: Optional[str] = None
        sep = "─" * 16

        if mode_str == "LIVE":
            # ── LIVE MODE: show real exchange data ──
            # Truthful equity: None means the live balance is unreadable —
            # render "unavailable", never the paper baseline.
            display_equity, _eq_source = await self.engine.resolve_display_equity(user_id)
            _eq_str = (f"${display_equity:,.2f}" if display_equity is not None
                       else "unavailable")

            executor = self.engine.live_executor
            live_open = executor.open_positions
            all_closed = executor.closed_positions

            # Exclude adopted orphan trades and injected diagnostic artifacts
            # so Portfolio matches Performance numbers
            from bot.utils.trade_filter import NON_TRADE_CLOSE_REASONS as _NON_TRADE_REASONS
            live_closed = [t for t in all_closed
                           if not any(getattr(t, "trade_id", "").startswith(p) for p in _ORPHAN_PREFIXES)
                           and getattr(t, "close_reason", "") not in _NON_TRADE_REASONS]
            adopted_trades = [t for t in all_closed
                              if any(getattr(t, "trade_id", "").startswith(p) for p in _ORPHAN_PREFIXES)]

            # Calculate live PnL from closed positions (net of fees).
            #
            # THE LINE THE COMMENT BELOW WAS WRITTEN FOR. Five lines down, the
            # UNREALIZED total was rewritten to count what marked and say so
            # when the count was short, under the sentence "a partial sum
            # presented as a whole one is a wrong number wearing a measured
            # number's authority". This line was left as
            #
            #     sum((p.pnl_usd or 0) for p in live_closed)
            #
            # which is that exact defect on the REALIZED total — the bigger of
            # the two claims, because it is the money already gone.
            #
            # `pnl_usd` is None by design, not by accident: live_executor's
            # loader preserves a JSON null verbatim, under its own comment
            # recording that `float(x or 0)` reading it back as 0.0 "silently
            # converted 'we could not price this' into 'this broke even'".
            #
            # This function already KNOWS: `_win_stats` + `_unpriced_tag` below
            # print the unpriced count on the Session line. So the card
            # disclosed the unpriced closes for the W/L counts and folded the
            # same rows into Net as break-even, on one line, in one message.
            from bot.formatters.realized_totals import realized_totals
            _rt = realized_totals(live_closed)
            _pnl_known = _rt["net"] is not None
            _fees_known = _rt["fees"] is not None
            _unpriced_closes = _rt["unpriced"]
            # Kept as floats for the existing format strings; the display
            # branches below gate on the *_known flags, so a None total can
            # never reach a `:+,.2f`.
            live_total_pnl = _rt["net"] if _pnl_known else 0.0
            live_total_fees = _rt["fees"] if _fees_known else 0.0
            live_total_gross = _rt["gross"] if _rt["gross"] is not None else 0.0
            # Unrealized P&L for open positions -- the SAME rule /open_positions
            # now enforces, because this surface was left with the disease
            # after that one was cured. Two bugs sat in the old two lines:
            #
            #   `and lp.pnl_usd` is a falsiness test, so a position marked at
            #   exactly break-even was skipped alongside one that was never
            #   marked at all; and an unmarked position then contributed 0 to
            #   the sum, so the survivors were rendered as THE unrealized
            #   total with a colour on it.
            #
            # A partial sum presented as a whole one is a wrong number wearing
            # a measured number's authority. Count what marked, and say so
            # when the count is short.
            #
            # Scope, stated rather than implied: this separates "no mark" from
            # "a mark", not "fresh" from "stale". A position carrying an old
            # pnl_usd still counts here. Detecting staleness needs a mark
            # timestamp the executor does not currently carry, and claiming
            # more than that would be the defect one level up.
            live_unrealized = 0.0
            _marked = 0
            for lp in live_open:
                _u = getattr(lp, "pnl_usd", None)
                if _u is None:
                    continue
                live_unrealized += float(_u)
                _marked += 1
            _unmarked = len(live_open) - _marked

            # Live exposure
            live_exposure = sum(lp.cost_usd for lp in live_open)

            # Count filled vs pending for display
            _filled_count = sum(1 for lp in live_open if lp.status != "pending_fill")
            _pending_count = sum(1 for lp in live_open if lp.status == "pending_fill")
            _pos_display = f"{_filled_count}"
            if _pending_count > 0:
                _pos_display += f" + {_pending_count} pending"

            # The realized line, built the way the unrealized one below is:
            # three outcomes, and the shortfall said out loud when it is
            # partial. Colour is a claim — a green accent asserts "in profit"
            # as loudly as the number, and `>= 0` would have painted the
            # manufactured 0.00 green, which is the reading that costs most.
            if not _pnl_known:
                _net_pnl_line = (f"- {t('lbl_net_pnl', lang)}: "
                                 f"<code>{t('pnl_unknown', lang)}</code> ⚠️")
            else:
                _net_pnl_line = (f"- {t('lbl_net_pnl', lang)}: "
                                 f"<code>${live_total_pnl:+,.2f}</code> "
                                 f"{'🟢' if live_total_pnl >= 0 else '🔴'}")
                if _unpriced_closes:
                    _net_pnl_line += (f" ⚠️ <i>"
                                      f"{t('total_partial', lang, n=_unpriced_closes)}</i>")

            lines = [
                f"\U0001f4bc <b>{t('portfolio_title', lang)}</b> (LIVE)",
                sep,
                "",
                f"- {t('lbl_equity', lang)}: <code>{_eq_str}</code>",
                f"- {t('lbl_open_positions', lang)}: <code>{_pos_display}</code>",
                f"- {t('lbl_exposure', lang)}: <code>${live_exposure:,.2f}</code>",
                _net_pnl_line,
                (f"- {t('lbl_fees_paid', lang)}: <code>${live_total_fees:,.2f}</code>"
                 if _fees_known else
                 f"- {t('lbl_fees_paid', lang)}: "
                 f"<code>{t('pnl_unknown', lang)}</code> ⚠️"),
            ]
            if live_open and _marked == 0:
                # Every mark missing. A "$0.00" here, or the old silent
                # omission, both read as "nothing is riding on the book".
                lines.append(
                    f"- {t('lbl_unrealized_pnl', lang)}: "
                    f"<code>{t('pnl_unknown', lang)}</code> \u26a0\ufe0f")
            elif _marked:
                _u_line = (f"- {t('lbl_unrealized_pnl', lang)}: "
                           f"<code>${live_unrealized:+,.2f}</code> "
                           f"{'🟢' if live_unrealized >= 0 else '🔴'}")
                if _unmarked:
                    _u_line += (f" \u26a0\ufe0f <i>"
                                f"{t('total_partial', lang, n=_unmarked)}</i>")
                lines.append(_u_line)

            # Open positions from LiveExecutor
            # Separate filled positions from pending limit orders
            filled_positions = [lp for lp in live_open if lp.status != "pending_fill"]
            pending_limits = [lp for lp in live_open if lp.status == "pending_fill"]

            if filled_positions:
                lines.extend(["", sep, "", f"<b>{t('hdr_open_positions', lang)}</b>"])
                for lp in filled_positions:
                    d_icon = "🟢" if lp.direction == "LONG" else "🔴"
                    lev_str = f" {lp.leverage}x" if (lp.leverage or 1) > 1 else ""
                    lines.append(
                        f"\n{d_icon} <b>{lp.symbol}</b> {lp.direction}{lev_str}"
                    )
                    lines.append(f"  {t('entry', lang)}: <code>${lp.entry_price:,.6f}</code>")
                    lines.append(f"  {t('lbl_size', lang)}: <code>${lp.cost_usd:,.2f}</code>")
                    if lp.stop_loss:
                        lines.append(f"  {t('lbl_sl', lang)}: <code>${lp.stop_loss:,.6f}</code> | {t('lbl_tp', lang)}: <code>${lp.take_profit:,.6f}</code>")

            if pending_limits:
                lines.extend(["", sep, "", f"⏳ <b>{t('hdr_pending_limits', lang)}</b>"])
                for lp in pending_limits:
                    d_icon = "🟢" if lp.direction == "LONG" else "🔴"
                    lev_str = f" {lp.leverage}x" if (lp.leverage or 1) > 1 else ""
                    pair = lp.symbol.replace("/", "").replace(":USDT", "")
                    # Calculate time since placed
                    if lp.opened_at:
                        from datetime import datetime, timezone
                        age_secs = (datetime.now(timezone.utc) - lp.opened_at).total_seconds()
                        if age_secs < 3600:
                            age_str = f"{age_secs / 60:.0f}m ago"
                        else:
                            age_str = f"{age_secs / 3600:.1f}h ago"
                    else:
                        age_str = "unknown"
                    lines.append(
                        f"\n{d_icon} <b>{pair}</b> {lp.direction}{lev_str} — LIMIT"
                    )
                    lines.append(f"  {t('lbl_limit', lang)}: <code>${lp.entry_price:,.6f}</code> | {t('lbl_placed', lang)}: {age_str}")
                    if lp.stop_loss:
                        lines.append(f"  {t('lbl_sl', lang)}: <code>${lp.stop_loss:,.6f}</code> | {t('lbl_tp', lang)}: <code>${lp.take_profit:,.6f}</code>")

            # Recent closed trades from LiveExecutor
            if live_closed:
                recent = live_closed[-5:]
                lines.extend(["", sep, "", f"<b>{t('hdr_recent_trades_net', lang)}</b>"])
                # `tr`, not `t`: `t` is the module-level i18n function, and a
                # statement-level `for t in ...` makes Python treat t as LOCAL
                # for the whole function — so every t('key', lang) call above
                # raises UnboundLocalError before this loop is ever reached.
                for tr in recent:
                    # Per-row, the same rule as the total. `tr.pnl_usd or 0`
                    # with `>= 0` rendered an unpriced close as
                    # "✅ BTCUSDT LONG → $+0.00" — a tick and a measured
                    # break-even for a trade nobody could price. ⚪ and an em
                    # dash say the one true thing instead.
                    _p = getattr(tr, "pnl_usd", None)
                    fee_val = tr.commission or 0
                    pair = tr.symbol.replace("/", "").replace(":USDT", "")
                    fee_note = f" (fee ${fee_val:.2f})" if fee_val > 0 else ""
                    if _p is None:
                        lines.append(f"  ⚪ {pair} {tr.direction} → <code>—</code>{fee_note}")
                    else:
                        pnl_val = float(_p)
                        pnl_icon = "✅" if pnl_val >= 0 else "❌"
                        lines.append(f"  {pnl_icon} {pair} {tr.direction} → <code>${pnl_val:+,.2f}</code>{fee_note}")

            # Session tally from LiveExecutor
            if live_closed:
                # `losses = len(...) - wins` is the same defect in its
                # second form: a close nobody could price was displayed as an
                # L. Losses are now the scored non-wins.
                _ws = _win_stats(live_closed)
                wins = _ws["wins"]
                losses = _ws["scored"] - wins
                # `else 0` printed "0%" when NOTHING was scorable. A 0% win
                # rate is a claim that everything lost — the public daily post
                # says so in its own comment and renders 'n/a'; this line, on
                # the surface the operator actually reads, still printed the
                # confident negative.
                _rate = _ws["rate"]
                _wr_str = "n/a" if _rate is None else f"{_rate * 100:.0f}%"
                _net_str = (f"${live_total_pnl:+,.2f}" if _pnl_known
                            else "unreadable")
                lines.extend([
                    "", sep, "",
                    f"<b>{t('lbl_session', lang)}</b> {wins}W/{losses}L"
                    + _unpriced_tag(_ws) + " | "
                    + f"{t('lbl_net', lang)}: <code>{_net_str}</code> | "
                    + f"{t('lbl_win_rate_lc', lang)}: <code>{_wr_str}</code>",
                ])
                if adopted_trades:
                    # THE ONE LINE ON THIS CARD THAT WAS NOT CONVERTED. 180
                    # lines above, the realized total, the fees, the per-row
                    # P&L, the W/L split and the win rate were all rewritten
                    # around realized_totals()/win_stats() with explicit
                    # unknown states. This parenthetical kept `or 0`, so
                    # "($+0.00)" read as "excluding them changed nothing" —
                    # which is exactly the reassuring reading, on the rows the
                    # bot did not open and knows least about.
                    _ad = realized_totals(adopted_trades)
                    _ad_str = ("P&L not recorded" if _ad["net"] is None
                               else f"${_ad['net']:+,.2f}")
                    lines.append(
                        f"<i>⚠️ Excluded {len(adopted_trades)} adopted orphans ({_ad_str})</i>")
            else:
                lines.extend(["", f"<i>{t('portfolio_no_live_trades', lang)}</i>"])

            # Card values from the readers this branch already built. `_rt`
            # and `_lws` are the same objects the text lines print, so the
            # picture and the text cannot disagree about the same book.
            _lws = _win_stats(live_closed)
            _card_pnl = _rt["net"]
            _card_wr = _lws["rate"]
            _card_open = len(live_open)
            _card_trades = len(live_closed)
            _card_exposure = ((live_exposure / display_equity * 100.0)
                              if display_equity else None)
            from bot.formatters.drawdown_card import enforced_drawdown as _ed
            try:
                _card_dd, _card_dd_src, _ = _ed(self.engine.risk.drawdown_status())
            except Exception:
                _card_dd, _card_dd_src = None, None

        else:
            # ── PAPER MODE: show paper portfolio data ──
            display_equity = state.equity_usd
            lines = [
                f"\U0001f4bc <b>{t('portfolio_title', lang)}</b> (PAPER)",
                sep,
                "",
                f"- {t('lbl_equity', lang)}: <code>${display_equity:,.2f}</code>",
                f"- {t('lbl_cash', lang)}: <code>${state.balance_usd:,.2f}</code>",
                f"- {t('lbl_open_positions', lang)}: <code>{state.open_positions}</code>",
                f"- {t('lbl_daily_pnl', lang)}: <code>{'+' if state.daily_pnl >= 0 else '-'}${abs(state.daily_pnl):.2f}</code> {'🟢' if state.daily_pnl >= 0 else '🔴'}",
                f"- {t('lbl_drawdown', lang)}: <code>{state.max_drawdown_pct:.2f}%</code>",
            ]

            if positions:
                lines.extend(["", sep, "", f"<b>{t('hdr_open_positions', lang)}</b>"])
                for pos in positions:
                    d_icon = "🟢" if pos.direction.value == "LONG" else "🔴"
                    # No entry-price fallback: an unpriced position rendered as
                    # exactly 0.00% beside a green circle and a "→ $entry"
                    # current price — three separate claims built from a mark we
                    # never read.
                    _mark = portfolio._last_prices.get(pos.asset)
                    _priced = _mark is not None and _mark > 0
                    last = _mark if _priced else pos.entry_price
                    size_usd = pos.quantity * pos.entry_price
                    if _priced:
                        if pos.direction.value == "LONG":
                            pnl_pct = ((last - pos.entry_price) / pos.entry_price) * 100
                        else:
                            pnl_pct = ((pos.entry_price - last) / pos.entry_price) * 100
                        pnl_usd = size_usd * pnl_pct / 100
                        pnl_icon = "🟢" if pnl_pct >= 0 else "🔴"
                        arrow = "▲" if pnl_pct > 0 else "▼" if pnl_pct < 0 else "◇"
                        _pnl_line = f"{pnl_icon} {'+' if pnl_pct >= 0 else ''}{pnl_pct:.2f}%"
                    else:
                        pnl_pct = None
                        pnl_usd = None
                        pnl_icon = "⚪"          # not green, not red — unknown
                        arrow = "◇"
                        _pnl_line = "price unavailable"
                    lines.append(
                        f"\n{pnl_icon}{arrow} <b>{pos.asset}</b> {pos.direction.value} | "
                        f"{_pnl_line}"
                    )
                    _cur_txt = (f"<code>${last:,.4f}</code>" if _priced else "—")
                    lines.append(f"  {t('entry', lang)}: <code>${pos.entry_price:,.4f}</code> → {t('lbl_current', lang)}: {_cur_txt}")
                    lines.append(f"  {t('lbl_sl', lang)}: <code>${pos.stop_loss:,.4f}</code> | {t('lbl_tp', lang)}: <code>${pos.take_profit:,.4f}</code>")
                    _pnl_usd_txt = ("—" if pnl_usd is None else f"<code>${pnl_usd:+,.2f}</code>")
                    lines.append(f"  {t('lbl_size', lang)}: <code>${size_usd:,.2f}</code> | {t('lbl_pnl', lang)}: {_pnl_usd_txt}")

            if history:
                lines.extend(["", sep, "", f"<b>{t('hdr_recent_trades', lang)}</b>"])
                for tr in history[-5:]:
                    pnl_icon = "✅" if tr.pnl > 0 else "❌"
                    lines.append(f"  {pnl_icon} {tr.asset} {tr.direction.value} → <code>${tr.pnl:+.2f}</code>")

            # Session tally
            if state.total_trades > 0:
                # `state.total_trades - wins` is the defect the LIVE branch of
                # this same card was cured of, left standing on the paper one:
                # the remainder after wins is losses PLUS every break-even and
                # every close nobody could price, all displayed as an L. Same
                # reader as the live branch (bot/utils/win_rate.py), so the two
                # halves of one card cannot disagree.
                _ws = _win_stats(history)
                wins = _ws["wins"]
                losses = _ws["scored"] - wins
                lines.extend([
                    "", sep, "",
                    f"<b>{t('lbl_session', lang)}</b> {wins}W/{losses}L"
                    + _unpriced_tag(_ws) + " | "
                    f"{t('lbl_net', lang)}: <code>${state.total_pnl:+.2f}</code> | "
                    f"{t('lbl_win_rate_lc', lang)}: <code>{state.win_rate:.0%}</code>",
                ])
            else:
                lines.extend(["", f"<i>{t('portfolio_no_trades', lang)}</i>"])

            # On the paper book the snapshot IS the measurement, so these are
            # real — except the win rate, which PortfolioState computes as
            # `... if total > 0 else 0.0`. A fresh account has no rate, and
            # "0%" on a card reads as a measured record of total failure.
            _pws = _win_stats(history)
            _card_pnl = state.total_pnl
            _card_wr = _pws["rate"]
            _card_open = state.open_positions
            _card_trades = state.total_trades
            _card_exposure = state.portfolio_exposure_pct
            _card_dd = state.max_drawdown_pct
            _card_dd_src = "paper" if _card_dd is not None else None

        # Visual stats card (guarded — any error falls back to the text above).
        try:
            from bot.formatters.drawdown_card import drawdown_tile as _dd_tile
            from bot.formatters.signal_card import render_stats_card
            # Tri-state tiles. `drawdown_tile` is the seam the /risk PNG
            # already uses — an unread drawdown is "--" in gray, and a MEASURED
            # 0.0 keeps its green, because a flat book is a real reading and
            # the commonest state the bot is ever in.
            _dd_val, _dd_col = _dd_tile(_card_dd)
            _dd_lbl = t("lbl_max_drawdown", lang) + (
                f" ({_card_dd_src})" if _card_dd_src else "")
            _png = render_stats_card({
                "title": t("portfolio_card_title", lang),
                "subtitle": f"{mode_str} · {datetime.now(UTC).strftime('%H:%M')} UTC",
                "hero": {"label": t("lbl_equity", lang),
                         "value": (f"${display_equity:,.2f}" if display_equity is not None
                                   else "unavailable"),
                         "color": "white"},
                "tiles": [
                    {"label": t("lbl_realized_pnl", lang),
                     "value": ("--" if _card_pnl is None
                               else f"${_card_pnl:+,.2f}"),
                     "color": ("gray" if _card_pnl is None
                               else "green" if _card_pnl >= 0 else "red")},
                    {"label": t("lbl_win_rate", lang),
                     "value": "--" if _card_wr is None else f"{_card_wr:.0%}",
                     "color": "gray" if _card_wr is None else "cyan"},
                    {"label": t("lbl_open_positions", lang),
                     "value": str(_card_open), "color": "white"},
                    {"label": t("lbl_total_trades", lang),
                     "value": str(_card_trades), "color": "white"},
                    {"label": t("lbl_exposure", lang),
                     "value": ("--" if _card_exposure is None
                               else f"{_card_exposure:.0f}%"),
                     "color": "gray" if _card_exposure is None else "yellow"},
                    {"label": _dd_lbl, "value": _dd_val, "color": _dd_col},
                ],
            })
            if _png and await self._send_photo(update, _png, f"\U0001f4ca <b>{t('portfolio_card_title', lang)}</b>"):
                return
        except Exception as exc:
            system_log.debug("portfolio card render failed: %s", exc)

        await self._send(update, "\n".join(lines))

    @guard("risk")
    async def _cmd_risk(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        user_id = self._get_tg_id(update)
        lang = self._lang(update)  # i18n: resolve once for this command
        portfolio = self.engine.user_portfolios.get(user_id)
        state = portfolio.snapshot()
        # LIVE FIX: use real open position count (per-user: the caller's own).
        if CONFIG.is_live() and hasattr(self.engine, 'live_executor'):
            _risk_ex = self._caller_executor(update)
            open_count = len(_risk_ex.open_positions) if _risk_ex else 0
        else:
            open_count = state.open_positions
        # Source every number from the control that ENFORCES it. This card
        # previously reported `state.max_drawdown_pct` as "current drawdown" —
        # the PAPER portfolio's monotonic worst-EVER, which never recovers and
        # never moves in pure-live operation — and the renderer then measured
        # it against the DAILY-LOSS cap to produce the HEALTHY/WARNING verdict,
        # the health score and the drawdown gauge. Two different controls, so
        # /risk could read HEALTHY with the drawdown breaker about to trip.
        # NOT `state.max_drawdown_pct` as the fallback. That is the PAPER
        # snapshot, and substituting it when the enforced reading cannot be had
        # re-creates precisely what `drawdown_status()`'s own comment records:
        # "an operator could read ~0% from a gate that was refusing trades at
        # 9%". The seed is None, so a failed read stays a failed read and the
        # renderer says UNKNOWN instead of scoring the card from a number that
        # describes a different book.
        _dd_now = None
        _dd_limit = CONFIG.risk.max_drawdown_pct
        try:
            _st = self.engine.risk.drawdown_status() or {}
            if _st.get("drawdown_pct") is not None:
                _dd_now = round(float(_st["drawdown_pct"]), 2)
            if _st.get("effective_limit_pct"):
                _dd_limit = float(_st["effective_limit_pct"])
        except Exception:
            pass
        # In LIVE mode two independent caps bound the position count — the risk
        # engine's and the executor's — so the BINDING one is the lower. Showing
        # only the higher would promise room the other refuses.
        _max_trades = CONFIG.risk.max_open_positions
        if CONFIG.is_live():
            try:
                _max_trades = min(_max_trades,
                                  int(CONFIG.execution.max_live_open_positions))
            except Exception:
                pass
        data = {
            "daily_loss_limit": CONFIG.risk.max_daily_loss_pct,
            "drawdown_limit": _dd_limit,
            "current_drawdown": _dd_now,
            "max_open_trades": _max_trades,
            "open_trades": open_count,
            "leverage_cap": CONFIG.exchange.default_leverage,
            # WHY trades are being rejected, or "" — so this card cannot score
            # a halted engine as HEALTHY. Without it the text renderer knew
            # only the drawdown reading, and a restart-erased high-water mark
            # made that read 0.0% while the breaker was open.
            # WHY entries are being refused, or "" — from the full gate, not
            # the shared engine's breaker field alone. The text renderer scored
            # a HALTED engine as HEALTHY when the cause was the caller's own
            # breaker or the venue auth halt, neither of which appears in
            # self.engine.risk.trading_blocked_by.
            "trading_blocked_by": "; ".join(
                entry_gate(self.engine, str(user_id or ""))["reasons"]),
        }
        rendered = wr_risk(data)
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton(t("btn_safe_mode", lang), callback_data="risk_safe_mode"),
             InlineKeyboardButton(t("btn_pause", lang), callback_data="risk_pause")],
            [InlineKeyboardButton(t("btn_stop_bot", lang), callback_data="risk_emergency_stop")],
        ])
        # Visual stats card (guarded — falls back to text + same keyboard).
        try:
            from bot.formatters.signal_card import render_stats_card
            # Same widening as /status, and now from the same helper: this
            # tile read OK while the warning-rate breaker was rejecting
            # trades, because that breaker is not part of
            # circuit_breaker_active — and it kept reading OK for the two
            # gates discovered after that fix.
            cb = bool(data["trading_blocked_by"])
            dd = data["current_drawdown"]
            from bot.formatters.drawdown_card import drawdown_tile
            _dd_txt, _dd_col = drawdown_tile(dd)
            _png = render_stats_card({
                "title": t("lbl_risk_title", lang),
                "subtitle": f"{datetime.now(UTC).strftime('%H:%M')} UTC",
                "tiles": [
                    {"label": t("lbl_daily_loss_limit", lang), "value": f"{data['daily_loss_limit']:.1f}%", "color": "yellow"},
                    {"label": t("lbl_current_drawdown", lang), "value": _dd_txt,
                     "color": _dd_col},
                    {"label": t("lbl_open_trades", lang), "value": f"{data['open_trades']}/{data['max_open_trades']}", "color": "white"},
                    {"label": t("lbl_leverage_cap", lang), "value": f"{data['leverage_cap']}x", "color": "cyan"},
                    {"label": t("lbl_circuit_breaker", lang), "value": t("val_tripped", lang) if cb else t("val_ok", lang),
                     "color": "red" if cb else "green"},
                ],
            })
            if _png and await self._send_photo(update, _png, f"\U0001f6e1️ <b>{t('lbl_risk_title', lang)}</b>", reply_markup=kb):
                return
        except Exception as exc:
            system_log.debug("risk card render failed: %s", exc)
        await self._send(update, rendered["text"], reply_markup=kb)

    @guard("enforcing")
    async def _cmd_enforcing(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """Which controls would refuse a bad trade right now.

        `/risk` shows the drawdown backstop; `/guardian` shows the guardian
        suite. Neither shows the SET, and the set is the question worth asking
        before real money: 27 flags live in RiskLimits and no surface listed
        them together, so "what is enforcing?" was an investigation rather than
        a glance.

        Read fresh on every call, never cached. A cached enforcement posture is
        a claim about the past presented as the present, and the whole card is
        an answer to "right now".
        """
        from bot.formatters.gate_card import render_gate_card
        from bot.guardian.gate_inventory import inventory, refusal_summary
        try:
            rows = inventory(getattr(CONFIG, "risk", None))
            text = render_gate_card(rows, refusal_summary(rows))
        except Exception as exc:
            # Never swallow into an empty card. A heading with nothing under it
            # reads as "nothing to report", which on THIS screen means "nothing
            # is wrong" — the third thing the guard/omit table warns about.
            system_log.debug("gate card render failed: %s", exc)
            text = ("🛡 <b>Enforcement inventory</b>\n\n"
                    "⚪ Could not read the control configuration, so the "
                    "posture is unknown. This is NOT the same as 'no controls "
                    "are active' — it means nobody looked successfully.")
        await self._send(update, text)

    @guard("rejected")
    async def _cmd_rejected(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        result = await self.registry.dispatch("rejected_trades", self.engine, user_id=self._get_tg_id(update))
        await self._send(update, result)

    @guard("costs")
    async def _cmd_costs(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        result = await self.registry.dispatch("costs", self.engine, user_id=self._get_tg_id(update))
        await self._send(update, result)

    @guard("portfolio")
    async def _cmd_performance(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """Performance summary — per-user."""
        user_id = self._get_tg_id(update)

        # LIVE mode: use real trade data from executor + exchange fallback
        if CONFIG.is_live() and hasattr(self.engine, 'live_executor'):
            executor = self.engine.live_executor
            live_closed = executor.closed_positions

            # ── Exchange trade history fallback ──
            # If local closed_trades is empty, try to fetch recent trades
            # from the exchange to capture trades closed outside the bot
            if not live_closed:
                try:
                    exchange = await executor._get_exchange()
                    # Fetch recent closed orders across major pairs
                    import time as _time
                    since_ms = int((_time.time() - 7 * 86400) * 1000)  # last 7 days
                    ex_trades = await exchange.fetch_my_trades(symbol=None, since=since_ms, limit=50)
                    if ex_trades:
                        from bot.core.live_executor import LivePosition
                        # Group trades by order to reconstruct PnL
                        _trade_pnl_map: dict[str, float] = {}
                        _trade_sym_map: dict[str, str] = {}
                        for t in ex_trades:
                            oid = t.get("order", t.get("id", "unknown"))
                            info = t.get("info", {})
                            pnl = float(info.get("profit", 0) or 0)
                            _trade_pnl_map[oid] = _trade_pnl_map.get(oid, 0) + pnl
                            _trade_sym_map[oid] = t.get("symbol", "UNKNOWN")
                        # Create synthetic LivePosition entries for display
                        for oid, pnl in _trade_pnl_map.items():
                            if pnl == 0:
                                continue  # skip zero-PnL (likely open leg)
                            sym = _trade_sym_map.get(oid, "UNKNOWN")
                            lp = LivePosition(
                                trade_id=f"EX-{oid}",
                                symbol=sym,
                                side="long",
                                entry_price=0,
                                qty=0,
                                cost_usd=0,
                                leverage=1,
                                sl_price=None,
                                tp_price=None,
                            )
                            lp.status = "closed"
                            lp.pnl_usd = pnl
                            live_closed.append(lp)
                        if live_closed:
                            audit(system_log, f"Performance: loaded {len(live_closed)} trades from exchange history",
                                  action="perf_exchange_fallback", result="OK")
                except Exception as exc:
                    audit(system_log, f"Performance exchange fallback error: {exc}",
                          action="perf_exchange_fallback", result="ERROR")

            # ── Separate adopted/injected vs user-initiated trades ──
            # Exclude: TI-adopted (orphan positions), TI-injected (diagnostic artifacts),
            # canceled/expired/price_drift (never-filled limit orders with $0 PnL)
            from bot.utils.trade_filter import NON_TRADE_CLOSE_REASONS as _NON_TRADE_REASONS_PERF
            user_trades = [t for t in live_closed
                           if not any(getattr(t, "trade_id", "").startswith(p) for p in _ORPHAN_PREFIXES)
                           and getattr(t, "close_reason", "") not in _NON_TRADE_REASONS_PERF]
            adopted_trades = [t for t in live_closed
                              if any(getattr(t, "trade_id", "").startswith(p) for p in _ORPHAN_PREFIXES)]
            # Third copy of the same parenthetical (see /balance and
            # /portfolio). The win rate six lines below was carefully made to
            # pass None through; this total beside it was not.
            from bot.formatters.realized_totals import realized_totals
            adopted_pnl = realized_totals(adopted_trades)["net"]

            total_trades = len(user_trades)
            _ws = _win_stats(user_trades)
            # None travels. `... if rate is not None else 0` converted "nothing
            # could be scored" into "everything lost" one layer ABOVE the
            # renderer, so no amount of care in render_performance could
            # recover it — the card was handed a measured-looking 0.0 and had
            # no way to know. win_stats returns None for a reason; pass it on.
            win_rate = (_ws["rate"] * 100) if _ws["rate"] is not None else None
            _tot = realized_totals(user_trades)
            # `if ... is not None else 0.0` and then a `_total_known` flag that
            # NOTHING READ — the one occurrence of that name in this file. The
            # fold happened first and the flag recorded it happening, so a book
            # where nothing could be priced published `All-time $+0.00` in
            # green. `realized_totals` returns None precisely so it cannot, and
            # `render_performance` already draws None as an em dash with a
            # neutral arrow: the renderer was waiting and the caller filled the
            # hole before it got there.
            total_pnl = _tot["net"]

            # ── Date-filtered PnL ──
            from datetime import datetime as _dt, timedelta as _td
            from bot.compat import UTC as _UTC
            _now = _dt.now(_UTC)
            _today_start = _now.replace(hour=0, minute=0, second=0, microsecond=0)
            _week_start = _today_start - _td(days=7)

            today_pnl = 0.0
            week_pnl = 0.0
            trades_today = 0
            _today_priced = _today_unpriced = 0
            _week_priced = _week_unpriced = 0
            for t in user_trades:
                closed_at = getattr(t, "closed_at", None)
                if closed_at:
                    if isinstance(closed_at, str):
                        try:
                            closed_at = _dt.fromisoformat(closed_at)
                        except (ValueError, TypeError):
                            closed_at = None
                    if closed_at is not None:
                        # Ensure timezone-aware
                        if closed_at.tzinfo is None:
                            closed_at = closed_at.replace(tzinfo=_UTC)
                        # An unpriced close contributes NOTHING and is
                        # counted as unpriced, rather than contributing 0 and
                        # being counted as a measurement. `or 0` made an
                        # all-unpriced day print "$+0.00" in green (>= 0 picks
                        # green for the manufactured zero) and, worse, fed the
                        # `today_pnl == 0 and week_pnl == 0` fallback below,
                        # which then silently re-labels the ALL-TIME total as
                        # this week's.
                        _p = getattr(t, "pnl_usd", None)
                        if closed_at >= _today_start:
                            trades_today += 1
                            if _p is None:
                                _today_unpriced += 1
                            else:
                                today_pnl += float(_p)
                                _today_priced += 1
                        if closed_at >= _week_start:
                            if _p is None:
                                _week_unpriced += 1
                            else:
                                week_pnl += float(_p)
                                _week_priced += 1
                        continue
                # Fallback: if no closed_at, count in total only
            # If no date info at all, fall back to total for both
            # Guard the fallback on "nothing was PRICED", not on the sum being
            # zero: a genuinely flat week and a week nobody could price both
            # gave 0.0, and only the second should borrow the all-time figure.
            if (_today_priced == 0 and _week_priced == 0
                    and _today_unpriced == 0 and _week_unpriced == 0
                    and total_pnl):
                week_pnl = total_pnl
                trades_today = total_trades
            # A window whose closes could not be priced is not a flat window.
            # These accumulate only PRICED closes, so 0.0 with nothing priced
            # is the sum of an empty set — `$+0.00 today` in green over a day
            # of unreadable closes.
            if _today_priced == 0 and _today_unpriced > 0:
                today_pnl = None
            if _week_priced == 0 and _week_unpriced > 0:
                week_pnl = None

            best_pair = "N/A"
            worst_pair = "N/A"
            # A SORT KEY IS USUALLY NOT A CLAIM — but here the ORDER ITSELF is
            # published, as "Best 🏆" and "Worst". `t.pnl_usd or 0` maps every
            # unpriced close to 0.0, so on a book of losses the row nobody
            # could price sorts HIGHEST and gets crowned best. Rank only what
            # was actually priced; if nothing was, the honest answer is the
            # "N/A" both already default to.
            from bot.formatters.realized_totals import best_and_worst
            _best_t, _worst_t = best_and_worst(user_trades)
            if _best_t is not None:
                worst_pair = _worst_t.symbol.replace("/USDT", "").replace(":USDT", "")
                best_pair = _best_t.symbol.replace("/USDT", "").replace(":USDT", "")
            data = {
                "today_pnl": None if today_pnl is None else round(today_pnl, 2),
                "week_pnl": None if week_pnl is None else round(week_pnl, 2),
                "total_pnl": None if total_pnl is None else round(total_pnl, 2),
                "today_unpriced": _today_unpriced,
                "week_unpriced": _week_unpriced,
                "win_rate": win_rate,
                # How many closes the rate actually covers. The card shows
                # "Win Rate" and "Trades" as neighbouring tiles, so without
                # this the two together imply a denominator the rate never
                # used.
                "win_rate_scored": _ws["scored"],
                "win_rate_unscored": _ws["unscored"],
                "trades_today": trades_today,
                "total_trades": total_trades,
                "best_pair": best_pair,
                "worst_pair": worst_pair,
                "adopted_count": len(adopted_trades),
                "adopted_pnl": round(adopted_pnl, 2),
            }
        else:
            portfolio = self.engine.user_portfolios.get(user_id)
            state = portfolio.snapshot()
            trades = portfolio.trade_history
            today_trades = len(trades)
            # RC-2026-010. `sum(1 for t in trades if t.pnl > 0)` raises on a
            # close carrying no recorded P&L, and `... else 0` mapped "no
            # trades" onto a MEASURED 0% win rate -- a claim that everything
            # lost. `_win_stats` (module-level, line 684) is the helper the
            # live branch already uses and answers None when nothing could be
            # scored. NOT re-imported here: a function-local import binds the
            # name for the WHOLE function, which made the live branch's call
            # above an unbound local.
            _ws = _win_stats(trades)
            win_rate = (_ws["rate"] * 100) if _ws["rate"] is not None else None
            best_pair = None
            worst_pair = None
            _scored = [t for t in trades if getattr(t, "pnl", None) is not None]
            if _scored:
                sorted_t = sorted(_scored, key=lambda t: t.pnl)
                worst_pair = sorted_t[0].asset.replace("/USDT", "")
                best_pair = sorted_t[-1].asset.replace("/USDT", "")
            data = {
                "today_pnl": (round(state.daily_pnl, 2)
                              if getattr(state, "daily_pnl", None) is not None else None),
                # RC-2026-009. This was the literal `0.0`. Nothing computes a
                # week for paper, and the tile painted `week_pnl >= 0` GREEN --
                # so a figure that was never measured was published in the
                # colour that claims a profitable week.
                "week_pnl": None,
                "win_rate": win_rate,
                "win_rate_scored": _ws["scored"],
                "win_rate_unscored": _ws["unscored"],
                "trades_today": today_trades,
                "best_pair": best_pair,
                "worst_pair": worst_pair,
            }

        rendered = wr_performance(data)
        # Visual stats card (guarded — falls back to the text readout).
        try:
            from bot.formatters.performance_card import performance_card_payload
            from bot.formatters.signal_card import render_stats_card
            # The tiles were built inline here, which is why three defects sat
            # in them that no test could reach: a hardcoded week in green, the
            # hero publishing TODAY'S figure under a "Total PnL" label whenever
            # total_pnl was absent, and `f"{None:.0f}%"` raising on the honest
            # unscored rate so the `except` below deleted the entire card.
            # bot/formatters/performance_card.py is the seam.
            _png = render_stats_card(performance_card_payload(
                data, subtitle=f"{datetime.now(UTC).strftime('%H:%M')} UTC"))
            if _png and await self._send_photo(update, _png, "\U0001f4c8 <b>PERFORMANCE</b>"):
                return
        except Exception as exc:
            system_log.debug("performance card render failed: %s", exc)
        await self._send(update, rendered["text"])

    @guard("journal")
    async def _cmd_daily_report(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """Daily trading report."""
        user_id = self._get_tg_id(update)

        # LIVE mode: use real trade data from executor
        if CONFIG.is_live() and hasattr(self.engine, 'live_executor'):
            executor = self.engine.live_executor
            from bot.utils.trade_filter import NON_TRADE_CLOSE_REASONS as _non_trade_reasons_daily
            closed = [t for t in executor.closed_positions
                       if not any(getattr(t, "trade_id", "").startswith(p)
                                  for p in _ORPHAN_PREFIXES)
                       and getattr(t, "close_reason", "") not in _non_trade_reasons_daily]
            today_trades = len(closed)
            _ws = _win_stats(closed)
            wins = _ws["wins"]
            losses = _ws["scored"] - wins
            # `sum((t.pnl_usd or 0) for t in closed)` was a PARTIAL TOTAL
            # PRINTED AS WHOLE: every close the record could not price added
            # 0.00 and the result went out as the day's net. Reachable in
            # production now that an unpriced close persists a null rather
            # than a fabricated break-even. `pnl_stats` sums only what it
            # could read and says how much of the set that was.
            _ps = _pnl_stats(closed)
            net_pnl = _ps["total"]                       # None when unpriceable
            unscored = _ps["unscored"]
            best_trade = "N/A"
            best_pnl = None
            worst_trade = "N/A"
            worst_pnl = None
            # Rank over the SCORABLE closes only. The old sort key resolved an
            # unreadable P&L to 0, so an unpriced close could be named the
            # day's best or worst trade — at $0.00, which is also the value
            # that would make it neither.
            # Carry the figure ALONGSIDE the row rather than re-reading it in
            # the sort key: the filter guarantees it is a float, and pairing
            # is what lets the reader (and the type checker) see that.
            _scored = [(p, t) for t in closed
                       if (p := _trade_pnl(t)) is not None]
            if _scored:
                _scored.sort(key=lambda pt: pt[0])
                worst_pnl = round(_scored[0][0], 2)
                worst_trade = _scored[0][1].symbol.replace("/USDT", "").replace(":USDT", "")
                best_pnl = round(_scored[-1][0], 2)
                best_trade = _scored[-1][1].symbol.replace("/USDT", "").replace(":USDT", "")

            # `dd = 0.0` and `risk_status = "Healthy"` were HARDCODED here —
            # not a reading that failed, a verdict with no reading behind it,
            # on the LIVE branch, printed under a shield icon. The paper
            # branch below actually computes both.
            #
            # `drawdown_status()` is documented "best-effort; returns empty on
            # any error", so {} is the unreadable case and must not collapse
            # into the calmest verdict. It reports the drawdown the breaker
            # ACTUALLY gates on (live high-water mark in live mode) plus the
            # limit in force, so the bands come off the real gate instead of
            # the two constants the paper branch carries.
            from bot.formatters.drawdown_card import live_risk_status
            try:
                _dd_st = self.engine.risk.drawdown_status()
            except Exception:
                _dd_st = {}
            dd, risk_status = live_risk_status(_dd_st)
        else:
            portfolio = self.engine.user_portfolios.get(user_id)
            trades = portfolio.trade_history
            today_trades = len(trades)
            # The PAPER twin of the live branch above, and it carried the same
            # two defects in a different shape: `t.pnl > 0` raises rather than
            # mis-scores when pnl is None, and `today_trades - wins` shows an
            # unpriced close as an L. Found by the source test written for the
            # live sites -- a grep for `pnl_usd` never reached this one.
            _ws = _win_stats(trades)
            wins = _ws["wins"]
            losses = _ws["scored"] - wins
            net_pnl = sum(t.pnl for t in trades)
            best_trade = "N/A"
            best_pnl = 0.0
            worst_trade = "N/A"
            worst_pnl = 0.0
            if trades:
                sorted_t = sorted(trades, key=lambda t: t.pnl)
                worst_trade = sorted_t[0].asset.replace("/USDT", "").replace(":USDT", "")
                worst_pnl = round(sorted_t[0].pnl, 2)
                best_trade = sorted_t[-1].asset.replace("/USDT", "").replace(":USDT", "")
                best_pnl = round(sorted_t[-1].pnl, 2)

            state = portfolio.snapshot()
            dd = state.max_drawdown_pct if state.max_drawdown_pct else 0
            risk_status = "Healthy" if dd < 2.0 else "Warning" if dd < 3.0 else "Critical"
            # Paper trades are priced atomically on close (see the note in
            # CLAUDE.md on `Trade.model_copy`), so there is no unscorable row
            # to disclose here. Set explicitly so the renderer never has to
            # infer which branch built the dict.
            unscored = 0

        data = {
            "trades": today_trades, "wins": wins, "losses": losses,
            "net_pnl": None if net_pnl is None else round(net_pnl, 2),
            "best_trade": best_trade, "best_pnl": best_pnl,
            "worst_trade": worst_trade, "worst_pnl": worst_pnl,
            "risk_status": risk_status, "drawdown_pct": dd,
            "unscored": unscored,
        }
        rendered = wr_daily_report(data)
        await self._send(update, rendered["text"])

        # Forward daily report to marketing channels. §4: those groups are
        # PUBLIC, so percent / ratio / count only — Net PnL, Best and Worst
        # were all dollar amounts. The private reply above (rendered["text"])
        # keeps them; this copy names the symbols and drops the figures.
        #
        # Two other things were wrong in the same six lines:
        #
        #   * `wins / today_trades` counted every close in the denominator,
        #     while `losses` came from `_ws["scored"]`. An unpriced close was
        #     therefore excluded from the W/L pair but included in the rate —
        #     the two numbers on one line disagreed about the same day.
        #     win_rate.py exists to settle that; use its rate.
        #   * a day with zero closes posted "W/L: 0/0 | Win Rate: 0%". A 0%
        #     win rate is a claim that everything lost. Nothing traded is a
        #     different statement, and it does not need a post at all.
        try:
            if today_trades > 0:
                _rate = _ws.get("rate")
                _lines = [
                    f"Trades: <code>{today_trades}</code> | "
                    f"W/L: <code>{wins}/{losses}</code> | "
                    f"Win Rate: <code>"
                    f"{'n/a' if _rate is None else format(_rate * 100, '.0f') + '%'}"
                    f"</code>"
                ]
                if _ws.get("unscored"):
                    _lines.append(
                        f"<i>Rate covers {_ws.get('scored', 0)} of "
                        f"{today_trades} closes — {_ws['unscored']} carry no "
                        f"recorded P&amp;L and are scored neither way.</i>")
                if best_trade != "N/A":
                    _lines.append(f"Best: <code>{html.escape(best_trade)}</code> | "
                                  f"Worst: <code>{html.escape(worst_trade)}</code>")
                _lines.append(f"Risk: {html.escape(str(risk_status))}")
                await self.forwarder.post_daily_report("\n".join(_lines))
        except Exception:
            pass

    @guard("scan")
    async def _cmd_signals(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """Show per-pair signal stats using SignalTracker."""
        text = self.signal_tracker.format_for_telegram()
        await self._send(update, text)
