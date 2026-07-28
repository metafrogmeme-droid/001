"""Per-user strategy gate — the chosen preset, enforced at confirm time.

The Guardian principle on the bot's own confirm path: the user picks a
strategy in their own words (a preset from the same catalogue the
marketplace serves), and this gate makes that choice REFUSE non-compliant
confirms for them. Deterministic, tighten-only: it can veto a trade, it can
never create one, and it never touches the operator's global stance.

Honesty rules (the same contract the web Authority Envelope carries):
- Only gates the confirm-time facts can truly evaluate are enforced.
  A TradeIdea carries its asset and its confidence; it does NOT carry the
  scan-time RSI, regime or volume-spike readings. So `confidence_threshold`
  and an explicit `symbols` list enforce here; `rsi_threshold`, `regime`,
  `volume_spike_min` and the "top3_volume" symbol rule apply in the SCAN
  (where those facts exist) and are reported as `scan_only` — the gate
  never claims more protection than it delivers.
- Every refusal names its rule with the numbers that tripped it.
- No selection → ok (an unselected user confirms exactly as before).

Pure: preset + idea facts in, verdict out. The engine does the I/O.
"""

from __future__ import annotations

from typing import Any, Optional


def _base(sym: str) -> str:
    """'BTC/USDT:USDT' | 'BTCUSDT' | 'BTC' → 'BTC' (base ticker)."""
    s = str(sym or "").upper().split(":")[0].split("/")[0].strip()
    if s.endswith("USDT") and len(s) > 4:
        s = s[:-4]
    return s


def resolve_key(raw, presets, aliases) -> Optional[str]:
    """slug ('dip-sniper') | alias ('dip') | key ('dip sniper') → canonical
    preset key, or None. The web and Telegram surfaces speak different
    spellings of the same catalogue; the store only ever holds the key."""
    r = str(raw or "").strip().lower()
    if not r:
        return None
    r = (aliases or {}).get(r, r)
    if r in (presets or {}):
        return r
    r2 = r.replace("-", " ")
    return r2 if r2 in (presets or {}) else None


def describe_gates(preset: dict) -> tuple[list[str], list[str]]:
    """(confirm_gates, scan_gates) — the SAME split check_confirm enforces,
    exported so every surface states it identically and none can overclaim."""
    confirm: list[str] = []
    scan: list[str] = []
    for g in ("rsi_threshold", "regime", "volume_spike_min"):
        if preset.get(g) is not None:
            scan.append(g)
    syms = preset.get("symbols")
    if isinstance(syms, (list, tuple)) and syms:
        confirm.append("symbols")
    elif isinstance(syms, str) and syms:
        scan.append(f"symbols:{syms}")
    if preset.get("confidence_threshold") is not None:
        confirm.append(f"confidence>={float(preset['confidence_threshold']) * 100:.0f}%")
    return confirm, scan


