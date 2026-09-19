"""A risk state nobody read is not a safe one — the loader and the card.

TWO SURFACES, ONE CLAIM, and the link is the rule this repo states as *ask
which OTHER surface makes the same claim*:

  * `RiskEngine._load_from_state_dict` claimed "fail-closed semantics matching
    `_load_state`" over a body with no `try` and six `.get(key, <the safe
    value>)` calls, and it never called the third restore helper — so the day's
    realized LIVE loss was written to disk on every close and read by nobody on
    restart;
  * `RuneClawEngine.account_risk_overview` defaulted `circuit_open=False` and
    `consecutive_losses=0` for an account whose engine `risk_for` has not bound
    in this process, which after every restart is all of them — and `/accounts`
    printed `·` in the column whose job is to say which accounts are halted.

Everything here is DRIVEN. The loader gets real blocks; the card gets the real
`account_risk_overview` and the real `_cmd_accounts` renderer, because the claim
is what a person reads and no scan of either can see it.
"""
from __future__ import annotations

import asyncio
import json
import os
import re

from bot.core.engine import RuneClawEngine
from bot.risk.portfolio import PortfolioTracker
from bot.risk.risk_engine import RiskEngine
from bot.skills.engine_ops_commands import EngineOpsCommands

# ---------------------------------------------------------------- the loader

def _engine(tmpdir: str, name: str = "risk_state.json") -> RiskEngine:
    pf = PortfolioTracker(initial_balance=10_000.0,
                          state_file=os.path.join(tmpdir, f"pf-{name}"))
    return RiskEngine(pf, state_file=os.path.join(tmpdir, name))


def _halted(eng: RiskEngine) -> RiskEngine:
    """The engine as `__init__` leaves it when the individual file said HALTED."""
    eng._circuit_open = True
    eng._consecutive_losses = 4
    eng._circuit_trip_cause = "daily_loss"
    eng._circuit_trip_day = "2026-09-19"
    eng._circuit_breaker_trips = 2
    return eng


def _block(**over) -> dict:
    base = {
        "circuit_open": True, "consecutive_losses": 4,
        "last_loss_time": 1_758_000_000.0, "circuit_breaker_trips": 2,
        "circuit_trip_cause": "daily_loss", "circuit_trip_day": "2026-09-19",
    }
    base.update(over)
    return base


class TestTheReading:
    """`_read_state_dict` answers the six fields, or None for a block that is
    not a risk state."""

    def test_a_complete_block_reads(self):
        got = RiskEngine._read_state_dict(_block())
        assert got is not None
        assert got["circuit_open"] is True
        assert got["consecutive_losses"] == 4

    def test_a_none_where_the_default_is_none_is_a_reading(self):
        # `last_loss_time` is Optional[float] and an engine that has not had a
        # loss writes None on every save. Refusing it read every honest block as
        # unreadable and halted the engine on EVERY restart — caught by the
        # real-boot drive, not by reading the function.
        got = RiskEngine._read_state_dict(_block(last_loss_time=None))
        assert got is not None and got["last_loss_time"] is None

    def test_a_non_mapping_is_not_a_risk_state(self):
        for junk in (None, [], "nope", 7):
            assert RiskEngine._read_state_dict(junk) is None

    def test_a_block_without_circuit_open_is_not_a_risk_state(self):
        # The portfolio sibling demands `balance` for the identical reason.
        assert RiskEngine._read_state_dict({}) is None
        assert RiskEngine._read_state_dict({"saved_at": "2026-09-19"}) is None

    def test_a_field_of_the_wrong_type_is_no_reading_of_that_field(self):
        for over in ({"circuit_open": "false"}, {"circuit_open": 0},
                     {"consecutive_losses": "4"},
                     {"circuit_breaker_trips": None},
                     {"circuit_trip_cause": 3}):
            assert RiskEngine._read_state_dict(_block(**over)) is None, over

    def test_a_bool_is_not_a_count(self):
        # isinstance(True, int) is True in Python; a trips count of True is not
        # a count of one.
        assert RiskEngine._read_state_dict(_block(circuit_breaker_trips=True)) is None

    def test_the_absent_optional_fields_take_their_documented_defaults(self):
        got = RiskEngine._read_state_dict({"circuit_open": False})
        assert got == {"circuit_open": False, "consecutive_losses": 0,
                       "last_loss_time": None, "circuit_breaker_trips": 0,
                       "circuit_trip_cause": "", "circuit_trip_day": ""}


