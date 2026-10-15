"""Every setting an operator may change while the bot runs: one list, one write.

WHY THIS EXISTS. The bot's runtime settings were attributes on `RUNTIME`, and
each door that wrote one did its own share of the job:

* ``/leverage set`` wrote the attribute and nothing else, so a restart put the
  configured leverage back without a word to anyone;
* ``/autoconfirm`` wrote it and logged a line, and a restart undid it too;
* ``/drawdownlimit`` wrote it and saved it with the risk state, so it survived;
* ``/mode`` and the stance buttons wrote theirs and nothing else.

Three answers to "does my change survive a restart", and no answer at all to
"who changed this, and when". A website admin page built over that would have
been a fourth door with a fourth answer.

So every door now calls `apply` or `reset` here, and that one path:

* parses and bounds the value the same way for Telegram and the website;
* writes it through `RUNTIME`'s setter, which still clamps;
* saves it (``data/settings_overrides.json``; the live drawdown cap stays in
  the risk state, where it already lived and where its tests pin it);
* appends one line to ``data/settings_audit.jsonl``: when, who, through which
  door, the value before and after, and whether the change tightened or
  loosened the guard (`effect`, computed from `Setting.up`, never from words).

What this module does NOT decide is who may change a setting. The doors decide
that, as they always did (the operator check on Telegram, `_policy_op_guard`
on the gateway). And two settings are not here on purpose (`LOCKED`): live
trading on/off and the trading venue. Their doors do more than set a value
(``/golive`` asks for a typed CONFIRM, ``/venue`` checks the target venue's
keys and refuses while positions are open), and the website must never be
able to turn live trading on: a stolen web session would be able to trade
real money.

THE STANCE CHANGES NOTHING, and the registry says so. ``strategy_mode`` is
read by the /agent card, the war-room card and the dashboard, and by nothing
that sizes, selects or confirms a trade. Its `means` states that, so the admin
page does not repeat the claim the old cards made.
"""

from __future__ import annotations

import json
import logging
import threading
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Optional

from bot.utils.atomic_write import atomic_write_json
from bot.utils.paths import state_path

log = logging.getLogger(__name__)

#: Where operator settings are kept across restarts. Values only: a key that
#: is absent means "the configured default".
OVERRIDES_PATH = state_path("data/settings_overrides.json")
#: One JSON object per line, appended on every change through `apply`/`reset`.
AUDIT_PATH = state_path("data/settings_audit.jsonl")

#: How many bytes of the audit file's tail `recent_changes` reads. Bounded so
#: a file that has grown for a year is not read whole to show twenty lines.
_AUDIT_TAIL_BYTES = 64 * 1024

STANCES = ("defensive", "balanced", "aggressive", "manual")

#: What a stance is, said wherever one is offered or shown: /agent, the
#: proposal a "be more careful" message gets, the war room and this page.
#: The cards used to promise "smaller sizing bias, stricter setup selection"
#: and "you confirm every trade" for a value no trading code reads.
STANCE_NOTE = ("The stance is a label. It does not change how trades are "
               "sized, which setups are taken, or whether a trade needs a "
               "tap: those follow /leverage, /autoconfirm and the risk gate.")
UNIVERSES = ("all_markets", "all", "solana", "stocks", "hybrid", "metals",
             "commodities", "etfs", "pre_ipo", "tradfi")


@dataclass(frozen=True)
class Setting:
    """One operator setting. `up` is what RAISING it (or turning it on) does
    to the guard it belongs to: ``"tightens"``, ``"loosens"``, or None when
    the setting is not a guard at all (a choice of label or universe)."""
    key: str
    label: str
    kind: str                       # "choice" | "number" | "int" | "bool"
    means: str
    door: str                       # the Telegram command that also sets it
    up: Optional[str] = None
    choices: tuple[str, ...] = ()
    lo: Optional[float] = None
    hi: Optional[float] = None
    store: str = "overrides"        # "overrides" | "risk_state"


def _rt():
    from bot.config import RUNTIME
    return RUNTIME


def _cfg():
    from bot.config import CONFIG
    return CONFIG


def _lev_bounds() -> tuple[int, int]:
    from bot.config import RuntimeState
    return RuntimeState.LEVERAGE_OVERRIDE_MIN, RuntimeState.LEVERAGE_OVERRIDE_MAX


