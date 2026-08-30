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

LISTEN = ("127.0.0.1", int(os.environ.get("RUNECLAW_PROXY_PORT", "11435")))
UPSTREAM = ("127.0.0.1", int(os.environ.get("OLLAMA_PORT", "11434")))
TOKEN = os.environ.get("RUNECLAW_PROXY_TOKEN", "")

# End-to-end hop-by-hop headers we must not blindly forward.
_SKIP_REQ = {"host", "authorization", "connection", "accept-encoding", "content-length"}
_SKIP_RESP = {"transfer-encoding", "connection", "keep-alive", "content-encoding"}


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

    def _proxy(self):
        auth = self.headers.get("Authorization", "")
        if not secrets.compare_digest(auth, "Bearer " + TOKEN):
            return self._deny(401, "unauthorized")

        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else None

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

        while True:
            chunk = resp.read1(65536)
            if not chunk:
                break
            self.wfile.write(chunk)
            self.wfile.flush()
        conn.close()

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
