# SPDX-License-Identifier: AGPL-3.0-or-later
import base64
import http.client
import json
import os
import re
import shutil
import socket
import tempfile
import threading
import unittest

from bitcoinmonetaryview.analytics import csv_safe
from bitcoinmonetaryview.config import Config
from bitcoinmonetaryview.scanner import Status
from bitcoinmonetaryview.server import host_allowed, make_server

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


class StubScanner:
    def __init__(self):
        self.status = Status()
        self.store_path = None
        self.actions = []
        self.wake = threading.Event()

    def pause(self):
        self.actions.append("pause")

    def resume(self):
        self.actions.append("resume")

    def request_rescan(self):
        self.actions.append("rescan")


class ServerTest(unittest.TestCase):
    env = {}
    cli_extra = {}

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.port = free_port()
        cli = {"port": str(self.port), "rpc_password": "rpc-s3cret", "rpc_user": "alice"}
        cli.update(self.cli_extra)
        self.config = Config(self.dir, cli=cli, env=self.env)
        self.scanner = StubScanner()
        self.srv = make_server(self.config, self.scanner)
        self.t = threading.Thread(target=self.srv.serve_forever, daemon=True)
        self.t.start()

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()
        shutil.rmtree(self.dir)

    def req(self, method, path, body=None, headers=None, host=None):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        h = {"Host": host or f"127.0.0.1:{self.port}"}
        h.update(headers or {})
        c.request(method, path, body=body, headers=h)
        r = c.getresponse()
        data = r.read()
        c.close()
        return r, data

    def csrf(self):
        r, d = self.req("GET", "/api/session")
        return json.loads(d)["csrf"]

    def post(self, path, obj, token=None, extra=None):
        h = {"Content-Type": "application/json"}
        if token:
            h["X-CSRF-Token"] = token
        h.update(extra or {})
        return self.req("POST", path, json.dumps(obj), h)


class Basics(ServerTest):
    def test_index_and_security_headers(self):
        r, d = self.req("GET", "/")
        self.assertEqual(r.status, 200)
        self.assertIn(b"Monetary View", d)
        csp = r.getheader("Content-Security-Policy")
        self.assertIn("script-src 'self'", csp)
        self.assertNotIn("unsafe-inline", csp)
        self.assertEqual(r.getheader("X-Frame-Options"), "DENY")
        self.assertEqual(r.getheader("X-Content-Type-Options"), "nosniff")

    def test_no_inline_scripts_or_external_resources(self):
        html = open(os.path.join(ROOT, "bitcoinmonetaryview", "static", "index.html")).read()
        self.assertIsNone(re.search(r"<script(?![^>]*\bsrc=)[^>]*>", html))
        self.assertNotRegex(html, r"(src|href)=\"https?://(?!github\.com/sambitcoin)")
        js = open(os.path.join(ROOT, "bitcoinmonetaryview", "static", "app.js")).read()
        self.assertNotIn("innerHTML", js)
        self.assertNotIn("eval(", js)
        self.assertIsNone(re.search(r"fetch\(\s*[\"'`]https?:", js))

    def test_path_traversal_refused(self):
        for p in ("/../server.py", "/static/../server.py", "/%2e%2e/server.py", "/..%2fserver.py",
                  "/app.js/../../server.py", "//etc/passwd", "/server.py", "/config.py"):
            r, d = self.req("GET", p)
            self.assertIn(r.status, (400, 404), p)
            self.assertNotIn(b"import", d, p)

    def test_host_header_rebinding_refused(self):
        r, _ = self.req("GET", "/api/status", host="evil.example.com")
        self.assertEqual(r.status, 421)
        r, _ = self.req("GET", "/api/status", host=f"localhost:{self.port}")
        self.assertEqual(r.status, 200)

    def test_csrf_and_origin(self):
        r, _ = self.post("/api/control", {"action": "pause"})
        self.assertEqual(r.status, 403)
        r, _ = self.post("/api/control", {"action": "pause"}, token="wrong")
        self.assertEqual(r.status, 403)
        tok = self.csrf()
        r, _ = self.post("/api/control", {"action": "pause"}, token=tok, extra={"Origin": "https://evil.example.com"})
        self.assertEqual(r.status, 403)
        r, _ = self.post("/api/control", {"action": "pause"}, token=tok,
                         extra={"Origin": f"http://127.0.0.1:{self.port}"})
        self.assertEqual(r.status, 200)
        self.assertEqual(self.scanner.actions, ["pause"])
        r, _ = self.req("POST", "/api/control", "action=pause",
                        {"X-CSRF-Token": tok, "Content-Type": "application/x-www-form-urlencoded"})
        self.assertEqual(r.status, 415)

    def test_body_limit(self):
        tok = self.csrf()
        r, _ = self.post("/api/settings", {"changes": {"x": "a" * 20000}}, token=tok)
        self.assertEqual(r.status, 413)

    def test_settings_update_and_locks(self):
        tok = self.csrf()
        r, d = self.post("/api/settings", {"changes": {"scan_window": "01:00-05:00"}}, token=tok)
        self.assertEqual(r.status, 200, d)
        self.assertEqual(self.config.scan_window, "01:00-05:00")
        r, _ = self.post("/api/settings", {"changes": {"rpc_url": "http://evil:1"}}, token=tok)
        self.assertEqual(r.status, 403)
        r, _ = self.post("/api/settings", {"changes": {"scan_window": "nonsense"}}, token=tok)
        self.assertEqual(r.status, 400)

    def test_secrets_never_returned(self):
        for p in ("/api/settings", "/api/status", "/api/summary", "/api/connection-test"):
            r, d = self.req("GET", p)
            self.assertEqual(r.status, 200, p)
            self.assertNotIn(b"rpc-s3cret", d, p)

    def test_empty_data_endpoints(self):
        for p in ("/api/summary", "/api/blocks", "/api/history", "/api/export.csv", "/api/export.json",
                  "/api/range?from=0&to=10"):
            r, _ = self.req("GET", p)
            self.assertEqual(r.status, 200, p)
        r, _ = self.req("GET", "/api/block/abc")
        self.assertEqual(r.status, 400)
        r, _ = self.req("GET", "/api/range?from=x&to=1")
        self.assertIn(r.status, (200, 400))


