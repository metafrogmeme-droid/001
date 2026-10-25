"""/quarantine — hide unwanted tokens and NFTs, and do not touch them.

Admin-only while QUARANTINE_ENABLED is off. The command calls
``_is_admin``. A non-admin is told the command was not found while the
flag is off. With the flag on, the same command is the caller's own list.

The reply never carries an approve, transfer, or burn button. EVM
disposal stays off. Solana burn is not in this build.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from telegram import Update
from telegram.ext import ContextTypes

from bot.quarantine.book import contract_key, load_book, save_book
from bot.quarantine.flag import FLAG
from bot.quarantine.present import list_text, warn_text

if TYPE_CHECKING:
    from bot.utils.user_store import UserStore


class QuarantineCommands:
    """Hide list and lookalike warning. Host contract below."""

    if TYPE_CHECKING:
        users: UserStore

        async def _send(self, update: Update, text: str,
                        reply_markup=None, edit: bool = False) -> None: ...

        def _is_admin(self, update: Update) -> bool: ...

        def _get_tg_id(self, update: Update) -> str: ...

    async def _cmd_quarantine(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """/quarantine — hidden tokens and NFTs the bot will not touch."""
        from bot import admin_preview

        admin = self._is_admin(update)
        tg = self._get_tg_id(update)
        if not admin_preview.flag_on_for(tg, FLAG, store=self.users):
            await self._send(update, "Not found.")
            return
        # Preview is the operator, and only while the public flag is off.
        preview = admin and admin_preview.is_preview(tg, FLAG, store=self.users)
        args = list(getattr(ctx, "args", None) or [])
        verb = args[0].lower() if args else ""
        book = load_book(tg)
        if verb in ("", "list"):
            await self._send(update, list_text(book, preview=preview))
            return
        if book.get("state") == "unreadable":
            await self._send(update, list_text(book, preview=preview))
            return
        if verb == "hide":
            await self._send(update, _mutate(tg, book, args[1:], add=True, preview=preview))
            return
        if verb == "unhide":
            await self._send(update, _mutate(tg, book, args[1:], add=False, preview=preview))
            return
        if verb == "own":
            await self._send(update, _remember(tg, book, "own", args[1:], preview=preview))
            return
        if verb == "counterparty":
            await self._send(update, _remember(tg, book, "counterparties", args[1:], preview=preview))
            return
        if verb == "warn":
            dest = args[1] if len(args) > 1 else ""
            text = warn_text(dest, counterparties=list(book.get("counterparties") or []),
                             own=list(book.get("own") or []))
            if preview:
                text = "Admin preview. Not public.\n" + text
            await self._send(update, text)
            return
        await self._send(update, "Use /quarantine, hide, unhide, own, counterparty, or warn.")


def _asset_from(args: list[str]) -> Optional[dict]:
    if len(args) < 2:
        return None
    key = contract_key(args[0], args[1], args[2] if len(args) > 2 else "")
    if key is None:
        return None
    tid = "" if len(args) < 3 else args[2].strip()
    return {"chain_id": int(args[0]), "contract": args[1].lower(), "token_id": tid}


def _mutate(owner: str, book: dict, args: list[str], *, add: bool, preview: bool) -> str:
    row = _asset_from(args)
    if row is None:
        return "Name the chain id and the contract. Nothing was saved."
    assets = [a for a in book.get("assets") or [] if isinstance(a, dict)]
    key = contract_key(row["chain_id"], row["contract"], row["token_id"])
    kept = []
    for item in assets:
        if contract_key(item.get("chain_id"), item.get("contract"), item.get("token_id") or "") == key:
            continue
        kept.append(item)
    if add:
        kept.append(row)
    saved = save_book(owner, {
        "assets": kept,
        "own": book.get("own") or [],
        "counterparties": book.get("counterparties") or [],
    })
    if saved != "saved":
        return "The quarantine list could not be saved. Nothing was touched."
    fresh = load_book(owner)
    return list_text(fresh, preview=preview)


def _remember(owner: str, book: dict, field: str, args: list[str], *, preview: bool) -> str:
    from bot.quarantine.poison import canon
    parsed = canon(args[0]) if args else None
    if parsed is None:
        return "That address could not be read. Nothing was saved."
    addr = "0x" + parsed
    current = [a for a in book.get(field) or [] if isinstance(a, str)]
    if addr.lower() not in {a.lower() for a in current}:
        current.append(addr.lower())
    payload = {
        "assets": book.get("assets") or [],
        "own": book.get("own") or [],
        "counterparties": book.get("counterparties") or [],
    }
    payload[field] = current
    saved = save_book(owner, payload)
    if saved != "saved":
        return "The quarantine list could not be saved. Nothing was touched."
    return list_text(load_book(owner), preview=preview)
