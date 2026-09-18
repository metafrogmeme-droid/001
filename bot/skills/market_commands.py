"""The market-context command group — a slice out of the handler.

`/macro` (and `/macro brief`), `/eventrisk`, `/news`, `/funding`,
`/fundingscan`, `/arb`, `/rwa`, the website-card commands `/nft`, `/spot`,
`/airdrops`, `/venue_router` and `/meme_radar`, plus the operator's `/compliance` and
`/readiness`, and the two helpers `/news` shares with the free-text
intercept: the digest renderer and the held-symbol read. Read-only cards
over macro, funding and news data; nothing here places an order. Their
behaviour is covered where it always was (`test_news_radar`,
`test_news_radar_honesty`, `test_macro_cards_are_reachable`,
`test_telegram_web_parity`, `test_the_website_cards_are_telegram_commands`);
`tests/test_handler_mixins.py` holds this class to the split's rules.

A mixin, not a leaf: every method dispatches through `self.registry` or
reads `self.engine`, and answers through `self._send`. There is no RWA
formatter here any more: `_format_rwa` was a second copy of the card
`app/lib/rwa.js` renders, and it RAISED on the honest `None` that module
publishes for an unreadable 24h change — so the card is fetched RENDERED
over `/api/bot/sync/card/rwa`, like the nine other website cards.
"""
from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Optional

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

        @staticmethod
        def _link_hint(surface: str = "telegram") -> str: ...

        @staticmethod
        def _unlinked_hint(surface: str = "telegram") -> str: ...

        async def _send(self, update: Update, text: str,
                        reply_markup=None, edit: bool = False) -> None: ...

        async def _guard(self, update: Update, command: str = "", ctx=None) -> bool: ...

        def _is_admin(self, update: Update) -> bool: ...

        def _get_tg_id(self, update: Update) -> str: ...

    @guard("rwa")
    async def _cmd_rwa(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """/rwa — the tokenized-RWA sector radar (live venue tickers). The
        card is `rwa_card_text`, the seam the routed "rwa radar" renders on
        both surfaces."""
        await self._send(update, await self.rwa_card_text())

    async def rwa_card_text(self, *, surface: str = "telegram") -> str:
        """The RWA radar as text — the reading BOTH surfaces render.

        Fetched RENDERED, never re-formatted here. `_format_rwa` was a second
        copy of `app/lib/rwa.js`'s card kept in step by hand, and it had
        diverged where it costs most: its `_pct` did ``float(v)`` and
        ``.get(k, 0)`` does not fire for a key PRESENT with ``None``, which is
        exactly what the radar publishes for a 24h change the venue did not
        report. Driven, the whole card raised `TypeError` — so the honest
        `null` upstream was what crashed the reader downstream. One renderer
        now, over the card route nine other website cards already use.

        The fetch runs off the event loop (blocking urllib). ``surface`` keys
        only the sentence for a channel that did not answer: the Telegram one
        names `/link`, which a web caller cannot run.
        """
        import asyncio as _aio
        from bot.utils.web_data_pull import fetch_web_card, web_card_text
        payload = await _aio.to_thread(fetch_web_card, "rwa")
        text = web_card_text(payload)
        return text if text else self._link_hint(surface)

    # ── The website chat's own cards, as commands ─────────────────────────
    # /nft, /spot and /airdrops render the SAME card the website's chat
    # intercept answers with — fetched rendered, never re-formatted here.
    # Three of the nine reads only the website answered (`bot/nlp/web_reads`)
    # were a door notice on Telegram; these three are the read itself now.

    @guard("nft")
    async def _cmd_nft(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """/nft — top NFT collections by real 7-day volume, floor and volume
        (OpenSea, read-only). The card is `nft_card_text`, the seam the routed
        "nft radar" renders on both surfaces."""
        await self._send(update, await self.nft_card_text())

    async def nft_card_text(self, *, surface: str = "telegram") -> str:
        """The NFT radar card — the website's own rendering, both surfaces."""
        return await self._web_card_text("nft", surface=surface)

    @guard("spot")
    async def _cmd_spot(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """/spot — the spot pairs across connected venues and the spot/perp
        basis (read-only; nothing here places a spot order). The card is
        `spot_card_text`, the seam the routed "spot market" renders on both
        surfaces."""
        await self._send(update, await self.spot_card_text())

    async def spot_card_text(self, *, surface: str = "telegram") -> str:
        """The spot market card — the website's own rendering, both surfaces."""
        return await self._web_card_text("spot", surface=surface)

    @guard("airdrops")
    async def _cmd_airdrops(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """/airdrops — the airdrop and testnet radar, guided only (the card
        itself carries the anti-sybil line; nothing is farmed for anybody).
        The card is `airdrops_card_text`, the seam the routed "airdrop radar"
        renders on both surfaces."""
        await self._send(update, await self.airdrops_card_text(self._get_tg_id(update)))

    async def airdrops_card_text(self, user_id: str, *, surface: str = "telegram") -> str:
        """The airdrop radar card — the website's own rendering, both surfaces.

        ``user_id`` is the caller's Telegram id: the website adds THEIR
        wallet-readiness hints when that id is linked to a web account, and
        answers the public radar otherwise. It is never a guess about whose
        wallet to read."""
        return await self._web_card_text("airdrops", surface=surface,
                                         telegram_id=str(user_id or ""))

    async def _web_card_text(self, name: str, surface: str,
                             telegram_id: str = "", params: Optional[dict] = None,
                             unlinked: Optional[str] = None) -> str:
        """Fetch one website card off the event loop (blocking urllib) and
        hand it back as Telegram HTML. Three absences, three sentences: the
        channel not answering is `_link_hint`, a caller the website could not
        map to a web account is `_unlinked_hint`, and a card is the card —
        never one of the first two rendered as the third. ``params`` are the
        card's own arguments (`WEB_CARD_PARAMS`), a dict so the host contract
        can declare the method. ``unlinked`` replaces the wallet sentence for
        a card that is a WRITE: nothing was armed, not nothing was read."""
        import asyncio as _aio

        from bot.utils.web_data_pull import fetch_web_card, web_card_text, web_card_unlinked
        payload = await _aio.to_thread(fetch_web_card, name, telegram_id, **(params or {}))
        if web_card_unlinked(payload):
            return unlinked if unlinked is not None else self._unlinked_hint(surface)
        text = web_card_text(payload)
        if text is None:
            return self._link_hint(surface)
        return text

    @guard("venue_router")
    async def _cmd_venue_router(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE,
                                *, base: str = "") -> None:
        """/venue_router [BASE] — the cheapest venue to hold a position on, by
        funding cost, from the hourly cross-venue scan (read-only; nothing is
        routed). The card is `venue_router_card_text`, the seam the routed
        "best venue for BTC" renders on both surfaces; ``base`` is how the
        free-text branch hands the asset in."""
        args = getattr(ctx, "args", None) or []
        want = str(base or (args[0] if args else "")).strip()
        await self._send(update, await self.venue_router_card_text(want))

    async def venue_router_card_text(self, base: str = "", *, surface: str = "telegram") -> str:
        """The venue-router card — the website's own rendering, both surfaces;
        ``base`` narrows it to one asset, '' is the top five."""
        return await self._web_card_text("venue_router", surface=surface,
                                         params={"base": base or ""})

    @guard("meme_radar")
    async def _cmd_meme_radar(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """/meme_radar — the on-chain meme and AI-token snapshot with its
        safety read (DEXScreener, read-only; nothing is bought and nothing is
        launched). The card is `meme_radar_card_text`, the seam the routed
        "meme radar" renders on both surfaces."""
        await self._send(update, await self.meme_radar_card_text())

    async def meme_radar_card_text(self, *, surface: str = "telegram") -> str:
        """The meme radar card — the website's own rendering, both surfaces."""
        return await self._web_card_text("meme_radar", surface=surface)

    # `status`, matching /fundingscan and /arb — its own subject siblings,
    # which gate with the in-body spelling on the same permission. It was the
    # one ungated command in "Market context", and it spends a live venue
    # fetch per invocation.
    @guard("status")
    async def _cmd_funding(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """/funding [SYMBOL] — live funding rates for a perp across every
        connected venue (Bitget home rate + Bybit + Hyperliquid), with the
        cross-venue spread. Positive funding = longs pay shorts = crowded
        longs. Default symbol: BTC."""
        from bot.core.cross_venue import CROSS_VENUE, VenueFunding, base_of, funding_reading
        from bot.formatters.market_cards import render_funding

        args = ctx.args or []
        raw = (args[0].strip().upper() if args else "BTC")
        base = base_of(raw)
        deriv = f"{base}/USDT:USDT"

        # The HOME venue read, carrying its OWN outcome. It used to be an
        # `except Exception: pass` into a dict, and a dict cannot say whether
        # Bitget was down or simply does not list this base — so the card
        # below answered "check the symbol" for both.
        home: VenueFunding
        try:
            fut_ex = await self.engine.scanner._get_futures_exchange()
            fr = await fut_ex.fetch_funding_rate(deriv)
            rate = fr.get("fundingRate") if isinstance(fr, dict) else None
            home = (VenueFunding("bitget", float(rate), "read")
                    if rate is not None
                    else VenueFunding("bitget", None, "not_listed"))
        except Exception as exc:
            # The venue's own rejection text is logged and never printed: a
            # rejection can echo request params back into a chat bubble.
            system_log.debug("/funding home venue read failed for %s: %s",
                             deriv, type(exc).__name__)
            home = VenueFunding("bitget", None, "unread")

        # Cross-venue map (bulk-cached, keyless). `states_for` never raises and
        # answers one row per polled venue whatever happens, so an `except`
        # around it would be a branch no input can reach.
        cross = await CROSS_VENUE.states_for(base)
        await self._send(update,
                         render_funding(base, funding_reading([home, *cross])))

    async def _cmd_arb(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """/arb — the funding-arb paper tracker: what a fixed $1k delta-
        neutral pair WOULD have earned on the recorded cross-venue spreads,
        with the fee reality check and the VERDICT over it — survives fees,
        does not, too thin to say, or the record could not be read. 100%
        paper — the evidence that gates whether a real capture strategy is
        worth building, read through the one seam the web report reads."""
        if not await self._guard(update, "status"):
            return
        await self._send(update, "⏳ Crunching the paper-arb history…")
        try:
            from bot.core.arb_tracker import arb_reading, format_arb_html
            from bot.core.funding_radar import build_comparison
            carries, verdict = await asyncio.to_thread(arb_reading)
            current = []
            try:
                current = await asyncio.to_thread(
                    build_comparison, ["BTC", "ETH", "SOL", "XRP", "DOGE"])
            except Exception:
                pass
            await self._send(update, format_arb_html(carries, current, verdict=verdict))
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
