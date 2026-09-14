"""The risk backstop as ONE reading for the website: drawdown against the halt
threshold, the position slots against the binding cap, the entry gate — every
figure a percent, a count or a flag, every absence its own word.

The website could not reach any of this. Nothing under ``app/`` carried the
live drawdown, the halt threshold, the slot cap or the gate state, and every
``drawdown`` the website does hold is the HISTORICAL drawdown of its own
closed-trade curve — a different quantity under the same name, which is
exactly the number an implementer greps to and wires under "halt threshold"
with every test green. This block rides the scan payload's ``circuit_breaker``
section so the web reads the engine's own figures.

Three rules decide its shape.

**Existing readings, never re-derived.** The drawdown triple comes from
``enforced_drawdown`` (the NaN test, the ``limit > 0`` rejection, the source
word) and the verdict from ``live_risk_status`` — the same seams every Telegram
card reads — so the card on the web and the card in the chat cannot disagree
about what the breaker is enforcing.

**The slot count comes from where it EXISTS.** ``RiskEngine.slot_status``
takes the caller's count as a parameter, because the only count reachable
from the risk engine is the PAPER book, and publishing that against the LIVE
binding cap is the defect ``drawdown_status`` was cured of, one field over.
The scan builder holds the live count when its venue read succeeded and
``None`` when it did not; ``None`` stays ``None``.

**A fault is a marker, not an absence.** A key that is ABSENT means a bot
build that predates this reading — a redeploy instruction — so a fault in
the composer must not be reported that way: it would send the operator to
redeploy the build they are already running. ``{"unreadable": True}`` is the
fault, the same shape ``credential_state()`` and ``master_key_state()`` use.
``drawdown_status() == {}`` is NOT a fault: it is that reader's one failure
signal, and it renders as an unread drawdown beside a read gate.

No dollar figure travels: ``live_equity_peak`` is deliberately not sent (the
payload is served to anonymous callers), and the gate is the CATEGORY-only
form the scan builder already computes.
"""
from __future__ import annotations

import math
from typing import Any, Optional

from bot.formatters.drawdown_card import enforced_drawdown, live_risk_status

UNREADABLE: dict = {"unreadable": True}

#: Every key the block can carry, so a reader can pin the vocabulary. None of
#: them is a DOLLAR_KEY token on the web's scrub (usd, equity, balance,
#: notional, margin, collateral, dollars, account_value, pnl, cash, funds,
#: wallet_value), so the anonymous form is the signed-in form.
KEYS = ("drawdown_pct", "limit_pct", "source", "verdict", "override_pct",
        "default_limit_pct", "hardening", "slots_used", "slots_cap",
        "slots_floor", "slots_note", "slots_person", "gate")


def _num(v: Any) -> Optional[float]:
    """A finite number, or None. A bool is not a number here."""
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    f = float(v)
    return f if math.isfinite(f) else None


def _gate(gate: Any) -> Optional[dict]:
    """The gate block as the scan builder computed it — ``{blocked, unknown,
    reasons}`` — or None when it could not be asked. Passed in rather than
    asked again: one read, one answer, and the builder's is already the
    public (category-only) form."""
    if not isinstance(gate, dict):
        return None
    blocked, unknown = gate.get("blocked"), gate.get("unknown")
    if not isinstance(blocked, bool) or not isinstance(unknown, bool):
        return None
    reasons = gate.get("reasons")
    return {"blocked": blocked, "unknown": unknown,
            "reasons": [str(r) for r in reasons] if isinstance(reasons, (list, tuple)) else []}


def backstop_block(engine: Any, *, open_count: Optional[int] = None,
                   gate: Any = None) -> dict:
    """The block. Never raises; ``UNREADABLE`` on any fault, including no
    engine and no risk engine to ask."""
    try:
        risk = getattr(engine, "risk", None)
        if risk is None:
            return dict(UNREADABLE)
        st = risk.drawdown_status()
        if not isinstance(st, dict):
            st = {}
        pct, source, limit = enforced_drawdown(st)
        _, verdict = live_risk_status(st)
        hardening = st.get("live_hardening")
        slots = risk.slot_status(open_count)
        if not isinstance(slots, dict):
            slots = {}
        used = slots.get("used")
        cap = slots.get("cap")
        return {
            "drawdown_pct": pct,
            "limit_pct": limit,
            "source": source,
            # The band word off the limit the breaker is ACTUALLY enforcing;
            # "Unknown" when either half could not be read.
            "verdict": verdict,
            "override_pct": _num(st.get("override_pct")),
            "default_limit_pct": _num(st.get("config_live_limit_pct")),
            # Only a bool is a reading: a partial payload must not be able
            # to manufacture "hardening OFF".
            "hardening": hardening if isinstance(hardening, bool) else None,
            "slots_used": used if isinstance(used, int) and not isinstance(used, bool) else None,
            "slots_cap": cap if isinstance(cap, int) and not isinstance(cap, bool) else None,
            "slots_floor": bool(slots.get("floor")) if slots else None,
            "slots_note": str(slots.get("note") or "") if slots else "",
            "slots_person": str(slots.get("person") or "") if slots else "",
            "gate": _gate(gate),
        }
    except Exception:
        return dict(UNREADABLE)
