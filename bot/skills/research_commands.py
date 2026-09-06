"""The research-and-tuning command group — a slice out of the handler.

`/backtest`, `/walkforward`, `/run`, `/playbook`, `/learn`, `/proposals`,
`/optimize`, and the operator's `/montecarlo` and `/calibration`. Most are
one-line dispatchers into the registry behind `@guard(...)`; the two
operator commands carry their own admin gate. Their behaviour lives in the
skills they dispatch (`test_skill_dispatch`, `test_tier_gate_coverage`);
`tests/test_handler_mixins.py` holds this class to the split's rules.

The tier gate is the reason this group is worth a careful move: four of its
dispatches are paid features, and `scripts/guard_lint.py`'s `tier-gate` rule
and `tests/test_tier_gate_coverage.py` read the files these methods live in.
Both follow the class's MRO now, so a dispatch that moves here stays gated.
"""
from __future__ import annotations

import html
from typing import TYPE_CHECKING

from telegram import Update
from telegram.ext import ContextTypes

from bot.config import CONFIG
from bot.skills.command_guard import guard
from bot.utils.exc_text import _safe_exc_text
from bot.utils.logger import system_log

if TYPE_CHECKING:
    from bot.core.engine import RuneClawEngine
    from bot.skills.skill_registry import SkillRegistry
    from bot.utils.user_store import UserStore


