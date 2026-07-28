"""Nothing noticed that the website could not reach the bot.

2026-07-28. Web chat was down for most of a day. The website could not report
it — it has no alert channel — and the bot never knew it was being reached
until it wasn't. The first report came from a person opening the page, and
the cause took hours to find because "cannot reach the bot" looked exactly
like "the web app is down".

Every fix that day made the failure diagnosable AFTER someone noticed. This
makes something notice.

The probe runs in the bot, because the bot is the process that can page an
operator, and it tests the same path the website uses — the public URL, not
the local bind. That distinction is the whole point: the bot's gateway was
listening on 127.0.0.1 and answering perfectly to anything on the box, while
being unreachable from the internet.

It names the two faults apart, because their remedies differ:

    unreachable   the URL changed/expired, or the path is blocked
    forbidden     reachable, but the shared secret no longer matches

The first is what an ephemeral tunnel does on every restart.
"""

import asyncio
from types import SimpleNamespace

import pytest

from bot.config import CONFIG
from bot.core.proactive_monitor import ProactiveMonitor


@pytest.fixture(autouse=True)
def _restore():
    url = getattr(CONFIG.monitoring, "public_gateway_url", "")
    every = getattr(CONFIG.monitoring, "public_gateway_probe_interval_s", 300.0)
    yield
    object.__setattr__(CONFIG.monitoring, "public_gateway_url", url)
    object.__setattr__(CONFIG.monitoring, "public_gateway_probe_interval_s", every)


def _pm(probe=None, alerted=None):
    pm = ProactiveMonitor(SimpleNamespace())
    pm._gw_probe = probe
    pm._gw_alerted_state = alerted
    return pm


# ── the alert ─────────────────────────────────────────────────────────────

def test_one_failure_says_nothing():
    """A single miss is a blip, not an outage."""
    assert _pm({"state": "unreachable", "consecutive_failures": 1})._check_public_gateway() == []


def test_a_sustained_outage_pages_once_and_names_the_fault():
    pm = _pm({"state": "unreachable", "consecutive_failures": 2})
    alerts = pm._check_public_gateway()
    assert len(alerts) == 1
    body = alerts[0].body
    assert "expired" in body or "changed" in body
    assert "ephemeral tunnel" in body, "name the thing that does this on every restart"
    # and it must scope the blast radius honestly
    assert "Trading is unaffected" in body
    assert pm._check_public_gateway() == [], "one outage, one page"


def test_a_rejected_secret_is_a_DIFFERENT_page():
    """Reachable-but-refused and unreachable send an operator to different
    places; collapsing them would repeat the day this was written."""
    alerts = _pm({"state": "forbidden", "consecutive_failures": 2})._check_public_gateway()
    assert len(alerts) == 1
    assert "REJECTED the shared secret" in alerts[0].body
    assert "WEB_GATEWAY_SECRET" in alerts[0].body


def test_the_fault_CHANGING_pages_again():
    pm = _pm({"state": "unreachable", "consecutive_failures": 2})
    assert len(pm._check_public_gateway()) == 1
    pm._gw_probe = {"state": "forbidden", "consecutive_failures": 3}
    assert len(pm._check_public_gateway()) == 1, "a new fault is new information"


def test_recovery_is_announced_and_re_arms():
    pm = _pm({"state": "unreachable", "consecutive_failures": 2})
    assert len(pm._check_public_gateway()) == 1
    pm._gw_probe = {"state": "ok", "consecutive_failures": 0}
    rec = pm._check_public_gateway()
    assert len(rec) == 1 and "RECOVERED" in rec[0].body
    assert pm._check_public_gateway() == [], "recovery announces once"
    # and a later outage pages again
    pm._gw_probe = {"state": "unreachable", "consecutive_failures": 2}
    assert len(pm._check_public_gateway()) == 1


def test_a_healthy_gateway_that_was_never_broken_is_silent():
    assert _pm({"state": "ok", "consecutive_failures": 0})._check_public_gateway() == []


def test_no_probe_yet_and_malformed_probes_are_silent():
    assert _pm(None)._check_public_gateway() == []
    for bad in ({}, "nope", 7, {"state": "unreachable"}):
        assert _pm(bad)._check_public_gateway() == []


def test_the_page_is_a_WARNING_not_a_critical():
    """Chat being down is not the exchange being down. Crying CRITICAL for a
    website feature is how a real trading alert gets ignored."""
    a = _pm({"state": "unreachable", "consecutive_failures": 2})._check_public_gateway()[0]
    assert a.severity == "WARNING"


# ── the probe ─────────────────────────────────────────────────────────────

def test_unset_url_means_no_probe_at_all():
    object.__setattr__(CONFIG.monitoring, "public_gateway_url", "")
    pm = _pm()
    asyncio.run(pm._probe_public_gateway())
    assert pm._gw_probe is None, "opt-in: no URL, no probe, no alerts"


def test_an_unreachable_host_records_unreachable_and_counts_up():
    object.__setattr__(CONFIG.monitoring, "public_gateway_url", "http://127.0.0.1:1")
    object.__setattr__(CONFIG.monitoring, "public_gateway_probe_interval_s", 30.0)
    pm = _pm()
    asyncio.run(pm._probe_public_gateway())
    assert pm._gw_probe["state"] == "unreachable"
    assert pm._gw_probe["consecutive_failures"] == 1
    pm._gw_probe_at = 0.0                      # allow an immediate re-probe
    asyncio.run(pm._probe_public_gateway())
    assert pm._gw_probe["consecutive_failures"] == 2


def test_the_probe_never_raises_and_is_throttled():
    object.__setattr__(CONFIG.monitoring, "public_gateway_url", "http://127.0.0.1:1")
    object.__setattr__(CONFIG.monitoring, "public_gateway_probe_interval_s", 3600.0)
    pm = _pm()
    asyncio.run(pm._probe_public_gateway())
    first = pm._gw_probe
    asyncio.run(pm._probe_public_gateway())    # inside the interval
    assert pm._gw_probe is first, "throttled — one bounded request per interval"


def test_it_is_wired_into_the_loop_and_the_checks():
    from pathlib import Path
    src = Path("bot/core/proactive_monitor.py").read_text(encoding="utf-8")
    assert "await self._probe_public_gateway()" in src, "the probe must actually run"
    assert "alerts.extend(self._check_public_gateway())" in src
