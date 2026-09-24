"""`/pocretest` and `/pocshadow` say what the setup paid when it was replayed.

A confirmed read prints an entry, a stop, a target and a verdict that the
levels clear the R floor, and until now nothing on either card said that the
same read, replayed over seventeen months of frozen snapshots, averaged
+0.03R a setup after fees with an interval straddling zero. That figure lived
in a document. It is a written artefact now, read three ways and pinned to
the snapshots it was read off and to the settings the live read uses, and
these are the rules that keep it from becoming a number typed into a card.
"""
from __future__ import annotations

import asyncio
import importlib.util
import json
import math
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace

import pytest

from bot.core import poc_retest_history as ph
from bot.core.poc_retest import PocRetestParams
from bot.core.poc_retest_record import MIN_SCORED_SETUPS, OUTCOMES

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("poc_retest_replay",
                                               ROOT / "scripts" / "poc_retest_replay.py")
pr = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(pr)

HASH = "a" * 64
H1 = 3_600_000
T0 = 1_767_571_200_000          # 2026-01-05T00:00Z, a Monday


# ── the one rule for the word ──────────────────────────────────────────────


class TestTheVerdictWord:
    def test_the_whole_interval_decides_it(self):
        n = MIN_SCORED_SETUPS
        assert ph.replay_verdict(n, (0.01, 0.5)) == "survives"
        assert ph.replay_verdict(n, (-0.5, -0.01)) == "does_not"
        assert ph.replay_verdict(n, (-0.25, 0.30)) == "no_edge"

    def test_an_interval_touching_zero_is_not_clear_of_it(self):
        n = MIN_SCORED_SETUPS
        assert ph.replay_verdict(n, (0.0, 0.5)) == "no_edge"
        assert ph.replay_verdict(n, (-0.5, 0.0)) == "no_edge"

    def test_the_floor_is_admitted_and_one_under_it_is_thin(self):
        n = MIN_SCORED_SETUPS
        assert ph.replay_verdict(n, (0.1, 0.2)) == "survives"
        assert ph.replay_verdict(n - 1, (0.1, 0.2)) == "thin"

    def test_no_interval_is_thin_however_many_were_scored(self):
        # the cluster bootstrap answers none under five week clusters
        assert ph.replay_verdict(10_000, None) == "thin"


# ── the reader ─────────────────────────────────────────────────────────────


def _root(tmp_path: Path, *names: str, h: str = HASH) -> Path:
    for name in names or ("benchmark/snap_a",):
        d = tmp_path / name
        d.mkdir(parents=True, exist_ok=True)
        (d / "manifest.json").write_text(json.dumps({"dataset_hash": h}))
    return tmp_path


def _window(**over) -> dict:
    live = ph.live_settings()
    w = {
        "label": "17 months", "since": None, "recorded_at": "2026-09-24T17:00:00+00:00",
        "code_sha": "006a8ec3" + "0" * 32, "params": dict(live["params"]),
        "fees": dict(live["fees"]),
        "datasets": [{"dataset": "benchmark/snap_a", "dataset_hash": HASH}],
        "first_retest": "2025-02-27T10:00:00+00:00",
        "last_retest": "2026-07-04T18:00:00+00:00",
        "armed": 278, "scored": 262,
        "outcomes": {"target": 63, "stop": 199, "ambiguous": 0, "open": 0,
                     "not_triggered": 16, "unscored": 0},
        "mean_r": 0.031, "interval": [-0.25, 0.30], "clusters": 71, "level": 0.95,
        "verdict": "no_edge",
    }
    w.update(over)
    return w


def _write(tmp_path: Path, doc) -> Path:
    p = tmp_path / "benchmark" / "poc_retest" / "result.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(doc if isinstance(doc, str) else json.dumps(doc))
    return p


def _read(tmp_path: Path, *windows: dict, root_names=None, **kw):
    root = _root(tmp_path, *(root_names or ()), **kw)
    p = _write(tmp_path, {"kind": ph.KIND, "windows": list(windows) or [_window()]})
    return ph.history_on_record(p, root=root)


