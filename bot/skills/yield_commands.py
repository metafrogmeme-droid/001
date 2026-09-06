"""The yield and staking command group — the fifth slice out of the handler.

`/yield`, `/idleyield`, `/stake`, `/unstake`, the locked-staking plan step
and the two helpers they share: the Bitget v3 client factory and the
None-aware free-margin read. Operator commands that move idle margin into
Earn products and back; the money paths behind them are `bot.core.yield_radar`
and `bot.core.idle_yield`, which is where their tests live
(`test_yield_card_says_when_the_margin_was_unread`,
`test_idle_yield_partial_report`, `test_web_staking_fixed`,
`test_telegram_commands`). `tests/test_handler_mixins.py` holds this class
to the split's rules.

A mixin, not a leaf, for the reason the Guardian group gives: each method
reads `self.engine`, gates on `self._is_admin` and answers through
`self._send`. `_engine_free_usdt` is the seam three of those tests drive —
"we do not know" is None, paper mode is 0.0, and the two are never confused
— and it stays a method so a bare host can bind it.
"""
from __future__ import annotations

import asyncio
import html
from typing import TYPE_CHECKING, Optional

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from bot.config import CONFIG
from bot.utils.logger import system_log

if TYPE_CHECKING:
    from bot.core.engine import RuneClawEngine