def _dd_bounds() -> tuple[float, float]:
    from bot.config import RuntimeState
    return RuntimeState.LIVE_DRAWDOWN_OVERRIDE_MIN, RuntimeState.LIVE_DRAWDOWN_OVERRIDE_MAX


SETTINGS: dict[str, Setting] = {s.key: s for s in (
    Setting(
        key="auto_confirm_threshold", label="Auto-confirm threshold",
        kind="number", door="/autoconfirm", up="tightens", lo=0.5, hi=1.0,
        means=("Ideas at or above this confidence are confirmed without a tap; "
               "1.00 turns auto-confirm off. Raising it tightens. The adaptive "
               "threshold may move the value in force from the one set here.")),
    Setting(
        key="leverage_override", label="Standard leverage",
        kind="int", door="/leverage set", up="loosens",
        lo=float(_lev_bounds()[0]), hi=float(_lev_bounds()[1]),
        means=("The leverage new positions are set to on the venue, never above "
               "MAX_LEVERAGE. Open positions keep theirs. Raising it loosens.")),
    Setting(
        key="live_drawdown_override_pct", label="Live drawdown cap",
        kind="number", door="/drawdownlimit", up="loosens",
        lo=_dd_bounds()[0], hi=_dd_bounds()[1], store="risk_state",
        means=("How far, in percent, the live account may fall from its peak "
               "before the bot halts. Raising it loosens: the bot accepts a "
               "larger real loss before stopping. It does not resume a halt.")),
    Setting(
        key="paper_auto_accept", label="Paper auto-accept",
        kind="bool", door="(website Admin page)", up="loosens",
        means=("On, a newcomer gets PAPER access at first contact without an "
               "admin; live trading stays a separate permission. Turning it "
               "on loosens the access gate.")),
    Setting(
        key="asset_universe", label="Asset universe",
        kind="choice", door="/mode", choices=UNIVERSES,
        means="Which markets the scan loop pulls, for every account."),
    Setting(
        key="strategy_mode", label="Stance",
        kind="choice", door="/agent", choices=STANCES,
        means=STANCE_NOTE),
)}

#: Shown on the admin page, never written from it. See the module docstring.
LOCKED: dict[str, str] = {
    "live_mode": ("Turned on only from Telegram with /golive CONFIRM, never from "
                  "the website: a stolen web session must not be able to trade "
                  "real money."),
    "venue": ("Switched from Telegram with /venue, which checks the target "
              "venue's keys and refuses while positions or orders are open."),
}


# ── reading and writing one setting ──────────────────────────────────────────

def _default(key: str) -> Any:
    """The value a setting returns to on `reset`."""
    cfg = _cfg()
    if key == "auto_confirm_threshold":
        return float(cfg.auto_confirm_threshold)
    if key == "paper_auto_accept":
        return bool(cfg.paper_auto_accept)
    if key == "asset_universe":
        return str(cfg.exchange.asset_universe)
    if key == "strategy_mode":
        return "balanced"
    return None     # leverage / drawdown: no override is the default


def read(key: str) -> Any:
    """The value in force. For leverage and the drawdown cap that is the
    override, None meaning "the configured default"."""
    rt = _rt()
    if key == "auto_confirm_threshold":
        return float(rt.auto_confirm_threshold)
    if key == "leverage_override":
        return rt.leverage_override
    if key == "live_drawdown_override_pct":
        return rt.live_drawdown_override_pct
    if key == "paper_auto_accept":
        return rt.paper_auto_accept_in_force(bool(_cfg().paper_auto_accept))
    if key == "asset_universe":
        return str(rt.asset_universe)
    if key == "strategy_mode":
        return str(rt.strategy_mode)
    raise KeyError(key)


def _write(key: str, value: Any) -> None:
    """Write through RUNTIME's setter. ``None`` returns to the default."""
    rt = _rt()
    if key == "auto_confirm_threshold":
        rt.auto_confirm_threshold = _default(key) if value is None else float(value)
    elif key == "leverage_override":
        rt.leverage_override = value
    elif key == "live_drawdown_override_pct":
        if value is None:
            rt.clear_live_drawdown_override()
        else:
            rt.live_drawdown_override_pct = value
    elif key == "paper_auto_accept":
        rt.paper_auto_accept_override = value
    elif key == "asset_universe":
        rt.asset_universe = _default(key) if value is None else value
    elif key == "strategy_mode":
        rt.strategy_mode = _default(key) if value is None else value
    else:
        raise KeyError(key)


