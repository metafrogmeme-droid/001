"""`/users` said two. Nothing had been deleted.

    DB_PATH = Path(os.getenv("DB_PATH", "data/runeclaw.db"))
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)

A relative path is resolved against the process's WORKING DIRECTORY, so which
database the bot opened was decided by whoever launched it. `deploy.sh`
symlinks ./data at the repo root to a persistent store, so a bot started from
the repo found the real users and a bot started from anywhere else — $HOME, /,
a unit file with no WorkingDirectory — found nothing.

AND IT DID NOT FAIL. `mkdir(exist_ok=True)` and `sqlite3.connect` both create,
so the wrong directory produced a brand-new empty database in silence, and
`/users` counted it and printed a total with complete confidence. This
repository's central rule, broken at the storage layer: absent rendered as a
measurement, with no way for any reader above it to tell.

THE RECOVERY PATH WENT WITH IT. `bot/utils/backup.py` took `root: str = "."`,
so the same wrong-cwd process found none of the ten critical files, wrote a
valid archive with an empty manifest into a directory nobody looks in, and
returned success. Per-file absence was recorded honestly and always had been —
what was missing is that ALL-absent is not a backup.

SIX MODULES HAD THE SHAPE, not one, so the anchor is a module rather than a
`Path(__file__).resolve().parents[2]` in each. Every such copy is a hardcoded
count of how deep that particular file sits; all six happen to want 2 today,
and the first module that moves gets a root pointing at `bot/` and re-creates
the bug somewhere nobody is looking.

`test_no_module_resolves_durable_state_against_the_cwd` is the one that matters
— it is the re-run of the search that found the other five, and it fails on the
seventh.
"""

from __future__ import annotations

import os
import pathlib
import re

import pytest

from bot.utils.paths import REPO_ROOT, env_state_path, state_path

ROOT = pathlib.Path(__file__).resolve().parent.parent


# ── the anchor itself ───────────────────────────────────────────────────────

def test_the_repo_root_is_the_repo_root():
    """`bot/utils/paths.py` → parents[2]. If this module moves, everything
    anchored to it moves with it, so the depth is asserted rather than
    assumed."""
    assert REPO_ROOT == ROOT
    assert (REPO_ROOT / "bot" / "utils" / "paths.py").is_file()


