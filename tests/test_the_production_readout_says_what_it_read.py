"""The day-0 production readout says what it read, and only that.

`scripts/production_readout.py` is plan item D0: the record every week-1
decision (G0's ADRs, D1's research ceiling, E1's budget split, C2's row
count) is made from. A readout that printed `0 rows` for a ledger it could
not open would be the one document where that shape does the most damage:
the operator would decide C2 is not runnable on a file that is full.

So every field is driven on a planted checkout that holds each state at
once: a ledger that reads, one that is not there, and one that is there and
will not open. The flags are driven through a real `bot.config` import in a
child process, because what matters is the value the bot resolves (an
unparseable number is the default in force, with ENV_UNREAD's reason). The
child runs inside a planted CHECKOUT, holding its own copy of `bot/`, the
script and a `.env`, because on the bot box the checkout the readout reads
is the one it imports from: the snapshot around the child then covers the
bytecode an import could leave beside `bot/` and the secrets vault that
importing `bot.config` seeds.
"""

from __future__ import annotations

import ast
import importlib.util
import json
import math
import os
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "production_readout.py"

_spec = importlib.util.spec_from_file_location("production_readout", SCRIPT)
assert _spec is not None and _spec.loader is not None
pr = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(pr)


def _jsonl(path: Path, lines: list) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join((x if isinstance(x, str) else json.dumps(x)) + "\n" for x in lines),
                    encoding="utf-8")


def _closed_rows() -> list[dict]:
    """Closed records spelled by the executor's own writer, oldest first."""
    from datetime import datetime, timezone

    from bot.core.live_executor import LivePosition, closed_trade_row

    def row(i, **kw):
        pos = LivePosition(trade_id=f"T{i}", symbol="BTC/USDT:USDT", direction="LONG",
                           entry_price=100.0, quantity=1.0, cost_usd=20.0,
                           stop_loss=95.0, take_profit=110.0, leverage=5,
                           closed_at=datetime(2026, 9, 10 + i, 12, tzinfo=timezone.utc),
                           status="closed")
        for k, v in kw.items():
            setattr(pos, k, v)
        return closed_trade_row(pos)

    return [
        # a cancelled order that never filled: not a close, not in the window
        row(0, close_reason="cancelled", pnl_usd=0.0, close_price=None, fill_source=None),
        row(1, close_reason="TP HIT (exchange)", close_price=110.0, pnl_usd=9.5,
            fill_source="bitget_position_history"),
        row(2, close_reason="SL HIT (inferred)", close_price=95.0, pnl_usd=-5.3,
            fill_source="ticker_fallback_after_3_retries",
            close_lookup="history raised NetworkError"),
        row(3, close_reason="CLOSED (unknown)", close_price=None, pnl_usd=None,
            fill_source="unread"),
        row(4, close_reason="SL HIT", close_price=96.0, pnl_usd=-4.2,
            fill_source="ticker_after_bot_close"),
        # the emergency close records an exit and no fill source
        row(5, close_reason=None, close_price=100.5, pnl_usd=0.4, fill_source=None),
    ]