def effective_text(key: str) -> Optional[str]:
    """What the value in force amounts to, where the raw value does not say
    it: an unset leverage override is the configured standard, under the
    ceiling; an unset drawdown cap is the configured one."""
    try:
        cfg, rt = _cfg(), _rt()
        if key == "leverage_override":
            from bot.core.leverage import operator_standard
            std = operator_standard(cfg.exchange, rt.leverage_override)
            return f"{std.leverage}x ({std.source_note()})"
        if key == "live_drawdown_override_pct":
            v = rt.live_drawdown_override_pct
            if v is None:
                return (f"{float(cfg.risk.live_max_drawdown_pct):g}% "
                        "(LIVE_MAX_DRAWDOWN_PCT)")
            return f"{float(v):g}% (override)"
        if key == "auto_confirm_threshold":
            v = float(rt.auto_confirm_threshold)
            return "off (every trade needs a tap)" if v >= 1.0 else f"{v:.0%} and above"
    except Exception as exc:
        log.debug("settings: effective value of %s unreadable: %s", key, exc)
        return None
    return None


def _as_number(key: str, value: Any) -> Optional[float]:
    """A value on the axis `Setting.up` speaks about. None (no override) is
    the configured default; None back means it could not be read."""
    try:
        if value is None:
            cfg = _cfg()
            if key == "leverage_override":
                from bot.core.leverage import operator_standard
                return float(operator_standard(cfg.exchange, None).leverage)
            if key == "live_drawdown_override_pct":
                return float(cfg.risk.live_max_drawdown_pct)
            return None
        if isinstance(value, bool):
            return 1.0 if value else 0.0
        return float(value)
    except Exception:
        return None


def effect(key: str, before: Any, after: Any) -> Optional[str]:
    """``"tightens"``, ``"loosens"``, ``"neither"`` or ``"unknown"``; None when
    nothing changed. Computed from `Setting.up`, so a door's wording cannot
    call a loosening a tightening."""
    s = SETTINGS[key]
    if before == after:
        return None
    if s.up is None:
        return "neither"
    a, b = _as_number(key, before), _as_number(key, after)
    if a is None or b is None:
        return "unknown"
    if a == b:
        return None
    raised = b > a
    if raised:
        return s.up
    return "loosens" if s.up == "tightens" else "tightens"


def parse(key: str, raw: Any) -> Any:
    """The typed value for ``raw``, or ValueError with a sentence a person can
    read. Bounds refuse rather than clamp here, except where the Telegram door
    has always clamped (leverage, the drawdown cap): the setter clamps those
    and `apply` reports that it did."""
    s = SETTINGS[key]
    if s.kind == "bool":
        if isinstance(raw, bool):
            return raw
        word = str(raw).strip().lower()
        if word in ("1", "true", "on", "yes"):
            return True
        if word in ("0", "false", "off", "no"):
            return False
        raise ValueError(f"{s.label} takes on or off.")
    if s.kind == "choice":
        word = str(raw).strip().lower()
        if word not in s.choices:
            raise ValueError(f"{s.label} is one of: {', '.join(s.choices)}.")
        return word
    try:
        num = float(str(raw).strip().rstrip("x%"))
    except (TypeError, ValueError):
        raise ValueError(f"{s.label} takes a number.") from None
    if num != num or num in (float("inf"), float("-inf")):
        raise ValueError(f"{s.label} takes a number.")
    if key == "auto_confirm_threshold" and not (s.lo <= num <= s.hi):  # type: ignore[operator]
        raise ValueError(f"{s.label} must be between {s.lo:.2f} and {s.hi:.2f}.")
    if s.kind == "int":
        return int(num)
    return num


# ── persistence ──────────────────────────────────────────────────────────────

def load_overrides() -> tuple[Optional[dict], Optional[str]]:
    """``(values, None)``, ``({}, None)`` for no file, or ``(None, <class>)``
    for a file that could not be read. Unreadable is not "nothing saved": a
    caller that wrote over an unreadable file would erase every other
    setting's saved value with it."""
    try:
        with open(OVERRIDES_PATH, encoding="utf-8") as fh:
            data = json.load(fh)
    except FileNotFoundError:
        return {}, None
    except (OSError, ValueError) as exc:
        return None, type(exc).__name__
    values = data.get("values") if isinstance(data, dict) else None
    if not isinstance(values, dict):
        return None, "BadShape"
    return {k: v for k, v in values.items() if k in SETTINGS}, None


