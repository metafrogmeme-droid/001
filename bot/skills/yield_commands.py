"""The yield and staking command group — the fifth slice out of the handler.

`/yield`, `/idleyield`, `/stake`, `/unstake`, the locked-staking plan step
and the helpers they share: the Bitget v3 client factory, the None-aware
free-margin read, and — since the money doors opened to linked callers —
the one reading of WHOSE account a stake or a redeem acts on. The two
radars (`/yield`, `/idleyield`) stay the operator's, admin-only: they read
the operator's book. The three money doors act on the account the caller
linked with /connect, or on the operator's for an admin who linked none;
`bot.core.earn_account` is that decision, made once, and the confirm
buttons in `callback_handler` ask it again at press time. The money paths
behind them are `bot.core.yield_radar` and `bot.core.idle_yield`, which is
where their tests live (`test_yield_card_says_when_the_margin_was_unread`,
`test_idle_yield_partial_report`, `test_web_staking_fixed`,
`test_telegram_commands`,
`test_a_linked_trader_stakes_their_own_account`).
`tests/test_handler_mixins.py` holds this class to the split's rules.

A mixin, not a leaf, for the reason the Guardian group gives: each method
reads `self.engine`, gates through `self._guard` or `self._is_admin` and
answers through `self._send`. `_engine_free_usdt` is the seam three of those
tests drive — "we do not know" is None, paper mode is 0.0, and the two are
never confused — and it stays a method so a bare host can bind it.
"""
from __future__ import annotations

import asyncio
import html
from typing import TYPE_CHECKING, Optional

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from bot.config import CONFIG
from bot.core.earn_account import (
    EARN_TAG_MISMATCH,
    EarnAccount,
    earn_account_line,
    earn_refusal,
    resolve_earn_account,
)
from bot.skills.command_guard import guard
from bot.utils.logger import system_log

if TYPE_CHECKING:
    from bot.core.engine import RuneClawEngine
    from bot.utils.user_store import UserStore


