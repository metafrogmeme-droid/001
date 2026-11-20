"""The locks carry hashes, every install asks for them, and the inputs pin everything.

Until 2026-11-19 `requirements.lock` was twenty top-level pins. About forty-five
packages came in under them at whatever version was newest that day, nothing
had a hash, and the header said to regenerate the file with `pip freeze`. CI
installed its own set the same way. The pip wheel and the Docker base image
were pinned by hash and digest; everything they then installed was not.

The locks are now written by `scripts/compile_locks.py` from the `*.in` file
beside each, and installed with `pip install --require-hashes`. Three things
have to stay true for that to mean anything, and each has a test here:

  1. A lock is what its input says. Every package in an output is pinned in
     the input, at that version, so a compile has nothing to choose and a
     newer release upstream cannot move a pin.
  2. Every install asks. One unhashed `pip install` beside a hashed one
     brings its packages in from wherever pip finds them. The Makefile had
     exactly that: `uvicorn[standard]` on a line of its own, after the lock.
  3. The check says what it could not check. `--check` exits 2, not 0, when
     it ran on the wrong interpreter or could not reach the index.

NO NETWORK HERE. Whether the committed hashes are still the ones the index
publishes is the CI job's question ("The committed lock is the compiled
lock"); `make lock-check` asks it locally.
"""
from __future__ import annotations

import importlib.metadata
import re
import subprocess
from pathlib import Path

import pytest

from tests import pip_installs
from tests.dep_policy import require
from tests.pip_installs import compile_locks as cl

ROOT = Path(__file__).resolve().parent.parent
LOCKS = cl.LOCKS
SECTION = "# ── Transitives"


def _lock(target: str) -> dict:
    return cl.parse_lock((ROOT / target).read_text(encoding="utf-8"))


# ── 1. a lock is what its input says ──────────────────────────────────────

@pytest.mark.parametrize("target", [target for _, target in LOCKS])
def test_every_requirement_has_an_exact_pin_and_a_hash(target):
    text = (ROOT / target).read_text(encoding="utf-8")
    pins = _lock(target)
    assert pins, f"{target} parsed to no requirement at all"
    for name, pin in pins.items():
        assert pin.hashes, f"{target}: {name}=={pin.version} carries no sha256"
    # Nothing at column 0 but a comment or a requirement the parser read. A
    # line it did not read (`name>=1`, an option) would be a requirement this
    # test never looked at.
    unread = [line for line in text.splitlines()
              if line and not line.startswith(("#", " ", "\t")) and not cl._REQ.match(line)]
    assert unread == [], f"{target} has lines that are not `name==version`: {unread}"


def test_the_parse_finds_the_whole_lock():
    """A parser that stopped early would make every check above pass on a stub."""
    assert len(_lock("requirements.lock")) >= 60
    assert len(_lock("requirements-ci.txt")) >= 90
    for name in ("ccxt", "cryptography", "certifi", "setuptools", "httpx", "pydantic-core"):
        assert name in _lock("requirements.lock"), name
    assert "pytest" in _lock("requirements-ci.txt")


@pytest.mark.parametrize("source,target", LOCKS)
def test_each_lock_pins_exactly_what_its_input_pins(source, target):
    declared = cl.parse_input(source)
    compiled = {name: pin.version for name, pin in _lock(target).items()}
    assert cl.unpinned(_lock(target), declared) == [], (
        f"{target} holds packages {source} does not pin. pip-compile chose those versions; "
        "a later compile is free to choose others.")
    assert compiled == declared, (
        f"{target} and {source} disagree. Edit the input, then run "
        "`python3 scripts/compile_locks.py`; never edit the generated file.")


def test_the_pins_this_change_was_told_not_to_move():
    """Named because a general "inputs equal outputs" check passes just as
    well when both moved together. ccxt's parsing is what the executor reads."""
    lock, ci = _lock("requirements.lock"), _lock("requirements-ci.txt")
    assert lock["ccxt"].version == "4.5.56"
    assert lock["cryptography"].version == "50.0.0"
    assert lock["certifi"].version == "2026.10.19"
    assert ci["pytest"].version == "9.0.3"
    assert _lock("requirements-xdist.txt")["pytest-xdist"].version == "3.8.0"