@pytest.fixture
def planted(tmp_path):
    """A checkout holding every state: read, absent, unreadable."""
    learning = tmp_path / "data" / "learning"
    _jsonl(learning / "llm_calibration.jsonl", [
        {"ts": "2026-09-03T11:00:00+00:00", "llm_source": "LLM_TIER2"},
        {"ts": "2026-09-01T10:00:00+00:00", "llm_source": "LLM_TIER2_CACHED"},
        {"ts": "2026-09-05T09:30:00+00:00", "llm_source": "RULE_ENGINE_BUDGET"},
        {"ts": "2026-09-02T00:00:00+00:00", "llm_source": "RULE_ENGINE"},
        {"ts": "2026-09-04T00:00:00+00:00", "llm_source": "unknown"},
        {"llm_source": "LLM_FALLBACK_OPENAI"},       # no time stated
        "{not json",
        "[1, 2]",
    ])
    # order_flow_snapshots.jsonl: not planted -- absent.
    (learning / "decision_memory.jsonl").mkdir()   # there, and will not open
    (tmp_path / "data" / "closed_trades.json").write_text(json.dumps(_closed_rows()), encoding="utf-8")
    (tmp_path / "data" / "live_positions.json").write_text(json.dumps({
        "a": {"origin": "adopted", "cost_usd": 0.0, "adoption_unread": ["margin", "leverage"]},
        "b": {"origin": "executed", "cost_usd": 12.5},
        "c": {"origin": "reclaimed", "cost_usd": None},
        "d": {"origin": "adopted", "cost_usd": 8.0, "adoption_unread": []},
    }), encoding="utf-8")
    (tmp_path / "data" / "live_positions_42.json").write_text("{", encoding="utf-8")
    logs = tmp_path / "logs"
    _jsonl(logs / "trade.jsonl", [
        {"ts": "2026-09-20T01:00:00.000000Z", "message": "LLM daily dollar budget exhausted "
         "($1.0100 >= $1.0), using rules", "action": "analyze", "result": "LLM_BUDGET_USD"},
        {"ts": "2026-09-21T01:00:00.000000Z", "message": "LLM daily dollar budget exhausted "
         "($1.2000 >= $1.0), using rules", "action": "analyze", "result": "LLM_BUDGET_USD"},
        {"ts": "2026-09-21T02:00:00.000000Z", "message": "LLM daily budget exhausted (500 calls), "
         "using rules", "action": "analyze", "result": "LLM_BUDGET"},
        {"ts": "2026-09-21T03:00:00.000000Z", "message": "Trade idea generated", "action": "analyze"},
        '{"message": "LLM daily dollar budget exhausted (truncated',
    ])
    _jsonl(logs / "system.jsonl.1", [
        {"ts": "2026-09-19T00:00:00.000000Z", "message": "Chat LLM budget exhausted: share",
         "action": "chat_llm_budget", "result": "EXHAUSTED"},
        {"ts": "2026-09-19T05:00:00.000000Z", "message": "Tick exceeded its hard timeout",
         "action": "tick", "result": "HARD_TIMEOUT"},
    ])
    _jsonl(logs / "audit_chain.jsonl", [
        {"message": "LLM daily dollar budget exhausted (a flight record, not a log line)"},
    ])
    return tmp_path


def _build(root, env=None, **kw):
    import bot.config as config

    return pr.build_readout(root, config, env if env is not None else {}, **kw)


# ── the three values ────────────────────────────────────────────────────────

def test_each_ledger_is_a_reading_an_absence_or_unreadable(planted):
    led = _build(planted)["ledgers"]

    cal = led["llm_calibration"]
    assert cal["state"] == "read"
    assert (cal["rows"], cal["bad_lines"]) == (6, 2)

    of = led["order_flow_snapshots"]
    assert of["state"] == "absent"
    assert of["path"] == "data/learning/order_flow_snapshots.jsonl"
    assert "rows" not in of, "an absent file is not a ledger of zero rows"

    dec = led["decision_memory"]
    assert dec["state"] == "unreadable"
    assert dec["error"] == "IsADirectoryError"
    assert "rows" not in dec and "by_decision_type" not in dec


def test_the_date_range_is_the_rows_own_and_says_what_it_could_not_date(planted):
    dates = _build(planted)["ledgers"]["llm_calibration"]["dates"]
    assert dates["state"] == "read"
    # min and max, not the first and last line: the file is out of order
    assert dates["first"].startswith("2026-09-01T10:00")
    assert dates["last"].startswith("2026-09-05T09:30")
    assert dates["undated_rows"] == 1


def test_decision_rows_are_counted_by_type_not_by_trade_id(tmp_path):
    _jsonl(tmp_path / "data" / "learning" / "decision_memory.jsonl", [
        {"timestamp_utc": "2026-09-01T00:00:00Z", "decision": "TRADE_ACCEPTED_PAPER",
         "source": "runeclaw_engine"},
        {"timestamp_utc": "2026-09-02T00:00:00Z", "decision": "OUTCOME:TI-1", "source": "live_outcome"},
        {"timestamp_utc": "2026-09-03T00:00:00Z", "decision": "OUTCOME:TI-2", "source": "live_outcome"},
        {"timestamp_utc": "2026-09-04T00:00:00Z", "decision": "RESULT_FOR:AUD-9", "source": "trade_result"},
        {"timestamp_utc": "2026-09-05T00:00:00Z", "decision": ""},
    ])
    dec = _build(tmp_path)["ledgers"]["decision_memory"]
    assert dec["by_decision_type"] == {"OUTCOME": 2, "RESULT_FOR": 1, "TRADE_ACCEPTED_PAPER": 1,
                                       "(not recorded)": 1}
    assert dec["by_source"]["live_outcome"] == 2
    assert dec["dates"]["first"].startswith("2026-09-01") and dec["dates"]["last"].startswith("2026-09-05")


