"""scripts/sizing_forward.py: the window, the refusal, and the sealed rule."""
from __future__ import annotations

import importlib.util
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
UTC = timezone.utc


def _m():
    spec = importlib.util.spec_from_file_location(
        "sizing_forward", ROOT / "scripts" / "sizing_forward.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _close(day: int, net: float, notional: float = 1000.0, **kw) -> dict:
    t = datetime(2026, 11, 2, tzinfo=UTC) + timedelta(days=day)
    row = {"closed_at": t.isoformat(), "pnl_usd": net, "entry_price": 100.0,
           "quantity": notional / 100.0}
    row.update(kw)
    return row


def test_the_docstring_names_the_path_the_loader_uses():
    m = _m()
    assert 'state_path("data/closed_trades.json")' in m.__doc__


def test_the_window_is_twelve_weeks_after_the_seal():
    m = _m()
    assert (m.END - m.START).days == 84
    sealed = (ROOT / "docs/research/prereg/equal_sizing_forward_2026-10/"
              "PREREGISTRATION.sealed_at").read_text().strip()
    assert datetime.fromisoformat(sealed.replace("Z", "+00:00")) < m.START


def test_unreadable_rows_are_counted_not_scored_as_zero():
    m = _m()
    rows = [_close(0, 5.0), _close(1, None), _close(2, 3.0, quantity=None),
            {"pnl_usd": 1.0}, _close(-30, 9.0)]
    scored, left = m.score(rows)
    assert len(scored) == 1
    assert left == {"no_close_time": 1, "no_net": 1, "no_notional": 1}


def test_equal_size_wins_where_the_big_positions_lose():
    m = _m()
    rows = []
    for d in range(60):
        rows.append(_close(d, 10.0, notional=1000.0))     # small, +1.0%
        rows.append(_close(d, -40.0, notional=8000.0))    # large, -0.5%
    j = m.judge(m.score(rows)[0])
    assert j["E"] > 0 > j["W"]
    assert j["q1_equal_size_makes_money"] is True and j["q2_equal_beats_sized"] is True


def test_the_same_sizes_make_neither_claim_when_nothing_wins():
    m = _m()
    rows = [_close(d, (-5.0 if d % 2 else 4.0)) for d in range(80)]
    j = m.judge(m.score(rows)[0])
    assert j["q1_equal_size_makes_money"] is False and j["q2_equal_beats_sized"] is False


def test_too_few_closes_is_no_verdict():
    m = _m()
    j = m.judge(m.score([_close(d, 5.0) for d in range(39)])[0])
    assert j["q1_equal_size_makes_money"] is None and "no verdict" in j["verdict"]


def test_evaluate_refuses_before_the_window_closes_and_status_shows_no_return(tmp_path, capsys):
    m = _m()
    f = tmp_path / "closed_trades.json"
    f.write_text(json.dumps([_close(d, 5.0) for d in range(50)]))
    assert m.main(["evaluate", "--trades", str(f)], now=datetime(2027, 1, 22, tzinfo=UTC)) == 2
    assert m.main(["status", "--trades", str(f)], now=datetime(2026, 12, 1, tzinfo=UTC)) == 0
    out = capsys.readouterr().out
    assert "scored 50" in out and "%" not in out and '"E"' not in out
    assert m.main(["evaluate", "--trades", str(f)], now=datetime(2027, 1, 23, tzinfo=UTC)) == 0
    assert '"q1_equal_size_makes_money": true' in capsys.readouterr().out


def test_a_positive_mean_on_noise_is_not_money():
    m = _m()
    rows = [_close(d, (60.0 if d % 2 else -59.0)) for d in range(84)]
    j = m.judge(m.score(rows)[0])
    assert j["E"] > 0 and j["E_lower"] < 0 and j["q1_equal_size_makes_money"] is False


# ── Addendum 1: the paper book ───────────────────────────────────────────────

def _paper_close(day: int, net: float, notional: float = 1000.0, **kw) -> dict:
    t = datetime(2026, 11, 2, tzinfo=UTC) + timedelta(days=day)
    row = {"closed_at": t.isoformat(), "pnl": net, "entry_price": 100.0,
           "quantity": notional / 100.0, "status": "executed"}
    row.update(kw)
    return row


def test_the_paper_window_opens_after_the_addendum_and_runs_twelve_weeks():
    m = _m()
    assert (m.PAPER_END - m.PAPER_START).days == 84
    sealed = (ROOT / "docs/research/prereg/equal_sizing_forward_2026-10/"
              "ADDENDUM_1.sealed_at").read_text().strip()
    assert datetime.fromisoformat(sealed.replace("Z", "+00:00")) < m.PAPER_START


def test_each_book_reads_its_own_net_field_and_window():
    m = _m()
    paper_row = _paper_close(0, 5.0)
    live_row = _close(0, 5.0)
    paper = dict(start=m.PAPER_START, end=m.PAPER_END, net_keys=m.PAPER_NET_KEYS)
    assert len(m.score([paper_row], **paper)[0]) == 1
    assert m.score([live_row], **paper)[1]["no_net"] == 1, "pnl_usd is not the paper field"
    assert m.score([paper_row])[1]["no_net"] == 1, "pnl is not the live field"
    on_the_31st = dict(paper_row, closed_at="2026-10-31T12:00:00+00:00")
    assert m.score([on_the_31st], **paper)[0] == [], "before the paper window"
    assert len(m.score([dict(live_row, closed_at="2026-10-31T12:00:00+00:00")])[0]) == 1


def test_paper_status_counts_and_evaluate_waits_for_its_own_close(tmp_path, capsys):
    m = _m()
    f = tmp_path / "portfolio_1.json"
    f.write_text(json.dumps({"history": [_paper_close(d, 5.0) for d in range(45)]}))
    argv = ["--book", "paper", "--trades", str(f)]
    assert m.main(["status", *argv], now=datetime(2026, 12, 1, tzinfo=UTC)) == 0
    out = capsys.readouterr().out
    assert "paper book" in out and "scored 45" in out and "%" not in out
    assert m.main(["evaluate", *argv], now=datetime(2027, 1, 23, 12, tzinfo=UTC)) == 2
    capsys.readouterr()
    assert m.main(["evaluate", *argv], now=datetime(2027, 1, 24, tzinfo=UTC)) == 0
    res = json.loads(capsys.readouterr().out)
    assert res["book"] == "paper" and res["n"] == 45 and res["history_at_cap"] is False


def test_a_paper_history_at_its_cap_is_said(tmp_path, capsys):
    m = _m()
    f = tmp_path / "portfolio_1.json"
    f.write_text(json.dumps({"history": [_paper_close(0, 1.0)] * m.PAPER_HISTORY_CAP}))
    assert m.main(["status", "--book", "paper", "--trades", str(f)]) == 0
    assert "cap" in capsys.readouterr().out
    f.write_text(json.dumps({"history": [_paper_close(0, 1.0)] * 3}))
    assert m.main(["status", "--book", "paper", "--trades", str(f)]) == 0
    assert "cap" not in capsys.readouterr().out


def test_a_paper_book_with_no_history_does_not_read_as_empty(tmp_path, capsys):
    m = _m()
    f = tmp_path / "portfolio_1.json"
    f.write_text(json.dumps({"balance": 10000.0}))
    assert m.main(["status", "--book", "paper", "--trades", str(f)]) == 3
    assert "does not read" in capsys.readouterr().out


def test_the_paper_book_is_the_first_operators(monkeypatch):
    import dataclasses

    from bot.config import CONFIG
    m = _m()
    was = CONFIG.telegram
    try:
        object.__setattr__(CONFIG, "telegram", dataclasses.replace(was, chat_id="111, 222"))
        assert m.paper_path().name == "portfolio_111.json"
        object.__setattr__(CONFIG, "telegram", dataclasses.replace(was, chat_id=""))
        assert m.paper_path() is None
    finally:
        object.__setattr__(CONFIG, "telegram", was)


# ── Addendum 2: the paper book under resting limits ─────────────────────────

T0 = datetime(2026, 11, 10, 9, 0, tzinfo=UTC)


def _marker(tmp_path, since=T0, model="rests_like_live"):
    p = tmp_path / "paper_fill_model.json"
    p.write_text(json.dumps({"model": model, "since": since.isoformat()}))
    return p


def _opened(day_opened: float, day_closed: float, net: float = 5.0) -> dict:
    row = _paper_close(0, net)
    row["opened_at"] = (T0 + timedelta(days=day_opened)).isoformat()
    row["closed_at"] = (T0 + timedelta(days=day_closed)).isoformat()
    return row


def test_addendum_2_was_sealed_before_the_bot_could_write_its_record():
    """T0 is written by the first paper confirm the merged code makes, so a
    seal dated before this branch's code exists is before any T0."""
    sealed = (ROOT / "docs/research/prereg/equal_sizing_forward_2026-10/"
              "ADDENDUM_2.sealed_at").read_text().strip()
    assert datetime.fromisoformat(sealed.replace("Z", "+00:00")) < datetime(
        2026, 11, 9, tzinfo=UTC)
    import hashlib
    folder = ROOT / "docs/research/prereg/equal_sizing_forward_2026-10"
    digest = hashlib.sha256((folder / "ADDENDUM_2.md").read_bytes()).hexdigest()
    assert (folder / "ADDENDUM_2.sha256").read_text().split()[0] == digest


def test_only_trades_opened_at_or_after_t0_are_scored():
    m = _m()
    rows = [_opened(0, 1), _opened(-0.01, 1), _opened(5, 6), {**_opened(1, 2), "opened_at": None}]
    scored, left = m.score(rows, T0, T0 + timedelta(weeks=12), m.PAPER_NET_KEYS, T0)
    assert len(scored) == 2
    assert left["opened_under_the_old_fill_model"] == 1 and left["no_open_time"] == 1
    old, left_old = m.score(rows, T0, T0 + timedelta(weeks=12), m.PAPER_NET_KEYS,
                            opened_before=T0)
    assert len(old) == 1 and left_old["opened_under_the_new_fill_model"] == 2


def test_the_window_is_twelve_weeks_from_t0(tmp_path, capsys):
    m = _m()
    f = tmp_path / "portfolio_1.json"
    f.write_text(json.dumps({"history": [_opened(1, 2), _opened(83, 83.9), _opened(83, 84.1)]}))
    argv = ["--book", "paper-limits", "--trades", str(f)]
    end = T0 + timedelta(weeks=12)
    assert m.main(["status", *argv], marker=_marker(tmp_path)) == 0
    assert "scored 2" in capsys.readouterr().out
    assert m.main(["evaluate", *argv], now=end - timedelta(seconds=1),
                  marker=_marker(tmp_path)) == 2
    capsys.readouterr()
    assert m.main(["evaluate", *argv], now=end, marker=_marker(tmp_path)) == 0
    res = json.loads(capsys.readouterr().out)
    assert res["book"] == "paper-limits" and res["n"] == 2
    assert res["window"] == [T0.isoformat(), end.isoformat()]


def test_no_record_is_a_window_not_yet_open_and_a_bad_one_is_no_verdict(tmp_path, capsys):
    m = _m()
    f = tmp_path / "portfolio_1.json"
    f.write_text(json.dumps({"history": [_opened(1, 2)]}))
    argv = ["status", "--book", "paper-limits", "--trades", str(f)]
    assert m.main(argv, marker=tmp_path / "absent.json") == 0
    assert "has not opened" in capsys.readouterr().out
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    assert m.main(argv, marker=bad) == 3
    assert "unreadable" in capsys.readouterr().out
    assert m.main(argv, marker=_marker(tmp_path, model="something_else")) == 3


def test_addendum_1_is_reported_whole_and_split_at_t0(tmp_path, capsys):
    m = _m()
    f = tmp_path / "portfolio_1.json"
    # Addendum 1's window is fixed; T0 falls inside it.
    rows = [_opened(-3, -2), _opened(-1, 1), _opened(1, 2), _opened(2, 3)]
    f.write_text(json.dumps({"history": rows}))
    argv = ["--book", "paper", "--trades", str(f)]
    assert m.main(["status", *argv], marker=_marker(tmp_path)) == 0
    out = capsys.readouterr().out
    assert "scored 4" in out and "2 opened before it" in out and "2 opened under" in out
    assert m.main(["evaluate", *argv], now=m.PAPER_END, marker=_marker(tmp_path)) == 0
    res = json.loads(capsys.readouterr().out)
    assert res["n"] == 4 and res["fill_model_record"] == "read"
    assert res["by_fill_model"]["filled_at_once"]["n"] == 2
    assert res["by_fill_model"]["rests_like_live"]["n"] == 2


def test_the_bot_writes_the_record_once_and_never_rewrites_it(tmp_path, monkeypatch):
    from bot.core import paper_limits as pl
    from bot.utils import paths
    monkeypatch.setattr(paths, "state_path", lambda rel: tmp_path / rel)
    pl.mark_fill_model_start(now=T0.timestamp())
    m = _m()
    rec = tmp_path / pl.FILL_MODEL_MARKER
    assert m.fill_model_since(rec) == (T0, "read")
    pl.mark_fill_model_start(now=T0.timestamp() + 86400)
    assert m.fill_model_since(rec) == (T0, "read"), "a later confirm moved T0"
    rec.write_text("{not json")
    pl.mark_fill_model_start(now=T0.timestamp() + 86400)
    assert rec.read_text() == "{not json", "an unreadable record was replaced"
    assert pl.FILL_MODEL_MARKER == m.FILL_MODEL_MARKER and pl.FILL_MODEL == m.FILL_MODEL
