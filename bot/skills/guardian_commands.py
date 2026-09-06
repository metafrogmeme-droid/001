"""The Guardian command group — the second slice out of the handler.

`/policy`, `/twin`, `/sentinel`, `/escape`, `/guardian`, `/approvals` and
`/xray`, plus the confirm/cancel callback that binds a compiled policy and the
two sync HTTP helpers the X-ray commands run through `to_thread`. Their
behaviour is covered where it always was (`test_guardian_chat_tools`,
`test_policy_mode_reports_the_bind`, `test_escape_plan_failure_is_not_a_flat_book`
and the guardian suites); `tests/test_guardian_commands_split.py` pins the SPLIT.

A MIXIN, NOT A LEAF. The first slice (`chat_runtime.py`) could be a leaf
because nothing in it read handler state. A command group cannot: every
method here reads `self.engine`, gates on `self._is_admin`, and answers
through `self._send`, the F-15 redaction chokepoint every outgoing message
must pass. So this class provides the methods and the handler provides the
host — `class TelegramHandler(GuardianCommands)` — and the contract between
them is written down once, under `TYPE_CHECKING`, where the type checker
reads it and nothing else does. That block must never grow a body: a mixin
that defines `_send` would be a second chokepoint, which is the shape the
redaction test exists to forbid.

The five console commands are operator-only by an inline `_is_admin` check,
which is how `tests/test_operator_controls_are_derived.py` already classifies
them. The two X-ray commands are `@guard("token")`: the move is what showed
they had NO gate at all — not the allowlist, not the rate limiter, not a
permission — while the catalogue filed them under "operator". They read
public chain data through the website's public API and touch no account, so
they belong to every admitted role, and `token` is the permission
ROLE_PERMISSIONS already argues that case for: the contract detective, "a
safety check for the users most likely to be handed a scam address".
Reused rather than invented, the way /eventrisk reused `macro`.
"""
from __future__ import annotations

import html
from typing import TYPE_CHECKING

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from bot.config import CONFIG
from bot.skills.command_guard import guard
from bot.utils.exc_text import _safe_exc_text
from bot.utils.i18n import t
from bot.utils.logger import audit, system_log
from bot.utils.site_url import site_url

if TYPE_CHECKING:
    from bot.core.engine import RuneClawEngine


