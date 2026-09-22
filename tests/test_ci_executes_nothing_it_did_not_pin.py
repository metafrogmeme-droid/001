r"""CI must not execute what it did not pin, and the rule is spelling-agnostic.

THE DISPROOF WAS THE DEFECT. A recorded finding said CI piped two remote
scripts into a shell. `grep -rn 'curl.*| *sh' .github/workflows/*.yml`
returned NOTHING, and the finding was filed as refuted -- while the code
spelled it

    sh -c "$(curl -sSfL https://release.anza.xyz/v1.18.26/install)"

which is the same execution one syntax over. That is this repo's own
COVERAGE OF A SPELLING IS NOT COVERAGE OF THE GUARD, arriving in the
instrument used to RETIRE a true finding -- a false acquittal, which is the
quiet direction.

So none of these rules greps for a spelling of "pipe into a shell". They ask
what the step DOES: does it fetch bytes over the network, and is there a
checksum in the same step; is every action pinned to a commit rather than to
a tag somebody else can move; does a global install name the version it
installs.

WHAT THIS DELIBERATELY DOES NOT CLAIM, stated because a gate whose coverage
is overstated is the failure this repo's gates exist to prevent:

* It checks the ENTRYPOINT, not the closure. The anza installer's hash is
  verified and that script then downloads toolchain tarballs itself, so a
  compromised release host still serves compromised binaries to a script
  whose hash checks out. That limit is written beside the step.
* `npm ci` and `pip install -r` are not fetch sites here. They resolve from
  lockfiles and manifests with their own integrity story (driven elsewhere:
  944 of 950 lockfile entries carry integrity hashes), and folding them in
  would make this rule fire on every dependency install in the tree.
* The POSTURE this measures was already good: 17 of 17 `uses:` were
  SHA-pinned before this guard existed. The two holes were the ones nobody
  had a rule for.
"""

from __future__ import annotations

import pathlib
import re

import pytest
import yaml

ROOT = pathlib.Path(__file__).resolve().parents[1]
WORKFLOWS = sorted((ROOT / ".github/workflows").glob("*.yml"))

_SHA = re.compile(r"^[^@\s]+@[0-9a-f]{40}$")
_FETCH = re.compile(r"\b(curl|wget)\b")
_CHECKSUM = re.compile(r"\b(sha256sum|shasum|sha512sum|cosign|gpg)\b.*-c|\b(sha256sum|shasum)\s+-c")
_GLOBAL_INSTALL = re.compile(r"\bcargo install\b|\bnpm\s+(?:i|install)\s+-g\b|\bpipx install\b|\bgo install\b")


def _uncommented(script: str) -> str:
    """The shell a step really runs. A comment that quotes the thing it
    forbids is indistinguishable from the code doing it -- and both new
    comments in ci.yml quote `curl ... | sh` to explain why it was removed."""
    return "\n".join(re.sub(r"#.*$", "", line) for line in script.splitlines())


def _steps():
    for wf in WORKFLOWS:
        doc = yaml.safe_load(wf.read_text())
        for job_name, job in (doc.get("jobs") or {}).items():
            for i, step in enumerate(job.get("steps") or []):
                yield wf.name, job_name, i, step


def test_there_are_workflows_to_check():
    """A rule over an empty set passes for the wrong reason."""
    assert WORKFLOWS, "no workflows found — this whole file would vacuously pass"
    assert sum(1 for _ in _steps()) > 20


def _faults(step) -> set[str]:
    """Which rules this ONE step breaks. THE one reading.

    The first draft had the three rules written twice -- once inline in the
    assertions below and once in a `_verdicts` helper the planted fixtures
    drove. They agreed on every fixture, which is what a second copy looks
    like from outside, and the mutation round said so: weakening the real
    fetch rule changed no planted verdict and weakening the planted one
    changed no real verdict. A second copy of a map is a second answer.
    """
    bad: set[str] = set()
    uses = step.get("uses")
    if uses and not uses.startswith("./") and not _SHA.match(uses):
        bad.add("action")
    script = _uncommented(str(step.get("run") or ""))
    if _FETCH.search(script) and not _CHECKSUM.search(script):
        bad.add("fetch")
    for line in script.splitlines():
        if _GLOBAL_INSTALL.search(line) and not re.search(r"--version|@\d", line):
            bad.add("install")
    return bad