class TestAnUnreadableBlockFailsClosed:

    def test_a_keyless_block_no_longer_erases_a_halt(self, tmp_path):
        eng = _halted(_engine(str(tmp_path)))
        eng._load_from_state_dict({"saved_at": "2026-09-19T04:00:00+00:00"})
        assert eng._circuit_open is True
        assert eng._circuit_trip_cause == "state_unreadable"

    def test_the_spelling_trap_does_not_decide_the_breaker(self, tmp_path):
        # bool("false") is True and bool(None) is False — one expression, two
        # wrong answers, decided by spelling.
        eng = _halted(_engine(str(tmp_path)))
        eng._load_from_state_dict(_block(circuit_open="false"))
        assert eng._circuit_open is True
        assert eng._circuit_trip_cause == "state_unreadable"

    def test_it_does_not_rescue_a_file_that_read_perfectly_well(self, tmp_path):
        # `_fail_closed_restore`'s rescue moves `self._state_file` aside. The
        # damaged file here is the engine's COMBINED one, which this object does
        # not know the path of, so rescuing its own would label the wrong file
        # as the evidence.
        eng = _halted(_engine(str(tmp_path)))
        with open(eng._state_file, "w") as fh:
            fh.write(json.dumps(_block()))
        eng._load_from_state_dict({})
        assert os.path.exists(eng._state_file)
        assert not os.path.exists(eng._state_file + ".corrupt")

    def test_the_individual_path_still_rescues(self, tmp_path):
        eng = _engine(str(tmp_path))
        with open(eng._state_file, "w") as fh:
            fh.write("{not json")
        eng._load_state()
        assert eng._circuit_open is True
        assert os.path.exists(eng._state_file + ".corrupt")

    def test_valid_json_that_is_not_a_risk_state_is_CORRUPT_not_UNREADABLE(
            self, tmp_path, monkeypatch):
        # Both branches halt, so "it failed closed" is satisfied either way —
        # the mutation round said so by surviving an assertion that only
        # checked that. The DIAGNOSIS is the thing: without the explicit read,
        # `_apply_state_dict(None)` raises TypeError into the catch-all and the
        # operator is told "State file unreadable", which sends them to check
        # permissions and disk for a file that read perfectly and holds the
        # wrong thing.
        said: list[str] = []
        import bot.risk.risk_engine as mod
        monkeypatch.setattr(mod, "audit",
                            lambda log, msg, **kw: said.append(kw.get("result", "")))
        eng = _engine(str(tmp_path))
        with open(eng._state_file, "w") as fh:
            fh.write(json.dumps(["not", "a", "risk", "state"]))
        eng._load_state()
        assert eng._circuit_open is True
        assert eng._circuit_trip_cause == "state_unreadable"
        assert "CORRUPT_FAIL_CLOSED" in said, said
        assert "IO_FAIL_CLOSED" not in said, said


