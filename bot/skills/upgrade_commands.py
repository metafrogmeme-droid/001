"""The /upgrade command group: how a member gets Pro or Elite.

Two doors, one command. While Telegram Stars payments are off (the default,
`bot/token/stars.py::payments_on`), /upgrade says what each tier adds on this bot
and sends the operator one request, the way an unadmitted newcomer's /start
does. While they are on, it shows a Stars invoice for each tier on sale.

The pre-checkout handler is the last point where a refusal costs the buyer
nothing, so it re-asks every check (`stars.check_checkout`). The payment
handler grants the period (`UserStore.grant_paid_tier`, idempotent on the
charge id) and refunds a payment that would buy nothing.

A mixin, held to `tests/test_handler_mixins.py`: it reads `self.users`,
answers through `self._send`, and declares what it needs from the host.
"""
from __future__ import annotations

import html
from datetime import datetime
from typing import TYPE_CHECKING

from telegram import LabeledPrice, Update
from telegram.ext import ContextTypes

from bot.compat import UTC
from bot.skills.command_guard import guard
from bot.utils.logger import audit, system_log

if TYPE_CHECKING:
    from bot.utils.user_store import UserStore


def _until_text(until) -> str:
    return datetime.fromtimestamp(float(until), UTC).strftime("%Y-%m-%d %H:%M UTC")


def upgrade_card(ladder: dict, offers: list[dict], paid: bool) -> str:
    """The /upgrade reply: what each tier adds here, and how to get it."""
    from bot.token import stars
    lines = ["<b>Upgrade</b>"]
    for tier in stars.SOLD:
        adds = stars.benefits(ladder, tier)
        name = tier.capitalize()
        if adds:
            lines.append(f"<b>{name}</b> adds: " + "; ".join(html.escape(a) for a in adds) + ".")
        else:
            lines.append(f"<b>{name}</b> adds nothing over Basic on this bot right now.")
    lines.append("")
    if paid:
        if offers:
            lines.append("Pay with Telegram Stars below. A period starts when the payment "
                         "lands, and a second period of the same plan extends the first.")
        else:
            lines.append("Nothing is on sale right now: no plan adds anything here.")
        lines.append("Payment problems or a refund: reply here with /upgrade help.")
    else:
        lines.append("Plans are granted by the operator. I've passed your request on.")
    lines.append("<i>Never sold: the kill switch, your own positions and risk, why a "
                 "trade was refused, and live trading.</i>")
    return "\n".join(lines)