class BasicAuth(ServerTest):
    cli_extra = {"auth_user": "bob", "auth_password": "pw"}

    def test_auth_required(self):
        r, _ = self.req("GET", "/")
        self.assertEqual(r.status, 401)
        self.assertIn("Basic", r.getheader("WWW-Authenticate"))
        good = "Basic " + base64.b64encode(b"bob:pw").decode()
        bad = "Basic " + base64.b64encode(b"bob:nope").decode()
        r, _ = self.req("GET", "/", headers={"Authorization": bad})
        self.assertEqual(r.status, 401)
        r, _ = self.req("GET", "/", headers={"Authorization": good})
        self.assertEqual(r.status, 200)
        r, _ = self.req("GET", "/api/health")
        self.assertEqual(r.status, 200)


class Managed(ServerTest):
    env = {"BMV_MANAGED_BY": "startos"}

    def test_managed_mode(self):
        r, d = self.req("GET", "/api/status", host="myservice.startos.local:443")
        self.assertEqual(r.status, 200)        # Host check delegated to the platform proxy
        st = json.loads(d)
        self.assertTrue(st["managed"])
        self.assertFalse(st["can_rescan"])
        tok = self.csrf()
        r, _ = self.post("/api/settings", {"changes": {"scan_window": "01:00-05:00"}}, token=tok)
        self.assertEqual(r.status, 403)
        r, _ = self.post("/api/control", {"action": "rescan"}, token=tok)
        self.assertEqual(r.status, 403)
        r, _ = self.post("/api/control", {"action": "pause"}, token=tok)
        self.assertEqual(r.status, 200)


class Helpers(unittest.TestCase):
    def test_csv_safe(self):
        for v in ("=cmd|'/c calc'!A1", "+1", "-1", "@SUM(A1)"):
            self.assertTrue(csv_safe(v).startswith("'"), v)
        self.assertEqual(csv_safe(123), "123")
        self.assertEqual(csv_safe("abc"), "abc")

    def test_host_allowed(self):
        self.assertTrue(host_allowed("127.0.0.1:8338", "127.0.0.1", [], False))
        self.assertFalse(host_allowed("attacker.com:8338", "127.0.0.1", [], False))
        self.assertFalse(host_allowed("192.168.1.5:8338", "127.0.0.1", [], False))
        self.assertTrue(host_allowed("192.168.1.5:8338", "0.0.0.0", [], False))
        self.assertTrue(host_allowed("mypc.local", "0.0.0.0", [], False))
        self.assertFalse(host_allowed("attacker.com", "0.0.0.0", [], False))
        self.assertTrue(host_allowed("node.example.org", "0.0.0.0", ["node.example.org"], False))
        self.assertTrue(host_allowed("[::1]:8338", "127.0.0.1", [], False))
        self.assertFalse(host_allowed("", "127.0.0.1", [], False))


class StaticSafety(unittest.TestCase):
    """The app may only write inside its own data directory: via SQLite (store.py) and
    settings.json (config.py). Node-facing code must not contain any write operation."""

    WRITE_PATTERNS = [
        r"open\([^)]*[\"'][wax+]b?[\"']", r"os\.(remove|unlink|rename|replace|rmdir|truncate|chmod|chown|mkdir|makedirs)\(",
        r"shutil\.", r"O_WRONLY|O_RDWR|O_CREAT|O_TRUNC|O_APPEND", r"\.write_(text|bytes)\(", r"os\.fchmod",
        r"tempfile\.",
    ]
    ALLOWED = {"config.py", "store.py", "__main__.py"}

    def test_no_writes_outside_allowed_modules(self):
        pkg = os.path.join(ROOT, "bitcoinmonetaryview")
        for dirpath, _, files in os.walk(pkg):
            if os.sep + "upstream" in dirpath:
                continue          # vendored, never executed for I/O (only pure functions imported)
            for f in files:
                if not f.endswith(".py") or f in self.ALLOWED:
                    continue
                src = open(os.path.join(dirpath, f)).read()
                for pat in self.WRITE_PATTERNS:
                    self.assertIsNone(re.search(pat, src), f"{f}: matches {pat}")

    def test_node_code_uses_only_whitelisted_rpc(self):
        src = "".join(open(os.path.join(ROOT, "bitcoinmonetaryview", p)).read()
                      for p in ("scanner.py", "diagnostics.py", "node/client.py"))
        from bitcoinmonetaryview.node.rpc import ALLOWED
        for m in re.findall(r"\.call\(\s*\"(\w+)\"", src) + re.findall(r"\(\"(get\w+)\", \[", src):
            self.assertIn(m, ALLOWED, m)

    def test_cookie_opened_read_only(self):
        src = open(os.path.join(ROOT, "bitcoinmonetaryview", "node", "rpc.py")).read()
        self.assertIn("os.open(path, os.O_RDONLY)", src)


if __name__ == "__main__":
    unittest.main()
