"""The engine refreshes the website's cards, signal stream and Deep Scan itself.

18 October: the heartbeat was minutes old while the last setup cards and
signals were two and a half hours old, and the Deep Scan page waited for a
run nothing scheduled, because all three moved only when a person typed
/scan or /deepscan. `bot/core/website_refresh.py` runs the same reads on a
cadence from the engine's run loop.

Driven on a fake clock: what runs when, that a cadence of 0 turns a refresh
off and an unreadable one does not, that each run's outcome is recorded with
an exception's class and never its message, and that the website scan pushes
what it read and pushes nothing when it read nothing.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from bot.core import website_refresh as wr


class TestTheCadence:
    @pytest.mark.parametrize("raw,want", [
        (None, 15 * 60.0), ("", 15 * 60.0), ("5", 300.0), ("0.5", 30.0),
        ("abc", 15 * 60.0), ("nan", 15 * 60.0),
    ])
    def test_unset_or_unreadable_is_the_default(self, raw, want):
        env = {} if raw is None else {wr.SCAN_ENV: raw}
        assert wr.interval_s(wr.SCAN_ENV, wr.SCAN_DEFAULT_MIN, env) == want

    @pytest.mark.parametrize("raw", ["0", "-1", " 0 "])
    def test_zero_is_off(self, raw):
        assert wr.interval_s(wr.SCAN_ENV, wr.SCAN_DEFAULT_MIN, {wr.SCAN_ENV: raw}) is None

    def test_the_deep_scan_lands_inside_the_window_the_website_keeps_one(self):
        import re
        from pathlib import Path
        sync = (Path(__file__).resolve().parents[1] / "app" / "routes" / "sync.js").read_text()
        hours = int(re.search(r"DEEPSCAN_TTL_MS\s*=\s*(\d+)\s*\*\s*60\s*\*\s*60\s*\*\s*1000", sync).group(1))
        assert wr.DEEPSCAN_DEFAULT_MIN * 60 + wr.FIRST_DEEPSCAN_DELAY_S < hours * 3600, (
            "a default deep-scan cadence longer than the website's window leaves the page empty between runs")


def _drive(env, until_s):
    """Run the loop on a fake clock until ``until_s``; return the run log."""
    t = [0.0]
    ran: list[tuple[str, float]] = []

    def job(name):
        async def _j(engine):
            ran.append((name, t[0]))
            return 3
        return _j

    async def sleep(dt):
        t[0] += dt
        if t[0] > until_s:
            raise asyncio.CancelledError

    engine = SimpleNamespace()
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(wr.refresh_loop(engine, env=env, sleep=sleep, monotonic=lambda: t[0],
                                    jobs={"scan": job("scan"), "deepscan": job("deepscan")}))
    return ran, engine


class TestTheLoop:
    def test_both_run_after_startup_and_then_on_their_cadence(self):
        ran, engine = _drive({wr.SCAN_ENV: "15", wr.DEEPSCAN_ENV: "180"}, until_s=3 * 3600 + 600)
        scans = [at for n, at in ran if n == "scan"]
        deeps = [at for n, at in ran if n == "deepscan"]
        assert scans[0] >= wr.FIRST_SCAN_DELAY_S and deeps[0] >= wr.FIRST_DEEPSCAN_DELAY_S
        assert all(14 * 60 <= b - a <= 16 * 60 for a, b in zip(scans, scans[1:])), scans
        assert len(deeps) == 2 and 179 * 60 <= deeps[1] - deeps[0] <= 181 * 60, deeps
        assert engine._website_refresh["scan"]["outcome"] == "ok"

    def test_one_turned_off_leaves_the_other_running(self):
        ran, _ = _drive({wr.SCAN_ENV: "15", wr.DEEPSCAN_ENV: "0"}, until_s=7200)
        assert {n for n, _ in ran} == {"scan"}
        ran, _ = _drive({wr.SCAN_ENV: "0", wr.DEEPSCAN_ENV: "60"}, until_s=7200)
        assert {n for n, _ in ran} == {"deepscan"}

    def test_both_off_runs_nothing_and_returns(self):
        engine = SimpleNamespace()
        asyncio.run(wr.refresh_loop(engine, env={wr.SCAN_ENV: "0", wr.DEEPSCAN_ENV: "0"},
                                    sleep=lambda dt: (_ for _ in ()).throw(AssertionError("slept"))))
        assert not hasattr(engine, "_website_refresh")


class TestTheRecord:
    def _run(self, job):
        engine = SimpleNamespace()
        return asyncio.run(wr.run_once(engine, "scan", job, clock=lambda: 42.0)), engine

    def test_a_read_is_ok_with_its_count(self):
        async def job(e):
            return 7
        rec, engine = self._run(job)
        assert rec == {"at": 42.0, "outcome": "ok", "count": 7} and engine._website_refresh["scan"] is rec

    def test_nothing_read_is_empty_not_ok(self):
        async def job(e):
            return None
        rec, _ = self._run(job)
        assert rec["outcome"] == "empty"

    def test_a_failure_is_named_by_its_class_never_its_message(self):
        async def job(e):
            raise ConnectionError("https://api.example.invalid/?apiKey=SECRET123")
        rec, _ = self._run(job)
        assert rec["outcome"] == "failed" and rec["error"] == "ConnectionError"
        assert "SECRET123" not in repr(rec)


class TestTheWebsiteScan:
    def _rows(self, n):
        return [{"sym": f"S{i}/USDT", "score": i / 10} for i in range(n)]

    def test_it_pushes_the_top_rows_as_one_payload_and_every_row_beside_it(self, monkeypatch):
        from bot.skills import scan_skill as ss
        rows = self._rows(14) + [None, None]
        pushed = {}

        async def fake_rows(engine):
            return [dict(r) if r else r for r in rows], None

        monkeypatch.setattr(ss, "_fullscan_rows", fake_rows)
        monkeypatch.setattr(ss, "_build_scan_payload",
                            lambda results, engine: {"built_from": [r["sym"] for r in results]})
        monkeypatch.setattr(ss, "_push_scan_to_dashboard",
                            lambda results, engine, payload=None: pushed.update(results=results, payload=payload))
        n = asyncio.run(ss.website_scan(SimpleNamespace(), top_n=10))
        assert n == 14
        assert len(pushed["payload"]["built_from"]) == 10
        assert pushed["payload"]["built_from"][0] == "S13/USDT", "highest score first, as /scan orders it"
        assert len(pushed["results"]) == 14

    def test_nothing_read_pushes_nothing(self, monkeypatch):
        from bot.skills import scan_skill as ss

        async def fake_rows(engine):
            return [None, None, None], None

        monkeypatch.setattr(ss, "_fullscan_rows", fake_rows)
        monkeypatch.setattr(ss, "_push_scan_to_dashboard",
                            lambda *a, **k: (_ for _ in ()).throw(AssertionError("pushed an empty read")))
        assert asyncio.run(ss.website_scan(SimpleNamespace())) is None


def test_the_run_loop_starts_it_and_its_end_stops_it(monkeypatch):
    """Driven on the real methods, bound to a stand-in: the refresher starts
    once, and ends when the task that started it ends."""
    from bot.core.engine import RuneClawEngine
    started = []

    async def fake_loop(engine, **kw):
        started.append(engine)
        await asyncio.Event().wait()

    monkeypatch.setattr(wr, "refresh_loop", fake_loop)
    eng = SimpleNamespace()
    eng._start_website_refresh = RuneClawEngine._start_website_refresh.__get__(eng)
    eng._cancel_website_refresh = RuneClawEngine._cancel_website_refresh.__get__(eng)

    async def run():
        eng._start_website_refresh()
        eng._start_website_refresh()          # a second start is the same task
        await asyncio.sleep(0)
        return eng._website_refresh_task

    async def main():
        task = await asyncio.create_task(run())
        for _ in range(3):
            await asyncio.sleep(0)
        # Read inside the loop: asyncio.run cancels leftovers on its way out,
        # which would pass this for a refresher nothing ever stopped.
        return task.cancelled()

    assert asyncio.run(main()) is True, "the run that started it ended, so it ended"
    assert started == [eng], "started once"


def test_the_engine_run_loop_calls_it():
    """The one call site a test cannot reach without a whole engine run: the
    run loop starts the refresher beside the position guard."""
    import ast
    import inspect
    import textwrap

    from bot.core.engine import RuneClawEngine
    tree = ast.parse(textwrap.dedent(inspect.getsource(RuneClawEngine.run)))
    calls = {n.func.attr for n in ast.walk(tree)
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)}
    assert "_start_website_refresh" in calls
    stop = ast.parse(textwrap.dedent(inspect.getsource(RuneClawEngine.stop)))
    assert "_cancel_website_refresh" in {n.func.attr for n in ast.walk(stop)
                                         if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)}
