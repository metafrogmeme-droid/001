"""A local table of public advisories. A match is a fact, not a verdict.

The table ships with one seed. A missing or unreadable file is not "no
match": the caller says the table could not be read.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from bot.silent_fee.address import parse_address

_TABLE = Path(__file__).with_name("advisories.json")


def load_advisories(path: Optional[Path] = None) -> Optional[list[dict]]:
    """The seed rows, or ``None`` when the file cannot be read."""
    try:
        raw = json.loads((path or _TABLE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    rows = raw.get("advisories") if isinstance(raw, dict) else None
    if not isinstance(rows, list):
        return None
    clean: list[dict] = []
    for row in rows:
        if not isinstance(row, dict):
            return None
        ident = row.get("id")
        published = row.get("published")
        builder = row.get("builder")
        code = row.get("referral_code")
        if not isinstance(ident, str) or not ident.strip():
            return None
        if not isinstance(published, str) or not published.strip():
            return None
        parsed = parse_address(builder) if isinstance(builder, str) else None
        if parsed is None or not isinstance(code, str) or not code.strip():
            return None
        versions = row.get("versions")
        if not isinstance(versions, list) or not all(isinstance(v, str) for v in versions):
            return None
        package = row.get("package")
        if not isinstance(package, str):
            return None
        clean.append({
            "id": ident.strip(),
            "published": published.strip(),
            "package": package,
            "versions": list(versions),
            "builder": parsed,
            "referral_code": code.strip(),
            "url": row.get("url") if isinstance(row.get("url"), str) else "",
        })
    return clean


def match_advisories(
    table: Optional[list[dict]],
    *,
    builder: Optional[str] = None,
    code: Optional[str] = None,
) -> Optional[list[dict]]:
    """Rows that name this builder or this referral code.

    ``None`` means the table was not read. An empty list means it was read
    and no seed names this input.
    """
    if table is None:
        return None
    want = parse_address(builder) if isinstance(builder, str) else None
    want_code = code.strip() if isinstance(code, str) else ""
    hits: list[dict] = []
    for row in table:
        on: list[str] = []
        if want is not None and want == row["builder"]:
            on.append("builder")
        if want_code and want_code == row["referral_code"]:
            on.append("referral_code")
        if not on:
            continue
        hits.append({
            "id": row["id"],
            "published": row["published"],
            "package": row["package"],
            "versions": list(row["versions"]),
            "matched_on": on,
            "url": row["url"],
        })
    return hits
