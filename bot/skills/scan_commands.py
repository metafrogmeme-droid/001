"""The scan-and-analyse command group — a slice out of the handler.

`/scan`, `/analyze`, `/deepscan`, `/fullscan`, `/stockscan`, `/patterns`,
`/scalp`, `/intraday`, `/swing`, `/momentum`, `/dip`, `/alpha`, `/whynot`,
`/zones`, `/squeeze`, `/sweep`, `/session`, `/mode`, `/research`, `/token`
and `/memeplan`, with the three card renderers and the two per-venue scan
helpers they share. Most of these dispatch a registry skill and render its
answer; the ones that are sold by tier ask `self._token_gate_blocks` first,
and `tests/test_tier_gate_coverage.py` reads every file the handler class is
made of to check that the gate is reached at each dispatch. Their behaviour
is covered where it always was (`test_venue_scan`, `test_scan_ack_tg2`,
`test_scan_timeout_parity`, `test_token_command`, `test_memeplan_command`,
`test_meme_preflight`, `test_telegram_web_parity`);
`tests/test_handler_mixins.py` holds this class to the split's rules.

`_SYMBOL_RE` moved with the group: the three commands that parse a symbol
argument are its only readers. The timeout and freshness hints
(`_scan_timeout_hint` and friends) went to `bot/skills/scan_hints.py`, a
leaf, because `/latest_signal` on the handler reads them too and a mixin
must not import from the handler.

A mixin, not a leaf: every method dispatches through `self.registry` or
reads `self.engine`, and answers through `self._send`, `self._send_photo`
or `self._send_error`. `_token_gate_blocks` stays on the handler — the
free-text intercept and the pane renderers gate through it as well — and
is declared below as part of the host contract.
"""
from __future__ import annotations

import asyncio
import html
import re
from datetime import datetime
from typing import TYPE_CHECKING

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatAction
from telegram.ext import ContextTypes

from bot.compat import UTC
from bot.config import CONFIG
from bot.skills.command_guard import guard
from bot.skills.scan_coverage import coverage_note
from bot.skills.scan_hints import _scan_timeout_hint
from bot.skills.scan_skill import cmd_scan as _scan_skill_handler
from bot.utils.exc_text import _safe_exc_text
from bot.utils.i18n import t
from bot.utils.logger import system_log

if TYPE_CHECKING:
    from bot.core.engine import RuneClawEngine
    from bot.skills.skill_registry import SkillRegistry
    from bot.utils.user_store import UserStore

#: A bare symbol argument: `BTC`, `BTCUSDT`, `BTC/USDT`. Anything else is
#: refused before it reaches an exchange call.
_SYMBOL_RE = re.compile(r'^[A-Z0-9]{1,15}(/[A-Z0-9]{1,15})?$')