def test_an_empty_file_is_a_reading_of_no_rows_and_no_dates(tmp_path):
    (tmp_path / "data" / "learning").mkdir(parents=True)
    (tmp_path / "data" / "learning" / "llm_calibration.jsonl").write_text("", encoding="utf-8")
    r = _build(tmp_path)
    cal = r["ledgers"]["llm_calibration"]
    assert (cal["state"], cal["rows"]) == ("read", 0)
    assert cal["dates"]["state"] == "absent"
    assert r["llm_rule_share"]["state"] == "absent"


# ── the LLM / RULE_ENGINE share ─────────────────────────────────────────────

def test_the_llm_share_counts_every_row_and_folds_none_into_the_wrong_family(planted):
    share = _build(planted)["llm_rule_share"]
    assert share["state"] == "read"
    assert share["counts"] == {"LLM": 3, "RULE_ENGINE": 2, "other": 1}
    assert share["percent"] == {"LLM": 50.0, "RULE_ENGINE": 33.3, "other": 16.7}


def test_an_unreadable_calibration_file_has_no_share_at_all(tmp_path):
    (tmp_path / "data" / "learning" / "llm_calibration.jsonl").mkdir(parents=True)
    share = _build(tmp_path)["llm_rule_share"]
    assert share == {"state": "unreadable", "error": "IsADirectoryError"}


# ── the venue-priced share ──────────────────────────────────────────────────

def test_the_venue_priced_share_reads_the_records_own_fields(planted):
    r = _build(planted)
    closed = r["ledgers"]["closed_trades"]
    assert (closed["state"], closed["rows"]) == ("read", 6)
    assert closed["kinds"]["counts"]["non_fills"] == 1
    assert closed["dates"]["first"].startswith("2026-09-10")

    vp = r["venue_priced_closes"]
    assert vp["state"] == "read"
    assert vp["window"] == 5, "the cancelled order is not a close"
    # One venue-priced close. An exit nobody read and a close that recorded
    # no source are NOT venue-priced, whatever they are not.
    assert vp["counts"] == {"venue": 1, "ticker": 2, "no_exit_price": 1, "unrecorded": 1}
    assert vp["venue_priced_percent"] == 20.0
    assert vp["ticker_causes"] == {"history raised NetworkError": 1, "unrecorded": 1}
    assert "history raised NetworkError ×1" in vp["ticker_cause_sentence"]


def test_the_window_is_the_newest_filled_closes(planted):
    vp = _build(planted, last=2)["venue_priced_closes"]
    assert vp["window"] == 2 and vp["asked"] == 2
    assert vp["counts"] == {"venue": 0, "ticker": 1, "no_exit_price": 0, "unrecorded": 1}


def test_a_closed_record_the_executor_would_refuse_is_unreadable(tmp_path):
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "closed_trades.json").write_text('{"closed": []}', encoding="utf-8")
    r = _build(tmp_path)
    assert r["ledgers"]["closed_trades"]["state"] == "unreadable"
    assert r["venue_priced_closes"]["state"] == "unreadable"

    (tmp_path / "data" / "closed_trades.json").write_text("[{", encoding="utf-8")
    r = _build(tmp_path)
    assert r["ledgers"]["closed_trades"]["error"] == "JSONDecodeError"
    assert r["venue_priced_closes"] == {"state": "unreadable", "error": "JSONDecodeError"}


def test_the_state_dir_is_the_one_the_executor_is_pointed_at(planted, tmp_path_factory):
    other = tmp_path_factory.mktemp("state")
    (other / "closed_trades.json").write_text(json.dumps(_closed_rows()[:2]), encoding="utf-8")
    r = _build(planted, {"RUNECLAW_STATE_DIR": str(other)})
    assert r["ledgers"]["closed_trades"]["rows"] == 2
    assert r["positions"]["state"] == "absent"


def test_per_user_closed_records_are_counted_in_aggregate_and_never_named(planted):
    assert _build(planted)["ledgers"]["closed_trades_per_user"]["state"] == "absent"
    data = planted / "data"
    (data / "closed_trades_42.json").write_text(json.dumps(_closed_rows()[:3]), encoding="utf-8")
    (data / "venue" / "bybit").mkdir(parents=True)
    (data / "venue" / "bybit" / "closed_trades_43.json").write_text("[{", encoding="utf-8")
    pu = _build(planted)["ledgers"]["closed_trades_per_user"]
    assert pu == {"state": "read", "files": 2, "files_unreadable": {"JSONDecodeError": 1},
                  "files_at_cap": 0, "cap": pr.CLOSED_TRADES_CAP, "rows": 3, "rows_are_a_floor": True}
    assert "42" not in json.dumps(pu) and "43" not in json.dumps(pu)


