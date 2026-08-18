"""The website's watchdog was probing a route that did not exist.

`bot/core/proactive_monitor.py` GETs ``PUBLIC_GATEWAY_URL + "/gateway/health"``
every five minutes and pages the operator when the WEBSITE can no longer reach
the bot. It is the watchdog for the 2026-07-28 outage, where a quick tunnel's
URL rotated and web chat stayed down until a human noticed.

`scripts/cloudflared/README.md` documents the same URL as the way to verify a
newly-built tunnel.

Neither worked. `build_gateway()` registered 56 routes and none of them was
``/health``, so the probe got a 404 on every pass. The monitor maps any status
that is not 200/401/403 to ``state="error"``, increments
``consecutive_failures``, and pages once it reaches ``GATEWAY_PROBE_ALERT_AT``
— which it did, permanently, for a bot that was working fine.

A watchdog that fires constantly is not a noisy watchdog. It is a DISABLED one:
the operator learns to dismiss it, and the outage it exists to catch arrives
looking exactly like the noise it has been making all month. The repository
already states this in `tests/test_gate_flake_filter_needs_a_stable_tree.py` —
"a guard that always fires gets switched off".

WHY NOBODY SAW IT. `secret_middleware` runs BEFORE routing, so an
unauthenticated request returns 403 whether or not any route exists behind it.
The README's verification step sends no secret, so a completely empty gateway
and a fully working one produce the identical documented response. Only a probe
carrying the secret can tell them apart, and nothing in the repo sent one until
this file did.

That is this codebase's recurring shape once more: the code was present, the
config was right, the guard was wired — and the thing it pointed at was not
there.
"""

from __future__ import annotations

import pathlib
import re

import pytest

from bot.web import user_gateway

ROOT = pathlib.Path(__file__).resolve().parent.parent


# ── the route exists and the probe's own URL resolves ────────────────────────

def _gateway_paths() -> set[str]:
    """Every path build_gateway() registers, read off the built app."""
    app = user_gateway.build_gateway(engine=None, tg_handler=None)
    out: set[str] = set()
    for resource in app.router.resources():
        info = resource.get_info()
        path = info.get("path") or info.get("formatter")
        if path:
            out.add(path)
    return out


def test_the_gateway_serves_health():
    assert "/health" in _gateway_paths(), (
        "build_gateway() registers no /health route, so the URL the proactive "
        "monitor probes every five minutes is a 404")


def test_the_monitor_probes_a_path_the_gateway_actually_serves():
    """The two halves, checked against each other rather than each against a
    literal. A rename on either side fails here instead of silently pointing
    the watchdog at nothing again."""
    src = (ROOT / "bot" / "core" / "proactive_monitor.py").read_text(encoding="utf-8")
    m = re.search(r'url\.rstrip\("/"\)\s*\+\s*"(/[^"]+)"', src)
    assert m, "the gateway probe no longer builds its URL the way this test reads it"
    probed = m.group(1)
    assert probed.startswith("/gateway/"), (
        f"the monitor probes {probed!r}, which is not under the /gateway mount")
    # dashboard_server mounts the sub-app at /gateway, so strip that prefix.
    assert probed[len("/gateway"):] in _gateway_paths(), (
        f"the monitor probes {probed!r} and the gateway does not serve it")


def test_the_subapp_is_mounted_where_the_probe_expects():
    src = (ROOT / "bot" / "web" / "dashboard_server.py").read_text(encoding="utf-8")
    assert 'add_subapp("/gateway"' in src, (
        "the gateway is no longer mounted at /gateway, so every documented URL "
        "and the monitor's probe are wrong")


# ── it answers the question it claims to, and only that ──────────────────────

@pytest.mark.asyncio
async def test_health_returns_ok_and_nothing_else():
    """Coarse and fixed. A probe that grows engine state gets read as an
    all-clear for the engine, which is a different claim it cannot support."""
    import json

    resp = await user_gateway.handle_health(object())      # type: ignore[arg-type]
    assert resp.status == 200
    body = json.loads(resp.body.decode())
    assert body == {"ok": True, "service": "gateway"}, (
        f"the health payload has changed to {body!r} — anything beyond "
        "reachability is a claim this endpoint cannot back")


def test_health_is_behind_the_secret_middleware():
    """It must NOT become an unauthenticated hole. The sub-app applies
    secret_middleware to every route, so this is really a check that nothing
    has moved /health out of that app."""
    app = user_gateway.build_gateway(engine=None, tg_handler=None)
    assert user_gateway.secret_middleware in app.middlewares, (
        "the gateway sub-app no longer applies secret_middleware")


# ── the monitor still treats a 404 as a failure (it should) ──────────────────

def test_a_missing_route_would_still_page():
    """THE CONTROL. This defect was survivable only because the monitor was
    right: it called a 404 an error. If somebody 'fixes the noise' by making
    the monitor tolerate non-200s, the watchdog stops watching and this whole
    class of failure becomes invisible instead of loud."""
    src = (ROOT / "bot" / "core" / "proactive_monitor.py").read_text(encoding="utf-8")
    block = src[src.index('result["status"] = resp.status'):]
    block = block[:block.index("except Exception")]
    assert 'if resp.status == 200:' in block and 'result["state"] = "ok"' in block, (
        "only a 200 may count as ok")
    assert 'result["state"] = "error"' in block, (
        "the probe no longer classifies an unexpected status as an error — a "
        "watchdog that tolerates a 404 cannot tell a working gateway from an "
        "empty one")


def test_the_runbook_documents_a_url_that_resolves():
    """The README's verify step is the first thing a new operator runs. It sent
    no secret, so it returned 403 for a working gateway AND for one with no
    routes at all — the check could not fail, which is why this went unnoticed
    through a whole tunnel setup."""
    readme = (ROOT / "scripts" / "cloudflared" / "README.md").read_text(encoding="utf-8")
    for path in re.findall(r"https://[^\s/]+(/gateway/[A-Za-z0-9_/-]+)", readme):
        assert path[len("/gateway"):] in _gateway_paths(), (
            f"the runbook tells operators to curl {path!r}, which the gateway "
            "does not serve")
