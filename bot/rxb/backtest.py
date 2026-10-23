"""Stream / Elixir / xUSD / deUSD, October–November 2025.

The Morpho DAY series frozen in the fixture was pulled on 2026-10-23 from
``https://api.morpho.org/graphql``. It does not contain the circular book.
Those windows stay unreadable. Unreadable is not an all-clear, and it is
not a crossing.

The earliest sourced snapshot that does cross is the published comparison
dated 28 Oct 2025. That graph is labelled ``published_comparison``. It is
not a chain read.
"""

from __future__ import annotations

import json
from pathlib import Path

from bot.rxb.backtest_v2 import run_v2
from bot.rxb.indexer import parse_history_point
from bot.rxb.solver import Claim, Issuer, solve_issuers

_FIXTURE = Path(__file__).parent / "fixtures" / "stream_elixir_2025.json"
_PUBLISHED_DAY = "2025-10-28"


def load_fixture(path: Path | None = None) -> dict | None:
    try:
        raw = json.loads((path or _FIXTURE).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(raw, dict):
        return None
    return raw


def _int(value: object) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def _issuers(nodes: object) -> list[Issuer] | None:
    if not isinstance(nodes, list):
        return None
    out: list[Issuer] = []
    for row in nodes:
        if not isinstance(row, dict) or not isinstance(row.get("token"), str):
            return None
        claims_in = row.get("claims")
        claims_raw = claims_in if isinstance(claims_in, list) else []
        claims: list[Claim] = []
        for claim in claims_raw:
            if not isinstance(claim, dict):
                return None
            claims.append(Claim(to=str(claim.get("to") or ""), amount=_int(claim.get("amount"))))
        count = row.get("borrower_count")
        if "borrower_count" not in row:
            count = None
        out.append(Issuer(
            token=row["token"],
            nav=_int(row.get("nav")),
            external=_int(row.get("external")),
            onchain_face=_int(row.get("onchain_face")),
            debt=_int(row.get("debt")),
            attested=_int(row.get("attested")),
            attestation_age_s=_int(row.get("attestation_age_s")),
            claims=tuple(claims),
            borrower_count=_int(count) if count is not None else None,
        ))
    return out


def _window(fact: dict) -> dict:
    points_in = fact.get("points")
    points = points_in if isinstance(points_in, list) else []
    parsed = [parse_history_point(p) for p in points]
    return {
        "market_id": fact.get("market_id"),
        "as_of": fact.get("as_of"),
        "readable": False,
        "crossed": None,
        "reason": "nav_and_cycle_edges_absent",
        "history_points": len(parsed),
        "history_unreadable": sum(1 for p in parsed if p["readable"] is not True),
        "history_measured_zero": sum(1 for p in parsed if p["readable"] is True and p["y"] == "0"),
    }


def run_backtest(path: Path | None = None) -> dict:
    data = load_fixture(path)
    if data is None:
        return {
            "readable": False,
            "shows_crossing_before_2025_10_28": None,
            "earlier_windows_readable": False,
            "all_clear": False,
            "earliest_sourced_crossing": None,
            "earliest_basis": None,
            "published": [],
            "windows": [],
            "reason": "fixture_unreadable",
        }
    published_in = data.get("published")
    published_raw = published_in if isinstance(published_in, dict) else {}
    issuers = _issuers(published_raw.get("nodes"))
    published = list(solve_issuers(issuers).values()) if issuers is not None else []
    facts_in = data.get("market_facts")
    facts = facts_in if isinstance(facts_in, list) else []
    windows = [_window(f) for f in facts if isinstance(f, dict)]
    tripped = [row for row in published if row.get("tripped") is True]
    earlier_crossed = any(w.get("crossed") is True for w in windows)
    earlier_clear = bool(windows) and all(
        w.get("readable") is True and w.get("crossed") is False for w in windows
    )
    return {
        "readable": True,
        "shows_crossing_before_2025_10_28": True if earlier_crossed else False,
        "earlier_windows_readable": all(w.get("readable") is True for w in windows) and bool(windows),
        "all_clear": earlier_clear and not tripped,
        "earliest_sourced_crossing": _PUBLISHED_DAY if tripped else None,
        "earliest_basis": "published_comparison" if tripped else None,
        "published_as_of": published_raw.get("as_of"),
        "published": published,
        "windows": windows,
        "pulled_at": data.get("pulled_at"),
        "endpoint": data.get("endpoint"),
        "reason": None,
        "v2": run_v2(),
    }
