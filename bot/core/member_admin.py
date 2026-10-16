"""Every operator change to a member, through one write path.

Four Telegram commands change a member: ``/approve``, ``/revoke``,
``/set_tier`` and ``/revoke_live``. Each validated its own arguments, wrote the
store its own way, and nothing recorded who changed whom or from where. The
admin page adds a second door, and a second copy of four validations is how
two doors come to disagree about who may be let in. So the rules live here,
both doors call them, and every change is appended to
``data/member_admin_audit.jsonl``: when, who, which door, what, and the
member's state before and after.

WHAT THE WEBSITE MAY NOT DO. A web session is one stolen cookie away from
someone else, so these stay Telegram-only, refused here by door:

* granting the ``admin`` role (an operator is a vouched Telegram identity);
* setting the ``admin`` tier (the website reads ``plan == 'admin'`` as THE
  OPERATOR, ``app/lib/operator_view.js``);
* granting live trading (``/grant_live`` has no web door at all);
* any change to an account that holds the admin role or tier. Taking admin
  away is as much a change of who runs the bot as giving it: from the website
  it would let one session demote the operator, its own row included, with a
  tap on the select beside their name.

What the website may do loosens nothing an attacker could use against the
book: approve a member as paper, trader or viewer, revoke a member, set a
tier below admin, and take live trading away, for members who are not admins.

Each function returns an :class:`Outcome`. ``error`` is a machine code; for a
refusal the Telegram commands have always printed, it is the i18n key they
print, with ``params`` for its placeholders, so the Telegram replies are the
same sentences they were.
"""

from __future__ import annotations

import json
import logging
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

from bot.utils.paths import state_path
from bot.utils.user_store import SELF_ADMISSION_ROLE, StoreWriteHeld, is_vouchable

log = logging.getLogger("runeclaw.member_admin")

#: One JSON object per line, appended on every change through this module.
AUDIT_PATH = state_path("data/member_admin_audit.jsonl")
#: How many bytes of the audit file's tail `recent_changes` reads.
_AUDIT_TAIL_BYTES = 64 * 1024

TELEGRAM = "telegram"
WEBSITE = "website"
DOORS = (TELEGRAM, WEBSITE)

#: Roles /approve grants. `paper` is grantable on purpose: it is where a
#: self-admitted member already sits, so an admin can keep someone there or
#: move a trader back down to it without revoking access outright.
ROLES_GRANTABLE = ("trader", SELF_ADMISSION_ROLE, "viewer", "admin")
#: What the website may grant: everything but `admin`.
ROLES_WEB = tuple(r for r in ROLES_GRANTABLE if r != "admin")

#: Refusal codes the website door has and Telegram never reaches.
ROLE_TELEGRAM_ONLY = "role_telegram_only"
TIER_TELEGRAM_ONLY = "tier_telegram_only"
ADMIN_TELEGRAM_ONLY = "admin_telegram_only"

_LOCK = threading.Lock()


@dataclass
class Outcome:
    """What a change did. ``ok`` False with ``error`` set is a refusal, and
    nothing was written, except ``held``: the change is in force in this
    process and could not be saved (`StoreWriteHeld`)."""
    ok: bool
    action: str
    target: str
    error: str = ""
    params: dict = field(default_factory=dict)
    name: str = ""
    before: Any = None
    after: Any = None
    held: bool = False
    logged: bool = False

    def as_dict(self) -> dict:
        return {"ok": self.ok, "action": self.action, "target": self.target,
                "error": self.error or None, "params": self.params, "name": self.name,
                "before": self.before, "after": self.after, "held": self.held,
                "logged": self.logged}


def _is_ascii_digits(value: str) -> bool:
    return bool(value) and value.isascii() and value.isdigit()


def _is_web_member(value: str) -> bool:
    """`web:<id>`, the store's key for a website-only member, read by the
    gateway's own pattern (the one door that provisions them)."""
    from bot.web.user_gateway import _is_web_id
    return _is_web_id(value)


def _state(users, target: str) -> Optional[dict]:
    """A member's role, authorization, tier and live flag, or None when the
    store has no record of them."""
    u = users.get(target)
    if not u:
        return None
    return {"role": u.get("role"), "authorized": u.get("authorized"),
            "tier": u.get("tier"), "live": u.get("can_trade_live")}


def _name(users, target: str) -> str:
    """The member's name as the commands always printed it: "Unknown" for no
    record, the record's own name otherwise (an empty one stays empty)."""
    u = users.get(target)
    return str(u.get("name", "Unknown")) if u else "Unknown"


