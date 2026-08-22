"""The scan universe has no fixed size, so no surface may quote one.

`800+ symbols` was on eight surfaces — both landing pages' `og:` and `twitter:`
descriptions, the explore page's loop step, and the `lp.loop1_s` i18n key in all
fourteen languages. The catalogue held 759 at the time somebody checked.

BUT THE REAL PROBLEM IS NOT THAT 800 WAS WRONG BY FORTY-ONE. It is that no
number can be right. The scan universe is whatever clears a **configurable 24h
quote-volume floor** (`bot/config.py`, "Minimum 24h quote volume (USD) for a
symbol to enter the scan universe") on whatever the connected venue lists today.
Both inputs move: exchanges list and delist, and volume is volume. A figure
compiled into a static page describes one morning of one venue, forever.

Two of the sites made the claim harder to dislodge by building evidence for it.
`app/public/index.html` carried a live market strip under the comment

    Living proof of "800+ symbols scanned"

so a real feed of real tickers stood underneath an invented total, which is the
most convincing way to publish a number nobody measured: the reader checks the
tickers, finds them genuine, and infers the count was checked too.

This is the same defect as `test_no_hardcoded_risk_check_count.py` — a headline
figure standing where a per-run measurement belongs — and it takes the same
answer. Drop the number; say the true thing, which is that the engine watches
the liquid perp market rather than a fixed list.
"""

from __future__ import annotations

import pathlib
import re

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent

#: Every surface that describes the scan universe to a user or an agent.
SURFACES = [
    "app/public/index.html",
    "app/public/explore.html",
    "app/public/js/i18n.js",
    "dashboard_static/index.html",
    "agent_card.json",
    "README.md",
]

#: "800+ symbols", "scans 750 markets", "oltre 800 strumenti", "800+ 交易對" …
#: The unit list is multilingual because `lp.loop1_s` ships in fourteen
#: languages and a guard that only reads English would have caught two of the
#: fourteen places this exact string lived.
_UNIT = (r"(?:symbols?|markets?|pairs?|coins?|tickers?|instruments?|perps?|"
         r"strumenti|s[íi]mbolos|symbole|symboles|symbolen|交易對|交易对|銘柄|종목|"
         r"инструментов|enstr[üu]man|رمز)")
_COUNTED = re.compile(rf"(?<![.\d%])\b\d{{2,5}}\s*\+?\s*{_UNIT}", re.IGNORECASE)

#: A COUNT OF THE SCAN UNIVERSE, not every number that precedes "symbols".
#:
#: The first draft was just `_COUNTED`, and it flagged three true statements:
#:
#:   "Watchlist is capped at 20 symbols."   a real enforced cap — WATCHLIST_MAX
#:                                          is 20 in app/routes/profile.js and
#:                                          bot/core/user_profile_store.py
#:   "5 regimes x 20 symbols x 5 seeds"     a backtest matrix dimension
#:   "67 Bitget USDT pairs (API bridge)"    …this one WAS a defect, but it sat
#:                                          beside the other two
#:
#: A cap and a matrix dimension are derived, fixed and checkable. The universe
#: size is none of those. Removing a true statement to satisfy a rule about
#: false ones is the more expensive mistake, so the match now requires a
#: SCANNING context within the surrounding window — which is what makes a
#: number a claim about the universe rather than about a limit.
#: WORD-BOUNDED, and the reason is the sentence this guard exists beside.
#: Without `\b`, `watch` matches inside "**Watch**list is capped at 20 symbols"
#: — so the context test that was added to stop flagging a cap went and flagged
#: the same cap through the word "watchlist". A narrowing that re-creates the
#: problem it narrowed is worth the two characters to avoid.
_SCAN_CONTEXT = re.compile(
    r"\b(?:scans?|scanned|scanning|watches|watching|monitors?|monitoring|"
    r"universe|covers?|coverage|around the clock|24/7)\b",
    re.IGNORECASE)
_WINDOW = 90


def _universe_claims(text: str) -> list[str]:
    out = []
    for m in _COUNTED.finditer(text):
        window = text[max(0, m.start() - _WINDOW):m.end() + _WINDOW]
        if _SCAN_CONTEXT.search(window):
            out.append(m.group(0))
    return out


def _strip_comments(rel: str, text: str) -> str:
    """Blank only the comment syntax the file actually has.

    Per-language, for the reason its sibling guard records: a blanket `//.*`
    rule eats everything after the `//` in `https://…`, which on a docs or
    markup file hides far more than it strips.
    """
    if rel.endswith(".py"):
        from tests.source_scan import code_only
        return code_only(text)
    if rel.endswith((".js", ".ts", ".mjs")):
        return re.sub(r"//[^\n]*|/\*[\s\S]*?\*/", " ", text)
    if rel.endswith((".html", ".md")):
        return re.sub(r"<!--[\s\S]*?-->", " ", text)
    return text


@pytest.mark.parametrize("rel", SURFACES)
def test_no_surface_states_a_universe_size(rel):
    path = ROOT / rel
    if not path.exists():          # a surface that was deleted is not a failure
        pytest.skip(f"{rel} no longer exists")
    hits = _universe_claims(_strip_comments(rel, path.read_text(encoding="utf-8")))
    assert not hits, (
        f"{rel} states a scan-universe size ({hits[:3]}). There is no fixed "
        "size: the universe is whatever clears the configurable 24h volume "
        "floor on the connected venue, and both of those change daily")


def test_the_volume_floor_that_makes_the_number_meaningless_still_exists():
    """The premise, checked rather than asserted.

    If the floor were ever removed and the universe became the venue's whole
    catalogue, a count would still be venue-dependent but it would at least be
    derivable — and this guard's reasoning would need rewriting rather than
    quietly continuing to forbid something that had become knowable.
    """
    cfg = (ROOT / "bot" / "config.py").read_text(encoding="utf-8")
    assert re.search(r"Minimum 24h quote volume", cfg), (
        "the 24h volume floor is gone from config.py — the scan universe may "
        "now be enumerable, and this file's argument needs revisiting")


def test_the_scanner_is_still_described_without_the_number():
    """THE CONTROL. Removing the count must not remove the capability.

    Deleting the claim outright is the opposite error: the engine really does
    scan continuously across the market, and that is worth saying.
    """
    for rel in ("app/public/index.html", "app/public/explore.html"):
        text = (ROOT / rel).read_text(encoding="utf-8")
        assert re.search(r"liquid perp|Scan the liquid|scan", text, re.IGNORECASE), (
            f"{rel} stopped describing the scanner at all — the number had to "
            "go, the capability did not")