def test_every_action_is_pinned_to_a_commit():
    """A tag is a pointer its owner can move under you."""
    loose = [f"{wf}:{job}[{i}] {step.get('uses')}"
             for wf, job, i, step in _steps() if "action" in _faults(step)]
    assert not loose, (
        "these actions are pinned to a movable ref rather than a commit sha: "
        f"{loose}")


def test_every_network_fetch_is_checksummed():
    unverified = [f"{wf}:{job}[{i}] {step.get('name') or '<unnamed>'}"
                  for wf, job, i, step in _steps() if "fetch" in _faults(step)]
    assert not unverified, (
        "these steps fetch bytes over the network and execute or install them "
        "without verifying a checksum in the same step. The gitleaks step in "
        "ci.yml is the in-tree template: pin the version, carry the sha256 in "
        f"`env:`, curl to a file, `sha256sum -c -`, then run it. {unverified}")


def test_every_global_install_names_its_version():
    """`cargo install X` resolves whatever is newest on the day, and this
    workflow's own comment records that rust-cache keeps ~/.cargo/bin across
    runs -- so an unpinned resolution is not one job's risk."""
    loose = [f"{wf}:{job}[{i}] {step.get('name') or '<unnamed>'}"
             for wf, job, i, step in _steps() if "install" in _faults(step)]
    assert not loose, (
        f"these install a tool globally without naming its version: {loose}")


# The three rules pass on the real tree, so a mutation of a RULE changes no
# verdict there -- and a rule no input can reach is a claim that there is a
# check. They are driven on PLANTED workflows, the argument
# candle_hygiene_baseline.txt already makes for its own.

_PLANTED = {
    "tag-pinned action": {
        "jobs": {"j": {"steps": [{"uses": "actions/checkout@v4"}]}}},
    "curl piped to sh": {
        "jobs": {"j": {"steps": [{"name": "x", "run": "curl -sSL https://e.example/i | sh"}]}}},
    "curl in a command substitution": {
        "jobs": {"j": {"steps": [{"name": "x", "run": 'sh -c "$(curl -sSfL https://e.example/i)"'}]}}},
    "curl to a file then run, no checksum": {
        "jobs": {"j": {"steps": [{"name": "x", "run": "curl -o i.sh https://e.example/i\nsh i.sh"}]}}},
    "wget": {
        "jobs": {"j": {"steps": [{"name": "x", "run": "wget https://e.example/i && sh i"}]}}},
    "unpinned cargo install": {
        "jobs": {"j": {"steps": [{"name": "x", "run": "cargo install cargo-audit --locked"}]}}},
}

_PLANTED_CLEAN = {
    "sha-pinned action": {
        "jobs": {"j": {"steps": [{"uses": "actions/checkout@" + "a" * 40}]}}},
    "checksummed fetch": {
        "jobs": {"j": {"steps": [{"name": "x", "run":
            "curl -sSfLo i.sh https://e.example/i\n"
            'echo "abc  i.sh" | sha256sum -c -\nsh i.sh'}]}}},
    "pinned cargo install": {
        "jobs": {"j": {"steps": [{"name": "x", "run": "cargo install cargo-audit --locked --version 0.22.2"}]}}},
    "a comment that quotes the forbidden form": {
        "jobs": {"j": {"steps": [{"name": "x", "run":
            "# this was `curl https://e.example/i | sh` and is not any more\n"
            "echo ok"}]}}},
    "npm ci is not a fetch site": {
        "jobs": {"j": {"steps": [{"name": "x", "run": "npm ci"}]}}},
}


def _verdicts(doc) -> set[str]:
    """The planted workflow's faults, through the SAME `_faults` the real
    assertions use -- so a mutation of the rule moves both."""
    bad: set[str] = set()
    for job in (doc.get("jobs") or {}).values():
        for step in job.get("steps") or []:
            bad |= _faults(step)
    return bad


@pytest.mark.parametrize("label", sorted(_PLANTED))
def test_a_planted_hole_is_caught(label):
    assert _verdicts(_PLANTED[label]), f"{label!r} slipped past every rule"


@pytest.mark.parametrize("label", sorted(_PLANTED_CLEAN))
def test_a_planted_clean_step_is_not_accused(label):
    assert not _verdicts(_PLANTED_CLEAN[label]), f"{label!r} was falsely accused"


