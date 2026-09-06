"""The market-context command group — a slice out of the handler.

`/macro` (and `/macro brief`), `/eventrisk`, `/news`, `/funding`,
`/fundingscan`, `/arb`, `/rwa`, plus the operator's `/compliance` and
`/readiness`, and the two helpers `/news` shares with the free-text
intercept: the digest renderer and the held-symbol read. Read-only cards
over macro, funding and news data; nothing here places an order. Their
behaviour is covered where it always was (`test_news_radar`,
`test_news_radar_honesty`, `test_macro_cards_are_reachable`,
`test_telegram_web_parity`); `tests/test_handler_mixins.py` holds this class
to the split's rules.

A mixin, not a leaf: every method dispatches through `self.registry` or
reads `self.engine`, and answers through `self._send`. `_format_rwa` stays
on the handler beside its three sibling formatters (the portfolio group
reads them); it is declared below as a host staticmethod.
"""
from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import ContextTypes

from bot.skills.command_guard import guard
from bot.utils.logger import system_log

if TYPE_CHECKING:
    from bot.core.engine import RuneClawEngine
    from bot.skills.skill_registry import SkillRegistry


class MarketCommands:
    """Macro, funding and news cards. Host contract below; methods after."""

    #: The news radar, built on first use by `_news_digest_text` — the
    #: mixin's own state, declared so the type checker knows it exists.
    _news_radar: object

    if TYPE_CHECKING:
        # Provided by TelegramHandler, and ONLY declared here — declarations,
        # never bodies; tests/test_handler_mixins.py checks every name against
        # what the handler really defines.
        engine: RuneClawEngine
        registry: SkillRegistry
        _WEB_LINK_HINT: str

        async def _send(self, update: Update, text: str,
                        reply_markup=None, edit: bool = False) -> None: ...

        async def _guard(self, update: Update, command: str = "", ctx=None) -> bool: ...

        def _is_admin(self, update: Update) -> bool: ...

        @staticmethod
        def _format_rwa(data: dict) -> str: ...

    @guard("rwa")
    async def _cmd_rwa(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """/rwa — the tokenized-RWA sector radar (live venue tickers)."""
        import asyncio as _aio
        from bot.utils.web_data_pull import fetch_rwa
        data = await _aio.to_thread(fetch_rwa)
        if not data or "sector" not in data:
            await self._send(update, self._WEB_LINK_HINT)
            return
        await self._send(update, self._format_rwa(data))

    async def _cmd_funding(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """/funding [SYMBOL] — live funding rates for a perp across every
        connected venue (Bitget home rate + Bybit + Hyperliquid), with the
        cross-venue spread. Positive funding = longs pay shorts = crowded
        longs. Default symbol: BTC."""
        from bot.core.cross_venue import CROSS_VENUE, base_of

        args = ctx.args or []
        raw = (args[0].strip().upper() if args else "BTC")
        base = base_of(raw)
        deriv = f"{base}/USDT:USDT"

        rates: dict[str, float] = {}
        # Home venue (Bitget market data) — per-symbol fetch, best-effort.
        try:
            fut_ex = await self.engine.scanner._get_futures_exchange()
            fr = await fut_ex.fetch_funding_rate(deriv)
            home = fr.get("fundingRate") if isinstance(fr, dict) else None
            if home is not None:
                rates["bitget"] = float(home)
        except Exception:
            pass
        # Cross-venue map (bulk-cached, keyless).
        try:
            rates.update(await CROSS_VENUE.rates_for(base))
        except Exception:
            pass

        if not rates:
            await self._send(update,
                             f"📡 No funding data found for <b>{base}</b> on "
                             "any connected venue — check the symbol.")
            return

        lines = [f"📡 <b>{base} funding across venues</b>",
                 "(8h rate · annualized · positive = longs pay)"]
        for venue, r in sorted(rates.items(), key=lambda kv: kv[1], reverse=True):
            ann = r * 3 * 365 * 100  # 8h rate -> annualized %
            crowd = "🔴 longs crowded" if r >= 0.0005 else \
                    "🟢 shorts crowded" if r <= -0.0005 else "⚪ balanced"
            lines.append(f"• <b>{venue}</b>: {r * 100:+.4f}% "
                         f"(≈{ann:+.1f}%/yr) {crowd}")
        div = CROSS_VENUE.divergence(rates)
        if div is not None:
            lines.append("")
            lines.append(f"Spread across {div['venues']} venues: "
                         f"<b>{div['spread'] * 100:.4f}%</b>")
            if div["spread"] >= 0.0005:
                lines.append("⚠️ Wide divergence — positioning is venue-"
                             "concentrated; expect funding-driven flows.")
        await self._send(update, "\n".join(lines))

    async def _cmd_arb(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """/arb — the funding-arb paper tracker: what a fixed $1k delta-
        neutral pair WOULD have earned on the recorded cross-venue spreads,
        with the fee reality check. 100% paper — the evidence that gates
        whether a real capture strategy is worth building."""
        if not await self._guard(update, "status"):
            return
        await self._send(update, "⏳ Crunching the paper-arb history…")
        try:
            from bot.core.arb_tracker import (compute_paper_carry,
                                              format_arb_html,
                                              load_snapshots)
            from bot.core.funding_radar import build_comparison
            snaps = await asyncio.to_thread(load_snapshots)
            carries = compute_paper_carry(snaps)
            current = []
            try:
                current = await asyncio.to_thread(
                    build_comparison, ["BTC", "ETH", "SOL", "XRP", "DOGE"])
            except Exception:
                pass
            await self._send(update, format_arb_html(carries, current))
        except Exception as exc:
            system_log.warning("/arb failed: %s", exc)
            await self._send(update, "🔴 Paper-arb report failed — see logs.")

    async def _cmd_fundingscan(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """/fundingscan [SYMBOLS…] — annualized funding across Bitget, Bybit
        and Hyperliquid for MANY coins at once, widest spread first, with the
        delta-neutral direction. Complements /funding (the single-symbol deep
        view via bot.core.cross_venue). Read-only public data; the measurement
        layer for the funding-arb roadmap item. Defaults to the open
        positions' coins plus the majors."""
        if not await self._guard(update, "status"):
            return
        await self._send(update, "⏳ Comparing funding across venues…")
        try:
            from bot.core.funding_radar import (build_comparison,
                                                format_funding_html)
            bases: list[str] = []
            for a in (ctx.args or []):
                b = a.upper().replace("/USDT", "").replace("USDT", "").strip(":/")
                if b and b not in bases:
                    bases.append(b)
            if not bases:
                # Positions first — carry cost is most actionable there.
                try:
                    ex = getattr(self.engine, "live_executor", None)
                    for p in getattr(ex, "open_positions", []) or []:
                        b = str(getattr(p, "symbol", "")).split("/")[0].upper()
                        if b and b not in bases:
                            bases.append(b)
                except Exception:
                    pass
                for b in ("BTC", "ETH", "SOL", "XRP", "DOGE", "BNB", "AVAX", "LINK"):
                    if b not in bases:
                        bases.append(b)
            rows = await asyncio.to_thread(build_comparison, bases[:12])
            await self._send(update, format_funding_html(rows))
        except Exception as exc:
            system_log.warning("/funding failed: %s", exc)
            await self._send(update,
                "🔴 Funding comparison failed — venues unreachable?")

    def _held_symbols(self) -> list:
        """Base symbols the operator currently holds (paper + live), de-duped.
        Best-effort — a source that isn't present is simply skipped."""
        syms: list = []
        seen: set = set()

        def _add(s):
            s = (s or "").strip()
            if s and s not in seen:
                seen.add(s)
                syms.append(s)

        try:
            for p in getattr(getattr(self.engine, "portfolio", None), "open_positions", []) or []:
                _add(getattr(p, "symbol", None) or getattr(p, "asset", None))
        except Exception:
            pass
        try:
            le = getattr(self.engine, "live_executor", None)
            for p in (getattr(le, "open_positions", []) if le else []) or []:
                _add(getattr(p, "symbol", None))
        except Exception:
            pass
        return syms

    async def _news_digest_text(self) -> str:
        """Shared news-radar reply used by BOTH the /news command and the
        free-text "news" intercept (web + Telegram): the off-state notice when
        disabled, otherwise a freshly-refreshed headline digest with high-impact
        alerts on held positions. Advisory only; never moves or blocks a trade."""
        import time as _t

        from bot.core.news import NewsRadar, render_news_digest
        if not NewsRadar.enabled():
            return (
                "📰 <b>News radar is off.</b>\n"
                "It's on by default (CoinDesk / Cointelegraph / Decrypt RSS — no "
                "API key), but an operator has turned it off with "
                "<code>NEWS_RADAR_ENABLED=0</code>. When on, it gives high-impact "
                "alerts on your open positions.\n\n"
                "<i>Advisory only — news never moves or blocks a trade.</i>")
        radar = getattr(self, "_news_radar", None)
        if radar is None:
            radar = NewsRadar()
            self._news_radar = radar
        held = self._held_symbols()
        watch = held or ["BTC/USDT", "ETH/USDT", "SOL/USDT", "XRP/USDT"]
        _refresh_failed = False
        try:
            await radar.refresh(symbols=watch)
        except Exception as exc:
            # Swallowing this into a debug log is what let a total feed
            # outage render as "No headlines yet". The digest is entitled to
            # know the difference between a quiet tape and an unread one.
            _refresh_failed = True
            system_log.debug("news refresh failed: %s", exc)
        now = _t.time()
        return render_news_digest(
            radar.recent(8), radar.standdown(held, now) if held else [], now,
            refresh_failed=_refresh_failed)

    @guard("status")
    async def _cmd_news(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """NEWS-1b: /news — public-RSS headline radar with high-impact alerts on
        the positions you hold. Advisory only; never moves or blocks a trade."""
        try:
            await update.effective_chat.send_chat_action(ChatAction.TYPING)
        except Exception:
            pass
        await self._send(update, await self._news_digest_text())

    @guard("macro")
    async def _cmd_macro(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """/macro — the calendar; /macro brief — the macro gate's posture.

        Two cards, one command, on purpose. `macro_brief` advertised `/macro`
        while the calendar already answered to it, and two commands under one
        name is what kept the brief dark. It answers a different question —
        the gate's risk state, the size multiplier on new entries, whether the
        calendar is stale or blind — so it is a sub-mode of the same command
        rather than a second name for the same subject. Same permission,
        because it is the same read-only macro data: `macro`, like /eventrisk.
        """
        args = [str(a).lower() for a in (ctx.args or [])]
        if args and args[0] == "brief":
            result = await self.registry.dispatch("macro_brief", self.engine)
        else:
            result = await self.registry.dispatch("macro_calendar", self.engine)
        await self._send(update, result)

    @guard("macro")
    async def _cmd_eventrisk(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """Macro-event risk for one symbol.

        `check_event_risk` was a REGISTERED SKILL NO TRANSPORT DISPATCHED — it
        advertised `/eventrisk` in its own class body and no handler existed,
        so the string was documentation of a command that did not run. Being
        unrunnable is why nobody noticed that every one of macro_skills.py's
        attribute probes named fields the real objects do not have; those were
        fixed in #213 against tests, which left the module correct-if-wired.

        `@guard("macro")` rather than a new permission: this is the same
        read-only macro data /macro already serves, scoped to a symbol, and
        `macro` is a permission trader and paper already hold. Inventing
        `eventrisk` here would repeat the exposure/networth/research/rwa
        mistake recorded in ROLE_PERMISSIONS — a permission string no role
        holds makes a "user" command admin-only in fact.
        """
        args = ctx.args or []
        if not args:
            await self._send(update, "Usage: <code>/eventrisk BTC</code>")
            return
        result = await self.registry.dispatch(
            "check_event_risk", self.engine, symbol=str(args[0]))
        await self._send(update, result)

    @guard("compliance")
    async def _cmd_compliance(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """Restricted jurisdictions and the consent ledger. OPERATOR-ONLY.

        Deliberately not a trader permission. The card summarises the GLOBAL
        consent ledger — up to 5,000 authorization decisions across every user,
        with trade ids and the locks each one failed. No subject id is
        rendered, but a stream of other people's grant/deny outcomes is still
        operator information, and "read-only" is not the same as "shared".

        `compliance` is therefore held by no role but admin (which holds "*").
        That is the exposure/networth shape on purpose rather than by accident,
        and tests/test_command_audience_matches_permission.py is what keeps the
        catalogue honest about it: the entry is filed under an "admin" group,
        and that test fails if the permission ever becomes reachable by a
        normal role without the documentation moving too.
        """
        result = await self.registry.dispatch("compliance_status", self.engine)
        await self._send(update, result)
