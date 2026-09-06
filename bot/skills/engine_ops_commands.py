"""The engine-ops command group — a slice out of the handler.

The operator's controls over the running engine: `/accounts`, `/venue`,
`/leverage`, `/drawdownlimit`, `/autoconfirm`, `/forcescan`, `/golive`,
`/liveclose`, `/closeall`, `/audit`, `/shadow`, `/parity`, `/gates`,
`/flags`, `/strategy`, `/journal`, and the read-only attribution, equity
curve, cross-asset and slippage reports. Every one of these either changes
what the engine will do next or reports on what it did, which is why the
mutating ones sit behind `guard('admin')` and an audit line. Their behaviour
is covered where it always was (`test_venue_switch`, `test_telegram_commands`,
`test_closeall_confirm_tg2b`, `test_class_evidence_tuning`,
`test_strategy_cmd_regime`, `test_journal_gap_honesty`,
`test_balance_fields_beyond_free_are_three_valued`);
`tests/test_handler_mixins.py` holds this class to the split's rules.

Three module-level helpers moved with the group because the group is their
only caller. `venue_balance_line` is the three-valued balance line under a
"venue switched" banner (RC-2026-017); `_journal_gap_closes` counts the
closes the executor recorded in a window the journal shows as empty, so
`/journal` can tell a recording gap from a quiet week; `_each_executor`
walks the operator executor and every per-user one once each.

A mixin, not a leaf: every method reads `self.engine` and answers through
`self._send` (or `_reply`, its alias, and `_send_error`, the friendly-reply
chokepoint for a failed operator call).
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from bot.config import CONFIG
from bot.core.margin_clamp import read_money_field
from bot.skills.command_guard import guard
from bot.utils.exc_text import _safe_exc_text
from bot.utils.i18n import t
from bot.utils.logger import audit, system_log

if TYPE_CHECKING:
    from bot.core.engine import RuneClawEngine


def venue_balance_line(acct: object, coin: str) -> str:
    """The balance line under a "venue switched" banner, three-valued.

    RC-2026-017, extracted so it can be exercised. Inline in the handler,
    nothing could plant a venue that answered without a balance-coin entry and
    read what the operator would see -- and what they would have seen was

        • Balance: 0.00 USDC (free 0.00)

    directly beneath a green "Venue switched" heading, because the reads were
    `float(acct.get("free") or 0)`. A confident zero, assembled from an absent
    entry, shown at the moment somebody is deciding whether the switch worked.

    Each half is stated only when it was read; a venue that reports a total but
    no free margin says so rather than printing a zero for the half nobody
    measured.
    """
    total = read_money_field(acct, "total")
    free = read_money_field(acct, "free")
    if total is None and free is None:
        return (f"\n• Balance: <i>could not be read</i> — the venue answered "
                f"without a {coin} entry")
    _t = f"{total:,.2f} {coin}" if total is not None else f"unknown {coin}"
    _f = f"{free:,.2f}" if free is not None else "unknown"
    return f"\n• Balance: <b>{_t}</b> (free {_f})"


def _each_executor(engine):
    """The operator executor plus any per-user ones, de-duplicated by id."""
    seen: set = set()
    for ex in (getattr(engine, "live_executor", None),
               *(getattr(engine, "_user_executors", None) or {}).values()):
        if ex is not None and id(ex) not in seen:
            seen.add(id(ex))
            yield ex


def _journal_gap_closes(engine, *, days: int = 7) -> int:
    """Closes the EXECUTOR recorded in the window, for a journal that has none.

    The two stores answer different questions, and the journal's emptiness has
    never been evidence about the other one. Returns 0 on any error: this
    exists to make a message more honest and must never be why /journal fails.
    """
    try:
        from datetime import datetime, timedelta
        from bot.compat import UTC
        cutoff = datetime.now(UTC) - timedelta(days=max(1, int(days)))
        n = 0
        for ex in _each_executor(engine):
            for pos in (getattr(ex, "closed_positions", None) or []):
                closed = getattr(pos, "closed_at", None)
                if closed is None:
                    continue
                if getattr(closed, "tzinfo", None) is None:
                    closed = closed.replace(tzinfo=UTC)
                if closed >= cutoff:
                    n += 1
        return n
    except Exception:
        return 0


class EngineOpsCommands:
    """Operator controls over the running engine. Host contract below; methods after."""

    if TYPE_CHECKING:
        # Provided by TelegramHandler, and ONLY declared here — declarations,
        # never bodies; tests/test_handler_mixins.py checks every name against
        # what the handler really defines.
        engine: RuneClawEngine

        async def _send(self, update: Update, text: str,
                        reply_markup=None, edit: bool = False) -> None: ...

        async def _reply(self, update: Update, text: str, reply_markup=None) -> None: ...

        async def _send_error(self, update: Update, command_name: str, exc: Exception) -> None: ...

        def _get_tg_id(self, update: Update) -> str: ...

        def _lang(self, update: Update) -> str: ...

        def _is_admin(self, update: Update) -> bool: ...

        def _caller_executor(self, update: Update): ...

    async def _cmd_accounts(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """Admin only: /accounts — live risk snapshot per trading account.

        One row per active account (operator + every per-user account): live
        equity, open positions, margin exposure, and circuit-breaker state. This
        is the per-user live observability view — what /users (a registration
        roster) does not show.
        """
        if not self._is_admin(update):
            await self._send(update, f"\U0001f512 {t('admin_only', self._lang(update))}")
            return
        try:
            rows = await self.engine.account_risk_overview()
        except Exception as exc:
            await self._send(update,
                             f"❌ Account overview failed: {_safe_exc_text(exc)}")
            return
        if not rows:
            await self._send(update, "📋 No active trading accounts.")
            return

        _dash = "─"
        lines = ["🛡 <b>ACCOUNT RISK</b>", "<pre>"]
        lines.append(f" {'ACCT':<10}{'EQUITY':>9}{'POS':>4}{'EXPOSURE':>10}{'CB':>4}{'STRK':>5}")
        lines.append(f" {_dash*10}{_dash*9}{_dash*4}{_dash*10}{_dash*4}{_dash*5}")
        n_live = n_halted = 0
        for r in rows:
            acct = r["account"][:10]
            if r.get("error"):
                lines.append(f" {acct:<10}  ERROR: {str(r['error'])[:24]}")
                continue
            eq = r["equity_usd"]
            eq_s = f"${eq:,.0f}" if eq is not None else "—"
            pos = r["open_positions"]
            exp = f"${r['exposure_usd']:,.0f}"
            cb = "⛔" if r["circuit_open"] else "·"
            strk = r["consecutive_losses"]
            if eq is not None:
                n_live += 1
            if r["circuit_open"]:
                n_halted += 1
            lines.append(f" {acct:<10}{eq_s:>9}{pos:>4}{exp:>10}{cb:>4}{strk:>5}")
        lines.append("</pre>")
        lines.append(
            f"\n<i>{len(rows)} account(s) · {n_live} with live equity · "
            f"{n_halted} halted (⛔)</i>")
        # ⚙ Live-performance governor — surface only accounts it is actively
        # throttling (REDUCE/PAUSE) so the size changes aren't invisible. Quiet
        # when nothing is throttled or the governor is off.
        throttled = []
        for r in rows:
            g = r.get("governor")
            if g and g.get("status") in ("REDUCE", "PAUSE"):
                throttled.append((r["account"], g))
        if throttled:
            lines.append("\n⚙ <b>Governor throttling:</b>")
            for acct, g in throttled:
                icon = "⏸" if g["status"] == "PAUSE" else "🔻"
                lines.append(
                    f"{icon} <code>{acct[:10]}</code> {g['status']} "
                    f"(×{g['multiplier']:.2f} · win {g['win_rate']*100:.0f}% · "
                    f"net ${g['net_pnl']:,.0f} · n={g['samples']})")
        # 🎛 Continuous equity throttle — same quiet-unless-acting rule.
        pf_throttled = []
        for r in rows:
            th = r.get("throttle")
            if th and th.get("status") == "THROTTLED":
                pf_throttled.append((r["account"], th))
        if pf_throttled:
            lines.append("\n🎛 <b>Equity throttle:</b>")
            for acct, th in pf_throttled:
                pf_s = f"{th['pf']:.2f}" if th.get("pf") is not None else "—"
                lines.append(
                    f"🔻 <code>{acct[:10]}</code> ×{th['multiplier']:.2f} "
                    f"(rolling PF {pf_s} · n={th['samples']})")
        # 🎚 Per-user margin caps (/setcap) — show only accounts that have one set.
        capped = [(r["account"], r["cap_usd"]) for r in rows
                  if r.get("cap_usd") and r["cap_usd"] > 0]
        if capped:
            lines.append("\n🎚 <b>Per-trade caps:</b> " + " · ".join(
                f"<code>{a[:10]}</code> ${c:,.0f}" for a, c in capped))
        await self._send(update, "\n".join(lines))

    def _persist_drawdown_override(self) -> None:
        """Flush the risk state so the admin live-drawdown override survives a
        restart (it is serialized into the risk state file and reloaded on
        boot). Best-effort — the in-memory override still applies this session
        even if the disk write fails."""
        try:
            self.engine.risk._save_state()
        except Exception as exc:
            system_log.debug("drawdown override persist failed: %s", exc)

    async def _cmd_drawdownlimit(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """Admin only: /drawdownlimit <pct | off | status> — temporarily override
        the LIVE max-drawdown breaker limit at runtime, without a redeploy.

        Purpose: after the account has drawn down past the default live cap the
        drawdown breaker keeps re-tripping (correctly). To keep testing live with
        tiny size, an admin can loosen the cap here. Bounded hard in config
        (never disables the breaker); 'off' reverts to the configured default.
        This does NOT itself resume — run /resume after loosening.
        """
        if not self._is_admin(update):
            await self._send(update, f"\U0001f512 {t('admin_only', self._lang(update))}")
            return
        from bot.config import RUNTIME, CONFIG as _CFG

        def _status_lines() -> list:
            # Two layers used to swallow this — `drawdown_status()` is itself
            # "best-effort; returns empty on any error", and this wrapped it in
            # another try/except — with one outcome: a heading and nothing
            # under it, on the control that decides how much real money is lost
            # before the bot halts. The renderer refuses to produce that.
            from bot.formatters.drawdown_card import render_drawdown_status
            try:
                st = self.engine.risk.drawdown_status()
            except Exception:
                st = {}
            return render_drawdown_status(st)

        args = ctx.args or []
        if not args or args[0].strip().lower() in ("status", "show"):
            lines = _status_lines()
            lines.append("")
            lines.append("Usage: <code>/drawdownlimit 15</code> · "
                         "<code>/drawdownlimit off</code>")
            lines.append(f"Bounded {RUNTIME.LIVE_DRAWDOWN_OVERRIDE_MIN:.0f}–"
                         f"{RUNTIME.LIVE_DRAWDOWN_OVERRIDE_MAX:.0f}%. "
                         "Loosening accepts larger real losses before the bot halts.")
            await self._send(update, "\n".join(lines))
            return

        raw = args[0].strip().lower()
        if raw in ("off", "none", "clear", "default", "reset"):
            RUNTIME.clear_live_drawdown_override()
            self._persist_drawdown_override()
            audit(system_log, "Live drawdown override cleared via /drawdownlimit",
                  action="drawdown_override", result="CLEARED")
            lines = ["🟢 Live drawdown override <b>cleared</b> — back to the "
                     f"configured {_CFG.risk.live_max_drawdown_pct:.1f}% cap.", ""]
            lines += _status_lines()
            await self._send(update, "\n".join(lines))
            return

        try:
            pct = float(raw)
        except ValueError:
            await self._send(update,
                "🔴 Value must be a number (percent), <code>off</code>, or "
                "<code>status</code>.")
            return

        lo, hi = RUNTIME.LIVE_DRAWDOWN_OVERRIDE_MIN, RUNTIME.LIVE_DRAWDOWN_OVERRIDE_MAX
        RUNTIME.live_drawdown_override_pct = pct
        applied = RUNTIME.live_drawdown_override_pct
        clamped = abs(applied - pct) > 1e-9
        self._persist_drawdown_override()
        audit(system_log, "Live drawdown override set via /drawdownlimit",
              action="drawdown_override", result="SET",
              data={"requested_pct": pct, "applied_pct": applied})
        lines = [f"🟠 Live drawdown limit override set to <b>{applied:.1f}%</b>."]
        if clamped:
            lines.append(f"   (clamped into the {lo:.0f}–{hi:.0f}% safe band)")
        lines += ["", *_status_lines(), "",
                  "⚠️ Real money is down — a looser cap means the bot tolerates "
                  "<b>more loss</b> before halting. Pair with tiny per-trade margin. "
                  "Run <code>/resume</code> to lift the current halt."]
        await self._send(update, "\n".join(lines))

    def _venue_status_lines(self) -> list:
        """Status block for /venue: active venue, source, per-venue
        credential readiness, and the open-position switch blocker."""
        from bot.core.venues import get_venue, get_venue_override, valid_venue_ids
        try:
            active = self.engine.live_executor._venue
        except Exception:
            active = get_venue()
        override = get_venue_override()
        lines = ["🏦 <b>Trading venue</b>",
                 f"• Active: <b>{active.display_name}</b> "
                 f"({active.quote}-margined perps)",
                 "• Source: " + ("runtime override (set via /venue)"
                                 if override else ".env VENUE setting")]
        for vid in valid_venue_ids():
            v = get_venue(vid)
            ready = v.has_operator_credentials(CONFIG.exchange)
            mark = "🟢 credentials ready" if ready else "⚪ no credentials"
            cur = " ← active" if v.id == active.id else ""
            lines.append(f"• {v.display_name}: {mark} · "
                         f"min order ${v.min_notional_usd:.0f}{cur}")
        try:
            open_count = len(self.engine.live_executor.open_positions)
            if open_count:
                # The COUNT is right — an unfilled limit order blocks a venue
                # switch just as a held position does, so it belongs in this
                # total. Only the wording was wrong: `open_positions` includes
                # pending_fill records, and calling them all "open position(s)"
                # tells the operator they hold something they may not.
                lines.append(f"• ⚠️ {open_count} open position(s) or unfilled "
                             "order(s) — switching is blocked until they are "
                             "closed or cancelled.")
        except Exception:
            pass
        return lines

    async def _cmd_venue(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """Admin only: /venue [bitget | hyperliquid | status] — show or switch
        the live trading venue at runtime. No .env edit, no restart: the
        switch preflights the target venue with a read-only balance call,
        hot-swaps the operator executor, and persists the choice across
        restarts. Blocked while positions are open. Per-user (/connect)
        executors always stay on Bitget.
        """
        if not self._is_admin(update):
            await self._send(update, f"\U0001f512 {t('admin_only', self._lang(update))}")
            return
        from bot.core.venues import (get_venue, set_venue_override,
                                     valid_venue_ids)

        args = ctx.args or []
        raw = (args[0].strip().lower() if args else "")
        if not raw or raw in ("status", "show"):
            lines = self._venue_status_lines()
            lines += ["", "Usage: <code>/venue hyperliquid</code> · "
                          "<code>/venue bitget</code>",
                      "Switching preflights the target venue and refuses "
                      "while positions are open."]
            await self._send(update, "\n".join(lines))
            return

        if raw in ("clear", "env", "default", "reset"):
            raw = getattr(CONFIG.exchange, "venue", "bitget").strip().lower()

        if raw not in valid_venue_ids():
            await self._send(update,
                             "🔴 Unknown venue <code>" + raw + "</code>. "
                             "Valid: " + ", ".join(
                                 f"<code>{v}</code>" for v in valid_venue_ids()))
            return

        target = get_venue(raw)
        active = self.engine.live_executor._venue
        if target.id == active.id:
            await self._send(update,
                             f"✅ Already trading on <b>{target.display_name}</b>.")
            return

        if not target.has_operator_credentials(CONFIG.exchange):
            await self._send(update,
                             f"🔴 <b>{target.display_name}</b> has no credentials "
                             f"configured.\n{target.missing_credentials_error(False)}")
            return

        # ── Preflight: read-only balance call against the TARGET venue ──
        acct: object = {}
        coin = target.balance_coin
        probe = None
        try:
            probe = target.create_exchange(CONFIG.exchange)
            try:
                bal = await probe.fetch_balance(target.balance_fetch_params())
            except Exception:
                bal = await probe.fetch_balance()
            # RC-2026-017, same shape as `fetch_balance`'s `free`/`used`:
            # `or 0` cannot tell an absent balance-coin entry from an empty
            # account, and the answer is printed under a GREEN "venue
            # switched" banner. The raw entry goes to the renderer, which does
            # the reading -- there is nowhere left here to mint a zero.
            acct = bal.get(coin, {}) if isinstance(bal, dict) else {}
        except Exception as exc:
            await self._send(update,
                             f"🔴 <b>Preflight failed</b> — {target.display_name} "
                             f"did not accept the credentials:\n<code>"
                             f"{_safe_exc_text(exc)}</code>\nVenue NOT switched — "
                             f"still on {active.display_name}.")
            return
        finally:
            if probe is not None:
                try:
                    await probe.close()
                except Exception:
                    pass

        result = await self.engine.switch_venue(target.id)
        if not result.startswith("switched"):
            await self._send(update, f"🔴 {result}")
            return
        # Switching back to the .env-configured venue clears the override so
        # .env stays the single source of truth when they agree.
        if target.id == getattr(CONFIG.exchange, "venue", "bitget").strip().lower():
            try:
                set_venue_override(None)
            except Exception:
                pass
        bal_line = venue_balance_line(acct, coin)
        await self._send(update,
                         f"🟢 <b>Venue switched: {active.display_name} → "
                         f"{target.display_name}</b>{bal_line}\n"
                         f"• Min order notional: ${target.min_notional_usd:.0f}\n"
                         f"• Persisted — survives restarts. "
                         f"<code>/venue {active.id}</code> switches back.\n"
                         f"• Per-user /connect accounts remain on Bitget.")

    async def _cmd_audit(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """Admin only: /audit — show the last nightly self-audit report;
        /audit run — trigger an audit now (background; the report arrives
        via the proactive monitor when the benchmark runs finish)."""
        if not self._is_admin(update):
            await self._send(update, f"\U0001f512 {t('admin_only', self._lang(update))}")
            return
        try:
            from bot.core.self_audit import SELF_AUDIT
            args = [a.lower() for a in (ctx.args or [])]
            if args and args[0] == "run":
                if SELF_AUDIT._running:
                    await self._send(update, "\U0001f9fe Self-audit already running.")
                    return
                import asyncio as _aio
                _aio.get_running_loop().create_task(SELF_AUDIT.run(self.engine))
                await self._send(
                    update,
                    "\U0001f9fe Self-audit started — evidence → LLM proposals "
                    "→ benchmark measurement. Report arrives here when the "
                    "runs finish (a few minutes). Nothing is auto-applied.")
                return
            report = SELF_AUDIT.last_report()
            if report:
                await self._send(update, report)
            else:
                await self._send(
                    update,
                    "\U0001f9fe No self-audit report yet. It runs nightly at "
                    f"{CONFIG.self_audit_hour_utc:02d}:00 UTC, or start one "
                    "now with /audit run.")
        except Exception as exc:
            await self._send(update,
                             f"Self-audit unavailable: {_safe_exc_text(exc)}")

    async def _cmd_shadow(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """Admin only: /shadow — the counterfactual shadow book scoreboard.
        Every gate-rejected idea trades on paper; a gate whose blocked
        trades net POSITIVE R is eating edge, negative is saving money."""
        if not self._is_admin(update):
            await self._send(update, f"\U0001f512 {t('admin_only', self._lang(update))}")
            return
        try:
            from bot.core.shadow_book import SHADOW_BOOK
            await self._send(update, SHADOW_BOOK.render_report())
        except Exception as exc:
            await self._send(update,
                             f"Shadow book unavailable: {_safe_exc_text(exc)}")

    @guard("leverage")
    async def _cmd_leverage(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """/leverage — the standard leverage, runtime-adjustable (admin).

        ``/leverage`` shows the standard and where it comes from;
        ``/leverage set <n>`` overrides it at runtime (clamped 1-20x, applies
        to every NEW position on every venue); ``/leverage reset`` returns to
        the configured default. Open positions keep the leverage they were
        opened with — the exchange cannot change it under an open position.
        """
        from bot.config import CONFIG as _CFG, RUNTIME as _RT
        args = [a.lower() for a in (ctx.args or [])]
        # NB3: a non-admin BYOK user manages their OWN standard leverage
        # (reduce-only vs the operator default); the GLOBAL standard stays
        # admin-only. A user setting/resetting only touches their per-user pref.
        if args and not self._is_admin(update):
            from bot.core import user_leverage_store as _lev_store
            from bot.core.leverage import resolve_user_leverage
            _tg_id = self._get_tg_id(update)
            if args[:1] == ["reset"]:
                _lev_store.clear(_tg_id)
                try:
                    _ex = self.engine._user_executors.get(str(_tg_id))
                    if _ex is not None:
                        _ex._user_leverage_pref = None
                except Exception:
                    pass
                await self._reply(update,
                    "⚙️ Your leverage preference is cleared — back to the "
                    f"operator standard (<b>{_CFG.exchange.default_leverage}x</b>).")
                return
            if args[:1] == ["set"] and len(args) >= 2:
                try:
                    _val = int(float(args[1].rstrip("x")))
                except ValueError:
                    await self._reply(update, "Usage: /leverage set <n>")
                    return
                _stored = _lev_store.set_pref(_tg_id, _val)
                if _stored is None:
                    await self._reply(update,
                        "Couldn't save that — use a whole number ≥ 1, "
                        "e.g. <code>/leverage set 3</code>.")
                    return
                try:
                    _ex = self.engine._user_executors.get(str(_tg_id))
                    if _ex is not None:
                        _ex._user_leverage_pref = _stored
                except Exception:
                    pass
                _eff = resolve_user_leverage(_stored, _CFG.exchange.default_leverage)
                _note = "" if _eff == _stored else \
                    f" (capped at the operator {_CFG.exchange.default_leverage}x)"
                await self._reply(update,
                    f"⚙️ Your standard leverage is now <b>{_eff}x</b>{_note}.\n"
                    "Applies to your NEW live positions; open positions keep "
                    "theirs. Reset with <code>/leverage reset</code>.")
                return
            await self._reply(update, "Usage: /leverage set <n> · /leverage reset")
            return
        if args[:1] == ["set"] and len(args) >= 2:
            try:
                val = int(float(args[1].rstrip("x")))
            except ValueError:
                await self._reply(update, "Usage: /leverage set <1-20>")
                return
            _RT.leverage_override = val
            applied = _RT.leverage_override
            note = "" if applied == val else f" (clamped from {val}x)"
            await self._reply(
                update,
                f"⚙️ Standard leverage set to <b>{applied}x</b>{note}.\n"
                "Applies to every NEW position on every venue. Open positions "
                "keep the leverage they were opened with.")
            return
        if args[:1] == ["reset"]:
            _RT.leverage_override = None
            await self._reply(
                update,
                f"⚙️ Standard leverage reset to the configured default "
                f"(<b>{_CFG.exchange.default_leverage}x</b>).")
            return
        override = _RT.leverage_override
        standard = override if override is not None else _CFG.exchange.default_leverage
        dyn = getattr(_CFG.exchange, "dynamic_leverage_enabled", False)
        lines = [
            "⚙️ <b>Leverage standard</b>",
            f"• Standard: <b>{standard}x</b> "
            + ("(runtime override)" if override is not None else "(configured default)"),
            f"• Dynamic vol scaling: {'ON — can only REDUCE below the standard' if dyn else 'OFF — uniform everywhere'}",
            "• Unconfirmed leverage: orders ABORT (fail-closed) unless "
            "LEVERAGE_FAIL_OPEN=1",
            "",
            "Change: <code>/leverage set 5</code> · reset: <code>/leverage reset</code>",
        ]
        await self._reply(update, "\n".join(lines))

    async def _cmd_parity(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """Admin only: /parity — the live↔backtest parity report, on demand
        from Telegram (previously shell-only: bot/backtest/parity.py). Live
        realized PF/fees vs the modeled benchmark, bucketed by signal type,
        setup, exit reason, AND asset class — the tool for dissecting the
        crypto bleed the /classpf card surfaced."""
        if not self._is_admin(update):
            await self._send(update, f"\U0001f512 {t('admin_only', self._lang(update))}")
            return
        import asyncio as _aio
        import html as _html
        from bot.backtest.parity import (_bucket_lines, _group,
                                         format_report, load_closed_trades,
                                         parity_summary)
        from bot.core.market_scanner import category_for_symbol

        path = self.engine.live_executor._closed_trades_file
        try:
            trades = await _aio.to_thread(load_closed_trades, path)
        except Exception as exc:
            await self._send(update,
                             f"🔴 Could not read closed trades "
                             f"({_safe_exc_text(exc, limit=120)})")
            return
        if not trades:
            await self._send(update, "📏 No closed live trades yet — the parity "
                                     "report needs at least a few closes.")
            return
        summary = await _aio.to_thread(parity_summary, trades,
                                       CONFIG.risk.commission_pct)
        report = format_report(summary)
        # Evidence extension: the per-asset-class bucket (classpf's view,
        # inside the parity framing). Filter never-filled records with the
        # SAME rule the headline stats use — previously this bucket counted
        # all raw records, so its totals disagreed with the summary (25 vs
        # 18) and win rates were diluted by zero-PnL non-fills.
        from bot.utils.close_reason import is_filled_close
        from bot.backtest.parity import _net
        filled = [tr for tr in trades
                  if is_filled_close(tr.get("close_reason"), _net(tr))]
        for tr in filled:
            tr["asset_class"] = category_for_symbol(tr.get("symbol", "") or "")
        cls_lines = _bucket_lines("By asset class",
                                  _group(filled, "asset_class"))
        if cls_lines:
            report += "\n" + "\n".join(cls_lines)
        text = f"📏 <b>Live ↔ backtest parity</b>\n<pre>{_html.escape(report)}</pre>"
        if len(text) > 4000:
            text = text[:3990] + "\n…</pre>"
        await self._send(update, text)

    @guard("admin")
    async def _cmd_autoconfirm(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """/autoconfirm — view or set auto-confirm threshold.

        Usage:
          /autoconfirm         — show current threshold
          /autoconfirm 0.75    — set to 75% confidence
          /autoconfirm off     — disable (set to 1.0)
        """

        from bot.config import RUNTIME
        args = ctx.args or []

        if not args:
            # Show current state
            threshold = CONFIG.auto_confirm_threshold
            if threshold >= 1.0:
                status = "\U0001f534 <b>OFF</b> — all trades require manual confirmation"
            else:
                status = f"\U0001f7e2 <b>ON</b> — trades with confidence \u2265 <b>{threshold*100:.0f}%</b> auto-execute"
            await self._send(update,
                f"\U0001f916 <b>Auto-Confirm Status</b>\n\n"
                f"{status}\n\n"
                f"<b>Commands:</b>\n"
                f"\u2022 <code>/autoconfirm 0.75</code> — auto-confirm \u2265 75%\n"
                f"\u2022 <code>/autoconfirm off</code> — disable\n"
                f"\u2022 <code>/autoconfirm 0.60</code> — aggressive (60%+)")
            return

        arg = args[0].lower()
        if arg in ("off", "disable", "manual"):
            # Use RUNTIME to override the frozen CONFIG value
            RUNTIME.auto_confirm_threshold = 1.0
            audit(system_log, "Auto-confirm DISABLED via /autoconfirm off",
                  action="autoconfirm", result="DISABLED",
                  data={"user": self._get_tg_id(update)})
            await self._send(update,
                "\U0001f534 <b>Auto-Confirm DISABLED</b>\n\n"
                "All trades now require manual confirmation.")
            return

        try:
            new_threshold = float(arg)
            if new_threshold < 0.5 or new_threshold > 1.0:
                await self._send(update,
                    "\u274c Threshold must be between 0.50 and 1.00\n"
                    "Example: <code>/autoconfirm 0.75</code>")
                return
            RUNTIME.auto_confirm_threshold = new_threshold
            audit(system_log, f"Auto-confirm threshold set to {new_threshold}",
                  action="autoconfirm", result="SET",
                  data={"user": self._get_tg_id(update), "threshold": new_threshold})
            await self._send(update,
                f"\U0001f916 <b>Auto-Confirm Updated</b>\n\n"
                f"Threshold: <b>{new_threshold*100:.0f}%</b>\n"
                f"Trades with confidence \u2265 {new_threshold*100:.0f}% will auto-execute.\n"
                f"Lower confidence trades still require manual confirmation.")
        except ValueError:
            await self._send(update,
                "\u274c Invalid value. Use a number (0.50-1.00) or 'off'.\n"
                "Example: <code>/autoconfirm 0.75</code>")

    @guard("admin")
    async def _cmd_forcescan(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """/forcescan — force immediate scan bypassing cooldown and pending gates."""

        await self._send(update,
            "\U0001f50d <b>Force scan starting...</b>\n"
            "Clearing pending ideas, bypassing cooldown.")

        try:
            result = await self.engine.force_scan()
        except Exception as exc:
            await self._send(update,
                f"\u274c <b>Force scan failed:</b> {_safe_exc_text(exc)}")
            return

        if result.get("error"):
            await self._send(update,
                f"\u274c <b>Scan error:</b> {result['error']}")
            return

        lines = [
            "\u2705 <b>Force Scan Complete</b>",
            "",
            f"\U0001f4e1 Signals found: <b>{result.get('signals', 0)}</b>",
            f"\U0001f4a1 Ideas generated: <b>{result.get('ideas', 0)}</b>",
            f"\U0001f916 Auto-confirmed: <b>{result.get('auto_confirmed', 0)}</b>",
            f"\u23f3 Pending confirmation: <b>{result.get('pending', 0)}</b>",
        ]
        if result.get('cleared_pending', 0) > 0:
            lines.append(f"\U0001f9f9 Cleared old pending: <b>{result['cleared_pending']}</b>")

        await self._send(update, "\n".join(lines))

    async def _cmd_attribution(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Show trade signal attribution — which indicators contribute to wins."""
        if not self._is_admin(update):
            return
        chat_id = update.effective_chat.id

        try:
            from bot.core.metrics import MetricsEngine
            trades = self.engine.portfolio._history
            _me = MetricsEngine()
            attribution = _me.compute_attribution(trades)

            if not attribution:
                await context.bot.send_message(
                    chat_id=chat_id,
                    text="\u26a0\ufe0f No signal attribution data yet. Need closed trades with signal tracking.",
                )
                return

            lines = [
                "\U0001f4ca <b>Signal Attribution Report</b>",
                "\u2500" * 28,
                "",
            ]

            # Sort by edge score
            sorted_signals = sorted(attribution.items(), key=lambda x: x[1].get("edge_score", 0), reverse=True)

            for name, stats in sorted_signals[:15]:
                wr = stats.get("win_rate", 0) * 100
                total = stats.get("total", 0)
                avg = stats.get("avg_pnl", 0)
                edge = stats.get("edge_score", 0)
                emoji = "\u2705" if wr >= 55 else "\u26a0\ufe0f" if wr >= 45 else "\u274c"
                lines.append(f"{emoji} <b>{name}</b>: {wr:.0f}% WR ({total} trades) avg=${avg:.2f} edge={edge:.1f}")

            lines.extend(["", "\u2500" * 28, "\U0001f43e RUNECLAW Attribution Engine"])

            await context.bot.send_message(
                chat_id=chat_id,
                text="\n".join(lines),
                parse_mode="HTML",
            )
        except Exception as exc:
            await self._send_error(update, "signal attribution", exc)

    async def _cmd_equitycurve(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Show equity curve circuit breaker status."""
        if not self._is_admin(update):
            return
        chat_id = update.effective_chat.id

        try:
            risk = self.engine.risk
            eq_mult = risk.equity_curve_size_multiplier
            in_recovery = risk.in_drawdown_recovery

            if eq_mult <= 0:
                status = "\U0001f6d1 PAUSED — equity below 2\u03c3 of MA"
                status_emoji = "\U0001f6d1"
            elif eq_mult < 1.0:
                status = f"\u26a0\ufe0f HALVED — equity below MA (sizing at {eq_mult:.0%})"
                status_emoji = "\u26a0\ufe0f"
            else:
                status = "\u2705 HEALTHY — equity above MA"
                status_emoji = "\u2705"

            # "equity above MA" ASSERTS A COMPARISON. equity_curve_size_multiplier
            # returns 1.0 whenever neither breaker flag is set, and those flags
            # are only ever written inside `if len(self._equity_history) >=
            # ma_period:` — so with fewer snapshots than the period, which is
            # the state after every restart, the verdict above is the initial
            # value of two booleans and no moving average exists to be above.
            # The card printed it directly over its own "Equity snapshots: 0".
            _snaps = len(risk._equity_history)
            _ma_period = int(CONFIG.risk.equity_curve_ma_period or 0)
            if _snaps < _ma_period:
                status = ("\u26aa NOT YET MEASURED — "
                          f"{_snaps} of {_ma_period} snapshots")
                status_emoji = "\u26aa"

            lines = [
                "\U0001f4c8 <b>Equity Curve Health</b>",
                "\u2500" * 28,
                "",
                f"Status: {status}",
                f"Size multiplier: <code>{eq_mult:.0%}</code>",
                f"Equity snapshots: <code>{_snaps}</code>",
                f"MA period: <code>{_ma_period}</code>",
                "",
            ]
            if _snaps < _ma_period:
                lines.insert(4, "<i>The moving average needs a full window "
                                "before this gate can say anything. Sizing is "
                                "at 100% because nothing has told it "
                                "otherwise — not because the curve was "
                                "checked and found healthy.</i>\n")
            _dr_str = "<b>ACTIVE</b> ⚠️" if in_recovery else "Inactive ✅"
            lines.append(f"Drawdown recovery: {_dr_str}")

            if in_recovery:
                lines.append(f"  Min confidence: <code>{CONFIG.risk.drawdown_recovery_conf_min}</code>")
                lines.append(f"  Size multiplier: <code>{CONFIG.risk.drawdown_recovery_size_mult:.0%}</code>")

            lines.extend(["", "\u2500" * 28, "\U0001f43e RUNECLAW Risk Management"])

            await context.bot.send_message(
                chat_id=chat_id,
                text="\n".join(lines),
                parse_mode="HTML",
            )
        except Exception as exc:
            await self._send_error(update, "the equity curve report", exc)

    async def _cmd_crossasset(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Show cross-asset correlation context."""
        if not self._is_admin(update):
            return
        chat_id = update.effective_chat.id
        try:
            ctx = self.engine.cross_asset.get_context(force=True)
            lines = [
                "\U0001f310 <b>Cross-Asset Context</b>",
                "\u2500" * 28,
                "",
                f"BTC Dominance: <b>{ctx.btc_dominance_trend}</b> ({ctx.btc_dominance_change_1h:+.2f}%)",
                f"ETH/BTC: <b>{ctx.eth_btc_trend}</b> (ratio: {ctx.eth_btc_ratio:.6f})",
                f"Alt-BTC Correlation: <code>{ctx.alt_correlation:.2f}</code>",
                f"Market Regime: <b>{ctx.market_regime.upper()}</b>",
                "",
                f"Confidence adj: <code>{ctx.confidence_adjustment:+.3f}</code>",
                f"Size multiplier: <code>{ctx.size_multiplier:.0%}</code>",
                "",
                f"\U0001f4dd {ctx.description}",
                "",
                "\u2500" * 28,
                "\U0001f43e RUNECLAW Cross-Asset Engine",
            ]
            await context.bot.send_message(chat_id=chat_id, text="\n".join(lines), parse_mode="HTML")
        except Exception as exc:
            await self._send_error(update, "the cross-asset context", exc)

    async def _cmd_slippage(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Show slippage statistics."""
        if not self._is_admin(update):
            return
        chat_id = update.effective_chat.id
        try:
            all_stats = self.engine.slippage.get_all_stats()
            if not all_stats:
                await context.bot.send_message(chat_id=chat_id, text="\u26a0\ufe0f No slippage data recorded yet.")
                return

            lines = [
                "\U0001f4ca <b>Slippage Report</b>",
                "\u2500" * 28,
                "",
            ]

            total_lost = 0
            for symbol, stats in sorted(all_stats.items(), key=lambda x: x[1].total_slippage_usd, reverse=True)[:10]:
                lines.append(
                    f"<b>{symbol}</b>: mean={stats.mean_slippage_pct:.3f}% "
                    f"p95={stats.p95_slippage_pct:.3f}% "
                    f"({stats.total_trades} fills, ${stats.total_slippage_usd:.2f} lost)"
                )
                total_lost += stats.total_slippage_usd

            lines.extend([
                "",
                f"\U0001f4b8 Total slippage cost: <b>${total_lost:.2f}</b>",
                "",
                "\u2500" * 28,
                "\U0001f43e RUNECLAW Execution Quality",
            ])

            await context.bot.send_message(chat_id=chat_id, text="\n".join(lines), parse_mode="HTML")
        except Exception as exc:
            await self._send_error(update, "the slippage report", exc)

    @guard("admin")
    async def _cmd_golive(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """/golive — enable live trading with double confirmation."""

        args = ctx.args or []
        if args and args[0].upper() == "OFF":
            # Disable live mode
            from bot.config import RUNTIME
            RUNTIME.live_mode = False
            from bot.compliance.compliance_engine import Permission
            self.engine.compliance_profile.permissions.discard(Permission.LIVE_TRADE)
            audit(system_log, "LIVE TRADING DISABLED via /golive OFF",
                  action="golive", result="DISABLED",
                  data={"user": self._get_tg_id(update)})
            await self._send(update,
                "\U0001f534 <b>LIVE TRADING DISABLED</b>\n\n"
                "Reverted to paper-trade mode.\n"
                "Use <code>/golive CONFIRM</code> to re-enable.")
            return

        # Readiness is read BEFORE anything is armed, and shown on the preview
        # as well as the confirm. The preview used to advertise "real order
        # execution on Bitget" and list leverage limits without checking one
        # precondition.
        from bot.core.live_readiness import from_engine as _live_readiness
        from bot.core.live_readiness import render as _render_readiness
        readiness = _live_readiness(self.engine)

        if not args or args[0].upper() != "CONFIRM":
            _limits = (
                f"\nIf armed, the limits are:\n"
                f"\u2022 Max {CONFIG.risk.max_open_positions} concurrent positions\n"
                f"\u2022 Max {CONFIG.risk.max_symbol_exposure_pct:.0f}% per symbol\n"
                f"\u2022 USDT-M perpetual futures\n"
                f"\u2022 Default {CONFIG.exchange.default_leverage}x leverage\n")
            await self._send(update,
                "\u26a0\ufe0f <b>LIVE TRADING ACTIVATION</b>\n\n"
                + _render_readiness(readiness) + "\n"
                + _limits
                + ("\nTo confirm, type:\n<code>/golive CONFIRM</code>"
                   if readiness["can_execute"] else
                   "\n<i>Clear the blockers above first \u2014 "
                   "<code>/golive CONFIRM</code> will refuse until they are "
                   "gone.</i>"))
            return

        # REFUSE to arm when no order could execute anyway. The old code set
        # RUNTIME.live_mode, granted Permission.LIVE_TRADE and replied "Real
        # orders will execute on Bitget" \u2014 on a default install SIMULATION_MODE,
        # the empty chat allow-list and the absent credentials each made that
        # false on their own. Granting a real authorization on a false premise
        # is worse than the wrong message.
        if not readiness["can_execute"]:
            audit(system_log, "LIVE TRADING REFUSED via /golive \u2014 preconditions",
                  action="golive", result="REFUSED",
                  data={"user": self._get_tg_id(update),
                        "blockers": [b["code"] for b in readiness["blockers"]]})
            await self._send(update, _render_readiness(readiness)
                + "\n\n<i>Nothing was armed and no permission was granted.</i>")
            return

        # Enable live mode via RuntimeState (CONFIG is frozen)
        from bot.config import RUNTIME
        RUNTIME.live_mode = True

        # Grant LIVE_TRADE permission on the engine's compliance profile
        # so Lock 1 passes. This is the explicit human authorization.
        from bot.compliance.compliance_engine import Permission
        self.engine.compliance_profile.permissions.add(Permission.LIVE_TRADE)

        audit(system_log, "LIVE TRADING ENABLED via /golive",
              action="golive", result="ENABLED",
              data={"user": self._get_tg_id(update),
                    "unverified": [u["code"] for u in readiness["unverified"]]})
        await self._send(update,
            _render_readiness(readiness) + "\n\n"
            f"Limits: {CONFIG.risk.max_open_positions} positions, "
            f"{CONFIG.exchange.default_leverage}x leverage.\n\n"
            "\u2022 <code>/livebalance</code> — check USDT balance\n"
            "\u2022 <code>/livepositions</code> — view open positions\n"
            "\u2022 <code>/liveclose &lt;id&gt;</code> — close a position\n"
            "\u2022 <code>/golive OFF</code> — disable live mode")

    @guard("admin")
    async def _cmd_liveclose(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """/liveclose <trade_id> — manually close a live position."""
        args = ctx.args or []
        if not args:
            await self._send(update, "Usage: <code>/liveclose TRADE_ID</code>")
            return
        trade_id = args[0]
        # Per-user isolation: close via the CALLER's executor, same as
        # _cmd_livepositions and the pos_close button callback (resolves to
        # the shared operator executor when PER_USER_LIVE_ENABLED is off --
        # byte-identical default) so a user can only ever close their OWN
        # account's positions.
        executor = self._caller_executor(update)
        if executor is None:
            await self._send(update,
                "\U0001f512 <b>Access denied</b>\n\nNo linked exchange account for this user.")
            return
        result = await executor.close_position(trade_id, "manual_telegram")
        await self._send(update, f"\U0001f510 {result}")

    async def _cmd_gates(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """/gates — per-gate pass/fail/skip telemetry (threshold tuning evidence)."""
        if not self._is_admin(update):
            return
        stats = self.engine.risk.gate_stats()
        if not stats:
            await self._send(update, "No gate evaluations recorded yet this session.")
            return
        lines = ["\U0001f6a6 <b>Risk Gate Telemetry</b>", "\u2500" * 28, ""]
        for name, rec in stats.items():
            total = rec["passed"] + rec["failed"] + rec["skipped"]
            if total == 0:
                continue
            fail_pct = rec["failed"] / total * 100
            lines.append(
                f"<b>{name}</b>: {rec['passed']}P/{rec['failed']}F/{rec['skipped']}S"
                f"  ({fail_pct:.0f}% fail)")
        lines += ["", "Skips = fail-open (no data). High skip rates mean a gate",
                  "is not really running; high fail rates mean it may be too strict."]
        await self._send(update, "\n".join(lines))

    async def _cmd_journal(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """Show weekly trade journal review."""
        if not self._is_admin(update):
            return
        chat_id = update.effective_chat.id
        try:
            review = self.engine.journal.get_weekly_review()

            if review.get("trades", 0) == 0:
                # "No trades in the last 7 days" is a claim about TRADING,
                # derived from the emptiness of the JOURNAL. Two different
                # stores: the journal is fed by _on_live_position_closed and
                # persists to data/trade_journal.json, while /portfolio counts
                # the executor's closed_positions. The operator saw this pair
                # on one screen -- /journal saying no trades in a week beside
                # /portfolio saying 104 -- and only one was talking about
                # trades.
                #
                # The bot holds both, so it can check rather than assert.
                #
                # Deliberately NOT back-filled. An entry carries the regime
                # and strategy that were live at the close; those cannot be
                # recovered afterwards, and inventing them would put
                # fabricated context under a heading that reads as recorded
                # fact. Explain the gap, never paper over it.
                _gap = _journal_gap_closes(self.engine, days=7)
                if _gap > 0:
                    await ctx.bot.send_message(
                        chat_id=chat_id, parse_mode="HTML",
                        text=(
                            "\u26a0\ufe0f <b>The journal has no entries for the "
                            "last 7 days</b> \u2014 but "
                            f"<b>{_gap}</b> position(s) closed in that "
                            "window.\n\n"
                            "That is a recording gap, not a quiet week. The "
                            "journal is written when a position closes, so "
                            "closes from before it was wired \u2014 or while "
                            "the bot was down \u2014 have no entry.\n\n"
                            "<i>Not back-filled on purpose: an entry carries "
                            "the regime and strategy that were live at the "
                            "close, and those cannot be recovered later. "
                            "<code>/portfolio</code> and "
                            "<code>/performance</code> read the executor "
                            "directly and cover the whole history.</i>"))
                    return
                await ctx.bot.send_message(
                    chat_id=chat_id,
                    text="\u26a0\ufe0f No trades in the last 7 days.")
                return

            lines = [
                "\U0001f4d3 <b>Weekly Trade Review</b>",
                "\u2500" * 28,
                "",
                f"Period: {review['period']}",
                f"Trades: <b>{review['trades']}</b> ({review['wins']}W / {review['losses']}L)",
                f"Win Rate: <b>{review['win_rate']:.0f}%</b>",
                f"Total PnL: <b>${review['total_pnl']:+.2f}</b>",
                f"Avg R-Multiple: <code>{review['avg_r_multiple']:+.2f}</code>",
                f"Avg Hold: <code>{review['avg_holding_hours']:.1f}h</code>",
                "",
                f"\U0001f3c6 Best: {review['best_trade']['symbol']} ${review['best_trade']['pnl']:+.2f} ({review['best_trade']['r']:.1f}R)",
                f"\U0001f4a9 Worst: {review['worst_trade']['symbol']} ${review['worst_trade']['pnl']:+.2f} ({review['worst_trade']['r']:.1f}R)",
            ]

            # Top lessons
            if review.get("top_lessons"):
                lines.extend(["", "<b>Recurring Lessons:</b>"])
                for lesson, count in review["top_lessons"][:3]:
                    lines.append(f"  \u2022 {lesson} ({count}x)")

            lines.extend(["", "\u2500" * 28, "\U0001f43e RUNECLAW Trade Journal"])

            await ctx.bot.send_message(chat_id=chat_id, text="\n".join(lines), parse_mode="HTML")
        except Exception as exc:
            await self._send_error(update, "the trade journal", exc)

    async def _cmd_close_all(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """Admin only: /closeall — flatten all open positions on EVERY account.

        Two-step (TG-2b): this shows a confirm keyboard; the closeall_confirm
        callback runs the actual flatten. /emergency_stop already confirmed;
        /closeall used to flatten immediately, so a fat-finger market-closed
        every operator and per-user position with no undo."""
        if not self._is_admin(update):
            await self._send(update, "🔒 Admin only.")
            return
        if not CONFIG.is_live() or not hasattr(self.engine, 'live_executor'):
            await self._send(update, "No live executor available.")
            return
        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("⛔ Confirm — flatten ALL", callback_data="closeall_confirm"),
            InlineKeyboardButton("↩️ Cancel", callback_data="closeall_cancel"),
        ]])
        await self._send(update,
            "⚠️ <b>Flatten ALL open positions on EVERY account?</b>\n"
            "This market-closes every operator and per-user position immediately "
            "— it cannot be undone.",
            reply_markup=kb)

    async def _flatten_all_accounts(self, update: Update) -> None:
        """The actual /closeall flatten, run only after the confirm button."""
        await self._send(update, "⏳ Closing all positions (every account)...", edit=True)
        try:
            # Flatten EVERY account (operator + per-user), not just the operator.
            accounts = await self.engine.flatten_all_positions(reason="admin_closeall")
            if not accounts:
                await self._send(update, "No live accounts to close.", edit=True)
                return
            lines = ["⛔ <b>Close All Results:</b>"]
            for acct in accounts:
                lines.append(f"\n<b>{acct['account']}:</b>")
                lines.extend(f"• {m[:120]}" for m in acct["messages"][:10])
            await self._send(update, "\n".join(lines), edit=True)
        except Exception as exc:
            await self._send(update,
                             f"❌ Close all failed: {_safe_exc_text(exc)}",
                             edit=True)

    async def _cmd_flags(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """Show which deep-audit opt-in flags are ON/OFF (admin)."""
        if not self._is_admin(update):
            return
        chat_id = update.effective_chat.id
        try:
            from bot.core.flag_status import format_flag_report
            await ctx.bot.send_message(chat_id=chat_id, text=format_flag_report(),
                                       parse_mode="HTML")
        except Exception as exc:
            await self._send_error(update, "the feature flags", exc)

    def _representative_regime(self) -> str:
        """A real market regime to display for /strategy. The risk engine's
        _current_regime stays "UNKNOWN" unless REGIME_SIZING_ENABLED (the
        regime→sizing bridge is gated), but the analyzer detects a regime per
        symbol regardless. Return the most common real regime the analyzer
        currently sees, falling back to the risk engine's value."""
        try:
            regimes = getattr(getattr(self.engine, "analyzer", None), "_current_regimes", None)
            if regimes:
                from collections import Counter
                vals = [str(getattr(r, "value", r)) for r in regimes.values()
                        if str(getattr(r, "value", r) or "").upper() not in ("", "UNKNOWN")]
                if vals:
                    return Counter(vals).most_common(1)[0][0]
        except Exception:
            pass
        return str(getattr(self.engine.risk, "_current_regime", "UNKNOWN") or "UNKNOWN")

    async def _cmd_strategy(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """Show active strategy and regime-based routing."""
        if not self._is_admin(update):
            return
        chat_id = update.effective_chat.id
        try:
            from bot.core.strategy_router import select_strategy
            regime = self._representative_regime()
            vol_state = self.engine.risk._current_vol_state
            profile = select_strategy(regime, vol_state)

            lines = [
                "\U0001f3af <b>Strategy Router</b>",
                "\u2500" * 28,
                "",
                f"Current Regime: <b>{regime}</b>",
                f"Volatility: <b>{vol_state}</b>",
                "",
                f"Active Strategy: <b>{profile.name}</b>",
                f"Type: <code>{profile.strategy_type}</code>",
                f"SL: <code>{profile.sl_atr_mult}x ATR</code>",
                f"TP: <code>{profile.tp_atr_mult}x ATR</code>",
                f"Size: <code>{profile.size_multiplier:.0%}</code>",
                f"Min Confidence: <code>{profile.min_confidence:.0%}</code>",
                "",
                f"\U0001f4dd {profile.description}",
                "",
                "\u2500" * 28,
                "\U0001f43e RUNECLAW Strategy Engine",
            ]

            await ctx.bot.send_message(chat_id=chat_id, text="\n".join(lines), parse_mode="HTML")
        except Exception as exc:
            await self._send_error(update, "the strategy settings", exc)