class YieldCommands:
    """The idle-yield radars and the Earn money doors. Host contract below; methods after."""

    if TYPE_CHECKING:
        # Provided by TelegramHandler, and ONLY declared here — declarations,
        # never bodies; tests/test_handler_mixins.py checks every name against
        # what the handler really defines.
        engine: RuneClawEngine
        users: UserStore

        async def _send(self, update: Update, text: str,
                        reply_markup=None, edit: bool = False) -> None: ...

        def _is_admin(self, update: Update) -> bool: ...

        def _get_tg_id(self, update: Update) -> str: ...

        def _lang(self, update: Update) -> str: ...

    async def _cmd_yield(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """/yield — READ-ONLY idle-asset yield radar (admin).

        Scans the operator account's idle balances (free futures margin +
        available spot coins), pulls Bitget Earn's current savings catalog,
        and reports what the idle money could earn on the best FLEXIBLE
        products (instantly redeemable, so margin stays recallable). Places
        no orders, subscribes to nothing — the money doors are /stake and
        /unstake, which act on the CALLER's linked account."""
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
        """Signed OPERATOR Bitget client for Earn calls, or None if no keys.

        The radars read through it directly; the money doors reach it only
        through `resolve_earn_account`, on the one branch where the operator's
        account is the right answer (an admin who linked no account of their
        own)."""
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

    # ── whose account a stake or a redeem acts on ──────────────────────

    def _earn_account(self, update: Update) -> EarnAccount:
        """The account THIS caller's Earn action is over — `earn_account`'s
        eight states. The revoke is read here rather than in the leaf because
        the store that holds it is the handler's; a store that cannot answer
        the question is `unavailable`, never "not revoked"."""
        tg_id = self._get_tg_id(update)
        try:
            revoked = bool(self.users.live_trading_revoked(tg_id))
        except Exception as exc:
            return EarnAccount("unavailable", owner=tg_id, fault=type(exc).__name__)
        return resolve_earn_account(tg_id, is_admin=self._is_admin(update),
                                    operator_client=self._yield_client,
                                    revoked=revoked)

    async def _free_usdt_for(self, acct: EarnAccount) -> Optional[float]:
        """Free futures margin OF THE ACCOUNT THE PLAN IS OVER.

        The operator's comes from the engine's age-gated cache, unchanged. A
        caller's is read fresh from THEIR executor — the same read
        /livebalance shows them — and is None whenever it could not be read,
        which `build_report` prints as a partial report rather than a
        complete one. Never the operator's number for a caller: a resolver
        that fell back to the operator's book answers None, not that book.
        """
        if acct.state == "operator":
            return self._engine_free_usdt()
        if acct.state != "caller":
            return None
        try:
            from bot.formatters.live_balance import read_balance
            ex = self.engine.balance_view_executor(acct.owner)
            if ex is None or ex is getattr(self.engine, "live_executor", None):
                return None
            bal = await ex.fetch_balance()
            return read_balance(bal).free
        except Exception as exc:
            system_log.warning("Earn: caller free margin unread for %s: %s",
                               acct.owner, type(exc).__name__)
            return None

    async def _earn_button_account(self, update: Update, tag: str) -> Optional[EarnAccount]:
        """The account a tapped Earn button may act on, or None after telling
        the presser why not.

        Three checks, in order. The presser holds `stake` — the buttons never
        run the @guard, so the role gate is asked here, the same question the
        command asked and the same answer. The presser resolves to a usable
        account. And that account is the one the plan was built over, by the
        tag the button carries: a plan over the operator's book tapped by a
        linked trader would otherwise execute against the trader's account
        with the operator's numbers, and a plan over one caller's book tapped
        by another would move the second caller's funds.
        """
        tg_id = self._get_tg_id(update)
        denial = self.users.permission_denial(tg_id, "stake")
        if denial:
            from bot.formatters.onboarding import permission_denied_notice
            role = (self.users.get(tg_id) or {}).get("role", "pending")
            await self._send(update, permission_denied_notice(
                "stake", role, denial, lang=self._lang(update)), edit=True)
            return None
        acct = self._earn_account(update)
        if not acct.usable:
            await self._send(update, earn_refusal(acct), edit=True)
            return None
        if not tag or tag != acct.tag:
            await self._send(update, EARN_TAG_MISMATCH, edit=True)
            return None
        return acct

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

    @guard("stake")
    async def _cmd_stake(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """/stake — put idle stables into flexible Bitget Earn, on the account
        the caller linked with /connect (an admin who linked none: the
        operator's). /stake fixed [COIN] — fixed-term LOCK options
        (double-confirm).

        Two-step by design: this command only SHOWS the plan; money moves
        exclusively on the explicit confirm button, and even then the amount
        is recomputed and re-clamped from live balances at press time — the
        button carries the coin and the account's owner tag, never a number.
        Flexible products redeem instantly; fixed terms LOCK funds until the
        term ends and therefore require a second confirmation that shows the
        lock END date (SPOT-2 hard line). The margin reserve always stays
        free. The role gate is `stake`, held by trader and admin: a
        self-admitted paper user is refused before any account is resolved."""
        acct = self._earn_account(update)
        if not acct.usable:
            await self._send(update, earn_refusal(acct))
            return
        args = [a.lower() for a in (ctx.args or [])]
        if args and args[0] == "fixed":
            await self._stake_fixed_plan(
                update, args[1].upper() if len(args) > 1 else "", acct)
            return
        await self._send(update, "⏳ Computing the stake plan…")
        try:
            from bot.core.yield_radar import (
                MARGIN_RESERVE_PCT, MIN_IDLE_USD, STAKEABLE_COINS, build_report)
            free = await self._free_usdt_for(acct)
            report = await asyncio.to_thread(build_report, acct.client, free)
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
                    "margin reserve, or no flexible Earn product available.\n"
                    + earn_account_line(acct) + _why)
                return
            lines = ["⚡ <b>Stake plan — flexible Earn, instantly redeemable</b>\n"
                     + earn_account_line(acct)]
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
                    callback_data=f"yld:s:{r.coin}:{acct.tag}")])
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

    async def _stake_fixed_plan(self, update: Update, coin_filter: str,
                                acct: EarnAccount) -> None:
        """/stake fixed — step 1 of the LOCKED-staking double-confirm.

        Lists every live fixed-term option per stakeable coin with its lock
        duration and projected unlock date, over the account `/stake`
        resolved. Choosing one does NOT move money: it opens the final-confirm
        screen (step 2) which re-shows the lock END date; only that second
        press executes."""
        await self._send(update, "⏳ Fetching fixed-term lock options…")
        try:
            from bot.core.yield_radar import (
                MIN_IDLE_USD, STAKEABLE_COINS, build_report, lock_end_date)
            free = await self._free_usdt_for(acct)
            report = await asyncio.to_thread(build_report, acct.client, free)
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
                    + ".\n" + earn_account_line(acct) + "\nFlexible staking: /stake")
                return
            lines = ["🔒 <b>Fixed-term Earn — funds LOCK until the term ends</b>\n"
                     + earn_account_line(acct)]
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
                                       f"{t_['days']}:{acct.tag}"))])
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

    @guard("stake")
    async def _cmd_unstake(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """/unstake — redeem flexible Earn holdings back to trading margin, on
        the account the caller linked (an admin who linked none: the
        operator's). Button-confirmed; the holdings read is three-valued, so
        a venue that did not answer is never shown as an empty book."""
        acct = self._earn_account(update)
        if not acct.usable:
            await self._send(update, earn_refusal(acct))
            return
        await self._send(update, "⏳ Loading Earn holdings…")
        try:
            from bot.core.yield_radar import fetch_savings_assets
            holdings = await asyncio.to_thread(fetch_savings_assets, acct.client)
            if holdings is None:
                # NOT "nothing to redeem": the venue did not answer these keys,
                # and a card that says the account holds nothing would be a
                # confident negative about the caller's own money.
                await self._send(update,
                    "🔴 Your Earn holdings could not be read — Bitget did not "
                    "answer, so this is unknown, not empty. Nothing was "
                    "redeemed; try again in a moment.\n" + earn_account_line(acct))
                return
            if not holdings:
                await self._send(update,
                    "🟡 No flexible Earn holdings found — nothing to redeem.\n"
                    + earn_account_line(acct))
                return
            lines = ["🏦 <b>Flexible Earn holdings</b>\n" + earn_account_line(acct)]
            buttons = []
            for h in holdings:
                apy = f" @ {h['apy']:.2f}%" if h.get("apy") else ""
                lines.append(f"<b>{h['coin']}</b>: <code>{h['amount']:g}</code>{apy}")
                buttons.append([InlineKeyboardButton(
                    f"↩️ Redeem {h['amount']:g} {h['coin']} → margin",
                    callback_data=f"yld:r:{h['product_id']}:{acct.tag}")])
            buttons.append([InlineKeyboardButton("❌ Cancel", callback_data="yld:x")])
            lines.append("<i>Redeems in full; stables are moved back to "
                         "futures margin automatically.</i>")
            await self._send(update, "\n\n".join(lines),
                             reply_markup=InlineKeyboardMarkup(buttons))
        except Exception as exc:
            system_log.warning("/unstake failed: %s", exc)
            await self._send(update,
                "🔴 Could not load Earn holdings — nothing was moved.")