class UpgradeCommands:
    if TYPE_CHECKING:
        # Provided by TelegramHandler, and ONLY declared here — declarations,
        # never bodies; tests/test_handler_mixins.py checks every name against
        # what the handler really defines.
        users: UserStore

        async def _send(self, update: Update, text: str,
                        reply_markup=None, edit: bool = False) -> None: ...

        def _get_tg_id(self, update: Update) -> str: ...

        @staticmethod
        def _operator_chat_ids() -> list: ...

    async def _notify_operator_upgrade(self, ctx, text: str) -> bool:
        bot = getattr(ctx, "bot", None)
        if bot is None:
            return False
        delivered = False
        for admin_id in self._operator_chat_ids():
            try:
                await bot.send_message(chat_id=int(admin_id), text=text, parse_mode="HTML")
                delivered = True
            except Exception as exc:  # noqa: BLE001
                system_log.warning("upgrade note to admin %s failed (%s)",
                                   admin_id, type(exc).__name__)
        return delivered

    @guard("tier")
    async def _cmd_upgrade(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """/upgrade — what Pro and Elite add here, and the way to get one."""
        from bot.token import stars, tier_gate
        tg_id = self._get_tg_id(update)
        user = update.effective_user
        who = html.escape(getattr(user, "first_name", "") or "Unknown")
        if (ctx.args or [""])[0].lower() == "help":
            sent = await self._notify_operator_upgrade(
                ctx, f"💳 Payment help asked by {who} (<code>{tg_id}</code>).")
            await self._send(update, "I've told the operator; they will reply here."
                             if sent else "I could not reach the operator just now. "
                             "Please try again in a few minutes.")
            return
        ladder = tier_gate.plan_ladder(self.users, tg_id)
        paid = stars.payments_on()
        offers = stars.offers(ladder) if paid else []
        if not paid:
            sent = await self._notify_operator_upgrade(
                ctx, f"⭐ Plan request from {who} (<code>{tg_id}</code>). "
                     f"Grant with /set_tier {tg_id} pro")
            text = upgrade_card(ladder, offers, paid=False)
            if not sent:
                text = text.replace("I've passed your request on.",
                                    "I could not reach the operator just now; please "
                                    "try again in a few minutes.")
            await self._send(update, text)
            return
        await self._send(update, upgrade_card(ladder, offers, paid=True))
        for o in offers:
            await ctx.bot.send_invoice(
                chat_id=update.effective_chat.id,
                title=f"RUNECLAW {o['tier'].capitalize()}, {o['days']} days",
                description=("; ".join(o["benefits"]))[:255],
                payload=stars.payload(tg_id, o["tier"], o["days"], o["stars"]),
                provider_token="",
                currency=stars.CURRENCY,
                prices=[LabeledPrice(f"{o['tier'].capitalize()} {o['days']}d", o["stars"])],
            )

    async def _on_pre_checkout(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """Telegram asks before it takes the Stars: accept only an invoice this
        bot would issue now, to this buyer, that changes what they hold."""
        from bot.token import stars, tier_gate
        q = update.pre_checkout_query
        uid = str(q.from_user.id)
        try:
            if self.users.get(uid) is None:
                why = "Send /start to the bot first, then /upgrade."
            else:
                why = stars.check_checkout(
                    q.invoice_payload, uid, q.currency, q.total_amount,
                    tier_gate.plan_ladder(self.users, uid),
                    self.users.get_tier(uid), self.users.tier_until(uid),
                    datetime.now(UTC).timestamp())
        except Exception as exc:  # noqa: BLE001
            system_log.warning("pre-checkout for %s could not be checked (%s)",
                               uid, type(exc).__name__)
            why = "The bot could not check this payment just now. Nothing was charged."
        if why is None:
            await q.answer(ok=True)
        else:
            await q.answer(ok=False, error_message=why)

    async def _on_successful_payment(self, update: Update,
                                     ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """The Stars landed: grant the period, or refund one that buys nothing."""
        from bot.token import stars
        pay = update.message.successful_payment
        uid = str(update.effective_user.id)
        p = stars.parse_payload(pay.invoice_payload)
        charge = pay.telegram_payment_charge_id
        grant = None
        if p is not None and p["uid"] == uid:
            grant = self.users.grant_paid_tier(uid, p["tier"], p["days"], charge)
        if grant is None:
            refunded = False
            try:
                refunded = bool(await ctx.bot.refund_star_payment(
                    user_id=int(uid), telegram_payment_charge_id=charge))
            except Exception as exc:  # noqa: BLE001
                system_log.warning("star refund for %s failed (%s)", uid, type(exc).__name__)
            audit(system_log, f"Star payment not granted: {uid}, refunded={refunded}",
                  action="tier_paid", result="REFUNDED" if refunded else "FAILED")
            await self._notify_operator_upgrade(
                ctx, f"⚠️ A Stars payment from <code>{uid}</code> bought nothing and "
                     + ("was refunded." if refunded else "could NOT be refunded: "
                        f"charge <code>{html.escape(charge)}</code>."))
            await self._send(update, "This payment would not have changed your plan, so "
                             + ("it has been refunded." if refunded else
                                "I've asked the operator to refund it."))
            return
        await self._send(update, f"✅ <b>{str(grant['tier']).capitalize()}</b> is active "
                         f"until {_until_text(grant['until'])}. /tier shows what it includes.")