#: Transitive pins that nothing in their lock requires, each with the reason
#: it is named anyway. Two-way: an entry that gained a parent, or left the
#: input, fails below.
NAMED_WITHOUT_A_PARENT = {
    ("requirements.in", "async-timeout"):
        "redis 8.1.0 requires it below Python 3.11.3 only. The lock is compiled on a newer 3.11, "
        "where the compile never sees that requirement, and a hashed install on 3.11.0-3.11.2 "
        "refuses a package the lock does not name.",
    ("requirements.in", "colorama"):
        "tqdm requires it on Windows only, and docs/gitbook/getting-started.md documents a "
        "Windows install. A Linux compile never sees the requirement.",
}


def _transitive_pins(source: str) -> list[str]:
    """The pins of one input below its Transitives line (its own, not its `-r`'s)."""
    text = (ROOT / source).read_text(encoding="utf-8")
    assert text.count(SECTION) == 1, f"{source} must mark where its transitive pins begin, once"
    below = text.split(SECTION, 1)[1]
    return [cl.canon(found.group(1)) for found in
            (cl._REQ.match(line.split("#", 1)[0].strip()) for line in below.splitlines()) if found]


def _parents(pin) -> list[str]:
    return [who for who in pin.via if not who.startswith("-r ")]


@pytest.mark.parametrize("source,target", LOCKS)
def test_no_transitive_pin_outlives_what_required_it(source, target):
    """The check that a lock equals its input is one-way about time: a pin
    whose parent dropped the dependency would stay, installed and audited for
    nothing, until somebody read the list."""
    lock = _lock(target)
    orphans = [name for name in _transitive_pins(source)
               if not _parents(lock[name]) and (source, name) not in NAMED_WITHOUT_A_PARENT]
    assert orphans == [], (
        f"{source} pins {orphans} as transitives and nothing in {target} requires them. "
        "Remove the pin, or name it in NAMED_WITHOUT_A_PARENT with the reason.")


def test_the_named_exceptions_still_hold():
    for (source, name), why in NAMED_WITHOUT_A_PARENT.items():
        assert len(why) >= 60, f"{name}: too short to be a reason"
        target = dict(LOCKS)[source]
        assert name in _transitive_pins(source), f"{name} is no longer pinned in {source}: delete its entry"
        assert not _parents(_lock(target)[name]), (
            f"{name} has a parent in {target} now ({_lock(target)[name].via}): delete its entry")


def test_a_section_reader_that_finds_nothing_is_caught():
    for source, _ in LOCKS:
        assert _transitive_pins(source), f"{source}: no pin found below its Transitives line"
    assert "setuptools" in _transitive_pins("requirements.in")
    assert "ccxt" not in _transitive_pins("requirements.in")


# ── 2. every install asks ─────────────────────────────────────────────────

#: Installs that do not check hashes, each with a reason. Two-way.
#:
#: Empty since 2026-11-20, and it should stay that way. It held three for a
#: day: the nightly scan's `pip-audit==2.9.0`, and two lines of the GitLab
#: pipeline (a root requirements.txt that has never existed in this
#: repository's history, then seven tools by name alone). All three now
#: install requirements-ci.txt under --require-hashes. An entry here is a
#: claim that some install may take whatever the index serves; write down who
#: decided and why.
UNHASHED: dict = {}


def _unhashed() -> set:
    return {(rel, arguments) for rel in pip_installs.build_files()
            for arguments, kind in pip_installs.installs(rel) if kind == "unhashed"}


def test_every_pip_install_checks_hashes_or_is_named_with_a_reason():
    unexplained = sorted(_unhashed() - set(UNHASHED))
    assert unexplained == [], (
        "these installs download without checking a hash:\n  "
        + "\n  ".join(f"{rel}: pip install {arguments}" for rel, arguments in unexplained)
        + "\nInstall a generated lock with --require-hashes, and nothing else on that line.")


