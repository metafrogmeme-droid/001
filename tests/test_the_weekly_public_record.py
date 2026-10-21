"""The weekly public record says what was recorded, in percent, ratio and
count, and says what it could not read.

`bot/core/public_record.py` builds it, `scripts/weekly_record.py` freezes a
week into `benchmark/record/`, and `scripts/verify_evidence.py` checks every
committed week. Driven here, both arms each time:

- each section has three states (read, absent, unreadable), and a section
  that could not be read leaves the other two standing;
- the sleeve publishes that its record is kept and nothing in a row: its
  pre-registration forbids a look at the rows before 2027-10-17, and the
  constant is held to the document's own words;
- the equity section prints a Sharpe ratio only from 30 daily returns, and
  never an equity figure;
- the parity section is the reports panel's own dollar-free projection, and
  a broken trades file is unreadable, not "no trades";
- the script writes the week once, and refuses to move a written week;
- the verifier passes a clean week and fails each defect it exists to catch.
"""
from __future__ import annotations

import importlib.util
import json
import math
import sys
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from bot.core import public_record as pr

ROOT = Path(__file__).resolve().parents[1]

#: Dollar-shaped keys: the verifier's list, the `_usd` suffix, and the
#: parity summary's own dollar fields.
DOLLAR_KEYS = {"net_pnl", "gross_pnl", "total_fees", "notional", "mean_net",
               "equity_usd", "free_usd", "total_pnl", "final_equity", "balance"}


def _keys(obj) -> set[str]:
    if isinstance(obj, dict):
        return set(obj) | set().union(*(_keys(v) for v in obj.values()))
    if isinstance(obj, list):
        return set().union(*(_keys(v) for v in obj))
    return set()


def _no_dollars(obj):
    bad = sorted(k for k in _keys(obj) if k in DOLLAR_KEYS or k.endswith("_usd"))
    assert not bad, bad


def _jsonl(path: Path, rows: list) -> Path:
    path.write_text("".join((r if isinstance(r, str) else json.dumps(r)) + "\n" for r in rows))
    return path


def _days(start: date, n: int, skip: frozenset = frozenset()):
    return [(start + timedelta(days=i)).isoformat() for i in range(n) if i not in skip]


# ── sleeve ───────────────────────────────────────────────────────────────

class TestTheSleeve:
    def test_the_sealed_date_is_the_pre_registration_s_own(self):
        text = (ROOT / pr.SLEEVE_PREREG).read_text(encoding="utf-8")
        assert f"Not before {pr.SLEEVE_MEASURED_ON}" in text

    def test_absent_when_no_row_was_ever_written(self, tmp_path):
        s = pr.sleeve_section(tmp_path / "none.jsonl")
        assert s["state"] == pr.ABSENT and s["days_recorded"] == 0
        assert s["measured"] is False and s["measured_on"] == "2027-10-17"

    def test_unreadable_when_the_file_cannot_be_opened_or_is_all_junk(self, tmp_path):
        with pytest.raises(pr.RecordUnreadable):
            pr.sleeve_section(tmp_path)                 # a directory: IsADirectoryError
        with pytest.raises(pr.RecordUnreadable):
            pr.sleeve_section(_jsonl(tmp_path / "junk.jsonl", ["{not json", "[1, 2]"]))

    def test_read_counts_days_and_gaps_and_publishes_nothing_in_a_row(self, tmp_path):
        rows = [{"day": d, "read_at": d + "T00:05:00+00:00", "n_book": 16, "gross_pct": 42.0,
                 "holds": [{"symbol": "BTC/USDT", "score": 0.8, "weight_pct": 12.5}],
                 "unread": ["XYZ/USDT"]}
                for d in _days(date(2026, 10, 1), 10, skip=frozenset({3, 4}))]
        p = _jsonl(tmp_path / "book.jsonl", rows + ["garbage"])
        s = pr.sleeve_section(p)
        assert s["state"] == pr.READ
        assert (s["days_recorded"], s["gap_days"], s["skipped_rows"]) == (8, 2, 1)
        assert (s["first_day"], s["last_day"]) == ("2026-10-01", "2026-10-10")
        # Nothing that is in a row: no symbol, weight, score, gross or count of
        # markets, at any depth, and none of the row's values either.
        leaked = [k for k in _keys(s) if any(w in k for w in
                  ("hold", "weight", "score", "gross", "return", "symbol", "unread", "n_book", "markets"))]
        assert not leaked, leaked
        assert "BTC" not in json.dumps(s) and "42.0" not in json.dumps(s)


# ── equity ───────────────────────────────────────────────────────────────

