"""Shadow restitution, for the operator.

The public door is the website. This command stays operator-only even when
SRB_ENABLED is on, so turning the flag on does not add a public slash
command. Amounts are base units on this admin view. The status word is
Bonded, Unbonding, or Unbonded, with no amount beside it.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from telegram import Update
from telegram.ext import ContextTypes

from bot.srb.flag import flag_on
from bot.srb.shadow import demo_report
from bot.utils.i18n import t


class SrbCommands:
    """The operator's shadow restitution record. Host contract below."""

    if TYPE_CHECKING:
        async def _send(self, update: Update, text: str,
                        reply_markup=None, edit: bool = False) -> None: ...

        def _is_admin(self, update: Update) -> bool: ...

        def _lang(self, update: Update) -> str: ...

    async def _cmd_srb(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """/srb — the recorded window. Nothing is paid."""
        if not self._is_admin(update):
            await self._send(update, f"\U0001f512 {t('admin_only', self._lang(update))}")
            return
        lines: list[str] = []
        if not flag_on():
            lines.append("Admin preview. Not public.")
        try:
            report = demo_report()
        except Exception as exc:  # noqa: BLE001 — the chat is told the class only
            await self._send(update, type(exc).__name__)
            return
        public = report.get("public")
        if not isinstance(public, dict):
            await self._send(update, "SrbUnreadable")
            return
        providers = public.get("providers")
        if isinstance(providers, list) and providers and isinstance(providers[0], dict):
            row = providers[0]
            lines.append(f"{row.get('provider_id')}: {row.get('status')}")
        else:
            lines.append("No provider in this record.")
        signals = public.get("signals")
        if isinstance(signals, list):
            for signal in signals:
                if not isinstance(signal, dict):
                    continue
                sentence = signal.get("sentence")
                if isinstance(sentence, str) and sentence:
                    lines.append(sentence)
                bps = signal.get("restitution_bps")
                if isinstance(bps, int):
                    lines.append(f"restitution {bps} bps")
        admin = report.get("admin")
        amounts = admin.get("amounts") if isinstance(admin, dict) else None
        if isinstance(amounts, dict):
            bond = amounts.get("bond_base_units")
            if isinstance(bond, int):
                lines.append(f"Bond base units: {bond}")
            rows = amounts.get("signals")
            if isinstance(rows, list) and rows and isinstance(rows[0], dict):
                recorded = rows[0].get("would_record_base_units")
                if isinstance(recorded, int):
                    lines.append(f"Recorded base units: {recorded}")
        await self._send(update, "\n".join(lines))