def test_no_stale_exception_for_an_unhashed_install():
    gone = sorted(set(UNHASHED) - _unhashed())
    assert gone == [], f"these installs are hashed now, or gone: delete their entries: {gone}"
    for why in UNHASHED.values():
        assert len(why) >= 60


def test_the_scan_finds_the_installs_it_is_about():
    kinds = {rel: [kind for _, kind in pip_installs.installs(rel)] for rel in pip_installs.build_files()}
    assert kinds["Makefile"] == ["wheel", "locked"]
    assert kinds["Dockerfile"] == ["wheel", "wheel", "locked"]
    assert kinds[".github/workflows/ci.yml"].count("locked") >= 5
    assert kinds[".github/workflows/ci.yml"].count("wheel") >= 4
    # The two that were excused by name until 2026-11-20. Asserted here, and
    # not left to the empty exception table: a scan that stopped reading one
    # of these files would find nothing unhashed in it either.
    assert kinds[".github/workflows/nightly-audit.yml"] == ["wheel", "locked"]
    assert kinds[".gitlab-ci.yml"] == ["wheel", "locked"]


@pytest.mark.parametrize("arguments,kind", [
    # What the Makefile ran after the lock until 2026-11-19.
    ('"fastapi==0.141.1" "uvicorn[standard]==0.52.3"', "unhashed"),
    # What CI ran.
    ("-r requirements-ci.txt", "unhashed"),
    ('"pytest-xdist==3.8.0"', "unhashed"),
    # The flag with a requirement beside the lock: the one thing with no hash.
    ("--require-hashes -r requirements.lock fastapi==0.141.1", "unhashed"),
    # The flag over a file nobody generated.
    ("--require-hashes -r bot/requirements.txt", "unhashed"),
    # A pip from somewhere else, and a wheel URL with no hash in it.
    ('"pip @ https://example.org/pip-26.2.2-py3-none-any.whl#sha256=' + "0" * 64 + '"', "unhashed"),
    ('"pip @ https://files.pythonhosted.org/packages/aa/bb/pip-26.2.2-py3-none-any.whl"', "unhashed"),
    ("--upgrade pip", "unhashed"),
    # The two shapes that are allowed.
    ("--no-cache-dir --prefix=/install --require-hashes -r requirements.lock", "locked"),
    ('--no-cache-dir "pip @ https://files.pythonhosted.org/packages/aa/bb/pip-26.2.2-py3-none-any.whl'
     '#sha256=' + "0" * 64 + '"', "wheel"),
])
def test_the_classifier_tells_the_shapes_apart(arguments, kind):
    assert pip_installs.classify(arguments) == kind


def test_a_comment_that_quotes_an_install_is_not_an_install():
    text = "# pip " + "install -r requirements.lock used to be the command\nRUN true\n"
    assert pip_installs.invocations(text) == []
    joined = "RUN python -m pip install --no-cache-dir \\\n    --require-hashes -r requirements.lock\n"
    assert pip_installs.invocations(joined) == ["--no-cache-dir --require-hashes -r requirements.lock"]


#: Files that quote an install of a lock WITHOUT the flag as a record of what
#: was once run or once found. Two-way.
HISTORICAL = {
    "deploy.sh": "A dated comment (2026-08-17) recording the command that failed on the box. "
                 "deploy.sh itself installs nothing.",
    "tests/test_python_version_gate.py": "The same dated incident, quoted in a docstring.",
    "audit/workflow_raw_findings.md": "Audit findings quoting the remediation proposed at the time.",
    "docs/DEEP_AUDIT_2026-08-14.md": "An audit's citation of ci.yml as it read on that date.",
    "docs/TOKEN_SECURITY_AUDIT.md": "An audit's quotation of ci.yml as it read on that date.",
}

