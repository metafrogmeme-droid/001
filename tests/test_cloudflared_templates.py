"""The tunnel templates must stay templates.

A committed `config.yml` with a real tunnel UUID in it is halfway to a
committed credential: the UUID names the tunnel, and the file beside it named
`<UUID>.json` IS the bearer credential for it. So the invariant is not "no
secrets" — it is "no real values at all", because a real value here means
somebody filled the template in place and committed it instead of copying it
to ~/.cloudflared.

Also pinned: the setup doc still says the thing that is easy to get wrong.
Naming the tunnel fixes the URL changing on restart. It does NOT fix the
tunnel process being down. Two failure modes, and fixing one while believing
you fixed both is how this bites a second time.
"""

import re
from pathlib import Path

import pytest

DIR = Path("scripts/cloudflared")
CONFIG = (DIR / "config.example.yml").read_text(encoding="utf-8")
UNIT = (DIR / "runeclaw-gateway.service").read_text(encoding="utf-8")
README = (DIR / "README.md").read_text(encoding="utf-8")
RUNBOOK = Path("docs/LIVE_HARDENING_RUNBOOK.md").read_text(encoding="utf-8")

UUID_RE = re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.I)


# ── still a template ──────────────────────────────────────────────────────

def test_the_config_carries_placeholders_not_a_real_tunnel():
    assert "TUNNEL_UUID" in CONFIG and "HOSTNAME" in CONFIG
    assert not UUID_RE.search(CONFIG), (
        "a real tunnel UUID was committed — the credentials file named after "
        "it is a bearer token for that tunnel")


@pytest.mark.parametrize("text,name", [(CONFIG, "config"), (UNIT, "unit"), (README, "README")])
def test_no_concrete_quick_tunnel_host_is_committed(text, name):
    """Naming the CURRENT quick tunnel would be the same mistake as the rest
    of this: it changes on every restart, so a committed value is a wrong
    value with a plausible look. The generic `*.trycloudflare.com` shape is
    fine and explains the contrast; a real host is not."""
    concrete = [m for m in re.findall(r"[A-Za-z0-9-]+\.trycloudflare\.com", text)
                if not m.startswith("*.")]
    assert concrete == [], f"{name} names a specific quick tunnel: {concrete}"


def test_no_credential_material_is_present():
    for text in (CONFIG, UNIT, README):
        assert "BEGIN " not in text, "a PEM block was committed"
        assert not re.search(r"WEB_GATEWAY_SECRET\s*[=:]\s*\S{8,}", text)
        assert not re.search(r"\bsk-[A-Za-z0-9]{16,}\b", text)


# ── the config says the right thing ───────────────────────────────────────

def test_the_gateway_is_reached_over_loopback_only():
    """The bot binds 127.0.0.1 on purpose; the tunnel is the only way in."""
    assert "service: http://127.0.0.1:8080" in CONFIG
    assert "http_status:404" in CONFIG, (
        "anything else arriving on this tunnel is not ours and must not fall "
        "through to the gateway")


def test_the_unit_restarts_forever_rather_than_giving_up():
    assert "Restart=always" in UNIT
    assert "StartLimitIntervalSec=0" in UNIT, (
        "systemd's default start limit gives up after a few rapid failures — "
        "an unattended tunnel that stops retrying is the outage this prevents")
    assert "WantedBy=multi-user.target" in UNIT, "it must survive a reboot"


def test_the_unit_asks_for_no_privileges_it_does_not_need():
    assert "NoNewPrivileges=true" in UNIT
    assert "User=" in UNIT and "User=root" not in UNIT


# ── the doc says the thing that is easy to get wrong ──────────────────────

def test_both_failure_modes_are_named():
    assert "Two failure modes" in README
    for phrase in ("named", "supervisor"):
        assert phrase in README
    assert "as unreachable as a quick tunnel" in README, (
        "a named tunnel whose process is dead is still down — say so, or "
        "somebody stops after step 4")


def test_the_prerequisite_is_stated_plainly():
    """`cloudflared tunnel login` authorises a ZONE. Without a domain there is
    no named tunnel, and finding that out at step 1 wastes the session."""
    assert "zone" in README and "domain is not optional" in README


def test_the_verification_expects_the_status_the_middleware_actually_returns():
    """/gateway/* checks the shared secret, so a 200 from an unauthenticated
    curl would mean the gateway is OPEN, not that it is healthy.

    THIS TEST USED TO ASSERT THE LITERAL `"401 is CORRECT" in README`, and it
    passed for as long as the README said 401 — which was wrong the whole time.
    `secret_middleware` returns 403, in both of its branches. A test that pins a
    sentence rather than the fact behind it is only ever as right as whoever
    wrote the sentence, and here it actively defended the error: correcting the
    runbook to 403 reddened this file.

    So the expected status is read out of the middleware. Change the code and
    this fails until the runbook agrees; change the runbook alone and it fails
    too.
    """
    src = Path("bot/web/user_gateway.py").read_text(encoding="utf-8")
    # Bound the slice to the FUNCTION. Cutting at the next "\n@" ran past the
    # end of it — the next decorator is hundreds of lines away — and swept up
    # every `status=` in the module, so the test demanded the runbook document
    # status codes from unrelated handlers.
    start = src.index("async def secret_middleware")
    rest = src[start:]
    end = re.search(r"\n(?:@|def |async def )", rest[1:])
    body = rest[:end.start() + 1] if end else rest
    codes = {int(m) for m in re.findall(r"status=(\d{3})", body)}
    assert codes, "secret_middleware no longer returns an explicit status"
    assert codes != {200}, "the gateway middleware is not rejecting anything"
    for code in codes:
        assert f"{code} is CORRECT" in README or f"`{code}`" in README, (
            f"secret_middleware can answer {code} and the runbook never mentions "
            "it — an operator who sees it has nothing to check it against")
    assert "200" not in README.split("Verify it")[1][:400] or "expect 200" in README, (
        "the verify section must not imply an unauthenticated 200 is the "
        "healthy answer")


def test_the_two_variables_are_both_listed():
    # Setting one and not the other is the failure that took web chat down
    # once already: the website had GATEWAY_URL, the code read BOT_GATEWAY_URL.
    assert "BOT_GATEWAY_URL" in README
    assert "PUBLIC_GATEWAY_URL" in README


def test_the_runbook_points_at_this_procedure():
    assert "scripts/cloudflared" in RUNBOOK, (
        "the standing-hazard section must lead somewhere actionable")
