"""Which file a process reads must not depend on who launched it.

    DB_PATH = Path(os.getenv("DB_PATH", "data/runeclaw.db"))
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)

A relative path is resolved against the process's WORKING DIRECTORY. And it
does not fail: `mkdir(exist_ok=True)` and `sqlite3.connect` both CREATE, so a
wrong directory produces a brand-new empty store in silence and every surface
above counts it and prints a total. This repository's central rule broken at
the storage layer — absent rendered as a measurement, with no way for any
reader above it to tell.

WHAT THIS DID AND DID NOT EXPLAIN. It was written on 2026-08-19 while chasing
`/users` reporting two accounts where there had been eighteen, and the evidence
came back against it: that box's cwd was the repo root and its data/ symlink
was intact. The relative paths were real, latent, and not the cause. The
docstring is corrected rather than deleted because a test that narrates a
diagnosis it has outgrown is worse than one that narrates none — and the fix
stands on its own: it removes a way for the same symptom to be produced.

THE RECOVERY PATH HAD IT TOO. `bot/utils/backup.py` took `root: str = "."`, so
a wrong-cwd process found none of the ten critical files, wrote a valid archive
with an empty manifest, and returned success. Per-file absence was recorded
honestly and always had been; what was missing is that ALL-absent is not a
backup.

THE SEARCH TOOK THREE PASSES AND THE GUARD IS THE ONLY REASON IT FINISHED.
A grep for the shape found six modules. The first version of this test found
six MORE it could not reach, because they wrap the call across lines. And it
still missed the one that mattered — `UserStore(path="data/users.json")`, a
parameter DEFAULT, on the file holding every registered user — because the
pattern only looked at module level.

A checker with a blind spot manufactures exactly the reassurance it exists to
prevent. So the pattern no longer enumerates shapes: every literal is a
finding, and `tests/durable_path_baseline.txt` makes each exemption argue for
itself.
"""

from __future__ import annotations

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




@pytest.mark.parametrize("build", [
    pytest.param(lambda: __import__("bot.utils.user_store", fromlist=["x"])
                 .UserStore("data/users.json")._path, id="user-store"),
    pytest.param(lambda: __import__("bot.utils.audit_chain", fromlist=["x"])
                 .AuditChain("logs/audit_chain.jsonl")._path, id="audit-chain"),
    pytest.param(lambda: __import__("bot.core.trade_journal", fromlist=["x"])
                 .TradeJournal("data/trade_journal.json")._journal_file, id="trade-journal"),
    pytest.param(lambda: __import__("bot.proofofpnl.publish", fromlist=["x"])
                 .PublicationStore("data/p.json")._path, id="pnl-publications"),
    pytest.param(lambda: __import__("bot.proofofpnl.seasons", fromlist=["x"])
                 .SeasonStore("data/s.json")._path, id="pnl-seasons"),
    pytest.param(lambda: __import__("bot.proofofpnl.leaderboard", fromlist=["x"])
                 .LeaderboardRegistry("data/l.json")._path, id="pnl-leaderboard"),
])
def test_the_owning_classes_anchor_a_relative_argument(build):
    """DRIVEN, because a scan could not have caught what went wrong here.

    The three Proof-of-PnL stores were edited to call `state_path(path)` and the
    import was silently skipped — the helper checked `"state_path" not in line`
    against a line already reading `import env_state_path`, and a substring
    match said it was there. Every source scan passed. ruff's F821 caught it,
    and constructing the object catches it without waiting for a linter.
    """
    p = pathlib.Path(str(build()))
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

#: ANY `"data/…"` or `"logs/…"` literal, wherever it appears.
#:
#: THE FIRST VERSION OF THIS GUARD MATCHED ONLY MODULE-LEVEL BINDINGS —
#: `os.getenv("X", "data/…")` and `Path("data/…")` — and reported the tree
#: clean while `UserStore.__init__(self, path="data/users.json")` sat
#: unanchored. That is the file holding every registered user, and it is the
#: one that actually went missing; the guard written to catch this class of
#: bug could not see the instance that prompted it.
#:
#: A checker with a blind spot manufactures exactly the reassurance it exists
#: to prevent — which this repository already learned once, when the
#: reachability sweep scanned only `bot/` and `scripts/` and declared a
#: mounted module dead.
#:
#: So the shape is no longer enumerated. Every literal is a finding unless it
#: is an argument to the anchoring helpers (stripped by `_ANCHORED`) or is
#: listed, with a reason, in the baseline.
_LITERAL = re.compile(r'["\'](?:data|logs)/[^"\']*["\']')
#: `[frbu]*` and the `\s*` before it are load-bearing: the wrapped value is
#: often an f-string, and often on the next line. Without them a correctly
#: anchored path is reported as a defect, and the fix for that false positive
#: is to stop anchoring — the guard would be arguing against itself.
_ANCHORED = re.compile(
    r'(?:env_)?state_path\(\s*(?:["\'][A-Z_]+["\']\s*,\s*)?[frbFRB]*["\'](?:data|logs)/[^"\']*["\']')

BASELINE = ROOT / "tests" / "durable_path_baseline.txt"


def _baseline() -> set[tuple[str, str]]:
    out = set()
    for raw in BASELINE.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = [p.strip() for p in line.split("::")]
        assert len(parts) == 3, f"malformed baseline row: {raw}"
        assert parts[2], f"baseline row with no reason: {raw}"
        out.add((parts[0], parts[1]))
    return out


def _found() -> set[tuple[str, str]]:
    from tests.source_scan import code_only

    found = set()
    for path in sorted((ROOT / "bot").rglob("*.py")):
        src = _ANCHORED.sub("", code_only(path.read_text(encoding="utf-8")))
        for m in _LITERAL.finditer(src):
            found.add((str(path.relative_to(ROOT)), m.group(0)))
    return found