_INSTALL_OF_A_LOCK = re.compile(
    r"pip3?\s+install\b[^\n]*-r\s+(?:" + "|".join(re.escape(name) for name in pip_installs.HASHED) + r")\b")
_TEXT = (".md", ".sh", ".py", ".yml", ".yaml", ".txt", ".toml", ".template", ".service", ".in")


def _tracked() -> list[str]:
    listing = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, check=True)
    names = [name for name in listing.stdout.decode("utf-8").split("\0") if name]
    assert len(names) > 1000, "git ls-files returned almost nothing; this is not the repository"
    return names


def _unflagged_installs() -> dict[str, list[str]]:
    """{file: lines} that tell a reader to install a lock without --require-hashes."""
    found: dict[str, list[str]] = {}
    for name in _tracked():
        if not (name.endswith(_TEXT) or Path(name).name in ("Makefile", "Dockerfile")):
            continue
        path = ROOT / name
        if not path.is_file() or name in pip_installs.HASHED:
            continue
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            if _INSTALL_OF_A_LOCK.search(line) and "--require-hashes" not in line:
                found.setdefault(name, []).append(line.strip())
    return found


def test_nobody_is_told_to_install_a_lock_without_its_hashes():
    found = _unflagged_installs()
    unexplained = {name: lines for name, lines in found.items() if name not in HISTORICAL}
    assert unexplained == {}, (
        "these tell a reader to install a hashed lock with the check switched off. pip turns "
        "it on by itself when the file has hashes, so the command still works; the reader who "
        "copies the command onto another file does not get it:\n"
        + "\n".join(f"  {name}: {line}" for name, lines in unexplained.items() for line in lines))
    stale = sorted(set(HISTORICAL) - set(found))
    assert stale == [], f"these no longer quote the old command: delete their entries: {stale}"


def test_the_documented_install_is_the_hashed_one():
    """The positive half: the docs an operator follows give the command."""
    for rel in ("docs/DEPLOY_COMMANDS.md", "README.md", "docs/gitbook/getting-started.md", "Makefile"):
        text = (ROOT / rel).read_text(encoding="utf-8")
        assert "pip install --require-hashes -r requirements.lock" in text, rel


@pytest.mark.parametrize("target", [target for _, target in LOCKS])
def test_a_generated_file_says_how_it_is_generated(target):
    head = "".join(line for line in (ROOT / target).read_text(encoding="utf-8").splitlines(keepends=True)
                   if line.startswith("#"))
    assert "python3 scripts/compile_locks.py" in head
    assert f"pip install --require-hashes -r {target}\n" in head
    assert "Do not edit" in head


def test_the_pip_wheel_and_the_pip_pin_are_one_pip():
    """The wheel URL is typed in four files and `pip==` in two inputs. The
    wheel is installed first everywhere, so a lock that pinned another pip
    would replace it on every install."""
    wheels = {rel: pip_installs.wheels(rel) for rel in pip_installs.build_files()}
    typed = {wheel for found in wheels.values() for wheel in found}
    assert len(typed) == 1, f"more than one pip wheel is installed: {wheels}"
    for rel in (".github/workflows/ci.yml", "Makefile", "Dockerfile"):
        assert wheels[rel], f"{rel} no longer installs the pinned pip wheel"
    version, sha256 = next(iter(typed))
    for source, target in (("requirements-ci.in", "requirements-ci.txt"),
                           ("requirements-piptools.in", "requirements-piptools.txt")):
        assert cl.parse_input(source)["pip"] == version, source
        assert f"sha256:{sha256}" in _lock(target)["pip"].hashes, (
            f"{target} does not list the hash of the wheel every install starts from")


