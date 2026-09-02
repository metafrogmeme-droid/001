"""`/gateway/health` can silently not exist, and :8080 keeps answering 200.

Reported live on 2026-09-02 as "bot health endpoint (port 8080) not
responding". It was responding. `/health` on the dashboard answered fine; it
was `/gateway/*` that 404'd, because the subapp mount is conditional:

    if tg_handler is not None:
        try:
            app.add_subapp("/gateway", build_gateway(engine, tg_handler))
        except Exception as exc:
            logging.getLogger(__name__).warning("Web gateway not mounted: %s", exc)

Two probes ask that path. The launcher gates DEPLOY_DONE on it, and
`proactive_monitor._probe_public_gateway` uses it to tell a dead tunnel apart
from a firewall — a distinction its own comment calls the point of probing
from inside the bot. A THIRD fault, the route never being registered, arrived
looking like one of those two and sends an operator to restart cloudflared
over a route the bot never mounted.

Three values, because "nobody asked for the gateway" and "it was asked for and
broke" have different remedies and neither is the good one.
"""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace as NS

from bot.web import dashboard_server as ds


class _Req:
    """Minimal aiohttp request: handle_health only reads `.app`."""

    def __init__(self, app):
        self.app = app


def _health(app):
    resp = asyncio.run(ds.handle_health(_Req(app)))
    return json.loads(resp.body.decode())


def test_a_mounted_gateway_says_so():
    app = ds.create_app(NS(), tg_handler=NS())
    assert app["gateway_status"] == "mounted"
    assert _health(app)["gateway"] == "mounted"


def test_no_handler_is_not_requested_not_a_failure():
    """Dashboard-only is a legitimate way to run. Calling it "failed" would
    page someone over a deliberate configuration."""
    app = ds.create_app(NS(), tg_handler=None)
    assert app["gateway_status"] == "not_requested"
    assert _health(app)["gateway"] == "not_requested"


def test_a_raising_build_is_failed_and_still_serves_the_dashboard(monkeypatch):
    """The `except` exists so a broken gateway cannot take the dashboard down,
    and that stays true. What changes is that it stops being invisible."""
    import bot.web.user_gateway as ug

    def _boom(*_a, **_k):
        raise RuntimeError("gateway config missing")
    monkeypatch.setattr(ug, "build_gateway", _boom)

    app = ds.create_app(NS(), tg_handler=NS())
    assert app["gateway_status"] == "failed"
    body = _health(app)
    assert body["status"] == "ok", "the dashboard must still answer"
    assert body["gateway"] == "failed"


def test_health_still_answers_and_still_carries_the_build():
    """Adding a field must not cost the two this endpoint already promised:
    it always answers, and it names WHICH COMMIT answered."""
    body = _health(ds.create_app(NS(), tg_handler=None))
    assert body["status"] == "ok"
    assert "build" in body and "timestamp" in body


def test_an_app_that_never_recorded_a_status_reads_unknown():
    """An older app object, or one built by another path, has not told us the
    gateway is fine. Defaulting to a cheerful value would be the whole defect
    again, one layer up."""
    assert _health({})["gateway"] == "unknown"


def test_the_failure_is_audited_not_just_logged(monkeypatch):
    """A `logging.warning` on a module logger is where the original fault
    went to die. The operator surface for this is the audit stream."""
    import bot.utils.logger as blog
    import bot.web.user_gateway as ug

    def _boom(*_a, **_k):
        raise RuntimeError("gateway config missing")
    monkeypatch.setattr(ug, "build_gateway", _boom)

    seen = []
    monkeypatch.setattr(blog, "audit",
                        lambda *a, **k: seen.append((a, k)))
    ds.create_app(NS(), tg_handler=NS())
    assert seen, "the mount failure never reached the audit stream"
    kw = seen[0][1]
    assert kw.get("action") == "gateway_mount" and kw.get("result") == "FAILED"


def test_the_audit_line_does_not_carry_the_exception_message():
    """A gateway build failure can name configuration. The class is enough to
    tell an operator what happened."""
    src = open("bot/web/dashboard_server.py", encoding="utf-8").read()
    code = "\n".join(ln for ln in src.split("\n") if not ln.lstrip().startswith("#"))
    i = code.index('action="gateway_mount"')
    window = code[max(0, i - 600):i]
    assert "type(exc).__name__" in window
    assert "{exc}" not in window, "the raw exception message reaches the audit line"


def test_both_probes_still_target_the_path_this_reports_on():
    """If the probed path ever moves, this whole diagnosis moves with it."""
    launcher = open("scripts/launch_all.sh.template", encoding="utf-8").read()
    monitor = open("bot/core/proactive_monitor.py", encoding="utf-8").read()
    assert "/gateway/health" in launcher
    assert "/gateway/health" in monitor
    ds_src = open("bot/web/dashboard_server.py", encoding="utf-8").read()
    assert 'add_subapp("/gateway"' in ds_src, "the mount prefix moved"