def test_per_user_closed_records_none_of_which_would_read_are_no_row_count(tmp_path):
    """Two files found, neither read: that is not a reading of 0 rows."""
    (tmp_path / "closed_trades_42.json").write_text("[{", encoding="utf-8")
    (tmp_path / "closed_trades_43.json").write_text("{}", encoding="utf-8")
    pu = pr.read_per_user_closed(tmp_path)
    assert pu == {"state": "unreadable",
                  "reason": "no per-user or per-venue closed-trade file could be read",
                  "files": 2, "files_unreadable": {"JSONDecodeError": 1, "not a list of rows": 1}}
    assert "rows" not in pu
    # one that reads, even an empty list, is a reading again
    (tmp_path / "closed_trades_44.json").write_text("[]", encoding="utf-8")
    pu = pr.read_per_user_closed(tmp_path)
    assert (pu["state"], pu["rows"], pu["rows_are_a_floor"]) == ("read", 0, True)


# ── the executor's cap on a closed record ───────────────────────────────────

def test_the_cap_the_readout_names_is_the_executors():
    from bot.core.live_executor import _MAX_CLOSED_TRADES

    assert pr.CLOSED_TRADES_CAP == _MAX_CLOSED_TRADES


def _dated_rows(n: int) -> list[dict]:
    from datetime import datetime, timedelta, timezone

    t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    return [{"trade_id": f"T{i}", "closed_at": (t0 + timedelta(hours=i)).isoformat(),
             "close_reason": "TP HIT (exchange)", "close_price": 1.0,
             "fill_source": "bitget_position_history"} for i in range(n)]


@pytest.mark.parametrize("n", [pr.CLOSED_TRADES_CAP - 1, pr.CLOSED_TRADES_CAP])
def test_a_closed_record_at_the_cap_is_a_floor_and_one_below_it_is_whole(tmp_path, n):
    """The executor keeps the newest 500 rows. A file holding 500 may have
    lost older closes, so its count is a floor and its first date is the
    oldest row kept; a file of 499 was never trimmed."""
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "closed_trades.json").write_text(json.dumps(_dated_rows(n)), encoding="utf-8")
    (tmp_path / "data" / "closed_trades_42.json").write_text(json.dumps(_dated_rows(n)),
                                                             encoding="utf-8")
    r = _build(tmp_path)
    closed, pu = r["ledgers"]["closed_trades"], r["ledgers"]["closed_trades_per_user"]
    at_cap = n >= pr.CLOSED_TRADES_CAP
    assert (closed["rows"], closed["rows_are_a_floor"]) == (n, at_cap)
    assert (pu["files_at_cap"], pu["rows_are_a_floor"]) == (int(at_cap), at_cap)

    md = pr.render_markdown(r)
    row = next(ln for ln in md.splitlines() if ln.startswith("| closed trades (operator) |"))
    per_user = next(ln for ln in md.splitlines() if ln.startswith("- per-user / per-venue"))
    if at_cap:
        assert f"| {n} (at the executor's cap of {n}: a floor) |" in row
        assert "(the first is the oldest row the cap kept)" in row
        assert "1 file(s) at the executor's cap of 500 rows" in per_user
    else:
        assert row.split("|")[3].strip() == str(n)
        assert "cap" not in row and "cap" not in per_user


# ── positions with unread margin ────────────────────────────────────────────

def test_adopted_positions_with_no_margin_on_record_are_counted(planted):
    pos = _build(planted)["positions"]
    assert pos["state"] == "read"
    assert pos["rows"] == 4
    assert pos["by_origin"] == {"adopted": 2, "executed": 1, "reclaimed": 1}
    assert (pos["margin_unread"], pos["adopted_margin_unread"], pos["marked_margin_unread"]) == (2, 2, 1)
    # the per-user book that would not parse makes every count a floor, and
    # is listed without the user id its file name carries
    assert pos["counts_are_a_floor"] is True
    bad = [f for f in pos["files"] if f["state"] == "unreadable"]
    assert bad == [{"state": "unreadable", "error": "JSONDecodeError", "path": "per-user book"}]