def test_ci_checks_the_lock_with_the_compiler_the_lock_pins():
    yaml = require("yaml", "PyYAML parses the workflow")
    workflow = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8"))
    owners = [job for job in workflow["jobs"].values()
              if any(step.get("run") == "python3 scripts/compile_locks.py --check" for step in job["steps"])]
    assert len(owners) == 1, "exactly one job runs the stale-lock check"
    job = owners[0]
    assert job.get("continue-on-error") is not True, "a stale lock must fail the workflow"
    runs = "\n".join(str(step.get("run") or "") for step in job["steps"])
    assert "pip install --require-hashes -r requirements-piptools.txt" in runs
    assert "pip install --require-hashes -r requirements.lock" in runs, (
        "nothing else runs the production install command")
    for step in job["steps"]:
        assert step.get("continue-on-error") is not True


def test_the_audit_reads_the_pins_as_written():
    """Left to resolve the lock itself, pip-audit builds a scratch
    environment and reports what pip would install INTO it. A pin that
    environment already satisfies is not installed, so it is not reported,
    and the two a fresh environment always holds are pip and setuptools.
    `--disable-pip --require-hashes` reads each `name==version` from the file.

    Measured 2026-11-19 with pip-audit 2.9.0: in this mode a file pinning
    setuptools 79.0.1 is reported with two vulnerabilities, and the lock,
    which pins 84.2.1, with none.
    """
    yaml = require("yaml", "PyYAML parses the workflow")
    workflow = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8"))
    audits = [str(step.get("run")) for job in workflow["jobs"].values() for step in job["steps"]
              if "pip-audit" in str(step.get("run") or "")]
    assert audits == ["pip-audit -r requirements.lock --disable-pip --require-hashes"], audits
    assert "setuptools" in _lock("requirements.lock"), "the pin that mode exists to reach"


_AUDIT = re.compile(r"pip-audit\s+-r\s+(\S+)(.*)")


def _audit_commands(rel: str) -> list[tuple[str, str]]:
    """(file audited, the rest of the command) for every `pip-audit -r` in a file, comments dropped."""
    lines = [line for line in (ROOT / rel).read_text(encoding="utf-8").splitlines()
             if not line.lstrip().startswith("#")]
    return [(found.group(1), found.group(2).strip()) for found in map(_AUDIT.search, lines) if found]


def test_every_audit_of_a_lock_reads_it_the_same_way():
    """The pull-request gate, the nightly scan and the GitLab pipeline audit
    the same files. One of them resolving while the others read would be two
    answers to "is this lock clean" from one lock."""
    wanted = {
        ".github/workflows/ci.yml": ["requirements.lock"],
        ".github/workflows/nightly-audit.yml": ["requirements.lock", "requirements-ci.txt"],
        ".gitlab-ci.yml": ["requirements.lock"],
    }
    for rel, files in wanted.items():
        found = _audit_commands(rel)
        assert [name for name, _ in found] == files, f"{rel} audits {found}"
        for name, rest in found:
            assert rest == "--disable-pip --require-hashes", f"{rel}: pip-audit -r {name} {rest}"
            assert name in pip_installs.HASHED, f"{rel} audits {name}, which is not a generated lock"


def test_the_auditor_is_installed_from_the_lock_that_pins_it():
    """pip-audit was the last thing in the workflows installed by version
    alone. The nightly scan now takes it from requirements-ci.txt, so the pin
    it runs is the pin the pull-request gate runs."""
    assert _lock("requirements-ci.txt")["pip-audit"].version == "2.9.0"
    nightly = pip_installs.installs(".github/workflows/nightly-audit.yml")
    assert ("--require-hashes -r requirements-ci.txt", "locked") in nightly, nightly


