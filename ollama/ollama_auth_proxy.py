#!/usr/bin/env python3
r"""
RUNECLAW - Bearer-token gate in front of a local Ollama
=========================================================
Ollama checks no credentials. Publishing it through a tunnel without a
gate hands the GPU to anyone who finds the URL. This proxy sits between
the tunnel and Ollama and refuses every request whose Authorization
header is not `Bearer <RUNECLAW_PROXY_TOKEN>` — which is exactly the
header the bot already sends when RUNECLAW_LLM_API_KEY is set
(bot/llm/provider.py builds an AsyncOpenAI client from it).

Pure stdlib, binds 127.0.0.1 only (no firewall prompt, no admin),
streams responses (SSE-safe via read1).

Usage (Windows, no admin):
  python -c "import secrets; print(secrets.token_urlsafe(32))"   # make a token
  set RUNECLAW_PROXY_TOKEN=<that token>
  python ollama_auth_proxy.py                    # listens on 127.0.0.1:11435

Then point cloudflared at http://localhost:11435 (NOT 11434), and set on
the bot host: RUNECLAW_LLM_API_KEY=<same token>.
"""

import http.client
import http.server
import os
import secrets
import sys
import time

LISTEN = ("127.0.0.1", int(os.environ.get("RUNECLAW_PROXY_PORT", "11435")))
UPSTREAM = ("127.0.0.1", int(os.environ.get("OLLAMA_PORT", "11434")))
TOKEN = os.environ.get("RUNECLAW_PROXY_TOKEN", "")

# End-to-end hop-by-hop headers we must not blindly forward.
_SKIP_REQ = {"host", "authorization", "connection", "accept-encoding", "content-length"}
_SKIP_RESP = {"transfer-encoding", "connection", "keep-alive", "content-encoding"}


#: Set RUNECLAW_PROXY_DEBUG to a directory to capture request/response bodies
#: there. Unset (the default) captures nothing at all.
DEBUG_DIR = os.environ.get("RUNECLAW_PROXY_DEBUG", "").strip()


class GateHandler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "RuneclawGate/1.0"

    def log_message(self, fmt, *args):  # quieter: one line per request
        sys.stderr.write("%s %s\n" % (self.address_string(), fmt % args))

    def _deny(self, code, msg):
        body = ('{"error":"%s"}' % msg).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _dump(self, kind, payload):
        """Write one request/response body to DEBUG_DIR. Opt-in, off by default.

        THIS EXISTS BECAUSE GUESSING FAILED TWICE. On 2026-08-30 the bot showed
        "the AI is temporarily unavailable" while ollama logged HTTP 200 with 21
        tokens generated, and the same prompt sent by hand through this proxy
        returned a full 1,487-token answer. Two hypotheses were argued from the
        outside - a tool-call swallowing the content, then a template problem -
        and neither could be confirmed or refuted without the actual bytes.

        The bot's request and ollama's response both pass through here. There
        is no reason to infer what they contain.

        Bodies can carry user text, so this stays OFF unless
        RUNECLAW_PROXY_DEBUG is set, writes only under a directory the operator
        names, and the Authorization header is never among what is written.
        """
        if not DEBUG_DIR:
            return
        try:
            os.makedirs(DEBUG_DIR, exist_ok=True)
            stamp = time.strftime("%H%M%S") + "-%03d" % (time.time() % 1 * 1000)
            path = os.path.join(DEBUG_DIR, "%s-%s.txt" % (stamp, kind))
            with open(path, "wb") as fh:
                fh.write(payload if isinstance(payload, bytes)
                         else str(payload).encode("utf-8", "replace"))
        except Exception as exc:
            sys.stderr.write("debug dump failed: %r\n" % (exc,))

    def _proxy(self):
        auth = self.headers.get("Authorization", "")
        if not secrets.compare_digest(auth, "Bearer " + TOKEN):
            return self._deny(401, "unauthorized")

        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else None
        if body:
            self._dump("request", body)

        headers = {k: v for k, v in self.headers.items()
                   if k.lower() not in _SKIP_REQ}
        headers["Accept-Encoding"] = "identity"
        if body is not None:
            headers["Content-Length"] = str(len(body))

        try:
            conn = http.client.HTTPConnection(*UPSTREAM, timeout=600)
            conn.request(self.command, self.path, body=body, headers=headers)
            resp = conn.getresponse()
        except OSError as exc:
            return self._deny(502, "ollama unreachable: %s" % exc.__class__.__name__)

        self.send_response(resp.status)
        has_length = False
        for key, value in resp.getheaders():
            if key.lower() in _SKIP_RESP:
                continue
            if key.lower() == "content-length":
                has_length = True
            self.send_header(key, value)
        if not has_length:
            # Streaming (SSE/chunked) upstream: deliver close-delimited.
            self.send_header("Connection", "close")
            self.close_connection = True
        self.end_headers()

        captured = bytearray() if DEBUG_DIR else None
        while True:
            chunk = resp.read1(65536)
            if not chunk:
                break
            if captured is not None and len(captured) < 262144:
                captured.extend(chunk)
            self.wfile.write(chunk)
            self.wfile.flush()
        conn.close()
        if captured is not None:
            self._dump("response", bytes(captured))

    do_GET = do_POST = do_DELETE = do_HEAD = _proxy


def main():
    if len(TOKEN) < 16:
        print("ERROR: RUNECLAW_PROXY_TOKEN unset or shorter than 16 chars.")
        print("An unset token must not mean an open gate. Generate one:")
        print('  python -c "import secrets; print(secrets.token_urlsafe(32))"')
        sys.exit(1)

    server = http.server.ThreadingHTTPServer(LISTEN, GateHandler)
    print("RUNECLAW auth gate: http://%s:%d -> ollama on :%d" %
          (LISTEN[0], LISTEN[1], UPSTREAM[1]))
    print("Point cloudflared at the gate port, never at ollama directly.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