def _save_override(key: str, value: Any) -> tuple[bool, Optional[str]]:
    values, err = load_overrides()
    if values is None:
        return False, f"the saved settings file is unreadable ({err})"
    if value is None:
        values.pop(key, None)
    else:
        values[key] = value
    try:
        atomic_write_json(OVERRIDES_PATH, {
            "version": 1, "values": values,
            "saved_at": datetime.now(UTC).isoformat()})
    except OSError as exc:
        return False, f"the write did not land ({type(exc).__name__})"
    return True, None


def _persist(s: Setting, value: Any, engine: Any) -> tuple[Optional[bool], Optional[str]]:
    """Save one setting where it lives. True saved, False failed, None not
    confirmed (no engine, or a process that does not write the bot's state)."""
    if s.store == "overrides":
        return _save_override(s.key, value)
    if engine is None or getattr(engine, "_state_persistence_detached", False):
        return None, "this process does not write the bot's risk state"
    try:
        landed = engine.risk._save_state()
    except Exception as exc:
        return False, f"the risk state did not save ({type(exc).__name__})"
    if landed is None:
        return None, "this process does not write the bot's risk state"
    return (True, None) if landed else (False, "the risk state did not save")


def restore_overrides() -> dict:
    """Apply the saved settings to RUNTIME. Called once at boot, before the
    engine. A value the setter refuses is skipped and logged, never fatal;
    the setters clamp, so a hand-edited file cannot carry a value past a
    bound. Returns ``{"applied": [...], "skipped": [...], "error": ...}``."""
    out: dict = {"applied": [], "skipped": [], "error": None}
    values, err = load_overrides()
    if values is None:
        out["error"] = err
        log.warning("settings: %s unreadable (%s); every setting starts at "
                    "its configured value", OVERRIDES_PATH, err)
        return out
    for key, raw in values.items():
        s = SETTINGS.get(key)
        if s is None or s.store != "overrides":
            out["skipped"].append(key)
            continue
        try:
            _write(key, parse(key, raw))
            out["applied"].append(key)
        except (ValueError, TypeError) as exc:
            out["skipped"].append(key)
            log.warning("settings: saved %s not applied: %s", key, exc)
    return out


# ── the audit trail ──────────────────────────────────────────────────────────

def _append_audit(row: dict) -> bool:
    try:
        AUDIT_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(AUDIT_PATH, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, default=str) + "\n")
        return True
    except OSError as exc:
        log.warning("settings: audit line not written (%s)", type(exc).__name__)
        return False


def recent_changes(limit: int = 20) -> tuple[Optional[list], Optional[str]]:
    """The newest ``limit`` audit rows, newest first: ``([], None)`` for no
    file, ``(None, <class>)`` for one that could not be read. A row that does
    not parse is skipped and counted, not shown as a change."""
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
    if len(tail.encode("utf-8")) >= _AUDIT_TAIL_BYTES and lines:
        lines = lines[1:]           # the first line may be cut mid-row
    rows = []
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


# ── the one write path ───────────────────────────────────────────────────────

_LOCK = threading.Lock()


def _commit(key: str, value: Any, requested: Any, *, actor: str, via: str,
            engine: Any) -> dict:
    s = SETTINGS[key]
    with _LOCK:
        before = read(key)
        _write(key, value)
        after = read(key)
        saved_value = None if value is None else after
        persisted, why = _persist(s, saved_value, engine)
        eff = effect(key, before, after)
        clamped = (value is not None and s.kind in ("number", "int")
                   and after is not None and abs(float(after) - float(value)) > 1e-9)
        row = {"at": datetime.now(UTC).isoformat(), "key": key,
               "before": before, "after": after, "requested": requested,
               "reset": value is None, "actor": actor, "via": via,
               "effect": eff, "persisted": persisted}
        logged = _append_audit(row)
    return {"ok": True, "key": key, "label": s.label, "before": before,
            "after": after, "reset": value is None, "clamped": clamped,
            "effect": eff, "persisted": persisted, "persist_note": why,
            "logged": logged, "effective": effective_text(key)}


def apply(key: str, raw: Any, *, actor: str, via: str, engine: Any = None) -> dict:
    """Set ``key`` from ``raw`` through the one write path. Never raises for a
    bad key or value: the answer carries ``ok: False`` and a sentence."""
    if not actor or not via:
        raise ValueError("a settings change must name who made it and how")
    if key not in SETTINGS:
        return {"ok": False, "error": "unknown_setting",
                "detail": "There is no setting by that name."}
    try:
        value = parse(key, raw)
    except ValueError as exc:
        return {"ok": False, "error": "bad_value", "detail": str(exc)}
    return _commit(key, value, raw, actor=actor, via=via, engine=engine)