def test_what_the_image_base_bundles_comes_from_a_hash_checked_source():
    """python:3.11-slim ships pip, setuptools and wheel. The production stage
    copies the builder's prefix over the base, so whatever the base bundles
    and the Dockerfile does not remove first is in the image twice, or is in
    the image and in no lock. The image job found `wheel 0.46.3` that way on
    2026-11-19.

    A scan, because no test can run a Docker build; the image job in ci.yml
    drives the behaviour (`compile_locks.py --installed` inside the image).
    """
    lines = [line.strip() for line in (ROOT / "Dockerfile").read_text(encoding="utf-8").splitlines()
             if not line.lstrip().startswith("#")]
    removal = [line for line in lines if "pip uninstall" in line]
    assert len(removal) == 1, removal
    removed = set(removal[0].split("pip uninstall", 1)[1].replace("-y", " ").split())
    assert removed == {"pip", "setuptools", "wheel"}, removed
    copy = [line for line in lines if line.startswith("COPY --from=builder /install")]
    assert len(copy) == 1 and lines.index(removal[0]) < lines.index(copy[0]), (
        "the base's copies must be removed before the prefix is copied over them")
    # Each one removed comes back from somewhere that checks a hash: pip from
    # the wheel URL installed into the prefix, the others from the lock.
    lock = _lock("requirements.lock")
    assert lock["wheel"].version == "0.46.3"
    for name in removed - {"pip"}:
        assert name in lock, f"the Dockerfile removes {name} and requirements.lock does not pin it"
    kinds = [kind for _, kind in pip_installs.installs("Dockerfile")]
    assert kinds == ["wheel", "wheel", "locked"], "the second wheel line is the pip that goes into the prefix"


# ── 3. the check says what it could not check ─────────────────────────────

_A, _B, _C = "a" * 64, "b" * 64, "c" * 64
_FRESH = f"""# header
alpha==1.0 \\
    --hash=sha256:{_A} \\
    --hash=sha256:{_B}
    # via -r demo.in
beta==2.0 ; sys_platform != "win32" \\
    --hash=sha256:{_C}
    # via
    #   -r demo.in
    #   alpha

# The following packages are considered to be unsafe in a requirements file:
gamma==3.0 \\
    --hash=sha256:{_A}
    # via alpha
"""
_DECLARED = {"alpha": "1.0", "beta": "2.0", "gamma": "3.0"}


def test_the_parser_reads_a_compiled_file():
    pins = cl.parse_lock(_FRESH)
    assert set(pins) == {"alpha", "beta", "gamma"}
    assert pins["alpha"].hashes == {f"sha256:{_A}", f"sha256:{_B}"}
    assert pins["beta"].marker == 'sys_platform != "win32"'
    assert pins["beta"].via == ("-r demo.in", "alpha")
    assert pins["gamma"].via == ("alpha",)


def _kinds(committed: str, fresh: str = _FRESH, declared: dict = _DECLARED) -> list[str]:
    return [finding.kind for finding in cl.compare("demo.txt", committed, fresh, declared)]


def test_compare_names_each_way_a_lock_goes_stale():
    assert _kinds(_FRESH) == [], "identical text must find nothing"
    assert _kinds(_FRESH.replace("alpha==1.0", "alpha==0.9")) == ["pin"]
    assert _kinds(_FRESH.replace(f"    --hash=sha256:{_B}\n", f"    --hash=sha256:{'d' * 64}\n")) == [
        "hash-withdrawn", "hash-added"]
    # Upstream published one more file for alpha 1.0: the committed lock is
    # the fresh one minus a hash. No version moved, and the kind says so.
    shorter = _FRESH.replace(f" \\\n    --hash=sha256:{_B}", "")
    assert _kinds(shorter) == ["hash-added"]
    # The other direction: a hash the lock trusts is no longer published.
    assert _kinds(_FRESH, fresh=shorter) == ["hash-withdrawn"]
    assert _kinds(_FRESH.replace("# header", "# another header")) == ["text"]
    assert _kinds("") == ["pin", "pin", "pin"], "a missing lock is three pins not committed"


def test_a_package_no_input_pins_is_refused_with_the_line_to_add():
    findings = cl.compare("demo.txt", _FRESH, _FRESH, {"alpha": "1.0", "beta": "2.0"})
    assert [finding.kind for finding in findings] == ["unpinned"]
    assert "`gamma==3.0`" in findings[0].detail