def test_a_relative_state_path_is_anchored(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert state_path("data/x.json") == ROOT / "data" / "x.json"


def test_an_absolute_state_path_is_honoured_as_given(tmp_path):
    assert state_path(str(tmp_path / "x.json")) == tmp_path / "x.json"


def test_the_working_directory_cannot_move_it(tmp_path, monkeypatch):
    """THE PROPERTY. Same answer from anywhere — that is the whole fix."""
    here = state_path("data/runeclaw.db")
    monkeypatch.chdir(tmp_path)
    assert state_path("data/runeclaw.db") == here
    monkeypatch.chdir("/")
    assert state_path("data/runeclaw.db") == here


def test_a_relative_env_override_is_anchored_too(monkeypatch, tmp_path):
    """An operator who writes `DB_PATH=data/x.db` is naming a file, not asking
    for "wherever this process happens to be standing" — the override had
    exactly the failure the default had."""
    monkeypatch.setenv("SOME_PATH", "data/custom.db")
    monkeypatch.chdir(tmp_path)
    assert env_state_path("SOME_PATH", "data/default.db") == ROOT / "data" / "custom.db"


def test_an_absolute_env_override_wins(monkeypatch, tmp_path):
    monkeypatch.setenv("SOME_PATH", str(tmp_path / "real.db"))
    assert env_state_path("SOME_PATH", "data/default.db") == tmp_path / "real.db"


def test_a_blank_env_override_is_not_a_path(monkeypatch):
    """`DB_PATH=` in a .env is empty, and `Path("")` is `Path(".")` — truthy,
    and a directory. It must read as unset."""
    monkeypatch.setenv("SOME_PATH", "   ")
    assert env_state_path("SOME_PATH", "data/default.db") == ROOT / "data" / "default.db"


# ── every durable path, driven ──────────────────────────────────────────────

@pytest.mark.parametrize("get", [
    pytest.param(lambda: __import__("bot.db.models", fromlist=["x"]).DB_PATH, id="db"),
    pytest.param(lambda: __import__("bot.utils.backup", fromlist=["x"])._backup_dir(), id="backups"),
    pytest.param(lambda: __import__("bot.utils.backup", fromlist=["x"]).rootp_of(), id="backup-root"),
    pytest.param(lambda: __import__("bot.web.chat_quota", fromlist=["x"])._STORE_PATH, id="chat-quota"),
    pytest.param(lambda: pathlib.Path(__import__("bot.core.engine", fromlist=["x"])._of_snapshot_path()),
                 id="order-flow-snapshots"),
    pytest.param(lambda: __import__("bot.marketing.channel_forwarder", fromlist=["x"])._CONFIG_PATH,
                 id="channel-config"),
])
def test_every_durable_path_is_absolute_and_under_the_repo(get):
    p = get()
    assert p.is_absolute(), f"{p} is relative — the cwd decides where it lands"
    assert str(p).startswith(str(ROOT)), p


def test_the_order_flow_reader_and_writer_agree():
    """One definition for both ends. The reader (warm_oi_history) and the
    writer (record_snapshot) each spelled out the same relative default, so
    they agreed only while every process was launched from the same place —
    and a writer and reader disagreeing looks exactly like a cold start,
    forever."""
    from tests.source_scan import code_only

    src = code_only((ROOT / "bot" / "core" / "engine.py").read_text(encoding="utf-8"))
    assert src.count('"data/learning/order_flow_snapshots.jsonl"') == 1, (
        "the snapshot path is spelled out at more than one site again")
    # One definition plus both call sites. Counting only the calls means
    # guessing whether the `def` line matches, which is how a test ends up
    # asserting its own formatting.
    assert src.count("_of_snapshot_path()") >= 3, (
        "a call site stopped going through the shared definition")


# ── a new database is a loud event ──────────────────────────────────────────

def test_a_fresh_database_is_reported_as_fresh(tmp_path, monkeypatch, caplog):
    """CREATING a database and OPENING one are different events, and both were
    silent."""
    import logging

    import bot.db.models as m
    monkeypatch.setattr(m, "DB_PATH", tmp_path / "new.db")
    monkeypatch.setattr(m, "_db_existed", {})
    with caplog.at_level(logging.WARNING, logger="bot.db.models"):
        m.init_db()
    assert any("CREATING A NEW DATABASE" in r.message for r in caplog.records), (
        "a brand-new database was created without a word")


def test_an_existing_database_is_not_reported_as_fresh(tmp_path, monkeypatch, caplog):
    """CONTROL. Every normal restart must be quiet, or the warning is noise
    and gets ignored on the one run that matters."""
    import logging

    import bot.db.models as m
    db = tmp_path / "existing.db"
    monkeypatch.setattr(m, "DB_PATH", db)
    monkeypatch.setattr(m, "_db_existed", {})
    m.init_db()                                   # creates it
    monkeypatch.setattr(m, "_db_existed", {})     # a "restart"
    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="bot.db.models"):
        m.init_db()
    assert not any("CREATING A NEW DATABASE" in r.message for r in caplog.records)


def test_freshness_is_answered_per_database_not_once_per_process(tmp_path, monkeypatch):
    """Tests and the api_bridge both point DB_PATH elsewhere. "Did THIS
    database exist" has to be about the file in use."""
    import bot.db.models as m
    monkeypatch.setattr(m, "_db_existed", {})
    monkeypatch.setattr(m, "DB_PATH", tmp_path / "a.db")
    assert m.database_is_new() is True
    (tmp_path / "b.db").write_text("")
    monkeypatch.setattr(m, "DB_PATH", tmp_path / "b.db")
    assert m.database_is_new() is False


