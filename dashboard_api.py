"""
RUNECLAW — Combined Website + Dashboard API Server.
Serves the landing page from website/ at root, dashboard from
dashboard_static/ at /dashboard, and the snapshot API on /api/*.
"""
import hmac
import json
import os
import mimetypes
import tempfile
import threading
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from datetime import datetime, timezone

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_FILE = os.path.join(BASE_DIR, "data", "dashboard_snapshot.json")
FEED_FILE = os.path.join(BASE_DIR, "data", "dashboard_feed.json")
WEBSITE_DIR = os.path.join(BASE_DIR, "website")
DASHBOARD_DIR = os.path.join(BASE_DIR, "dashboard_static")
API_KEY = os.environ.get("DASHBOARD_API_KEY", "")
CORS_ORIGIN = os.environ.get("DASHBOARD_CORS_ORIGIN", "")
ENVIRONMENT = os.environ.get("ENVIRONMENT", "production")

# Additional allowed origins, from configuration.
#
# This used to be a hardcoded `{"https://pmvc58g2.mule.page"}` — one operator's
# hosting URL, compiled into the CORS allow-list of every deployment, in
# production, unconditionally. An allow-list is a security decision and belongs
# in the environment of the deployment making it.
_EXTRA_ORIGINS = {
    o.strip() for o in os.environ.get("DASHBOARD_EXTRA_ORIGINS", "").split(",")
    if o.strip()
}
if ENVIRONMENT == "development":
    _EXTRA_ORIGINS.add("http://localhost:9090")

# One writer at a time for the feed. The feed is read-modify-write and this is
# a ThreadingHTTPServer, so two concurrent POSTs both read the same list,
# both prepend, and the second write erases the first one's entry.
_FEED_LOCK = threading.Lock()

# Distinguishes "there is no file yet" from "the read failed". Both used to
# come back as the caller's fallback, so an unreadable snapshot rendered as
# `{"status":"ok","traders":0}` — a confident measurement assembled from a
# failure. Unreadable is never zero, and absent is never a measurement.
MISSING = object()
UNREADABLE = object()


def load_json(path, fallback):
    """Deprecated: collapses absent and unreadable into one answer.

    Kept because removing it is a bigger change than this fix; every caller in
    this module now uses read_json instead.
    """
    result = read_json(path)
    return fallback if result in (MISSING, UNREADABLE) else result


def read_json(path):
    """Three outcomes, not two: the value, MISSING, or UNREADABLE."""
    if not os.path.exists(path):
        return MISSING
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return UNREADABLE


def save_json(path, data):
    """Write atomically. Returns True on success, False on failure.

    Two bugs here. It swallowed every exception and returned None, and the POST
    handler then answered `{"ok": True}` — success reported on a failed write.
    And it wrote straight to the destination, so a crash or a full disk
    mid-write left a truncated file that the next read could not parse. Write
    to a temporary file in the same directory and rename: on POSIX that
    replacement is atomic, so a reader sees either the old file or the new one
    and never a half-written one.
    """
    tmp = None
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        # mkstemp, not f"{path}.tmp.{os.getpid()}": this is a ThreadingHTTPServer,
        # so concurrent writers share a PID and would collide on one temp name —
        # one thread's os.replace moves the file out from under another's write,
        # and both report failure. Caught by test_the_feed_survives_concurrent_writers.
        fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path) or ".",
                                   prefix=os.path.basename(path) + ".", suffix=".tmp")
        with os.fdopen(fd, "w") as f:
            json.dump(data, f)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
        return True
    except Exception:
        if tmp:
            try:
                os.unlink(tmp)
            except OSError:
                pass
        return False


def _is_within(path, root):
    """True if `path` is inside `root`.

    NOT `path.startswith(root)`. That is a string test, and a string test says
    `<root>-backup/secret.txt` is inside `<root>`. Demonstrated: with
    base=".../website", a request for "/../website_backup/secret.txt" resolved
    outside the root and the startswith check passed. `website-old/`,
    `website.bak/` and `dashboard_static_v2/` are ordinary deploy artifacts.
    commonpath compares path COMPONENTS, which is the question being asked.
    """
    try:
        return os.path.commonpath([os.path.realpath(path), os.path.realpath(root)]) \
            == os.path.realpath(root)
    except ValueError:
        # Different drives on Windows, or a mix of absolute and relative.
        return False