class TestTheDayIsRestoredOnTheProductionPath:
    """The combined saver is what `_save_state` delegates to once wired, so the
    individual file goes stale from the migration moment and the combined block
    is the only durable record of the day's realized LIVE loss."""

    @staticmethod
    def _stand(risk, combined_path):
        class StandPortfolio:
            _persistence_active = True
            _combined_saver = None
            def _export_state_dict(self): return {"balance": 10_000.0}
            def _load_from_state_dict(self, d): self.loaded = dict(d)

        class Stand:
            _combined_state_file = combined_path
            _wire_combined_state_saver = RuneClawEngine._wire_combined_state_saver
            _save_combined_state = RuneClawEngine._save_combined_state
            def __init__(self):
                self.portfolio = StandPortfolio()
                self.risk = risk
        return Stand()

    def test_the_days_live_loss_survives_a_restart(self, tmp_path):
        combined = str(tmp_path / "combined_state.json")
        r1 = _engine(str(tmp_path), "r1.json")
        self._stand(r1, combined)._wire_combined_state_saver()
        day = r1._utc_day()
        r1._live_daily_day, r1._live_daily_pnl = day, -412.55
        r1._consecutive_losses = 3
        r1._save_state()

        on_disk = json.load(open(combined))["risk"]
        assert on_disk["live_daily_pnl"] == -412.55

        r2 = _engine(str(tmp_path), "r2.json")
        assert r2.live_daily_pnl_today() == 0.0          # nothing restored yet
        self._stand(r2, combined)._wire_combined_state_saver()
        assert r2.live_daily_pnl_today() == -412.55
        assert r2._consecutive_losses == 3

    def test_both_loaders_call_all_three_restore_helpers(self):
        # The asymmetry is what this slice removes, and a source read is the
        # honest instrument: the claim is that neither loader can drift from
        # the other again, which no single drive can state.
        import ast
        import inspect
        seen = {}
        for name in ("_load_state", "_load_from_state_dict"):
            src = inspect.getsource(getattr(RiskEngine, name))
            seen[name] = {
                n.func.attr for n in ast.walk(ast.parse(src.lstrip()))
                if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                and n.func.attr.startswith("_restore")
            }
        want = {"_restore_dd_override", "_restore_live_daily", "_restore_live_peak"}
        assert seen["_load_state"] == want
        assert seen["_load_from_state_dict"] == want

    def test_the_erasure_of_a_halt_is_audited(self, tmp_path, monkeypatch):
        said: list[tuple[str, str]] = []
        import bot.risk.risk_engine as mod
        monkeypatch.setattr(mod, "audit",
                            lambda log, msg, **kw: said.append((msg, kw.get("result", ""))))
        eng = _halted(_engine(str(tmp_path)))
        eng._load_from_state_dict(_block(circuit_open=False, consecutive_losses=0))
        assert eng._circuit_open is False
        assert any(r == "CLEARED_BY_COMBINED" for _, r in said), said

    def test_a_restore_that_keeps_the_halt_still_says_active(self, tmp_path, monkeypatch):
        said: list[str] = []
        import bot.risk.risk_engine as mod
        monkeypatch.setattr(mod, "audit",
                            lambda log, msg, **kw: said.append(kw.get("result", "")))
        eng = _engine(str(tmp_path))
        eng._load_from_state_dict(_block())
        assert eng._circuit_open is True
        assert "LOADED" in said


# ------------------------------------------------------------------ the card

class _Pos:
    def __init__(self, cost=None):
        self.cost_usd = cost
        self.entry_price = 0.0
        self.quantity = 0.0


class _Ex:
    def __init__(self, uid, positions):
        self.user_id = uid
        self.open_positions = positions


class _Risk:
    def __init__(self, open_=False, streak=0):
        self._o = open_
        self.consecutive_losses = streak

    @property
    def circuit_breaker_active(self):
        return self._o

    def live_performance_state(self):
        return {"status": "OK"}

    def equity_throttle_state(self):
        return {"status": "OK"}


class _SulkyRisk(_Risk):
    @property
    def circuit_breaker_active(self):
        raise RuntimeError("venue-backed read failed")


class _BrokenEx:
    """An executor whose book cannot be read at all — the ERROR row.

    `getattr(ex, "open_positions", [])` returns the default only on
    AttributeError, so a RuntimeError here propagates to the sweep's own
    per-account `except` exactly as a venue listing refusal does.
    """

    def __init__(self, uid):
        self.user_id = uid

    @property
    def open_positions(self):
        raise RuntimeError("venue listing refused")


class _StandEngine:
    account_risk_overview = RuneClawEngine.account_risk_overview
    _user_store = None

    def __init__(self, execs, resident):
        self._execs = execs
        self._user_risk = resident
        self.risk = _Risk()

    def _all_live_executors(self):
        return self._execs

    async def get_user_live_equity(self, uid):
        return {"total": 2_500.0}

    async def get_live_equity(self):
        return {"total": 9_000.0}


def _rows(execs, resident):
    return {r["account"]: r
            for r in asyncio.run(_StandEngine(execs, resident).account_risk_overview())}


def _card(execs, resident) -> str:
    sent: list[str] = []

    class Host(EngineOpsCommands):
        def __init__(self, e): self.engine = e
        def _is_admin(self, u): return True
        def _lang(self, u): return "en"
        async def _send(self, u, text, **kw): sent.append(text)

    asyncio.run(Host(_StandEngine(execs, resident))._cmd_accounts(None, None))
    return re.sub(r"</?(pre|b|i)>", "", sent[0])


