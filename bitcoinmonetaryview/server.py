# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Web interface and JSON API.

The API never acts on the node. The only state-changing endpoints affect this
app (pause/resume, rescan of its own database, UI-editable settings) and are
POST-only with a CSRF token, an Origin check and a Host allowlist.
"""

import base64
import hmac
import ipaddress
import json
import logging
import os
import secrets
import socket
import ssl
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import __version__
from .analytics import Analytics
from .diagnostics import run_connection_test
from .rules import UPSTREAM_COMMIT, UPSTREAM_REPO

log = logging.getLogger("bmv.server")

STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
STATIC_FILES = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/index.html": ("index.html", "text/html; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/app.css": ("app.css", "text/css; charset=utf-8"),
    "/favicon.svg": ("favicon.svg", "image/svg+xml"),
}
MAX_BODY = 16 * 1024
CSP = ("default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data: blob:; "
       "connect-src 'self'; font-src 'self'; object-src 'none'; frame-ancestors 'none'; "
       "base-uri 'none'; form-action 'self'")


def host_allowed(host_header, bind, allowed, managed):
    """DNS-rebinding protection: only accept Host names we expect."""
    if managed:
        return True          # the platform's reverse proxy fronts the app
    if not host_header:
        return False
    host = host_header.strip().lower()
    if host.startswith("["):
        name = host[1:host.find("]")] if "]" in host else host
    else:
        name = host.rsplit(":", 1)[0] if host.count(":") == 1 else host
    if name in ("localhost", "127.0.0.1", "::1"):
        return True
    for a in allowed:
        if a == name or (a.startswith("*.") and name.endswith(a[1:])):
            return True
    loopback_bind = bind in ("127.0.0.1", "localhost", "::1")
    if loopback_bind:
        return False
    try:
        ipaddress.ip_address(name)
        return True          # IP literals cannot be rebound to an attacker's server
    except ValueError:
        pass
    return name.endswith(".local") or name.endswith(".onion")


class App:
    def __init__(self, config, scanner):
        self.config = config
        self.scanner = scanner
        self.csrf = secrets.token_urlsafe(32)
        self._analytics = {}
        self._conn_test = {"running": False, "result": None, "time": None}
        self._conn_lock = threading.Lock()

    def analytics(self):
        path = self.scanner.store_path
        if not path:
            return None
        a = self._analytics.get(path)
        if a is None:
            a = self._analytics[path] = Analytics(path)
        return a

    def start_connection_test(self):
        with self._conn_lock:
            if self._conn_test["running"]:
                return False
            if self._conn_test["time"] and time.time() - self._conn_test["time"] < 5:
                return False
            self._conn_test["running"] = True

        def run():
            try:
                res = run_connection_test(self.config)
            except Exception as e:     # pragma: no cover - defensive
                res = [{"name": "Test", "status": "fail", "message": str(e), "hint": ""}]
            with self._conn_lock:
                self._conn_test.update(running=False, result=res, time=time.time())
        threading.Thread(target=run, daemon=True).start()
        return True

    def status(self):
        s = self.scanner.status.snapshot()
        c = self.config
        s["managed"] = c.managed
        s["managed_by"] = c.managed_by
        s["can_pause"] = (not c.managed) or c.allow_pause_when_managed
        s["can_rescan"] = not c.managed
        s["upstream"] = {"repo": UPSTREAM_REPO, "commit": UPSTREAM_COMMIT}
        s["auth_enabled"] = bool(c.auth_user)
        s["bind"] = c.bind
        s["warnings"] = list(c.warnings)
        if c.bind not in ("127.0.0.1", "localhost", "::1") and not c.auth_user and not c.managed:
            s["warnings"].append("The web interface is reachable from your network without a password. "
                                 "Set auth_user/auth_password (and ideally tls_cert/tls_key).")
        a = self.analytics()
        if a is not None:
            size = 0
            for suffix in ("", "-wal", "-shm"):
                try:
                    size += os.path.getsize(a.db_path + suffix)
                except OSError:
                    pass
            s["db_size"] = size or None
        return s


def make_handler(app):
    class Handler(BaseHTTPRequestHandler):
        server_version = "BitcoinMonetaryView"
        sys_version = ""
        protocol_version = "HTTP/1.1"
        timeout = 30

        def log_message(self, fmt, *args):
            log.debug("%s %s", self.address_string(), fmt % args)

        # ------------------------------------------------------------ helpers
        def _headers(self, code, ctype, length, extra=None, cache=False):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(length))
            self.send_header("Content-Security-Policy", CSP)
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Cross-Origin-Opener-Policy", "same-origin")
            self.send_header("Cross-Origin-Resource-Policy", "same-origin")
            self.send_header("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
            self.send_header("Cache-Control", "no-cache" if cache else "no-store")
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()

        def send_json(self, obj, code=200):
            body = json.dumps(obj, separators=(",", ":"), default=str).encode()
            self._headers(code, "application/json; charset=utf-8", len(body))
            if self.command != "HEAD":
                self.wfile.write(body)

        def send_text(self, text, code=200, ctype="text/plain; charset=utf-8", extra=None):
            body = text.encode() if isinstance(text, str) else text
            self._headers(code, ctype, len(body), extra)
            if self.command != "HEAD":
                self.wfile.write(body)

        def error(self, code, msg):
            self.send_json({"error": msg}, code)

        def guard(self):
            c = app.config
            if not host_allowed(self.headers.get("Host"), c.bind, c.allowed_hosts, c.managed):
                self.send_text("Host not allowed. Add it to allowed_hosts if you use this name.", 421)
                return False
            if c.auth_user and self.path != "/api/health":
                hdr = self.headers.get("Authorization", "")
                ok = False
                if hdr.startswith("Basic "):
                    try:
                        user, _, pw = base64.b64decode(hdr[6:]).decode().partition(":")
                        ok = (hmac.compare_digest(user.encode(), c.auth_user.encode())
                              & hmac.compare_digest(pw.encode(), c.auth_password.encode()))
                    except (ValueError, UnicodeDecodeError):
                        ok = False
                if not ok:
                    body = b"Authentication required"
                    self._headers(401, "text/plain", len(body),
                                  {"WWW-Authenticate": 'Basic realm="BitcoinMonetaryView", charset="UTF-8"'})
                    self.wfile.write(body)
                    return False
            return True

        def query(self):
            u = urllib.parse.urlsplit(self.path)
            return u.path, dict(urllib.parse.parse_qsl(u.query))

        # ------------------------------------------------------------ GET
        def do_HEAD(self):
            self.do_GET()

        def do_GET(self):
            if not self.guard():
                return
            path, q = self.query()
            try:
                if path in STATIC_FILES:
                    return self.static(path)
                if path == "/api/health":
                    return self.send_json({"ok": True, "version": __version__})
                if path == "/api/session":
                    return self.send_json({"csrf": app.csrf})
                if path == "/api/status":
                    return self.send_json(app.status())
                if path == "/api/settings":
                    return self.send_json({"managed": app.config.managed, "managed_by": app.config.managed_by,
                                           "settings": app.config.public_view()})
                if path == "/api/connection-test":
                    return self.send_json(app._conn_test)
                a = app.analytics()
                if path == "/api/summary":
                    return self.send_json(a.summary() if a else {"empty": True})
                if path == "/api/blocks":
                    before = q.get("before")
                    return self.send_json(a.blocks(int(q.get("limit", 50)), int(before) if before else None)
                                          if a else [])
                if path.startswith("/api/block/"):
                    h = path[len("/api/block/"):]
                    if not h.isdigit():
                        return self.error(400, "invalid height")
                    b = a.block(int(h)) if a else None
                    return self.send_json(b) if b else self.error(404, "block not analysed yet")
                if path == "/api/range":
                    if not a:
                        return self.send_json({"buckets": []})
                    return self.send_json(a.range(int(q["from"]), int(q["to"]), int(q.get("points", 400))))
                if path == "/api/history":
                    return self.send_json(a.history() if a else {"months": []})
                if path == "/api/export.csv":
                    return self.stream((a or Analytics("")).iter_csv(), "text/csv; charset=utf-8",
                                       "bitcoinmonetaryview-blocks.csv")
                if path == "/api/export.json":
                    return self.stream((a or Analytics("")).iter_json(), "application/json; charset=utf-8",
                                       "bitcoinmonetaryview.json")
                return self.error(404, "not found")
            except (ValueError, KeyError):
                return self.error(400, "bad request")
            except Exception:
                log.exception("API error")
                return self.error(500, "internal error")

        def stream(self, chunks, ctype, filename):
            """Chunked transfer: exports of ~1M blocks never sit in memory at once."""
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Transfer-Encoding", "chunked")
            self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
            self.send_header("Content-Security-Policy", CSP)
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            if self.command == "HEAD":
                return
            buf = []
            size = 0
            for piece in chunks:
                b = piece.encode()
                buf.append(b)
                size += len(b)
                if size >= 65536:
                    data = b"".join(buf)
                    self.wfile.write(b"%x\r\n%s\r\n" % (len(data), data))
                    buf, size = [], 0
            if buf:
                data = b"".join(buf)
                self.wfile.write(b"%x\r\n%s\r\n" % (len(data), data))
            self.wfile.write(b"0\r\n\r\n")

        def static(self, path):
            name, ctype = STATIC_FILES[path]
            with open(os.path.join(STATIC_DIR, name), "rb") as f:
                data = f.read()
            self._headers(200, ctype, len(data), cache=True)
            if self.command != "HEAD":
                self.wfile.write(data)

        # ------------------------------------------------------------ POST
        def do_POST(self):
            # Until the body has been read, any early error response must close the
            # connection, otherwise the unread body would be parsed as the next request.
            self.close_connection = True
            if not self.guard():
                return
            path, _ = self.query()
            origin = self.headers.get("Origin")
            if origin:
                o = urllib.parse.urlsplit(origin)
                if o.netloc.lower() != (self.headers.get("Host") or "").lower():
                    return self.error(403, "cross-origin request refused")
            if not hmac.compare_digest(self.headers.get("X-CSRF-Token", ""), app.csrf):
                return self.error(403, "missing or invalid CSRF token")
            if (self.headers.get("Content-Type") or "").split(";")[0].strip() != "application/json":
                return self.error(415, "JSON required")
            try:
                n = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                return self.error(400, "bad length")
            if n < 0 or n > MAX_BODY:
                return self.error(413, "request too large")
            raw = self.rfile.read(n)
            self.close_connection = False
            try:
                body = json.loads(raw or b"{}")
                if not isinstance(body, dict):
                    raise ValueError
            except ValueError:
                return self.error(400, "invalid JSON")
            try:
                if path == "/api/control":
                    action = body.get("action")
                    st = app.status()
                    if action in ("pause", "resume"):
                        if not st["can_pause"]:
                            return self.error(403, "managed by the platform")
                        app.scanner.pause() if action == "pause" else app.scanner.resume()
                        return self.send_json({"ok": True})
                    if action == "rescan":
                        if not st["can_rescan"]:
                            return self.error(403, "managed by the platform")
                        app.scanner.request_rescan()
                        return self.send_json({"ok": True})
                    return self.error(400, "unknown action")
                if path == "/api/settings":
                    changes = body.get("changes")
                    if not isinstance(changes, dict):
                        return self.error(400, "changes must be an object")
                    try:
                        app.config.update_file(changes)
                    except PermissionError as e:
                        return self.error(403, str(e))
                    except (ValueError, TypeError) as e:
                        return self.error(400, f"invalid value: {e}")
                    app.scanner.wake.set()
                    return self.send_json({"ok": True, "settings": app.config.public_view()})
                if path == "/api/connection-test":
                    started = app.start_connection_test()
                    return self.send_json({"ok": True, "started": started})
                return self.error(404, "not found")
            except Exception:
                log.exception("API error")
                return self.error(500, "internal error")

    return Handler


class Server(ThreadingHTTPServer):
    daemon_threads = True
    request_queue_size = 64

    ssl_ctx = None

    def __init__(self, addr, handler, ipv6=False):
        if ipv6:
            self.address_family = socket.AF_INET6
        super().__init__(addr, handler)

    def finish_request(self, request, client_address):
        # TLS handshake in the per-connection thread, so a slow client cannot block accept()
        if self.ssl_ctx is not None:
            request.settimeout(30)
            try:
                request = self.ssl_ctx.wrap_socket(request, server_side=True)
            except (ssl.SSLError, OSError):
                return
        super().finish_request(request, client_address)


def make_server(config, scanner):
    app = App(config, scanner)
    handler = make_handler(app)
    bind = config.bind
    srv = Server((bind, config.port), handler, ipv6=":" in bind)
    if config.tls_cert and config.tls_key:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.minimum_version = ssl.TLSVersion.TLSv1_2
        ctx.load_cert_chain(config.tls_cert, config.tls_key)
        srv.ssl_ctx = ctx
    srv.app = app
    return srv