def test_no_positions_file_is_an_absence_and_only_unreadable_ones_are_unreadable(tmp_path):
    assert _build(tmp_path)["positions"]["state"] == "absent"
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "live_positions.json").write_text("[]", encoding="utf-8")
    assert _build(tmp_path)["positions"]["state"] == "unreadable"


@pytest.mark.parametrize("cost", [0.0, -1.0, None, "abc", "", float("nan"), float("inf"),
                                  12.5, "7.5", 1e-9, True])
def test_the_margin_reading_is_the_executors(cost):
    """`margin_on_record` stands in for `position_size_basis`, which cannot
    be imported without opening the bot's log files. They agree, value by
    value, or the readout counts a different thing than every card does."""
    from bot.core.live_executor import position_size_basis

    theirs = position_size_basis(SimpleNamespace(cost_usd=cost, entry_price=None, quantity=None))[0]
    ours = pr.margin_on_record({"cost_usd": cost})
    assert (theirs is None) == (ours is None)
    if theirs is not None:
        assert math.isclose(theirs, ours)


# ── logs ────────────────────────────────────────────────────────────────────

def test_the_budget_audits_are_counted_across_rotations_and_the_audit_chain_is_not_read(planted):
    logs = _build(planted)["logs"]
    assert logs["state"] == "read"
    assert logs["files"] == ["system.jsonl.1", "trade.jsonl"]
    counts = {k: v["count"] for k, v in logs["audits"].items()}
    assert counts == {"thesis_dollar_budget": 2, "thesis_call_limit": 1, "chat_budget": 1,
                      "tick_phase_timeout": 0, "tick_hard_timeout": 1, "alert_monitor_stalled": 0}
    assert logs["audits"]["thesis_dollar_budget"]["dates"]["first"].startswith("2026-09-20T01:00")
    assert logs["audits"]["tick_phase_timeout"]["dates"]["state"] == "absent"
    assert logs["candidate_lines_unparsed"] == 1
    assert logs["monitor_pass_gap"]["state"] == "absent"


def test_a_log_it_cannot_open_makes_the_counts_a_floor_and_no_logs_is_an_absence(planted, tmp_path_factory):
    (planted / "logs" / "trade.jsonl.2").mkdir()
    logs = _build(planted)["logs"]
    assert logs["files_unreadable"] == {"trade.jsonl.2": "IsADirectoryError"}
    assert logs["counts_are_a_floor"] is True

    bare = tmp_path_factory.mktemp("bare")
    assert _build(bare)["logs"]["state"] == "absent"
    (bare / "logs").mkdir()
    (bare / "logs" / "trade.jsonl").mkdir()
    assert _build(bare)["logs"]["state"] == "unreadable"


