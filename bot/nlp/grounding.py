"""Shadow-check figures with a unit against this turn's tool readings.

A figure in the reply is verified when a tool that actually read something
returned the same number, in the same unit, within a small tolerance. An
unread or absent result supports nothing, including zero. The rate is
published per model. Replies are annotated only once that rate is below the
threshold; until then the check records and leaves the words alone.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

from bot.nlp.tool_reading import parse_reading, prose_of

# A figure has a unit. A bare integer is not one: "2 positions" and "15 minute"
# are not measurements this check is allowed to call unverified.
_FIGURE = re.compile(
    r"\$\s*-?\d[\d,]*(?:\.\d+)?"
    r"|-?\d[\d,]*(?:\.\d+)?\s*%"
    r"|-?\d+(?:\.\d+)?\s*R\b"
    # A ratio is written tight, "1:2.5". With spaces allowed, "Day 1: 25%"
    # was read as the ratio "1: 25" and the percent after it went unchecked.
    # Not part of a longer colon run: "16:23:05" is a clock.
    r"|(?<![\d:.])\d+(?:\.\d+)?:\d+(?:\.\d+)?\b(?!:\d)",
    re.IGNORECASE,
)

# A colon pair that is a time of day, by the words around it: "16:23 UTC",
# "as of 9:30". A clock is not a figure this check can call unverified.
_CLOCK_AFTER = re.compile(
    r"\s*(?:utc|gmt|z\b|am\b|pm\b|a\.m\.|p\.m\.|hrs?\b|h\b"
    r"|[ecmp][sd]?t\b|cet\b|cest\b|bst\b|jst\b|hkt\b|sgt\b|kst\b)",
    re.IGNORECASE,
)
_CLOCK_BEFORE = re.compile(
    r"(?:\bat|\bas of|\bsince|\buntil|\btill|\bby|\bbefore|\bafter|\baround"
    r"|\bfrom|\bto)\s*$",
    re.IGNORECASE,
)

_UNIT = {
    "usd": "usd", "$": "usd", "dollar": "usd", "dollars": "usd",
    "pct": "pct", "%": "pct", "percent": "pct",
    "r": "r",
}

# Annotation stays off until the published rate is below these. Exactly on
# the line is still shadow: "below 1%" does not include 1%.
ADMIN_MAX_UNVERIFIED = 0.01
FREE_MAX_UNVERIFIED = 0.03

_LEDGER: dict[str, dict[str, int]] = {}


@dataclass(frozen=True)
class Figure:
    kind: str
    value: float
    value2: Optional[float]
    display: str


def reset() -> None:
    """Drop the published rates. Tests start from nothing measured."""
    _LEDGER.clear()


def note(model: str, checked: int, unverified: int) -> None:
    """Record one turn. A turn that checked nothing is not a perfect rate."""
    if not model or checked <= 0 or unverified < 0 or unverified > checked:
        return
    row = _LEDGER.setdefault(str(model), {"checked": 0, "unverified": 0})
    row["checked"] += checked
    row["unverified"] += unverified


def rate(model: str) -> Optional[float]:
    """Unverified / checked for this model, or None when nothing was checked.

    None is not zero. Zero is a measured run in which every figure matched.
    """
    row = _LEDGER.get(str(model))
    if row is None:
        return None
    checked = row.get("checked")
    if not isinstance(checked, int) or isinstance(checked, bool) or checked <= 0:
        return None
    return row["unverified"] / checked


def snapshot() -> dict:
    """Per-model counts. ``rate`` is null when that model has not been checked."""
    models = []
    for model in sorted(_LEDGER):
        row = _LEDGER[model]
        checked = row["checked"]
        unverified = row["unverified"]
        models.append({
            "model": model,
            "checked": checked,
            "unverified": unverified,
            "rate": (unverified / checked) if checked > 0 else None,
        })
    return {"models": models}


def public_line(snap: Optional[dict]) -> str:
    """The operator sentence. An empty ledger is 'not measured', never 0%."""
    models = snap.get("models") if isinstance(snap, dict) else None
    if not isinstance(models, list) or not models:
        return "figures: not measured"
    parts: list[str] = []
    for m in models:
        if not isinstance(m, dict):
            continue
        name = str(m.get("model") or "model")
        checked = m.get("checked")
        unverified = m.get("unverified")
        got = m.get("rate")
        if (not isinstance(checked, int) or isinstance(checked, bool)
                or checked <= 0 or not isinstance(got, (int, float))
                or isinstance(got, bool)
                or not isinstance(unverified, int) or isinstance(unverified, bool)):
            parts.append(f"{name}: not measured")
            continue
        parts.append(
            f"{name}: {unverified} of {checked} unverified ({_pct(float(got))}%)")
    return " · ".join(parts) if parts else "figures: not measured"


def annotation_on(model: str, is_admin: bool) -> bool:
    """True only when this model's published rate is already below the line."""
    got = rate(model)
    if got is None:
        return False
    ceiling = ADMIN_MAX_UNVERIFIED if is_admin else FREE_MAX_UNVERIFIED
    return got < ceiling


