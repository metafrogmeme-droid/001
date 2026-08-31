"""The operator dashboard's mode badge must report the mode, not assert one.

RC-2026-023. `bot/web/dashboard.html` shipped its header badge as literal
markup::

    <span class="header-badge badge-sim" id="modeBadge">
      <span class="status-dot dot-amber" id="statusDot"></span>
      SIMULATION
    </span>

and `updateEngine` never touched it. The SERVER half had already been fixed
(RC-AUD-016, `dashboard_server.py`), so the real mode sat in the payload and
was unreachable from the UI -- code present, never reached, which is the one
thing a source scan cannot distinguish from code that works.

The dot INSIDE the badge *is* updated (`dashboard.html:824`), so a live engine
rendered as a **green-dot SIMULATION pill**: the reassuring word, with a live
indicator beside it, on the console an operator consults to find out whether
real money is moving.

Three things are pinned here, and the middle one is the reason this file runs
node rather than grepping:

1. the markup must not hardcode a mode;
2. `modeBadgeView` must answer all FOUR states -- and the fourth is the point.
   `dashboard_server.py`'s outer handler emits ``{"state": "UNKNOWN"}`` with no
   mode key at all, so "nobody read it" is a state that reaches this badge in
   ordinary operation, and it must not resolve to a word;
3. colour is a claim -- UNKNOWN and IDLE must not borrow the live green.
"""
import json
import re
import subprocess
from pathlib import Path

import pytest

PAGE = Path(__file__).resolve().parents[1] / "bot" / "web" / "dashboard.html"


def _slice_function(src: str, name: str) -> str:
    """Return the source of `function <name>(...) {...}` by brace matching.

    Comments are not stripped here because the extracted text is EXECUTED
    rather than pattern-matched -- the hazard CLAUDE.md warns about (a comment
    quoting the string it forbids) cannot apply to code node actually runs.
    """
    start = src.find(f"function {name}(")
    if start == -1:
        raise AssertionError(
            f"{name}() is not defined in dashboard.html. The mode badge has no "
            "seam, so nothing can plant a payload and read what the operator "
            "would see."
        )
    i = src.index("{", start)
    depth = 0
    for j in range(i, len(src)):
        if src[j] == "{":
            depth += 1
        elif src[j] == "}":
            depth -= 1
            if depth == 0:
                return src[start:j + 1]
    raise AssertionError(f"unbalanced braces while slicing {name}()")


def _run_view(payloads):
    """Evaluate modeBadgeView(...) under node for each payload."""
    src = PAGE.read_text(encoding="utf-8")
    fn = _slice_function(src, "modeBadgeView")
    script = (
        fn
        + "\nconst out = "
        + json.dumps(payloads)
        + ".map((p) => modeBadgeView(p));\n"
        + "process.stdout.write(JSON.stringify(out));\n"
    )
    proc = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, timeout=60
    )
    if proc.returncode != 0:
        raise AssertionError(f"node failed: {proc.stderr.strip()}")
    return json.loads(proc.stdout)


def test_the_header_markup_does_not_hardcode_a_trading_mode():
    """The badge ships with no mode asserted before anything is read."""
    src = PAGE.read_text(encoding="utf-8")
    m = re.search(r'id="modeBadge"[^>]*>(.*?)</span>\s*</span>', src, re.S)
    assert m, "could not locate the #modeBadge element"
    badge = m.group(1)
    # Anchored to the badge's own markup rather than the whole file: the word
    # SIMULATION legitimately appears elsewhere (CSS class names, the view's
    # own vocabulary). Asserting a short string is absent from a whole file is
    # the assertion that keeps misfiring in this repo.
    for word in ("SIMULATION", "LIVE", "PAPER"):
        assert word not in badge, (
            f"the header badge hardcodes {word!r}. Whatever the engine is "
            "actually doing, the operator reads this word."
        )


@pytest.mark.parametrize(
    "name,engine,must_say,must_not_say",
    [
        ("live is named", {"trading_mode": "LIVE"}, "LIVE", "SIMULATION"),
        ("paper is named", {"trading_mode": "PAPER"}, "PAPER", "LIVE"),
        ("idle is not simulation", {"trading_mode": "IDLE"}, "IDLE", "LIVE"),
        # The two shapes the server actually emits when it could not read the
        # config. Neither may resolve to a word about trading.
        ("explicit unknown", {"trading_mode": "UNKNOWN"}, "UNKNOWN", "SIMULATION"),
        ("the key is absent entirely", {"state": "UNKNOWN"}, "UNKNOWN", "SIMULATION"),
        ("no engine block at all", None, "UNKNOWN", "SIMULATION"),
    ],
)
def test_every_state_including_the_unreadable_one(name, engine, must_say, must_not_say):
    view = _run_view([engine])[0]
    assert must_say in view["text"], f"{name}: expected {must_say!r}, got {view['text']!r}"
    assert must_not_say not in view["text"], (
        f"{name}: says {must_not_say!r}, which it cannot know"
    )


def test_only_live_gets_the_live_colour():
    """Colour is a claim. A green pill says 'real money' as loudly as the word."""
    views = _run_view(
        [
            {"trading_mode": "LIVE"},
            {"trading_mode": "PAPER"},
            {"trading_mode": "IDLE"},
            {"trading_mode": "UNKNOWN"},
            {"state": "UNKNOWN"},
        ]
    )
    live, rest = views[0], views[1:]
    assert live["badgeClass"] == "badge-live"
    for v in rest:
        assert v["badgeClass"] != "badge-live", (
            f"{v['text']!r} borrows the live badge colour"
        )


def test_an_unreadable_mode_is_not_styled_as_a_measured_one():
    """UNKNOWN must be visually distinct from PAPER, not just differently worded.

    The defect this replaces rendered a live engine as a green-dot SIMULATION
    pill; a fix that gives 'unknown' the same amber as 'paper' repeats it one
    step further along.
    """
    paper, unknown = _run_view([{"trading_mode": "PAPER"}, {"trading_mode": "UNKNOWN"}])
    assert unknown["badgeClass"] != paper["badgeClass"]


# ── the server half ────────────────────────────────────────────────────────

class _Cfg:
    """A stand-in for CONFIG with the three behaviours that matter."""

    def __init__(self, live, sim=True, raises=False):
        self._live, self._sim, self._raises = live, sim, raises

    def is_live(self):
        if self._raises:
            raise RuntimeError("config unreadable")
        return self._live

    @property
    def simulation_mode(self):
        return self._sim


@pytest.mark.parametrize(
    "cfg,expected_mode,expected_sim",
    [
        (_Cfg(live=True), "LIVE", False),
        (_Cfg(live=False, sim=True), "PAPER", True),
        (_Cfg(live=False, sim=False), "IDLE", True),
        (_Cfg(live=False, raises=True), "UNKNOWN", True),
    ],
)
def test_simulation_mode_keeps_its_old_value_for_every_input(
    cfg, expected_mode, expected_sim
):
    """`simulation_mode` is now derived; it must not have changed meaning.

    The payload gained `trading_mode` and kept `simulation_mode` so no existing
    reader shifts underfoot. That compatibility claim was a sentence in a
    comment, which is the part that rots first -- so it is executed here. The
    old expression was `not is_live()` with an `except -> True` fail-safe.
    """
    from bot.core.live_readiness import mode_label

    mode = mode_label(cfg)
    assert mode == expected_mode
    assert (mode != "LIVE") is expected_sim

    # And the old expression, evaluated the old way, agrees.
    try:
        legacy = not cfg.is_live()
    except Exception:
        legacy = True
    assert (mode != "LIVE") is legacy
