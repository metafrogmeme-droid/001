"""The LLM command group — the third slice out of the handler.

`/settier`, `/ultra`, `/setllm`, `/llmstatus`, `/llmreset`, `/llmtiers` and
the tier card `/dashboard` borrows. Operator commands over the model routing:
which provider answers each tier, whether a key is bound, whether the brain
has been seen working. Their behaviour is covered where it always was
(`test_telegram_commands`, `test_brain_state_is_not_inferred`,
`test_llm_tier_card`, `test_dashboard_llm_tiers_are_resolved`);
`tests/test_handler_mixins.py` holds every mixin to the split's rules.

A mixin, not a leaf, for the reason the Guardian group gives: each method
reads `self.engine`, gates on `self._is_admin` and answers through
`self._send`. What is deliberately NOT here is anything the chat brain
reads — `_llm_chat` and `_chat_tools_for` stay in the handler because
eighteen suites monkeypatch `CONFIG`, `llm_complete` and friends on THAT
module. These commands read `CONFIG` from this module, and the one test
that patches the handler's `CONFIG` around `/setllm` is exercising the
handler's own `_is_admin`, which still reads it there.
"""
from __future__ import annotations

import asyncio
import html
from typing import TYPE_CHECKING

from telegram import Update
from telegram.ext import ContextTypes

from bot.config import CONFIG
from bot.formatters.brain_state import UNTESTED as _BRAIN_UNTESTED
from bot.formatters.brain_state import brain_state as _brain_state
from bot.formatters.brain_state import sweep_note as _sweep_note
from bot.formatters.brain_state import untested_confirmation as _untested_confirm
from bot.llm.provider import BYOK, LLMConfig, LLMProvider
from bot.skills.command_guard import guard
from bot.utils.i18n import t
from bot.utils.logger import audit, system_log

if TYPE_CHECKING:
    from bot.core.engine import RuneClawEngine