def _holds_admin(state: Optional[dict]) -> bool:
    """Whether a member's recorded state carries the admin role or tier."""
    if not state:
        return False
    return state.get("role") == "admin" or state.get("tier") == "admin"


def _web_on_admin(out: Outcome, via: str) -> bool:
    """Refuse the website a change to an admin account; True when refused."""
    if via == WEBSITE and _holds_admin(out.before):
        out.error = ADMIN_TELEGRAM_ONLY
        return True
    return False


def _check_door(via: str) -> None:
    if via not in DOORS:
        raise ValueError(f"unknown door {via!r}")


def _append_audit(row: dict) -> bool:
    try:
        AUDIT_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(AUDIT_PATH, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, default=str) + "\n")
        return True
    except OSError as exc:
        log.warning("members: audit line not written (%s)", type(exc).__name__)
        return False


def _record(out: Outcome, *, by: str, via: str) -> Outcome:
    out.logged = _append_audit({
        "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "actor": str(by), "via": via, "action": out.action, "target": out.target,
        "before": out.before, "after": out.after, "held": out.held,
    })
    return out


def recent_changes(limit: int = 30) -> tuple[Optional[list], Optional[str]]:
    """The newest ``limit`` audit rows, newest first: ``([], None)`` for no
    file, ``(None, <class>)`` for one that could not be read. A row that does
    not parse is skipped, not shown as a change."""
    try:
        with open(AUDIT_PATH, "rb") as fh:
            fh.seek(0, 2)
            size = fh.tell()
            fh.seek(max(0, size - _AUDIT_TAIL_BYTES))
            tail = fh.read().decode("utf-8", errors="replace")
    except FileNotFoundError:
        return [], None
    except OSError as exc:
        return None, type(exc).__name__
    lines = tail.splitlines()
    if size > _AUDIT_TAIL_BYTES and lines:
        lines = lines[1:]           # the first line may be cut mid-row
    rows: list = []
    for line in reversed(lines):
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict):
            rows.append(row)
        if len(rows) >= limit:
            break
    return rows, None


# ── the four changes ─────────────────────────────────────────────────────────

def approve(users, target: Any, role: Optional[str], *, by: Any, via: str) -> Outcome:
    """Let a member in with ``role`` (default trader). The id must be one a
    person can be vouched for (`is_vouchable`): a web-only account cannot be."""
    _check_door(via)
    target = str(target or "").strip()
    role = (role or "trader").strip().lower()
    out = Outcome(ok=False, action="approve", target=target)
    if not is_vouchable(target):
        out.error = "invalid_tg_id_numeric"
        return out
    if role not in ROLES_GRANTABLE:
        out.error, out.params = "invalid_role", {"role": role}
        return out
    if via == WEBSITE and role not in ROLES_WEB:
        out.error, out.params = ROLE_TELEGRAM_ONLY, {"role": role}
        return out
    with _LOCK:
        out.before = _state(users, target)
        if _web_on_admin(out, via):
            return out
        # `by=` is what makes this stick: without it the store records an
        # authorization the allowlist does not read.
        if not users.authorize(target, role=role, by=str(by)):
            out.error, out.params = "approve_failed", {"id": target}
            return out
        out.after = _state(users, target)
    out.ok, out.name = True, _name(users, target)
    out.params = {"role": role}
    return _record(out, by=str(by), via=via)


def revoke(users, target: Any, *, by: Any, via: str) -> Outcome:
    """Take a member's access away. Never the caller's own."""
    _check_door(via)
    target = str(target or "").strip()
    out = Outcome(ok=False, action="revoke", target=target)
    if not target.isdigit():
        out.error = "invalid_tg_id_format"
        return out
    if target == str(by):
        out.error = "cannot_revoke_self"
        return out
    with _LOCK:
        out.before = _state(users, target)
        if _web_on_admin(out, via):
            return out
        if not users.revoke(target):
            out.error, out.params = "user_not_found_id", {"id": target}
            return out
        out.after = _state(users, target)
    out.ok, out.name = True, _name(users, target)
    return _record(out, by=str(by), via=via)