def _equity_rows(n: int, start=date(2026, 9, 1), base=1000.0):
    """`n` daily rows whose returns alternate +1% / -0.5% (a known series)."""
    eq = base
    rows = []
    for i, d in enumerate(_days(start, n)):
        if i:
            eq *= 1.01 if i % 2 else 0.995
        rows.append({"day": d, "read_at": d + "T00:01:00+00:00", "mode": "live",
                     "source": "venue", "equity_usd": round(eq, 6), "free_usd": 1.0})
    return rows


def _sharpe(returns):
    m = sum(returns) / len(returns)
    sd = math.sqrt(sum((r - m) ** 2 for r in returns) / (len(returns) - 1))
    return round(m / sd * math.sqrt(365), 2)


class TestTheEquity:
    def test_absent_when_the_bot_never_recorded_live_equity(self, tmp_path):
        e = pr.equity_section(tmp_path / "none.jsonl")
        assert e["state"] == pr.ABSENT and e["sharpe"] is None and e["daily_returns"] == 0

    def test_unreadable_is_not_absent(self, tmp_path):
        with pytest.raises(pr.RecordUnreadable):
            pr.equity_section(tmp_path)

    def test_no_sharpe_under_thirty_daily_returns(self, tmp_path):
        e = pr.equity_section(_jsonl(tmp_path / "eq.jsonl", _equity_rows(pr.MIN_DAILY_RETURNS)))
        assert e["state"] == pr.READ and e["daily_returns"] == pr.MIN_DAILY_RETURNS - 1
        assert e["sharpe"] is None
        assert e["min_daily_returns"] == 30

    def test_a_sharpe_from_thirty_daily_returns_annualised_on_365(self, tmp_path):
        rows = _equity_rows(pr.MIN_DAILY_RETURNS + 1)
        e = pr.equity_section(_jsonl(tmp_path / "eq.jsonl", rows))
        eq = [r["equity_usd"] for r in rows]
        returns = [b / a - 1 for a, b in zip(eq, eq[1:])]
        assert e["daily_returns"] == 30
        assert e["sharpe"] == _sharpe(returns)
        assert e["return_pct"] == round((eq[-1] / eq[0] - 1) * 100, 2)
        # The worst fall from a running high is one -0.5% day.
        assert e["max_drawdown_pct"] == -0.5

    def test_equity_itself_is_never_published_and_flows_are_said(self, tmp_path):
        e = pr.equity_section(_jsonl(tmp_path / "eq.jsonl", _equity_rows(40, base=12345.678)))
        _no_dollars(e)
        assert "12345" not in json.dumps(e)
        assert e["flows_separated"] is False and e["book"] == "live"

    def test_a_zero_or_missing_equity_row_is_skipped_not_read_as_a_wipeout(self, tmp_path):
        rows = _equity_rows(5)
        rows[2]["equity_usd"] = 0
        rows[3].pop("equity_usd")
        e = pr.equity_section(_jsonl(tmp_path / "eq.jsonl", rows))
        assert e["days_recorded"] == 3 and e["skipped_rows"] == 2
        assert e["max_drawdown_pct"] > -1.0, "a zero row would read as a -100% drawdown"


# ── parity ───────────────────────────────────────────────────────────────

def _trade(pnl, reason="take_profit", symbol="BTC/USDT:USDT"):
    return {"symbol": symbol, "side": "long", "entry_price": 100.0, "exit_price": 101.0,
            "size": 1.0, "pnl_usd": pnl, "close_reason": reason, "commission": 0.05,
            "closed_at": "2026-10-10T12:00:00+00:00", "fill_source": "venue"}


class TestTheParity:
    def test_no_live_book_and_no_file_are_absent(self, tmp_path):
        assert pr.parity_section(None)["state"] == pr.ABSENT
        assert pr.parity_section(tmp_path / "closed.json")["state"] == pr.ABSENT

    def test_a_broken_file_is_unreadable_not_no_trades(self, tmp_path):
        p = tmp_path / "closed.json"
        p.write_text("{truncated")
        with pytest.raises(pr.RecordUnreadable):
            pr.parity_section(p)
        p.write_text("42")
        with pytest.raises(pr.RecordUnreadable):
            pr.parity_section(p)

    def test_non_fills_only_is_absent_with_no_stated_win_rate(self, tmp_path):
        p = tmp_path / "closed.json"
        p.write_text(json.dumps([_trade(0.0, reason="expired"), _trade(0.0, reason="canceled")]))
        s = pr.parity_section(p)
        assert s["state"] == pr.ABSENT and s["trades"] == 0
        assert "win_rate" not in s and s["excluded_non_fills"] == 2

    def test_read_is_the_reports_panel_s_own_projection_and_dollar_free(self, tmp_path):
        from bot.core import web_reports as wr
        p = tmp_path / "closed.json"
        p.write_text(json.dumps([_trade(2.0), _trade(-1.0), _trade(3.0), _trade(0.0, reason="expired")]))
        s = pr.parity_section(p)
        assert s["state"] == pr.READ and s["trades"] == 3
        assert s["win_rate"] == pytest.approx(2 / 3)
        _no_dollars(s)
        # One projection: the dashboard's section and the record agree field
        # for field, so the two cannot drift into publishing different things.
        panel = wr._parity_section(NS(live_executor=NS(_closed_trades_file=str(p))))
        assert {k: v for k, v in s.items() if k != "state"} == panel