def _fake_compile(monkeypatch, result):
    monkeypatch.setattr(cl, "environment_refusal", lambda root=cl.ROOT: None)

    def compile_all(root=cl.ROOT):
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(cl, "compile_all", compile_all)


def _committed() -> dict:
    return {target: (ROOT / target).read_text(encoding="utf-8") for _, target in LOCKS}


def test_check_passes_on_the_committed_locks_and_fails_on_a_moved_hash(monkeypatch, capsys):
    _fake_compile(monkeypatch, _committed())
    assert cl.check() == cl.OK

    moved = _committed()
    one = next(iter(sorted(_lock("requirements.lock")["ccxt"].hashes)))
    moved["requirements.lock"] = moved["requirements.lock"].replace(one, "sha256:" + "0" * 64)
    _fake_compile(monkeypatch, moved)
    assert cl.check() == cl.STALE
    out = capsys.readouterr().out
    assert "STALE requirements.lock [hash-withdrawn] ccxt==4.5.56" in out
    assert "STALE requirements.lock [hash-added] ccxt==4.5.56" in out


def test_check_cannot_check_is_two_and_never_zero(monkeypatch, capsys):
    # The interpreter cannot produce the files.
    monkeypatch.setattr(cl, "environment_refusal", lambda root=cl.ROOT: "this is not the pinned pip-tools")
    assert cl.check() == cl.CANNOT_CHECK
    assert "COULD NOT CHECK" in capsys.readouterr().err

    # The index could not be reached.
    _fake_compile(monkeypatch, cl.CompileFailure("requirements.in", "ReadTimeout: HTTPSConnectionPool"))
    assert cl.check() == cl.CANNOT_CHECK
    assert "COULD NOT CHECK" in capsys.readouterr().err

    # pip-compile ran and refused: that is a failure, not an absence.
    _fake_compile(monkeypatch, cl.CompileFailure(
        "requirements.in", "ResolutionImpossible: ccxt 4.5.56 depends on setuptools>=60.9.0"))
    assert cl.check() == cl.STALE
    assert "COULD NOT CHECK" not in capsys.readouterr().err


def test_write_refuses_to_put_an_unpinned_package_in_a_lock(monkeypatch, tmp_path, capsys):
    """In a copy of the inputs, never in the repository. The first version of
    this test called `write()` on the real tree and trusted the refusal it
    was testing: with `unpinned()` mutated to find nothing, the refusal did
    not happen and the test wrote `surprise==9.9` into requirements.lock."""
    for source, _ in LOCKS:
        (tmp_path / source).write_text((ROOT / source).read_text(encoding="utf-8"), encoding="utf-8")
    compiled = _committed()
    compiled["requirements.lock"] += f"surprise==9.9 \\\n    --hash=sha256:{_A}\n    # via ccxt\n"
    _fake_compile(monkeypatch, compiled)
    assert cl.write(tmp_path) == cl.STALE
    assert "surprise==9.9" in capsys.readouterr().err
    written = sorted(path.name for path in tmp_path.iterdir() if path.name in dict(LOCKS).values())
    assert written == [], f"a refused compile must write nothing, and it wrote {written}"


def test_write_writes_every_lock_when_the_inputs_pin_everything(monkeypatch, tmp_path):
    """The other arm, in the same copy: a compile the inputs fully pin is written."""
    for source, _ in LOCKS:
        (tmp_path / source).write_text((ROOT / source).read_text(encoding="utf-8"), encoding="utf-8")
    _fake_compile(monkeypatch, _committed())
    assert cl.write(tmp_path) == cl.OK
    for _, target in LOCKS:
        assert (tmp_path / target).read_text(encoding="utf-8") == (ROOT / target).read_text(encoding="utf-8")