def _audit_messages() -> dict[str, list]:
    """{path: [(leading literal of the message, action, result)]} for every
    `audit(...)` call under bot/, off the AST: no comment can be read as one."""
    out: dict[str, list] = {}
    for path in sorted((ROOT / "bot").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for n in ast.walk(tree):
            if not (isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "audit"):
                continue
            lead = ""
            if len(n.args) > 1:
                msg = n.args[1]
                if isinstance(msg, ast.JoinedStr) and msg.values and isinstance(msg.values[0], ast.Constant):
                    lead = str(msg.values[0].value)
                elif isinstance(msg, ast.Constant) and isinstance(msg.value, str):
                    lead = msg.value
            kw = {k.arg: k.value.value for k in n.keywords
                  if k.arg in ("action", "result") and isinstance(k.value, ast.Constant)}
            out.setdefault(str(path.relative_to(ROOT)), []).append(
                (lead, kw.get("action"), kw.get("result")))
    return out


def test_the_budget_audits_it_counts_are_the_ones_the_code_writes():
    """Both ways: every audit the bot writes about an exhausted budget is one
    the readout counts, and every one it counts is written somewhere."""
    written = [lead for rows in _audit_messages().values() for lead, _, _ in rows
               if "budget exhausted" in lead]
    assert written, "the walk found no budget audit at all, so it measured nothing"
    # The fixed sentence of each message: its text before the first figure.
    heads = {w.split("(")[0].split(":")[0].strip() for w in written}
    assert heads == {p for _, p in pr.BUDGET_AUDITS}
    assert "LLM daily dollar budget exhausted" in heads


def test_the_tick_audits_it_counts_are_written_by_the_engine():
    pairs = {(a, r) for rows in _audit_messages().values() for _, a, r in rows}
    missing = [(a, r) for _, a, r in pr.TICK_AUDITS if (a, r) not in pairs]
    assert missing == [], missing


# ── eligibility ─────────────────────────────────────────────────────────────

def test_eligibility_is_read_for_the_code_in_the_checkout(planted):
    from bot.core.live_eligibility import strategy_hash

    el = _build(planted)["eligibility"]
    running = strategy_hash(planted)
    assert el["state"] == "read"
    assert el["verdict"] == "missing"
    assert el["strategy_hash_prefix"] == running[:12]

    record = planted / "benchmark" / "eligibility" / f"{running}.json"
    record.parent.mkdir(parents=True)
    record.write_text(json.dumps({"schema": 1, "strategy_hash": running, "verdict": "survives",
                                  "stage": "minimum"}), encoding="utf-8")
    el = _build(planted)["eligibility"]
    assert (el["verdict"], el["stage"]) == ("eligible", "minimum")


# ── flags, through a real bot.config, in a child process ────────────────────

_SECRET = "sk-PLANTED-" + "0123456789abcdef" * 2


def _tree(root: Path) -> dict:
    return {str(p.relative_to(root)): (p.is_dir(), p.stat().st_size, p.stat().st_mtime_ns)
            for p in sorted(root.rglob("*"))}


@pytest.fixture
def checkout(planted):
    """The planted state inside a checkout of its own: a copy of `bot/` and of
    the script, and no `__pycache__` anywhere, so a byte the child writes
    beside the modules it imports shows in the snapshot."""
    shutil.copytree(ROOT / "bot", planted / "bot",
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    (planted / "scripts").mkdir()
    shutil.copy2(SCRIPT, planted / "scripts" / SCRIPT.name)
    return planted


def _run(root: Path, *args: str, script: Path | None = None, **env: str):
    """The readout in a child, run as the operator runs it: the copy inside
    ``root``, from ``root``. ``script`` runs another copy with ``--root``."""
    base = {k: v for k, v in os.environ.items()
            if not k.startswith(("AUTO_CONFIRM", "MICRO_MAX", "LLM_", "PER_USER", "WEB_LIVE",
                                 "LIVE_", "PAPER_", "RUNECLAW_", "SECRETS_VAULT", "OF_",
                                 "TRADE_MODE", "PYTHONDONTWRITEBYTECODE", "PYTHONPYCACHEPREFIX"))}
    base.update(RUNECLAW_ENV_INHERIT="1", RUNECLAW_STATE_DIR=str(root / "data"),
                SECRETS_VAULT_ENABLED="true", TELEGRAM_BOT_TOKEN=_SECRET,
                BITGET_API_SECRET=_SECRET, **env)
    argv = [str(root / "scripts" / SCRIPT.name)] if script is None else [str(script), "--root", str(root)]
    return subprocess.run([sys.executable, *argv, *args],
                          capture_output=True, text=True, timeout=120, cwd=str(root), env=base)


def test_the_flags_are_what_the_bot_resolves_and_nothing_is_written(checkout):
    before = _tree(checkout)
    proc = _run(checkout, "--json",
                AUTO_CONFIRM_THRESHOLD="0.9x", MICRO_MAX_POSITION_USD="40",
                AUTO_CONFIRM_LIVE_ENABLED="false", PER_USER_LIVE_ENABLED="maybe",
                LIVE_OPEN_TO_KEY_HOLDERS="0", WEB_LIVE_TRADING_ENABLED="on")
    assert proc.returncode == 0, proc.stderr[-2000:]
    assert _SECRET not in proc.stdout and _SECRET not in proc.stderr
    # `bot/` is in the snapshot: no bytecode beside the modules it imported,
    # and no vault file under the checkout the config was imported from.
    assert _tree(checkout) == before, "the readout wrote into the checkout it read"
    assert json.loads(proc.stdout)["config"] == {"state": "read"}

    flags = json.loads(proc.stdout)["flags"]
    th = flags["AUTO_CONFIRM_THRESHOLD"]
    # did not parse: the default is in force, and the reason is ENV_UNREAD's
    assert (th["value"], th["env_unread"]) == (0.85, "not a number")
    assert th["source"].startswith("code default")
    assert flags["MICRO_MAX_POSITION_USD"]["value"] == 40.0
    assert flags["MICRO_MAX_POSITION_USD"]["source"] == "environment"
    assert flags["AUTO_CONFIRM_LIVE_ENABLED"]["value"] is False
    assert "env_unread" not in flags["AUTO_CONFIRM_LIVE_ENABLED"]
    per_user = flags["PER_USER_LIVE_ENABLED"]
    assert (per_user["value"], per_user["env_unread"]) == (False, "not a recognised boolean")
    assert flags["LIVE_OPEN_TO_KEY_HOLDERS"]["value"] is False
    assert flags["WEB_LIVE_TRADING_ENABLED"]["value"] is True


def test_the_call_limits_unit_names_every_call_it_stops():
    """LLM_DAILY_LIMIT stops chat once every model call of the day, thesis
    and chat together, reaches it; a unit that said "thesis calls" would
    tell the operator chat is not bound by it."""
    from bot.core.cost import CostSummary, chat_budget_bound

    llm = SimpleNamespace(daily_call_limit=3, daily_budget_usd=1.0, chat_budget_share=0.5)
    assert chat_budget_bound(CostSummary(llm_calls=2), llm) == ""
    assert chat_budget_bound(CostSummary(llm_calls=3), llm) == "daily call limit"
    unit = {name: unit for name, _, unit in pr.FLAGS}["LLM_DAILY_LIMIT"]
    assert "chat" in unit and "thesis" in unit


def test_a_flag_nobody_set_says_it_is_the_code_default(planted):
    flags = _build(planted, {"MICRO_MAX_POSITION_USD": "40"})["flags"]
    assert flags["MICRO_MAX_OPEN_POSITIONS"]["source"] == "code default"
    assert flags["MICRO_MAX_POSITION_USD"]["source"] == "environment"


def test_the_flags_are_the_checkouts_own_env_and_another_checkout_is_not_read_as_it(checkout):
    """`bot.config` loads the `.env` beside its own package. Run from the
    bot's checkout, that `.env` decides the flags. Run from another copy with
    `--root` at the bot's checkout, the config it would import is the other
    copy's, so the readout imports none and prints no flag as a reading:
    printing the other copy's defaults as the bot's would say auto-confirm is
    on for a checkout whose `.env` turns it off."""
    (checkout / ".env").write_text("AUTO_CONFIRM_LIVE_ENABLED=false\nAUTO_CONFIRM_THRESHOLD=1.0\n",
                                   encoding="utf-8")
    before = _tree(checkout)

    own = _run(checkout, "--json")
    assert own.returncode == 0, own.stderr[-2000:]
    flags = json.loads(own.stdout)["flags"]
    assert flags["AUTO_CONFIRM_LIVE_ENABLED"]["value"] is False
    assert flags["AUTO_CONFIRM_THRESHOLD"]["value"] == 1.0
    assert _tree(checkout) == before

    other = _run(checkout, "--json", script=SCRIPT)
    assert other.returncode == 0, other.stderr[-2000:]
    r = json.loads(other.stdout)
    assert r["config"]["state"] == "unreadable"
    assert "--root" in r["config"]["reason"] and "bot's own checkout" in r["config"]["remedy"]
    assert {f["state"] for f in r["flags"].values()} == {"unreadable"}
    assert r["flags"]["AUTO_CONFIRM_LIVE_ENABLED"]["reason"] == r["config"]["reason"]
    assert r["env_unread"]["state"] == "unreadable"
    # the ledgers are still read from --root
    assert r["ledgers"]["closed_trades"]["rows"] == 6
    assert _tree(checkout) == before

    md = _run(checkout, script=SCRIPT).stdout
    assert "- `bot.config` was not read: unreadable (--root is not the checkout" in md
    assert "Run the script from inside the bot's own checkout." in md
    assert "| `AUTO_CONFIRM_LIVE_ENABLED` | unreadable (--root is not the checkout" in md


def test_a_config_that_refuses_to_start_is_unreadable_everywhere_and_never_quoted(checkout):
    """A setting `bot.config` refuses to start with raises SystemExit, and so
    does every later import of a reader that imports the config (parity, for
    the closed record). The readout still prints, names the class, and never
    the refusal's text, which quotes the value."""
    before = _tree(checkout)
    proc = _run(checkout, "--json", TRADE_MODE="spot")
    assert proc.returncode == 0, proc.stderr[-2000:]
    assert "'spot'" not in proc.stdout and "'spot'" not in proc.stderr
    r = json.loads(proc.stdout)
    assert r["config"] == {"state": "unreadable", "error": "SystemExit"}
    assert r["flags"]["AUTO_CONFIRM_LIVE_ENABLED"] == {"state": "unreadable", "error": "SystemExit",
                                                       "unit": "switch"}
    assert {f.get("error") for f in r["flags"].values()} == {"SystemExit"}
    assert r["env_unread"] == {"state": "unreadable", "error": "SystemExit"}
    closed = r["ledgers"]["closed_trades"]
    assert (closed["state"], closed["rows"]) == ("read", 6)
    assert closed["kinds"] == {"state": "unreadable", "error": "SystemExit"}
    assert r["venue_priced_closes"] == {"state": "unreadable", "error": "SystemExit"}
    assert r["positions"]["state"] == "read"
    assert _tree(checkout) == before

    md = _run(checkout, TRADE_MODE="spot").stdout
    assert "- `bot.config` was not read: unreadable (SystemExit)." in md
    assert "`RUNECLAW_STATE_DIR` or `OF_SNAPSHOT_PATH` set only there was not seen" in md


def test_the_markdown_says_absent_and_unreadable_in_words(checkout):
    before = _tree(checkout)
    proc = _run(checkout, AUTO_CONFIRM_THRESHOLD="high")
    assert proc.returncode == 0, proc.stderr[-2000:]
    assert _tree(checkout) == before
    md = proc.stdout
    assert "| `AUTO_CONFIRM_THRESHOLD` | 0.85 |" in md
    assert "`AUTO_CONFIRM_THRESHOLD` (not a number; default 0.85)" in md
    lines = {ln.split("|")[1].strip(): ln for ln in md.splitlines()
             if ln.startswith("| ") and ln.count("|") == 5}
    assert "absent (no file at this path)" in lines["order-flow snapshots"]
    assert "unreadable (IsADirectoryError)" in lines["decision memory"]
    assert "| 6 (+2 unreadable row(s), not counted) |" in lines["LLM calibration"]
    assert "venue-priced 1 (20.0%) · ticker-priced 2 · no exit price 1 · no fill source recorded 1" in md
    assert "- thesis dollar budget: 2 (" in md


def test_every_flag_the_plan_names_is_read_off_the_variable_it_names():
    """The readout's name for a value is the variable the config reads for
    it: a mapping that pointed MICRO_MAX_POSITION_USD at the wrong field
    would print a true number under a false name."""
    named = {"AUTO_CONFIRM_THRESHOLD", "AUTO_CONFIRM_LIVE_ENABLED", "AUTO_CONFIRM_USE_CALIBRATED",
             "MICRO_MAX_POSITION_USD", "MICRO_MAX_TOTAL_EXPOSURE", "MICRO_MAX_OPEN_POSITIONS",
             "LLM_DAILY_BUDGET_USD", "LLM_CHAT_BUDGET_SHARE", "PER_USER_LIVE_ENABLED",
             "WEB_LIVE_TRADING_ENABLED", "LIVE_OPEN_TO_KEY_HOLDERS", "PAPER_SIM_OPT_IN_ENABLED"}
    assert named <= {name for name, _, _ in pr.FLAGS}

    tree = ast.parse((ROOT / "bot" / "config.py").read_text(encoding="utf-8"))
    classes = {n.name: n for n in tree.body if isinstance(n, ast.ClassDef)}
    sections = {s.target.id: s.annotation.id for s in classes["AppConfig"].body
                if isinstance(s, ast.AnnAssign) and isinstance(s.target, ast.Name)
                and isinstance(s.annotation, ast.Name)}

    def env_names(cls: str, attr: str) -> set:
        for s in classes[cls].body:
            if isinstance(s, ast.AnnAssign) and isinstance(s.target, ast.Name) and s.target.id == attr:
                return {c.args[0].value for c in ast.walk(s.value)
                        if isinstance(c, ast.Call) and isinstance(c.func, ast.Name)
                        and c.func.id.startswith("_env") and c.args
                        and isinstance(c.args[0], ast.Constant)}
        raise AssertionError(f"{cls}.{attr} is not declared")

    for name, where, _ in pr.FLAGS:
        if where.startswith("bot."):
            continue
        *section, attr = where.split(".")
        cls = sections[section[0]] if section else "AppConfig"
        assert name in env_names(cls, attr), (name, where)
