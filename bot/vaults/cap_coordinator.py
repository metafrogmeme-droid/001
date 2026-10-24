"""One decrease, the lowest one.

RXB owns this coordinator. CGC and T2C submit proposals here; they do not
keep a second minimum. Proposals group by ``(vault, id_data)``. The
minimum absolute cap and the minimum relative cap win. An unreadable
proposal makes that key unreadable and nothing is sent: it is not treated
as 0 and not treated as no cap. An absent opinion (None) is skipped and
cannot loosen the min.

``cap_id`` is ``keccak256(id_data)`` when ``id_data`` is hex calldata.
Anything else leaves ``cap_id`` null. Vault V2's collateral calldata is
``abi.encode("collateralToken", token)``.

The coordinator does not know the vault's current cap. Raising is the
caller's refusal, in ``plan_cap_down``.
"""

from __future__ import annotations

import json
from collections import OrderedDict
from pathlib import Path

from bot.vaults.cap_id import cap_id_hex


def _cap_id(id_data: str) -> str | None:
    if not id_data.startswith("0x"):
        return None
    try:
        raw = bytes.fromhex(id_data[2:])
    except ValueError:
        return None
    if not raw:
        return None
    return cap_id_hex(raw)


def _cap(value: object) -> int | None | str:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return "bad"
    return value


def coordinate_caps(proposals: list[dict], audit_path: str | None) -> dict:
    """Decide each (vault, id). Append one JSON line per key when a path is set."""
    groups: OrderedDict[tuple[str, str], list[dict]] = OrderedDict()
    for row in proposals:
        if not isinstance(row, dict):
            continue
        vault = row.get("vault")
        id_data = row.get("id_data")
        if not isinstance(vault, str) or not isinstance(id_data, str):
            continue
        groups.setdefault((vault, id_data), []).append(row)

    decisions: list[dict[str, object]] = []
    for (vault, id_data), group in groups.items():
        sources = [str(p.get("source") or "") for p in group]
        if any(p.get("readable") is not True for p in group):
            decisions.append({
                "vault": vault,
                "id_data": id_data,
                "cap_id": None,
                "sources": sources,
                "readable": False,
                "absolute": None,
                "relative": None,
                "reason": "unreadable",
            })
            continue
        absolutes: list[int] = []
        relatives: list[int] = []
        bad = False
        for p in group:
            a = _cap(p.get("absolute"))
            r = _cap(p.get("relative"))
            if a == "bad" or r == "bad":
                bad = True
                break
            if isinstance(a, int):
                absolutes.append(a)
            if isinstance(r, int):
                relatives.append(r)
        if bad:
            decisions.append({
                "vault": vault,
                "id_data": id_data,
                "cap_id": None,
                "sources": sources,
                "readable": False,
                "absolute": None,
                "relative": None,
                "reason": "unreadable",
            })
            continue
        decisions.append({
            "vault": vault,
            "id_data": id_data,
            "cap_id": _cap_id(id_data),
            "sources": sources,
            "readable": True,
            "absolute": min(absolutes) if absolutes else None,
            "relative": min(relatives) if relatives else None,
            "reason": None,
        })

    if audit_path:
        path = Path(audit_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            for row in decisions:
                fh.write(json.dumps(row, sort_keys=True) + "\n")
    return {"decisions": decisions}
