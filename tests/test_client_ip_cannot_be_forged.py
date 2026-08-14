"""H1: rotating one header bought a fresh rate-limit and lockout bucket.

Production ran `uvicorn --proxy-headers --forwarded-allow-ips='*'` behind an
nginx whose `X-Forwarded-For $proxy_add_x_forwarded_for` APPENDS the observed
peer to whatever the caller sent. uvicorn's always-trust mode takes the
LEFT-most entry and overwrites `scope["client"]`, so `request.client.host` was
the caller's own string before any application code ran.

Two controls keyed on it:

  * `api_bridge._check_rate_limit` — 30/min, guarding unauthenticated endpoints
    that each hit the exchange (/scan, /patterns).
  * `bot/api/auth_routes._check_auth_rate_limit` — 5 failures per minute then a
    5-minute lockout, on /auth/login and /auth/register.

Send `X-Forwarded-For: 9.9.9.9`, then `9.9.9.10`, and each request is a new
client with a fresh budget. The per-email lockout (RC-AUD-026) was the only
survivor, and it does not cover registration.

TWO DEFECTS, NOT ONE, and the second is the one a config change alone leaves
standing:

1. THE FLAGS. `--forwarded-allow-ips='*'` is removed in docker-compose.yml. The
   audit's own remediation, and the load-bearing half — without it, nothing the
   application does can help, because the poisoning happens upstream of it.

2. THE WALK'S MISSING PREMISE. `_client_ip` consulted X-Forwarded-For whenever
   `TRUSTED_PROXY` was non-empty and never checked that the PEER was that proxy.
   Reach the service directly — a published port, an attacker inside the network
   — and `XFF: a, b` returns `b`, which the attacker wrote. The header was
   trusted because a config value existed somewhere, not because the connection
   came from the hop it names. That is fixed here, and the fix is what makes the
   direct-reach case safe whatever the proxy is doing.

AND THE TWO CONTROLS NOW AGREE. `auth_routes` read `request.client.host`
directly and never called `_client_ip` at all, so the mitigation lived on the
limiter that throttles scans and not on the one standing between a password list
and an account.
"""
from __future__ import annotations

import importlib
from types import SimpleNamespace

import pytest


def _request(peer: str, **headers):
    """A FastAPI-shaped request. Header lookup is case-insensitive, as Starlette's
    is — a fixture that only matched lower-case would pass a lookup that used
    'X-Real-IP' and fail in production."""
    lowered = {k.lower().replace("_", "-"): v for k, v in headers.items()}
    return SimpleNamespace(
        client=SimpleNamespace(host=peer) if peer else None,
        headers=SimpleNamespace(get=lambda k, d="": lowered.get(k.lower(), d)))


def _module(monkeypatch, trusted: str = ""):
    """Reload with a given TRUSTED_PROXY — it is read at import."""
    monkeypatch.setenv("TRUSTED_PROXY", trusted)
    import bot.utils.client_ip as mod
    return importlib.reload(mod)


PROXY = "172.28.0.5"
NET = "172.28.0.0/16"
REAL = "203.0.113.7"
FORGED = "9.9.9.9"


# ── nothing in front ─────────────────────────────────────────────────

class TestWithNoTrustedProxy:
    def test_headers_are_ignored_entirely(self, monkeypatch):
        m = _module(monkeypatch, "")
        got = m.client_ip(_request(REAL, x_forwarded_for=FORGED, x_real_ip=FORGED))
        assert got == REAL, (
            "a caller with no proxy in front chose their own rate-limit bucket")

    def test_rotating_the_header_does_not_move_the_bucket(self, monkeypatch):
        m = _module(monkeypatch, "")
        keys = {m.client_ip(_request(REAL, x_forwarded_for=f"9.9.9.{i}"))
                for i in range(10)}
        assert keys == {REAL}, f"10 forged headers produced {len(keys)} buckets"


# ── behind a trusted hop ─────────────────────────────────────────────

