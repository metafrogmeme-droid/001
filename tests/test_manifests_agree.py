"""The file Docker installs must be the file CI audits.

`Dockerfile:16-17` installs `bot/requirements.txt`. `ci.yml` runs
`pip-audit -r requirements.lock`. Those were different files with different
pins, so commit 7f2e715 — "aiohttp 3.14.1 → 3.14.3, PYSEC-2026-3545/3546/3547"
— bumped the two manifests CI reads and left the one the production image
installs. For five days the container that trades real money shipped the three
advisories the repo believed it had fixed, and the SCA gate reported green the
entire time.

That is this repository's own named defect, in its supply chain: running a
check over a subset and reporting it as the whole. It is the reason
`preflight.py` parses ci.yml instead of restating it, and the same reasoning
applies one layer down — a manifest nobody audits is a manifest nobody can
vouch for.

WHY A PIN-AGREEMENT TEST RATHER THAN ONE MANIFEST. Merging the files would
break two deliberate properties: `requirements-ci.txt` omits heavy extras
because the known-failures baseline was generated without them, and the lock
carries what a freeze produces. So the manifests stay separate and the
INVARIANT is pinned instead: where two of them name the same package, they must
name the same version. New drift fails; today's state is clean.

Exclusions belong in ALLOWED_DIVERGENCE with a reason, never as a silent skip —
an unexplained exemption is how the next 7f2e715 hides.
"""
from __future__ import annotations

import pathlib
import re

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent

MANIFESTS = [
    "bot/requirements.txt",     # what the Dockerfile installs
    "requirements-ci.txt",      # what CI installs to run the suite
    "requirements.lock",        # what pip-audit reads
    "pyproject.toml",           # published metadata; not installed anywhere
]

#: package -> why its versions are allowed to differ. Empty today, and it
#: should stay that way unless a divergence is genuinely intended: the
#: cryptography major bump was held back for vault compatibility at one point,
#: and if that returns it belongs here WITH that sentence, not as a quiet skip.
ALLOWED_DIVERGENCE: dict[str, str] = {}

_PIN = re.compile(r'^\s*"?([A-Za-z0-9_.\-]+)==([0-9][^"\s,;]*)', re.M)


def _pins(rel: str) -> dict[str, str]:
    path = ROOT / rel
    if not path.exists():
        return {}
    return {m.group(1).lower(): m.group(2)
            for m in _PIN.finditer(path.read_text(encoding="utf-8"))}


ALL = {rel: _pins(rel) for rel in MANIFESTS}


def test_the_manifests_are_all_present_and_parseable():
    """A silently-empty manifest would make every check below vacuous."""
    for rel in MANIFESTS:
        assert ALL[rel], f"{rel} parsed to zero pins — did its format change?"


def test_shared_pins_agree_across_manifests():
    disagree = []
    names = {n for pins in ALL.values() for n in pins}
    for name in sorted(names):
        seen = {rel: pins[name] for rel, pins in ALL.items() if name in pins}
        if len(seen) < 2 or len(set(seen.values())) == 1:
            continue
        if name in ALLOWED_DIVERGENCE:
            continue
        disagree.append(f"{name}: " + ", ".join(f"{v} ({k})" for k, v in seen.items()))
    assert not disagree, (
        "these packages are pinned to different versions in different "
        "manifests, so the artifact CI audits is not the artifact that "
        "ships:\n  " + "\n  ".join(disagree))


def test_the_dockerfile_installs_a_manifest_this_test_covers():
    """The whole point is that the DEPLOYED file is in scope. If the Dockerfile
    starts installing something else, this check silently stops protecting the
    image — so the link is asserted rather than assumed."""
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    installed = re.findall(r"COPY\s+(\S*requirements[\w.-]*\.txt)", dockerfile)
    assert installed, "no requirements file COPYed in the Dockerfile"
    for src in installed:
        assert src in MANIFESTS, (
            f"Dockerfile installs {src}, which no manifest check covers")


def test_pip_audit_reads_a_manifest_this_test_covers():
    """Same link on the other side: the audited file must be one of the files
    whose pins are held to agree with the deployed one."""
    ci = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    audited = re.findall(r"pip-audit\s+-r\s+(\S+)", ci)
    assert audited, "no pip-audit invocation found in ci.yml"
    for target in audited:
        assert target in MANIFESTS, (
            f"pip-audit reads {target}, which no manifest check covers")


@pytest.mark.parametrize("rel", MANIFESTS)
def test_aiohttp_carries_the_advisory_fix(rel):
    """The specific regression: PYSEC-2026-3545/3546/3547 were fixed in 3.14.3
    and the deployed manifest kept 3.14.1. Named explicitly because a general
    agreement check would also pass if every manifest agreed on the VULNERABLE
    version."""
    pins = ALL[rel]
    if "aiohttp" not in pins:
        pytest.skip(f"{rel} does not pin aiohttp")
    parts = tuple(int(x) for x in re.findall(r"\d+", pins["aiohttp"])[:3])
    assert parts >= (3, 14, 3), (
        f"{rel} pins aiohttp {pins['aiohttp']}; 3.14.3 carries the "
        "PYSEC-2026-3545/3546/3547 fixes")