class TestTheReading:
    def test_a_good_artefact_reads(self, tmp_path):
        r = _read(tmp_path)
        assert r.state == "read", r.reason
        (w,) = r.windows
        assert (w.armed, w.scored, w.n_target, w.n_stop) == (278, 262, 63, 199)
        assert w.mean_r == pytest.approx(0.031)
        assert w.interval == (-0.25, 0.30) and w.clusters == 71
        assert w.verdict == "no_edge" and w.datasets == ("benchmark/snap_a",)
        assert r.path == "benchmark/poc_retest/result.json"

    def test_no_file_is_none_and_not_no_edge(self, tmp_path):
        r = ph.history_on_record(tmp_path / "nope.json", root=tmp_path)
        assert r.state == "none" and r.windows == ()

    @pytest.mark.parametrize("doc,why", [
        ("{not json", "could not be parsed (JSONDecodeError)"),
        ([1, 2], "not a POC-retest replay artefact"),
        ({"kind": "portfolio_walk_forward", "windows": [1]}, "not a POC-retest"),
        ({"kind": ph.KIND}, "holds no replay window"),
        ({"kind": ph.KIND, "windows": []}, "holds no replay window"),
        ({"kind": ph.KIND, "windows": [7]}, "a window is not an object"),
    ])
    def test_a_file_that_cannot_answer_is_unreadable(self, tmp_path, doc, why):
        _root(tmp_path)
        r = ph.history_on_record(_write(tmp_path, doc), root=tmp_path)
        assert r.state == "unreadable" and why in r.reason, r

    def test_a_directory_where_the_file_should_be_does_not_raise(self, tmp_path):
        p = tmp_path / "result.json"
        p.mkdir()
        r = ph.history_on_record(p, root=tmp_path)
        assert r.state == "unreadable" and "IsADirectoryError" in r.reason

    @pytest.mark.parametrize("over,why", [
        ({"label": ""}, "carries no label"),
        ({"label": 3}, "carries no label"),
        ({"datasets": []}, "names no snapshot"),
        ({"datasets": [{"dataset": "benchmark/snap_a"}]}, "without its hash"),
        ({"datasets": [{"dataset_hash": HASH}]}, "without its hash"),
        ({"datasets": ["benchmark/snap_a"]}, "without its hash"),
        ({"outcomes": None}, "carries no outcome counts"),
        ({"armed": True}, "not a count"),
        ({"armed": -1}, "not a count"),
        ({"scored": "262"}, "not a count"),
        ({"outcomes": {"target": 63, "stop": 199, "ambiguous": 0, "open": 0,
                       "not_triggered": 16}}, "not a count"),
        ({"scored": 300}, "do not add up"),
        ({"armed": 279}, "do not add up"),
        ({"mean_r": None}, "mean does not match"),
        ({"mean_r": "0.03"}, "mean does not match"),
        ({"interval": [0.3, -0.25]}, "not an interval"),
        ({"interval": [-0.25]}, "not an interval"),
        ({"interval": [-0.25, "0.3"]}, "not an interval"),
        ({"interval": {"lo": -0.25, "hi": 0.3}}, "not an interval"),
        ({"clusters": None}, "without its cluster count"),
        ({"clusters": True}, "without its cluster count"),
        ({"interval": None, "verdict": "thin"}, "without its cluster count"),
        ({"verdict": "too_thin"}, "cannot place"),
        ({"verdict": "survives"}, "says 'survives' where its own interval"),
    ])
    def test_a_window_that_cannot_answer_is_unreadable(self, tmp_path, over, why):
        r = _read(tmp_path, _window(**over))
        assert r.state == "unreadable" and why in r.reason, r

    def test_a_nan_mean_is_not_a_mean(self, tmp_path):
        _root(tmp_path)
        text = json.dumps({"kind": ph.KIND, "windows": [_window()]}).replace(
            '"mean_r": 0.031', '"mean_r": NaN')
        r = ph.history_on_record(_write(tmp_path, text), root=tmp_path)
        assert r.state == "unreadable" and "mean does not match" in r.reason

    def test_a_scored_count_of_zero_carries_no_mean(self, tmp_path):
        zero = _window(armed=16, scored=0, mean_r=None, interval=None, clusters=None,
                       verdict="thin",
                       outcomes={k: (16 if k == "not_triggered" else 0) for k in OUTCOMES})
        assert _read(tmp_path, zero).state == "read"
        assert _read(tmp_path, dict(zero, mean_r=0.0)).state == "unreadable"

    def test_a_snapshot_whose_manifest_moved_is_not_pinned(self, tmp_path):
        r = _read(tmp_path, h="b" * 64)
        assert r.state == "unreadable"
        assert "read off benchmark/snap_a at aaaaaaaaaaaa" in r.reason
        assert "now names bbbbbbbbbbbb" in r.reason

    def test_a_snapshot_nobody_can_read_cannot_pin(self, tmp_path):
        r = _read(tmp_path, _window(datasets=[{"dataset": "benchmark/gone",
                                                "dataset_hash": HASH}]))
        assert r.state == "unreadable" and "manifest of benchmark/gone" in r.reason

    def test_every_snapshot_of_a_window_is_pinned(self, tmp_path):
        two = [{"dataset": "benchmark/snap_a", "dataset_hash": HASH},
               {"dataset": "benchmark/snap_b", "dataset_hash": "c" * 64}]
        r = _read(tmp_path, _window(datasets=two),
                  root_names=("benchmark/snap_a", "benchmark/snap_b"))
        assert r.state == "unreadable" and "snap_b" in r.reason

    def test_one_bad_window_makes_the_whole_artefact_unreadable(self, tmp_path):
        r = _read(tmp_path, _window(), _window(label="fresh", verdict="survives"))
        assert r.state == "unreadable" and "'fresh'" in r.reason and r.windows == ()

    @pytest.mark.parametrize("group,key,value", [
        ("params", "atr_buffer", 0.10),
        ("params", "retest_window", 8),
        ("params", "min_net_r", 3.0),
        ("params", "max_stop_atr", 1.5),
        ("params", "swing_order", 4),
        ("fees", "entry_pct", 0.02),
        ("fees", "exit_pct", 0.1),
    ])
    def test_other_settings_are_named_and_never_read_as_these(self, tmp_path,
                                                              group, key, value):
        w = _window()
        w[group] = dict(w[group], **{key: value})
        r = _read(tmp_path, w)
        assert r.state == "other_params" and key in r.reason and r.windows == ()

    def test_a_float_round_trip_is_not_a_different_setting(self, tmp_path):
        w = _window()
        w["params"] = dict(w["params"], atr_buffer=w["params"]["atr_buffer"] + 1e-12)
        assert _read(tmp_path, w).state == "read"

    def test_a_setting_the_window_does_not_record_is_not_assumed(self, tmp_path):
        w = _window()
        w["params"] = {k: v for k, v in w["params"].items() if k != "atr_period"}
        r = _read(tmp_path, w)
        assert r.state == "other_params" and "does not record atr_period" in r.reason
        r = _read(tmp_path, _window(fees=None))
        assert r.state == "other_params" and "carries no fees" in r.reason

    def test_a_stale_snapshot_outranks_other_settings(self, tmp_path):
        """Data no longer on disk is not a measurement of anything, so the pin
        is asked before the settings: 'other parameters' would describe a
        figure nobody can reproduce as though it were merely a different one."""
        w = _window()
        w["params"] = dict(w["params"], atr_buffer=0.5)
        r = _read(tmp_path, w, h="b" * 64)
        assert r.state == "unreadable"

    def test_the_live_settings_are_the_live_reads(self):
        live = ph.live_settings()
        assert live["params"] == asdict(PocRetestParams())
        assert live["fees"] == {"entry_pct": ph.entry_rate_pct(None),
                                "exit_pct": ph.exit_rate_pct()}