def check_custom(entry: Optional[dict], asset: str, confidence: Any,
                 direction: Any = None) -> dict:
    """The same contract for a COMMUNITY snapshot: only signal-checkable
    gates enforce, refusals name their rule, an unreadable snapshot fails
    CLOSED. `regime_filter` is scan-only here — a TradeIdea carries no
    regime reading, so claiming it would be claiming more than we deliver.
    """
    if not isinstance(entry, dict) or not entry.get("slug"):
        return {"ok": False, "enforced": [], "scan_only": [],
                "reason": ("Your pinned strategy could not be read — confirms are "
                           "refused until it can be (fail closed). "
                           "/mystrategy off disarms it.")}
    g = entry.get("gates") or {}
    label = entry.get("label") or entry.get("slug")
    enforced: list[str] = []
    scan_only: list[str] = []
    if g.get("regime_filter"):
        scan_only.append("regime")

    base = _base(asset)
    syms = g.get("symbols")
    if isinstance(syms, (list, tuple)) and syms:
        enforced.append("symbols")
        allow = {_base(x) for x in syms}
        if base not in allow:
            return {"ok": False, "enforced": enforced, "scan_only": scan_only,
                    "reason": (f"{label}: {base} is outside this strategy's symbols "
                               f"({', '.join(sorted(allow))}).")}
    deny = g.get("blocked_symbols")
    if isinstance(deny, (list, tuple)) and deny:
        enforced.append("blocked")
        if base in {_base(x) for x in deny}:
            return {"ok": False, "enforced": enforced, "scan_only": scan_only,
                    "reason": f"{label}: {base} is on this strategy's blocked list."}
    d = g.get("direction")
    if d in ("long_only", "short_only"):
        enforced.append("direction")
        got = str(direction or "").upper()
        want = "LONG" if d == "long_only" else "SHORT"
        if got and got != want:
            return {"ok": False, "enforced": enforced, "scan_only": scan_only,
                    "reason": f"{label}: this strategy is {want.lower()}-only — {got} refused."}
    thr = g.get("confidence_threshold")
    if thr is not None:
        enforced.append("confidence")
        try:
            conf = float(confidence)
        except (TypeError, ValueError):
            return {"ok": False, "enforced": enforced, "scan_only": scan_only,
                    "reason": (f"{label}: this idea carries no readable confidence — "
                               "refused (unreadable is never assumed to pass).")}
        if conf < float(thr):
            return {"ok": False, "enforced": enforced, "scan_only": scan_only,
                    "reason": (f"{label}: confidence {conf * 100:.0f}% is below this "
                               f"strategy's {float(thr) * 100:.0f}% floor.")}
    return {"ok": True, "enforced": enforced, "scan_only": scan_only}


def check_confirm(preset_key: str, preset: Optional[dict],
                  asset: str, confidence: Any) -> dict:
    """→ {ok, reason (when refused), enforced: [..], scan_only: [..]}.

    `preset is None` means the stored selection names a preset that no
    longer exists — that is an ARMED gate that cannot be evaluated, and it
    fails CLOSED: choosing a strategy must mean something even when the
    catalogue shifts underneath it. (/mystrategy off always works.)
    """
    if not preset_key:
        return {"ok": True, "enforced": [], "scan_only": []}
    if not isinstance(preset, dict):
        return {"ok": False, "enforced": [], "scan_only": [],
                "reason": (f"Your chosen strategy '{preset_key}' could not be read — "
                           "confirms are refused until it can be (fail closed). "
                           "/mystrategy off disarms it.")}

    enforced: list[str] = []
    scan_only: list[str] = []
    for g in ("rsi_threshold", "regime", "volume_spike_min"):
        if preset.get(g) is not None:
            scan_only.append(g)

    syms = preset.get("symbols")
    if isinstance(syms, (list, tuple)) and syms:
        enforced.append("symbols")
        allow = {_base(s) for s in syms}
        if _base(asset) not in allow:
            return {"ok": False, "enforced": enforced, "scan_only": scan_only,
                    "reason": (f"{preset.get('label', preset_key)}: {_base(asset)} is outside "
                               f"this strategy's symbols ({', '.join(sorted(allow))}).")}
    elif isinstance(syms, str) and syms:
        # e.g. "top3_volume" — a scan-time universe rule; confirm-time has no
        # volume ranking to evaluate, so it is stated, never silently claimed.
        scan_only.append(f"symbols:{syms}")

    thr = preset.get("confidence_threshold")
    if thr is not None:
        enforced.append("confidence")
        try:
            conf = float(confidence)
        except (TypeError, ValueError):
            return {"ok": False, "enforced": enforced, "scan_only": scan_only,
                    "reason": (f"{preset.get('label', preset_key)}: this idea carries no readable "
                               "confidence — refused (unreadable is never assumed to pass).")}
        if conf < float(thr):
            return {"ok": False, "enforced": enforced, "scan_only": scan_only,
                    "reason": (f"{preset.get('label', preset_key)}: confidence "
                               f"{conf * 100:.0f}% is below your strategy's "
                               f"{float(thr) * 100:.0f}% floor.")}

    return {"ok": True, "enforced": enforced, "scan_only": scan_only}