def test_the_environment_refusal_names_what_is_wrong(monkeypatch, tmp_path):
    for name in (".python-version", cl.TOOLS_INPUT):
        (tmp_path / name).write_text((ROOT / name).read_text(encoding="utf-8"), encoding="utf-8")

    monkeypatch.setattr(cl.sys, "platform", "darwin")
    assert "Linux" in cl.environment_refusal(tmp_path)
    monkeypatch.setattr(cl.sys, "platform", "linux")

    (tmp_path / ".python-version").write_text("2.7\n", encoding="utf-8")
    assert "Python 2.7" in cl.environment_refusal(tmp_path)
    (tmp_path / ".python-version").write_text("%d.%d\n" % cl.sys.version_info[:2], encoding="utf-8")

    def missing(dist):
        raise importlib.metadata.PackageNotFoundError(dist)

    monkeypatch.setattr(cl.importlib.metadata, "version", missing)
    assert "pip-tools is not installed" in cl.environment_refusal(tmp_path)

    monkeypatch.setattr(cl.importlib.metadata, "version", lambda dist: "0.0.1")
    assert "pip-tools 0.0.1 is running" in cl.environment_refusal(tmp_path)

    pinned = cl.parse_input(cl.TOOLS_INPUT)
    monkeypatch.setattr(cl.importlib.metadata, "version", lambda dist: pinned[dist])
    assert cl.environment_refusal(tmp_path) is None, "the pinned tools on the pinned Python may compile"


def test_a_mistyped_flag_is_not_reported_as_could_not_check():
    with pytest.raises(SystemExit) as stop:
        cl.main(["--chekc"])
    assert stop.value.code == cl.USAGE
    assert cl.USAGE not in (cl.OK, cl.STALE, cl.CANNOT_CHECK)


# ── --installed: an environment against a lock ────────────────────────────

def test_installed_differences_have_three_outcomes_and_no_fourth():
    pins = cl.parse_lock(_FRESH)

    wrong, conditional, unlisted = cl.differences(pins, {"alpha": ["1.0"], "beta": ["2.0"], "gamma": ["3.0"]})
    assert (wrong, conditional, unlisted) == ([], [], [])

    # A pin at another version, and a pin that is not there.
    wrong, _, _ = cl.differences(pins, {"alpha": ["0.9"], "beta": ["2.0"]})
    assert len(wrong) == 2 and "alpha" in wrong[0] and "0.9" in wrong[0] and "gamma" in wrong[1]

    # A conditional pin that is absent is reported apart: its marker may
    # explain it, and this function does not evaluate markers.
    wrong, conditional, _ = cl.differences(pins, {"alpha": ["1.0"], "gamma": ["3.0"]})
    assert wrong == [] and len(conditional) == 1 and "beta" in conditional[0]

    # Two versions of one distribution: what copying one site-packages over
    # another leaves. A difference whether the lock names the package or not.
    wrong, _, unlisted = cl.differences(
        pins, {"alpha": ["1.0"], "beta": ["2.0"], "gamma": ["3.0", "2.9"], "pip": ["26.2.2", "24.0"], "wheel": ["1"]})
    assert len(wrong) == 2 and "gamma" in wrong[0] and "pip" in wrong[1]
    assert unlisted == ["wheel 1"]


def test_installed_reads_this_interpreter(tmp_path, capsys):
    """Driven, not stubbed: a lock made from what is really installed passes,
    and the same lock with one version moved does not."""
    name, versions = next((name, versions) for name, versions in sorted(cl.installed_versions().items())
                          if name == "pytest")
    lock = tmp_path / "here.txt"
    lock.write_text(f"{name}=={versions[0]} \\\n    --hash=sha256:{_A}\n", encoding="utf-8")
    assert cl.installed(str(lock)) == cl.OK

    lock.write_text(f"{name}==0.0.1 \\\n    --hash=sha256:{_A}\n", encoding="utf-8")
    assert cl.installed(str(lock)) == cl.STALE
    assert "DIFFERS" in capsys.readouterr().out

    assert cl.installed(str(tmp_path / "absent.txt")) == cl.CANNOT_CHECK
    (tmp_path / "empty.txt").write_text("# nothing\n", encoding="utf-8")
    assert cl.installed(str(tmp_path / "empty.txt")) == cl.CANNOT_CHECK