# ── the record ───────────────────────────────────────────────────────────

class TestTheRecord:
    NOW = datetime(2026, 10, 21, 9, 30, tzinfo=UTC)

    def test_a_dead_section_leaves_the_others_standing(self, tmp_path):
        eq = _jsonl(tmp_path / "eq.jsonl", _equity_rows(5))
        rec = pr.build_public_record(closed_trades_path=None, sleeve_path=tmp_path,
                                     equity_path=eq, now=self.NOW)
        assert rec["sleeve"] == {"state": pr.UNREADABLE, "exception": "RecordUnreadable"}
        assert rec["equity"]["state"] == pr.READ
        assert rec["parity"]["state"] == pr.ABSENT
        assert (rec["format"], rec["week"], rec["week_starts"]) == (
            "runeclaw.public_record.v1", "2026-W43", "2026-10-19")

    def test_the_whole_record_carries_no_dollar_key(self, tmp_path):
        closed = tmp_path / "closed.json"
        closed.write_text(json.dumps([_trade(2.0), _trade(-1.0)]))
        rec = pr.build_public_record(
            closed_trades_path=closed,
            sleeve_path=_jsonl(tmp_path / "b.jsonl", [{"day": "2026-10-20", "holds": []}]),
            equity_path=_jsonl(tmp_path / "eq.jsonl", _equity_rows(3)), now=self.NOW)
        assert {rec[k]["state"] for k in ("sleeve", "equity", "parity")} == {pr.READ}
        _no_dollars(rec)

    def test_the_iso_week_crosses_the_year_as_iso_does(self):
        assert pr.iso_week(datetime(2027, 1, 1, tzinfo=UTC)) == "2026-W53"
        assert pr.iso_week(datetime(2026, 12, 28, tzinfo=UTC)) == "2026-W53"


# ── the script ───────────────────────────────────────────────────────────