def reset(key: str, *, actor: str, via: str, engine: Any = None) -> dict:
    """Return ``key`` to its configured default and forget the saved value."""
    if not actor or not via:
        raise ValueError("a settings change must name who made it and how")
    if key not in SETTINGS:
        return {"ok": False, "error": "unknown_setting",
                "detail": "There is no setting by that name."}
    return _commit(key, None, None, actor=actor, via=via, engine=engine)


# ── the admin page's reading ─────────────────────────────────────────────────

def _source(s: Setting, value: Any, saved: Any, saved_ok: bool) -> str:
    """Where the value in force came from, in one word the page can say."""
    if s.store == "risk_state":
        return "default" if value is None else "set"
    if not saved_ok:
        return "unknown"
    if saved is None:
        return "default" if value == _default(s.key) else "unsaved"
    try:
        same = parse(s.key, saved) == value
    except ValueError:
        same = False
    return "set" if same else "moved"


def _locked_rows(engine: Any) -> list[dict]:
    rows = []
    try:
        live: Optional[str] = "on" if _cfg().is_live() else "off"
    except Exception:
        live = None
    rows.append({"key": "live_mode", "label": "Live trading", "value": live,
                 "why": LOCKED["live_mode"]})
    venue: Optional[str] = None
    try:
        v = getattr(getattr(engine, "live_executor", None), "_venue", None)
        if v is None:
            from bot.core.venues import get_venue
            v = get_venue()
        venue = str(v.display_name)
    except Exception:
        venue = None
    rows.append({"key": "venue", "label": "Trading venue", "value": venue,
                 "why": LOCKED["venue"]})
    return rows


def _flag_rows() -> tuple[Optional[list], Optional[str]]:
    """The self-audit's allow-listed flags with their values in force: set in
    .env, read at boot, shown here and changed nowhere here."""
    try:
        from bot.core.self_audit import FLAG_MEANINGS, FLAG_UP_DIRECTION, values_in_force
        vals = values_in_force()
        return [{"name": k, "value": vals.get(k), "means": FLAG_MEANINGS.get(k),
                 "up": FLAG_UP_DIRECTION.get(k)} for k in sorted(vals)], None
    except Exception as exc:
        return None, type(exc).__name__


def snapshot(engine: Any = None, changes: int = 20) -> dict:
    """Everything the admin page shows, in one read. Each part that could not
    be read is None with the class of what went wrong beside it, so one dead
    source leaves the rest standing and says which one is missing."""
    saved, saved_err = load_overrides()
    rows = []
    for s in SETTINGS.values():
        try:
            value: Any = read(s.key)
            value_err = None
        except Exception as exc:
            value, value_err = None, type(exc).__name__
        sv = (saved or {}).get(s.key) if s.store == "overrides" else None
        rows.append({
            "key": s.key, "label": s.label, "kind": s.kind, "means": s.means,
            "door": s.door, "up": s.up, "choices": list(s.choices),
            "lo": s.lo, "hi": s.hi, "store": s.store,
            "value": value, "value_error": value_err,
            "default": _default(s.key), "effective": effective_text(s.key),
            "saved": sv,
            "source": ("unknown" if value_err else
                       _source(s, value, sv, saved is not None)),
        })
    changes_rows, changes_err = recent_changes(changes)
    flags, flags_err = _flag_rows()
    return {
        "settings": rows,
        "saved_readable": saved is not None, "saved_error": saved_err,
        "locked": _locked_rows(engine),
        "changes": changes_rows, "changes_error": changes_err,
        "flags": flags, "flags_error": flags_err,
    }


def saved_sentence(result: dict) -> str:
    """The line a door adds after a change, from `apply`/`reset`'s answer:
    whether the change survives a restart. One sentence for Telegram and the
    website, so the two cannot disagree about it."""
    persisted = result.get("persisted")
    note = result.get("persist_note") or ""
    if persisted is True:
        return "Saved: it survives a restart."
    if persisted is False:
        return f"⚠️ Not saved, so a restart puts it back: {note}."
    return f"Not confirmed saved: {note}." if note else "Not confirmed saved."