class TestBehindATrustedProxy:
    def test_x_real_ip_is_believed(self, monkeypatch):
        """nginx sets it from $remote_addr and proxy_set_header REPLACES any
        inbound value, so past a trusted hop it cannot be forged — and unlike
        XFF there is no list to walk and mis-walk."""
        m = _module(monkeypatch, NET)
        assert m.client_ip(_request(PROXY, x_real_ip=REAL)) == REAL

    def test_x_real_ip_wins_over_a_forged_xff(self, monkeypatch):
        m = _module(monkeypatch, NET)
        got = m.client_ip(_request(PROXY, x_real_ip=REAL,
                                   x_forwarded_for=f"{FORGED}, {REAL}"))
        assert got == REAL

    def test_the_rightmost_untrusted_xff_entry_is_used(self, monkeypatch):
        """The RC-AUD-012 walk, kept: nginx appends the peer on the RIGHT, so
        the attacker's prepended entries are to the left and skipped."""
        m = _module(monkeypatch, NET)
        got = m.client_ip(_request(PROXY, x_forwarded_for=f"{FORGED}, {REAL}"))
        assert got == REAL

    def test_trusted_hops_inside_the_header_are_skipped(self, monkeypatch):
        m = _module(monkeypatch, NET)
        got = m.client_ip(_request(PROXY, x_forwarded_for=f"{REAL}, 172.28.0.9"))
        assert got == REAL

    def test_forging_the_left_of_the_header_changes_nothing(self, monkeypatch):
        m = _module(monkeypatch, NET)
        keys = {m.client_ip(_request(PROXY, x_forwarded_for=f"9.9.9.{i}, {REAL}"))
                for i in range(10)}
        assert keys == {REAL}, f"10 forged prefixes produced {len(keys)} buckets"

    def test_garbage_entries_are_not_identities(self, monkeypatch):
        """An unparseable hop is skipped rather than used as a key. It is not an
        address, so it is not a caller — and it would otherwise be a free bucket
        per arbitrary string."""
        m = _module(monkeypatch, NET)
        got = m.client_ip(_request(PROXY, x_forwarded_for=f"{REAL}, not-an-ip"))
        assert got == REAL


# ── the premise the old walk was missing ─────────────────────────────

class TestTheWalkChecksThePeer:
    def test_reaching_the_service_directly_does_not_get_to_use_headers(self, monkeypatch):
        """THE bug in the old walk. TRUSTED_PROXY is set — but this connection
        did not come from it, so nothing it says about itself counts.

        Before, `TRUSTED_PROXY` being non-empty was the whole condition, so an
        attacker who reached the port directly sent `XFF: a, b` and was keyed on
        `b`, which they wrote.
        """
        m = _module(monkeypatch, NET)
        attacker = "198.51.100.4"
        got = m.client_ip(_request(attacker, x_forwarded_for=f"{FORGED}, {REAL}",
                                   x_real_ip=REAL))
        assert got == attacker, (
            "headers from an untrusted peer were believed — the walk trusts the "
            "config, not the connection")

    def test_and_they_cannot_rotate_it(self, monkeypatch):
        m = _module(monkeypatch, NET)
        attacker = "198.51.100.4"
        keys = {m.client_ip(_request(attacker, x_forwarded_for=f"9.9.9.{i}, 9.9.8.{i}"))
                for i in range(10)}
        assert keys == {attacker}


# ── configuration honesty ────────────────────────────────────────────

class TestConfiguration:
    def test_a_bare_address_is_accepted_as_well_as_a_cidr(self, monkeypatch):
        m = _module(monkeypatch, PROXY)
        assert m.is_trusted_proxy(PROXY) is True
        assert m.is_trusted_proxy("172.28.99.1") is False

    def test_a_typo_does_not_silently_widen_trust(self, monkeypatch):
        """An unparseable entry is dropped and named. Dropped silently, an
        operator believes a hop is trusted when it is not — and the failure is
        invisible until somebody forges past it."""
        m = _module(monkeypatch, "not-a-network")
        assert m.is_trusted_proxy(PROXY) is False
        assert m.client_ip(_request(PROXY, x_real_ip=REAL)) == PROXY

    def test_one_bad_entry_does_not_discard_the_good_ones(self, monkeypatch):
        m = _module(monkeypatch, f"nonsense, {NET}")
        assert m.is_trusted_proxy(PROXY) is True

    def test_no_client_at_all_is_unknown_not_a_guess(self, monkeypatch):
        m = _module(monkeypatch, NET)
        assert m.client_ip(_request(None, x_real_ip=REAL)) == m.UNKNOWN


# ── the detector for the flag that started it ────────────────────────