# ── the lines a card appends ───────────────────────────────────────────────


class TestTheNote:
    def test_a_read_window_prints_its_figures_and_its_word(self, tmp_path):
        note = ph.history_note(_read(tmp_path))
        assert "📚 <b>Replayed history</b>" in note
        assert ("• <b>17 months</b> (retests 2025-02-27 to 2026-07-04): "
                "<b>+0.03R</b> per setup over 262 scored (63 target, 199 stop), "
                "95% -0.25 to +0.30 over 71 week clusters — no edge measurable "
                "either way") in note
        assert "not a forecast" in note and "recorded 2026-09-24" in note
        assert "$" not in note

    @pytest.mark.parametrize("interval,verdict,words", [
        ([0.05, 0.4], "survives", "it paid after fees"),
        ([-0.6, -0.1], "does_not", "it lost after fees"),
    ])
    def test_each_word_has_its_own_sentence(self, tmp_path, interval, verdict, words):
        note = ph.history_note(_read(tmp_path, _window(interval=interval, verdict=verdict)))
        assert note.splitlines()[1].endswith(words), note

    def test_a_thin_window_says_so_and_quotes_no_interval(self, tmp_path):
        w = _window(interval=None, clusters=None, verdict="thin")
        line = ph.history_note(_read(tmp_path, w)).splitlines()[1]
        assert line.endswith("too few setups to say") and "95%" not in line

    def test_a_window_with_nothing_scored_quotes_no_r(self, tmp_path):
        zero = _window(armed=16, scored=0, mean_r=None, interval=None, clusters=None,
                       verdict="thin", first_retest=None, last_retest=None,
                       outcomes={k: (16 if k == "not_triggered" else 0) for k in OUTCOMES})
        line = ph.history_note(_read(tmp_path, zero)).splitlines()[1]
        assert "16 armed, none scored, so no R is quoted" in line
        assert "no retest in it" in line and "+0.00R" not in line

    def test_every_window_gets_its_own_line(self, tmp_path):
        fresh = _window(label="after 2026-07-06", armed=45, scored=45, mean_r=-0.39,
                        interval=[-0.74, 0.01], clusters=11,
                        outcomes={"target": 8, "stop": 37, "ambiguous": 0, "open": 0,
                                  "not_triggered": 0, "unscored": 0})
        lines = ph.history_note(_read(tmp_path, _window(), fresh)).splitlines()
        assert lines[1].startswith("• <b>17 months</b>")
        assert lines[2].startswith("• <b>after 2026-07-06</b>") and "-0.39R" in lines[2]

    def test_a_label_is_escaped(self, tmp_path):
        note = ph.history_note(_read(tmp_path, _window(label="<b>x & y")))
        assert "&lt;b&gt;x &amp; y" in note and "<b><b>" not in note

    def test_none_is_said_and_is_not_no_edge(self):
        note = ph.history_note(ph.HistoryReading("none", "x", "p"))
        assert "No replayed history" in note and "no edge" not in note
        assert "R" not in note.replace("Replayed", "")

    def test_unreadable_names_its_reason_escaped_and_is_not_no_history(self):
        note = ph.history_note(ph.HistoryReading("unreadable", "window '<x>' broke", "p"))
        assert "could not be read: window '&lt;x&gt;' broke" in note
        assert 'That is not "no history"' in note

    def test_other_settings_are_said_to_describe_another_setup(self):
        note = ph.history_note(ph.HistoryReading("other_params", "atr_buffer 0.1 where", "p"))
        assert "measured other settings (atr_buffer 0.1 where)" in note
        assert "says nothing about the ones this read uses" in note
        assert "Replayed history</b>:" not in note