# ---------------------------------------------------------------------------
# THE SCA GATE'S SUBJECT IS NOT THE FILE ANYTHING INSTALLS, and that is fine
# only while one contains the other.
#
# Driven: `pip-audit -r requirements.lock` (17 packages) is the audit;
# `bot/requirements.txt` (12) is what the Dockerfile and Makefile install;
# `requirements-ci.txt` (27) is what CI installs. Nothing installs the lock at
# all -- its own header used to say "use it for production" and to name an
# update command running `pip install -r requirements.txt`, a file that does
# not exist at that path. The header is corrected; this is the invariant that
# keeps the correction true.
# ---------------------------------------------------------------------------

_REQ_NAME = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)")


def _declared(rel: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in (ROOT / rel).read_text().splitlines():
        line = line.strip()
        if not line or line.startswith(("#", "-")):
            continue
        m = _REQ_NAME.match(line)
        if m:
            out[m.group(1).lower().replace("_", "-")] = line
    return out


def _audited_manifest() -> str:
    m = re.search(r"pip-audit\s+-r\s+(\S+)",
                  (ROOT / ".github/workflows/ci.yml").read_text())
    assert m, "no `pip-audit -r <file>` step in ci.yml — the SCA gate is gone"
    return m.group(1)


def _shipped() -> dict[str, str]:
    """What the production image really installs, following the COPY.

    The Dockerfile says `-r requirements.txt` and there is no such file at
    this path -- it is `COPY bot/requirements.txt ./requirements.txt` two
    lines above. That indirection is exactly what made the lock file's own
    header claim a root manifest that does not exist, so the derivation
    follows it rather than reading the install line alone.

    It also picks up the packages named INLINE on the install line
    (`fastapi`, `uvicorn`), which are shipped and appear in no manifest at
    all -- a hand-written list of files would have missed them entirely.
    """
    docker = (ROOT / "Dockerfile").read_text()
    m = re.search(r"pip install([^\n\\]*(?:\\\n[^\n\\]*)*)", docker)
    assert m, "the Dockerfile installs nothing with pip"
    install_line = m.group(1)

    ref = re.search(r"-r\s+(\S+)", install_line)
    assert ref, "the Dockerfile's pip install names no requirements file"
    wanted = ref.group(1).lstrip("./")

    copied = dict(re.findall(r"^COPY\s+(\S+)\s+\.?/?(\S+)\s*$", docker, re.M))
    src = next((s for s, d in copied.items() if d.lstrip("./") == wanted), wanted)
    assert (ROOT / src).exists(), (
        f"the Dockerfile installs `-r {wanted}` and nothing in the build "
        f"context provides it")

    out = _declared(src)
    # FLAGS FIRST. The first draft read `--prefix=/install` as a package
    # called "prefix" and accused the Dockerfile of shipping it -- a false
    # accusation manufactured by the instrument, which is the shape this
    # repo's guards keep producing when a scan is one token too greedy.
    bare = re.sub(r"(?:^|\s)-{1,2}[^\s=]+(?:=\S+)?", " ",
                  re.sub(r"-r\s+\S+", " ", install_line))
    for extra in re.findall(r'["\']?([A-Za-z][A-Za-z0-9._-]*)(?:\[[^\]]*\])?\s*[><=!]=',
                            bare):
        out.setdefault(extra.lower().replace("_", "-"), extra)
    return out


def test_every_shipped_package_is_in_the_file_pip_audit_reads():
    """Both filenames are DERIVED, not typed. The first draft named them and
    kept a separate canary asserting ci.yml still said `requirements.lock` --
    and the round showed that canary could be disabled with nothing noticing.
    Repointing either side now moves this test's subject."""
    shipped = _shipped()
    audited = _declared(_audited_manifest())
    assert shipped and audited, "a containment over an empty set proves nothing"
    missing = sorted(set(shipped) - set(audited))
    assert not missing, (
        "these are installed into the shipped image by the Dockerfile and are "
        "absent from the only file the SCA gate audits, so they ship and are "
        f"never scanned: {missing}")


def test_the_lock_header_names_a_file_that_exists():
    """Its previous update command named `requirements.txt`, which is not
    there — a header naming a command that does nothing."""
    header = "\n".join(line for line in
                       (ROOT / "requirements.lock").read_text().splitlines()
                       if line.startswith("#"))
    for ref in re.findall(r"-r\s+(\S+)", header):
        assert (ROOT / ref).exists(), (
            f"the lock's header tells a reader to run `-r {ref}`, and that "
            f"file does not exist")
