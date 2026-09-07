"""The Guardian console must not report NONE about a book it never read.

``engine.guardian_status()`` carries a comment saying, in capitals, that every
fail-open default on the console pointed at "safe" — and it now returns ``None``
for ``posture`` and for each module's ``risk`` when the assessment could not
run. That fix ends at the edge of the engine. The Telegram card then did::

    posture = s.get("posture", "none")
    ... html.escape(str(posture).upper())

``s`` HAS the key, holding ``None``, so the default never fires and
``str(None).upper()`` is the string **"NONE"** — the same word the engine
stopped emitting, restored one layer downstream. The icon was already honest
(``_RISK_ICON.get(None, "⚪")`` misses), so the card showed a grey dot beside
the word NONE, and the word is the half that gets read. ``position_count``
went the same way: ``.get("position_count", 0)`` printed **(0 pos)** for a book
nobody could count.

The web console had the identical pair — see
``app/test/guardian_unknown_is_not_safe.test.js``. Both are here because
CLAUDE.md's corollary is to ask which OTHER surface makes the same claim, and
the answer was "the other one, for the same reason".
"""

from types import SimpleNamespace

import pytest

from bot.skills.telegram_handler import TelegramHandler


def _host(sent: list, admin: bool = True):
    h = TelegramHandler.__new__(TelegramHandler)

    async def _send(update, text, reply_markup=None, edit=False):
        sent.append(str(text))

    h._send = _send
    h._is_admin = lambda update: admin
    h._lang = lambda update: "en"
    return h


def _update():
    return SimpleNamespace(effective_user=SimpleNamespace(id=1),
                           effective_chat=SimpleNamespace(id=1),
                           message=SimpleNamespace(text="/guardian"),
                           callback_query=None)


# The document engine.guardian_status() returns when every read failed: the
# flags are config and are always readable, and everything measured is None.
UNREADABLE = {
    "flags": {"intent_policy": False, "firewall": False, "firewall_block": False,
              "digital_twin": True, "risk_sentinel": True, "escape": True},
    "chain": {"length": 0, "ok": None, "tip": ""},
    "policy": None,
    "twin": {"risk": None, "position_count": None},
    "sentinel": {"risk": None},
    "escape": {"risk": None},
    "posture": None,
}

MEASURED = {
    "flags": {"intent_policy": True, "firewall": True, "firewall_block": True,
              "digital_twin": True, "risk_sentinel": True, "escape": True},
    "chain": {"length": 12, "ok": True, "tip": "abc"},
    "policy": {"label": "x"},
    "twin": {"risk": "none", "position_count": 0},
    "sentinel": {"risk": "none"},
    "escape": {"risk": "none"},
    "posture": "none",
}


async def _card(status: dict) -> str:
    sent: list = []
    h = _host(sent)
    h.engine = SimpleNamespace(guardian_status=lambda: status)
    await h._cmd_guardian(_update(), SimpleNamespace(args=[]))
    assert len(sent) == 1
    return sent[0]


@pytest.mark.asyncio
async def test_an_unreadable_console_says_unknown_not_none():
    card = await _card(UNREADABLE)
    assert "UNKNOWN" in card, card
    # Anchored to the word, and the word is the whole point: NONE is the
    # calmest verdict in this vocabulary and it must not appear anywhere on a
    # card assembled entirely from failed reads.
    assert "NONE" not in card, (
        "the console still reports NONE about a book it never read:\n" + card)


@pytest.mark.asyncio
async def test_every_module_row_says_unknown_not_none():
    card = await _card(UNREADABLE)
    for label in ("Digital Twin", "Risk Sentinel", "Escape Agent"):
        line = next((ln for ln in card.splitlines() if label in ln), "")
        assert line, f"{label} row is missing from the card"
        assert "UNKNOWN" in line, f"{label}: {line}"
        # Anchored to this module's OWN line rather than to the whole card —
        # CLAUDE.md: asserting a short string is absent from a document is the
        # assertion that keeps misfiring.
        assert "NONE" not in line, f"{label} reports NONE from no reading: {line}"


@pytest.mark.asyncio
async def test_an_uncountable_book_is_not_reported_as_zero_positions():
    card = await _card(UNREADABLE)
    twin = next(ln for ln in card.splitlines() if "Digital Twin" in ln)
    assert "(0 pos)" not in twin, (
        "an unread book was reported as holding zero positions — `.get(k, 0)` "
        f"is on CLAUDE.md's table of shapes: {twin}")
    assert "(None pos)" not in twin, f"the None leaked into the card verbatim: {twin}"
    assert "(? pos)" in twin, twin


@pytest.mark.asyncio
async def test_an_unverified_chain_is_neither_verified_nor_broken():
    # Pinned because the readiness score got this wrong in the other direction:
    # `chain.ok !== false` scored an unchecked chain as intact. This card has
    # told the three apart all along, and that is why it is the reference.
    card = await _card(UNREADABLE)
    assert "unchecked" in card
    assert "verified" not in card and "UNVERIFIED" not in card


@pytest.mark.asyncio
async def test_a_measured_none_still_reads_none():
    """NOT EVERY MATCH IS A DEFECT.

    A console that ran every assessment and flagged nothing is reporting a
    measurement, and "none" is the honest word for it. Turning that into
    UNKNOWN would remove a true statement to satisfy a rule about false ones —
    the mistake CLAUDE.md records a test making on `d_icon`.
    """
    card = await _card(MEASURED)
    assert "NONE" in card
    assert "UNKNOWN" not in card
    assert "(0 pos)" in card, "a book that WAS counted and holds nothing is 0"
    assert "verified" in card


@pytest.mark.asyncio
async def test_a_graded_posture_is_unchanged():
    card = await _card({**MEASURED, "posture": "high",
                        "twin": {"risk": "medium", "position_count": 3}})
    assert "HIGH" in card
    assert "MEDIUM" in card and "(3 pos)" in card
    assert "UNKNOWN" not in card


@pytest.mark.asyncio
async def test_a_missing_section_is_unknown_rather_than_a_crash():
    # An older engine, or one whose status document lost a section entirely.
    card = await _card({"flags": {}, "chain": {}})
    assert "UNKNOWN" in card
    assert "NONE" not in card