# ── where the cards take it ────────────────────────────────────────────────


def _seen(state):
    return SimpleNamespace(setup=SimpleNamespace(read=SimpleNamespace(state=state)),
                           armed=None, not_armed=None, scored=0, record_error=None)


class TestTheCards:
    def test_only_a_confirmed_read_carries_the_history(self):
        from bot.skills.scan_commands import _history_note
        reading = ph.HistoryReading("none", "", "p")
        assert _history_note(_seen("confirmed"), reading).startswith("\n\n📚 No replayed")
        for state in ("no_breakout", "awaiting_retest", "expired", "no_leg", None):
            assert _history_note(_seen(state), reading) == "", state
        assert _history_note(SimpleNamespace(setup=None), reading) == ""

    def _stand_in(self, sent):
        async def _send(_update, text, **_kw):
            sent.append(text)

        async def _send_error(_update, what, exc):
            sent.append(f"ERROR {what}: {exc!r}")

        async def get_exchange():
            return object()
        return SimpleNamespace(_send=_send, _send_error=_send_error,
                               engine=SimpleNamespace(get_exchange=get_exchange))

    def test_the_read_command_sends_the_history_under_a_confirmed_card(self, monkeypatch):
        from bot.core import poc_retest_scan
        from bot.skills.scan_commands import ScanCommands
        seen = _seen("confirmed")

        async def observe(_ex, symbol):
            assert symbol == "SOL/USDT"
            return seen
        monkeypatch.setattr(poc_retest_scan, "observe_setup", observe)
        monkeypatch.setattr(poc_retest_scan, "setup_card", lambda _s: "CARD")
        monkeypatch.setattr(ph, "history_on_record",
                            lambda: ph.HistoryReading("other_params", "atr_buffer x", "p"))
        sent: list[str] = []
        asyncio.run(ScanCommands._cmd_pocretest.__wrapped__(
            self._stand_in(sent), SimpleNamespace(), SimpleNamespace(args=["sol"])))
        (text,) = sent
        assert text.startswith("CARD\n\n📚 The replayed history on record measured "
                               "other settings (atr_buffer x)"), text

    def test_the_read_command_sends_no_history_under_a_read_with_no_setup(self, monkeypatch):
        from bot.core import poc_retest_scan
        from bot.skills.scan_commands import ScanCommands

        async def observe(_ex, _symbol):
            return _seen("awaiting_retest")
        monkeypatch.setattr(poc_retest_scan, "observe_setup", observe)
        monkeypatch.setattr(poc_retest_scan, "setup_card", lambda _s: "CARD")
        sent: list[str] = []
        asyncio.run(ScanCommands._cmd_pocretest.__wrapped__(
            self._stand_in(sent), SimpleNamespace(), SimpleNamespace(args=[])))
        assert sent == ["CARD"]

    def test_the_shadow_command_sends_the_history_under_the_record(self, monkeypatch):
        from bot.core import poc_retest_record
        from bot.skills.scan_commands import ScanCommands
        monkeypatch.setattr(poc_retest_record, "shadow_reading", lambda: ([], "V"))
        monkeypatch.setattr(poc_retest_record, "shadow_card", lambda v: f"RECORD {v}")
        monkeypatch.setattr(ph, "history_on_record",
                            lambda: ph.HistoryReading("none", "", "p"))
        sent: list[str] = []
        asyncio.run(ScanCommands._cmd_pocshadow.__wrapped__(
            self._stand_in(sent), SimpleNamespace(), SimpleNamespace(args=[])))
        (text,) = sent
        assert text.startswith("RECORD V\n\n📚 No replayed history"), text


