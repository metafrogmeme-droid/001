"""Per-user strategy preference store (non-secret, JSON-backed).

"Your bot, your strategy": a user pins ONE of the engine's strategy presets
(the same catalogue /public/strategies serves) and their confirms are then
gated by that preset's rules — a tighten-only veto that can refuse a trade
for them but never invent one. This file mirrors user_leverage_store.py:
it holds no secret, just a preset key per user, so it never touches key
material and never takes a command down.

Fail-safe on READS of the file (a missing/corrupt preferences file returns
None and the caller treats the user as having no selection). The GATE built
on top of a stored selection is a different matter — an ARMED preference
that cannot be evaluated fails CLOSED at confirm time (see
bot/core/strategy_gate.py), because choosing a strategy must mean something
even mid-outage. The two directions are deliberate, not accidental.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from typing import Optional

log = logging.getLogger(__name__)
_LOCK = threading.Lock()


def _path() -> str:
    base = os.environ.get("RUNECLAW_STATE_DIR", "data")
    return os.path.join(base, "user_strategy.json")


def _load() -> dict:
    try:
        with open(_path(), "r", encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except FileNotFoundError:
        return {}
    except Exception as exc:  # pragma: no cover - defensive
        log.warning("user_strategy read failed: %s", exc)
        return {}


def get(user_id) -> Optional[str]:
    """The stored preset key for a user, or None (→ no per-user gating)."""
    uid = str(user_id or "").strip()
    if not uid:
        return None
    v = _load().get(uid)
    return str(v) if v else None


def set_pref(user_id, preset_key, valid_keys) -> Optional[str]:
    """Persist a strategy selection. `preset_key` must already be canonical
    (alias resolution is the caller's job) and must be in `valid_keys` —
    a selection that names no real preset is refused, never stored.
    Returns the stored key, or None. Never raises."""
    uid = str(user_id or "").strip()
    key = str(preset_key or "").strip().lower()
    if not uid or not key or key not in set(valid_keys or []):
        return None
    with _LOCK:
        d = _load()
        d[uid] = key
        try:
            os.makedirs(os.path.dirname(_path()) or ".", exist_ok=True)
            tmp = _path() + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(d, f)
            os.replace(tmp, _path())
        except Exception as exc:
            log.warning("user_strategy write failed: %s", exc)
            return None
    return key


def clear(user_id) -> bool:
    """Remove a user's selection (→ ungated confirms again). Revocable is the
    whole point; clearing always works. Never raises."""
    uid = str(user_id or "").strip()
    if not uid:
        return False
    with _LOCK:
        d = _load()
        if uid not in d:
            return False
        del d[uid]
        try:
            with open(_path(), "w", encoding="utf-8") as f:
                json.dump(d, f)
        except Exception as exc:  # pragma: no cover - defensive
            log.warning("user_strategy clear failed: %s", exc)
            return False
    return True