class ResearchCommands:
    """Backtests, tuning and the learning loop. Host contract below."""

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

        async def _guard(self, update: Update, command: str = "", ctx=None) -> bool: ...

        def _is_admin(self, update: Update) -> bool: ...

        def _get_tg_id(self, update: Update) -> str: ...

        async def _token_gate_blocks(self, update: Update, mode: str,
                                     feature: str = "premium_scan") -> bool: ...

    async def _cmd_montecarlo(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Run Monte Carlo risk simulation on trade history."""
        if not self._is_admin(update):
            return
        chat_id = update.effective_chat.id

        try:
            from bot.core.monte_carlo import run_monte_carlo
            trades = self.engine.portfolio._history
            closed_pnls = [t.pnl for t in trades if t.closed_at is not None]

            if len(closed_pnls) < 5:
                await context.bot.send_message(
                    chat_id=chat_id,
                    text="\u26a0\ufe0f Need at least 5 closed trades for Monte Carlo simulation.",
                )
                return

            equity = self.engine.portfolio.balance
            result = run_monte_carlo(closed_pnls, starting_equity=equity, num_simulations=5000)

            if result is None:
                await context.bot.send_message(chat_id=chat_id, text="\u274c Monte Carlo simulation failed.")
                return

            lines = [
                "\U0001f3b2 <b>Monte Carlo Risk Simulation</b>",
                "\u2500" * 28,
                "",
                f"\U0001f4ca <b>{result.num_simulations:,} simulations</b> on <b>{result.num_trades}</b> trades",
                "",
                "<b>Max Drawdown Distribution:</b>",
                f"  50th: <code>{result.dd_50th:.1f}%</code>",
                f"  75th: <code>{result.dd_75th:.1f}%</code>",
                f"  90th: <code>{result.dd_90th:.1f}%</code>",
                f"  95th: <code>{result.dd_95th:.1f}%</code> \u2190 key metric",
                f"  99th: <code>{result.dd_99th:.1f}%</code>",
                "",
                "<b>Return Distribution:</b>",
                f"  Worst 5%:  <code>{result.return_5th:+.1f}%</code>",
                f"  Median:    <code>{result.return_median:+.1f}%</code>",
                f"  Best 5%:   <code>{result.return_95th:+.1f}%</code>",
                "",
                f"\U0001f480 Probability of ruin: <code>{result.probability_of_ruin:.1%}</code>",
                f"\u26a0\ufe0f Risk rating: <b>{result.risk_rating}</b>",
            ]
            if result.recommended_size_mult < 1.0:
                lines.append(f"\U0001f4c9 Suggested size reduction: <b>{result.recommended_size_mult:.0%}</b>")
            else:
                lines.append("\u2705 Current sizing is within acceptable risk bounds")

            lines.extend(["", "\u2500" * 28, "\U0001f43e RUNECLAW Monte Carlo Engine"])

            await context.bot.send_message(
                chat_id=chat_id,
                text="\n".join(lines),
                parse_mode="HTML",
            )
        except Exception as exc:
            await self._send_error(update, "the Monte Carlo simulation", exc)

    @guard("admin")
    async def _cmd_calibration(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """/calibration [refit] — show the learning overlays (confidence
        calibration + per-setup expectancy), or refit/reload them from closed-
        trade history. Each is applied only when its flag is enabled."""
        args = ctx.args or []
        do_refit = bool(args) and args[0].lower() in ("refit", "fit", "rebuild", "reload")
        try:
            from bot.learning.confidence_calibration import refit_and_save, ConfidenceCalibrator
            from bot.learning.setup_expectancy import get_setup_expectancy
            from bot.learning import voter_weights as _vw
            if do_refit:
                cal = refit_and_save()
                if hasattr(self.engine, "analyzer") and hasattr(self.engine.analyzer, "refresh_calibrator"):
                    self.engine.analyzer.refresh_calibrator()
                exp = get_setup_expectancy(reload=True)
                vw = _vw.refit_and_save()
                action = "Refit/reload complete.\n\n"
            else:
                cal = ConfidenceCalibrator.load() or ConfidenceCalibrator()
                exp = get_setup_expectancy()
                vw = _vw.VoterWeightLearner.load() or _vw.VoterWeightLearner()
                action = ""
        except Exception as exc:
            await self._send(update, f"🔴 Learning overlay error: {_safe_exc_text(exc)}")
            return

        cal_on = getattr(CONFIG.analyzer, "confidence_calibration_enabled", False)
        exp_on = getattr(CONFIG.analyzer, "setup_expectancy_enabled", False)
        vw_on = getattr(CONFIG.analyzer, "voter_weight_learning_enabled", False)
        _mode = lambda on: "APPLIED (live)" if on else "SHADOW (logged, not applied)"
        await self._send(update,
            "<b>Learning overlays</b>\n\n"
            f"{action}"
            f"<b>Confidence calibration</b> — <code>{_mode(cal_on)}</code>\n"
            f"<code>{html.escape(cal.summary())}</code>\n\n"
            f"<b>Per-setup expectancy</b> — <code>{_mode(exp_on)}</code>\n"
            f"<code>{html.escape(exp.summary())}</code>\n\n"
            f"<b>Voter-weight learning</b> — <code>{_mode(vw_on)}</code>\n"
            f"<code>{html.escape(vw.summary())}</code>\n\n"
            "<i>Refit/reload from history: </i><code>/calibration refit</code>")

    async def _cmd_readiness(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """/readiness — is the learning loop validated enough to apply?"""
        if not self._is_admin(update):
            return
        try:
            from bot.learning.readiness import assess_readiness, render_report
            await self._send(update, render_report(assess_readiness()))
        except Exception as exc:
            await self._send(update, f"⚠️ Readiness assessment failed: {_safe_exc_text(exc, limit=160)}")

    @guard("backtest")
    async def _cmd_backtest(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if await self._token_gate_blocks(update, "backtest", "run_backtest"):
            return
        args = ctx.args or []
        bars = args[0] if args else "720"
        seed = args[1] if len(args) > 1 else "42"
        await self._send(update,
            f"\u23f3 <i>Backtest running  \u2022  {bars} bars  \u2022  seed {seed}</i>")
        result = await self.registry.dispatch("run_backtest",
            self.engine, bars=bars, seed=seed)
        await self._send(update, result)

    @guard("walkforward")
    async def _cmd_walkforward(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if await self._token_gate_blocks(update, "walk-forward", "walk_forward"):
            return
        args = ctx.args or []
        bars = args[0] if args else "1440"
        folds = args[1] if len(args) > 1 else "3"
        await self._send(update, "\u23f3 <i>Walk-forward running...</i>")
        result = await self.registry.dispatch("walk_forward",
            self.engine, bars=bars, folds=folds)
        await self._send(update, result)

    @guard("run")
    async def _cmd_run(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        strategy = " ".join(ctx.args) if ctx.args else ""
        if strategy:
            await self._send(update,
                f"\u23f3 <i>Running {html.escape(strategy)}...</i>")
        result = await self.registry.dispatch("run_strategy",
            self.engine, strategy=strategy)
        await self._send(update, result)

    @guard("playbook")
    async def _cmd_playbook(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """GetClaw-style full system playbook briefing."""
        await self._send(update, "📋 <i>Assembling playbook...</i>")
        try:
            result = await self.registry.dispatch("playbook", self.engine, user_id=self._get_tg_id(update))
            await self._send(update, result)
        except Exception as exc:
            system_log.error(f"Playbook error: {exc}")
            await self._send(update, f"🔴 <b>Playbook error:</b> <code>{_safe_exc_text(exc)}</code>")

    @guard("learn")
    async def _cmd_learn(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if await self._token_gate_blocks(update, "learning", "learning"):
            return
        result = await self.registry.dispatch("learning", self.engine)
        await self._send(update, result)

    @guard("proposals")
    async def _cmd_proposals(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        result = await self.registry.dispatch("proposals", self.engine)
        await self._send(update, result)

    @guard("optimize")
    async def _cmd_optimize(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if await self._token_gate_blocks(update, "optimize", "optimize"):
            return
        result = await self.registry.dispatch("optimize", self.engine)
        await self._send(update, result)
