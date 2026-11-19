"""When is `pytest.importorskip` honest, and when is it the bug?

2026-08-17, second half of the day. Cards were fixed by pinning Pillow. Charts
were still dead, for the identical reason one layer down: matplotlib,
mplfinance and pandas were imported by `bot/skills/chart_renderer.py` and
installed in no environment, so `build_chart_png` took its
`if not _CHARTS_AVAILABLE: return None` branch and the caller sent text.

What made that survivable for months is in the TEST file, not the source:

    needs_charts = pytest.mark.skipif(
        not cr.charts_available(), reason="charting libs not installed")

Twenty tests, every one of them gated on that. With the libs absent the file
reported twenty skips and the suite was green. With the libs present — first
time today — all twenty pass. The tests were correct and had never run.

THIS IS THE HOUSE RULE AT THE TEST LAYER. `skipif` on a missing dependency
renders "I could not check" as "nothing is wrong", which is the same move as
rendering an unreadable price as `0.00%`. The skip is not wrong in general;
it is wrong when nobody decided the dependency was optional.

So the decision gets a single source of truth — `tests/optional_imports.json`,
which already exists and already ratchets both ways:

    pinned in requirements.lock  →  missing means THIS ENVIRONMENT IS BROKEN.
                                    Fail. A green run here would hide exactly
                                    the defect that took out cards and charts.
    declared optional            →  missing is expected and deliberate. Skip.
    neither                      →  a test-only tool (pytest-xdist). Skip, and
                                    say which bucket it landed in.

PyYAML was this file's example of the third bucket until 2026-11-19. The lock
now names every package an install brings, and `uvicorn[standard]` brings
PyYAML, so `yaml` is pinned and a test that needs it fails rather than skips.

`tests/test_web3_signer.py` skipping on `eth_account` is the honest case and
stays a skip — the signer is declared optional on purpose. The chart tests
were the dishonest case. Nothing but the declaration file tells them apart,
which is why the discrimination lives here rather than in each test.
"""

import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
LOCK = ROOT / "requirements.lock"
_OPTIONAL_JSON = ROOT / "tests" / "optional_imports.json"

#: Distribution name → the module name it installs under, for the ones where
#: the two differ. THE ONE TABLE: test_requirements_cover_imports.py reads it
#: from here. Keys are canonical names (lowercase, `-`), because that is how
#: pip-compile writes them: the lock said `Pillow` while it was written by
#: hand and says `pillow` now, and a table keyed on the old spelling stopped
#: finding `PIL` without any test noticing which line had gone quiet.
DIST_TO_MODULE = {
    "python-dotenv": "dotenv",
    "python-telegram-bot": "telegram",
    "pillow": "PIL",
    "pyyaml": "yaml",
    "eth-account": "eth_account",
    "eth-utils": "eth_utils",
    "py-solc-x": "solcx",
}


def canonical(dist: str) -> str:
    """PEP 503: `Typing_Extensions` and `typing-extensions` are one name."""
    return re.sub(r"[-_.]+", "-", dist).lower()


def pinned_modules() -> set:
    """Lowercased module names that requirements.lock guarantees are present.

    The lock is a pip-compile file: a requirement, then `--hash=` lines, then
    a `# via` note. Only the first is a package. A line that starts with `-`
    is an option, and reading one as a distribution called `--hash` would put
    a module nobody can import on the pinned side of every comparison.
    """
    mods = set()
    for line in LOCK.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith(("#", "-")):
            continue
        dist = canonical(re.split(r"[=<>!~\[;\s]", line)[0])
        mods.add(DIST_TO_MODULE.get(dist, dist).lower())
        mods.add(dist.replace("-", "_"))
    return mods


def optional_modules() -> set:
    return set(json.loads(_OPTIONAL_JSON.read_text(encoding="utf-8"))["optional"])


def require(module: str, why: str = ""):
    """Import `module`, or decide whether skipping it is honest.

    Returns the imported module so this reads as a drop-in for
    `pytest.importorskip`. Raises `Failed` — not `Skipped` — when the module is
    pinned, because a pinned module that will not import is a broken
    environment and the run has established nothing about the code.
    """
    try:
        return __import__(module)
    except ImportError as exc:
        tail = f" ({why})" if why else ""
        if module.lower() in pinned_modules():
            pytest.fail(
                f"{module} is pinned in requirements.lock but will not "
                f"import{tail}: {exc}\n"
                "This is an ENVIRONMENT failure, not a passing test. Skipping "
                "here is how signal cards and charts both shipped broken on "
                "2026-08-17 with a green suite — rebuild the venv from "
                "requirements.lock.")
        if module in optional_modules():
            pytest.skip(f"{module} is declared optional in "
                        f"tests/optional_imports.json{tail}")
        pytest.skip(f"{module} is a test-only tool — neither pinned nor "
                    f"declared optional{tail}")


def requires(module: str, why: str = ""):
    """Decorator form, for whole-file gating.

    `pytest.mark.skipif` cannot express "fail instead" — its only outcomes are
    run and skip — so this evaluates the policy inside the test rather than at
    collection time. The cost is that a pinned-but-missing module reports N
    failures instead of one collection error, which is the louder direction.
    """
    def deco(fn):
        import functools

        @functools.wraps(fn)
        def wrapper(*a, **kw):
            require(module, why)
            return fn(*a, **kw)
        return wrapper
    return deco
