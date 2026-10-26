"""Admin preview of a product flag that is still off for everyone else.

The public reader of each flag stays where it is (``scam_or_safe.flag_on``).
When that reader is off, this module asks the same question
``TelegramHandler._is_admin_id`` asks: the user-store role is ``admin``, or
the id is in ``ADMIN_TELEGRAM_IDS`` (``CONFIG.telegram.admin_ids``). There is
no second allowlist.

The anonymous Daily Duel process has no admin method of its own and must not
open ``UserStore`` — a failed read there renames the file. It reuses the same
rule: ``ADMIN_TELEGRAM_IDS`` at call time, and a read-only parse of
``data/users.json``. A missing, empty, or unreadable file is not an admin.
This module never creates or writes that file.

An unknown flag is off. A sibling command registers by adding the flag to
``_FLAGS`` and a public reader in ``public_on``, then calling
``flag_on_for(tg_id, FLAG)``. Do not name that reader ``enabled``: a second
module-level ``enabled`` is what the method sweep cannot tell apart from
``NewsRadar.enabled`` and ``ProofOfPnLPublisher.enabled``.
"""

from __future__ import annotations

import json
import os
from typing import Mapping, Optional

BANNER = "Admin preview. Not public."

# Product flags the Telegram surfaces in this tree actually gate. Web-only
# flags stay in app/lib/admin_preview.js. An unlisted name is off.
_FLAGS = frozenset({
    "SCAM_OR_SAFE_V0", "DUEL_CONFIDENCE_ENABLED", "SRB_ENABLED", "FVB_ENABLED",
})
# --- XNAV begin ---
_FLAGS = _FLAGS | frozenset({"XNAV_ENABLED"})
# --- XNAV end ---
# --- Incident pack begin ---
_FLAGS = _FLAGS | frozenset({"INCIDENT_PACK_ENABLED"})
# --- Incident pack end ---
_CONFIDENCE_ON = frozenset({"1", "true", "yes", "on"})


def public_on(flag: str, env: Optional[Mapping[str, str]] = None) -> bool:
    """Whether `flag` is on for everyone. An unknown flag is off."""
    if flag not in _FLAGS:
        return False
    if flag == "SCAM_OR_SAFE_V0":
        from bot.scam_or_safe import flag_on
        return flag_on(env) is True
    if flag == "SRB_ENABLED":
        from bot.srb.flag import flag_on
        return flag_on(env) is True
    if flag == "FVB_ENABLED":
        from bot.fvb.flag import flag_on
        return flag_on(env) is True
    # --- XNAV begin ---
    if flag == "XNAV_ENABLED":
        from bot.xnav.flag import flag_on
        return flag_on(env) is True
    # --- XNAV end ---
    # --- Incident pack begin ---
    if flag == "INCIDENT_PACK_ENABLED":
        from bot.incident_pack import flag_on
        return flag_on(env) is True
    # --- Incident pack end ---
    source = os.environ if env is None else env
    return str(source.get("DUEL_CONFIDENCE_ENABLED", "")).strip().lower() in _CONFIDENCE_ON


def _tg_id(user) -> str:
    if user is None or isinstance(user, bool):
        return ""
    if isinstance(user, (str, int)):
        return str(user).strip()
    person = getattr(user, "effective_user", None)
    if person is not None:
        return str(getattr(person, "id", "") or "").strip()
    return ""


def _admin_ids(env: Optional[Mapping[str, str]]) -> set[str]:
    """``ADMIN_TELEGRAM_IDS``. A passed mapping is the only source, so a test
    that blanks it is not answered by a developer's process environment."""
    if env is not None:
        raw = env.get("ADMIN_TELEGRAM_IDS", "")
    else:
        raw = ""
        try:
            from bot.config import CONFIG
            raw = CONFIG.telegram.admin_ids or ""
        except Exception:
            raw = os.environ.get("ADMIN_TELEGRAM_IDS", "")
    return {part.strip() for part in str(raw).split(",") if part.strip()}


def _role_admin(tg: str, store) -> bool:
    """The user-store half of ``_is_admin_id``. `store` is a mapping or an
    object with ``get``. None means read ``data/users.json`` and do not write."""
    if not tg:
        return False
    if store is not None:
        getter = getattr(store, "get", None)
        row = getter(tg) if callable(getter) else None
        return isinstance(row, dict) and row.get("role") == "admin"
    try:
        from bot.utils.paths import state_path
        path = state_path("data/users.json")
        if not path.is_file() or path.stat().st_size == 0:
            return False
        with path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError):
        return False
    if not isinstance(data, dict):
        return False
    row = data.get(tg)
    return isinstance(row, dict) and row.get("role") == "admin"


def telegram_admin(user, store=None, env: Optional[Mapping[str, str]] = None) -> bool:
    """``TelegramHandler._is_admin_id``, for a bare id or an Update."""
    tg = _tg_id(user)
    if not tg:
        return False
    if _role_admin(tg, store):
        return True
    return tg in _admin_ids(env)


def flag_on_for(user, flag: str, env: Optional[Mapping[str, str]] = None, store=None) -> bool:
    """Public flag, or this user is an admin while the flag is off."""
    if flag not in _FLAGS:
        return False
    if public_on(flag, env):
        return True
    return telegram_admin(user, store=store, env=env)


def is_preview(user, flag: str, env: Optional[Mapping[str, str]] = None, store=None) -> bool:
    """True only when the caller is an admin and the public flag is off.

    A failed public read is not preview: the caller of a gate that could not
    be asked stays off, the same as a non-admin.
    """
    if flag not in _FLAGS:
        return False
    if public_on(flag, env):
        return False
    return telegram_admin(user, store=store, env=env)
