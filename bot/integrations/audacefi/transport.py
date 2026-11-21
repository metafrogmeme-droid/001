"""The one door out: an HTTP POST to the loopback gateway.

`http.client` directly, not `urllib`: urllib honours ``HTTP_PROXY``, and on a
box whose proxy variable names a local port the "loopback" request would leave
the machine through it. A plain connection to 127.0.0.1 goes nowhere else, and
`http.client` follows no redirect.
"""

from __future__ import annotations

import http.client
import ipaddress

from bot.integrations.audacefi.settings import GATEWAY_HOST, GATEWAY_PATH, GATEWAY_PORT, MODE


def post_to_loopback_gateway(body: bytes, *, port: int = GATEWAY_PORT,
                             timeout_s: float = 2.0) -> int:
    """POST ``body`` to ``http://127.0.0.1:<port>/signals``; the HTTP status.

    The host is the module constant and is checked to be a loopback address
    before anything connects. ``port`` exists so a test can bind its own
    ephemeral port; production sends to 8787.
    """
    if not ipaddress.ip_address(GATEWAY_HOST).is_loopback:
        raise ValueError("the AudaceFi gateway host must be a loopback address")
    if isinstance(port, bool) or not 0 < int(port) < 65536:
        raise ValueError(f"port {port!r} is not a TCP port")
    conn = http.client.HTTPConnection(GATEWAY_HOST, int(port), timeout=timeout_s)
    try:
        conn.request("POST", GATEWAY_PATH, body=body, headers={
            "Content-Type": "application/json",
            "Content-Length": str(len(body)),
            "X-Audacefi-Mode": MODE.replace("_", "-"),
        })
        resp = conn.getresponse()
        resp.read(4096)
        return int(resp.status)
    finally:
        conn.close()
