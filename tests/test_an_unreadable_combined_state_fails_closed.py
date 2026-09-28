"""A combined state file that cannot be read is an unread risk state.

`combined_state.json` is the ONLY current record of the operator's breaker:
every save since the C2-34 migration funnels through `_save_combined_state`,
so the individual files each component loads in `__init__` have been written
by nobody since. `_wire_combined_state_saver` used to catch every failure
around the whole load with *"Combined state corrupt, using individual
files"* and go on -- which restored whatever the breaker read weeks ago,
closed as often as not, over a combined file whose last good write may have
held a trip. Driven on the base tree, a file that will not parse, an EMPTY
file (with no log at all) and a portfolio block that would not load beside a
risk block saying HALTED each booted the engine with the breaker CLOSED.
`RiskEngine._load_state` fails its own corrupt file closed; the combined
file's reader had the opposite rule for the same state one file over.

Now: the two blocks are read independently, an unreadable file opens the
breaker with `_fail_closed_restore`'s own sentence, and the file is moved
aside FIRST -- because `_save_combined_state` copies the current file over
the `.bak` before every write, so the next save would have replaced the one
backup that held the last good state with the unreadable bytes.
"""
import json

import pytest

import bot.core.engine as engine_mod
import bot.risk.risk_engine as risk_mod
from bot.core.engine import RuneClawEngine
from bot.risk.portfolio import PortfolioTracker
from bot.risk.risk_engine import RiskEngine

HALTED = {"circuit_open": True, "consecutive_losses": 4, "last_loss_time": 1.0,
          "circuit_breaker_trips": 2, "circuit_trip_cause": "daily_loss",
          "circuit_trip_day": "2026-09-19"}
CLOSED = dict(HALTED, circuit_open=False, consecutive_losses=0,
              circuit_trip_cause="", circuit_trip_day="")
SECRET_TEXT = "sign=SECRETVALUE9 at /srv/private/path"


def _risk(tmp_path, name="risk_state.json") -> RiskEngine:
    pf = PortfolioTracker(initial_balance=10_000.0,
                          state_file=str(tmp_path / f"pf-{name}"))
    return RiskEngine(pf, state_file=str(tmp_path / name))


def _stand(risk, combined_path):
    class StandPortfolio:
        _persistence_active = False
        _combined_saver = None
        loaded = None

        def _export_state_dict(self):
            return {"balance": 10_000.0}

        def _load_from_state_dict(self, d):
            if "balance" not in d:
                raise ValueError("no balance -- the paper book's own refusal")
            self.loaded = dict(d)

    class Stand:
        _combined_state_file = str(combined_path)
        _wire_combined_state_saver = RuneClawEngine._wire_combined_state_saver
        _save_combined_state = RuneClawEngine._save_combined_state
        _load_combined_blocks = RuneClawEngine._load_combined_blocks
        _combined_state_unreadable = RuneClawEngine._combined_state_unreadable

        def __init__(self):
            self.portfolio = StandPortfolio()
            self.risk = risk
    return Stand()


def _boot(tmp_path, content, *, name="risk_state.json"):
    combined = tmp_path / "combined_state.json"
    if content is not None:
        combined.write_text(content)
    r = _risk(tmp_path, name)
    assert r._circuit_open is False, "the individual file left the breaker closed"
    st = _stand(r, combined)
    st._wire_combined_state_saver()
    return r, st, combined


class _Said:
    """What reached the operator: `system_log` lines (rendered) and the risk
    engine's audits, by result word. Both loggers set `propagate = False`, so
    the harness's log capture sees neither; they are recorded at the module
    names the code reads."""

    def __init__(self):
        self.lines: list[tuple[str, str]] = []     # (level, rendered message)
        self.audits: list[tuple[str, str]] = []    # (message, result)

    def _log(self, level):
        def _f(msg, *args, **kw):
            self.lines.append((level, (msg % args) if args else str(msg)))
        return _f

    @property
    def text(self) -> str:
        return " ".join(m for _lvl, m in self.lines)

    @property
    def errors(self) -> list[str]:
        return [m for lvl, m in self.lines if lvl == "error"]

    @property
    def results(self) -> list[str]:
        return [r for _m, r in self.audits]


@pytest.fixture
def said(monkeypatch) -> _Said:
    rec = _Said()
    log = type("Log", (), {lvl: staticmethod(rec._log(lvl))
                           for lvl in ("debug", "info", "warning", "error", "critical")})()
    monkeypatch.setattr(engine_mod, "system_log", log)
    monkeypatch.setattr(risk_mod, "audit",
                        lambda _log, msg, **kw: rec.audits.append((msg, kw.get("result", ""))))
    return rec


@pytest.mark.parametrize("content,word", [
    ("{not json", "will not parse"),
    ("", "is empty"),
    ("   \n", "is empty"),
    ("[1, 2, 3]", "is not a state object"),
])
def test_a_file_that_cannot_be_read_opens_the_breaker(tmp_path, content, word, said):
    r, st, combined = _boot(tmp_path, content)
    assert r._circuit_open is True, "an unread combined file left the breaker closed"
    assert r._circuit_trip_cause == "state_unreadable"
    assert st.portfolio.loaded is None
    assert word in said.text and word in said.audits[0][0], (said.text, said.audits)
    assert said.results == ["COMBINED_FILE_FAIL_CLOSED"], said.results
    assert said.errors, "an unread combined file was not said at ERROR"


