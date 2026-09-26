"""A failed nightly self-audit waits before it asks the model again.

`maybe_spawn` runs on every successful tick, and the audit is due inside the
configured UTC hour until `last_run_ts` says it ran. That stamp is written
only by a run that FINISHED, so a run that failed (a 529, a refusal, a
timeout, no model configured) left the audit due on the very next tick, and
the next. Driven: forty ticks ninety seconds apart inside the hour made forty
LLM calls, and a restart inside the hour started again.

A scheduled attempt is recorded now, before the run starts, and the next one
waits fifteen minutes: at most four attempts in the hour. The time is stored
beside `last_run_ts` so a restart inside the hour does not reset it, and kept
in memory too so a state file that cannot be written does not turn the bound
back off. An operator's `/audit run` calls `run()` directly and is neither
limited nor counted.
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

import bot.core.self_audit as self_audit_mod
import bot.llm.provider as provider
from bot.config import CONFIG
from bot.core.self_audit import SelfAudit

HOUR = 4
# 04:00 UTC on a fixed day.
T0 = 1_750_000_000 - (1_750_000_000 % 86400) + HOUR * 3600


@pytest.fixture
def audit_on():
    saved = {k: getattr(CONFIG, k) for k in ("self_audit_enabled", "self_audit_hour_utc")}
    object.__setattr__(CONFIG, "self_audit_enabled", True)
    object.__setattr__(CONFIG, "self_audit_hour_utc", HOUR)
    yield
    for k, v in saved.items():
        object.__setattr__(CONFIG, k, v)


@pytest.fixture
def llm(monkeypatch):
    calls: list = []

    async def failing(*a, **k):
        calls.append(1)
        raise RuntimeError("upstream 529 overloaded")
    monkeypatch.setattr(provider, "llm_complete", failing)
    return calls


class _Analyzer:
    def _resolve_llm_config(self):
        return object()

    def _build_client_for_config(self, cfg):
        return object()


ENGINE = SimpleNamespace(analyzer=_Analyzer(), live_executor=None)


async def _ticks(sa, times):
    spawned = []
    for t in times:
        if sa.maybe_spawn(ENGINE, now_ts=t):
            spawned.append(t)
        await asyncio.sleep(0.005)      # let the spawned run finish (and fail)
    return spawned


def _audit(path):
    return SelfAudit(state_file=str(path), run_backtest=lambda *a, **k: {})


class TestAFailedNight:
    @pytest.mark.asyncio
    async def test_forty_ticks_in_the_hour_make_four_attempts(self, tmp_path, audit_on, llm):
        sa = _audit(tmp_path / "sa.json")
        spawned = await _ticks(sa, [T0 + i * 90 for i in range(40)])

        assert spawned == [T0, T0 + 900, T0 + 1800, T0 + 2700], spawned
        assert len(llm) == 4, f"the model was asked {len(llm)} times in one hour"

    @pytest.mark.asyncio
    async def test_a_restart_inside_the_hour_does_not_start_again(self, tmp_path, audit_on, llm):
        path = tmp_path / "sa.json"
        await _ticks(_audit(path), [T0 + i * 90 for i in range(31)])   # to T0+2700
        before = len(llm)

        again = _audit(path)                                           # a restart
        spawned = await _ticks(again, [T0 + 2790 + i * 90 for i in range(9)])

        assert spawned == [], "a restart re-armed the retry at once"
        assert len(llm) == before
        assert json.loads(path.read_text())["last_attempt_ts"] == T0 + 2700

    @pytest.mark.asyncio
    async def test_an_unwritable_state_file_still_spaces_the_attempts(self, tmp_path, audit_on, llm):
        blocked = tmp_path / "a-file"
        blocked.write_text("")
        sa = _audit(blocked / "sa.json")          # its directory is a file
        spawned = await _ticks(sa, [T0 + i * 90 for i in range(40)])

        assert len(spawned) == 4, spawned
        assert len(llm) == 4


    @pytest.mark.asyncio
    async def test_the_newer_of_the_two_stamps_decides(self, tmp_path, audit_on, llm,
                                                        monkeypatch):
        # An attempt recorded at T0 reached the disk; the file then became
        # unwritable, so every later attempt lives in memory only. The disk's
        # stamp is the OLDER one, and reading it would re-arm the retry at once.
        path = tmp_path / "sa.json"
        path.write_text(json.dumps({"last_attempt_ts": T0}))

        def unwritable(*a, **k):
            raise OSError("read-only file system")
        monkeypatch.setattr(self_audit_mod, "atomic_write_json", unwritable)
        sa = _audit(path)
        spawned = await _ticks(sa, [T0 + 900, T0 + 1000, T0 + 1799, T0 + 1800])

        assert spawned == [T0 + 900, T0 + 1800], spawned
        assert json.loads(path.read_text())["last_attempt_ts"] == T0

    @pytest.mark.asyncio
    async def test_the_next_night_is_due_again(self, tmp_path, audit_on, llm):
        sa = _audit(tmp_path / "sa.json")
        await _ticks(sa, [T0 + i * 90 for i in range(40)])
        spawned = await _ticks(sa, [T0 + 86400])
        assert spawned == [T0 + 86400]


class TestWhatStaysTheSame:
    def test_an_unreadable_attempt_stamp_is_no_stamp(self, tmp_path, audit_on):
        path = tmp_path / "sa.json"
        for junk in ("junk", True, None, float("nan")):
            path.write_text(json.dumps({"last_attempt_ts": junk}))
            assert _audit(path).due(now_ts=T0) is True, junk

    def test_a_finished_run_still_blocks_for_the_night(self, tmp_path, audit_on):
        path = tmp_path / "sa.json"
        path.write_text(json.dumps({"last_run_ts": T0 - 3600}))
        assert _audit(path).due(now_ts=T0 + 1800) is False

    def test_the_retry_boundary(self, tmp_path, audit_on):
        path = tmp_path / "sa.json"
        path.write_text(json.dumps({"last_attempt_ts": T0}))
        sa = _audit(path)
        assert sa.due(now_ts=T0 + 899) is False
        assert sa.due(now_ts=T0 + 900) is True

    @pytest.mark.asyncio
    async def test_an_operator_run_is_not_limited_or_counted(self, tmp_path, audit_on, llm):
        path = tmp_path / "sa.json"
        sa = _audit(path)
        await _ticks(sa, [T0])
        assert await sa.run(ENGINE) is None          # the model failed again
        await sa.run(ENGINE)
        assert len(llm) == 3
        assert json.loads(path.read_text())["last_attempt_ts"] == T0