def unverified_note(figures: list[str]) -> str:
    shown = ", ".join(figures[:8])
    return f"Unverified figure: {shown}"


def check_reply(reply: str, tool_results: list[str]) -> tuple[int, list[str]]:
    """``(checked, unverified displays)`` against this turn's tool results.

    A result with no reading line is treated as a read: the loop's own
    executor returned plain text. A reading whose state is unread or absent
    supports no figure, and a null ``value`` is not a zero.
    """
    found = _figures(reply or "")
    support: list[Figure] = []
    for raw in tool_results or []:
        text = raw or ""
        row = parse_reading(text)
        if row is not None and row.get("read_state") != "read":
            continue
        blob = prose_of(text) if row is not None else text
        support.extend(_figures(blob))
        if row is not None and row.get("read_state") == "read":
            scalar = _from_value(row)
            if scalar is not None:
                support.append(scalar)
    unverified = [f.display for f in found if not any(_same(f, s) for s in support)]
    return len(found), unverified


def tools_footer(events) -> str:
    """Which tools ran, in call order. A tool that did not succeed is marked."""
    parts: list[str] = []
    for e in events or []:
        if not isinstance(e, dict):
            continue
        name = str(e.get("name") or "").strip()
        if not name:
            continue
        parts.append(name if e.get("ok") else name + "\u2717")
    if not parts:
        return ""
    return "read: " + ", ".join(parts)


def telegram_read_from_html(meta) -> str:
    """The footer as Telegram HTML, or '' when this turn read nothing."""
    import html as _html
    if not isinstance(meta, dict):
        return ""
    line = str(meta.get("read_from") or "").strip()
    if not line:
        return ""
    return "\n\n<i>" + _html.escape(line) + "</i>"


def _pct(rate_value: float) -> str:
    pct = rate_value * 100
    if pct == 0:
        return "0"
    if pct < 10:
        return f"{pct:.1f}"
    return str(round(pct))


def _figures(text: str) -> list[Figure]:
    out: list[Figure] = []
    text = text or ""
    for m in _FIGURE.finditer(text):
        fig = _parse_figure(m.group(0))
        if fig is None:
            continue
        if fig.kind == "ratio" and (_CLOCK_AFTER.match(text, m.end())
                                    or _CLOCK_BEFORE.search(text[:m.start()])):
            continue
        out.append(fig)
    return out


def _parse_figure(raw: str) -> Optional[Figure]:
    display = raw.strip()
    if ":" in display and "$" not in display and "%" not in display:
        left, _, right = display.partition(":")
        a, b = _num(left), _num(right)
        if a is None or b is None:
            return None
        return Figure("ratio", a, b, display)
    if display.startswith("$"):
        n = _num(display[1:])
        return None if n is None else Figure("usd", n, None, display)
    if display.endswith("%"):
        n = _num(display[:-1])
        return None if n is None else Figure("pct", n, None, display)
    if display[-1:] in ("R", "r"):
        n = _num(display[:-1])
        return None if n is None else Figure("r", n, None, display)
    return None


def _from_value(row: dict) -> Optional[Figure]:
    """A scalar the tool measured. A null value is not a figure."""
    kind = _UNIT.get(str(row.get("unit") or "").strip().lower())
    if kind is None:
        return None
    n = row.get("value")
    if isinstance(n, bool) or not isinstance(n, (int, float)):
        return None
    return Figure(kind, float(n), None, str(n))


def _num(raw: str) -> Optional[float]:
    try:
        return float(str(raw).replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def _same(a: Figure, b: Figure) -> bool:
    if a.kind != b.kind or not _close(a.value, b.value):
        return False
    if a.value2 is None and b.value2 is None:
        return True
    if a.value2 is None or b.value2 is None:
        return False
    return _close(a.value2, b.value2)


def _close(a: float, b: float) -> bool:
    return abs(a - b) <= max(0.02, abs(b) * 0.005)