def _row_of(card: str, acct: str) -> str:
    for line in card.splitlines():
        if line.strip().startswith(acct):
            return line
    raise AssertionError(f"{acct!r} has no row in:\n{card}")


# The row is fixed-width: " {acct:<10}{eq:>9}{pos:>4}{exp:>10}{cb:>4}{strk:>5}".
# Asserting a SHORT STRING is in a row is the assertion that keeps misfiring —
# `—` spells both an unread exposure and the streak of an unread breaker, so
# the mutation that prints `$0` for the first survived a green suite. Each cell
# is read by its own column.
_COLS = {"acct": (1, 11), "equity": (11, 20), "pos": (20, 24),
         "exposure": (24, 34), "cb": (34, 38), "strk": (38, 43)}


def _cell(card: str, acct: str, col: str) -> str:
    lo, hi = _COLS[col]
    return _row_of(card, acct)[lo:hi].strip()


class TestTheBreakerColumnIsAReading:

    def test_an_unbound_engine_is_not_a_closed_breaker(self):
        rows = _rows([_Ex("777", [_Pos(120.0)])], {})
        assert rows["777"]["breaker_read"] == "not_resident"
        assert rows["777"]["circuit_open"] is None
        assert rows["777"]["consecutive_losses"] is None

    def test_the_card_prints_a_question_not_a_dot(self):
        card = _card([_Ex("777", [_Pos(120.0)])], {})
        assert _cell(card, "777", "cb") == "?"
        assert _cell(card, "777", "strk") == "—"
        assert "breaker not read" in card

    def test_a_read_breaker_still_prints_its_verdict(self):
        card = _card([_Ex("888", [_Pos(50.0)])], {"888": _Risk(True, 4)})
        assert _cell(card, "888", "cb") == "⛔"
        assert _cell(card, "888", "strk") == "4"
        assert "1 halted" in card
        assert "breaker not read" not in card

    def test_a_read_breaker_that_is_closed_prints_the_dot(self):
        card = _card([_Ex("888", [_Pos(50.0)])], {"888": _Risk(False, 0)})
        assert _cell(card, "888", "cb") == "·"

    def test_a_breaker_that_would_not_answer_keeps_the_rest_of_the_row(self):
        # A property that raises used to abort the whole row into `error`,
        # throwing away the equity, count and exposure already read — guard
        # where a composite view is owed omit.
        rows = _rows([_Ex("999", [_Pos(120.0)])], {"999": _SulkyRisk()})
        assert rows["999"]["breaker_read"] == "unreadable"
        assert rows["999"]["error"] is None
        assert rows["999"]["equity_usd"] == 2_500.0
        assert rows["999"]["exposure_usd"] == 120.0

    def test_a_fault_is_not_the_same_glyph_as_an_unbound_engine(self):
        card = _card([_Ex("777", [_Pos(120.0)]), _Ex("999", [_Pos(50.0)])],
                     {"999": _SulkyRisk()})
        assert _cell(card, "777", "cb") == "?"
        assert _cell(card, "999", "cb") == "!"
        assert "breaker not read" in card and "breaker read FAILED" in card

    def test_halted_counts_only_the_breakers_that_were_read(self):
        card = _card([_Ex("777", [_Pos(1.0)]), _Ex("888", [_Pos(1.0)])],
                     {"888": _Risk(True, 1)})
        assert "1 halted" in card
        assert "1 breaker not read" in card


