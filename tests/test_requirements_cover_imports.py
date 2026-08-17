"""Every module bot/ imports must be pinned, or declared optional on purpose.

2026-08-17. The box moved to a fresh Python 3.11 venv built from
`requirements.lock`. The bot started, the bridge started, every health check
passed — and Telegram cards stopped rendering.

`requirements.lock` had TEN pins. `bot/` imports TWENTY modules. Pillow was not
among them, so `bot/formatters/signal_card.py` took this branch:

    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        log.warning("Pillow not installed, cannot render signal card")
        return b""

and its caller does `if png:`. Empty bytes are falsy, so the photo was skipped
and text was sent instead. No error, no crash, one log line nobody reads. On
the old interpreter Pillow happened to be installed system-wide, so the whole
thing had been working by accident.

THIS IS THE HOUSE RULE AT THE DEPENDENCY LAYER. A capability that could not be
loaded rendered as "nothing to send" — absence presented as a normal outcome.
The guarded import is right; what was missing is anything that noticed the
guard had fired.

A GUARDED IMPORT DOES NOT MEAN AN OPTIONAL FEATURE. It means the code survives
the absence. Pillow is guarded at all eleven of its sites and is not optional
at all — that conflation is precisely what let this ship.
"""

import ast
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOCK = ROOT / "requirements.lock"
OPTIONAL = json.loads((ROOT / "tests" / "optional_imports.json").read_text(encoding="utf-8"))

#: Distribution name → the module name it installs under.
DIST_TO_MODULE = {
    "python-dotenv": "dotenv",
    "python-telegram-bot": "telegram",
    "Pillow": "PIL",
    "eth-account": "eth_account",
    "eth-utils": "eth_utils",
    "py-solc-x": "solcx",
}


def pinned_modules():
    mods = set()
    for line in LOCK.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        dist = re.split(r"[=<>!~\[]", line)[0].strip()
        # Everything lowercased on the way in, and compared lowercased on the
        # way out. The first version stored `PIL` from the mapping table and
        # compared `pil`, so the one module this whole file exists for came
        # back unaccounted — a guard that fails on its own subject.
        mods.add(DIST_TO_MODULE.get(dist, dist).lower())
        mods.add(dist.replace("-", "_").lower())
    return mods


def bot_imports():
    """Every third-party top-level module imported anywhere under bot/."""
    std = set(sys.stdlib_module_names)
    local = {"bot", "scripts", "tests", "api_bridge", "dashboard_api"}
    found = {}
    for f in (ROOT / "bot").rglob("*.py"):
        try:
            tree = ast.parse(f.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                mods = [a.name.split(".")[0] for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                mods = [node.module.split(".")[0]]
            else:
                continue
            for m in mods:
                if m not in std and m not in local and not m.startswith("_"):
                    found.setdefault(m, set()).add(str(f.relative_to(ROOT)))
    return found


def test_every_import_is_pinned_or_declared_optional():
    pinned = pinned_modules()
    optional = set(OPTIONAL["optional"])
    unaccounted = sorted(
        m for m in bot_imports() if m.lower() not in pinned and m not in optional)
    assert unaccounted == [], (
        "these modules are imported by bot/ but neither pinned in "
        f"requirements.lock nor declared optional: {unaccounted}\n"
        "A fresh venv will not have them. Pin it, or add it to "
        "tests/optional_imports.json with the reason.")


def test_pillow_is_pinned_because_cards_are_not_optional():
    """The specific regression, pinned by name.

    It is guarded at every site, which is what made it look expendable. If it
    leaves the lock again, cards go back to rendering as nothing at all.
    """
    assert "pil" in pinned_modules() or "pillow" in pinned_modules(), (
        "Pillow left requirements.lock — signal cards will silently stop "
        "rendering, exactly as they did on 2026-08-17")
    assert "PIL" not in OPTIONAL["optional"], (
        "Pillow is guarded at every import site, but a guarded import means "
        "the code SURVIVES the absence, not that the feature is optional")


def test_a_declared_optional_module_that_got_pinned_must_leave_the_list():
    """The ratchet's other half. Without it the file only grows and stops
    describing anything — the known_failures.txt rule, for the same reason."""
    pinned = pinned_modules()
    stale = sorted(m for m in OPTIONAL["optional"] if m.lower() in pinned)
    assert stale == [], (
        f"{stale} is pinned now — delete it from tests/optional_imports.json "
        "in this commit")


def test_a_declared_optional_module_that_is_no_longer_imported_must_leave():
    imported = set(bot_imports())
    gone = sorted(m for m in OPTIONAL["optional"] if m not in imported)
    assert gone == [], (
        f"{gone} is no longer imported by bot/ — delete it from the list")


def test_every_exception_carries_a_reason_someone_can_argue_with():
    for mod, why in OPTIONAL["optional"].items():
        assert isinstance(why, str) and len(why) >= 40, (
            f"{mod}'s reason is too short to be a reason: {why}")
        assert not re.fullmatch(r"(n/a|tbd|todo|internal|optional)\.?", why.strip(), re.I), (
            f"{mod} is excused by a placeholder, not a reason")


def test_the_scan_actually_finds_something():
    """A derived guard whose derivation silently stops matching passes
    vacuously, which is worse than not having it."""
    found = bot_imports()
    assert len(found) >= 15, f"only {len(found)} imports parsed from bot/ — the scan broke"
    for known in ("pydantic", "aiohttp", "numpy", "PIL"):
        assert known in found, f"{known} should have been found"
