"""Who is calling — one answer, with an explicit trust boundary.

There were two, and the worse one guarded logins. `api_bridge._client_ip`
carried the RC-AUD-012 anti-spoof walk; `bot/api/auth_routes.py` read
`request.client.host` directly and never called it. So the API limiter had a
mitigation and the per-IP failed-login lockout — the control that actually
stands between a password list and an account — did not.

WHAT WAS WRONG WITH THE WALK ITSELF

RC-AUD-012 consulted `X-Forwarded-For` whenever `TRUSTED_PROXY` was non-empty,
and took the right-most entry that was not a listed proxy. That is the right
shape and it was missing its premise: it never checked whether the ACTUAL PEER
was a trusted proxy. Reach the app directly — a misrouted port, a container
published by accident, an attacker inside the network — and `XFF: a, b` yields
`b`, which the attacker wrote. The header was trusted because a config value
existed somewhere, not because the connection came from the hop that config
names.

So the rule here is the standard one, stated positively: **headers describing
the caller are evidence only when the connection arrived from a hop we trust to
have written them.** Everything else is the peer address, which is the only
thing TCP will vouch for.

AND WHY THE "SAFE DEFAULT" WAS NOT SAFE

`_client_ip`'s docstring called returning `request.client.host` the unchanged,
safe default. It is safe only if that value is the peer. Production ran uvicorn
with `--proxy-headers --forwarded-allow-ips='*'` behind an nginx that sets
`X-Forwarded-For $proxy_add_x_forwarded_for` — which APPENDS the real peer to
whatever the client sent. uvicorn's always-trust mode takes the LEFT-most entry
and overwrites `scope["client"]`, so `request.client.host` was the attacker's
string before a line of application code ran. The mitigation was reading a
poisoned value and the docstring said it was the safe one.

`poisoned_peer()` below detects exactly that shape at runtime, because a
configuration defect that is invisible from inside the process is one nobody
fixes.

X-REAL-IP IS PREFERRED over X-Forwarded-For when the peer is trusted. nginx
already sets it (`proxy_set_header X-Real-IP $remote_addr`, nginx.conf:65 and
:103) from the address it actually observed, and `proxy_set_header` REPLACES any
value the client sent — so past a trusted hop it cannot be forged, and it has no
list to walk and mis-walk.
"""
from __future__ import annotations

import ipaddress
import logging
import os
from typing import Any, Optional

log = logging.getLogger("runeclaw.client_ip")

UNKNOWN = "unknown"

#: Hops permitted to describe the caller. Bare addresses and CIDRs both work —
#: a container on a bridge network has no stable address, so the deployable
#: answer is usually the network, e.g. ``TRUSTED_PROXY=172.28.0.0/16``.
_TRUSTED_PROXY_RAW = os.getenv("TRUSTED_PROXY", "").strip()


def _parse_networks(raw: str) -> list[ipaddress._BaseNetwork]:
    nets: list[ipaddress._BaseNetwork] = []
    for item in raw.split(","):
        item = item.strip()
        if not item:
            continue
        try:
            nets.append(ipaddress.ip_network(item, strict=False))
        except ValueError:
            # A typo must not silently widen or narrow trust. Named, once, at
            # import — an unparseable entry that vanished would leave an
            # operator believing a hop is trusted when it is not.
            log.warning("TRUSTED_PROXY entry %r is not an IP or CIDR — ignored", item)
    return nets


_TRUSTED_NETWORKS = _parse_networks(_TRUSTED_PROXY_RAW)


def _as_address(value: str) -> Optional[ipaddress._BaseAddress]:
    try:
        return ipaddress.ip_address(value.strip())
    except ValueError:
        return None


def is_trusted_proxy(addr: str) -> bool:
    """True when ``addr`` is a hop configured to describe callers."""
    ip = _as_address(addr)
    if ip is None:
        return False
    return any(ip in net for net in _TRUSTED_NETWORKS)


def _peer(request: Any) -> str:
    client = getattr(request, "client", None)
    host = getattr(client, "host", None) if client else None
    return host or UNKNOWN


def poisoned_peer(request: Any) -> bool:
    """True when the peer address looks like it was rewritten from a header.

    The signature of `--forwarded-allow-ips='*'`: an `X-Forwarded-For` is
    present and `request.client.host` is equal to its LEFT-most entry, which is
    the end a client controls and the end no honest proxy would leave there.
    Nothing else produces that pairing — a real peer matching the leftmost
    forwarded hop would mean the client truthfully announced its own address and
    then reached us through a proxy that added nothing.

    Detection only. Refusing traffic on this would turn a configuration mistake
    into an outage, and the value is no more spoofable after the check than
    before it; what the check buys is that somebody finds out.
    """
    xff = ""
    headers = getattr(request, "headers", None)
    if headers is not None:
        try:
            xff = headers.get("x-forwarded-for", "") or ""
        except Exception:
            xff = ""
    if not xff:
        return False
    leftmost = xff.split(",")[0].strip()
    return bool(leftmost) and leftmost == _peer(request)


_warned = False


def _warn_once(request: Any) -> None:
    global _warned
    if _warned or not poisoned_peer(request):
        return
    _warned = True
    log.warning(
        "client address appears to come from X-Forwarded-For rather than the "
        "connection: per-IP rate limits and login lockouts can be bypassed by "
        "rotating the header. Run uvicorn without --forwarded-allow-ips='*' "
        "and set TRUSTED_PROXY to the proxy's address or network.")


def client_ip(request: Any) -> str:
    """The caller's address, for rate limiting and lockout keys.

    Peer address unless the peer is a configured trusted proxy, in which case
    the headers that proxy wrote are believed: `X-Real-IP` first, then the
    right-most `X-Forwarded-For` entry that is not itself a trusted hop.
    """
    peer = _peer(request)
    if peer == UNKNOWN:
        return UNKNOWN
    if not _TRUSTED_NETWORKS or not is_trusted_proxy(peer):
        # Not behind a hop we trust — the connection is the only evidence.
        _warn_once(request)
        return peer

    headers = getattr(request, "headers", None)
    real = ""
    xff = ""
    if headers is not None:
        try:
            real = (headers.get("x-real-ip", "") or "").strip()
            xff = (headers.get("x-forwarded-for", "") or "").strip()
        except Exception:
            real = xff = ""

    if real and _as_address(real) is not None:
        return real

    for hop in reversed([h.strip() for h in xff.split(",") if h.strip()]):
        if _as_address(hop) is None:
            continue          # garbage entry — not an address, not an identity
        if not is_trusted_proxy(hop):
            return hop
    return peer