def set_tier(users, target: Any, tier: Optional[str], *, by: Any, via: str) -> Outcome:
    """Set a member's tier. A website-only member is ``web:<id>``; neither it
    nor the website door may set the operator tier."""
    from bot.utils.user_store import TIERS
    _check_door(via)
    target = str(target or "").strip()
    tier = str(tier or "").strip().lower()
    out = Outcome(ok=False, action="set_tier", target=target)
    is_web = _is_web_member(target)
    if not (is_web or _is_ascii_digits(target)):
        out.error = "invalid_tg_id"
        return out
    if tier not in TIERS:
        out.error, out.params = "invalid_tier", {"tier": tier, "valid": list(TIERS)}
        return out
    if is_web and tier == "admin":
        out.error = "set_tier_web_admin"
        return out
    if via == WEBSITE and tier == "admin":
        out.error, out.params = TIER_TELEGRAM_ONLY, {"tier": tier}
        return out
    with _LOCK:
        if not users.get(target):
            out.error, out.params = "user_not_found_id_period", {"id": target}
            return out
        out.before = _state(users, target)
        if _web_on_admin(out, via):
            return out
        if not users.set_tier(target, tier):
            out.error = "set_tier_failed"
            return out
        out.after = _state(users, target)
    out.ok, out.name = True, _name(users, target)
    out.params = {"tier": tier, "is_web": is_web}
    # The website's `users.plan` follows the bot's tier authority: mirrored
    # in the background, best-effort, from either door.
    try:
        from bot.utils.website_sync import sync_tiers_in_background
        sync_tiers_in_background(users.all_tiers())
    except Exception as exc:  # noqa: BLE001
        log.warning("members: tier mirror not started (%s)", type(exc).__name__)
    return _record(out, by=str(by), via=via)


def revoke_live(users, target: Any, *, by: Any, via: str) -> Outcome:
    """Restrict a member to paper. A write the disk refused is in force in
    this process and not saved, and the outcome says so (``held``)."""
    _check_door(via)
    target = str(target or "").strip()
    out = Outcome(ok=False, action="revoke_live", target=target)
    if not target.isdigit():
        out.error = "invalid_tg_id"
        return out
    with _LOCK:
        out.before = _state(users, target)
        if _web_on_admin(out, via):
            return out
        try:
            ok = users.set_live_trading(target, False)
        except StoreWriteHeld as exc:
            out.error, out.params, out.held = "revoke_live_held", {"id": target, "cls": exc.detail}, True
            out.after = _state(users, target)
            return _record(out, by=str(by), via=via)
        if not ok:
            out.error = "user_not_found"
            return out
        out.after = _state(users, target)
    out.ok, out.name = True, _name(users, target)
    return _record(out, by=str(by), via=via)


# ── telling the member ───────────────────────────────────────────────────────

_SEP = "\u2500" * 16


async def notify_approved(bot, users, target: str, role: str) -> bool:
    """Tell an approved member they are in, in their own language. False when
    it could not be delivered: they may not have started the bot, and the
    approval stands either way."""
    from bot.utils.i18n import get_user_lang, t
    if bot is None:
        return False
    try:
        await bot.send_message(
            chat_id=int(target),
            text=f"🟢 {t('access_granted', get_user_lang(users, target), sep=_SEP, role=role)}",
            parse_mode="HTML")
        return True
    except Exception:  # noqa: BLE001
        return False


async def notify_tier(bot, users, target: str, tier: str) -> bool:
    """Tell a member their tier changed: "changed", not "upgraded", since the
    same change lowers one. A website-only member has no chat to write to, so
    the website emails them instead (when it holds a real address and a mail
    relay; best-effort, in the background)."""
    if _is_web_member(target):
        try:
            from bot.utils.website_sync import notify_tier_change_in_background
            notify_tier_change_in_background(target, tier)
            return True
        except Exception:  # noqa: BLE001
            return False
    from bot.utils.i18n import get_user_lang, t
    if bot is None:
        return False
    try:
        lang = get_user_lang(users, target)
        label = users.tier_label(target)
        await bot.send_message(
            chat_id=int(target),
            text=f"\U0001f3af {t('tier_changed', lang, tier_label=label)}",
            parse_mode="HTML")
        return True
    except Exception:  # noqa: BLE001
        return False


# ── the roster ───────────────────────────────────────────────────────────────

def roster(users, can_trade_live, limit: Optional[int] = None) -> list[dict]:
    """Every member, newest first, as the admin page shows them. A field the
    store does not hold is None, never a default dressed as a value: a record
    with no tier is not a basic-tier record (`bot/formatters/user_roster.py`)."""
    rows = []
    for u in users.list_users():
        tid = str(u.get("telegram_id") or "")
        try:
            live = bool(can_trade_live(tid)) if tid else None
        except Exception:  # noqa: BLE001
            live = None
        rows.append({
            "id": tid, "name": u.get("name"), "role": u.get("role"),
            "authorized": u.get("authorized"), "tier": u.get("tier"),
            "live": live, "admitted_by": u.get("admitted_by"),
            "created_at": u.get("created_at"), "last_seen": u.get("last_seen"),
        })
    rows.sort(key=lambda r: str(r.get("created_at") or ""), reverse=True)
    return rows[:limit]
