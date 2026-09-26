"""The bot's breaker as it last SAVED it, for a process that is not the bot.

`api_bridge.py` is a separate process with its own `RuneClawEngine`. That
engine's breaker is a copy loaded when the bridge started and never refreshed
-- the bot runs the trading loop and the bridge does not -- so reading
`engine.risk.circuit_breaker_active` there answered for a process that trades
nothing. Driven: the bot's streak breaker tripped and the bridge's `/health`
read `circuit_breaker_active: false` with nothing blocking trading.

The bot saves its risk state on every change, to one file
(`RuneClawEngine._save_combined_state`), with an atomic rename, so this reader
never sees a torn write. That file is the one cross-process record of whether
the bot is halted -- the kill switch included, because it trips that same
breaker. Three outcomes, and only one of them is a reading:

  read        -- the saved block, accepted by the bot's OWN validator
                 (`RiskEngine._read_state_dict`), so the two processes cannot
                 disagree about what a readable risk state is;
  absent      -- no file: the bot has not saved a risk state here yet;
  unreadable  -- a file that will not parse, or a block that is not a risk
                 state. Never "not halted".

What it cannot see is said rather than guessed at: the warning-rate breaker,
the venue-authentication halt and the live-performance governor's pause are
held in the bot's memory and never saved, so no reader in another process can
know them. (The governor's window is rebuilt from the closed-trade record at
the bot's boot; that is the bot reading its own record, not a saved verdict.)
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

#: The gates that exist only in the running bot's memory. Named once, so
#: every surface that has to say what it could not read says the same thing.
UNSAVED_GATES = ("the warning-rate breaker, the venue-authentication halt and "
                 "the live-performance governor's pause")


def read_persisted_breaker(path: Any) -> dict:
    """The bot's saved breaker at ``path``: ``{"state": "read", ...}``,
    ``{"state": "absent"}`` or ``{"state": "unreadable"}``. Never raises."""
    from bot.risk.risk_engine import RiskEngine

    try:
        p = Path(path)
        if not p.exists():
            return {"state": "absent"}
        raw = json.loads(p.read_text())
    except (OSError, TypeError, ValueError):
        return {"state": "unreadable"}
    block = RiskEngine._read_state_dict(
        raw.get("risk") if isinstance(raw, dict) else None)
    if block is None:
        return {"state": "unreadable"}
    saved_at = raw.get("written_at")
    return {
        "state": "read",
        "circuit_open": block["circuit_open"],
        "cause": block["circuit_trip_cause"],
        "consecutive_losses": block["consecutive_losses"],
        "saved_at": saved_at if isinstance(saved_at, str) else None,
    }


def blocked_by(reading: dict) -> str:
    """What the saved breaker says is blocking trading, in the bot's own
    words (`RiskEngine.trading_blocked_by`: the trip cause, else "circuit"),
    or "" when it is closed. Only meaningful for a ``read`` reading."""
    if reading.get("state") != "read" or not reading.get("circuit_open"):
        return ""
    return reading.get("cause") or "circuit"