class GuardianCommands:
    """The Guardian console's chat surface. Host contract below; methods after."""

    #: Compiled policies awaiting a confirm tap, per user id. Created on first
    #: use by `_cmd_policy` and read by `_apply_policy_callback`; the mixin's
    #: own state, declared so the type checker knows its shape.
    _pending_policy: dict

    if TYPE_CHECKING:
        # Provided by TelegramHandler, and ONLY declared here — see the module
        # docstring for why this block must stay empty of bodies. The split
        # test checks every name against what the handler really defines.
        engine: RuneClawEngine

        async def _send(self, update: Update, text: str,
                        reply_markup=None, edit: bool = False) -> None: ...

        def _is_admin(self, update: Update) -> bool: ...

        def _lang(self, update: Update) -> str: ...

    async def _cmd_policy(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """/policy — Guardian Intent Compiler authoring (admin).

        /policy                → show the active policy + mode + enforce state
        /policy set <plain EN>  → compile a policy from a sentence, preview + confirm
        /policy mode shadow|enforce|off → change the active policy's mode
        /policy clear           → remove the policy

        The AI proposes (compiles your sentence into typed rules); nothing binds
        until you tap a confirm button. A policy can only TIGHTEN the engine's
        caps, and defaults to shadow (logs would-be rejections, blocks nothing).
        """
        if not self._is_admin(update):
            await self._send(update, f"\U0001f512 {t('admin_only', self._lang(update))}")
            return
        from bot.guardian import intent_policy as ip
        args = list(ctx.args or [])
        sub = args[0].lower() if args else ""
        uid = update.effective_user.id if update.effective_user else 0

        if sub in ("", "show"):
            summ = self.engine._intent_policy_summary()
            enabled = bool(getattr(CONFIG.risk, "intent_policy_enabled", False))
            if not summ:
                await self._send(update,
                    "🛡 <b>Intent policy</b> — none set.\n\n"
                    "Author one in plain language, e.g.\n"
                    "<code>/policy set only majors, max 5% per trade, "
                    "no shorts, min confidence 70%</code>\n\n"
                    f"<i>Enforcement flag INTENT_POLICY_ENABLED is "
                    f"<b>{'ON' if enabled else 'OFF'}</b>. The engine's fail-closed "
                    "risk gate always applies regardless.</i>")
                return
            body = ip.human_readable(summ)
            state = ("🟢 active" if enabled else "🟡 saved, dormant (INTENT_POLICY_ENABLED off)")
            await self._send(update,
                f"🛡 <b>Intent policy</b> — {state}\n\n<pre>{html.escape(body)}</pre>\n"
                "<i>/policy mode shadow|enforce|off · /policy clear · "
                "/policy set …</i>")
            return

        if sub == "set":
            nl = " ".join(args[1:]).strip()
            if not nl:
                await self._send(update,
                    "Usage: <code>/policy set only majors, max 5% per trade, "
                    "no shorts</code>")
                return
            parsed = ip.compile_nl(nl)
            if not parsed.get("rules"):
                await self._send(update,
                    "I couldn't turn that into any rules. Try phrasings like "
                    "“max 5% per trade”, “only majors”, “no shorts”, "
                    "“min confidence 70%”, “stop if down 8%”.")
                return
            policy = ip.compile_policy({
                "mode": "shadow", "source_text": nl,
                "label": "Operator policy",
                "rules": parsed["rules"],
            }, self.engine._intent_engine_caps())
            if not hasattr(self, "_pending_policy"):
                self._pending_policy = {}
            self._pending_policy[uid] = policy
            unparsed_note = ""
            body = ip.human_readable(policy)
            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("👁 Apply (shadow)", callback_data="policy_apply_shadow"),
                 InlineKeyboardButton("🛡 Apply (enforce)", callback_data="policy_apply_enforce")],
                [InlineKeyboardButton("Cancel", callback_data="policy_cancel")],
            ])
            await self._send(update,
                f"🛡 <b>Compiled this policy</b> — review before it binds:\n\n"
                f"<pre>{html.escape(body)}</pre>\n{unparsed_note}"
                "<i>Shadow logs would-be rejections without blocking. Enforce "
                "adds them to the risk gate as tighten-only rejections. Nothing "
                "changes until you tap.</i>",
                reply_markup=kb)
            return

        if sub == "mode":
            m = (args[1].lower() if len(args) > 1 else "")
            if m not in ("off", "shadow", "enforce"):
                await self._send(update, "Usage: <code>/policy mode shadow|enforce|off</code>")
                return
            try:
                bound = self.engine.set_intent_policy_mode(m)
            except FileNotFoundError:
                await self._send(update, "No policy to change. Set one with <code>/policy set …</code>")
                return
            except Exception as exc:
                await self._send(update, f"Couldn't change mode: {_safe_exc_text(exc)}")
                return
            # `bound` is WHAT THE RISK GATE WILL ACTUALLY CONSULT, and it was
            # discarded — the reply below was an unconditional ✅ whatever came
            # back. write_intent_policy fails OPEN and returns None on a
            # missing file, an invalid spec, or a COMPILE FAULT, so the shape
            # this could not report is the one that matters: the operator asks
            # for enforce, the spec does not compile, the engine ends up with
            # NO POLICY BOUND, and the bot says enforcement is on.
            #
            # The `tail` below covers only the flag-off cause, which is the
            # benign one. A guard computed correctly and never read is the
            # same defect as a guard that is absent.
            enabled = bool(getattr(CONFIG.risk, "intent_policy_enabled", False))
            if bound is None and enabled and m != "off":
                await self._send(update,
                    f"⚠️ <b>Policy mode saved as {m} — but NOTHING IS BOUND.</b>\n"
                    f"The file was written and the engine reloaded it, and the "
                    f"reload produced no policy: the spec is invalid or failed "
                    f"to compile.\n"
                    f"<b>The risk gate is consulting no intent policy right "
                    f"now.</b> Check it with <code>/policy show</code>, then "
                    f"re-set it with <code>/policy set …</code>.")
                return
            tail = ("" if enabled else
                    "\n<i>(Enforcement flag INTENT_POLICY_ENABLED is off, so it's "
                    "saved but dormant until enabled + restart.)</i>")
            await self._send(update, f"✅ Policy mode → <b>{m}</b>.{tail}")
            return

        if sub == "clear":
            removed = self.engine.clear_intent_policy()
            await self._send(update,
                "🗑 Policy cleared." if removed else "No policy was set.")
            return

        await self._send(update,
            "Usage: <code>/policy</code> · <code>/policy set …</code> · "
            "<code>/policy mode shadow|enforce|off</code> · <code>/policy clear</code>")

    async def _cmd_twin(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """/twin — Guardian Portfolio Digital Twin (admin, read-only).

        Stress-tests the live book against parametric price shocks (flash crash,
        severe correlated tail, alt capitulation, short squeeze) and shows the
        projected drawdown + which positions would be liquidated in each. Pure
        foresight — it proposes nothing and changes nothing. When
        GUARDIAN_DIGITAL_TWIN_ENABLED is on, each run also seals a TWIN verdict on
        the tamper-evident chain.
        """
        if not self._is_admin(update):
            await self._send(update, f"\U0001f512 {t('admin_only', self._lang(update))}")
            return
        report = self.engine.run_digital_twin()
        # THREE OUTCOMES, not two. `None` is now reserved for "the book could
        # not be read"; a book that WAS read and is empty comes back as a real
        # report with flat_book set. Both used to be None, and both rendered
        # as "no open positions to stress-test" — an all-clear on the
        # foresight screen, assembled from a crash. Byte-for-byte the
        # `_cmd_escape` defect, which was fixed and left no sibling here.
        if report is None:
            await self._send(update,
                "🔮 <b>Digital Twin</b> — <b>could not read the book.</b>\n\n"
                "<i>This is not an empty account: the position read failed, so "
                "nothing was stress-tested. Check /status and the exchange "
                "connection, then try again.</i>")
            return
        if report.get("flat_book") or not report.get("scenarios"):
            await self._send(update,
                "🔮 <b>Digital Twin</b> — no open positions to stress-test.\n\n"
                "<i>The twin shocks the live book (flash crash, correlated tail, "
                "alt capitulation, short squeeze) and shows projected drawdown + "
                "liquidations. Nothing to simulate while flat.</i>")
            return
        _RISK_ICON = {"none": "🟢", "low": "🟡", "medium": "🟠", "high": "🔴",
                      "unknown": "⚪"}
        icon = _RISK_ICON.get(report.get("risk", "none"), "⚪")
        # `f"${eq:,.0f}"` on a None raises, and `.get(k, 0.0)` would have
        # printed a funded account as $0 equity. The twin reports None when
        # the live balance cache is empty, which is normal after a restart.
        eq = report.get("equity_usd")
        eq_txt = "unavailable" if eq is None else f"${eq:,.0f}"
        lines = [f"🔮 <b>Digital Twin</b> — {icon} worst-case <b>{html.escape(str(report.get('risk','none')).upper())}</b>",
                 f"<i>{report.get('position_count', 0)} position(s) · equity {eq_txt}</i>", ""]
        if eq is None:
            lines.append("<i>⚪ Equity could not be read, so drawdown "
                         "percentages are unavailable. Liquidation checks do "
                         "not need equity and are still shown.</i>\n")
        for s in report.get("scenarios", []):
            s_icon = _RISK_ICON.get(s.get("risk", "none"), "⚪")
            liq = s.get("liquidations", [])
            liq_txt = (" · liquidates " + ", ".join(html.escape(x) for x in liq[:4])) if liq else ""
            _dd = s.get("drawdown_pct")
            _dd_txt = "unavailable" if _dd is None else f"{_dd}%"
            lines.append(
                f"{s_icon} <b>{html.escape(s.get('label', s.get('name','')))}</b>\n"
                f"   drawdown <b>{_dd_txt}</b> "
                f"(P&L ${s.get('projected_pnl_usd', 0):,.0f}){liq_txt}")
        fragile = report.get("fragile", [])
        if fragile:
            frag_txt = ", ".join(f"{html.escape(f['symbol'])} (~{f['liq_move_pct']}%)"
                                 for f in fragile[:4])
            lines.append(f"\n<i>Most fragile (adverse move to liquidation): {frag_txt}</i>")
        sealed = bool(getattr(CONFIG.risk, "guardian_digital_twin_enabled", False))
        lines.append(f"\n<i>{'🟢 sealed to the evidence chain' if sealed else '🟡 preview only (GUARDIAN_DIGITAL_TWIN_ENABLED off)'} · isolated-margin estimate</i>")
        await self._send(update, "\n".join(lines))

    async def _cmd_sentinel(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """/sentinel — Guardian Systemic Risk Sentinel (admin, read-only).

        Assesses how structurally crowded the live book is right now — is too much
        in one correlation group, is it heavily net one-direction, are many
        positions clustered in the same group/direction or sharing a liquidation
        zone. Pure telemetry — it warns, it changes nothing. When
        GUARDIAN_RISK_SENTINEL_ENABLED is on, each run also seals a SENTINEL
        verdict on the tamper-evident chain.
        """
        if not self._is_admin(update):
            await self._send(update, f"\U0001f512 {t('admin_only', self._lang(update))}")
            return
        report = self.engine.run_risk_sentinel()
        # Three outcomes, same split as /twin above: None now means the book
        # could not be read, and that is not a flat account.
        if report is None:
            await self._send(update,
                "🛰 <b>Risk Sentinel</b> — <b>could not read the book.</b>\n\n"
                "<i>This is not an empty account: the position read failed, so "
                "no crowding assessment was made. Check /status and the "
                "exchange connection, then try again.</i>")
            return
        if not report.get("position_count"):
            await self._send(update,
                "🛰 <b>Risk Sentinel</b> — no open positions to assess.\n\n"
                "<i>The sentinel flags intra-book crowding (one sector, one "
                "direction, shared liquidation zones). Nothing to assess while "
                "flat.</i>")
            return
        _RISK_ICON = {"none": "🟢", "low": "🟡", "medium": "🟠", "high": "🔴"}
        icon = _RISK_ICON.get(report.get("risk", "none"), "⚪")
        tg = report.get("top_group", {}) or {}
        lines = [
            f"🛰 <b>Risk Sentinel</b> — {icon} crowding <b>{html.escape(str(report.get('risk','none')).upper())}</b>",
            f"<i>{report.get('position_count', 0)} position(s) · gross "
            f"${report.get('gross_notional_usd', 0):,.0f} · "
            f"{int(report.get('net_bias', 0) * 100)}% net {html.escape(str(report.get('net_direction','')))}"
            + (f" · top {html.escape(str(tg.get('group','')))} {tg.get('share_pct',0)}%" if tg.get('group') else "")
            + "</i>", ""]
        concerns = report.get("concerns", [])
        if concerns:
            for c in concerns:
                c_icon = _RISK_ICON.get(c.get("severity", "none"), "⚪")
                lines.append(f"{c_icon} <b>{html.escape(c.get('kind','').replace('_',' '))}</b> — "
                             f"{html.escape(c.get('detail',''))}")
        else:
            lines.append("🟢 Book looks diversified — no crowding concern tripped.")
        sealed = bool(getattr(CONFIG.risk, "guardian_risk_sentinel_enabled", False))
        lines.append(f"\n<i>{'🟢 sealed to the evidence chain' if sealed else '🟡 preview only (GUARDIAN_RISK_SENTINEL_ENABLED off)'}</i>")
        await self._send(update, "\n".join(lines))

    async def _cmd_escape(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """/escape — Guardian Universal Escape Agent (admin, read-only PLAN).

        Builds a safe, ORDERED emergency-exit plan for the live book: which
        position to close first and why, ranked by escape urgency (how close each
        sits to liquidation × how large it is), with the margin each close frees.
        This PLANS only — it closes nothing. To actually flatten, use /closeall or
        /emergency_stop. When GUARDIAN_ESCAPE_ENABLED is on, each run also seals an
        ESCAPE plan on the tamper-evident chain.
        """
        if not self._is_admin(update):
            await self._send(update, f"\U0001f512 {t('admin_only', self._lang(update))}")
            return
        from bot.formatters.escape_card import render_escape_card
        report = self.engine.run_escape_agent()
        # The card is built by a pure renderer so a test can plant a crashed
        # planner and assert what the operator actually reads. Built inline,
        # nothing could — and a planner crash rendered as "no open positions
        # to unwind", which is an all-clear assembled from a failure.
        sealed = bool(getattr(CONFIG.risk, "guardian_escape_enabled", False))
        await self._send(update, render_escape_card(report, sealed=sealed))

    @staticmethod
    def _web_get_json(url: str, timeout: int = 20):
        """Sync GET helper for the bot->web public API (run via to_thread).
        Returns parsed JSON or None; never raises into the handler."""
        import json as _json
        import urllib.request as _rq
        try:
            with _rq.urlopen(_rq.Request(url, headers={"Accept": "application/json"}),
                             timeout=timeout) as resp:
                return _json.loads(resp.read().decode("utf-8"))
        except Exception:
            return None

    @staticmethod
    def _web_post_json(url: str, body: dict, timeout: int = 20):
        import json as _json
        import urllib.request as _rq
        try:
            data = _json.dumps(body).encode("utf-8")
            req = _rq.Request(url, data=data, headers={
                "Accept": "application/json", "Content-Type": "application/json"})
            with _rq.urlopen(req, timeout=timeout) as resp:
                return _json.loads(resp.read().decode("utf-8"))
        except Exception:
            return None

    @guard("token")
    async def _cmd_approvals(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """/approvals <address-or-name.eth> [chain] — the Allowance X-ray in
        chat: which contracts can spend the tokens of ANY address, read-only
        via the website's public API (the same bounded, honest check the
        /approvals page runs). Public chain data; nothing is signed or stored.

        `@guard("token")`, the contract detective's permission: same class of
        tool (public chain data, decides nothing, writes nothing), same
        audience. Shipped without any gate at all, so a stranger who was not
        even allowlisted could make the bot relay lookups to the site with no
        rate limit; the split's own tests found it.
        """
        import asyncio as _aio
        import re as _re
        base = site_url()
        args = list(ctx.args or [])
        if not args:
            await self._send(update,
                "\U0001fa7b <b>Allowance X-ray</b>\n"
                "Usage: <code>/approvals 0xADDRESS [chain]</code> or "
                "<code>/approvals name.eth [chain]</code>\n"
                "Chains: ethereum · base · arbitrum · optimism · bnb · avalanche · polygon · solana")
            return
        addr = args[0].strip()
        chain = (args[1].strip().lower() if len(args) > 1 else "ethereum")
        # name.eth resolves through the same endpoint the web page uses —
        # an unresolvable name scans nothing (unresolved is never zero).
        if _re.fullmatch(r"[a-z0-9-]+(\.[a-z0-9-]+)*\.eth", addr.lower()):
            r = await _aio.to_thread(self._web_get_json,
                                     f"{base}/api/allowances/resolve/{addr.lower()}")
            if not r or not r.get("address"):
                await self._send(update, "That name does not resolve \u2014 nothing was scanned.")
                return
            addr = r["address"]
        if _re.fullmatch(r"[1-9A-HJ-NP-Za-km-z]{32,44}", addr) and not addr.startswith("0x"):
            chain = "solana"
        d = await _aio.to_thread(self._web_get_json,
                                 f"{base}/api/allowances/{chain}/{addr}")
        if not d or d.get("error"):
            await self._send(update,
                "The chain would not answer just now \u2014 the grants are "
                "unknown, not zero. Try again shortly.")
            return
        lines = [f"\U0001fa7b <b>Allowance X-ray</b> \u00b7 {html.escape(d.get('label', chain))}"]
        finds = d.get("findings", [])
        if not finds:
            lines.append(f"\u2705 No live grants among {d.get('zero_pairs', 0)} checked pairs "
                         "\u2014 within this registry only; approvals outside it are not scanned.")
        for f in finds[:6]:
            amt = "\u26a0 UNLIMITED" if f.get("unlimited") else f"{f.get('allowance_raw')} raw"
            lines.append(f"\u2022 <b>{html.escape(str(f.get('token')))}</b> \u2192 "
                         f"{html.escape(str(f.get('spender_label')))} \u00b7 {html.escape(amt)}")
        if len(finds) > 6:
            lines.append(f"\u2026 and {len(finds) - 6} more.")
        if d.get("unreadable_pairs"):
            lines.append(f"\u26a0 {d['unreadable_pairs']} pair(s) unreadable \u2014 counted, never shown as zero.")
        lines.append(f"\nRevoke plans + full grid: {base}/approvals?a={addr}"
                     "\n<i>A clean result is never a guarantee. Read-only \u2014 nothing was signed.</i>")
        await self._send(update, "\n".join(lines))

    @guard("token")
    async def _cmd_xray(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """/xray <calldata> — decode what a transaction actually DOES before
        signing it, through the website's public tool dispatcher (the same
        xray_transaction tool any MCP agent calls). Pure decode: nothing sent
        here is stored, no account is seen, amounts are RAW token units.
        `@guard("token")` for the reason given on /approvals."""
        import asyncio as _aio
        base = site_url()
        data = (ctx.args[0].strip() if ctx.args else "")
        if not data.startswith("0x") or len(data) < 10:
            await self._send(update,
                "\U0001fa7b <b>Transaction X-ray</b>\n"
                "Usage: <code>/xray 0xCALLDATA</code> \u2014 paste the transaction "
                "data field from your wallet's confirmation screen.")
            return
        r = await _aio.to_thread(self._web_post_json, f"{base}/api/tool/invoke",
                                 {"tool": "xray_transaction", "args": {"data": data}})
        result = (r or {}).get("result")
        if not result:
            await self._send(update, "The decoder would not answer just now \u2014 try again shortly.")
            return
        lines = ["\U0001fa7b <b>Transaction X-ray</b>"]
        for a in (result.get("actions") or [])[:8]:
            en = str(a.get("en", ""))
            for k, v in (a.get("params") or {}).items():
                en = en.replace("{" + k + "}", str(v))
            lines.append(f"\u2022 {html.escape(en)}")
        for f in (result.get("flags") or [])[:4]:
            lines.append(f"\u26a0 <b>{html.escape(str(f.get('id', '')))}</b> \u00b7 {html.escape(str(f.get('sev', '')))}")
        lines.append("\n<i>Heuristic decode \u2014 a flag is not a verdict and unknown is not safe. "
                     "Nothing sent here is stored.</i>")
        await self._send(update, "\n".join(lines))

    async def _cmd_guardian(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """/guardian — the Guardian console (admin, read-only).

        One screen for the whole safety layer: the evidence chain's health, the
        intent policy, the firewall, and the live book's foresight / crowding /
        unwind urgency — plus which modules are armed. Pure read — viewing this
        seals nothing. Deep-dive with /twin, /sentinel, /escape, /policy.
        """
        if not self._is_admin(update):
            await self._send(update, f"\U0001f512 {t('admin_only', self._lang(update))}")
            return
        s = self.engine.guardian_status()
        _RISK_ICON = {"none": "🟢", "low": "🟡", "medium": "🟠", "high": "🔴"}
        flags = s.get("flags", {})

        def _arm(on: bool) -> str:
            return "🟢 armed" if on else "⚪ off"

        posture = s.get("posture", "none")
        chain = s.get("chain", {})
        chain_ok = chain.get("ok")
        chain_badge = ("✅ verified" if chain_ok is True
                       else "⚠️ UNVERIFIED" if chain_ok is False else "· unchecked")
        lines = [
            f"🛡 <b>Guardian console</b> — posture {_RISK_ICON.get(posture, '⚪')} "
            f"<b>{html.escape(str(posture).upper())}</b>",
            "",
            f"🎞 <b>Flight Recorder</b> — {chain.get('length', 0)} entries · {chain_badge}",
            f"📜 <b>Intent Compiler</b> — {'policy set' if s.get('policy') else 'no policy'} · "
            f"{_arm(flags.get('intent_policy'))}",
            f"🧱 <b>Firewall</b> — {_arm(flags.get('firewall'))}"
            + (" · blocks HIGH" if flags.get('firewall_block') else " · record-only"),
            "",
            "<b>Live book</b>",
            f"🔮 Digital Twin — {_RISK_ICON.get(s.get('twin', {}).get('risk','none'), '⚪')} "
            f"{html.escape(str(s.get('twin', {}).get('risk','none')).upper())} "
            f"({s.get('twin', {}).get('position_count', 0)} pos) · {_arm(flags.get('digital_twin'))}",
            f"🛰 Risk Sentinel — {_RISK_ICON.get(s.get('sentinel', {}).get('risk','none'), '⚪')} "
            f"{html.escape(str(s.get('sentinel', {}).get('risk','none')).upper())} · {_arm(flags.get('risk_sentinel'))}",
            f"🪂 Escape Agent — {_RISK_ICON.get(s.get('escape', {}).get('risk','none'), '⚪')} "
            f"{html.escape(str(s.get('escape', {}).get('risk','none')).upper())} · {_arm(flags.get('escape'))}",
            "",
            "<i>Deep-dive: /twin · /sentinel · /escape · /policy · /whynot</i>",
            "<i>The AI proposes · controls authorize · the wallet enforces · "
            "the recorder proves · the escape agent recovers.</i>",
        ]
        await self._send(update, "\n".join(lines))

    async def _apply_policy_callback(self, update: Update, data: str) -> None:
        """Confirm/cancel for /policy set — the SOLE place a compiled policy is
        persisted and bound to the live engine. Admin-perm gated upstream in
        _handle_callback (data.startswith('policy_') → 'mode' permission)."""
        uid = update.effective_user.id if update.effective_user else 0
        pend = getattr(self, "_pending_policy", {})
        if data == "policy_cancel":
            pend.pop(uid, None)
            await self._send(update, "👍 Cancelled — nothing changed.", edit=True)
            return
        policy = pend.pop(uid, None)
        if not policy:
            await self._send(update,
                "That policy preview expired. Run <code>/policy set …</code> again.",
                edit=True)
            return
        mode = "enforce" if data == "policy_apply_enforce" else "shadow"
        policy = dict(policy)
        policy["mode"] = mode
        try:
            bound = self.engine.write_intent_policy(policy)
        except Exception as exc:
            await self._send(update,
                f"Couldn't save policy: {_safe_exc_text(exc)}", edit=True)
            return
        enabled = bool(getattr(CONFIG.risk, "intent_policy_enabled", False))
        if enabled and bound:
            state = f"🟢 active in <b>{mode}</b> mode"
        else:
            state = (f"🟡 saved in <b>{mode}</b> mode but dormant — "
                     "INTENT_POLICY_ENABLED is off (enable + restart to activate)")
        audit(system_log, f"Intent policy applied via Telegram: {policy.get('policy_id')} mode={mode}",
              action="intent_policy_apply", result="APPLIED",
              data={"policy_id": policy.get("policy_id"), "mode": mode,
                    "hash": policy.get("compiled_hash"), "bound": bool(bound)})
        await self._send(update,
            f"🛡 Policy applied — {state}.\n"
            "<i>The fail-closed risk gate always applies; a policy can only add "
            "tighten-only rejections.</i>", edit=True)