def test_the_unreadable_file_is_moved_aside_before_anything_writes(tmp_path):
    r, st, combined = _boot(tmp_path, "{not json")
    damaged = tmp_path / "combined_state.json.corrupt"
    assert not combined.exists() and damaged.read_text() == "{not json"
    # and the next save cannot copy the unreadable bytes over the backup
    bak = tmp_path / "combined_state.json.bak"
    bak.write_text('{"version": 1, "portfolio": {"balance": 1}, "risk": %s}' % json.dumps(HALTED))
    good = bak.read_text()
    st._save_combined_state()
    assert bak.read_text() == good, "the save copied the corrupt file over the last good backup"
    assert json.loads(combined.read_text())["risk"]["circuit_open"] is True


def test_only_the_first_rescue_is_kept(tmp_path, said):
    _boot(tmp_path, "{first")
    r2, st2, combined = _boot(tmp_path, "{second", name="r2.json")
    damaged = tmp_path / "combined_state.json.corrupt"
    assert damaged.read_text() == "{first", "a second failure overwrote the good copy"
    assert combined.exists() and combined.read_text() == "{second"
    assert r2._circuit_open is True
    assert "earlier" in said.text and "earlier" in said.audits[-1][0], said.text


def test_a_file_that_raises_on_read_is_a_failed_read_too(tmp_path, said):
    combined = tmp_path / "combined_state.json"
    combined.mkdir()                     # exists, and read_text raises IsADirectoryError
    r = _risk(tmp_path)
    _stand(r, combined)._wire_combined_state_saver()
    assert r._circuit_open is True
    assert "could not be read" in said.text and "IsADirectoryError" in said.text, said.text
    assert said.results == ["COMBINED_FILE_FAIL_CLOSED"]


def test_an_unreadable_portfolio_block_does_not_cost_the_risk_block_its_read(tmp_path, said):
    r, st, combined = _boot(tmp_path, json.dumps({"version": 1, "portfolio": {}, "risk": HALTED}))
    assert r._circuit_open is True and r._circuit_trip_cause == "daily_loss", \
        "the halt the file held was not restored"
    assert r._consecutive_losses == 4
    assert st.portfolio.loaded is None and st.portfolio._persistence_active is False
    assert "portfolio block could not be loaded" in said.text and "ValueError" in said.text, said.text
    assert "no balance" not in said.text, "the exception's text reached the log"
    assert "COMBINED_FILE_FAIL_CLOSED" not in said.results, said.results
    assert combined.exists(), "a file that parsed is not moved aside"


def test_a_file_with_no_risk_block_is_an_unread_risk_state(tmp_path, said):
    r, st, combined = _boot(tmp_path, json.dumps({"version": 1, "portfolio": {"balance": 5.0}}))
    assert st.portfolio.loaded == {"balance": 5.0}, "the readable half is still read"
    assert r._circuit_open is True and r._circuit_trip_cause == "state_unreadable"
    assert said.results == ["COMBINED_FILE_FAIL_CLOSED"] and "no risk block" in said.audits[0][0]
    assert combined.exists()


def test_a_readable_file_restores_both_blocks_and_moves_nothing(tmp_path, said):
    r, st, combined = _boot(tmp_path, json.dumps(
        {"version": 1, "portfolio": {"balance": 7.0}, "risk": CLOSED}))
    assert st.portfolio.loaded == {"balance": 7.0} and st.portfolio._persistence_active is True
    assert r._circuit_open is False
    assert combined.exists() and not (tmp_path / "combined_state.json.corrupt").exists()
    assert not said.errors and "COMBINED_FILE_FAIL_CLOSED" not in said.results


def test_a_missing_file_is_a_fresh_start_not_a_failed_read(tmp_path, said):
    r, st, combined = _boot(tmp_path, None)
    assert r._circuit_open is False
    assert not said.errors and not said.results


def test_the_breaker_it_opens_reaches_disk_on_the_next_save(tmp_path):
    r, st, combined = _boot(tmp_path, "{not json")
    st._save_combined_state()
    assert json.loads(combined.read_text())["risk"]["circuit_trip_cause"] == "state_unreadable"


def test_a_read_that_raises_names_the_class_and_never_its_text(tmp_path, said, monkeypatch):
    """A venue never writes this file, but an OSError's text carries the
    PATH and whatever the filesystem said; the rule for the operator log is
    the class only, the same rule every other unread-state message keeps."""
    import pathlib
    combined = tmp_path / "combined_state.json"
    combined.write_text("{}")
    real = pathlib.Path.read_text

    def _raise(self, *a, **k):
        if self == combined:
            raise OSError(SECRET_TEXT)
        return real(self, *a, **k)
    monkeypatch.setattr(pathlib.Path, "read_text", _raise)
    r = _risk(tmp_path)
    _stand(r, combined)._wire_combined_state_saver()
    assert r._circuit_open is True
    assert "OSError" in said.text and "OSError" in said.audits[0][0]
    assert SECRET_TEXT not in said.text and SECRET_TEXT not in str(said.audits), (said.text, said.audits)


def test_the_risk_engines_own_file_is_left_alone(tmp_path, said):
    """The damaged file is the ENGINE's combined one, and the risk engine is
    told so: rescuing its own individual file would preserve a file that read
    perfectly well and label it as the evidence."""
    own = tmp_path / "risk_state.json"
    own.write_text(json.dumps(CLOSED))
    r, st, combined = _boot(tmp_path, "{not json")
    assert r._circuit_open is True
    assert own.exists() and json.loads(own.read_text()) == CLOSED
    assert not (tmp_path / "risk_state.json.corrupt").exists(), \
        "the risk engine moved its own, readable, file aside"
    assert (tmp_path / "combined_state.json.corrupt").exists()