class Handler(BaseHTTPRequestHandler):
    # Set by do_HEAD so the shared routing in do_GET emits headers only. The
    # two used to be separate implementations of the same routing, and they had
    # already diverged: HEAD allowed `/dashboard/../website/index.html`, which
    # GET refuses. A second copy of a routing decision is a second copy of a
    # security decision.
    _head_only = False

    def _cors_headers(self):
        req_origin = self.headers.get("Origin", "")
        allowed = CORS_ORIGIN if CORS_ORIGIN else None
        if req_origin and (req_origin == allowed or req_origin in _EXTRA_ORIGINS):
            self.send_header("Access-Control-Allow-Origin", req_origin)
        elif allowed:
            self.send_header("Access-Control-Allow-Origin", allowed)
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, X-API-Key")

    def _security_headers(self):
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "strict-origin-when-cross-origin")

    def _json_response(self, data, status=200):
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self._security_headers()
        self._cors_headers()
        self.end_headers()
        if not self._head_only:
            self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors_headers()
        self.end_headers()

    def _serve_static(self, base_dir, rel_path):
        """Serve a static file from base_dir, refusing anything outside it."""
        filepath = os.path.realpath(os.path.join(base_dir, rel_path.lstrip("/")))
        if not _is_within(filepath, base_dir):
            self.send_response(403)
            self.end_headers()
            return
        if not os.path.isfile(filepath):
            self.send_response(404)
            self.end_headers()
            if not self._head_only:
                self.wfile.write(b"Not found")
            return
        mime, _ = mimetypes.guess_type(filepath)
        try:
            size = os.path.getsize(filepath)
            content = None if self._head_only else open(filepath, "rb").read()
        except OSError:
            # The file exists and could not be read. Not a 404 — that would
            # claim it is absent, which is a different and wrong fact.
            self.send_response(500)
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Type", mime or "application/octet-stream")
        self.send_header("Content-Length", str(size))
        self._security_headers()
        # Cache images/videos for 1 hour, HTML for no-cache
        if mime and (mime.startswith("image/") or mime.startswith("video/")):
            self.send_header("Cache-Control", "public, max-age=3600")
        else:
            self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        if content is not None:
            self.wfile.write(content)

    def do_GET(self):
        path = self.path.split("?")[0]

        # API routes
        if path == "/api/snapshot":
            snap = read_json(DATA_FILE)
            if snap is UNREADABLE:
                # NOT `{"traders": [], "total_traders": 0}`. That renders as a
                # measured "no traders" when the truth is that nobody could
                # read the file.
                self._json_response({"error": "snapshot_unreadable"}, 503)
            elif snap is MISSING:
                self._json_response({"error": "no_snapshot_yet"}, 404)
            else:
                self._json_response(snap)
            return
        if path == "/api/feed":
            feed = read_json(FEED_FILE)
            if feed is UNREADABLE:
                self._json_response({"error": "feed_unreadable"}, 503)
            else:
                self._json_response([] if feed is MISSING else feed)
            return
        if path == "/api/health":
            snap = read_json(DATA_FILE)
            if snap is UNREADABLE:
                # "ok" with traders: 0 was the old answer here, off a failed
                # read, on the endpoint whose entire job is to say whether
                # things are working.
                self._json_response(
                    {"status": "degraded", "reason": "snapshot_unreadable"}, 503)
            elif snap is MISSING:
                self._json_response({"status": "starting", "reason": "no_snapshot_yet"})
            else:
                self._json_response({
                    "status": "ok",
                    "last_update": snap.get("received_at", ""),
                    "traders": snap.get("total_traders"),
                })
            return

        # Dashboard routes — /dashboard or /dashboard/*
        if path == "/dashboard" or path == "/dashboard/":
            self._serve_static(DASHBOARD_DIR, "index.html")
            return
        if path.startswith("/dashboard/"):
            self._serve_static(DASHBOARD_DIR, path[len("/dashboard/"):])
            return

        # Website landing page — everything else
        if path == "/" or path == "":
            path = "/index.html"
        self._serve_static(WEBSITE_DIR, path)

    def do_HEAD(self):
        """Headers only — routed by do_GET, so there is one routing decision.

        This used to be a second copy of the routing with a weaker guard:

            base_check = realpath(WEBSITE_DIR) if not filepath.startswith(
                realpath(DASHBOARD_DIR)) else realpath(DASHBOARD_DIR)

        which picks the base to match the already-resolved path, and so asks
        "is this under EITHER directory" rather than "is it under the one this
        route serves". Verified: HEAD /dashboard/../website/index.html was
        allowed where GET refuses it, and HEAD returns Content-Length, making
        it a file-existence and size oracle across both trees.
        """
        self._head_only = True
        try:
            self.do_GET()
        finally:
            self._head_only = False

    def do_POST(self):
        if self.path != "/api/snapshot":
            self.send_response(404)
            self.end_headers()
            return
        if not API_KEY:
            self._json_response({"error": "DASHBOARD_API_KEY not configured"}, 403)
            return
        key = self.headers.get("X-API-Key", "")
        if not key or not hmac.compare_digest(key, API_KEY):
            self._json_response({"error": "bad key"}, 403)
            return
        try:
            length = int(self.headers.get("Content-Length", 0))
        except (TypeError, ValueError):
            # A malformed header used to raise straight out of the handler,
            # which BaseHTTPRequestHandler turns into a 500 with a traceback.
            self._json_response({"error": "invalid Content-Length"}, 400)
            return
        if length < 0:
            self._json_response({"error": "invalid Content-Length"}, 400)
            return
        if length > 10_000_000:  # 10MB max
            self._json_response({"error": "payload too large"}, 413)
            return
        try:
            body = json.loads(self.rfile.read(length))
        except (json.JSONDecodeError, ValueError):
            self._json_response({"error": "invalid JSON"}, 400)
            return
        if not isinstance(body, dict):
            self._json_response({"error": "snapshot must be an object"}, 400)
            return

        body["received_at"] = datetime.now(timezone.utc).isoformat()
        if not save_json(DATA_FILE, body):
            # Reporting {"ok": True} here is what the pusher would record as a
            # delivered snapshot.
            self._json_response({"error": "snapshot_write_failed"}, 500)
            return

        # Feed — add new entry from snapshot, then truncate. Under the lock:
        # read-modify-write from two threads loses entries.
        with _FEED_LOCK:
            feed = read_json(FEED_FILE)
            if feed is UNREADABLE or not isinstance(feed, list):
                feed = []
            feed.insert(0, {
                "timestamp": body["received_at"],
                "traders": len(body.get("traders", [])),
                "total_traders": body.get("total_traders", 0),
            })
            save_json(FEED_FILE, feed[:100])

        self._json_response({"ok": True, "traders": len(body.get("traders", []))})

    def log_message(self, fmt, *args):
        pass


if __name__ == "__main__":
    if not API_KEY:
        print("ERROR: DASHBOARD_API_KEY environment variable is not set. Refusing to start.")
        print("Set it with: export DASHBOARD_API_KEY='your-secret-key'")
        raise SystemExit(1)
    port = int(os.environ.get("DASHBOARD_PORT", 9090))
    os.makedirs(os.path.join(BASE_DIR, "data"), exist_ok=True)
    os.makedirs(WEBSITE_DIR, exist_ok=True)
    os.makedirs(DASHBOARD_DIR, exist_ok=True)
    # Configurable, like bot/main.py:437 already is. The default stays
    # 0.0.0.0 because the deployment reaches this from another container, but
    # an operator running it on a host can now bind it to localhost.
    host = os.environ.get("DASHBOARD_BIND_HOST", "0.0.0.0")
    server = ThreadingHTTPServer((host, port), Handler)
    print(f"RUNECLAW on {host}:{port}  |  Landing: /  |  Dashboard: /dashboard")
    server.serve_forever()