def test_no_module_resolves_durable_state_against_the_cwd():
    """THE RE-RUN OF THE SEARCH.

    The database was found because users vanished. Grepping for the shape
    found six more modules. The FIRST version of this test then found six more
    that the grep could not reach, because they wrap the call across lines —
    the Guardian review queue, the user authority store, all three Proof-of-PnL
    stores and the web live ledger.

    And it still missed the one that mattered, because it only looked at
    module level. `UserStore(path="data/users.json")` is a parameter default,
    and that is the file that holds every user.

    The grep tells you where you looked; this test tells you where you didn't
    — but only for the shapes it can see, which is why it now looks at every
    literal and makes the exemptions argue for themselves.
    """
    new = sorted(_found() - _baseline())
    assert not new, (
        "durable state is being resolved against the working directory:\n  "
        + "\n  ".join(f"{f}  {lit}" for f, lit in new)
        + "\n\nUse bot.utils.paths.state_path / env_state_path. A relative "
          "path means whoever launches the process decides which file it "
          "reads, and a missing file is CREATED rather than reported. If the "
          "value IS anchored downstream, add it to tests/"
          "durable_path_baseline.txt with the reason.")


def test_the_baseline_has_no_stale_entries():
    """A ratchet in both directions. An exemption that no longer matches any
    code is an argument nobody can check, and it silently covers the next
    literal that happens to match it — the `known_failures.txt` rule, for the
    same reason."""
    stale = sorted(_baseline() - _found())
    assert not stale, (
        "these baseline entries match nothing any more and must be deleted:\n  "
        + "\n  ".join(f"{f}  {lit}" for f, lit in stale))


def test_the_store_that_actually_holds_the_users_is_anchored():
    """THE ONE THE FIRST GUARD MISSED, pinned by driving it rather than by
    matching source — a scan is what failed here."""
    from bot.utils.user_store import UserStore

    s = UserStore.__new__(UserStore)
    UserStore.__init__(s, "data/users.json")
    assert s._path.is_absolute(), (
        f"the user store resolved to {s._path} — relative, so which roster the "
        "bot reads depends on who launched it")
    assert s._path == ROOT / "data" / "users.json"


# ── a store that starts with nobody in it says so ───────────────────────────

class TestTheUserStoreCannotStartEmptyQuietly:
    """18 accounts became 2, and nothing anywhere said anything.

    The store was NOT corrupt — the quarantine guard never fired, and there is
    no code path in the tree that deletes a user: no `del self._users[...]`, no
    prune, no expiry. What it can do is start empty and then SAVE, and from
    that moment the file on disk legitimately contains only whoever has
    messaged since. `/users` then counts it and prints a total.

    Two routes reach that state and only one was guarded. A file that cannot be
    READ is quarantined and the store refuses to write. A file that is MISSING
    or ZERO-BYTE loads empty and saving is allowed — correctly, because a real
    first boot has to work and there is no file to protect. So the defence
    cannot be refusal; it has to be that it is impossible to miss.
    """

    def _store(self, tmp_path, name, contents=None):
        from bot.utils.user_store import UserStore
        p = tmp_path / name
        if contents is not None:
            p.write_text(contents)
        return UserStore(p)

    def test_a_missing_file_is_announced(self, tmp_path, caplog):
        import logging
        with caplog.at_level(logging.WARNING, logger="runeclaw.user_store"):
            s = self._store(tmp_path, "missing.json")
        assert s.started_empty is True
        assert any("starting EMPTY" in r.message for r in caplog.records)

    def test_a_zero_byte_file_is_announced_too(self, tmp_path, caplog):
        """THE UNGUARDED ROUTE. The comment justifying this branch reasons
        from atomic_write — which only covers writes THIS code made. A
        truncating copy, a failed restore or a disk-full from another writer
        produces a zero-byte file that looks identical to a `touch`."""
        import logging
        with caplog.at_level(logging.WARNING, logger="runeclaw.user_store"):
            s = self._store(tmp_path, "empty.json", "")
        assert s.started_empty is True
        assert any("zero-byte" in r.message for r in caplog.records)

    def test_a_real_store_is_silent(self, tmp_path, caplog):
        """CONTROL. Every normal restart must be quiet, or the warning is noise
        and gets scrolled past on the one boot that matters."""
        import logging
        with caplog.at_level(logging.WARNING, logger="runeclaw.user_store"):
            s = self._store(tmp_path, "real.json", '{"1": {"role": "admin"}}')
        assert s.started_empty is False
        assert not any("starting EMPTY" in r.message for r in caplog.records)

    def test_an_unreadable_store_still_refuses_to_write(self, tmp_path):
        """CONTROL, and the property that must not regress: a store that
        FAILED to read is a different case from one that started empty, and it
        must still quarantine and refuse rather than merely warn."""
        s = self._store(tmp_path, "bad.json", "{not json")
        assert s._load_failed is True
        assert s.started_empty is False, (
            "a failed read is being reported as an empty start, which would "
            "downgrade the refusal-to-write case to a warning")
        assert (tmp_path / "bad.json.corrupt").exists()

    def test_the_users_card_reads_the_store_it_actually_shows(self):
        """The first version of this caveat asked `database_is_new()` — the
        SQLite database, which /users never touches. It could not have fired
        for the incident it was written for."""
        from tests.source_scan import code_only

        src = code_only((ROOT / "bot" / "skills" / "telegram_handler.py")
                        .read_text(encoding="utf-8"))
        i = src.index("fresh_db = \"\"")
        block = src[i:i + 700]
        assert "started_empty" in block, (
            "the /users caveat is watching a store this command does not read")
        assert "database_is_new" not in block
