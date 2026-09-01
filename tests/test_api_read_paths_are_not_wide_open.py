"""Reads that carry money must be guarded like the writes that produce them.

Two findings from the audit's confirmed-not-remediated tier, plus a correction
to one of them.

1. "dashboard_api.py authenticates the snapshot WRITE but not the READ".
   True. The POST checked `X-API-Key` with `hmac.compare_digest`; the GET
   checked nothing and returned `system.equity` — the operator's real live
   account equity in dollars — plus per-trader total/daily P&L, commission,
   OPEN POSITIONS and recent trades. Open positions are worse than a privacy
   leak: someone who knows what you hold can trade against it.

   LATENT, and that is the trap rather than a reason to skip it. Nothing in
   this repo deploys the server, and the pusher only starts when an operator
   sets DASHBOARD_API_KEY and DASHBOARD_URL. The person exposed is whoever
   turns it on — and a guarded write path entitles them to assume the API is
   guarded. Nothing in the repo reads /api/snapshot, so requiring the key
   breaks no caller.

2. "unauthenticated /api/lab/run allows unbounded subprocess/job growth".
   THE MECHANISM IS WRONG. Subprocess concurrency is firmly bounded: one job
   at a time (409), a submit gap (429), whitelisted datasets, symbols checked
   against the snapshot manifest and capped at 4, every numeric clamped, and a
   subprocess with a hard timeout. What actually grew without limit was the
   RECORD DICT — `_jobs[job_id] = {...}` with no delete anywhere, under a
   comment reading "kept for the session (small dicts)", which is an
   assumption and not a bound.

   The auth half is real but is NOT fixed here: app/routes/lab.js already
   requires login and rate-limits per IP, so the intended path is
   authenticated and the bridge is exposed only to whoever can reach it
   directly. Closing that needs DASHBOARD_TOKEN on both sides — a deployment
   change that breaks a working Lab if only one side gets it — so it is the
   operator's call, not a silent one.
"""
from __future__ import annotations

import importlib
import sys

import pytest

# ── dashboard_api: the money reads ────────────────────────────────────────

class _FakeHeaders(dict):
    def get(self, k, default=""):
        return dict.get(self, k, default)


def _handler_with_key(api_key: str, sent_key: str | None):
    """A handler instance with the auth surface planted, nothing else."""
    import dashboard_api as da
    h = da.Handler.__new__(da.Handler)
    h.headers = _FakeHeaders({} if sent_key is None else {"X-API-Key": sent_key})
    return da, h


@pytest.fixture(autouse=True)
def _reload_dashboard_api(monkeypatch):
    monkeypatch.setenv("DASHBOARD_API_KEY", "k" * 32)
    sys.modules.pop("dashboard_api", None)
    importlib.import_module("dashboard_api")
    yield
    sys.modules.pop("dashboard_api", None)


def test_the_right_key_is_authorised():
    da, h = _handler_with_key("k" * 32, "k" * 32)
    assert h._read_authorised() is True


def test_no_key_is_refused():
    da, h = _handler_with_key("k" * 32, None)
    assert h._read_authorised() is False


def test_a_wrong_key_is_refused():
    da, h = _handler_with_key("k" * 32, "x" * 32)
    assert h._read_authorised() is False


def test_an_unconfigured_server_authorises_nobody():
    """Fail CLOSED. With no key set there is nothing to compare against, and
    "no key configured" must not mean "everybody is allowed"."""
    import dashboard_api as da
    prev = da.API_KEY
    try:
        da.API_KEY = ""
        h = da.Handler.__new__(da.Handler)
        h.headers = _FakeHeaders({"X-API-Key": "anything"})
        assert h._read_authorised() is False
    finally:
        da.API_KEY = prev