class TestTheExposureSaysWhatItSummed:

    def test_an_unstated_margin_is_not_zero(self):
        rows = _rows([_Ex("777", [_Pos(None), _Pos(120.0)])], {})
        assert rows["777"]["exposure_usd"] == 120.0
        assert rows["777"]["exposure_scored"] == 1

    def test_a_partial_total_is_marked_and_counted(self):
        card = _card([_Ex("777", [_Pos(None), _Pos(120.0)])], {})
        assert _cell(card, "777", "exposure") == "$120*"
        assert "exposure partial" in card

    def test_no_readable_margin_at_all_is_a_dash_not_a_zero(self):
        rows = _rows([_Ex("777", [_Pos(None)])], {})
        assert rows["777"]["exposure_usd"] is None
        # The breaker is READ here so the streak column spells a number:
        # the dash under test is the exposure's own and nothing else's.
        card = _card([_Ex("777", [_Pos(None)])], {"777": _Risk()})
        assert _cell(card, "777", "exposure") == "—"
        assert _cell(card, "777", "strk") == "0"

    def test_a_flat_book_is_a_measured_zero(self):
        # `scored == []` is both "nothing readable" and "nothing to read", and
        # folding them is the shape this whole reading exists to remove.
        rows = _rows([_Ex("555", [])], {"555": _Risk()})
        assert rows["555"]["exposure_usd"] == 0.0
        assert _cell(_card([_Ex("555", [])], {"555": _Risk()}), "555", "exposure") == "$0"

    def test_a_whole_readable_book_carries_no_marker(self):
        card = _card([_Ex("888", [_Pos(50.0), _Pos(70.0)])], {"888": _Risk()})
        assert _cell(card, "888", "exposure") == "$120"
        assert "exposure partial" not in card


class TestTheFooterOnlySaysWhatBit:

    def test_a_clean_sweep_carries_neither_clause(self):
        card = _card([_Ex("888", [_Pos(50.0)])], {"888": _Risk()})
        assert "breaker not read" not in card
        assert "exposure partial" not in card
        assert "breaker read FAILED" not in card

    def test_a_clean_sweep_carries_no_error_clause_either(self):
        card = _card([_Ex("888", [_Pos(50.0)])], {"888": _Risk()})
        assert "not read at all" not in card


class TestTheFooterClosesOverAnErroredRow:
    """The sweep is fail-open per account, so a row whose book could not be
    read at all carries `error` and the renderer `continue`s on it — BEFORE
    every counter. That row is therefore in `len(rows)` and in none of the
    figures beside it.

    It was invisible while the footer said three things. With the three "only
    when it bites" clauses the footer reads as exhaustive, and a reader
    counting glyphs against the account total finds one missing, which is the
    subtraction a partial taxonomy invites: `N - (live + halted + unread +
    faulted)` is not a number anybody measured.
    """

    def test_an_errored_account_is_counted_and_named(self):
        card = _card([_Ex("888", [_Pos(50.0)]), _BrokenEx("999")],
                     {"888": _Risk()})
        assert "2 account(s)" in card
        assert "1 not read at all (ERROR)" in card

    def test_the_row_itself_still_names_the_fault(self):
        card = _card([_Ex("888", [_Pos(50.0)]), _BrokenEx("999")],
                     {"888": _Risk()})
        assert "ERROR: venue listing refused" in _row_of(card, "999")

    def test_an_errored_row_is_not_filed_as_a_breaker_state(self):
        # Nothing about the account was read. That is a THIRD fact beside "no
        # engine is bound" and "the engine would not answer", each of which has
        # its own remedy, so folding it into either would send an admin to the
        # wrong one.
        card = _card([_Ex("888", [_Pos(50.0)]), _BrokenEx("999")],
                     {"888": _Risk()})
        assert "breaker not read" not in card
        assert "breaker read FAILED" not in card

    def test_every_state_is_named_in_one_sweep(self):
        card = _card([_Ex("777", [_Pos(50.0)]),     # breaker read
                      _Ex("888", [_Pos(50.0)]),     # no engine bound
                      _Ex("555", [_Pos(50.0)]),     # engine would not answer
                      _BrokenEx("999")],            # nothing read at all
                     {"777": _Risk(), "555": _SulkyRisk()})
        assert "4 account(s)" in card
        assert "1 breaker not read" in card
        assert "1 breaker read FAILED" in card
        assert "1 not read at all (ERROR)" in card


class TestTheCardDoesNotBindAnEngine:

    def test_reading_the_overview_creates_no_risk_engine(self):
        # `risk_for` would construct one, and `RiskEngine.__init__` runs
        # `_load_state`, which is FAIL-CLOSED: a per-user file that will not
        # parse would trip THAT ACCOUNT'S breaker as a side effect of an admin
        # READ. A read command that can halt an account is not a read command.
        eng = _StandEngine([_Ex("777", [])], {})
        asyncio.run(eng.account_risk_overview())
        assert eng._user_risk == {}
