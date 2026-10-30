"""JSON in, a verdict out. This process does not sign.

The website spawns ``python3 -m bot.claw_guard.service``. The trade-only
key is not read here.
"""

from __future__ import annotations

import json
import sys
from typing import Any

from bot.claw_guard.evaluate import OUT_OF_SCOPE, evaluate


def check_body(body: object, *, now: float = 0.0) -> dict[str, Any]:
    if not isinstance(body, dict):
        return {"ok": False, "error": "request"}
    if body.get("op") == "resting":
        return {"ok": True, "decision": None, "rules": [], "text": "", "out_of_scope": OUT_OF_SCOPE}
    action = body.get("action")
    account = body.get("account")
    if not isinstance(action, dict) or not isinstance(account, dict):
        return {"ok": False, "error": "request"}
    action = {key: value for key, value in action.items() if key not in {"origin", "client_tag", "cloid"}}
    stamp = body.get("now")
    if isinstance(stamp, (int, float)) and not isinstance(stamp, bool):
        now = float(stamp)
    result = evaluate(action, account, now=now, origin="user")
    result["ok"] = True
    return result


def main() -> int:
    raw = sys.stdin.read()
    try:
        body = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError:
        body = None
    sys.stdout.write(json.dumps(check_body(body)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