class YieldCommands:
    """The operator's idle-yield commands. Host contract below; methods after."""

    if TYPE_CHECKING:
        # Provided by TelegramHandler, and ONLY declared here — declarations,
        # never bodies; tests/test_handler_mixins.py checks every name against
        # what the handler really defines.
        engine: RuneClawEngine

        async def _send(self, update: Update, text: str,
                        reply_markup=None, edit: bool = False) -> None: ...

        def _is_admin(self, update: Update) -> bool: ...

    async def _cmd_yield(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """/yield — READ-ONLY idle-asset yield radar (admin).

        Scans the operator account's idle balances (free futures margin +
        available spot coins), pulls Bitget Earn's current savings catalog,
        and reports what the idle money could earn on the best FLEXIBLE
        products (instantly redeemable, so margin stays recallable). Places
        no orders, subscribes to nothing — the auto-staking phase ships
        separately behind an explicit admin confirmation."""
        if not self._is_admin(update):
            await self._send(update,
                "🔒 /yield reads the operator account — admin only.")
            return
        await self._send(update, "⏳ Scanning idle assets and Earn rates…")
        try:
            from bot.core.bitget_v3_client import BitgetV3Client
            from bot.core.yield_radar import build_report, format_report_html

            client = BitgetV3Client.from_config()
            if not client.has_credentials:
                await self._send(update,
                    "🔴 No operator Bitget keys configured — "
                    "<code>/setexchange</code> first.")
                return
            # Through the helper that already tells 0.0 (paper: nothing to
            # read) from None (live: could not read). This used to coerce the
            # cache's None to 0.0 itself, so build_report took the "nothing
            # idle on futures" path and the card presented spot-only idle
            # capital as the whole picture -- the same defect the web yield
            # panel had, one surface over.
            free_usdt = self._engine_free_usdt()
            report = await asyncio.to_thread(build_report, client,
                                             futures_free_usdt=free_usdt)
            # Cross-venue info: when Bybit Earn pays more on a coin, say so
            # (info only — /stake still executes where the funds are).
            try:
                from bot.core.yield_radar import (annotate_cross_venue,
                                                  fetch_bybit_savings_catalog)
                bybit_cat = await asyncio.to_thread(fetch_bybit_savings_catalog)
                if bybit_cat:
                    annotate_cross_venue(report, {"Bybit": bybit_cat})
            except Exception:
                pass
            await self._send(update, format_report_html(report))
        except Exception as exc:
            system_log.warning("/yield failed: %s", exc)
            await self._send(update,
                "🔴 Yield radar failed — check the logs. The account was "
                "not touched (the radar is read-only).")

    def _yield_client(self):
        """Signed operator Bitget client for Earn calls, or None if no keys."""
        from bot.core.bitget_v3_client import BitgetV3Client
        client = BitgetV3Client.from_config()
        return client if client.has_credentials else None

    def _engine_free_usdt(self) -> Optional[float]:
        """Free futures margin from the engine's venue-aware balance cache.

        Three outcomes, and the middle one used to be indistinguishable from
        the first:

          0.0   PAPER mode — there is no live futures margin, and a report
                that omits the row is complete and correct.
          None  LIVE mode with an empty or unreadable cache — we do not know.
                The Earn report then drops what is usually the LARGEST idle
                row and would otherwise present the remainder as the whole
                picture of the operator's idle capital.
          float the real number.
        """
        try:
            if not CONFIG.is_live():
                return 0.0
            # Age-gated: a stale cache is the SAME "we do not know" as an
            # empty one, and returning its old number here would present a
            # dead venue connection as a live margin figure.
            cache = self.engine.live_balance_cached()
            if not cache:
                return None
            free = cache.get("free")
            return None if free is None else float(free or 0)
        except Exception:
            return None

    async def _cmd_idleyield(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """/idleyield — cross-SOURCE best-rate scan for idle assets (admin only).

        Where /yield matches idle balances to ONE venue's Earn catalog, this
        matches them to the best rate across sources — CEX Earn (custodial) AND
        on-chain Lido/Aave (non-custodial, live from DefiLlama) — and prefers a
        marginally-lower non-custodial rate so you keep custody, stating the
        tradeoff. Read-only: it recommends, it never moves a cent (the money
        path stays the confirm-gated /stake)."""
        if not self._is_admin(update):
            await self._send(update,
                "🔒 /idleyield reads the operator account — admin only.")
            return
        await self._send(update, "⏳ Scanning idle assets across CEX + on-chain rates…")
        try:
            from bot.core.bitget_v3_client import BitgetV3Client
            from bot.core.yield_radar import (build_report, fetch_savings_catalog,
                                              fetch_bybit_savings_catalog)
            from bot.core.idle_yield_feeds import build_idle_options
            from bot.core import idle_yield as iy

            client = BitgetV3Client.from_config()
            if not client.has_credentials:
                await self._send(update,
                    "🔴 No operator Bitget keys — <code>/setexchange</code> first.")
                return
            # Reuse the radar's idle discovery (it values free margin + spot).
            report = await asyncio.to_thread(build_report, client, self._engine_free_usdt())
            if report.error:
                await self._send(update, f"🔴 {report.error}")
                return
            _incomplete = getattr(report, "incomplete", "")
            holdings = [{"asset": r.coin, "usd_value": r.idle_usd, "location": r.source}
                        for r in report.rows if r.idle_usd > 0]
            if not holdings:
                # "No idle assets" is a claim about the whole balance, and the
                # leg we could not read is usually the largest one.
                await self._send(update,
                    "🟡 No idle assets above the dust floor right now."
                    + (f"\n\n⚠️ {html.escape(_incomplete)}" if _incomplete else ""))
                return
            # Options: Bitget Earn (custodial) + Bybit Earn + non-custodial feeds.
            bitget_cat = await asyncio.to_thread(fetch_savings_catalog, client)
            extra = {}
            try:
                bybit_cat = await asyncio.to_thread(fetch_bybit_savings_catalog)
                if bybit_cat:
                    extra["Bybit Earn"] = bybit_cat
            except Exception:
                pass
            options = await asyncio.to_thread(
                build_idle_options, bitget_cat, extra_catalogs=extra)
            result = iy.optimize(holdings, options, prefer_noncustodial=True)
            body = iy.human_readable(result)
            nc = sum(1 for o in options if not o.get("custodial"))
            await self._send(update,
                "<b>💤→💸 Idle-Yield Optimizer</b>\n"
                + (f"⚠️ <i>{html.escape(_incomplete)}</i>\n" if _incomplete else "")
                + f"<pre>{html.escape(body)}</pre>\n"
                f"<i>{nc} non-custodial rate(s) live · recommendation only — "
                f"nothing moved. /stake executes flexible CEX Earn on confirm.</i>")
        except Exception as exc:
            system_log.warning("/idleyield failed: %s", exc)
            await self._send(update,
                "🔴 Idle-yield scan failed — the account was not touched (read-only).")

    async def _cmd_stake(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """/stake — put idle stables into flexible Bitget Earn (admin only).
        /stake fixed [COIN] — fixed-term LOCK options (double-confirm).

        Two-step by design: this command only SHOWS the plan; money moves
        exclusively on the explicit confirm button, and even then the amount
        is recomputed and re-clamped from live balances at press time — the
        button carries the coin, never a number. Flexible products redeem
        instantly; fixed terms LOCK funds until the term ends and therefore
        require a second confirmation that shows the lock END date (SPOT-2
        hard line). The margin reserve always stays free."""
        if not self._is_admin(update):
            await self._send(update,
                "🔒 /stake moves operator funds — admin only.")
            return
        args = [a.lower() for a in (ctx.args or [])]
        if args and args[0] == "fixed":
            await self._stake_fixed_plan(
                update, args[1].upper() if len(args) > 1 else "")
            return
        await self._send(update, "⏳ Computing the stake plan…")
        try:
            from bot.core.yield_radar import (
                MARGIN_RESERVE_PCT, MIN_IDLE_USD, STAKEABLE_COINS, build_report)
            client = self._yield_client()
            if client is None:
                await self._send(update,
                    "🔴 No operator Bitget keys configured — "
                    "<code>/setexchange</code> first.")
                return
            report = await asyncio.to_thread(
                build_report, client, self._engine_free_usdt())
            if report.error:
                await self._send(update, f"🔴 {html.escape(report.error)}")
                return
            # A partial report is not an error — it produced rows — but acting
            # on it as if it were the whole picture is the risk. Say it first,
            # before the plan it is missing a leg of.
            _incomplete = getattr(report, "incomplete", "")
            plans = [r for r in report.rows
                     if r.coin in STAKEABLE_COINS and r.apy_flexible
                     and r.product_id and r.stakeable_usd >= MIN_IDLE_USD]
            if not plans:
                # "Nothing stakeable" is a claim about the balance. Only make
                # it when the balance was fully read.
                _why = (f"\n\n⚠️ {html.escape(_incomplete)}" if _incomplete else "")
                await self._send(update,
                    "🟡 Nothing stakeable right now — no stable balance above "
                    f"${MIN_IDLE_USD:.0f} after the {MARGIN_RESERVE_PCT:.0%} "
                    "margin reserve, or no flexible Earn product available."
                    + _why)
                return
            lines = ["⚡ <b>Stake plan — flexible Earn, instantly redeemable</b>"]
            if _incomplete:
                lines.append(f"⚠️ <i>{html.escape(_incomplete)}</i>")
            buttons = []
            for r in plans:
                lines.append(
                    f"<b>{r.coin}</b>: stake ≈<code>${r.stakeable_usd:,.2f}</code> "
                    f"@ <code>{r.apy_flexible:.2f}%</code> APY "
                    f"(≈${r.est_year_usd:,.2f}/yr) — {r.source}")
                buttons.append([InlineKeyboardButton(
                    f"✅ Stake {r.coin} (~${r.stakeable_usd:,.0f})",
                    callback_data=f"yld:s:{r.coin}")])
            buttons.append([InlineKeyboardButton("❌ Cancel", callback_data="yld:x")])
            lines.append(
                f"<i>The exact amount is recomputed from live balances when "
                f"you press the button; the {MARGIN_RESERVE_PCT:.0%} margin "
                "reserve always stays free for the engine. Redeem any time "
                "with /unstake. Fixed-term locks (higher APY, funds locked "
                "until the term ends): <code>/stake fixed</code></i>")
            await self._send(update, "\n\n".join(lines),
                             reply_markup=InlineKeyboardMarkup(buttons))
        except Exception as exc:
            system_log.warning("/stake failed: %s", exc)
            await self._send(update,
                "🔴 Could not build the stake plan — nothing was moved.")

    async def _stake_fixed_plan(self, update: Update, coin_filter: str) -> None:
        """/stake fixed — step 1 of the LOCKED-staking double-confirm.

        Lists every live fixed-term option per stakeable coin with its lock
        duration and projected unlock date. Choosing one does NOT move money:
        it opens the final-confirm screen (step 2) which re-shows the lock
        END date; only that second press executes."""
        await self._send(update, "⏳ Fetching fixed-term lock options…")
        try:
            from bot.core.yield_radar import (
                MIN_IDLE_USD, STAKEABLE_COINS, build_report, lock_end_date)
            client = self._yield_client()
            if client is None:
                await self._send(update,
                    "🔴 No operator Bitget keys configured — "
                    "<code>/setexchange</code> first.")
                return
            report = await asyncio.to_thread(
                build_report, client, self._engine_free_usdt())
            if report.error:
                await self._send(update, f"🔴 {html.escape(report.error)}")
                return
            rows = [r for r in report.rows
                    if r.coin in STAKEABLE_COINS and r.fixed_terms
                    and r.stakeable_usd >= MIN_IDLE_USD
                    and (not coin_filter or r.coin == coin_filter)]
            if not rows:
                await self._send(update,
                    "🟡 No fixed-term lock available right now — no stable "
                    "balance above the minimum after the margin reserve, or "
                    "no fixed Earn products offered"
                    + (f" for {html.escape(coin_filter)}" if coin_filter else "")
                    + ". Flexible staking: /stake")
                return
            lines = ["🔒 <b>Fixed-term Earn — funds LOCK until the term ends</b>"]
            buttons = []
            for r in rows:
                lines.append(
                    f"<b>{r.coin}</b>: ≈<code>${r.stakeable_usd:,.2f}</code> "
                    f"stakeable after the margin reserve")
                for t_ in r.fixed_terms[:6]:
                    buttons.append([InlineKeyboardButton(
                        f"🔒 {r.coin} {t_['days']}d @ {t_['apy']:.2f}% — "
                        f"locked until {lock_end_date(t_['days'])}",
                        callback_data=(f"yldf:1:{r.coin}:{t_['product_id']}:"
                                       f"{t_['days']}"))])
            buttons.append([InlineKeyboardButton("❌ Cancel",
                                                 callback_data="yld:x")])
            lines.append(
                "<i>Step 1 of 2 — choosing a term opens a FINAL confirmation "
                "showing the exact lock END date. Locked funds are NOT "
                "redeemable, tradeable, or usable as margin until that date. "
                "Instant-redeem alternative: /stake</i>")
            await self._send(update, "\n\n".join(lines),
                             reply_markup=InlineKeyboardMarkup(buttons))
        except Exception as exc:
            system_log.warning("/stake fixed failed: %s", exc)
            await self._send(update,
                "🔴 Could not build the fixed-term plan — nothing was moved.")

    async def _cmd_unstake(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """/unstake — redeem flexible Earn holdings back to trading margin
        (admin only, button-confirmed)."""
        if not self._is_admin(update):
            await self._send(update,
                "🔒 /unstake moves operator funds — admin only.")
            return
        await self._send(update, "⏳ Loading Earn holdings…")
        try:
            from bot.core.yield_radar import fetch_savings_assets
            client = self._yield_client()
            if client is None:
                await self._send(update,
                    "🔴 No operator Bitget keys configured — "
                    "<code>/setexchange</code> first.")
                return
            holdings = await asyncio.to_thread(fetch_savings_assets, client)
            if not holdings:
                await self._send(update,
                    "🟡 No flexible Earn holdings found — nothing to redeem.")
                return
            lines = ["🏦 <b>Flexible Earn holdings</b>"]
            buttons = []
            for h in holdings:
                apy = f" @ {h['apy']:.2f}%" if h.get("apy") else ""
                lines.append(f"<b>{h['coin']}</b>: <code>{h['amount']:g}</code>{apy}")
                buttons.append([InlineKeyboardButton(
                    f"↩️ Redeem {h['amount']:g} {h['coin']} → margin",
                    callback_data=f"yld:r:{h['product_id']}")])
            buttons.append([InlineKeyboardButton("❌ Cancel", callback_data="yld:x")])
            lines.append("<i>Redeems in full; stables are moved back to "
                         "futures margin automatically.</i>")
            await self._send(update, "\n\n".join(lines),
                             reply_markup=InlineKeyboardMarkup(buttons))
        except Exception as exc:
            system_log.warning("/unstake failed: %s", exc)
            await self._send(update,
                "🔴 Could not load Earn holdings — nothing was moved.")
