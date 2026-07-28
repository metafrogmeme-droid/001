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


import re
from datetime import datetime, timezone

_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _save(d: dict) -> bool:
    """Atomic write. Never raises; False means nothing changed on disk."""
    try:
        os.makedirs(os.path.dirname(_path()) or ".", exist_ok=True)
        tmp = _path() + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(d, f)
        os.replace(tmp, _path())
        return True
    except Exception as exc:
        log.warning("user_strategy write failed: %s", exc)
        return False


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
    """The stored ENGINE preset key, or None. A community snapshot is not a
    preset key, so this returns None for it — callers that understand both
    shapes use get_entry()."""
    v = get_entry(user_id)
    if isinstance(v, str):
        return v
    return None


def get_entry(user_id):
    """The raw stored selection: a preset key (str) for engine presets, or a
    community snapshot dict {kind, slug, label, gates, armed_at}."""
    uid = str(user_id or "").strip()
    if not uid:
        return None
    v = _load().get(uid)
    if isinstance(v, dict) and v.get("kind") == "community" and v.get("slug"):
        return v
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
        if not _save(d):
            return None
    return key


def set_custom(user_id, slug, name, gates, source_rules=None) -> Optional[dict]:
    """Pin a COMMUNITY strategy by storing a validated SNAPSHOT of its
    signal-checkable gates.

    The bot cannot read the website's database, so what is stored here is a
    copy taken at arming time — not a live link. That is a fact about the
    system, so the snapshot carries `armed_at` and the surfaces SAY it is a
    snapshot: editing the strategy on the web does not silently re-arm the
    bot; the user re-arms. Gates are validated here (the caller's projection
    is not trusted blindly) — anything unrecognised is dropped rather than
    stored, so a malformed payload can never widen what the gate enforces.
    """
    uid = str(user_id or "").strip()
    sl = str(slug or "").strip().lower()
    if not uid or not sl or not _SLUG_RE.match(sl):
        return None
    g = {}
    src = gates if isinstance(gates, dict) else {}
    try:
        thr = src.get("confidence_threshold")
        if thr is not None and 0 < float(thr) <= 1:
            g["confidence_threshold"] = round(float(thr), 4)
    except (TypeError, ValueError):
        pass
    for key in ("symbols", "blocked_symbols"):
        v = src.get(key)
        if isinstance(v, (list, tuple)) and v:
            syms = [str(x).strip().upper()[:12] for x in v if str(x).strip()][:20]
            if syms:
                g[key] = syms
    if src.get("direction") in ("long_only", "short_only"):
        g["direction"] = src["direction"]
    rf = src.get("regime_filter")
    if rf and str(rf).lower() != "any":
        g["regime_filter"] = str(rf)[:24]
    entry = {
        "kind": "community", "slug": sl,
        "label": str(name or sl)[:80],
        "gates": g,
        "armed_at": _now_iso(),
    }
    with _LOCK:
        d = _load()
        d[uid] = entry
        if not _save(d):
            return None
    return entry


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