class TestPoisonedPeerDetection:
    def test_it_recognises_the_always_trust_signature(self, monkeypatch):
        """`--forwarded-allow-ips='*'` leaves the peer equal to the LEFT-most
        forwarded entry — the end a caller controls, and the end no honest proxy
        would leave there."""
        m = _module(monkeypatch, "")
        assert m.poisoned_peer(
            _request(FORGED, x_forwarded_for=f"{FORGED}, 172.28.0.5")) is True

    def test_a_normal_request_is_not_flagged(self, monkeypatch):
        m = _module(monkeypatch, "")
        assert m.poisoned_peer(_request(PROXY, x_forwarded_for=f"{FORGED}, {REAL}")) is False
        assert m.poisoned_peer(_request(REAL)) is False

    def test_it_warns_once_and_names_the_remedy(self, monkeypatch, caplog):
        m = _module(monkeypatch, "")
        with caplog.at_level("WARNING"):
            for _ in range(3):
                m.client_ip(_request(FORGED, x_forwarded_for=f"{FORGED}, 172.28.0.5"))
        warnings = [r for r in caplog.records if r.levelname == "WARNING"]
        assert len(warnings) == 1, f"warned {len(warnings)} times — log spam"
        assert "forwarded-allow-ips" in warnings[0].message
        assert "TRUSTED_PROXY" in warnings[0].message

    def test_detection_does_not_change_the_answer(self, monkeypatch):
        """Detection only. Refusing traffic here would turn a configuration
        mistake into an outage, and the value is no more spoofable after the
        check than before — what it buys is that somebody finds out."""
        m = _module(monkeypatch, "")
        assert m.client_ip(_request(FORGED, x_forwarded_for=f"{FORGED}, x")) == FORGED


# ── both controls, one answer ────────────────────────────────────────

class TestTheTwoLimitersAgree:
    def test_the_auth_lockout_uses_the_shared_derivation(self):
        """It read `request.client.host` directly and never called the walk, so
        the anti-spoof mitigation guarded scans and not logins."""
        import ast
        import pathlib
        src = (pathlib.Path(__file__).resolve().parent.parent
               / "bot" / "api" / "auth_routes.py").read_text(encoding="utf-8")
        tree = ast.parse(src)
        fn = next(n for n in ast.walk(tree)
                  if isinstance(n, ast.FunctionDef) and n.name == "_check_auth_rate_limit")
        body = ast.get_source_segment(src, fn) or ""
        code = "\n".join(ln.split("#", 1)[0] for ln in body.split("\n"))
        assert "client_ip(request)" in code, "the auth throttle stopped using it"
        assert "request.client.host" not in code, (
            "the auth throttle reads the peer directly again")

    def test_the_api_limiter_does_too(self, monkeypatch):
        # api_bridge refuses to import without a JWT secret — deliberately, and
        # not this fix's business. A throwaway satisfies the boot guard.
        monkeypatch.setenv("JWT_SECRET", "0" * 64)
        api_bridge = pytest.importorskip("api_bridge")
        from bot.utils.client_ip import client_ip as shared
        for req in (_request(REAL, x_forwarded_for=FORGED),
                    _request(PROXY, x_real_ip=REAL),
                    _request(None)):
            assert api_bridge._client_ip(req) == shared(req), (
                "the API limiter and the shared derivation disagree — two "
                "answers to 'who is calling' is how the auth throttle ended up "
                "with the weaker one")

    def test_the_compose_file_no_longer_always_trusts(self):
        """The load-bearing half. Without this the application cannot help:
        uvicorn rewrites the peer before the app is reached."""
        import pathlib
        src = (pathlib.Path(__file__).resolve().parent.parent
               / "docker-compose.yml").read_text(encoding="utf-8")
        code = "\n".join(ln.split("#", 1)[0] for ln in src.split("\n"))
        assert "forwarded-allow-ips" not in code, (
            "uvicorn is trusting forwarded headers again — the peer address "
            "becomes the caller's leftmost X-Forwarded-For entry")
        assert "--proxy-headers" not in code

    def test_trusted_proxy_is_documented(self):
        import pathlib
        src = (pathlib.Path(__file__).resolve().parent.parent
               / ".env.example").read_text(encoding="utf-8")
        assert "TRUSTED_PROXY" in src, (
            "the variable the whole trust boundary rests on is undocumented — "
            "it was absent before, which is why it was never set")