# ── the writer ─────────────────────────────────────────────────────────────


def _collected(root: Path, name: str, n: int, *, wins: bool = True, sha: str = "s" * 40,
               with_hash: object = True, h: str = HASH) -> str:
    """`n` setups on one symbol, one every 40 hours, each a win or a stop, as
    `collect` would have written them off `root/benchmark/<name>`."""
    plan = [(100 + k * 40, "target" if wins else "stop") for k in range(n)]
    return _collected_plan(root, name, plan, sha=sha, with_hash=with_hash, h=h)


def _collected_plan(root: Path, name: str, plan, *, total: int = 2000, sha: str = "s" * 40,
                    with_hash: object = True, h: str = HASH) -> str:
    """One setup per (bar, outcome) in `plan`: a target, a stop, or a setup
    whose entry is never reached. Each setup's scoring window must hold no
    other setup's trigger bars, or the outcomes are not the ones planned."""
    snap = root / "benchmark" / name
    snap.mkdir(parents=True, exist_ok=True)
    (snap / "manifest.json").write_text(json.dumps({"dataset_hash": h}))
    bars = [[T0 + k * H1, 99.0, 99.5, 98.5] for k in range(total)]
    confirmed = []
    for t, outcome in plan:
        if outcome != "none":
            bars[t + 1] = [T0 + (t + 1) * H1, 99.0, 100.5, 99.0]
            bars[t + 2] = ([T0 + (t + 2) * H1, 105.0, 111.0, 104.0] if outcome == "target"
                           else [T0 + (t + 2) * H1, 99.0, 99.5, 90.0])
        confirmed.append({"t": t, "retest_t": t, "side": "long", "entry": 100.0,
                          "stop": 98.0, "target": 110.0, "atr": 2.0})
    key = pr._read_key(PocRetestParams().atr_buffer, PocRetestParams().retest_window)
    d = {"dataset": name, "entry_bars": pr.ENTRY_BARS, "code_sha": sha,
         "bars": {"S": bars}, "reads": {key: {"S": {"states": {}, "confirmed": confirmed}}}}
    if with_hash is not False:
        d["dataset_path"] = str(snap)
        # `collect` writes None for a manifest it could not read
        d["dataset_hash"] = h if with_hash else None
    f = root / f"{name}.json"
    f.write_text(json.dumps(d))
    return str(f)