class LLMCommands:
    """The operator's model-routing commands. Host contract below; methods after."""

    if TYPE_CHECKING:
        # Provided by TelegramHandler, and ONLY declared here — declarations,
        # never bodies; tests/test_handler_mixins.py checks every name against
        # what the handler really defines.
        engine: RuneClawEngine

        async def _send(self, update: Update, text: str,
                        reply_markup=None, edit: bool = False) -> None: ...

        def _is_admin(self, update: Update) -> bool: ...

        def _lang(self, update: Update) -> str: ...

    @guard("mode")
    async def _cmd_settier(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """/settier <tier> <provider> [model] — runtime per-tier LLM routing.

        THE promotion path after a winning /llmab shadow A/B:
        `/settier chat runeclaw` flips the chat tier to the in-house model
        with no restart and no env edit. `/settier clear <tier|all>` reverts.
        """
        # Same blast radius as /setllm (changes which model answers every
        # user), so the same admin-only gate.
        if not self._is_admin(update):
            await self._send(update,
                f"\U0001f512 {t('admin_only_llm_set', self._lang(update))}")
            return
        from bot.llm.provider import (LLMTier, clear_tier_override,
                                      get_tier_overrides, set_tier_override)
        args = [a.lower() for a in (ctx.args or [])]
        tiers = ", ".join(x.value for x in LLMTier)
        if not args:
            cur = get_tier_overrides()
            cur_lines = ("\n".join(
                f" • <code>{html.escape(k)}</code> → "
                f"<code>{html.escape(v['provider'])}/{html.escape(v['model'] or 'default')}</code>"
                for k, v in cur.items()) if cur else " • <i>none — env/default routing active</i>")
            await self._send(update,
                "🎛 <b>Runtime tier routing</b>\n"
                "<pre>"
                " /settier chat runeclaw\n"
                " /settier scan runeclaw runeclaw-v6\n"
                " /settier clear chat\n"
                " /settier clear all"
                "</pre>\n"
                f"<b>Tiers:</b> <code>{tiers}</code>\n\n"
                f"<b>Active overrides</b>\n{cur_lines}\n\n"
                "<i>Applies instantly to every caller of the tier; survives "
                "until restart (set LLM_TIER_*_PROVIDER in .env to make it "
                "permanent). The operator Anthropic key stays admin-only "
                "regardless of routing.</i>")
            return
        if args[0] == "clear":
            if len(args) > 1 and args[1] != "all":
                try:
                    n = clear_tier_override(LLMTier(args[1]))
                except ValueError:
                    await self._send(update, f"Unknown tier. Tiers: <code>{tiers}</code>")
                    return
            else:
                n = clear_tier_override()
            audit(system_log, f"Tier override cleared ({args[1] if len(args) > 1 else 'all'})",
                  action="settier", result="CLEARED")
            await self._send(update, f"✅ Cleared {n} tier override(s) — env/default routing active.")
            return
        if len(args) < 2:
            await self._send(update, "Usage: /settier &lt;tier&gt; &lt;provider&gt; [model]")
            return
        try:
            tier = LLMTier(args[0])
        except ValueError:
            await self._send(update, f"Unknown tier <code>{html.escape(args[0])}</code>. Tiers: <code>{tiers}</code>")
            return
        try:
            provider = LLMProvider(args[1])
        except ValueError:
            await self._send(update, f"Unknown provider <code>{html.escape(args[1])}</code>.")
            return
        model = ctx.args[2] if len(ctx.args or []) > 2 else ""
        ok, detail = set_tier_override(tier, provider, model)
        if ok:
            audit(system_log, f"Tier override set: {detail}",
                  action="settier", result="OK",
                  data={"tier": tier.value, "provider": provider.value,
                        "model": model or "default"})
            await self._send(update,
                f"✅ <b>Routing updated:</b> <code>{html.escape(detail)}</code>\n"
                "<i>Applies instantly, reverts on restart — set "
                "LLM_TIER_*_PROVIDER in .env to make it permanent.</i>")
        else:
            await self._send(update,
                f"🔴 Override NOT set: {html.escape(detail)}")

    async def _cmd_ultra(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """/ultra [on|off] — ULTRA admin LLM routing (Claude Fable 5).

        Flips admin thesis/learning tiers to claude-fable-5 with
        output_config.effort high/max; scan/chat stay on Sonnet 5. Costs
        real money ($10/$50 per MTok) — explicit opt-in, never a default."""
        # Same blast radius as /setllm//settier (changes the analysis brain
        # and the bill), so the same admin-only gate.
        if not self._is_admin(update):
            await self._send(update,
                f"\U0001f512 {t('admin_only_llm_set', self._lang(update))}")
            return
        from bot.llm.provider import is_ultra_mode, set_ultra_mode
        args = [a.lower() for a in (ctx.args or [])]
        if not args or args[0] not in ("on", "off"):
            state = "🟣 ON" if is_ultra_mode() else "⚪ OFF"
            await self._send(update,
                f"🧠 <b>ULTRA routing:</b> {state}\n"
                "<pre>"
                " /ultra on\n"
                " /ultra off"
                "</pre>\n"
                "ON: admin thesis/learning → <code>claude-fable-5</code> "
                "(effort high/max), scan/chat → <code>claude-sonnet-5</code>.\n"
                "<i>Fable 5 bills $10/$50 per MTok (~2x Opus). Admin-only "
                "routing — non-admin users are never routed to the operator "
                "Anthropic key. Reverts on restart; set LLM_ULTRA_ENABLED=1 "
                "in .env to make it the boot default.</i>")
            return
        env_config = LLMConfig(
            provider=LLMProvider(CONFIG.llm.provider) if CONFIG.llm.provider else LLMProvider.OPENAI,
            api_key=CONFIG.llm.api_key,
            model=CONFIG.llm.model,
            base_url=CONFIG.llm.base_url,
        )
        ok, detail = set_ultra_mode(args[0] == "on", env_config)
        if not ok:
            await self._send(update, f"🔴 ULTRA NOT enabled: {html.escape(detail)}")
            return
        # Re-resolve the analyzer's cached admin tier clients so the toggle
        # takes effect on the next analysis, not the next restart.
        if hasattr(self.engine, 'analyzer') and hasattr(self.engine.analyzer, 'refresh_llm_client'):
            self.engine.analyzer.refresh_llm_client()
        audit(system_log, f"ULTRA routing {'ON' if args[0] == 'on' else 'OFF'}",
              action="ultra", result="OK", data={"state": args[0]})
        await self._send(update, f"✅ {html.escape(detail)}")

    async def _cmd_setllm(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """/setllm <provider> [api_key] [model] — switch LLM provider at runtime."""
        # SCRUB FIRST, AUTHORISE SECOND.
        #
        # The API key is already in the chat by the time this runs. The admin
        # rejection below used to `return` before the delete at the end of this
        # function — the one commented "Always try to delete the original message
        # containing the API key" — so a NON-admin who pasted a key had it
        # refused and left in the history permanently, which is the one case
        # where the key is most likely to have been pasted by mistake.
        #
        # Deleting is not a privileged action and does not depend on who asked,
        # so it happens before the authorisation check, unconditionally.
        try:
            await update.message.delete()
        except Exception as _del_exc:
            system_log.warning(
                "Failed to delete /setllm message containing API key: %s — "
                "key may be visible in chat history", _del_exc)

        # Audit F-12: swapping the analysis LLM / injecting a key affects every
        # trade decision — restrict to admins, not the broad `mode` permission.
        if not self._is_admin(update):
            await self._send(update,
                f"\U0001f512 {t('admin_only_llm_set', self._lang(update))}")
            return

        args = ctx.args or []
        if not args:
            providers = ", ".join(p.value for p in LLMProvider if p != LLMProvider.CUSTOM)
            SEP = "─" * 16
            await self._send(update,
                f"🤖 <b>BYOK — Bring Your Own Key</b>\n"
                f"{SEP}\n\n"
                "<pre>"
                " /setllm &lt;provider&gt; &lt;api_key&gt;\n"
                " /setllm groq gsk_your_key\n"
                " /setllm ollama\n"
                " /setllm anthropic sk-ant-key\n"
                " /setllm openai sk-key gpt-4o-mini\n"
                "</pre>\n\n"
                f"<b>Providers:</b> <code>{providers}</code>\n\n"
                "<i>🔑 Keys are validated live, then stored ENCRYPTED in the "
                "operator vault — they survive restarts and redeploys. Never "
                "logged.</i>")
            return

        provider_str = args[0].lower()
        api_key = args[1] if len(args) > 1 else ""
        model = args[2] if len(args) > 2 else ""

        # Warn about key exposure
        await self._send(update,
            f"⚠️ {t('llm_security_warning', self._lang(update))}")

        # Preflight: validate an Anthropic key with ONE real 1-token call
        # BEFORE storing it. Recurring live incident (2026-07-11): a typo'd/
        # stale key pasted via /setllm was accepted silently and then 401'd
        # on every analysis. Reject invalid keys at set time instead.
        if provider_str == "anthropic" and api_key:
            from bot.llm import key_health as _kh
            _status, _detail = await asyncio.to_thread(
                _kh.validate_anthropic_key, api_key,
                model or "claude-sonnet-5")
            if _status == _kh.INVALID:
                await self._send(update,
                    "🔴 <b>Key REJECTED — preflight failed.</b>\n"
                    f"<code>{html.escape(_detail[:160])}</code>\n\n"
                    "The key was NOT stored. Copy a fresh key from "
                    "console.anthropic.com and retry.")
                try:
                    await update.message.delete()
                except Exception:
                    pass
                return
            if _status == _kh.VALID:
                await self._send(update,
                    "🟢 Key preflight OK — the key answered a live call.")

        ok, msg = BYOK.set_provider(provider_str, api_key=api_key, model=model)
        if ok:
            # Persist the key ENCRYPTED in the operator vault so it survives
            # restarts and redeploys — the recurring "every LLM tier shows ❌
            # after a wiped .env" outage. The in-memory BYOK config stays the
            # runtime source; the vault re-injects the env var on the next
            # boot so tier resolution finds it again.
            if api_key:
                _key_env = {
                    "anthropic": "ANTHROPIC_API_KEY", "openai": "OPENAI_API_KEY",
                    "gemini": "GEMINI_API_KEY", "groq": "GROQ_API_KEY",
                    "deepseek": "DEEPSEEK_API_KEY", "alibaba": "ALIBABA_API_KEY",
                    "mistral": "MISTRAL_API_KEY", "together": "TOGETHER_API_KEY",
                    "openrouter": "OPENROUTER_API_KEY",
                    "runeclaw": "RUNECLAW_LLM_API_KEY",
                }.get(provider_str)
                if _key_env:
                    try:
                        from bot.core.secrets_vault import store_secrets
                        store_secrets({_key_env: api_key})
                    except Exception as exc:
                        system_log.error("setllm: vault store failed: %s", exc)
            # Refresh the analyzer's LLM client to use new provider
            if hasattr(self.engine, 'analyzer') and hasattr(self.engine.analyzer, 'refresh_llm_client'):
                self.engine.analyzer.refresh_llm_client()
            audit(system_log, f"LLM provider switched to {provider_str}",
                  action="setllm", result="OK",
                  data={"provider": provider_str, "model": model or "default"})
            SEP = "─" * 16
            await self._send(update,
                f"✅ {t('llm_provider_updated', self._lang(update), sep=SEP, provider=html.escape(provider_str), model=html.escape(model or 'default'))}")
        else:
            await self._send(update,
                f"🔴 {t('llm_update_failed', self._lang(update), msg=html.escape(msg))}")

        # (The message was already deleted at the top of this function, before
        # the authorisation check, so that a rejected non-admin's pasted key is
        # scrubbed too.)

    @guard("status")
    async def _cmd_llmstatus(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """/llmstatus — show current LLM provider and key fingerprint."""
        # Operator infrastructure: provider, model, base_url and per-tier key
        # fingerprints. @guard('status') is in the VIEWER role's permission set,
        # so the catalog's "operator" label was not enforced by anything.
        if not self._is_admin(update):
            await self._send(update, f"\U0001f512 {t('admin_only', self._lang(update))}")
            return


        env_config = LLMConfig(
            provider=LLMProvider(CONFIG.llm.provider) if CONFIG.llm.provider else LLMProvider.OPENAI,
            api_key=CONFIG.llm.api_key,
            model=CONFIG.llm.model,
            base_url=CONFIG.llm.base_url,
        )
        status = BYOK.status(env_config)
        SEP = "─" * 16
        # Live brain-health line: is the analyzer actually getting LLM answers,
        # or silently running on the rule engine? (Mirrors the proactive
        # LLM-degraded alert; lets the operator check on demand.) Best-effort.
        health_line = ""
        try:
            analyzer = getattr(self.engine, "analyzer", None)
            if analyzer is not None and hasattr(analyzer, "llm_health"):
                h = analyzer.llm_health()
                streak = int(h.get("degraded_streak", 0) or 0)
                # Same three-way split as _scan_timeout_hint, through the same
                # function. The two surfaces answer "is the brain answering?"
                # and only this one had the distinction; the scan-timeout hint
                # was ruling the LLM out on an untested one.
                _state = _brain_state(h)
                if streak > 0:
                    mins = float(h.get("degraded_seconds", 0.0) or 0.0) / 60.0
                    health_line = (
                        f"\n🚨 <b>Brain: DEGRADED</b> — every provider has failed "
                        f"{streak} analyses in a row"
                        + (f" (~{mins:.0f} min)" if mins >= 1 else "")
                        + "; running on the rule engine. Add/rotate an LLM key.")
                    # WHY it's failing (401 bad key / 404 model / 429 quota) —
                    # the live incident showed the streak without the cause.
                    _err = str(h.get("last_error", "") or "")
                    if _err:
                        health_line += (f"\nLast error: "
                                        f"<code>{html.escape(_err[:160])}</code>")
                elif _state == _BRAIN_UNTESTED:
                    # streak==0 but no success recorded either: nothing has been
                    # attempted since restart. Don't claim "answering" — the
                    # live incident showed "healthy" at 18:07 then 18 failures
                    # at 18:08 because the first status simply pre-dated any
                    # LLM call.
                    health_line = ("\n⚪ <b>Brain: untested</b> — no LLM "
                                   "analysis attempted since restart; "
                                   + _untested_confirm(h))
                else:
                    health_line = "\n✅ <b>Brain: healthy</b> — LLM answering."
                # WHETHER THE SWEEP ASKS THE LLM AT ALL — its own sentence,
                # never merged into the brain's. `sweep_note` was written and
                # tested for exactly this and, until now, called by NOTHING
                # outside its tests: a renderer for a state the operator could
                # not see anywhere.
                #
                # It was tracked, not invisible. tests/unreachable_baseline.txt
                # does only track MODULES, and brain_state.py is imported for
                # the brain icons — but the repo also runs a FUNCTION-level
                # ratchet, and `sweep_note` sat in
                # tests/unreachable_functions_baseline.txt. Wiring it here made
                # that entry stale and the ratchet refused to pass until it was
                # removed in this same commit — the known_failures.txt rule,
                # working exactly as designed.
                #
                # Empty for the historical default, so the common case stays
                # quiet. When the valve is off it says so, which matters most
                # beside a HEALTHY brain: one user /analyze keeps the streak at
                # 0 and last_ok fresh, so the brain reads healthy — truthfully
                # — while every background signal that tick came from the rule
                # engine, and the reader takes those for AI theses.
                _sweep = _sweep_note(h)
                if _sweep:
                    health_line += f"\n{_sweep}"
                # CHAT FAILURES, whatever the sweep says. 2026-09-02: a user
                # asked twice, was told the AI was unavailable twice, then read
                # "untested — no LLM analysis attempted since restart". True of
                # the SWEEP, and useless: they had not asked about the sweep.
                # Appended rather than folded into the line above, because the
                # sweep's state and chat's are different facts and the reader
                # needs both — a healthy sweep with failing chat is a real and
                # confusing state, and it is the one they were in.
                _chatf = int(h.get("chat_failures", 0) or 0)
                if _chatf:
                    _ago = h.get("chat_seconds_ago")
                    _when = (f" (last {float(_ago) / 60.0:.0f} min ago)"
                             if isinstance(_ago, (int, float)) and _ago >= 60
                             else " (last just now)" if _ago is not None else "")
                    health_line += (
                        f"\n🚨 <b>Chat: {_chatf} call"
                        f"{'s' if _chatf != 1 else ''} failed</b> — every "
                        f"provider fell through{_when}.")
                    _cerr = str(h.get("chat_last_error", "") or "")
                    if _cerr:
                        health_line += (f"\nLast chat error: "
                                        f"<code>{html.escape(_cerr[:160])}</code>")
        except Exception:
            health_line = ""
        # Key slots: every candidate Anthropic key the resolver can pick from,
        # with its health state, plus which key each ADMIN tier resolves to
        # RIGHT NOW. Recurring live incident (2026-07-11): multiple writable
        # key slots (runtime BYOK / ANTHROPIC_API_KEY / primary .env) and no
        # way to see which one the autonomous calls actually used.
        slots_block = ""
        try:
            from bot.llm import key_health as _kh
            active_cfg = BYOK.get_active_config(env_config)
            lines = []
            for _src, _key in _kh.anthropic_candidates(
                    env_config, BYOK._runtime_config):
                _st = _kh.status_of(_key)
                _icon = {"valid": "🟢", "invalid": "🔴"}.get(_st, "⚪")
                lines.append(f"{_icon} {_src}: {_kh.fp(_key)} [{_st}]")
            if lines:
                # ANTHROPIC SLOTS ONLY. The "— engine uses → ..." line that
                # used to close this block resolved each tier and printed
                # `key_fingerprint()` — under a heading that says Anthropic,
                # for tiers that now resolve to the self-hosted model, using a
                # helper whose answer for "keyless" is the words NOT SET. A
                # healthy keyless tier therefore read as a missing key, sitting
                # directly beneath a valid one.
                slots_block = (
                    "\n\n<b>Anthropic key slots</b>\n<pre>"
                    + html.escape("\n".join(lines))
                    + "</pre>")
        except Exception:
            slots_block = ""
        # What actually answers, for EVERY tier — via the same `tier_report`
        # collector `/llmtiers` and the web panel use. That collector was
        # written because these surfaces had drifted and it names /llmstatus
        # as one of them; this block is the rendering it never reached.
        engine_block = ""
        try:
            from bot.formatters.llm_tier_card import TierRow, render_engine_uses
            from bot.llm.provider import tier_report
            engine_block = "\n\n" + render_engine_uses(
                [TierRow(**_row) for _row in
                 tier_report(BYOK.get_active_config(env_config), is_admin=True)])
        except Exception:
            engine_block = ""
        # Runtime tier overrides (/settier) — the routing that actually
        # answers calls right now, ahead of env/default tables.
        override_block = ""
        try:
            from bot.llm.provider import get_tier_overrides
            _ovs = get_tier_overrides()
            if _ovs:
                override_block = ("\n\n<b>Runtime tier overrides (/settier)</b>\n" +
                                  "\n".join(
                                      f" • <code>{html.escape(k)}</code> → "
                                      f"<code>{html.escape(v['provider'])}/"
                                      f"{html.escape(v['model'] or 'default')}</code>"
                                      for k, v in _ovs.items()))
        except Exception:
            override_block = ""
        # What the brain has COST since this process started. Tokens are
        # measured (the provider reports them); the dollar line appears only
        # if the operator supplied $/1M rates, because a hardcoded price table
        # is a stale number wearing the authority of a measured one.
        usage_block = ""
        try:
            from bot.llm import usage as _usage
            _u = _usage.snapshot()
            if _u.get("calls"):
                usage_block = (
                    f"\n\n📊 <b>Since start</b>: <code>{_u['calls']:,}</code> calls · "
                    f"<code>{_u['tokens_in']:,}</code> in / "
                    f"<code>{_u['tokens_out']:,}</code> out")
                if "cost_usd" in _u:
                    usage_block += (f" · <code>${_u['cost_usd']:,.4f}</code> "
                                    f"<i>({html.escape(_u['cost_basis'])})</i>")
                else:
                    usage_block += ("\n<i>Set LLM_PRICE_PER_1M_IN / "
                                    "LLM_PRICE_PER_1M_OUT for a cost figure — "
                                    "unset, no price is invented.</i>")
        except Exception:
            usage_block = ""
        await self._send(update,
            f"🤖 {t('llm_status_title', self._lang(update))}\n"
            f"{SEP}\n"
            f"<pre>{html.escape(status)}</pre>"
            f"{health_line}{engine_block}{slots_block}{override_block}{usage_block}")

    @guard("mode")
    async def _cmd_llmreset(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """/llmreset — clear runtime LLM key, revert to .env settings."""
        # Audit F-12: admin-only, mirroring /setllm.
        if not self._is_admin(update):
            await self._send(update,
                f"\U0001f512 {t('admin_only_llm_reset', self._lang(update))}")
            return

        msg = BYOK.reset()
        # Refresh analyzer client back to .env config
        if hasattr(self.engine, 'analyzer') and hasattr(self.engine.analyzer, 'refresh_llm_client'):
            self.engine.analyzer.refresh_llm_client()
        audit(system_log, "LLM config reset to .env", action="llmreset", result="OK")
        SEP = "─" * 16
        await self._send(update,
            f"🔄 {t('llm_config_reset', self._lang(update), sep=SEP, msg=html.escape(msg))}")

    @guard("status")
    async def _cmd_llmtiers(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """/llmtiers — show multi-tier LLM routing configuration."""
        # Operator infrastructure: provider, model, base_url and per-tier key
        # fingerprints. @guard('status') is in the VIEWER role's permission set,
        # so the catalog's "operator" label was not enforced by anything.
        if not self._is_admin(update):
            await self._send(update, f"\U0001f512 {t('admin_only', self._lang(update))}")
            return


        env_config = LLMConfig(
            provider=LLMProvider(CONFIG.llm.provider) if CONFIG.llm.provider else LLMProvider.OPENAI,
            api_key=CONFIG.llm.api_key,
            model=CONFIG.llm.model,
            base_url=CONFIG.llm.base_url,
        )
        active_cfg = BYOK.get_active_config(env_config)
        await self._send(update, self._llm_tier_card(active_cfg,
                                                     self._lang(update)))

    def _llm_tier_card(self, active_cfg: LLMConfig, lang: str) -> str:
        """Collect each tier's resolved facts and hand them to the renderer.

        RESOLVED WITH is_admin=True, which this card did not do. It is gated on
        `_is_admin` three lines up, so an admin is the only reader it can ever
        have — and it was resolving the route a NON-admin call takes, then
        presenting it as the routing. `/llmstatus` already passed is_admin=True
        for its key fingerprints, so the two operator cards answered the same
        question differently and neither said which it meant.
        """
        from bot.formatters.llm_tier_card import TierRow, render_tier_card
        from bot.llm.provider import tier_report, unbound_tier_env

        # `tier_report` is shared with the web dashboard's routing panel. The
        # two surfaces had already drifted — this one resolved as non-admin
        # while /api/state serialised a module constant — so collecting the
        # facts once is the point, not a tidy-up.
        return render_tier_card(
            [TierRow(**row) for row in tier_report(active_cfg, is_admin=True)],
            unbound_env=unbound_tier_env(),
            title=t('llm_tiers_title', lang),
        )