def _load(name: str, rel: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules.setdefault(name, mod)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def script(monkeypatch, tmp_path):
    mod = _load("weekly_record_script", "scripts/weekly_record.py")
    monkeypatch.setenv("DAILY_TREND_BOOK_PATH", str(_jsonl(tmp_path / "b.jsonl", [{"day": "2026-10-20"}])))
    monkeypatch.setenv("EQUITY_LOG_PATH", str(tmp_path / "no_equity.jsonl"))
    monkeypatch.setattr(mod, "closed_trades_path", lambda: tmp_path / "no_closed.json")
    return mod


class TestTheScript:
    def test_writes_the_week_once_and_refuses_to_move_it(self, script, tmp_path, capsys):
        out = tmp_path / "record"
        assert script.main(["--out-dir", str(out)]) == 0
        files = list(out.glob("*.json"))
        assert len(files) == 1
        rec = json.loads(files[0].read_text())
        assert files[0].name == rec["week"] + ".json" and rec["sleeve"]["days_recorded"] == 1
        first = files[0].read_text()
        assert script.main(["--out-dir", str(out)]) == 2
        assert files[0].read_text() == first, "a refused write must leave the week as it was"
        assert script.main(["--out-dir", str(out), "--force"]) == 0

    def test_an_unreadable_section_is_written_and_exits_one(self, script, monkeypatch, tmp_path):
        monkeypatch.setenv("EQUITY_LOG_PATH", str(tmp_path))      # a directory
        out = tmp_path / "record"
        assert script.main(["--out-dir", str(out)]) == 1
        rec = json.loads(next(out.glob("*.json")).read_text())
        assert rec["equity"]["state"] == pr.UNREADABLE

    def test_print_writes_nothing(self, script, tmp_path, capsys):
        out = tmp_path / "record"
        assert script.main(["--print", "--out-dir", str(out)]) == 0
        assert json.loads(capsys.readouterr().out)["format"] == pr.FORMAT
        assert not out.exists()

    def test_the_default_trades_file_is_the_operator_executor_s(self):
        mod = _load("weekly_record_script_real", "scripts/weekly_record.py")
        from bot.core.live_executor import _CLOSED_TRADES_FILE
        p = mod.closed_trades_path()
        assert p.is_absolute() and p.name == Path(_CLOSED_TRADES_FILE).name


# ── the verifier ─────────────────────────────────────────────────────────

ve = _load("verify_evidence", "scripts/verify_evidence.py")


def _week(tmp_path, rec: dict, name: str | None = None) -> Path:
    d = tmp_path / "benchmark" / "record"
    d.mkdir(parents=True, exist_ok=True)
    (d / (name or rec["week"] + ".json")).write_text(json.dumps(rec))
    return tmp_path


def _clean(now=datetime(2026, 10, 21, tzinfo=UTC)):
    return pr.build_public_record(closed_trades_path=None, sleeve_path=Path("/nonexistent/b"),
                                  equity_path=Path("/nonexistent/e"), now=now)


class TestTheVerifier:
    def test_the_committed_weeks_all_match(self):
        rows = ve.check_records(ROOT)
        assert len(rows) == len(list((ROOT / "benchmark" / "record").glob("*.json")))
        assert all(r.status == ve.MATCH for r in rows), [r.__dict__ for r in rows]

    def test_a_clean_week_matches(self, tmp_path):
        rows = ve.check_records(_week(tmp_path, _clean()))
        assert [r.status for r in rows] == [ve.MATCH]

    def test_a_dollar_key_fails(self, tmp_path):
        rec = _clean()
        rec["equity"]["equity_usd"] = 1000.0
        assert [r.status for r in ve.check_records(_week(tmp_path, rec))] == [ve.MISMATCH]

    def test_a_sleeve_row_before_the_sealed_date_fails_and_after_it_does_not(self, tmp_path):
        rec = _clean()
        rec["sleeve"]["holds"] = [{"symbol": "BTC", "weight_pct": 10}]
        assert [r.status for r in ve.check_records(_week(tmp_path / "a", rec))] == [ve.MISMATCH]
        later = _clean(now=datetime(2027, 10, 18, tzinfo=UTC))
        later["sleeve"]["return_pct"] = 4.0
        assert [r.status for r in ve.check_records(_week(tmp_path / "b", later))] == [ve.MATCH]

    def test_a_misnamed_file_and_a_wrong_format_fail(self, tmp_path):
        assert [r.status for r in ve.check_records(_week(tmp_path / "a", _clean(), "2026-W01.json"))] == [ve.MISMATCH]
        rec = _clean()
        rec["format"] = "something.else"
        assert [r.status for r in ve.check_records(_week(tmp_path / "b", rec))] == [ve.MISMATCH]

    def test_an_unparseable_week_cannot_be_checked(self, tmp_path):
        d = tmp_path / "benchmark" / "record"
        d.mkdir(parents=True)
        (d / "2026-W43.json").write_text("{")
        assert [r.status for r in ve.check_records(tmp_path)] == [ve.CANNOT]

    def test_no_week_written_is_no_row(self, tmp_path):
        assert ve.check_records(tmp_path) == []


# ── the website's contract ───────────────────────────────────────────────

FIXTURE = ROOT / "app" / "test" / "fixtures" / "public_record_week.json"


def _fixture_record(tmp: Path) -> dict:
    """A week with every section read, from fixed inputs: the record the
    website's allowlist (app/lib/weekly_record.js) is tested against."""
    closed = tmp / "closed.json"
    closed.write_text(json.dumps([_trade(2.0), _trade(-1.0), _trade(3.0),
                                  _trade(0.0, reason="expired")]))
    sleeve = _jsonl(tmp / "book.jsonl", [{"day": d, "holds": []}
                                         for d in _days(date(2026, 10, 1), 20, frozenset({5}))])
    equity = _jsonl(tmp / "eq.jsonl", _equity_rows(35))
    return pr.build_public_record(closed_trades_path=closed, sleeve_path=sleeve,
                                  equity_path=equity,
                                  now=datetime(2026, 10, 21, 9, 30, tzinfo=UTC))


def test_the_website_s_fixture_is_what_the_builder_writes(tmp_path):
    """The Node suite holds the website's allowlist to this file, so the file
    must be what the builder really writes: a field renamed here and not
    there fails one side or the other. Regenerate with
    RECORD_FIXTURE_REGEN=1 after a deliberate change."""
    import os
    rec = _fixture_record(tmp_path)
    text = json.dumps(rec, indent=1, sort_keys=True) + "\n"
    if os.environ.get("RECORD_FIXTURE_REGEN") == "1":
        FIXTURE.parent.mkdir(parents=True, exist_ok=True)
        FIXTURE.write_text(text)
    assert FIXTURE.read_text() == text, "regenerate with RECORD_FIXTURE_REGEN=1"
    assert {rec[k]["state"] for k in ("sleeve", "equity", "parity")} == {pr.READ}
    assert rec["equity"]["sharpe"] is not None, "the fixture must reach the Sharpe arm"