class TestTheWriter:
    def test_collect_records_the_snapshot_it_read_and_the_commit(self, monkeypatch, tmp_path):
        from bot.backtest import snapshot
        snap = tmp_path / "planted"
        snap.mkdir()
        (snap / "manifest.json").write_text(json.dumps({"dataset_hash": HASH}))
        monkeypatch.setattr(snapshot, "load_dataset", lambda _d: {})
        monkeypatch.setattr(pr, "code_sha", lambda: "c" * 40)
        d = pr.collect(str(snap))
        assert d["dataset_path"] == str(snap) and d["dataset_hash"] == HASH
        assert d["code_sha"] == "c" * 40 and d["collected_at"].endswith("+00:00")
        assert pr.collect(str(tmp_path / "no_manifest"))["dataset_hash"] is None

    def test_a_window_round_trips_through_the_reader(self, monkeypatch, tmp_path):
        monkeypatch.setattr(pr, "_ROOT", tmp_path)
        files = [_collected(tmp_path, "a", 20), _collected(tmp_path, "b", 20, wins=False)]
        w = pr.record_window(files, PocRetestParams(), "planted")
        assert w["datasets"] == [{"dataset": "benchmark/a", "dataset_hash": HASH},
                                 {"dataset": "benchmark/b", "dataset_hash": HASH}]
        assert w["armed"] == w["scored"] == 40
        assert w["outcomes"]["target"] == 20 and w["outcomes"]["stop"] == 20
        assert w["code_sha"] == "s" * 40 and w["since"] is None
        assert w["first_retest"].startswith("2026-01-09") and w["verdict"] == \
            ph.replay_verdict(w["scored"], tuple(w["interval"]) if w["interval"] else None)
        out = tmp_path / "benchmark" / "poc_retest" / "result.json"
        pr.write_record(out, w)
        r = ph.history_on_record(out, root=tmp_path)
        assert r.state == "read", r.reason
        assert r.windows[0].mean_r == pytest.approx(w["mean_r"])

    def test_the_word_is_decided_by_the_scored_count_not_the_armed(self, monkeypatch,
                                                                   tmp_path):
        """Nine scored setups a week apart and nine that never triggered: 18
        armed clears the floor, 9 scored does not. A setup nothing traded is
        not a sample of what the setup pays."""
        monkeypatch.setattr(pr, "_ROOT", tmp_path)
        scored = [(100 + k * 300, "target" if k % 2 else "stop") for k in range(9)]
        idle = [(100 + k * 300 + 150, "none") for k in range(9)]
        f = _collected_plan(tmp_path, "a", scored + idle, total=3000)
        w = pr.record_window([f], PocRetestParams(), "x")
        assert w["armed"] == 18 and w["scored"] == 9 < MIN_SCORED_SETUPS <= 18
        assert w["interval"] is not None, "nine weeks of clusters: an interval exists"
        assert w["verdict"] == "thin"

    def test_since_keeps_only_later_retests_and_is_written_as_utc(self, monkeypatch, tmp_path):
        monkeypatch.setattr(pr, "_ROOT", tmp_path)
        f = _collected(tmp_path, "a", 20)
        cut = T0 + 500 * H1
        w = pr.record_window([f], PocRetestParams(), "late", since="2026-01-25T20:00:00")
        assert w["since"] == "2026-01-25T20:00:00+00:00"
        later = [k for k in range(20) if T0 + (100 + k * 40) * H1 > cut]
        assert 0 < len(later) < 20 and w["armed"] == len(later)
        assert w["first_retest"] == pr.datetime.fromtimestamp(
            (T0 + (100 + later[0] * 40) * H1) / 1000, tz=pr.timezone.utc).isoformat()

    def test_the_params_and_fees_are_the_ones_it_was_measured_at(self, monkeypatch, tmp_path):
        from dataclasses import replace
        monkeypatch.setattr(pr, "_ROOT", tmp_path)
        f = _collected(tmp_path, "a", 20)
        p = replace(PocRetestParams(), min_net_r=1.5)
        w = pr.record_window([f], p, "other")
        assert w["params"] == asdict(p)
        assert w["fees"] == {"entry_pct": pr.entry_rate_pct(None), "exit_pct": pr.exit_rate_pct()}
        out = tmp_path / "r.json"
        pr.write_record(out, w)
        assert ph.history_on_record(out, root=tmp_path).state == "other_params"

    @pytest.mark.parametrize("with_hash", [False, None],
                             ids=["an older collect", "a manifest collect could not read"])
    def test_a_collect_without_the_snapshots_hash_is_refused(self, monkeypatch, tmp_path,
                                                            with_hash):
        monkeypatch.setattr(pr, "_ROOT", tmp_path)
        f = _collected(tmp_path, "a", 5, with_hash=with_hash)
        with pytest.raises(SystemExit, match="re-run collect"):
            pr.record_window([f], PocRetestParams(), "x")

    def test_files_from_two_commits_are_refused(self, monkeypatch, tmp_path):
        monkeypatch.setattr(pr, "_ROOT", tmp_path)
        a = _collected(tmp_path, "a", 5)
        b = _collected(tmp_path, "b", 5, sha="t" * 40)
        with pytest.raises(SystemExit, match="different commits"):
            pr.record_window([a, b], PocRetestParams(), "x")

    def test_a_snapshot_outside_the_repository_is_refused(self, monkeypatch, tmp_path):
        monkeypatch.setattr(pr, "_ROOT", tmp_path / "repo")
        f = _collected(tmp_path, "a", 5)
        with pytest.raises(SystemExit, match="not a snapshot under the repository"):
            pr.record_window([f], PocRetestParams(), "x")

    def test_a_label_is_replaced_in_place_and_a_new_one_appended(self, tmp_path):
        out = tmp_path / "r.json"
        pr.write_record(out, {"label": "a", "v": 1})
        pr.write_record(out, {"label": "b", "v": 1})
        pr.write_record(out, {"label": "a", "v": 2})
        doc = json.loads(out.read_text())
        assert doc["kind"] == ph.KIND
        assert doc["windows"] == [{"label": "a", "v": 2}, {"label": "b", "v": 1}]

    @pytest.mark.parametrize("existing", ["{broken", json.dumps({"kind": "other"}),
                                          json.dumps({"kind": ph.KIND, "windows": {}})])
    def test_an_artefact_it_cannot_read_is_never_overwritten(self, tmp_path, existing):
        out = tmp_path / "r.json"
        out.write_text(existing)
        with pytest.raises(SystemExit):
            pr.write_record(out, {"label": "a"})
        assert out.read_text() == existing

    def test_the_cli_reads_back_what_it_wrote(self, monkeypatch, tmp_path, capsys):
        monkeypatch.setattr(pr, "_ROOT", tmp_path)
        f = _collected(tmp_path, "a", 20)
        out = tmp_path / "benchmark" / "poc_retest" / "result.json"
        rc = pr.main(["record", "--label", " planted ", "--out", str(out), f])
        text = capsys.readouterr().out
        assert rc == 0 and f"{out}: read" in text and "  planted: armed 20" in text
        # a window whose snapshot moved since is written, read back, refused,
        # and the exit status says so
        (tmp_path / "benchmark" / "a" / "manifest.json").write_text(
            json.dumps({"dataset_hash": "b" * 64}))
        rc = pr.main(["record", "--label", "planted", "--out", str(out), f])
        assert rc == 1 and ": unreadable (" in capsys.readouterr().out

    def test_a_blank_label_is_refused(self, tmp_path):
        with pytest.raises(SystemExit):
            pr.main(["record", "--label", "  ", "--out", str(tmp_path / "r.json"), "x.json"])


# ── the committed artefact ─────────────────────────────────────────────────


class TestTheArtefactOnRecord:
    def test_it_reads_at_the_live_settings_and_the_committed_snapshots(self):
        r = ph.history_on_record()
        assert r.state == "read", r.reason
        labels = [w.label for w in r.windows]
        assert len(labels) == 2, labels

    def test_the_document_quotes_the_artefacts_own_figures(self):
        """`docs/FROZEN_BENCHMARK.md` states the headline of each window; a
        re-record that moves one must move the prose with it, or the two are
        two answers about one replay."""
        doc = (ROOT / "docs" / "FROZEN_BENCHMARK.md").read_text(encoding="utf-8")
        for w in ph.history_on_record().windows:
            assert w.mean_r is not None and w.interval is not None
            lo, hi = w.interval
            quoted = (f"{w.mean_r:+.2f} [{lo:+.2f}, {hi:+.2f}]").replace("-", "−")
            assert quoted in doc, (w.label, quoted)
            assert f"| {w.scored} |" in doc, (w.label, w.scored)

    def test_its_numbers_are_finite(self):
        for w in ph.history_on_record().windows:
            assert w.mean_r is None or math.isfinite(w.mean_r)