class ScanCommands:
    """Market scans, single-symbol analysis and token research. Host contract below; methods after."""

    if TYPE_CHECKING:
        # Provided by TelegramHandler, and ONLY declared here — declarations,
        # never bodies; tests/test_handler_mixins.py checks every name against
        # what the handler really defines.
        engine: RuneClawEngine
        registry: SkillRegistry
        users: UserStore

        async def _send(self, update: Update, text: str,
                        reply_markup=None, edit: bool = False) -> None: ...

        async def _send_error(self, update: Update, command_name: str, exc: Exception) -> None: ...

        async def _send_photo(self, update: Update, png: bytes, caption: str,
                              reply_markup=None) -> bool: ...

        async def _refuse_shared_control(self, update: Update, command: str) -> None: ...

        async def _token_gate_blocks(self, update: Update, mode: str,
                                     feature: str = "premium_scan") -> bool: ...

        def _get_tg_id(self, update: Update) -> str: ...

        def _lang(self, update: Update) -> str: ...

        def _is_admin(self, update: Update) -> bool: ...

        def _is_operator(self, update: Update) -> bool: ...

        @staticmethod
        def _format_research(data: dict) -> str: ...

    @guard("research")
    async def _cmd_research(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """/research <symbol> — the cited research dossier (venue data +
        recorded platform history), same as the web research card."""
        import asyncio as _aio
        from bot.utils.web_data_pull import fetch_research
        args = getattr(ctx, "args", None) or []
        if not args:
            await self._send(update, "Usage: /research <symbol> — e.g. "
                                     "<code>/research PENDLE</code>")
            return
        data = await _aio.to_thread(fetch_research, str(args[0]))
        if not data or "sections" not in data:
            await self._send(update,
                             "No dossier — the symbol isn't listed on the "
                             "venue, or the web app isn't reachable.")
            return
        await self._send(update, self._format_research(data))

    _EVM_ADDR_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")

    @guard("token")
    async def _cmd_token(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """/token <address> [chain] — the contract detective.

        Two questions in one answer: what the contract can do to holders
        (`token_safety`) and who shipped it (`deployer_history`), composed by
        `token_dossier` with every unread section named.

        Detection only. The best verdict available is "nothing found against
        it", and the render leads with what could NOT be checked rather than
        burying it — an UNPROVEN dossier is the normal output for a token
        nobody has a paid data feed for, and it must not read like a warning.
        """
        from bot.core.deployer_sources import CHAIN_IDS
        from bot.core.token_research import human_readable as _dossier_text
        from bot.core.token_research import investigate
        args = getattr(ctx, "args", None) or []
        if not args:
            await self._send(
                update,
                "Usage: <code>/token &lt;contract address&gt; [chain]</code>\n"
                "e.g. <code>/token 0xdAC17F958D2ee523a2206206994597C13D831ec7</code>\n"
                f"Chains: {', '.join(sorted(set(CHAIN_IDS)))}")
            return
        address = str(args[0]).strip()
        if not self._EVM_ADDR_RE.match(address):
            await self._send(
                update, "That is not an EVM contract address (expected 0x + 40 "
                        "hex characters). Nothing was checked.")
            return
        chain = (str(args[1]).strip().lower() if len(args) > 1 else "eth")
        try:
            result = await investigate(address, chain=chain)
        except Exception as exc:                                  # noqa: BLE001
            # A crashed investigation is not a clean token. _send_error logs the
            # real exception and replies generically rather than implying a read.
            await self._send_error(update, "token", exc)
            return
        await self._send(update,
                         "<pre>" + html.escape(_dossier_text(result)) + "</pre>")

    _SOL_MINT_RE = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")

    @guard("memeplan")
    async def _cmd_memeplan(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """/memeplan <mint> [size_usd] — the fail-closed preflight for a meme buy.

        THIS CANNOT TRADE. `meme_executor` is a PLANNER: `would_execute` is a
        hardcoded False, and the signing slice that grew since deliberately
        cannot be reached from here — `meme_swap.build_swap` produces an
        UNSIGNED transaction for the user's own wallet, and only from the web
        surface. The answer here is "would this buy clear every precondition,
        and if not, which one stopped it" — diligence, not execution.

        Three preconditions, all fail-closed: the MEME_TRADING_ENABLED flag
        (default OFF), a human-set Authority Envelope in enforce mode, and the
        rug/liquidity/exit safety gate.

        The gathering itself lives in `meme_preflight` because the web gateway
        needs the identical sequence, and a fail-closed gate maintained in two
        places is one that stops being fail-closed in the copy nobody watches.
        """
        from bot.core import meme_executor
        from bot.core.meme_preflight import preflight

        args = getattr(ctx, "args", None) or []
        if not args:
            await self._send(
                update,
                "Usage: <code>/memeplan &lt;solana mint&gt; [size_usd]</code>\n"
                "The fail-closed preflight for a meme buy — it never trades.")
            return
        mint = str(args[0]).strip()
        if not self._SOL_MINT_RE.match(mint):
            await self._send(update, "That is not a Solana mint (base58, 32-44 "
                                     "chars). Nothing was checked.")
            return
        try:
            size_usd = float(args[1]) if len(args) > 1 else 25.0
        except (TypeError, ValueError):
            await self._send(update, "Size must be a number, in USD.")
            return

        try:
            plan = await preflight(mint, size_usd, tg_id=self._get_tg_id(update))
        except Exception as exc:                                  # noqa: BLE001
            await self._send_error(update, "memeplan", exc)
            return

        await self._send(
            update,
            "<pre>" + html.escape(meme_executor.human_readable(plan)) + "</pre>")

    @guard("mode")
    async def _cmd_mode(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """Switch asset universe: /mode solana | /mode all | /mode stocks | /mode metals | etc."""

        args = (update.message.text or "").split()
        valid_modes = {"all_markets", "all", "solana", "stocks", "hybrid", "metals",
                       "commodities", "etfs", "pre_ipo", "tradfi"}

        if len(args) < 2 or args[1].lower() not in valid_modes:
            from bot.config import RUNTIME
            current = RUNTIME.asset_universe
            icons = {
                "all_markets": "\U0001f310", "solana": "\u2600\ufe0f",
                "all": "\U0001f30d", "stocks": "\U0001f4c8",
                "hybrid": "\U0001f500", "metals": "\u2699\ufe0f", "commodities": "\U0001f6e2\ufe0f",
                "etfs": "\U0001f4ca", "pre_ipo": "\U0001f680", "tradfi": "\U0001f3e6",
            }
            icon = icons.get(current, "\U0001f30d")
            lines = [
                "\U0001f504 <b>ASSET UNIVERSE</b>\n",
                f"Current: {icon} <b>{current.upper()}</b>\n",
                "<b>Multi-Asset:</b>",
                "  <code>/mode all_markets</code> \u2014 EVERYTHING: crypto + all TradFi futures",
                "",
                "<b>Crypto:</b>",
                "  <code>/mode all</code> \u2014 all Bitget USDT spot pairs",
                "  <code>/mode solana</code> \u2014 Solana ecosystem tokens",
                "",
                "<b>TradFi Perpetuals (Futures):</b>",
                "  <code>/mode stocks</code> \u2014 US stock tokenized perps",
                "  <code>/mode hybrid</code> \u2014 crypto + stocks combined",
                "  <code>/mode metals</code> \u2014 Gold, Silver, Platinum, Copper",
                "  <code>/mode commodities</code> \u2014 WTI Oil, Brent, Natural Gas",
                "  <code>/mode etfs</code> \u2014 ETF perpetuals (XLK, KWEB, etc.)",
                "  <code>/mode pre_ipo</code> \u2014 Pre-IPO (OpenAI, Anthropic)",
                "  <code>/mode tradfi</code> \u2014 ALL TradFi combined",
            ]
            if current == "solana":
                from bot.config import SOLANA_ECOSYSTEM_SYMBOLS
                tokens = ", ".join(s.replace("/USDT", "") for s in SOLANA_ECOSYSTEM_SYMBOLS)
                lines.append(f"\nTokens: <i>{tokens}</i>")
            elif current == "stocks":
                from bot.config import US_STOCK_SYMBOLS
                tickers = ", ".join(s.replace("/USDT", "") for s in US_STOCK_SYMBOLS)
                lines.append(f"\nStocks: <i>{tickers}</i>")
            elif current in ("metals", "commodities", "etfs", "pre_ipo", "tradfi"):
                from bot.config import (
                    METAL_PERPETUALS, COMMODITY_PERPETUALS,
                    PRE_IPO_PERPETUALS, ETF_PERPETUALS, TRADFI_PERPETUALS,
                )
                perp_map = {
                    "metals": METAL_PERPETUALS,
                    "commodities": COMMODITY_PERPETUALS,
                    "pre_ipo": PRE_IPO_PERPETUALS,
                    "etfs": ETF_PERPETUALS,
                    "tradfi": TRADFI_PERPETUALS,
                }
                symbols = perp_map.get(current, [])
                names = ", ".join(s.split("/")[0] for s in symbols)
                lines.append(f"\nAssets: <i>{names}</i>")
            await self._send(update, "\n".join(lines))
            return

        new_mode = args[1].lower()
        # Operator only, and gated HERE rather than at the top of the handler:
        # `RUNTIME.asset_universe` is process-wide — it decides which symbols the
        # scan loop pulls for everybody — so the WRITE is an operator action,
        # while everything above this line is the read-only status card. Gating
        # the whole command would have hidden from a user which universe their
        # own scans are running against, and a mistyped `/mode slana` would have
        # been answered with a permission refusal instead of the card that shows
        # the valid names.
        if not self._is_operator(update):
            await self._refuse_shared_control(update, "mode")
            return
        # C1 FIX: use mutable RuntimeState instead of mutating frozen CONFIG
        from bot.config import RUNTIME
        RUNTIME.asset_universe = new_mode

        if new_mode == "solana":
            from bot.config import SOLANA_ECOSYSTEM_SYMBOLS
            tokens = ", ".join(s.replace("/USDT", "") for s in SOLANA_ECOSYSTEM_SYMBOLS)
            await self._send(update, (
                "\u2600\ufe0f <b>SOLANA MODE ACTIVE</b>\n\n"
                f"Scanner now prioritizes {len(SOLANA_ECOSYSTEM_SYMBOLS)} Solana ecosystem tokens:\n"
                f"<i>{tokens}</i>\n\n"
                "The full risk gate still applies. Meme tokens (BONK, WIF) "
                "use tighter volatility and correlation limits.\n\n"
                "Use <code>/mode all</code> to switch back."
            ))
        elif new_mode == "stocks":
            from bot.config import US_STOCK_SYMBOLS
            from bot.core.stock_trading import get_market_session, format_stock_scan_header
            session = get_market_session()
            tickers = ", ".join(s.replace("/USDT", "") for s in US_STOCK_SYMBOLS)
            await self._send(update, (
                "\U0001f4c8 <b>US STOCK MODE ACTIVE</b>\n\n"
                f"{format_stock_scan_header(session)}\n\n"
                f"Scanner now targets {len(US_STOCK_SYMBOLS)} tokenized US stock perps:\n"
                f"<i>{tickers}</i>\n\n"
                "Stock-specific risk rules:\n"
                f"\u2022 ATR guard: {CONFIG.stocks.volatility_guard_atr_pct}%\n"
                f"\u2022 Min R:R: {CONFIG.stocks.min_risk_reward}\n"
                f"\u2022 Max position: {CONFIG.stocks.max_position_pct}%\n"
                f"\u2022 Off-hours size: {CONFIG.stocks.reduce_size_outside_hours:.0%}\n"
                f"\u2022 Max sector positions: {CONFIG.stocks.max_sector_positions}\n\n"
                "Use <code>/mode all</code> to switch back."
            ))
        elif new_mode == "hybrid":
            await self._send(update, (
                "\U0001f500 <b>HYBRID MODE ACTIVE</b>\n\n"
                "Scanner shows both crypto movers and US stock tokenized perps.\n"
                "Risk engine applies stock-specific rules to stock symbols "
                "and crypto rules to crypto symbols automatically.\n\n"
                "Use <code>/mode all</code> to switch back."
            ))
        elif new_mode == "metals":
            from bot.config import METAL_PERPETUALS
            names = ", ".join(s.split("/")[0] for s in METAL_PERPETUALS)
            await self._send(update, (
                "\u2699\ufe0f <b>METALS MODE ACTIVE</b>\n\n"
                f"Scanner targets {len(METAL_PERPETUALS)} metal perpetual contracts (USDT-M Futures):\n"
                f"<i>{names}</i>\n\n"
                "These are commodity-backed perpetuals tradeable 24/7.\n"
                "Lower volume threshold applied for less liquid metals.\n\n"
                "Use <code>/mode all</code> to switch back."
            ))
        elif new_mode == "commodities":
            from bot.config import COMMODITY_PERPETUALS
            names = ", ".join(s.split("/")[0] for s in COMMODITY_PERPETUALS)
            await self._send(update, (
                "\U0001f6e2\ufe0f <b>COMMODITIES MODE ACTIVE</b>\n\n"
                f"Scanner targets {len(COMMODITY_PERPETUALS)} energy commodity perpetuals:\n"
                f"<i>{names}</i>\n\n"
                "WTI Oil, Brent Crude, Natural Gas — USDT-M Futures.\n\n"
                "Use <code>/mode all</code> to switch back."
            ))
        elif new_mode == "etfs":
            from bot.config import ETF_PERPETUALS
            names = ", ".join(s.split("/")[0] for s in ETF_PERPETUALS)
            await self._send(update, (
                "\U0001f4ca <b>ETF MODE ACTIVE</b>\n\n"
                f"Scanner targets {len(ETF_PERPETUALS)} ETF perpetual contracts:\n"
                f"<i>{names}</i>\n\n"
                "Tech, Defense, China Internet, Treasury, HK, India ETFs.\n\n"
                "Use <code>/mode all</code> to switch back."
            ))
        elif new_mode == "pre_ipo":
            from bot.config import PRE_IPO_PERPETUALS
            names = ", ".join(s.split("/")[0] for s in PRE_IPO_PERPETUALS)
            await self._send(update, (
                "\U0001f680 <b>PRE-IPO MODE ACTIVE</b>\n\n"
                f"Scanner targets {len(PRE_IPO_PERPETUALS)} pre-IPO stock perpetuals:\n"
                f"<i>{names}</i>\n\n"
                "Pre-IPO tech company tokens on Bitget — high volatility, use caution.\n\n"
                "Use <code>/mode all</code> to switch back."
            ))
        elif new_mode == "tradfi":
            from bot.config import TRADFI_PERPETUALS
            names = ", ".join(s.split("/")[0] for s in TRADFI_PERPETUALS)
            await self._send(update, (
                "\U0001f3e6 <b>TRADFI MODE ACTIVE</b>\n\n"
                f"Scanner covers ALL {len(TRADFI_PERPETUALS)} TradFi perpetuals:\n"
                f"<i>{names}</i>\n\n"
                "Metals + Commodities + ETFs + Pre-IPO combined.\n"
                "All USDT-M Futures.\n\n"
                "Use <code>/mode all</code> to switch back."
            ))
        elif new_mode == "all_markets":
            from bot.config import TRADFI_PERPETUALS
            await self._send(update, (
                "\U0001f310 <b>ALL MARKETS MODE ACTIVE</b>\n\n"
                "Scanner now covers <b>everything</b> in one scan:\n"
                "\u2022 All Bitget crypto spot pairs\n"
                f"\u2022 {len(TRADFI_PERPETUALS)} TradFi futures (metals, oil, ETFs, pre-IPO)\n\n"
                "Results are categorized by asset class.\n"
                "Spot + Futures fetched in parallel.\n\n"
                "Use <code>/mode all</code> for crypto-only."
            ))
        else:
            await self._send(update, (
                "\U0001f30d <b>CRYPTO-ONLY MODE</b>\n\n"
                "Scanner now covers all Bitget USDT spot pairs.\n"
                "Use <code>/mode all_markets</code> for everything or "
                "<code>/mode solana</code> for Solana."
            ))

    async def _cmd_session(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """/session — show current trading session and its risk adjustments."""
        try:
            from bot.core.session_aware import get_current_session
            session = get_current_session()
        except Exception as exc:
            await self._send(update,
                             f"\u274c Session check failed: {_safe_exc_text(exc)}")
            return

        # Session name styling
        session_icons = {
            "asian": "\U0001f30f",
            "london": "\U0001f1ec\U0001f1e7",
            "london_ny_overlap": "\U0001f525",
            "new_york": "\U0001f1fa\U0001f1f8",
            "late_ny": "\U0001f319",
        }
        icon = session_icons.get(session.session_name, "\U0001f554")

        lines = [
            f"{icon} <b>Current Session: {session.session_name.replace('_', ' ').title()}</b>",
            "",
            f"\U0001f4ca {session.description}",
            "",
            f"Position size: <b>{session.size_multiplier:.0%}</b> of normal",
            f"SL width: <b>{session.sl_width_multiplier:.0%}</b> of normal",
            f"Confidence adj: <b>{session.confidence_adjustment:+.1%}</b>",
            f"Peak liquidity: <b>{'Yes' if session.is_peak_liquidity else 'No'}</b>",
        ]
        if session.is_weekend_risk:
            lines.extend([
                "",
                "\u26a0\ufe0f <b>WEEKEND RISK ACTIVE</b>",
                "Position sizes reduced, SL widened for gap protection.",
            ])

        await self._send(update, "\n".join(lines))

    @guard("scan")
    async def _cmd_sweep(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Show liquidity sweep detection for a symbol."""
        args = context.args if context.args else []
        symbol = args[0].upper() + "/USDT" if args else "BTC/USDT"

        try:
            exchange = await self.engine.get_exchange()
            ohlcv = await exchange.fetch_ohlcv(symbol, "1h", limit=100)
            if not ohlcv or len(ohlcv) < 20:
                await self._send(update,
                    f"\u26a0\ufe0f Not enough candles for <b>{html.escape(symbol)}</b> to compute this yet.")
                return

            import numpy as np
            opens = np.array([c[1] for c in ohlcv])
            highs = np.array([c[2] for c in ohlcv])
            lows = np.array([c[3] for c in ohlcv])
            closes = np.array([c[4] for c in ohlcv])
            volumes = np.array([c[5] for c in ohlcv])

            from bot.core.liquidity_sweep import detect_sweeps
            signals = detect_sweeps(opens, highs, lows, closes, volumes)

            if not signals:
                from bot.formatters.market_cards import render_no_sweeps
                await self._send(update, render_no_sweeps(symbol))
                return

            from bot.formatters.market_cards import render_sweeps
            await self._send(update, render_sweeps(
                symbol, float(closes[-1]), signals))
        except Exception as exc:
            await self._send_error(update, "the liquidity sweep scan", exc)

    @guard("scan")
    async def _cmd_zones(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Show supply/demand zones for a symbol."""
        args = context.args if context.args else []
        symbol = args[0].upper() + "/USDT" if args else "BTC/USDT"

        try:
            exchange = await self.engine.get_exchange()
            ohlcv = await exchange.fetch_ohlcv(symbol, "1h", limit=200)
            if not ohlcv or len(ohlcv) < 20:
                await self._send(update,
                    f"\u26a0\ufe0f Not enough candles for <b>{html.escape(symbol)}</b> to compute this yet.")
                return

            import numpy as np
            opens = np.array([c[1] for c in ohlcv])
            highs = np.array([c[2] for c in ohlcv])
            lows = np.array([c[3] for c in ohlcv])
            closes = np.array([c[4] for c in ohlcv])
            volumes = np.array([c[5] for c in ohlcv])

            # Compute ATR
            tr = np.maximum(highs[1:] - lows[1:], np.maximum(
                np.abs(highs[1:] - closes[:-1]), np.abs(lows[1:] - closes[:-1])))
            atr = float(np.mean(tr[-14:])) if len(tr) >= 14 else float(np.mean(tr))

            from bot.core.supply_demand import detect_zones
            zones = detect_zones(opens, highs, lows, closes, volumes, atr=atr)

            if not zones:
                from bot.formatters.market_cards import render_no_zones
                await self._send(update, render_no_zones(symbol))
                return

            from bot.formatters.market_cards import render_zones
            await self._send(update, render_zones(
                symbol, float(closes[-1]), zones))
        except Exception as exc:
            await self._send_error(update, "the supply/demand zone scan", exc)

    @guard("scan")
    async def _cmd_squeeze(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Show volatility squeeze status for a symbol."""
        args = context.args if context.args else []
        symbol = args[0].upper() + "/USDT" if args else "BTC/USDT"

        try:
            exchange = await self.engine.get_exchange()
            ohlcv = await exchange.fetch_ohlcv(symbol, "1h", limit=200)
            if not ohlcv or len(ohlcv) < 30:
                await self._send(update,
                    f"\u26a0\ufe0f Not enough candles for <b>{html.escape(symbol)}</b> to compute this yet.")
                return

            import numpy as np
            highs = np.array([c[2] for c in ohlcv])
            lows = np.array([c[3] for c in ohlcv])
            closes = np.array([c[4] for c in ohlcv])

            from bot.core.smart_exits import detect_squeeze
            sig = detect_squeeze(closes, highs, lows)

            if sig is None:
                from bot.formatters.market_cards import render_squeeze_unavailable
                await self._send(update, render_squeeze_unavailable(symbol))
                return

            from bot.formatters.market_cards import render_squeeze
            await self._send(update, render_squeeze(
                symbol, float(closes[-1]), sig))
        except Exception as exc:
            await self._send_error(update, "the squeeze scan", exc)

    @guard("scan")
    async def _cmd_scan(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        user_id = self._get_tg_id(update)
        # `/scan <venue>` looks at ONE venue's own market list — Hyperliquid's
        # builder perps, a Bybit-only listing — whether or not anyone trades
        # there; `/scan venues` lists them. Anything else (`/scan BTC`,
        # `/scan deep`) is the market sweep below, as before.
        args = [a.strip().lower() for a in (ctx.args or []) if a and a.strip()]
        if args and args[0] in ("venues", "venue"):
            await self._send(update, self._scan_venues_text(self._lang(update)))
            return
        if args:
            from bot.core.venues import valid_venue_ids
            if args[0] in valid_venue_ids():
                await self._scan_one_venue(update, args[0])
                return
        # Immediate feedback: a full market scan sweeps ~200 pairs and can take
        # several seconds. Without an ack /scan reads as total silence (audit
        # TG-2) — show the typing indicator AND a lightweight status line, then
        # the result card follows. Both best-effort so a send hiccup never
        # blocks the actual scan.
        try:
            if update.effective_chat:
                await update.effective_chat.send_chat_action(ChatAction.TYPING)
        except Exception:
            pass
        await self._send(update,
            "🔍 <b>Scanning the market…</b> sweeping ~200 pairs for setups — "
            "the results card follows in a few seconds.")
        result = await self.registry.dispatch("scan_market", self.engine, user_id=user_id)
        # Visual grid card from the structured signals the skill stashed; falls
        # back to the text result on any failure.
        signals = getattr(self.engine, "_last_scan_signals", None)
        if signals and await self._render_scan_signals_card(update, signals, "MARKET SCAN"):
            return
        await self._send(update, result)

    def _scan_venues_text(self, lang: str) -> str:
        """The `/scan venues` list: every venue the table knows, as a command."""
        from bot.core.venues import get_venue, valid_venue_ids
        lines = [t("venue_scan_list", lang)]
        for vid in valid_venue_ids():
            lines.append(f"• <code>/scan {vid}</code> — "
                         f"{html.escape(get_venue(vid).display_name)}")
        return "\n".join(lines)

    async def _scan_one_venue(self, update: Update, venue_id: str) -> None:
        """`/scan <venue>`: one venue's own catalogue through a keyless client.

        Three outcomes reach the person (bot/core/venue_scan.py): movers, as
        the grid card — sparklines fetched from the venue itself, because the
        active executor's exchange does not know a Hyperliquid symbol — or
        the text list; none above the floor; or the venue did not answer,
        which is a failed read and says so rather than reading as quiet.
        """
        from bot.core.venue_scan import render_venue_scan
        from bot.core.venues import get_venue
        lang = self._lang(update)
        venue = get_venue(venue_id)
        try:
            if update.effective_chat:
                await update.effective_chat.send_chat_action(ChatAction.TYPING)
        except Exception:
            pass
        await self._send(update, t("venue_scan_ack", lang,
                                   venue=html.escape(venue.display_name)))
        vs = await self.engine.scanner.scan_venue(venue_id)
        if vs.signals:
            exchange = None
            try:
                exchange = await self.engine.scanner.venue_data_exchange(venue_id)
            except Exception:
                exchange = None
            if await self._render_scan_signals_card(
                    update, vs.signals, f"{venue.display_name.upper()} SCAN",
                    exchange=exchange):
                return
        await self._send(update, render_venue_scan(vs, lang))

    async def _render_scan_signals_card(self, update, signals, title: str,
                                        exchange=None) -> bool:
        """Render a list of MarketSignal objects as the breadth grid card
        (with sparklines + RSI). Best-effort; returns True if a card was sent.
        `exchange` names where the sparkline candles come from; the default is
        the active executor's exchange, which is right for the market sweep
        and wrong for a venue scan."""
        try:
            import asyncio as _asyncio

            import numpy as _np

            from bot.formatters.rich_cards import compute_rsi
            from bot.formatters.signal_card import render_scan_grid_card

            top = list(signals)[:18]
            if exchange is None:
                try:
                    exchange = await self.engine.live_executor._get_exchange()
                except Exception:
                    try:
                        exchange = await self.engine.get_exchange()
                    except Exception:
                        exchange = None

            async def _spark_rsi(sym):
                if not exchange:
                    return None, None
                try:
                    ohlcv = await exchange.fetch_ohlcv(sym, "1h", limit=30)
                    closes = [float(c[4]) for c in (ohlcv or []) if c and len(c) > 4]
                    if len(closes) < 5:
                        return None, None
                    return closes, float(compute_rsi(_np.array(closes, dtype=float)))
                except Exception:
                    return None, None

            enriched = await _asyncio.gather(*[_spark_rsi(s.symbol) for s in top])
            grid = []
            for s, (closes, rsi) in zip(top, enriched):
                grid.append({
                    "sym": s.symbol, "price": getattr(s, "price", 0) or 0,
                    "change_pct": getattr(s, "change_pct_24h", 0) or 0,
                    "spark": closes, "rsi": rsi,
                })
            up = sum(1 for s in signals if (getattr(s, "change_pct_24h", 0) or 0) > 0)
            dn = sum(1 for s in signals if (getattr(s, "change_pct_24h", 0) or 0) < 0)
            vol = sum((getattr(s, "volume_usd_24h", 0) or 0) for s in signals)
            png = render_scan_grid_card({
                "title": title,
                "timestamp": f"{datetime.now(UTC).strftime('%H:%M')} UTC",
                "grid": grid,
                "summary": {"up": up, "down": dn, "vol_usd": vol},
            })
            if not png:
                return False
            return await self._send_photo(
                update, png, f"\U0001f50e <b>{title}</b> — {len(signals)} pairs")
        except Exception as exc:
            system_log.debug("scan signals card render failed: %s", exc)
            return False

    @guard("analyze")
    async def _cmd_analyze(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if await self._token_gate_blocks(update, "analysis", "analyze_asset"):
            return
        args = ctx.args
        if args:
            raw = args[0].upper().strip()
            # Strip common display suffixes users might copy-paste
            # e.g. "ANTHROPICUSDT:USDT" -> "ANTHROPICUSDT" -> resolve below
            raw = raw.replace(":USDT", "")
            # SEC-H3 FIX: strict symbol validation before reaching CCXT/LLM
            if not _SYMBOL_RE.match(raw):
                await self._send(update,
                    f"\U0001f534 {t('analyze_invalid_symbol', self._lang(update))}")
                return
            # Prevent self-referencing pairs like USDT/USDT
            base = raw.split("/")[0]
            if base == "USDT":
                await self._send(update,
                    f"\U0001f534 {t('analyze_usdt_self', self._lang(update))}")
                return
            symbol = raw if "/" in raw else f"{raw}/USDT"
        else:
            symbol = "BTC/USDT"

        ids_before = set(idea.id for idea in self.engine.pending_ideas)
        admin = self._is_admin(update)
        # i18n: translate the inner sentence only; the \u23f3 + italics wrapper is
        # kept here so the English output is byte-identical to before.
        await self._send(
            update,
            f"\u23f3 <i>{t('analyzing', self._lang(update), asset=html.escape(symbol))}</i>")

        try:
            _tg_id = self._get_tg_id(update)
            result = await self.registry.dispatch("analyze_asset",
                self.engine, symbol=symbol, is_admin=admin,
                user_id=_tg_id,
                user_tier=(self.users.get(_tg_id) or {}).get("tier"))
        except Exception as exc:
            system_log.error("analyze_asset failed for %s: %s", symbol, exc, exc_info=True)
            await self._send(update,
                f"\U0001f534 {t('analyze_failed', self._lang(update), symbol=html.escape(symbol), detail=_safe_exc_text(exc))}")
            return

        new_idea = None
        for idea in self.engine.pending_ideas:
            if idea.id not in ids_before:
                new_idea = idea
                break

        if new_idea is not None:
            uid = update.effective_user.id if update.effective_user else ""
            kb = InlineKeyboardMarkup([[
                InlineKeyboardButton(t("btn_take_it", self._lang(update)), callback_data=f"confirm:{new_idea.id}:{uid}"),
                InlineKeyboardButton(t("lbl_limit", self._lang(update)), callback_data=f"setlimit:{new_idea.id}:{uid}"),
                InlineKeyboardButton(t("btn_skip", self._lang(update)), callback_data=f"reject:{new_idea.id}:{uid}"),
            ]])
            # Send signal card image with confirm/reject buttons
            card_sent = False
            if hasattr(self, '_signal_card_fn'):
                try:
                    chat_id = str(update.effective_chat.id) if update.effective_chat else ""
                    if chat_id:
                        from bot.formatters.signal_card import signal_card_from_idea
                        png = signal_card_from_idea(new_idea, rank=1)
                        if png:
                            cap = result[:1024] if len(result) <= 1024 else result[:1020] + "..."
                            card_sent = await self._send_photo(update, png, cap, reply_markup=kb)
                except Exception as exc:
                    system_log.debug("Analyze signal card failed: %s", exc)
            if not card_sent:
                await self._send(update, result, reply_markup=kb)
        else:
            await self._send(update, result)

    @guard("rejected")
    async def _cmd_whynot(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """/whynot [symbol] — explain why a trade was rejected by risk."""
        args = ctx.args or []
        symbol = args[0].upper().strip() if args else ""
        # H-17 FIX: validate symbol format before passing to skill
        if symbol and not _SYMBOL_RE.match(symbol):
            await self._send(update, t("invalid_symbol_format", self._lang(update)))
            return
        result = await self.registry.dispatch("whynot",
            self.engine, symbol=symbol)
        await self._send(update, result)

    async def _cmd_alpha(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """/alpha <symbol> — Daily Alpha insight card (exchange-style panel
        built entirely from the bot's own analysis + Bitget public data:
        MTF trend, key levels, MACD/RSI/ADX strength, funding/OI/long-short
        positioning, Fear&Greed)."""
        args = ctx.args or []
        raw = args[0] if args else "BTC"
        if not _SYMBOL_RE.match(raw.upper().strip().replace("/USDT", "").replace(":USDT", "")):
            await self._send(update, t("invalid_symbol_format", self._lang(update)))
            return
        from bot.core.alpha_card import (build_alpha_insight, format_alpha_card,
                                         normalize_alpha_symbol)
        symbol = normalize_alpha_symbol(raw)
        await self._send(update, f"📡 Building alpha card for <b>{html.escape(symbol.replace('/USDT:USDT', ''))}</b>…")
        try:
            data = await build_alpha_insight(self.engine, symbol)
            # RUNECLAW-styled PNG first; fall back to the HTML text card if
            # rendering is unavailable (no Pillow / error data / send failure).
            png = b""
            try:
                from bot.formatters.signal_card import render_alpha_card
                png = render_alpha_card(data)
            except Exception:
                png = b""
            if png:
                sym_short = html.escape(symbol.replace("/USDT:USDT", ""))
                cap = f"📡 <b>{sym_short} Daily Alpha</b> — same data the bot trades on"
                if await self._send_photo(update, png, cap):
                    return
            await self._send(update, format_alpha_card(data))
        except Exception as exc:
            await self._send(update, f"⚠️ Alpha card failed: {_safe_exc_text(exc, limit=160)}")

    @guard("run")
    async def _cmd_momentum(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """Shortcut for /run momentum."""
        await self._send(update, "\u23f3 <i>Running Momentum Hunter...</i>")
        result = await self.registry.dispatch("run_strategy",
            self.engine, strategy="momentum")
        if not await self._render_strategy_setups_card(update, "MOMENTUM HUNTER"):
            await self._send(update, result)

    @guard("run")
    async def _cmd_dip(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """Shortcut for /run dip."""
        await self._send(update, "\u23f3 <i>Running Dip Sniper (all symbols)...</i>")
        result = await self.registry.dispatch("run_strategy",
            self.engine, strategy="dip")
        if not await self._render_strategy_setups_card(update, "DIP SNIPER"):
            await self._send(update, result)

    async def _render_strategy_setups_card(self, update, label: str) -> bool:
        """Render the stashed strategy setups (entry/SL/TP/R:R per idea) as the
        setups card. Best-effort; returns True if a card was sent."""
        try:
            setups = getattr(self.engine, "_last_strategy_setups", None)
            if not setups:
                return False
            from bot.formatters.signal_card import render_scan_results_card
            png = render_scan_results_card(
                setups, scan_label=label,
                timestamp=f"{datetime.now(UTC).strftime('%H:%M')} UTC")
            if not png:
                return False
            return await self._send_photo(
                update, png, f"\U0001f3af <b>{label}</b> \u2014 {len(setups)} setup(s)")
        except Exception as exc:
            system_log.debug("strategy setups card render failed: %s", exc)
            return False

    @guard("scan")
    async def _cmd_scalp(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """Scalp scan: 5m candles, tight SL, top-3 by volume."""
        if await self._token_gate_blocks(update, "scalp"):
            return
        await self._send(update, "\u26a1 <i>Scalp scan — 5M candles, tight zones...</i>")
        try:
            result = await self.registry.dispatch("pro_scan",
                self.engine, mode="scalp", user_id=self._get_tg_id(update))
            signals = getattr(self.engine, "_last_scan_signals", None)
            if not (signals and await self._render_scan_signals_card(
                    update, signals, "SCALP SCAN")):
                await self._send(update, result)
        except Exception as exc:
            system_log.error(f"Scalp scan error: {exc}", exc_info=True)
            await self._send(update, f"🔴 <b>Scalp scan error:</b> <code>{_safe_exc_text(exc)}</code>")

    @guard("scan")
    async def _cmd_intraday(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """Intraday scan: 15m candles, top-5 movers."""
        if await self._token_gate_blocks(update, "intraday"):
            return
        await self._send(update, "\U0001f4ca <i>Intraday scan — 15M structure...</i>")
        try:
            result = await self.registry.dispatch("pro_scan",
                self.engine, mode="intraday", user_id=self._get_tg_id(update))
            signals = getattr(self.engine, "_last_scan_signals", None)
            if not (signals and await self._render_scan_signals_card(
                    update, signals, "INTRADAY SCAN")):
                await self._send(update, result)
        except Exception as exc:
            system_log.error(f"Intraday scan error: {exc}", exc_info=True)
            await self._send(update, f"🔴 <b>Intraday scan error:</b> <code>{_safe_exc_text(exc)}</code>")

    @guard("scan")
    async def _cmd_swing(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """Swing scan: 4h candles, wide SL/TP, trend-based."""
        if await self._token_gate_blocks(update, "swing"):
            return
        await self._send(update, "<i>Checking the 4H chart...</i>")
        try:
            result = await self.registry.dispatch("pro_scan",
                self.engine, mode="swing", user_id=self._get_tg_id(update))
            signals = getattr(self.engine, "_last_scan_signals", None)
            if not (signals and await self._render_scan_signals_card(
                    update, signals, "SWING SCAN")):
                await self._send(update, result)
        except ValueError as ve:
            # TradeIdea validation errors (SL=entry, etc.) — report but don't crash
            system_log.warning(f"Swing scan validation error: {ve}")
            await self._send(update,
                "<b>Swing scan:</b> skipped — invalid setup generated "
                "(SL too close to entry). Try again or use /scan.")
        except Exception as exc:
            system_log.error(f"Swing scan error: {exc}", exc_info=True)
            await self._send(update, f"\U0001f534 <b>Swing scan error:</b> <code>{_safe_exc_text(exc)}</code>")

    @guard("deepscan")
    async def _cmd_deepscan(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """Deep scan the universe with chart + candle patterns."""
        if await self._token_gate_blocks(update, "deep", "deepscan"):
            return
        # Parse optional timeframe from args: /deepscan 1h  (or /deepscan all
        # to sweep every timeframe 5m→1d in one pass).
        from bot.utils.candles import SUPPORTED_TIMEFRAMES
        tf = "4h"
        if ctx.args:
            arg = ctx.args[0].lower().strip()
            if arg == "all" or arg in SUPPORTED_TIMEFRAMES:
                tf = arg
        _multi = tf == "all"
        _tf_label = "ALL TIMEFRAMES (5m→1d)" if _multi else tf.upper()
        await self._send(update, f"🔬 <i>Deep scanning {_tf_label} — this may take a minute...</i>")
        try:
            result = await asyncio.wait_for(
                self.registry.dispatch("deepscan",
                    self.engine, timeframe=tf),
                # A full multi-timeframe sweep does ~5× the fetches, so give
                # it proportionally longer. Configurable: the right value
                # depends on the universe, and a deadline shorter than the
                # work is a sizing fact, not an exchange fault.
                timeout=(CONFIG.deepscan_multi_timeout_sec if _multi
                         else CONFIG.deepscan_timeout_sec),
            )
            if result:
                # Try to render a card image from structured hits
                card_sent = False
                try:
                    hits = getattr(self.engine, '_last_deepscan_hits', None)
                    if hits:
                        from bot.formatters.signal_card import render_scan_results_card
                        # Convert deepscan hits to scan card format
                        setups = []
                        for h in hits[:6]:
                            price = h["price"]
                            # Real ATR from the scan; fall back to 2% only if
                            # the scan couldn't compute one.
                            atr = h.get("atr") or price * 0.02
                            direction = "LONG" if h.get("rsi", 50) < 50 or h.get("chg", 0) > 0 else "SHORT"
                            if direction == "LONG":
                                entry = round(price - atr * 0.3, 8)
                                sl_val = round(price - atr * 2.5, 8)
                                tp_val = round(price + atr * 3.0, 8)
                            else:
                                entry = round(price + atr * 0.3, 8)
                                sl_val = round(price + atr * 2.5, 8)
                                tp_val = round(price - atr * 3.0, 8)
                            sl_dist = abs(entry - sl_val) / entry * 100 if entry > 0 else 0
                            tp_dist = abs(tp_val - entry) / entry * 100 if entry > 0 else 0
                            rr = tp_dist / sl_dist if sl_dist > 0 else 0
                            setups.append({
                                "sym": h["symbol"],
                                "dir": direction,
                                "price": price,
                                "entry": entry,
                                "sl": sl_val,
                                "tp": tp_val,
                                "rr": rr,
                                "rsi": h.get("rsi", 0),
                                "vol_ratio": 2.5 if h.get("vol_spike") else 1.0,
                                # Pre-normalized relative to this scan's best
                                # hit (see DeepScanSkill.execute) -- NOT a
                                # fixed-divisor guess that saturates at 100%.
                                "score": h.get("score_norm", 0.0),
                            })
                        now_str = datetime.now(UTC).strftime('%H:%M UTC')
                        card_png = render_scan_results_card(
                            setups, scan_label=f"DEEP SCAN {tf.upper()}",
                            timestamp=now_str)
                        if card_png:
                            import io as _io
                            buf = _io.BytesIO(card_png)
                            buf.name = "deepscan.png"
                            chat_id = str(update.effective_chat.id) if update.effective_chat else ""
                            if chat_id:
                                await update.get_bot().send_photo(
                                    chat_id=int(chat_id), photo=buf,
                                    caption=f"🔬 <b>RUNECLAW Deep Scan</b> — {tf.upper()} — {now_str}",
                                    parse_mode="HTML")
                                card_sent = True
                except Exception as exc:
                    system_log.warning("Deepscan card render failed: %s", exc)

                # Render the pattern observations as a card too (mirrors the
                # text patterns readout). Text is still sent below as a fallback.
                patterns_card_sent = False
                try:
                    p_hits = getattr(self.engine, '_last_deepscan_hits', None)
                    if p_hits:
                        from bot.formatters.signal_card import render_patterns_card
                        now_str = datetime.now(UTC).strftime('%H:%M UTC')
                        p_png = render_patterns_card(
                            p_hits,
                            scan_label=f"DEEP SCAN {tf.upper()}",
                            timestamp=now_str,
                            subtitle=f"{len(p_hits)} hits · {tf} · chart + candle patterns",
                        )
                        if p_png:
                            import io as _io
                            p_buf = _io.BytesIO(p_png)
                            p_buf.name = "deepscan_patterns.png"
                            chat_id = str(update.effective_chat.id) if update.effective_chat else ""
                            if chat_id:
                                await update.get_bot().send_photo(
                                    chat_id=int(chat_id), photo=p_buf,
                                    caption=f"🔍 <b>Patterns</b> — {tf.upper()} — {now_str}",
                                    parse_mode="HTML")
                                patterns_card_sent = True
                except Exception as exc:
                    system_log.warning("Deepscan patterns card render failed: %s", exc)

                # Send text result (full details + patterns). When the patterns
                # card rendered, the text is redundant noise — skip it.
                if not patterns_card_sent:
                    await self._send(update, result)
            else:
                await self._send(update, "🔴 <b>Deepscan returned empty result.</b>")
        except asyncio.TimeoutError:
            # NOT "Exchange may be slow". That was a verdict inferred from a
            # deadline expiring, and on 2026-07-29 the engine's own numbers
            # showed the exchange was fine — the analyze phase was simply
            # given more symbols than its budget covered. A timeout says the
            # work did not fit the time. Which of the two was wrong is a
            # separate question, and this message is not entitled to answer
            # it. Same correction as the interactive-scan hint above.
            _budget = (CONFIG.deepscan_multi_timeout_sec if _multi
                       else CONFIG.deepscan_timeout_sec)
            system_log.error("Deepscan timed out after %.0fs (multi=%s)",
                             _budget, _multi)
            # ...and where the time goes is a question this bot can already
            # answer. _scan_timeout_hint carries the degraded-LLM streak and
            # the engine's own analysis-timeout record (symbol AND stage).
            # Sending the operator to /status for a fact we hold is a
            # deflection, and it left the diagnostic wired to exactly one of
            # the four commands that can time out.
            await self._send(update,
                f"🔴 <b>Deepscan hit its {_budget:.0f}s limit.</b> That is this "
                f"command's own budget — not a diagnosis of the exchange."
                + (_scan_timeout_hint(getattr(self.engine, "analyzer", None),
                                      self.engine)
                   or "\n\n<code>/status</code> carries the engine's measured "
                      "scan timing if you want to know where the time goes."))
        except Exception as exc:
            system_log.error(f"Deepscan error: {exc}", exc_info=True)
            await self._send(update, f"🔴 <b>Deepscan error:</b> <code>{_safe_exc_text(exc)}</code>")

    @guard("scan")
    async def _cmd_fullscan(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """Full-universe scan via scan_skill module.

        No symbol count in this line: the count lives in scan_skill.UNIVERSE
        and every user-facing number derives from len() of it.
        /fullscan [deep|deepall|swing|scalp|SYMBOL]
        """
        await _scan_skill_handler(update, ctx)

    @guard("scan")
    async def _cmd_stockscan(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """/stockscan — Scan US stock tokenized perpetuals."""

        from bot.core.stock_trading import (
            get_market_session, format_stock_scan_header,
            format_stock_signal_line,
        )
        from bot.config import US_STOCK_SYMBOLS

        session = get_market_session()
        await self._send(update,
            f"\U0001f4c8 <i>Scanning US stock tokenized perps...</i>\n"
            f"{format_stock_scan_header(session)}")

        try:
            exchange = await self.engine.get_exchange()
            tickers = await exchange.fetch_tickers()
        except Exception as exc:
            await self._send(update, f"\U0001f534 <b>Exchange error:</b> {_safe_exc_text(exc)}")
            return

        # Filter to stock symbols — try exact match first, then fuzzy
        stock_set = set(US_STOCK_SYMBOLS)
        stock_signals = []
        # How many stock perps the venue listed, against how many priced.
        _stock_seen = 0

        # Also detect any symbol with stock-like naming (ON suffix or R prefix)
        stock_name_patterns = {
            "AAPL", "MSFT", "GOOGL", "AMZN", "META", "NVDA", "TSLA",
            "AMD", "QQQ", "SPY", "COIN", "HOOD", "ARM", "MRVL",
            "DELL", "INTC", "NOK", "ANET", "NFLX", "CRM",
        }
        for sym, tick in tickers.items():
            if not sym.endswith("/USDT"):
                continue
            # Check exact match or pattern match
            is_stock = sym in stock_set
            if not is_stock:
                base = sym.replace("/USDT", "")
                for pat in stock_name_patterns:
                    if pat in base.upper():
                        is_stock = True
                        break
            if not is_stock:
                continue
            _stock_seen += 1
            try:
                price = float(tick.get("last", 0) or 0)
                change = float(tick.get("percentage", 0) or 0)
                volume = float(tick.get("quoteVolume", 0) or 0)
                if price <= 0:
                    # Recognised as a stock perp, but the venue served no
                    # price for it. Dropping it silently and then heading the
                    # card with the surviving count presents a partial read as
                    # the whole board.
                    continue
                stock_signals.append({
                    "symbol": sym,
                    "price": price,
                    "change_pct": round(change, 2),
                    "volume": round(volume, 2),
                })
            except (TypeError, ValueError):
                continue

        if not stock_signals:
            await self._send(update,
                "\U0001f534 <b>No stock symbols found on exchange.</b>\n\n"
                "Stock tokenized perps may not be available on this Bitget account.\n"
                "Check if your account has access to tokenized equity derivatives.")
            return

        # Sort by absolute change
        stock_signals.sort(key=lambda s: abs(s["change_pct"]), reverse=True)

        # Summary counts (shared by card + text paths)
        gainers = sum(1 for s in stock_signals if s["change_pct"] > 0)
        losers = sum(1 for s in stock_signals if s["change_pct"] < 0)
        total_vol = sum(s["volume"] for s in stock_signals)

        # ── Visual card path (grid + sparklines + top setups). Best-effort:
        #    any failure falls through to the text list below. ──
        if await self._render_stockscan_card(
                update, exchange, stock_signals, session,
                gainers, losers, total_vol):
            return

        # Build output
        lines = [
            f"\U0001f4c8 <b>US STOCK SCAN</b> \u2014 {len(stock_signals)} symbols  |  "
            f"{datetime.now(UTC).strftime('%H:%M')} UTC\n"
            + coverage_note(_stock_seen, len(stock_signals)),
            format_stock_scan_header(session),
            "",
        ]

        # Get risk params
        risk_note = ""
        if session.is_weekend:
            risk_note = "\n\u26a0\ufe0f <i>Weekend: reduced liquidity, wider spreads</i>\n"
        elif session.session_name in ("closed", "pre_market", "after_hours"):
            risk_note = f"\n\u26a0\ufe0f <i>{session.session_name.replace('_', ' ').title()}: size reduced to {session.size_multiplier:.0%}</i>\n"
        if risk_note:
            lines.append(risk_note)

        for sig in stock_signals[:15]:
            line = format_stock_signal_line(
                sig["symbol"], sig["price"], sig["change_pct"],
            )
            lines.append(line)

        # Summary (counts computed above)
        lines.append(f"\n\U0001f7e2 {gainers} up  \U0001f534 {losers} down  |  Vol: ${total_vol/1e6:.1f}M")
        lines.append("\n<code>/mode stocks</code> to auto-scan stocks  |  <code>/mode hybrid</code> for both")

        await self._send(update, "\n".join(lines))

    async def _render_stockscan_card(self, update, exchange, stock_signals,
                                     session, gainers, losers, total_vol) -> bool:
        """Render the stock scan as a grid+setups+sparkline PNG card.

        Best-effort and display-only: enriches the top symbols with 1h closes
        (sparkline + RSI) and renders via render_scan_grid_card. Returns True if a
        card was sent; False (or on any error) lets the caller fall back to text.
        """
        try:
            import asyncio as _asyncio

            import numpy as _np

            from bot.formatters.rich_cards import compute_rsi
            from bot.formatters.signal_card import render_scan_grid_card

            # Enrich only the top symbols shown in the grid (bounded fan-out).
            top = stock_signals[:18]

            async def _spark_rsi(sym: str):
                try:
                    ohlcv = await exchange.fetch_ohlcv(sym, "1h", limit=30)
                    closes = [float(c[4]) for c in (ohlcv or []) if c and len(c) > 4]
                    if len(closes) < 5:
                        return None, None
                    rsi = float(compute_rsi(_np.array(closes, dtype=float)))
                    return closes, rsi
                except Exception:
                    return None, None

            enriched = await _asyncio.gather(*[_spark_rsi(s["symbol"]) for s in top])

            grid = []
            for s, (closes, rsi) in zip(top, enriched):
                row = {
                    "sym": s["symbol"],
                    "price": s["price"],
                    "change_pct": s["change_pct"],
                    "spark": closes,
                    "rsi": rsi,
                }
                grid.append(row)

            banner = ""
            if session.is_weekend:
                banner = "⚠ Weekend: reduced liquidity, wider spreads"
            elif session.session_name in ("closed", "pre_market", "after_hours"):
                banner = (f"⚠ {session.session_name.replace('_', ' ').title()}: "
                          f"size reduced to {session.size_multiplier:.0%}")

            png = render_scan_grid_card({
                "title": "US STOCK SCAN",
                "timestamp": f"{datetime.now(UTC).strftime('%H:%M')} UTC",
                "banner": banner,
                "grid": grid,
                "summary": {"up": gainers, "down": losers, "vol_usd": total_vol},
            })
            if not png:
                return False
            cap = (f"\U0001f4c8 <b>US STOCK SCAN</b> — {len(stock_signals)} symbols\n"
                   f"<code>/mode stocks</code> to auto-scan  |  <code>/mode hybrid</code> for both")
            return await self._send_photo(update, png, cap)
        except Exception as exc:
            system_log.debug("stockscan card render failed: %s", exc)
            return False

    @guard("patterns")
    async def _cmd_patterns(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """/patterns — chart-pattern sweep across the scan universe."""
        if await self._token_gate_blocks(update, "patterns", "patterns"):
            return
        # This awaited the dispatch with NO deadline: a stalled fetch left the
        # operator staring at a command that never answered and never failed.
        # Its sibling scans have all had a budget for months.
        _budget = float(getattr(CONFIG, "deepscan_timeout_sec", 120) or 120)
        try:
            result = await asyncio.wait_for(
                self.registry.dispatch("patterns", self.engine),
                timeout=_budget)
        except asyncio.TimeoutError:
            system_log.error("Patterns scan timed out after %.0fs", _budget)
            await self._send(update,
                f"🔴 <b>Pattern scan hit its {_budget:.0f}s limit.</b> That is "
                f"this command's own budget — not a diagnosis of the exchange."
                + (_scan_timeout_hint(getattr(self.engine, "analyzer", None),
                                      self.engine)
                   or "\n\n<code>/status</code> carries the engine's measured "
                      "scan timing if you want to know where the time goes."))
            return
        await self._send(update, result)
