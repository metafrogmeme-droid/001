"""A number nothing derives is a claim nobody can check.

`_TOTAL_RISK_CHECKS = 23` sat in `skill_registry.py` under a comment reading
"update this constant when adding or removing checks in risk_engine.py" — a
figure maintained by hand against a file that changes. It drifted, as that
comment predicted it would, and it drifted DOWNWARD: `risk_engine.py` emits 36
distinct check labels.

Twenty-three was asserted on eleven-plus surfaces: the landing page, the
onboarding ladder, chat, three Telegram messages, the MCP server description
that other agents read, two i18n keys across fourteen languages, and a test
that pinned the string — so the wrong number was actively held in place by the
suite that was supposed to protect it.

WHY A COUNT AND NOT A BUG. Nothing broke. The gate ran every check it has, on
every entry, the whole time. What was wrong was a sentence, in the place a
reader has no way to verify it, understating the product by a third while
looking exactly like a specific and confident measurement.

The operator's call was to drop the number rather than derive one, and that is
the stronger answer: the real count is per-trade, varies with what applies to
that trade, and is ALREADY reported where it is genuinely measured —
`checks_passed` on the decision record, the /risk card, the Shield response.
A headline figure could only ever be a second, less accurate copy of it.

AND ONE DISPLAY WENT WITH IT. The /risk card rendered

    _traffic_light(_TOTAL_RISK_CHECKS if not cb else _TOTAL_RISK_CHECKS - streak,
                   _TOTAL_RISK_CHECKS)

— a row of one green dot per check, drawn from the CIRCUIT BREAKER STATE and
the consecutive-loss streak. Neither is a check outcome. A clear breaker
painted a full set of passes that nothing had run, and a tripped one subtracted
the loss streak from a total, which is not a quantity that exists. A heuristic
is never a verdict; the breaker state is what that card actually knows.
"""

from __future__ import annotations

import pathlib
import re

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent

#: Every file that speaks to a user or to another agent about the risk gate.
SURFACES = [
    "bot/skills/skill_registry.py",
    "bot/skills/telegram_handler.py",
    "bot/mcp/server.py",
    "bot/guardian/intent_policy.py",
    "app/public/js/dashboard.js",
    "app/public/js/chat.js",
    "app/public/js/i18n.js",
]

#: "23-check", "23 checks", "36 fail-closed gates", "runs 23 risk checks" …
_COUNTED_GATE = re.compile(
    r"\b\d{1,3}[\s‑-]*(?:check|checks|-check|risk check|fail-closed|gates?|"
    r"Pr[üu]fungen|controlli|comprobaciones|verifica|v[ée]rifications|controles|"
    r"項|项|ग्र)", re.IGNORECASE)


@pytest.mark.parametrize("rel", SURFACES)
def test_no_surface_states_a_risk_check_count(rel):
    """The guard. Any surface that puts a number in front of the word "check"
    is asserting a total, and there is no total to assert."""
    from tests.source_scan import code_only

    path = ROOT / rel
    text = path.read_text(encoding="utf-8")
    # Comments explain the removed number, and a comment quoting the thing it
    # forbids is indistinguishable from the code doing it — CLAUDE.md counts
    # five false failures from exactly that, one of them this week.
    src = code_only(text) if rel.endswith(".py") else re.sub(r"//.*|/\*[\s\S]*?\*/", "", text)
    hits = [m.group(0) for m in _COUNTED_GATE.finditer(src)]
    assert not hits, (
        f"{rel} states a risk-check count ({hits[:3]}). There is no total to "
        "state: the number that matters is per-trade and already reported "
        "where it is measured")


def test_the_hand_maintained_constant_is_gone():
    from tests.source_scan import code_only

    for rel in SURFACES:
        if not rel.endswith(".py"):
            continue
        src = code_only((ROOT / rel).read_text(encoding="utf-8"))
        assert "_TOTAL_RISK_CHECKS" not in src, (
            f"{rel} reintroduced a hand-maintained risk-check total — the "
            "constant whose own comment said it had to be updated by hand, "
            "and which drifted from 36 down to 23")


def test_the_gate_is_still_promised_without_the_number():
    """THE CONTROL. Deleting the claim entirely would be the opposite error —
    the fail-closed gate is real, is the product's central safety property, and
    must still be stated."""
    js = (ROOT / "app" / "public" / "js" / "dashboard.js").read_text(encoding="utf-8")
    assert "fail-closed risk gate" in js
    chat = (ROOT / "app" / "public" / "js" / "chat.js").read_text(encoding="utf-8")
    assert "fail-closed risk engine" in chat
    tg = (ROOT / "bot" / "skills" / "telegram_handler.py").read_text(encoding="utf-8")
    assert tg.count("fail-closed risk gate") >= 2


def test_the_risk_card_reports_the_breaker_not_a_row_of_invented_dots():
    """`_traffic_light(TOTAL if not cb else TOTAL - streak, TOTAL)` drew one
    green dot per check from the breaker state and the loss streak. Neither is
    a check result."""
    from tests.source_scan import code_only

    src = code_only((ROOT / "bot" / "skills" / "skill_registry.py")
                    .read_text(encoding="utf-8"))
    assert "_traffic_light(_TOTAL" not in src, (
        "the risk card is painting per-check dots from the breaker again")
    assert "fail-closed — armed" in src, (
        "the card no longer states the gate status it can actually observe")


def test_the_real_per_trade_count_is_untouched():
    """What the engine measures per trade still flows. Dropping the headline
    figure must not remove the number that is real."""
    from tests.source_scan import code_only

    src = code_only((ROOT / "bot" / "risk" / "risk_engine.py").read_text(encoding="utf-8"))
    assert "checks_passed=passed" in src, (
        "the risk verdict no longer carries the checks it actually ran — that "
        "list is the only honest count there is")
