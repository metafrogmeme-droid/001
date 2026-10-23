"""Admin text and the page model. Both read the same onboarding file."""

from __future__ import annotations

import html
from pathlib import Path

from bot.rxb.backtest import run_backtest
from bot.rxb.chains import signing_allowed
from bot.rxb.flag import flag_on
from bot.rxb.reading import listed_tokens, node_view
from bot.vaults.sentinel import plan_cap_down

_DOC = Path(__file__).resolve().parents[2] / "docs" / "rxb" / "SENTINEL.md"


def onboarding_text() -> dict:
    try:
        return {"readable": True, "text": _DOC.read_text(encoding="utf-8")}
    except OSError:
        return {"readable": False, "text": None}


def sentinel_status(env: dict | None = None) -> dict:
    """A dry plan. No broadcaster, so this cannot send."""
    planned = plan_cap_down(
        chain_id=31337,
        vault="0x" + "11" * 20,
        id_data="0x",
        proposals=[],
        current_absolute=None,
        current_relative=None,
        broadcaster=None,
        audit_path=None,
        env=env,
    )
    return {
        "sent": False,
        "reason": planned["reason"],
        "mainnet_signing": signing_allowed(1),
        "fork_signing": signing_allowed(31337),
    }


def page_model() -> dict:
    back = run_backtest()
    published = back.get("published")
    public_back = {k: v for k, v in back.items() if k != "published"}
    return {
        "flag_on": flag_on(),
        "backtest": public_back,
        "published": published,
        "nodes": [node_view(token) for token in listed_tokens()],
        "sentinel": sentinel_status(),
        "onboarding": onboarding_text(),
    }


def _ratio_line(row: dict) -> str:
    token = row.get("token") or "?"
    if row.get("readable") is not True:
        return f"{token}: unreadable ({row.get('reason') or 'unread'})"
    return (
        f"{token}: {row.get('state') or 'unread'} "
        f"CBF {row.get('cbf')} LTS {row.get('lts')} UF {row.get('uf')}"
    )


def admin_text(raw: str = "") -> str:
    """HTML for an admin chat. Dynamic text is escaped. Nothing is sent."""
    model = page_model()
    focus = (raw or "").replace("/rxb", " ").strip()
    lines = [
        "<b>Recursion Breaker</b>",
        f"Flag: {'on' if model['flag_on'] else 'off'}",
    ]
    doc = model["onboarding"]
    if doc.get("readable") is True and isinstance(doc.get("text"), str):
        lines.append(html.escape(doc["text"]))
    else:
        lines.append("Onboarding document could not be read.")
    back = model["backtest"]
    if back.get("shows_crossing_before_2025_10_28") is True:
        lines.append("A window before 2025-10-28 crossed a threshold.")
    elif back.get("all_clear") is True:
        lines.append("Earlier windows were read and none crossed.")
    else:
        lines.append(
            "Computed: no threshold crossing is shown before 2025-10-28, "
            "and the earlier windows are not an all-clear."
        )
    wanted = focus.split()
    rows = model["nodes"]
    if wanted and wanted[0].lower() not in {"backtest", ""}:
        token = "".join(ch for ch in wanted[0] if ch.isalnum())
        rows = [node_view(token)] + [r for r in rows if r.get("token") != token]
    for row in rows:
        lines.append(html.escape(_ratio_line(row)))
    sent = model["sentinel"]
    lines.append(
        "Sentinel dry plan: "
        + html.escape(str(sent.get("reason") or "unread"))
        + ". Sent: no. Mainnet signing: "
        + ("allowed" if sent.get("mainnet_signing") is True else "refused")
        + "."
    )
    return "\n".join(lines)
