"""Ask the existing Daily Duel (``app/lib/daily_duel_gate.js``) and nothing else.

Cutoff, horizon, the entry and the seal are that module's answers. This file
does not keep a second copy of them. A Telegram id is not a field it forwards:
``seal`` copies an allow-list, so a caller that stuffed an id onto the dict
cannot reach the receipt through here.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any, Callable, Mapping, Optional

REPO_ROOT = Path(__file__).resolve().parents[1]
GATE_JS = REPO_ROOT / "app" / "lib" / "daily_duel_gate.js"

Runner = Callable[[str, Mapping[str, Any]], dict]


class GateDown(Exception):
    """The duel gate could not be asked. Not a price, not a closed day."""

    pass  # a docstring-only body does not parse once code_only blanks it


def node_runner(command: str, payload: Mapping[str, Any]) -> dict:
    """One JSON object on stdout. The input is stdin, never argv, and a
    failure is logged as an exit status only: the body can carry a handle."""
    node = os.environ.get("DAILY_DUEL_NODE", "node") or "node"
    try:
        proc = subprocess.run(
            [node, str(GATE_JS), command],
            input=json.dumps(dict(payload)),
            text=True,
            capture_output=True,
            timeout=20,
            cwd=str(REPO_ROOT),
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise GateDown(type(exc).__name__) from None
    if proc.returncode != 0 or not proc.stdout.strip():
        raise GateDown(str(proc.returncode))
    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise GateDown(type(exc).__name__) from None
    if not isinstance(data, dict):
        raise GateDown("shape")
    return data


# What a seal is allowed to be told. Identity is not on this list.
_SEAL_KEYS = ("now", "pick", "symbol", "marks")
# Scam or Safe uses the same gate. The player key is the salted hash the bot
# already computed. A Telegram id is not a key this list forwards.
_SOS_OFFER_KEYS = ("now", "player_key")
_SOS_SEAL_KEYS = ("now", "pick", "player_key", "puzzle_id")


class DuelGate:
    def __init__(self, runner: Optional[Runner] = None):
        self._runner = runner if runner is not None else node_runner

    def _call(self, command: str, payload: Mapping[str, Any]) -> dict:
        try:
            return self._runner(command, payload)
        except GateDown:
            raise
        except Exception as exc:
            raise GateDown(type(exc).__name__) from None

    def rules(self, day: Optional[str] = None) -> dict:
        body = self._call("rules", {"day": day} if day else {})
        if not body.get("ok") or not body.get("cutoff_at"):
            raise GateDown("rules")
        return body

    def offer(self, now: Optional[str] = None) -> dict:
        body = self._call("offer", {"now": now} if now else {})
        if not body.get("ok"):
            raise GateDown(str(body.get("error") or "offer"))
        return body

    def seal(self, facts: Mapping[str, Any]) -> dict:
        clean = {k: facts[k] for k in _SEAL_KEYS if k in facts and facts[k] is not None}
        return self._call("seal", clean)

    def sos_offer(self, now: Optional[str] = None, player_key: Optional[str] = None) -> dict:
        payload: dict[str, Any] = {}
        if now:
            payload["now"] = now
        if player_key:
            payload["player_key"] = player_key
        clean = {k: payload[k] for k in _SOS_OFFER_KEYS if k in payload}
        return self._call("sos-offer", clean)

    def sos_seal(self, facts: Mapping[str, Any]) -> dict:
        clean = {k: facts[k] for k in _SOS_SEAL_KEYS if k in facts and facts[k] is not None}
        return self._call("sos-seal", clean)

    def streak(self, picked_at: list[str], now: Optional[str] = None) -> dict:
        payload: dict[str, Any] = {"picked_at": list(picked_at)}
        if now:
            payload["now"] = now
        body = self._call("streak", payload)
        if not body.get("ok"):
            raise GateDown(str(body.get("error") or "streak"))
        return body