def test_the_money_reads_are_gated_and_health_is_not():
    """Gated: equity, P&L, positions. Open: a status word and a count.

    /api/health stays public deliberately — a liveness probe carrying a
    status, a timestamp and a COUNT is exactly what the public-surface rule
    permits, and locking it would make the endpoint whose job is to say
    whether things work unable to say so.
    """
    import inspect

    import dashboard_api as da
    src = inspect.getsource(da.Handler.do_GET)
    code = "\n".join(ln.split("#")[0] for ln in src.splitlines())
    i = code.index("_read_authorised")
    gate = code[:i]
    assert "/api/snapshot" in gate and "/api/feed" in gate, (
        "a money endpoint is not behind the read gate"
    )
    assert "/api/health" not in gate, (
        "the liveness probe was locked; it carries no dollars"
    )


# ── lab: the record registry ──────────────────────────────────────────────

def test_the_job_registry_is_bounded():
    from bot.api import lab

    lab._jobs.clear()
    lab._running_id = None
    for i in range(lab._MAX_JOBS + 25):
        lab._jobs[f"job-{i}"] = {"status": "done", "started_at": float(i)}
        lab._prune_jobs()
    assert len(lab._jobs) <= lab._MAX_JOBS, (
        f"{len(lab._jobs)} records kept — the dict grows for the life of the "
        "process, one entry every few seconds"
    )


def test_pruning_keeps_the_most_recent():
    from bot.api import lab

    lab._jobs.clear()
    lab._running_id = None
    for i in range(lab._MAX_JOBS + 10):
        lab._jobs[f"job-{i}"] = {"status": "done", "started_at": float(i)}
        lab._prune_jobs()
    newest = f"job-{lab._MAX_JOBS + 9}"
    assert newest in lab._jobs, "pruning evicted the job just created"


def test_the_running_job_is_never_evicted():
    """Its poller holds that id; a 404 mid-run reads as 'your backtest vanished'."""
    from bot.api import lab

    lab._jobs.clear()
    lab._running_id = "job-0"
    lab._jobs["job-0"] = {"status": "running", "started_at": 0.0}
    for i in range(1, lab._MAX_JOBS + 30):
        lab._jobs[f"job-{i}"] = {"status": "done", "started_at": float(i)}
        lab._prune_jobs()
    assert "job-0" in lab._jobs, "the running job was pruned out from under its poller"
    lab._running_id = None


def test_a_crashed_lab_job_does_not_echo_the_exception_text():
    """/lab/status hands this string straight back to the caller."""
    import inspect

    from bot.api import lab
    src = inspect.getsource(lab._run_job)
    code = "\n".join(ln.split("#")[0] for ln in src.splitlines())
    assert "Lab job crashed: {exc}" not in code, (
        "the raw exception text — paths, internals — is returned by /lab/status"
    )
    assert "type(exc).__name__" in code


def test_starting_a_job_actually_prunes(monkeypatch):
    """The pruner must be REACHED, not merely correct.

    Deleting the `_prune_jobs()` call from `lab_run` passed every assertion
    above, because they call the pruner directly. A perfect function nothing
    invokes is the #999 shape — code present, never reached — so this drives
    the real endpoint and lets the call site prove itself.
    """
    import asyncio

    from bot.api import lab

    monkeypatch.setattr(lab, "_datasets", lambda: {
        "fake_set": {"symbols": ["BTC/USDT"], "first": "", "last": ""}})

    async def _noop(*a, **k):
        return None

    monkeypatch.setattr(lab, "_run_job", _noop)

    lab._jobs.clear()
    lab._running_id = None
    lab._last_submit = 0.0
    for i in range(lab._MAX_JOBS + 20):
        lab._jobs[f"old-{i}"] = {"status": "done", "started_at": float(i)}
    assert len(lab._jobs) > lab._MAX_JOBS        # planted over the cap

    async def _go():
        return await lab.lab_run(lab.LabRunRequest(
            dataset="fake_set", symbols=["BTC/USDT"]))

    out = asyncio.run(_go())
    assert out["status"] == "running"
    assert len(lab._jobs) <= lab._MAX_JOBS, (
        f"{len(lab._jobs)} records after a run — lab_run does not call "
        "_prune_jobs, so the registry still grows for the life of the process"
    )
    lab._running_id = None
    lab._jobs.clear()