def test_freshness_is_recorded_before_anything_creates_the_file():
    """The order is load-bearing: `mkdir` and `sqlite3.connect` both create, so
    the question has to be asked before either runs."""
    from tests.source_scan import code_only

    src = code_only((ROOT / "bot" / "db" / "models.py").read_text(encoding="utf-8"))
    assert src.index("DB_EXISTED_AT_STARTUP") < src.index("DB_PATH.parent.mkdir"), (
        "the existence check now runs after the directory is created")


# ── an empty backup is not a backup ─────────────────────────────────────────

def test_a_backup_that_captured_nothing_says_so(tmp_path, monkeypatch, caplog):
    import logging

    import bot.utils.backup as b
    monkeypatch.setenv("BACKUP_DIR", str(tmp_path / "out"))
    with caplog.at_level(logging.ERROR, logger="bot.utils.backup"):
        archive, manifest = b.create_backup(root=str(tmp_path / "empty"))
    assert manifest["files"] == {}
    assert any("CAPTURED NOTHING" in r.message for r in caplog.records), (
        "an archive with nothing in it was returned as a success")


def test_a_real_backup_stays_quiet(tmp_path, monkeypatch, caplog):
    """CONTROL."""
    import logging

    import bot.utils.backup as b
    src = tmp_path / "src" / "data"
    src.mkdir(parents=True)
    (src / "runeclaw.db").write_text("x")
    monkeypatch.setenv("BACKUP_DIR", str(tmp_path / "out"))
    with caplog.at_level(logging.ERROR, logger="bot.utils.backup"):
        _, manifest = b.create_backup(root=str(tmp_path / "src"))
    assert manifest["files"]
    assert not any("CAPTURED NOTHING" in r.message for r in caplog.records)


def test_the_backup_root_no_longer_defaults_to_the_cwd():
    from tests.source_scan import code_only

    src = code_only((ROOT / "bot" / "utils" / "backup.py").read_text(encoding="utf-8"))
    assert 'root: str = "."' not in src, (
        "the critical set is resolved against the working directory again — a "
        "wrong-cwd process backs up nothing and reports success")


# ── the guard that finds the next one ───────────────────────────────────────

#: Module-level bindings that name a durable file without anchoring it.
_UNANCHORED = re.compile(
    r"""(?:os\.getenv|os\.environ\.get)\(\s*["'][A-Z_]+["']\s*,\s*["'](?:data|logs)/"""
    r"""|(?<![\w.])Path\(\s*["'](?:data|logs)/""",
    re.VERBOSE)


def test_no_module_resolves_durable_state_against_the_cwd():
    """THE RE-RUN OF THE SEARCH.

    The database was found because users vanished. Grepping for the SHAPE
    afterwards found five more modules with it — the free-chat quota (every
    user's counter silently back to zero), the order-flow snapshot history
    (reader and writer able to disagree about where it lives), the channel
    config, and the lab output directory. None of them had lost anything yet.

    The grep tells you where you looked; this test tells you where you didn't.
    """
    from tests.source_scan import code_only

    offenders = []
    for path in sorted((ROOT / "bot").rglob("*.py")):
        src = code_only(path.read_text(encoding="utf-8"))
        for m in _UNANCHORED.finditer(src):
            line = src[:m.start()].count("\n") + 1
            offenders.append(f"{path.relative_to(ROOT)}:{line}  {m.group(0)!r}")
    assert not offenders, (
        "durable state is being resolved against the working directory:\n  "
        + "\n  ".join(offenders)
        + "\n\nUse bot.utils.paths.state_path / env_state_path. A relative "
          "path here means whoever launches the process decides which file it "
          "reads, and a missing file is CREATED rather than reported.")
